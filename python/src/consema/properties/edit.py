"""Java Properties structural edit transactions: five frozen operations.

Authority (Rust arbitration for exact byte semantics):

- Operation and transaction model: https://github.com/consema/consema-rs/blob/main/consema-properties/src/edit.rs
 (EditOperation), (EditTransaction/Builder).
- Failure algebra and codes: edit.rs (EditFailure; StableFailure
  code mapping edit.rs).
- Atomic commit: edit.rs — Complete/WrongSnapshot gates
  (edit.rs), removed-anchor validation (edit.rs),
  per-operation preparation (semantic edit.rs with the canonical
  fallback warning java-properties.edit.canonical-fallback@1 at
  edit.rs and preserve_direct_value edit.rs; literal
  edit.rs with validate_literal edit.rs; insertion
  edit.rs with insertion_location edit.rs and
  canonical_record edit.rs; removal edit.rs with
  record_ownership edit.rs; rename edit.rs),
  non-overlapping ownership (edit.rs), bounded target length and
  rendering (edit.rs), reparse closure (edit.rs),
  ChangeSet source edits/node mappings (edit.rs), SourcePatch
  derivation (edit.rs), UntouchedByteProof (edit.rs).
- Dry-run: edit.rs (identical patch and target digest; RFC 0004
  section 14).
- Operation metadata and summaries: edit.rs
  (operation.{index} = "java-properties.edit.*@1"; safe summaries).
- Newline convention: edit.rs (first CR -> CRLF/CR, first LF ->
  LF, default LF); the line-boundary test edit.rs.
- Literal ownership: edit.rs (one exact value ownership
  interval, no delimiter/comment/newline consumption; RFC 0010 section 13,
  https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md).
- The five frozen operation ids (RFC 0010 section 13,
  https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md; operation_registry.rs):
  java-properties.edit.replace-semantic-value@1,
  replace-literal-value@1, insert-property@1, remove-property@1,
  rename-property@1.

Golden transcription targets: conformance/vectors/java-properties-v1.json
cases edit.all-five-operations (lines 106-109) and
edit.dry-run-patch-proof-conflict-atomicity (lines 111-114).
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
from consema.document.materialization import (
    MaterializationFailure,
    MaterializationFailureKind,
)
from consema.document.edit_plan import (
    EditOperationSummary,
    EditPlan,
    EditPlanSourceId,
)
from consema.document.ids import FormatOperationId
from consema.document.source import (
    BomPolicy,
    EncodingRequest,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    AssociationPlacement,
    FormationStatus,
    NodeRef,
    NodeRole,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof
from consema.properties.document import PropertiesDocument
from consema.properties.errors import (
    PropertiesDiagnostic,
    PropertiesEditFailure,
    PropertiesEditFailureKind,
    PropertiesSeverity,
)
from consema.properties.java_string import JavaString
from consema.properties.kinds import PropertiesProfile
from consema.properties.limits import (
    PropertiesEncodingSelection,
    PropertiesEncodingSelectionKind,
    PropertiesParseLimits,
)
from consema.properties.materialization import canonical_fragment
from consema.properties.parser import parse
from consema.protocol.error_registry import DiagnosticCategory


class EditOperationKind(enum.Enum):
    """Typed edit operation kinds (edit.rs)."""

    REPLACE_SEMANTIC_VALUE = "ReplaceSemanticValue"
    REPLACE_LITERAL_VALUE = "ReplaceLiteralValue"
    INSERT_PROPERTY = "InsertProperty"
    REMOVE_PROPERTY = "RemoveProperty"
    RENAME_PROPERTY = "RenameProperty"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One typed Java Properties structural edit operation (edit.rs)."""

    kind: EditOperationKind
    target: NodeRef | None = None
    value: JavaString | None = None
    literal: bytes | None = None
    document: NodeRef | None = None
    key: JavaString | None = None
    placement: AssociationPlacement | None = None

    @property
    def destructive_target(self) -> NodeRef | None:
        if self.kind in (
            EditOperationKind.REPLACE_SEMANTIC_VALUE,
            EditOperationKind.REPLACE_LITERAL_VALUE,
            EditOperationKind.REMOVE_PROPERTY,
            EditOperationKind.RENAME_PROPERTY,
        ):
            return self.target
        return None

    @property
    def operation_id(self) -> str:
        return _OPERATION_ID_BY_KIND[self.kind]


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable edit transaction; every operation resolves against one
    base snapshot (edit.rs)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs)."""

    def __init__(self, document: PropertiesDocument) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def semantic_value(self, target: NodeRef, value: JavaString) -> EditTransactionBuilder:
        """Replaces one property's semantic Java UTF-16 value
        (edit.rs)."""
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_SEMANTIC_VALUE,
                target=target,
                value=value,
            )
        )
        return self

    def literal_value(self, target: NodeRef, literal: bytes) -> EditTransactionBuilder:
        """Replaces one property's exact raw value literal (edit.rs)."""
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_LITERAL_VALUE,
                target=target,
                literal=bytes(literal),
            )
        )
        return self

    def insert_property(
        self,
        document: NodeRef,
        key: JavaString,
        value: JavaString,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        """Inserts one canonical property occurrence (edit.rs)."""
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_PROPERTY,
                document=document,
                key=key,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_property(self, target: NodeRef) -> EditTransactionBuilder:
        """Removes one exact property occurrence and all its natural lines
        (edit.rs)."""
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_PROPERTY, target=target)
        )
        return self

    def rename_property(self, target: NodeRef, key: JavaString) -> EditTransactionBuilder:
        """Replaces one exact property's semantic Java UTF-16 key
        (edit.rs)."""
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.RENAME_PROPERTY, target=target, key=key
            )
        )
        return self

    def build(self) -> EditTransaction:
        """Completes the request; validation remains atomic at dry-run or
        commit (edit.rs)."""
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """Atomic edit success (edit.rs)."""

    document: PropertiesDocument
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# -- internal preparation records --------------------------------------------


