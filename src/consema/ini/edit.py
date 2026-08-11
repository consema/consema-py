"""INI edit transactions: value replacement and structural operations.

Authority (Rust arbitration for exact byte semantics):

- Operation and policy model: crates/consema-ini/src/edit.rs:15-56
  (RepresentationPolicy, ValueReplacement), 57-107 (EditOperation), 108-243
  (EditTransaction/Builder).
- Failure algebra and codes: edit.rs:260-303 (EditFailure), 1722-1780
  (StableFailure; code mapping 1754-1779).
- Atomic commit: edit.rs:305-553 — Recovered/WrongSnapshot gates
  (edit.rs:308-316), dependency validation (edit.rs:317, 863-920),
  duplicate destructive targets (edit.rs:328-332), prepared-edit
  overlap/ownership conflicts (edit.rs:339-348), adjacent-deletion
  coalescing (edit.rs:1196-1226), bounded target length (edit.rs:357-367),
  rendering and reparse (edit.rs:368-403), ChangeSet source edits and node
  mappings (edit.rs:405-531), SourcePatch derivation (edit.rs:532-540),
  UntouchedByteProof (edit.rs:541-546). Dry-run produces the identical
  patch and target digest (edit.rs:556-570; RFC 0004 §14).
- Preparation: edit.rs:572-861 (value ownership edit.rs:1445-1475,
  section/entry insertion placement edit.rs:652-705/762-827,
  canonical entry text edit.rs:1101-1167, removals edit.rs:707-739/
  829-840, renames edit.rs:741-760/842-861).
- Value representation: edit.rs:1228-1303 (semantic/preserved/canonical
  values; the canonical-fallback diagnostic ini.edit.canonical-fallback@1
  edit.rs:1244-1251), 1305-1430 (Python multiline preserve/canonical
  forms), 1432-1443 (encode_value), 1518-1535 (validate_semantic_value).
- Name validation and collisions: edit.rs:950-1069 (InvalidName /
  NameCollision / InvalidKey / DuplicateKey / KeyCollision; Windows
  permits ordered case-equivalent occurrences, edit.rs:1048-1050).
- Operation metadata: edit.rs:1604-1627 (operation.{index} =
  "ini.edit.*@1" forms) and safe summaries edit.rs:1629-1702.
- The v1 vector goldens this module must reproduce byte-for-byte:
  conformance/vectors/ini-v1.json:89-106 (edit.all-eight-operations,
  edit.dry-run-patch-proof-and-atomic-failure).

Frozen operation ids (crates/consema-ini/src/operation_registry.rs:18-79):
ini.edit.insert-section@1, remove-section@1, rename-section@1,
insert-entry@1, remove-entry@1, rename-entry@1,
replace-semantic-value@1, replace-literal-value@1.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.change_set import (
    ChangeSet,
    NodeMapping,
    NodeMappingStatus,
    SourceEdit,
)
from consema.document.edit_plan import (
    EditOperationSummary,
    EditPlan,
    EditPlanSourceId,
)
from consema.document.ids import FormatOperationId
from consema.document.materialization import MaterializationFailure
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    AssociationPlacement,
    FormationStatus,
    NodeRef,
    NodeRole,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof
from consema.ini.document import IniDocument
from consema.ini.errors import (
    IniDiagnostic,
    IniEditFailure,
    IniEditFailureKind,
    IniSeverity,
)
from consema.ini.kinds import (
    IniProfile,
    IniQuoteStyle,
    IniSyntaxKind,
    is_portable_name,
    is_portable_value,
    is_windows_name,
    windows_value_needs_quotes,
)
from consema.ini.materialization import encode_fragment
from consema.ini.parser import IniEntry, IniSection, parse
from consema.ini.python_case import optionxform
from consema.protocol.error_registry import DiagnosticCategory


class RepresentationPolicy(enum.Enum):
    """Explicit semantic value representation policy (edit.rs:16-26)."""

    EXACT_LITERAL = "ExactLiteral"
    PRESERVE_COMPATIBLE = "PreserveCompatible"
    CANONICAL_FOR_PROFILE = "CanonicalForProfile"
    PRESERVE_ELSE_CANONICAL = "PreserveElseCanonical"


class ValueReplacementKind(enum.Enum):
    """Value operation kind (edit.rs:29-47)."""

    SEMANTIC = "Semantic"
    LITERAL = "Literal"


@dataclass(frozen=True, slots=True)
class ValueReplacement:
    """One value replacement bound to the transaction base snapshot
    (edit.rs:29-47)."""

    target: NodeRef
    value: str | None = None
    policy: RepresentationPolicy | None = None
    literal: bytes | None = None

    @property
    def kind(self) -> ValueReplacementKind:
        if self.value is not None:
            return ValueReplacementKind.SEMANTIC
        return ValueReplacementKind.LITERAL


class EditOperationKind(enum.Enum):
    """Typed edit operation kinds (edit.rs:57-107)."""

    REPLACE_VALUE = "ReplaceValue"
    INSERT_SECTION = "InsertSection"
    REMOVE_SECTION = "RemoveSection"
    RENAME_SECTION = "RenameSection"
    INSERT_ENTRY = "InsertEntry"
    REMOVE_ENTRY = "RemoveEntry"
    RENAME_ENTRY = "RenameEntry"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One typed INI edit operation bound to one immutable base snapshot
    (edit.rs:57-107)."""

    kind: EditOperationKind
    replacement: ValueReplacement | None = None
    document: NodeRef | None = None
    section: NodeRef | None = None
    target: NodeRef | None = None
    name: str | None = None
    key: str | None = None
    value: str | None = None
    placement: AssociationPlacement | None = None


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable transaction; every operation resolves against one base
    snapshot (edit.rs:108-127)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs:129-243)."""

    def __init__(self, document: IniDocument) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def semantic_value(
        self,
        target: NodeRef,
        value: str,
        policy: RepresentationPolicy,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_VALUE,
                replacement=ValueReplacement(target=target, value=value, policy=policy),
            )
        )
        return self

    def literal_value(self, target: NodeRef, literal: bytes) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_VALUE,
                replacement=ValueReplacement(target=target, literal=bytes(literal)),
            )
        )
        return self

    def insert_section(
        self,
        document: NodeRef,
        name: str,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_SECTION,
                document=document,
                name=name,
                placement=placement,
            )
        )
        return self

    def remove_section(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_SECTION, target=target)
        )
        return self

    def rename_section(self, target: NodeRef, name: str) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.RENAME_SECTION, target=target, name=name)
        )
        return self

    def insert_entry(
        self,
        section: NodeRef,
        key: str,
        value: str,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ENTRY,
                section=section,
                key=key,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_entry(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_ENTRY, target=target)
        )
        return self

    def rename_entry(self, target: NodeRef, key: str) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.RENAME_ENTRY, target=target, key=key)
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """Atomic edit success (edit.rs:245-256)."""

    document: IniDocument
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# -- internal preparation records --------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    old_span: Span
    replacement: bytes
    mappings: tuple[tuple[NodeRef, "_MappingPlan"], ...] = ()
    mergeable_deletion: bool = False


class _MappingPlanKind(enum.Enum):
    REPLACED_VALUE = "ReplacedValue"
    REPLACED_SECTION = "ReplacedSection"
    REPLACED_ENTRY = "ReplacedEntry"
    SECTION_AFTER_ENTRY_INSERTION = "SectionAfterEntryInsertion"
    DELETED = "Deleted"
    UNMAPPED = "Unmapped"


@dataclass(frozen=True, slots=True)
class _MappingPlan:
    kind: _MappingPlanKind
    expected: str | None = None  # expected key/name after reparse
    expected_value: str | None = None  # inserted entry stored value
    literal: bool = False  # literal-only error mapping for replaced values
    reason: str | None = None  # Unmapped reason


class _EditPlanner:
    """One planner bound to the base document (mirror of the Rust
    Document::prepare_* methods, edit.rs:572-1516)."""

    def __init__(self, document: IniDocument) -> None:
        self.document = document

    # -- resolution ---------------------------------------------------------

    def resolve_document(self, target: NodeRef) -> None:
        if target.snapshot != self.document.snapshot_identity():
            raise IniEditFailure(IniEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.INI_DOCUMENT:
            raise IniEditFailure(IniEditFailureKind.WRONG_ROLE)
        if target != self.document.node_ref():
            raise IniEditFailure(IniEditFailureKind.TARGET_NOT_FOUND)

    def resolve_section(self, target: NodeRef) -> IniSection:
        if target.snapshot != self.document.snapshot_identity():
            raise IniEditFailure(IniEditFailureKind.WRONG_SNAPSHOT)
        if target.role not in (NodeRole.INI_SECTION, NodeRole.INI_DEFAULT_SECTION):
            raise IniEditFailure(IniEditFailureKind.WRONG_ROLE)
        for section in self.document.sections:
            if section.node == target:
                return section
        raise IniEditFailure(IniEditFailureKind.TARGET_NOT_FOUND)

    def resolve_entry(self, target: NodeRef) -> IniEntry:
        if target.snapshot != self.document.snapshot_identity():
            raise IniEditFailure(IniEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.INI_ENTRY:
            raise IniEditFailure(IniEditFailureKind.WRONG_ROLE)
        for entry in self.document.entries:
            if entry.node == target:
                return entry
        raise IniEditFailure(IniEditFailureKind.TARGET_NOT_FOUND)

    def resolve_entry_in_section(
        self, target: NodeRef, section: NodeRef, entries: list[IniEntry]
    ) -> IniEntry:
        self.resolve_entry(target)
        for entry in entries:
            if entry.node == target and entry.section == section:
                return entry
        raise IniEditFailure(IniEditFailureKind.INVALID_PLACEMENT)

    # -- preparation --------------------------------------------------------

    def prepare_operation(
        self, operation: EditOperation, diagnostics: list[IniDiagnostic]
    ) -> list[_PreparedEdit]:
        if operation.kind is EditOperationKind.REPLACE_VALUE:
            assert operation.replacement is not None
            return [self.prepare_value(operation.replacement, diagnostics)]
        if operation.kind is EditOperationKind.INSERT_SECTION:
            assert operation.document is not None and operation.name is not None
            return [
                self.prepare_insert_section(
                    operation.document, operation.name, operation.placement
                )
            ]
        if operation.kind is EditOperationKind.REMOVE_SECTION:
            assert operation.target is not None
            return self.prepare_remove_section(operation.target)
        if operation.kind is EditOperationKind.RENAME_SECTION:
            assert operation.target is not None and operation.name is not None
            return [self.prepare_rename_section(operation.target, operation.name)]
        if operation.kind is EditOperationKind.INSERT_ENTRY:
            assert (
                operation.section is not None
                and operation.key is not None
                and operation.value is not None
            )
            return [
                self.prepare_insert_entry(
                    operation.section, operation.key, operation.value, operation.placement
                )
            ]
        if operation.kind is EditOperationKind.REMOVE_ENTRY:
            assert operation.target is not None
            return self.prepare_remove_entry(operation.target)
        assert operation.target is not None and operation.key is not None
        return [self.prepare_rename_entry(operation.target, operation.key)]

    def prepare_value(
        self, operation: ValueReplacement, diagnostics: list[IniDiagnostic]
    ) -> _PreparedEdit:
        target = operation.target
        if target.snapshot != self.document.snapshot_identity():
            raise IniEditFailure(IniEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.INI_ENTRY:
            raise IniEditFailure(IniEditFailureKind.WRONG_ROLE)
        entry = next(
            (candidate for candidate in self.document.entries if candidate.node == target),
            None,
        )
        if entry is None:
            raise IniEditFailure(IniEditFailureKind.WRONG_ROLE)
        old_span = _value_ownership(self.document, entry)
        if operation.kind is ValueReplacementKind.LITERAL:
            literal = operation.literal
            assert literal is not None
            if len(literal) > self.document.parse_limits.common.max_source_bytes:
                raise IniEditFailure(
                    IniEditFailureKind.RESOURCE_LIMIT, resource_name="replacement-bytes"
                )
            replacement, literal_only = literal, True
        else:
            value = operation.value
            assert value is not None and operation.policy is not None
            replacement = self.semantic_value(entry, value, operation.policy, diagnostics)
            literal_only = False
        return _PreparedEdit(
            old_span=old_span,
            replacement=replacement,
            mappings=(
                (
                    target,
                    _MappingPlan(
                        _MappingPlanKind.REPLACED_VALUE,
                        expected=entry.key,
                        literal=literal_only,
                    ),
                ),
            ),
        )

    def prepare_insert_section(
        self,
        document: NodeRef,
        name: str,
        placement: AssociationPlacement,
    ) -> _PreparedEdit:
        self.resolve_document(document)
        self.validate_section_name(name)
        self.validate_section_collision(name, None)
        if placement.kind == "Start":
            position = self.section_line_start(self.document.sections[0])
        elif placement.kind == "End":
            position = self.document.source.len()
        elif placement.kind == "Before":
            position = self.section_line_start(self.resolve_section(placement.anchor))
        else:  # After
            self.resolve_section(placement.anchor)
            ordinal = next(
                index
                for index, section in enumerate(self.document.sections)
                if section.node == placement.anchor
            )
            if ordinal + 1 < len(self.document.sections):
                position = self.section_line_start(self.document.sections[ordinal + 1])
            else:
                position = self.document.source.len()
        text = ""
        if position == self.document.source.len() and not _ends_with_newline(self.document):
            text += _profile_newline(self.document.profile)
        text += f"[{name}]"
        text += _profile_newline(self.document.profile)
        return _PreparedEdit(
            old_span=self.document.authority.span(position, position),
            replacement=self.encode_value(text),
            mappings=(
                (
                    document,
                    _MappingPlan(
                        _MappingPlanKind.UNMAPPED,
                        reason="document-reparsed-after-section-insertion",
                    ),
                ),
            ),
        )

    def prepare_remove_section(self, target: NodeRef) -> list[_PreparedEdit]:
        section = self.resolve_section(target)
        edits: list[_PreparedEdit] = []
        header = self.logical_physical_spans(section.logical_line)
        for index, span in enumerate(header):
            edits.append(_deletion_edit(span, target if index == 0 else None))
        for entry in self.document.entries:
            if entry.section != target:
                continue
            for index, span in enumerate(self.logical_physical_spans(entry.logical_line)):
                edits.append(_deletion_edit(span, entry.node if index == 0 else None))
        return edits

    def prepare_rename_section(self, target: NodeRef, name: str) -> _PreparedEdit:
        section = self.resolve_section(target)
        self.validate_section_name(name)
        self.validate_section_collision(name, target)
        return _PreparedEdit(
            old_span=section.name_span,
            replacement=self.encode_value(name),
            mappings=(
                (
                    target,
                    _MappingPlan(_MappingPlanKind.REPLACED_SECTION, expected=name),
                ),
            ),
        )

    def prepare_insert_entry(
        self,
        section: NodeRef,
        key: str,
        value: str,
        placement: AssociationPlacement,
    ) -> _PreparedEdit:
        self.resolve_section(section)
        self.validate_entry_key(key)
        self.validate_entry_collision(section, key, None)
        validate_semantic_value(self.document.profile, value)
        entries = [entry for entry in self.document.entries if entry.section == section]
        if placement.kind == "Start":
            position = (
                self.entry_line_start(entries[0])
                if entries
                else self.section_content_end(section)
            )
        elif placement.kind == "End":
            position = self.section_content_end(section)
        elif placement.kind == "Before":
            entry = self.resolve_entry_in_section(placement.anchor, section, entries)
            position = self.entry_line_start(entry)
        else:  # After
            entry = self.resolve_entry_in_section(placement.anchor, section, entries)
            position = self.entry_line_end(entry)
        text = ""
        if position == self.document.source.len() and not _ends_with_newline(self.document):
            text += _profile_newline(self.document.profile)
        text += self.canonical_entry_text(key, value)
        return _PreparedEdit(
            old_span=self.document.authority.span(position, position),
            replacement=self.encode_value(text),
            mappings=(
                (
                    section,
                    _MappingPlan(
                        _MappingPlanKind.SECTION_AFTER_ENTRY_INSERTION,
                        expected=key,
                        expected_value=value,
                    ),
                ),
            ),
        )

    def prepare_remove_entry(self, target: NodeRef) -> list[_PreparedEdit]:
        entry = self.resolve_entry(target)
        edits: list[_PreparedEdit] = []
        for index, span in enumerate(self.logical_physical_spans(entry.logical_line)):
            edits.append(_deletion_edit(span, target if index == 0 else None))
        return edits

    def prepare_rename_entry(self, target: NodeRef, key: str) -> _PreparedEdit:
        entry = self.resolve_entry(target)
        self.validate_entry_key(key)
        self.validate_entry_collision(entry.section, key, target)
        return _PreparedEdit(
            old_span=entry.key_span,
            replacement=self.encode_value(key),
            mappings=(
                (
                    target,
                    _MappingPlan(_MappingPlanKind.REPLACED_ENTRY, expected=key),
                ),
            ),
        )

    # -- validation ---------------------------------------------------------

    def validate_section_name(self, name: str) -> None:
        """Section-name validity (edit.rs:950-970)."""
        valid = _section_name_valid(self.document.profile, name)
        if not valid:
            raise IniEditFailure(IniEditFailureKind.INVALID_NAME)

    def validate_section_collision(self, name: str, except_target: NodeRef | None) -> None:
        """Strict-profile section collision (edit.rs:972-987)."""
        if self.document.profile is IniProfile.WINDOWS_V1:
            return
        for section in self.document.sections:
            if section.node != except_target and section.name == name:
                raise IniEditFailure(IniEditFailureKind.NAME_COLLISION)

    def validate_entry_key(self, key: str) -> None:
        """Entry-key validity (edit.rs:1016-1040)."""
        valid = _entry_key_valid(self.document.profile, key)
        if not valid:
            raise IniEditFailure(IniEditFailureKind.INVALID_KEY)

    def validate_entry_collision(
        self, section: NodeRef, key: str, except_target: NodeRef | None
    ) -> None:
        """Strict-profile entry collision (edit.rs:1042-1069; Windows keeps
        ordered case-equivalent occurrences, RFC 0009 §6,
        docs/rfcs/0009-...:207-213)."""
        if self.document.profile is IniProfile.WINDOWS_V1:
            return
        comparison = (
            optionxform(key)
            if self.document.profile is IniProfile.PYTHON_CONFIGPARSER_V1
            else key
        )
        for entry in self.document.entries:
            if (
                entry.section == section
                and entry.node != except_target
                and entry.comparison_key == comparison
            ):
                if entry.key == key:
                    raise IniEditFailure(IniEditFailureKind.DUPLICATE_KEY)
                raise IniEditFailure(IniEditFailureKind.KEY_COLLISION)

    # -- position helpers ---------------------------------------------------

    def entry_line_start(self, entry: IniEntry) -> int:
        return self._first_physical_span(entry.logical_line).start_byte

    def entry_line_end(self, entry: IniEntry) -> int:
        return self._last_physical_span(entry.logical_line).end_byte

    def section_line_start(self, section: IniSection) -> int:
        return self._first_physical_span(section.logical_line).start_byte

    def section_content_end(self, target: NodeRef) -> int:
        ordinal = next(
            index
            for index, section in enumerate(self.document.sections)
            if section.node == target
        )
        if ordinal + 1 < len(self.document.sections):
            return self.section_line_start(self.document.sections[ordinal + 1])
        return self.document.source.len()

    def _first_physical_span(self, logical_node: NodeRef) -> Span:
        logical = self.document.resolve_logical_line(logical_node)
        if not logical.physical_nodes:
            raise IniEditFailure(IniEditFailureKind.TARGET_NOT_FOUND)
        return self.document.resolve_physical_line(logical.physical_nodes[0]).span

    def _last_physical_span(self, logical_node: NodeRef) -> Span:
        logical = self.document.resolve_logical_line(logical_node)
        if not logical.physical_nodes:
            raise IniEditFailure(IniEditFailureKind.TARGET_NOT_FOUND)
        return self.document.resolve_physical_line(logical.physical_nodes[-1]).span

    def logical_physical_spans(self, logical_node: NodeRef) -> list[Span]:
        """Every physical span of one logical record (edit.rs:1178-1194)."""
        logical = self.document.resolve_logical_line(logical_node)
        spans = []
        for physical_node in logical.physical_nodes:
            spans.append(self.document.resolve_physical_line(physical_node).span)
        return spans

    # -- rendering ----------------------------------------------------------

    def canonical_entry_text(self, key: str, value: str) -> str:
        """Profile-canonical entry text including its newline
        (edit.rs:1101-1167)."""
        continuation_overhead = (
            value.count("\n") * 4
            if self.document.profile is IniProfile.PYTHON_CONFIGPARSER_V1
            else 0
        )
        estimated = (
            len(key.encode("utf-8"))
            + len(value.encode("utf-8"))
            + continuation_overhead
            + 8
        )
        if estimated > self.document.parse_limits.common.max_source_bytes:
            raise IniEditFailure(
                IniEditFailureKind.RESOURCE_LIMIT, resource_name="replacement-bytes"
            )
        text = key
        if self.document.profile is IniProfile.PORTABLE_V1:
            text += "="
            text += value
        elif self.document.profile is IniProfile.WINDOWS_V1:
            text += "="
            if windows_value_needs_quotes(value):
                quote = "'" if value.startswith('"') and value.endswith('"') else '"'
                text += quote + value + quote
            else:
                text += value
        else:
            text += " ="
            for index, line in enumerate(value.split("\n")):
                if index == 0:
                    if line:
                        text += " "
                else:
                    text += "\n"
                    if line:
                        text += "    "
                text += line
        text += _profile_newline(self.document.profile)
        if len(text.encode("utf-8")) > self.document.parse_limits.common.max_source_bytes:
            raise IniEditFailure(
                IniEditFailureKind.RESOURCE_LIMIT, resource_name="replacement-bytes"
            )
        return text

    def semantic_value(
        self,
        entry: IniEntry,
        value: str,
        policy: RepresentationPolicy,
        diagnostics: list[IniDiagnostic],
    ) -> bytes:
        """Renders the semantic replacement under the explicit policy
        (edit.rs:1228-1259)."""
        if policy is RepresentationPolicy.EXACT_LITERAL:
            raise IniEditFailure(IniEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION)
        validate_semantic_value(self.document.profile, value)
        if policy is RepresentationPolicy.PRESERVE_COMPATIBLE:
            return self.preserved_value(entry, value)
        if policy is RepresentationPolicy.CANONICAL_FOR_PROFILE:
            return self.canonical_value(entry, value)
        # PreserveElseCanonical
        try:
            return self.preserved_value(entry, value)
        except IniEditFailure as failure:
            if failure.kind is not IniEditFailureKind.REPRESENTATION_INCOMPATIBLE:
                raise
        diagnostics.append(
            IniDiagnostic(
                code="ini.edit.canonical-fallback@1",
                category=DiagnosticCategory.EDIT,
                severity=IniSeverity.WARNING,
                primary=entry.value_span,
                occurrence=len(diagnostics),
            )
        )
        return self.canonical_value(entry, value)

    def preserved_value(self, entry: IniEntry, value: str) -> bytes:
        """Compatible representation retention (edit.rs:1261-1284)."""
        if self.document.profile is IniProfile.PORTABLE_V1:
            return self.encode_value(value)
        if self.document.profile is IniProfile.WINDOWS_V1:
            if entry.quote_style is IniQuoteStyle.SINGLE:
                return self.encode_value(f"'{value}'")
            if entry.quote_style is IniQuoteStyle.DOUBLE:
                return self.encode_value(f'"{value}"')
            if not windows_value_needs_quotes(value):
                return self.encode_value(value)
            raise IniEditFailure(IniEditFailureKind.REPRESENTATION_INCOMPATIBLE)
        return self.preserved_python_value(entry, value)

    def canonical_value(self, entry: IniEntry, value: str) -> bytes:
        """Profile-canonical value representation (edit.rs:1286-1303)."""
        if self.document.profile is IniProfile.PORTABLE_V1:
            return self.encode_value(value)
        if self.document.profile is IniProfile.WINDOWS_V1:
            if windows_value_needs_quotes(value):
                quote = "'" if value.startswith('"') and value.endswith('"') else '"'
                return self.encode_value(quote + value + quote)
            return self.encode_value(value)
        return self.canonical_python_value(entry, value)

    def preserved_python_value(self, entry: IniEntry, value: str) -> bytes:
        """Line-for-line multiline preservation (edit.rs:1305-1385)."""
        logical = self.document.resolve_logical_line(entry.logical_line)
        physical = logical.physical_nodes
        new_lines = value.split("\n")
        old_lines = entry.value.split("\n")
        if len(physical) != len(new_lines) or len(old_lines) != len(new_lines):
            raise IniEditFailure(IniEditFailureKind.REPRESENTATION_INCOMPATIBLE)
        output = bytearray()
        output.extend(self.encode_value(new_lines[0]))
        first = self.document.resolve_physical_line(physical[0])
        output.extend(
            self.raw(entry.value_span.end_byte, first.content_span.end_byte)
        )
        for index in range(1, len(physical)):
            previous = self.document.resolve_physical_line(physical[index - 1])
            line_break = previous.line_break_span
            if line_break is None:
                raise IniEditFailure(IniEditFailureKind.REPRESENTATION_INCOMPATIBLE)
            output.extend(self.raw(line_break.start_byte, line_break.end_byte))
            line = self.document.resolve_physical_line(physical[index])
            if bool(old_lines[index]) != bool(new_lines[index]):
                raise IniEditFailure(IniEditFailureKind.REPRESENTATION_INCOMPATIBLE)
            if not new_lines[index]:
                output.extend(
                    self.raw(line.content_span.start_byte, line.content_span.end_byte)
                )
                continue
            value_piece = self.syntax_span(IniSyntaxKind.ENTRY_VALUE, line.content_span)
            if value_piece is None:
                raise IniEditFailure(IniEditFailureKind.REPRESENTATION_INCOMPATIBLE)
            output.extend(
                self.raw(line.content_span.start_byte, value_piece.start_byte)
            )
            output.extend(self.encode_value(new_lines[index]))
            output.extend(self.raw(value_piece.end_byte, line.content_span.end_byte))
        return bytes(output)

    def canonical_python_value(self, entry: IniEntry, value: str) -> bytes:
        """Canonical Python multiline value (edit.rs:1387-1430)."""
        first = self.document.resolve_physical_line(
            self.document.resolve_logical_line(entry.logical_line).physical_nodes[0]
        )
        base_indent = self.raw(first.content_span.start_byte, entry.key_span.start_byte)
        output = bytearray()
        for index, line in enumerate(value.split("\n")):
            if index > 0:
                output.extend(self.encode_value("\n"))
                output.extend(base_indent)
                if line:
                    output.extend(self.encode_value("    "))
            output.extend(self.encode_value(line))
        return bytes(output)

    def encode_value(self, value: str) -> bytes:
        """Exact encoding under the source encoding (edit.rs:1432-1443)."""
        try:
            return encode_fragment(
                value,
                self.document.source.encoding_facts().selected,
                self.document.parse_limits.common.max_source_bytes,
            )
        except MaterializationFailure as failure:
            if failure.kind.value == "resource-limit":
                raise IniEditFailure(
                    IniEditFailureKind.RESOURCE_LIMIT, resource_name=failure.name
                ) from None
            if failure.kind.value == "unsupported-encoding":
                raise IniEditFailure(IniEditFailureKind.ENCODING_UNREPRESENTABLE) from None
            raise IniEditFailure(IniEditFailureKind.UNREPRESENTABLE_VALUE) from None

    def syntax_span(self, kind: IniSyntaxKind, within: Span) -> Span | None:
        """First syntax piece of one kind within a raw range
        (edit.rs:1496-1508)."""
        for piece, candidate in zip(
            self.document.structural_index.pieces, self.document.syntax_kinds
        ):
            span = piece.span
            if (
                candidate is kind
                and span.start_byte >= within.start_byte
                and span.end_byte <= within.end_byte
            ):
                return span
        return None

    def raw(self, start: int, end: int) -> bytes:
        """Exact raw byte slice (edit.rs:1510-1515)."""
        raw_bytes = self.document.source.bytes()
        if start < 0 or end > len(raw_bytes) or start > end:
            raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return raw_bytes[start:end]


# ---------------------------------------------------------------------------
# Dependency validation (edit.rs:863-920)
# ---------------------------------------------------------------------------


def validate_dependencies(document: IniDocument, transaction: EditTransaction) -> None:
    """Cross-operation conflicts before any patch exists (edit.rs:863-920;
    RFC 0009 §12, docs/rfcs/0009-...:468-472)."""
    removed_sections = {
        operation.target
        for operation in transaction.operations
        if operation.kind is EditOperationKind.REMOVE_SECTION
    }
    removed_entries = {
        operation.target
        for operation in transaction.operations
        if operation.kind is EditOperationKind.REMOVE_ENTRY
    }
    for operation in transaction.operations:
        if operation.kind is EditOperationKind.INSERT_SECTION and operation.placement is not None:
            if operation.placement.kind in ("Before", "After") and operation.placement.anchor in removed_sections:
                raise IniEditFailure(IniEditFailureKind.PLACEMENT_ANCHOR_REMOVED)
        if operation.kind is EditOperationKind.INSERT_ENTRY:
            assert operation.section is not None
            if operation.placement is not None and operation.placement.kind in ("Before", "After") and operation.placement.anchor in removed_entries:
                raise IniEditFailure(IniEditFailureKind.PLACEMENT_ANCHOR_REMOVED)
            if operation.section in removed_sections:
                raise IniEditFailure(IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
        if operation.kind is EditOperationKind.REPLACE_VALUE:
            assert operation.replacement is not None
            entry = _find_entry(document, operation.replacement.target)
            if entry is not None and entry.section in removed_sections:
                raise IniEditFailure(IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
        if operation.kind in (
            EditOperationKind.REMOVE_ENTRY,
            EditOperationKind.RENAME_ENTRY,
        ):
            assert operation.target is not None
            entry = _find_entry(document, operation.target)
            if entry is not None and entry.section in removed_sections:
                raise IniEditFailure(IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)


def _find_entry(document: IniDocument, target: NodeRef) -> IniEntry | None:
    for entry in document.entries:
        if entry.node == target:
            return entry
    return None


# ---------------------------------------------------------------------------
# Commit and dry-run (edit.rs:305-570)
# ---------------------------------------------------------------------------


def commit(document: IniDocument, transaction: EditTransaction) -> EditCommit:
    """Atomically commits value and structural operations; on failure the
    base document remains unchanged (edit.rs:305-553)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise IniEditFailure(IniEditFailureKind.RECOVERED_DOCUMENT)
    if transaction.base != document.snapshot_identity():
        raise IniEditFailure(IniEditFailureKind.WRONG_SNAPSHOT)
    if len(transaction.operations) > document.parse_limits.common.max_node_count:
        raise IniEditFailure(IniEditFailureKind.RESOURCE_LIMIT, resource_name="edit-operations")
    validate_dependencies(document, transaction)
    targets: set[NodeRef] = set()
    diagnostics: list[IniDiagnostic] = []
    planner = _EditPlanner(document)
    prepared: list[_PreparedEdit] = []
    for operation in transaction.operations:
        target = _destructive_target(operation)
        if target is not None:
            if target in targets:
                raise IniEditFailure(IniEditFailureKind.DUPLICATE_TARGET)
            targets.add(target)
        prepared.extend(planner.prepare_operation(operation, diagnostics))
    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    prepared = _coalesce_adjacent_deletions(document, prepared)
    for index in range(len(prepared) - 1):
        left, right = prepared[index], prepared[index + 1]
        if left.old_span == right.old_span:
            raise IniEditFailure(IniEditFailureKind.OVERLAPPING_OWNERSHIP)
        if left.old_span.end_byte > right.old_span.start_byte:
            raise IniEditFailure(IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
    literal_only = bool(transaction.operations) and all(
        operation.kind is EditOperationKind.REPLACE_VALUE
        and operation.replacement is not None
        and operation.replacement.kind is ValueReplacementKind.LITERAL
        for operation in transaction.operations
    )
    raw = document.source.bytes()
    target_len = len(raw)
    for edit in prepared:
        target_len = target_len - edit.old_span.len() + len(edit.replacement)
        if target_len < 0:
            raise IniEditFailure(IniEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    if target_len > document.parse_limits.common.max_source_bytes:
        raise IniEditFailure(IniEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    rendered = bytearray()
    cursor = 0
    for edit in prepared:
        rendered.extend(raw[cursor : edit.old_span.start_byte])
        rendered.extend(edit.replacement)
        cursor = edit.old_span.end_byte
    rendered.extend(raw[cursor:])
    try:
        new_document = parse(
            bytes(rendered),
            document.profile,
            _original_encoding_selection(document),
            document.parse_limits,
        )
    except Exception:
        raise IniEditFailure(
            IniEditFailureKind.INVALID_LITERAL if literal_only else IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        ) from None
    if new_document.formation_status() is not FormationStatus.COMPLETE:
        raise IniEditFailure(
            IniEditFailureKind.INVALID_LITERAL if literal_only else IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        )

    delta = 0
    source_edits: list[SourceEdit] = []
    mappings: list[NodeMapping] = []
    mapped_old: set[NodeRef] = set()
    for edit in prepared:
        new_start = edit.old_span.start_byte + delta
        new_end = new_start + len(edit.replacement)
        new_span = new_document.authority.span(new_start, new_end)
        source_edits.append(
            SourceEdit(old_span=edit.old_span, new_span=new_span, replacement=edit.replacement)
        )
        for old, plan in edit.mappings:
            if old in mapped_old:
                continue
            mapped_old.add(old)
            if plan.kind is _MappingPlanKind.REPLACED_VALUE:
                new_entry = next(
                    (
                        candidate
                        for candidate in new_document.entries
                        if candidate.key == plan.expected
                        and _value_ownership(new_document, candidate) == new_span
                    ),
                    None,
                )
                if new_entry is None:
                    raise IniEditFailure(
                        IniEditFailureKind.INVALID_LITERAL
                        if plan.literal
                        else IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
                    )
                mappings.append(
                    NodeMapping(
                        old=old,
                        new=new_entry.node,
                        status=NodeMappingStatus.REPLACED,
                    )
                )
            elif plan.kind is _MappingPlanKind.REPLACED_SECTION:
                new_section = next(
                    (
                        candidate
                        for candidate in new_document.sections
                        if candidate.name == plan.expected and candidate.name_span == new_span
                    ),
                    None,
                )
                if new_section is None:
                    raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                mappings.append(
                    NodeMapping(
                        old=old,
                        new=new_section.node,
                        status=NodeMappingStatus.REPLACED,
                    )
                )
            elif plan.kind is _MappingPlanKind.REPLACED_ENTRY:
                new_entry = next(
                    (
                        candidate
                        for candidate in new_document.entries
                        if candidate.key == plan.expected and candidate.key_span == new_span
                    ),
                    None,
                )
                if new_entry is None:
                    raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                mappings.append(
                    NodeMapping(
                        old=old,
                        new=new_entry.node,
                        status=NodeMappingStatus.REPLACED,
                    )
                )
            elif plan.kind is _MappingPlanKind.SECTION_AFTER_ENTRY_INSERTION:
                inserted = any(
                    candidate.key == plan.expected
                    and candidate.value == plan.expected_value
                    and _entry_record_span(new_document, candidate).start_byte >= new_span.start_byte
                    and _entry_record_span(new_document, candidate).end_byte == new_span.end_byte
                    for candidate in new_document.entries
                )
                if not inserted:
                    raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
                mappings.append(
                    NodeMapping(
                        old=old,
                        new=None,
                        status=NodeMappingStatus.UNMAPPED,
                        reason="section-reparsed-after-entry-insertion",
                    )
                )
            elif plan.kind is _MappingPlanKind.DELETED:
                mappings.append(
                    NodeMapping(old=old, new=None, status=NodeMappingStatus.DELETED)
                )
            else:
                mappings.append(
                    NodeMapping(
                        old=old,
                        new=None,
                        status=NodeMappingStatus.UNMAPPED,
                        reason=plan.reason,
                    )
                )
        delta += len(edit.replacement) - edit.old_span.len()

    change_set = ChangeSet(
        old_snapshot=document.snapshot_identity(),
        new_snapshot=new_document.snapshot_identity(),
        source_edits=tuple(source_edits),
        node_mappings=tuple(mappings),
        diagnostics=tuple(diagnostics),
    )
    patch_limits = _source_patch_limits(document.parse_limits, len(source_edits))
    try:
        source_patch = SourcePatch.derive(
            document.source,
            new_document.source,
            change_set,
            operation_metadata(transaction),
            patch_limits,
        )
    except Exception:
        raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    try:
        untouched_proof = UntouchedByteProof.create(
            document.source, new_document.source, list(source_patch.replacements)
        )
    except Exception:
        raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def dry_run(
    document: IniDocument,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:556-570)."""
    commit_result = commit(document, transaction)
    try:
        return EditPlan.new(
            source_id,
            document.profile_id(),
            operation_summaries(transaction),
            commit_result.source_patch,
            list(commit_result.change_set.diagnostics),
        )
    except Exception:
        raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


def _source_patch_limits(parse_limits, operation_count: int) -> SourcePatchLimits:
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=parse_limits.common.max_source_bytes,
            max_decoded_utf8_bytes=parse_limits.max_decoded_utf8_bytes,
            max_decoded_scalars=parse_limits.max_decoded_scalars,
        ),
        max_replacements=operation_count,
        max_patch_bytes=parse_limits.common.max_source_bytes * 2,
    )


def operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Operation metadata keys: operation.{index} = "id@version"
    (edit.rs:1604-1627)."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


def _operation_id(operation: EditOperation) -> str:
    if operation.kind is EditOperationKind.REPLACE_VALUE:
        assert operation.replacement is not None
        if operation.replacement.kind is ValueReplacementKind.SEMANTIC:
            return "ini.edit.replace-semantic-value@1"
        return "ini.edit.replace-literal-value@1"
    return {
        EditOperationKind.INSERT_SECTION: "ini.edit.insert-section@1",
        EditOperationKind.REMOVE_SECTION: "ini.edit.remove-section@1",
        EditOperationKind.RENAME_SECTION: "ini.edit.rename-section@1",
        EditOperationKind.INSERT_ENTRY: "ini.edit.insert-entry@1",
        EditOperationKind.REMOVE_ENTRY: "ini.edit.remove-entry@1",
        EditOperationKind.RENAME_ENTRY: "ini.edit.rename-entry@1",
    }[operation.kind]


def operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe, content-free operation summaries (edit.rs:1629-1702)."""
    summaries = []
    for operation in transaction.operations:
        id_string, arguments = _summary_facts(operation)
        summaries.append(
            EditOperationSummary.new(
                FormatOperationId.new(id_string, 1),
                arguments,
            )
        )
    return summaries


def _summary_facts(operation: EditOperation) -> tuple[str, dict[str, str]]:
    if operation.kind is EditOperationKind.REPLACE_VALUE:
        replacement = operation.replacement
        assert replacement is not None
        if replacement.kind is ValueReplacementKind.SEMANTIC:
            assert replacement.value is not None and replacement.policy is not None
            return (
                "ini.edit.replace-semantic-value",
                {
                    "representation_policy": _policy_name(replacement.policy),
                    "value_scalars": str(len(replacement.value)),
                },
            )
        assert replacement.literal is not None
        return (
            "ini.edit.replace-literal-value",
            {"literal_bytes": str(len(replacement.literal))},
        )
    if operation.kind is EditOperationKind.INSERT_SECTION:
        assert operation.name is not None and operation.placement is not None
        return (
            "ini.edit.insert-section",
            {
                "name_scalars": str(len(operation.name)),
                "placement": _placement_name(operation.placement),
            },
        )
    if operation.kind is EditOperationKind.REMOVE_SECTION:
        return ("ini.edit.remove-section", {})
    if operation.kind is EditOperationKind.RENAME_SECTION:
        assert operation.name is not None
        return ("ini.edit.rename-section", {"name_scalars": str(len(operation.name))})
    if operation.kind is EditOperationKind.INSERT_ENTRY:
        assert operation.key is not None and operation.value is not None
        assert operation.placement is not None
        return (
            "ini.edit.insert-entry",
            {
                "key_scalars": str(len(operation.key)),
                "placement": _placement_name(operation.placement),
                "value_scalars": str(len(operation.value)),
            },
        )
    if operation.kind is EditOperationKind.REMOVE_ENTRY:
        return ("ini.edit.remove-entry", {})
    assert operation.key is not None
    return ("ini.edit.rename-entry", {"key_scalars": str(len(operation.key))})


def _policy_name(policy: RepresentationPolicy) -> str:
    return {
        RepresentationPolicy.EXACT_LITERAL: "exact-literal",
        RepresentationPolicy.PRESERVE_COMPATIBLE: "preserve-compatible",
        RepresentationPolicy.CANONICAL_FOR_PROFILE: "canonical-for-profile",
        RepresentationPolicy.PRESERVE_ELSE_CANONICAL: "preserve-else-canonical",
    }[policy]


def _placement_name(placement: AssociationPlacement) -> str:
    return {
        "Start": "start",
        "End": "end",
        "Before": "before",
        "After": "after",
    }[placement.kind]


# ---------------------------------------------------------------------------
# Shared helpers (edit.rs:1518-1602)
# ---------------------------------------------------------------------------


def validate_semantic_value(profile: IniProfile, value: str) -> None:
    """Stored-value representability (edit.rs:1518-1535)."""
    if profile is IniProfile.PORTABLE_V1:
        valid = all(is_portable_value(byte) for byte in value.encode("utf-8"))
    elif profile is IniProfile.WINDOWS_V1:
        valid = not any(character in value for character in "\0\r\n")
    else:
        lines = value.split("\n")
        valid = (
            not any(character in value for character in "\0\r")
            and not value.endswith("\n")
            and all(
                line.strip(" \t") == line
                and (index == 0 or not line.startswith(("#", ";")))
                for index, line in enumerate(lines)
            )
        )
    if not valid:
        raise IniEditFailure(IniEditFailureKind.UNREPRESENTABLE_VALUE)


def _destructive_target(operation: EditOperation) -> NodeRef | None:
    """One exact destructive target per operation (edit.rs:1537-1546)."""
    if operation.kind is EditOperationKind.REPLACE_VALUE:
        assert operation.replacement is not None
        return operation.replacement.target
    if operation.kind in (
        EditOperationKind.REMOVE_SECTION,
        EditOperationKind.RENAME_SECTION,
        EditOperationKind.REMOVE_ENTRY,
        EditOperationKind.RENAME_ENTRY,
    ):
        return operation.target
    return None


def _deletion_edit(span: Span, target: NodeRef | None) -> _PreparedEdit:
    """One record deletion span (edit.rs:1548-1561)."""
    return _PreparedEdit(
        old_span=span,
        replacement=b"",
        mappings=(
            ((target, _MappingPlan(_MappingPlanKind.DELETED)),)
            if target is not None
            else ()
        ),
        mergeable_deletion=True,
    )


def _profile_newline(profile: IniProfile) -> str:
    """Profile-canonical newline (edit.rs:1563-1568)."""
    if profile is IniProfile.WINDOWS_V1:
        return "\r\n"
    return "\n"


def _ends_with_newline(document: IniDocument) -> bool:
    """Whether the decoded source already ends with a line ending
    (edit.rs:681-688, 803-810)."""
    text = document.source.decoded_text()
    return text is not None and text.endswith(("\n", "\r"))


def _original_encoding_selection(document: IniDocument) -> object:
    """Reparse with the original encoding contract (edit.rs:1570-1575)."""
    from consema.ini.kinds import IniEncodingSelection

    facts = document.source.encoding_facts()
    if facts.caller_override is not None:
        return IniEncodingSelection.explicit(facts.caller_override)
    return IniEncodingSelection.profile_default()


def _coalesce_adjacent_deletions(
    document: IniDocument, edits: list[_PreparedEdit]
) -> list[_PreparedEdit]:
    """Merges adjacent deletion spans (edit.rs:1196-1226)."""
    merged: list[_PreparedEdit] = []
    for edit in edits:
        merge = bool(merged) and (
            merged[-1].mergeable_deletion
            and edit.mergeable_deletion
            and merged[-1].old_span.end_byte == edit.old_span.start_byte
        )
        if merge:
            previous = merged[-1]
            merged[-1] = _PreparedEdit(
                old_span=document.authority.span(
                    previous.old_span.start_byte, edit.old_span.end_byte
                ),
                replacement=b"",
                mappings=previous.mappings + edit.mappings,
                mergeable_deletion=True,
            )
        else:
            merged.append(edit)
    return merged


def _value_ownership(document: IniDocument, entry: IniEntry) -> Span:
    """Exact raw range owned by one value replacement (edit.rs:1445-1475)."""
    if document.profile is IniProfile.PORTABLE_V1:
        start = entry.value_span.start_byte
        end = entry.value_span.end_byte
    elif document.profile is IniProfile.WINDOWS_V1:
        delimiter = _syntax_span(document, IniSyntaxKind.DELIMITER, entry.span)
        if delimiter is None:
            raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        start = delimiter.end_byte
        end = entry.span.end_byte
    else:
        logical = document.resolve_logical_line(entry.logical_line)
        if not logical.physical_nodes:
            raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        last = document.resolve_physical_line(logical.physical_nodes[-1])
        start = entry.value_span.start_byte
        end = last.content_span.end_byte
    return document.authority.span(start, end)


def _entry_record_span(document: IniDocument, entry: IniEntry) -> Span:
    """First-to-last physical span of one entry record (edit.rs:1477-1494)."""
    logical = document.resolve_logical_line(entry.logical_line)
    if not logical.physical_nodes:
        raise IniEditFailure(IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    first = document.resolve_physical_line(logical.physical_nodes[0])
    last = document.resolve_physical_line(logical.physical_nodes[-1])
    return document.authority.span(first.span.start_byte, last.span.end_byte)


def _syntax_span(document: IniDocument, kind: IniSyntaxKind, within: Span) -> Span | None:
    """First syntax piece of one kind within a raw range (edit.rs:1496-1508)."""
    for piece, candidate in zip(document.structural_index.pieces, document.syntax_kinds):
        span = piece.span
        if (
            candidate is kind
            and span.start_byte >= within.start_byte
            and span.end_byte <= within.end_byte
        ):
            return span
    return None


def _section_name_valid(profile: IniProfile, name: str) -> bool:
    if profile is IniProfile.PORTABLE_V1:
        return bool(name) and all(is_portable_name(byte) for byte in name.encode("utf-8"))
    if profile is IniProfile.WINDOWS_V1:
        return bool(name) and all(is_windows_name(byte) for byte in name.encode("utf-8"))
    return bool(name) and not any(character in name for character in "\0\r\n")


def _entry_key_valid(profile: IniProfile, key: str) -> bool:
    if profile is IniProfile.PORTABLE_V1:
        return bool(key) and all(is_portable_name(byte) for byte in key.encode("utf-8"))
    if profile is IniProfile.WINDOWS_V1:
        return (
            bool(key)
            and key.strip(" \t") == key
            and all(is_windows_name(byte) for byte in key.encode("utf-8"))
        )
    return (
        bool(key)
        and key.strip(" \t") == key
        and not any(character in key for character in "\0\r\n=:")
        and not key.startswith(("#", ";"))
    )