@dataclass(frozen=True, slots=True)
class _ExpectedProperty:
    """The expected post-commit property fact (edit.rs)."""

    old: NodeRef | None
    key: JavaString
    value: JavaString | None
    literal: bool
    literal_old_span: Span | None
    removed: bool


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    """One planned raw-byte replacement (edit.rs)."""

    old_span: Span
    replacement: bytes


class _EditPlanner:
    """One planner bound to the base document (edit.rs)."""

    def __init__(self, document: PropertiesDocument) -> None:
        self.document = document

    # -- resolution ---------------------------------------------------------

    def property_ordinal(self, target: NodeRef) -> int:
        """Resolves one property target (edit.rs)."""
        if target.snapshot != self.document.snapshot_identity():
            raise PropertiesEditFailure(PropertiesEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.PROPERTIES_PROPERTY:
            raise PropertiesEditFailure(PropertiesEditFailureKind.WRONG_ROLE)
        for ordinal, property in enumerate(self.document.properties):
            if property.node == target:
                return ordinal
        raise PropertiesEditFailure(PropertiesEditFailureKind.TARGET_NOT_FOUND)

    def validate_document_target(self, target: NodeRef) -> None:
        """Resolves one document target (edit.rs)."""
        if target.snapshot != self.document.snapshot_identity():
            raise PropertiesEditFailure(PropertiesEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.PROPERTIES_DOCUMENT:
            raise PropertiesEditFailure(PropertiesEditFailureKind.WRONG_ROLE)
        if target != self.document.node_ref():
            raise PropertiesEditFailure(PropertiesEditFailureKind.TARGET_NOT_FOUND)

    def validate_removed_anchors(self, transaction: EditTransaction) -> None:
        """An insertion anchored to a removed property is a conflict
        (edit.rs)."""
        removed = {
            operation.target
            for operation in transaction.operations
            if operation.kind is EditOperationKind.REMOVE_PROPERTY
        }
        for operation in transaction.operations:
            if operation.kind is not EditOperationKind.INSERT_PROPERTY:
                continue
            placement = operation.placement
            if placement is None:
                continue
            if (
                placement.kind in ("Before", "After")
                and placement.anchor in removed
            ):
                raise PropertiesEditFailure(
                    PropertiesEditFailureKind.PLACEMENT_ANCHOR_REMOVED
                )

    # -- ownership ----------------------------------------------------------

    def record_ownership(self, property) -> Span:
        """The property's complete natural-line record range including
        terminators (edit.rs)."""
        logical = self.document.logical_line(property.logical_line)
        first = logical.natural_lines[0]
        last = logical.natural_lines[-1]
        try:
            first_line = self.document.natural_line(first)
            last_line = self.document.natural_line(last)
        except Exception:
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.TARGET_NOT_FOUND
            ) from None
        return self.document.authority.span(
            first_line.span.start_byte, last_line.span.end_byte
        )

    def key_ownership(self, property) -> Span:
        """The raw key ownership interval (edit.rs)."""
        return _fragment_ownership(
            self.document, property.key_fragments, property.key_anchor
        )

    def value_ownership(self, property) -> Span:
        """The raw value ownership interval (edit.rs)."""
        return _fragment_ownership(
            self.document, property.value_fragments, property.value_anchor
        )

    def insertion_location(
        self, placement: AssociationPlacement
    ) -> tuple[int, int]:
        """(boundary, raw position) for one insertion (edit.rs)."""
        count = len(self.document.properties)
        if placement.kind == "Start":
            if self.document.properties:
                return (
                    0,
                    self.record_ownership(self.document.properties[0]).start_byte,
                )
            return 0, len(self.document.render())
        if placement.kind == "End":
            return count, len(self.document.render())
        if placement.kind == "Before":
            ordinal = self.property_ordinal(placement.anchor)
            return (
                ordinal,
                self.record_ownership(self.document.properties[ordinal]).start_byte,
            )
        if placement.kind == "After":
            ordinal = self.property_ordinal(placement.anchor)
            return (
                ordinal + 1,
                self.record_ownership(self.document.properties[ordinal]).end_byte,
            )
        raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_PLACEMENT)

    # -- literals -----------------------------------------------------------

    def validate_literal(self, literal: bytes) -> None:
        """Literal bytes must form exactly one raw value element without
        line breaks (edit.rs)."""
        if len(literal) > self.document.parse_limits.common.max_source_bytes:
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.RESOURCE_LIMIT,
                resource_name="replacement-bytes",
            )
        encoding = self.document.source.encoding_facts().selected
        request = (
            EncodingRequest.new(encoding)
            .with_caller_override(encoding)
            .with_bom_policy(BomPolicy.TREAT_AS_CONTENT)
        )
        try:
            snapshot = SourceSnapshot.from_raw(
                literal,
                request,
                SourceLimits(
                    max_raw_bytes=self.document.parse_limits.common.max_source_bytes,
                    max_decoded_utf8_bytes=(
                        self.document.parse_limits.max_decoded_utf8_bytes
                    ),
                    max_decoded_scalars=(
                        self.document.parse_limits.max_decoded_scalars
                    ),
                ),
            )
        except Exception:
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.INVALID_LITERAL
            ) from None
        text = snapshot.decoded_text()
        if text is not None and ("\r" in text or "\n" in text):
            raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_LITERAL)

    # -- canonical bytes ----------------------------------------------------

    def preserve_direct_value(self, property, value: JavaString) -> bytes | None:
        """Direct style preservation when every precondition holds
        (edit.rs)."""
        logical = self.document.logical_line(property.logical_line)
        if len(logical.natural_lines) != 1:
            return None
        for escape_node in property.escapes:
            escape = self.document.escape(escape_node)
            if not escape.in_key:
                return None
        if not value.is_well_formed():
            return None
        text = value.to_unicode()
        if text[:1] in (" ", "\t", "") or "\\" in text or "\r" in text or "\n" in text:
            return None
        try:
            return _encode_selected(
                text,
                self.document.source.encoding_facts().selected,
                self.document.parse_limits.common.max_source_bytes,
            )
        except MaterializationFailure:
            return None

    def canonical_fragment(self, value: JavaString, is_key: bool) -> bytes:
        """Canonical escaped fragment under the selected source encoding
        (edit.rs)."""
        text = canonical_fragment(
            value,
            self.document.profile,
            is_key,
            self.document.parse_limits.common.max_source_bytes,
        )
        try:
            return _encode_selected(
                text,
                self.document.source.encoding_facts().selected,
                self.document.parse_limits.common.max_source_bytes,
            )
        except MaterializationFailure as failure:
            if failure.kind is MaterializationFailureKind.RESOURCE_LIMIT:
                raise PropertiesEditFailure(
                    PropertiesEditFailureKind.RESOURCE_LIMIT,
                    resource_name=failure.name,
                ) from None
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.ENCODING_UNREPRESENTABLE
            ) from None

    def canonical_record(
        self, position: int, key: JavaString, value: JavaString
    ) -> bytes:
        """One canonical ``key=value`` record with the newline convention
        (edit.rs)."""
        newline = self.newline_convention()
        text = ""
        if position > 0 and not self.is_line_boundary(position):
            text += newline
        text += canonical_fragment(
            key,
            self.document.profile,
            True,
            self.document.parse_limits.common.max_source_bytes,
        )
        text += "="
        text += canonical_fragment(
            value,
            self.document.profile,
            False,
            self.document.parse_limits.common.max_source_bytes,
        )
        text += newline
        try:
            return _encode_selected(
                text,
                self.document.source.encoding_facts().selected,
                self.document.parse_limits.common.max_source_bytes,
            )
        except MaterializationFailure as failure:
            if failure.kind is MaterializationFailureKind.RESOURCE_LIMIT:
                raise PropertiesEditFailure(
                    PropertiesEditFailureKind.RESOURCE_LIMIT,
                    resource_name=failure.name,
                ) from None
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.ENCODING_UNREPRESENTABLE
            ) from None

    def newline_convention(self) -> str:
        """The existing newline convention (edit.rs)."""
        text = self.document.source.decoded_text()
        assert text is not None, "Properties source is text"
        index = text.find("\r")
        if index >= 0:
            return "\r\n" if text[index + 1 :].startswith("\n") else "\r"
        if "\n" in text:
            return "\n"
        return "\n"

    def is_line_boundary(self, raw: int) -> bool:
        """Whether a raw position is preceded by a line terminator
        (edit.rs)."""
        text = self.document.source.decoded_text()
        assert text is not None, "Properties source is text"
        position = self.document.source.decoded_position(raw)
        return text[: position.decoded_utf8_byte].endswith(("\r", "\n"))

    def canonical_fallback_diagnostic(self, span: Span) -> PropertiesDiagnostic:
        """Authorized canonical representation fallback warning
        (edit.rs)."""
        return PropertiesDiagnostic(
            code="java-properties.edit.canonical-fallback@1",
            category=DiagnosticCategory.EDIT,
            severity=PropertiesSeverity.WARNING,
            primary=span,
        )

    def apply_prepared(self, prepared: list[_PreparedEdit]) -> bytes:
        """Renders the target bytes from ordered prepared edits
        (edit.rs)."""
        target_len = len(self.document.render())
        for edit in prepared:
            target_len = (
                target_len - edit.old_span.len() + len(edit.replacement)
            )
        if target_len > self.document.parse_limits.common.max_source_bytes:
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.RESOURCE_LIMIT,
                resource_name="target-bytes",
            )
        raw = self.document.render()
        output = bytearray()
        cursor = 0
        for edit in prepared:
            output += raw[cursor : edit.old_span.start_byte]
            output += edit.replacement
            cursor = edit.old_span.end_byte
        output += raw[cursor:]
        return bytes(output)


# -- module-level entry points ------------------------------------------------


def commit(document: PropertiesDocument, transaction: EditTransaction) -> EditCommit:
    """Atomically commits every declared Properties operation
    (edit.rs)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise PropertiesEditFailure(PropertiesEditFailureKind.RECOVERED_DOCUMENT)
    if transaction.base != document.snapshot_identity():
        raise PropertiesEditFailure(PropertiesEditFailureKind.WRONG_SNAPSHOT)
    if len(transaction.operations) > document.parse_limits.common.max_node_count:
        raise PropertiesEditFailure(
            PropertiesEditFailureKind.RESOURCE_LIMIT,
            resource_name="edit-operations",
        )
    planner = _EditPlanner(document)
    planner.validate_removed_anchors(transaction)

    targets = set()
    insert_boundaries = set()
    diagnostics: list[PropertiesDiagnostic] = []
    prepared: list[_PreparedEdit] = []
    expected = [
        _ExpectedProperty(
            old=property.node,
            key=property.key,
            value=property.value,
            literal=False,
            literal_old_span=None,
            removed=False,
        )
        for property in document.properties
    ]
    insertions: dict[int, _ExpectedProperty] = {}

    for operation in transaction.operations:
        target = operation.destructive_target
        if target is not None:
            if target in targets:
                raise PropertiesEditFailure(PropertiesEditFailureKind.DUPLICATE_TARGET)
            targets.add(target)
        kind = operation.kind
        if kind is EditOperationKind.REPLACE_SEMANTIC_VALUE:
            ordinal = planner.property_ordinal(operation.target)
            property = document.properties[ordinal]
            old_span = planner.value_ownership(property)
            replacement = planner.preserve_direct_value(property, operation.value)
            if replacement is None:
                diagnostics.append(planner.canonical_fallback_diagnostic(property.span))
                replacement = planner.canonical_fragment(operation.value, False)
            expected[ordinal] = _ExpectedProperty(
                old=expected[ordinal].old,
                key=expected[ordinal].key,
                value=operation.value,
                literal=False,
                literal_old_span=None,
                removed=False,
            )
            prepared.append(_PreparedEdit(old_span=old_span, replacement=replacement))
        elif kind is EditOperationKind.REPLACE_LITERAL_VALUE:
            ordinal = planner.property_ordinal(operation.target)
            planner.validate_literal(operation.literal)
            property = document.properties[ordinal]
            old_span = planner.value_ownership(property)
            expected[ordinal] = _ExpectedProperty(
                old=expected[ordinal].old,
                key=expected[ordinal].key,
                value=None,
                literal=True,
                literal_old_span=old_span,
                removed=False,
            )
            prepared.append(
                _PreparedEdit(old_span=old_span, replacement=bytes(operation.literal))
            )
        elif kind is EditOperationKind.INSERT_PROPERTY:
            planner.validate_document_target(operation.document)
            boundary, position = planner.insertion_location(operation.placement)
            if boundary in insert_boundaries:
                raise PropertiesEditFailure(
                    PropertiesEditFailureKind.OVERLAPPING_OWNERSHIP
                )
            insert_boundaries.add(boundary)
            insertions[boundary] = _ExpectedProperty(
                old=None,
                key=operation.key,
                value=operation.value,
                literal=False,
                literal_old_span=None,
                removed=False,
            )
            try:
                zero_span = document.authority.span(position, position)
            except Exception:
                raise PropertiesEditFailure(
                    PropertiesEditFailureKind.INVALID_PLACEMENT
                ) from None
            prepared.append(
                _PreparedEdit(
                    old_span=zero_span,
                    replacement=planner.canonical_record(
                        position, operation.key, operation.value
                    ),
                )
            )
        elif kind is EditOperationKind.REMOVE_PROPERTY:
            ordinal = planner.property_ordinal(operation.target)
            expected[ordinal] = _ExpectedProperty(
                old=expected[ordinal].old,
                key=expected[ordinal].key,
                value=None,
                literal=False,
                literal_old_span=None,
                removed=True,
            )
            prepared.append(
                _PreparedEdit(
                    old_span=planner.record_ownership(document.properties[ordinal]),
                    replacement=b"",
                )
            )
        elif kind is EditOperationKind.RENAME_PROPERTY:
            ordinal = planner.property_ordinal(operation.target)
            expected[ordinal] = _ExpectedProperty(
                old=expected[ordinal].old,
                key=operation.key,
                value=expected[ordinal].value,
                literal=False,
                literal_old_span=None,
                removed=False,
            )
            prepared.append(
                _PreparedEdit(
                    old_span=planner.key_ownership(document.properties[ordinal]),
                    replacement=planner.canonical_fragment(operation.key, True),
                )
            )
        else:
            raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_PLACEMENT)

    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    _validate_non_overlapping(prepared)
    final_expected = _assemble_expected(expected, insertions)
    closure_failure = PropertiesEditFailure(
        PropertiesEditFailureKind.INVALID_LITERAL
        if any(item.literal for item in final_expected)
        else PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
    )
    rendered = planner.apply_prepared(prepared)
    selection = _original_encoding_selection(document)
    try:
        new_document = parse(
            rendered, document.profile, selection, document.parse_limits
        )
    except Exception:
        raise closure_failure from None
    if new_document.formation_status() is not FormationStatus.COMPLETE:
        raise closure_failure
    _verify_expected(new_document, final_expected)

    source_edits = _build_source_edits(new_document, prepared)
    _verify_literal_ownership(new_document, final_expected, source_edits)
    mappings = _build_node_mappings(new_document, final_expected, transaction)
    change_set = ChangeSet(
        old_snapshot=document.snapshot_identity(),
        new_snapshot=new_document.snapshot_identity(),
        source_edits=tuple(source_edits),
        node_mappings=tuple(mappings),
        diagnostics=tuple(diagnostics),
    )
    patch_limits = _source_patch_limits(document.parse_limits, len(prepared))
    try:
        source_patch = SourcePatch.derive(
            document.source,
            new_document.source,
            change_set,
            _operation_metadata(transaction),
            patch_limits,
        )
        untouched_proof = UntouchedByteProof.create(
            document.source,
            new_document.source,
            list(source_patch.replacements),
        )
    except Exception:
        raise PropertiesEditFailure(
            PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        ) from None
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def dry_run(
    document: PropertiesDocument,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs)."""
    commit_result = commit(document, transaction)
    try:
        return EditPlan.new(
            source_id,
            document.profile_id(),
            _operation_summaries(transaction),
            commit_result.source_patch,
            list(commit_result.change_set.diagnostics),
        )
    except Exception:
        raise PropertiesEditFailure(
            PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        ) from None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fragment_ownership(
    document: PropertiesDocument, fragments: tuple[Span, ...], anchor: Span
) -> Span:
    """The ownership interval of one key/value fragment list
    (edit.rs)."""
    if not fragments:
        return anchor
    return document.authority.span(
        fragments[0].start_byte, fragments[-1].end_byte
    )


def _validate_non_overlapping(prepared: list[_PreparedEdit]) -> None:
    """Ownership conflict detection (edit.rs)."""
    for left, right in zip(prepared, prepared[1:]):
        if (
            left.old_span == right.old_span
            or left.old_span.end_byte > right.old_span.start_byte
            or (left.old_span.is_empty() and left.old_span.start_byte == right.old_span.start_byte)
            or (right.old_span.is_empty() and left.old_span.end_byte == right.old_span.start_byte)
        ):
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.OVERLAPPING_OWNERSHIP
            )


def _assemble_expected(
    old: list[_ExpectedProperty], insertions: dict[int, _ExpectedProperty]
) -> list[_ExpectedProperty]:
    """Ordered expected facts with insertions at their boundaries
    (edit.rs)."""
    output: list[_ExpectedProperty] = []
    for boundary in range(len(old) + 1):
        if boundary in insertions:
            output.append(insertions[boundary])
        if boundary < len(old) and not old[boundary].removed:
            output.append(old[boundary])
    return output


def _verify_expected(
    document: PropertiesDocument, expected: list[_ExpectedProperty]
) -> None:
    """Reparse closure verification (edit.rs)."""
    if len(document.properties) != len(expected):
        raise PropertiesEditFailure(
            PropertiesEditFailureKind.INVALID_LITERAL
            if any(item.literal for item in expected)
            else PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        )
    for actual, item in zip(document.properties, expected):
        if actual.key != item.key or (
            item.value is not None and actual.value != item.value
        ):
            raise PropertiesEditFailure(
                PropertiesEditFailureKind.INVALID_LITERAL
                if item.literal
                else PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
            )


def _build_source_edits(
    new_document: PropertiesDocument, prepared: list[_PreparedEdit]
) -> list[SourceEdit]:
    """Ordered old-to-new source edits (edit.rs)."""
    delta = 0
    source_edits: list[SourceEdit] = []
    for edit in prepared:
        new_start = edit.old_span.start_byte + delta
        new_end = new_start + len(edit.replacement)
        source_edits.append(
            SourceEdit(
                old_span=edit.old_span,
                new_span=new_document.authority.span(new_start, new_end),
                replacement=edit.replacement,
            )
        )
        delta += len(edit.replacement) - edit.old_span.len()
    return source_edits


def _verify_literal_ownership(
    document: PropertiesDocument,
    expected: list[_ExpectedProperty],
    source_edits: list[SourceEdit],
) -> None:
    """Literal replacements must own exactly one raw value interval
    (edit.rs)."""
    for ordinal, item in enumerate(expected):
        if not item.literal:
            continue
        if item.literal_old_span is None:
            raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_LITERAL)
        source_edit = next(
            (edit for edit in source_edits if edit.old_span == item.literal_old_span),
            None,
        )
        if source_edit is None:
            raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_LITERAL)
        actual = document.properties[ordinal]
        ownership = _fragment_ownership(
            document, actual.value_fragments, actual.value_anchor
        )
        if source_edit.new_span != ownership:
            raise PropertiesEditFailure(PropertiesEditFailureKind.INVALID_LITERAL)


def _build_node_mappings(
    document: PropertiesDocument,
    expected: list[_ExpectedProperty],
    transaction: EditTransaction,
) -> list[NodeMapping]:
    """Explicit old-to-new node mapping facts (edit.rs)."""
    mappings: list[NodeMapping] = []
    for operation in transaction.operations:
        if operation.kind is EditOperationKind.REMOVE_PROPERTY:
            mappings.append(
                NodeMapping(
                    old=operation.target,
                    new=None,
                    status=NodeMappingStatus.DELETED,
                )
            )
        elif operation.kind in (
            EditOperationKind.REPLACE_SEMANTIC_VALUE,
            EditOperationKind.REPLACE_LITERAL_VALUE,
            EditOperationKind.RENAME_PROPERTY,
        ):
            ordinal = next(
                (
                    index
                    for index, item in enumerate(expected)
                    if item.old == operation.target
                ),
                None,
            )
            if ordinal is not None:
                mappings.append(
                    NodeMapping(
                        old=operation.target,
                        new=document.properties[ordinal].node,
                        status=NodeMappingStatus.REPLACED,
                    )
                )
    return mappings


def _original_encoding_selection(
    document: PropertiesDocument,
) -> PropertiesEncodingSelection:
    """The base document's exact source contract (edit.rs)."""
    if document.profile is PropertiesProfile.READER_V1:
        return PropertiesEncodingSelection.reader(
            document.source.encoding_facts().selected
        )
    return PropertiesEncodingSelection.latin1()


def _source_patch_limits(
    limits: PropertiesParseLimits, operation_count: int
) -> SourcePatchLimits:
    """Patch limits derived from the parse limits (edit.rs)."""
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=limits.common.max_source_bytes,
            max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
            max_decoded_scalars=limits.max_decoded_scalars,
        ),
        max_replacements=operation_count,
        max_patch_bytes=limits.common.max_source_bytes * 2,
    )


def _operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Deterministic operation metadata (edit.rs)."""
    return {
        f"operation.{index}": f"{operation.operation_id}@1"
        for index, operation in enumerate(transaction.operations)
    }


def _operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe content-free operation summaries (edit.rs)."""
    summaries = []
    for operation in transaction.operations:
        arguments: dict[str, str] = {}
        kind = operation.kind
        if kind is EditOperationKind.REPLACE_SEMANTIC_VALUE:
            arguments["value_code_units"] = str(len(operation.value.code_units()))
        elif kind is EditOperationKind.REPLACE_LITERAL_VALUE:
            arguments["literal_bytes"] = str(len(operation.literal))
        elif kind is EditOperationKind.INSERT_PROPERTY:
            arguments["key_code_units"] = str(len(operation.key.code_units()))
            arguments["value_code_units"] = str(len(operation.value.code_units()))
            arguments["placement"] = _placement_name(operation.placement)
        elif kind is EditOperationKind.RENAME_PROPERTY:
            arguments["key_code_units"] = str(len(operation.key.code_units()))
        summaries.append(
            EditOperationSummary.new(
                FormatOperationId.new(operation.operation_id, 1),
                arguments,
            )
        )
    return summaries


def _placement_name(placement: AssociationPlacement | None) -> str:
    if placement is None:
        return "start"
    return {
        "Start": "start",
        "End": "end",
        "Before": "before",
        "After": "after",
    }[placement.kind]


_OPERATION_ID_BY_KIND = {
    EditOperationKind.REPLACE_SEMANTIC_VALUE: "java-properties.edit.replace-semantic-value",
    EditOperationKind.REPLACE_LITERAL_VALUE: "java-properties.edit.replace-literal-value",
    EditOperationKind.INSERT_PROPERTY: "java-properties.edit.insert-property",
    EditOperationKind.REMOVE_PROPERTY: "java-properties.edit.remove-property",
    EditOperationKind.RENAME_PROPERTY: "java-properties.edit.rename-property",
}


def _encode_selected(text: str, encoding, max_bytes: int) -> bytes:
    """Encodes canonical text under the selected source encoding
    (materialization.rs)."""
    from consema.properties.materialization import _encode_fragment

    return _encode_fragment(text, encoding, max_bytes)
