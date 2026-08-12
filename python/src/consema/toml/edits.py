"""Atomic scalar and structural edit transactions for TOML documents.

Authority:

- RFC 0004 §12/§13 (docs/rfcs/0004-materialization-conversion-and-
  structural-edit-v1.md:291-336): the frozen TOML structural surface
  (insert-entry into root/standard/inline tables with one direct key
  segment and a supported placement; remove-entry and rename-entry on one
  exact TomlEntry identity; insert-array-element and remove-array-element
  on one exact identity); one immutable transaction binds one base
  snapshot; every operation is fully validated before any output is
  published; the conflict algebra (WrongSnapshot, WrongRole,
  TargetNotFound, DuplicateTarget, OverlappingOwnership,
  AncestorDescendantConflict, PlacementAnchorRemoved, DuplicateKey,
  UnsupportedOperation, UnrepresentableValue, ResourceLimit,
  NewDocumentFormationFailed).
- The transaction algebra transcribes crates/consema-toml/src/edit.rs:
  RepresentationPolicy 16-26; ScalarReplacement/EditOperation 28-99;
  the atomic commit pipeline 281-430; dry-run 432-447; every prepare_*
  function 449-1062 (including delimiter-adjacent comma ownership,
  table-line insertion with newline inference from the first Newline
  piece, and key rename replacing only the key literal); the conflict
  preflight 1064-1100; canonical literal writers 1472-1636; exact-literal
  validation 1379-1413 (the candidate must parse as exactly one scalar
  value whose span is the whole candidate); operation metadata and
  summaries 1132-1240; failure codes edit.rs:1280-1332.
- The representation policies freeze the four values of RFC 0001 §6
  (docs/rfcs/0001-toml-1.0-profile.md:111-117); the fallback diagnostic
  is ``toml.edit.representation-fallback@1`` (error_registry.rs:339).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import Decimal, PortableValue
from consema.document.change_set import (
    ChangeSet,
    NodeMapping,
    NodeMappingStatus,
    SourceEdit,
)
from consema.document.edit_plan import EditOperationSummary, EditPlan, EditPlanSourceId
from consema.document.ids import FormatOperationId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationLimits,
)
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    AssociationPlacement,
    NodeRef,
    NodeRole,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof
from consema.protocol.error_registry import DiagnosticCategory
from consema.protocol.diagnostic import Severity

from consema.toml.document import Document, TomlItemKind, _ItemEntity
from consema.toml.errors import TomlDiagnostic, TomlEditFailure, TomlEditFailureKind
from consema.toml.materialization import canonical_fragment
from consema.toml.parser import parse


class RepresentationPolicy(enum.Enum):
    """Explicit semantic scalar representation policy (edit.rs:16-26;
    RFC 0001 §6)."""

    EXACT_LITERAL = "exact-literal"
    PRESERVE_COMPATIBLE = "preserve-compatible"
    CANONICAL_FOR_PROFILE = "canonical-for-profile"
    PRESERVE_ELSE_CANONICAL = "preserve-else-canonical"

    @classmethod
    def from_name(cls, name: str) -> RepresentationPolicy | None:
        try:
            return cls(name)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class ScalarReplacement:
    """One scalar operation bound to a transaction base snapshot
    (edit.rs:29-55)."""

    target: NodeRef
    value: PortableValue | None = None
    policy: RepresentationPolicy | None = None
    literal: bytes | None = None

    @classmethod
    def of_semantic(
        cls, target: NodeRef, value: PortableValue, policy: RepresentationPolicy
    ) -> ScalarReplacement:
        return cls(target=target, value=value, policy=policy)

    @classmethod
    def of_literal(cls, target: NodeRef, literal: bytes) -> ScalarReplacement:
        return cls(target=target, literal=literal)


class EditOperationKind(enum.Enum):
    REPLACE_SCALAR = "ReplaceScalar"
    INSERT_ENTRY = "InsertEntry"
    REMOVE_ENTRY = "RemoveEntry"
    RENAME_ENTRY = "RenameEntry"
    INSERT_ARRAY_ELEMENT = "InsertArrayElement"
    REMOVE_ARRAY_ELEMENT = "RemoveArrayElement"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One typed TOML edit operation bound to an immutable base snapshot
    (edit.rs:57-99)."""

    kind: EditOperationKind
    replacement: ScalarReplacement | None = None
    table: NodeRef | None = None
    key: str | None = None
    value: PortableValue | None = None
    placement: AssociationPlacement | None = None
    target: NodeRef | None = None
    array: NodeRef | None = None

    @classmethod
    def replace_scalar(cls, replacement: ScalarReplacement) -> EditOperation:
        return cls(kind=EditOperationKind.REPLACE_SCALAR, replacement=replacement)

    @classmethod
    def insert_entry(
        cls,
        table: NodeRef,
        key: str,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditOperation:
        return cls(
            kind=EditOperationKind.INSERT_ENTRY,
            table=table,
            key=key,
            value=value,
            placement=placement,
        )

    @classmethod
    def remove_entry(cls, target: NodeRef) -> EditOperation:
        return cls(kind=EditOperationKind.REMOVE_ENTRY, target=target)

    @classmethod
    def rename_entry(cls, target: NodeRef, key: str) -> EditOperation:
        return cls(kind=EditOperationKind.RENAME_ENTRY, target=target, key=key)

    @classmethod
    def insert_array_element(
        cls,
        array: NodeRef,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditOperation:
        return cls(
            kind=EditOperationKind.INSERT_ARRAY_ELEMENT,
            array=array,
            value=value,
            placement=placement,
        )

    @classmethod
    def remove_array_element(cls, target: NodeRef) -> EditOperation:
        return cls(kind=EditOperationKind.REMOVE_ARRAY_ELEMENT, target=target)


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable transaction; every operation resolves against one base
    snapshot (edit.rs:101-120)."""

    base: object  # SnapshotIdentity
    operations: tuple[EditOperation, ...] = field(default_factory=tuple)

    def base_snapshot(self):
        return self.base

    def operations(self) -> tuple[EditOperation, ...]:
        return self.operations


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs:122-227)."""

    def __init__(self, document: Document) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def semantic_scalar(
        self, target: NodeRef, value: PortableValue, policy: RepresentationPolicy
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation.replace_scalar(ScalarReplacement.of_semantic(target, value, policy))
        )
        return self

    def literal_scalar(self, target: NodeRef, literal: bytes) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation.replace_scalar(ScalarReplacement.of_literal(target, literal))
        )
        return self

    def insert_entry(
        self,
        table: NodeRef,
        key: str,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(EditOperation.insert_entry(table, key, value, placement))
        return self

    def remove_entry(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(EditOperation.remove_entry(target))
        return self

    def rename_entry(self, target: NodeRef, key: str) -> EditTransactionBuilder:
        self._operations.append(EditOperation.rename_entry(target, key))
        return self

    def insert_array_element(
        self, array: NodeRef, value: PortableValue, placement: AssociationPlacement
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation.insert_array_element(array, value, placement)
        )
        return self

    def remove_array_element(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(EditOperation.remove_array_element(target))
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """Atomic edit success (edit.rs:229-240)."""

    document: Document
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


class _MappingPlan(enum.Enum):
    REPLACED_LITERAL = "ReplacedLiteral"
    DELETED = "Deleted"
    UNMAPPED = "Unmapped"


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    old_span: Span
    replacement: bytes
    mapping: tuple[NodeRef, _MappingPlan, str | None] | None = None


class _DelimitedSyntax:
    __slots__ = ("anchor_role", "open", "close")

    def __init__(self, anchor_role: NodeRole, open_piece: str, close_piece: str) -> None:
        self.anchor_role = anchor_role
        self.open = open_piece
        self.close = close_piece


def commit_document(document: Document, transaction: EditTransaction) -> EditCommit:
    """Atomically commits scalar and structural operations; a failure
    never changes this snapshot (edit.rs:281-430)."""
    if transaction.base != document.snapshot_identity():
        raise TomlEditFailure(TomlEditFailureKind.WRONG_SNAPSHOT)
    _validate_dependencies(transaction)
    diagnostics: list[TomlDiagnostic] = []
    prepared: list[_PreparedEdit] = []
    for operation in transaction.operations:
        prepared.extend(_prepare_operation(document, operation, diagnostics))

    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    for first, second in zip(prepared, prepared[1:]):
        first_empty = first.old_span.is_empty()
        second_empty = second.old_span.is_empty()
        if (
            not first_empty
            and not second_empty
            and (
                first.old_span.end_byte > second.old_span.start_byte
                or first.old_span == second.old_span
            )
        ):
            raise TomlEditFailure(TomlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
        if first.old_span == second.old_span or (
            first_empty
            and second_empty
            and first.old_span.start_byte == second.old_span.start_byte
        ):
            raise TomlEditFailure(TomlEditFailureKind.OVERLAPPING_OWNERSHIP)

    raw = document.render()
    target_len = len(raw)
    for edit in prepared:
        target_len = target_len - edit.old_span.len() + len(edit.replacement)
        if target_len > document.parse_limits().max_source_bytes:
            raise TomlEditFailure(
                TomlEditFailureKind.RESOURCE_LIMIT, limit_name="target-bytes"
            )
    rendered = bytearray()
    cursor = 0
    for edit in prepared:
        rendered.extend(raw[cursor : edit.old_span.start_byte])
        rendered.extend(edit.replacement)
        cursor = edit.old_span.end_byte
    rendered.extend(raw[cursor:])
    try:
        new_document = parse(bytes(rendered), document._profile, document.parse_limits())
    except Exception:
        raise TomlEditFailure(TomlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None

    delta = 0
    source_edits: list[SourceEdit] = []
    mappings: list[NodeMapping] = []
    mapped_old: set[NodeRef] = set()
    new_raw = new_document.render()
    for edit in prepared:
        new_start = edit.old_span.start_byte + delta
        new_end = new_start + len(edit.replacement)
        source_edits.append(
            SourceEdit(
                old_span=edit.old_span,
                new_span=new_document.span(new_start, new_end),
                replacement=edit.replacement,
            )
        )
        if edit.mapping is not None:
            old, plan, reason = edit.mapping
            if old not in mapped_old:
                mapped_old.add(old)
                if plan is _MappingPlan.REPLACED_LITERAL:
                    new_item = _find_item_by_span(new_document, new_start, new_end)
                    mappings.append(
                        NodeMapping(
                            old=old,
                            new=new_item,
                            status=(
                                NodeMappingStatus.REPLACED
                                if new_item is not None
                                else NodeMappingStatus.UNMAPPED
                            ),
                            reason=(
                                None
                                if new_item is not None
                                else "reparsed-item-not-uniquely-located"
                            ),
                        )
                    )
                elif plan is _MappingPlan.DELETED:
                    mappings.append(
                        NodeMapping(old=old, new=None, status=NodeMappingStatus.DELETED)
                    )
                else:
                    mappings.append(
                        NodeMapping(
                            old=old,
                            new=None,
                            status=NodeMappingStatus.UNMAPPED,
                            reason=reason,
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
    patch_limits = _source_patch_limits(document.parse_limits(), len(source_edits))
    try:
        source_patch = SourcePatch.derive(
            document.source(),
            new_document.source(),
            change_set,
            _operation_metadata(transaction),
            patch_limits,
        )
        untouched_proof = UntouchedByteProof.create(
            document.source(),
            new_document.source(),
            list(source_patch.replacements),
        )
    except Exception:
        raise TomlEditFailure(TomlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def dry_run_document(
    document: Document, transaction: EditTransaction, source_id: EditPlanSourceId
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:432-447)."""
    commit = commit_document(document, transaction)
    try:
        return EditPlan.new(
            source_id,
            document.profile(),
            _operation_summaries(transaction),
            commit.source_patch,
            list(commit.change_set.diagnostics),
        )
    except Exception:
        raise TomlEditFailure(TomlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


def _validate_dependencies(transaction: EditTransaction) -> None:
    """edit.rs:1064-1100."""
    destructive: set[NodeRef] = set()
    removed: set[NodeRef] = set()
    anchors: list[NodeRef] = []
    for operation in transaction.operations:
        target = None
        if operation.kind is EditOperationKind.REPLACE_SCALAR:
            target = operation.replacement.target
        elif operation.kind in (
            EditOperationKind.REMOVE_ENTRY,
            EditOperationKind.RENAME_ENTRY,
            EditOperationKind.REMOVE_ARRAY_ELEMENT,
        ):
            target = operation.target
        if target is not None:
            if target in destructive:
                raise TomlEditFailure(TomlEditFailureKind.DUPLICATE_TARGET)
            destructive.add(target)
        if operation.kind in (
            EditOperationKind.REMOVE_ENTRY,
            EditOperationKind.REMOVE_ARRAY_ELEMENT,
        ):
            removed.add(operation.target)
        if operation.kind in (
            EditOperationKind.INSERT_ENTRY,
            EditOperationKind.INSERT_ARRAY_ELEMENT,
        ):
            placement = operation.placement
            if placement.kind in ("Before", "After"):
                anchors.append(placement.anchor)
    if any(anchor in removed for anchor in anchors):
        raise TomlEditFailure(TomlEditFailureKind.PLACEMENT_ANCHOR_REMOVED)


def _prepare_operation(
    document: Document, operation: EditOperation, diagnostics: list[TomlDiagnostic]
) -> list[_PreparedEdit]:
    if operation.kind is EditOperationKind.REPLACE_SCALAR:
        return [_prepare_scalar(document, operation.replacement, diagnostics)]
    if operation.kind is EditOperationKind.INSERT_ENTRY:
        return _prepare_insert_entry(document, operation)
    if operation.kind is EditOperationKind.REMOVE_ENTRY:
        return _prepare_remove_entry(document, operation.target)
    if operation.kind is EditOperationKind.RENAME_ENTRY:
        return [_prepare_rename_entry(document, operation)]
    if operation.kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
        return _prepare_insert_array_element(document, operation)
    return _prepare_remove_array_element(document, operation.target)


def _prepare_scalar(
    document: Document, operation: ScalarReplacement, diagnostics: list[TomlDiagnostic]
) -> _PreparedEdit:
    target = operation.target
    index = _resolve_target(document, target, NodeRole.TOML_ITEM)
    old_kind = document._item_entity(index).kind.public_kind()
    if not _is_scalar_kind(old_kind):
        raise TomlEditFailure(TomlEditFailureKind.WRONG_ROLE)
    if operation.literal is not None:
        _validate_exact_scalar(operation.literal)
        replacement = operation.literal
    else:
        assert operation.value is not None and operation.policy is not None
        replacement = _semantic_literal(
            operation.value,
            old_kind,
            operation.policy,
            document._entity(index).span,
            diagnostics,
        )
    return _PreparedEdit(
        old_span=document._entity(index).span,
        replacement=replacement,
        mapping=(target, _MappingPlan.REPLACED_LITERAL, None),
    )


def _prepare_insert_entry(
    document: Document, operation: EditOperation
) -> list[_PreparedEdit]:
    table_index = _resolve_target(document, operation.table, NodeRole.TOML_ITEM)
    item = document._item_entity(table_index).kind
    if item.name not in ("Table", "InlineTable"):
        raise TomlEditFailure(TomlEditFailureKind.WRONG_ROLE)
    kind = item.public_kind()
    if kind not in (
        TomlItemKind.ROOT_TABLE,
        TomlItemKind.STANDARD_TABLE,
        TomlItemKind.INLINE_TABLE,
    ):
        raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)
    entries = item.children
    if any(_entry_name(document, entry_index) == operation.key for entry_index in entries):
        raise TomlEditFailure(TomlEditFailureKind.DUPLICATE_KEY)
    fragment = _canonical_string(operation.key).encode("utf-8") + b" = " + _fragment(
        document, operation.value
    )
    if kind is TomlItemKind.INLINE_TABLE:
        prepared = _prepare_delimited_insertion(
            document,
            operation.table,
            document._entity(table_index).span,
            entries,
            _DelimitedSyntax(NodeRole.TOML_ENTRY, "LeftBrace", "RightBrace"),
            operation.placement,
            fragment,
        )
    else:
        prepared = _prepare_table_line_insertion(
            document,
            operation.table,
            table_index,
            entries,
            operation.placement,
            fragment,
        )
    return [prepared]


def _prepare_insert_array_element(
    document: Document, operation: EditOperation
) -> list[_PreparedEdit]:
    index = _resolve_target(document, operation.array, NodeRole.TOML_ITEM)
    item = document._item_entity(index).kind
    if item.name != "Array":
        raise TomlEditFailure(TomlEditFailureKind.WRONG_ROLE)
    return [
        _prepare_delimited_insertion(
            document,
            operation.array,
            document._entity(index).span,
            item.children,
            _DelimitedSyntax(NodeRole.TOML_ARRAY_ELEMENT, "LeftBracket", "RightBracket"),
            operation.placement,
            _fragment(document, operation.value),
        )
    ]


def _prepare_delimited_insertion(
    document: Document,
    container: NodeRef,
    container_span: Span,
    associations: tuple[int, ...],
    syntax: _DelimitedSyntax,
    placement: AssociationPlacement,
    fragment: bytes,
) -> _PreparedEdit:
    if not associations:
        if placement.kind == "Start":
            position = _delimiter(document, syntax.open, container_span, False)
            prefix_comma = False
            suffix_comma = False
        elif placement.kind == "End":
            position = _delimiter(document, syntax.close, container_span, True)
            prefix_comma = False
            suffix_comma = False
        else:
            raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    else:
        if placement.kind == "Start":
            position = document._entity(associations[0]).span.start_byte
            prefix_comma = False
            suffix_comma = True
        elif placement.kind == "End":
            position = document._entity(associations[-1]).span.end_byte
            prefix_comma = True
            suffix_comma = False
        elif placement.kind == "Before":
            anchor = _resolve_anchor(document, placement.anchor, syntax.anchor_role, associations)
            position = document._entity(anchor).span.start_byte
            prefix_comma = False
            suffix_comma = True
        else:  # After
            anchor = _resolve_anchor(document, placement.anchor, syntax.anchor_role, associations)
            position = document._entity(anchor).span.end_byte
            prefix_comma = True
            suffix_comma = False
    replacement = bytearray()
    if prefix_comma:
        replacement.append(ord(","))
    replacement.extend(fragment)
    if suffix_comma:
        replacement.append(ord(","))
    return _PreparedEdit(
        old_span=document.span(position, position),
        replacement=bytes(replacement),
        mapping=(container, _MappingPlan.UNMAPPED, "container-reparsed-after-structural-insertion"),
    )


def _prepare_table_line_insertion(
    document: Document,
    table: NodeRef,
    table_index: int,
    entries: tuple[int, ...],
    placement: AssociationPlacement,
    fragment: bytes,
) -> _PreparedEdit:
    kind = document._item_entity(table_index).kind.public_kind()
    if placement.kind == "Start":
        if kind is TomlItemKind.ROOT_TABLE:
            position = 0
        else:
            position = _line_after(document, document._entity(table_index).span.start_byte)
    elif placement.kind == "End":
        position = _table_end_insertion(document, entries, table_index)
    elif placement.kind == "Before":
        anchor = _resolve_anchor(document, placement.anchor, NodeRole.TOML_ENTRY, entries)
        position = _line_start(document, document._entity(anchor).span.start_byte)
    else:  # After
        anchor = _resolve_anchor(document, placement.anchor, NodeRole.TOML_ENTRY, entries)
        if _is_table_kind(_entry_item_kind(document, anchor)):
            raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)
        position = _line_after(document, document._entity(anchor).span.end_byte)
    return _PreparedEdit(
        old_span=document.span(position, position),
        replacement=_line_fragment(document, position, fragment),
        mapping=(table, _MappingPlan.UNMAPPED, "table-reparsed-after-entry-insertion"),
    )


def _prepare_remove_entry(document: Document, target: NodeRef) -> list[_PreparedEdit]:
    index = _resolve_target(document, target, NodeRole.TOML_ENTRY)
    if _is_table_kind(_entry_item_kind(document, index)):
        raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)
    parent = _parent_table(document, index)
    if parent is None:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    container, entries, ordinal = parent
    kind = document._item_entity(container).kind.public_kind()
    if kind is TomlItemKind.INLINE_TABLE:
        return _prepare_delimited_removal(
            document,
            target,
            index,
            entries,
            ordinal,
            document._entity(container).span.end_byte,
        )
    if kind in (TomlItemKind.ROOT_TABLE, TomlItemKind.STANDARD_TABLE):
        return [
            _PreparedEdit(
                old_span=document._entity(index).span,
                replacement=b"",
                mapping=(target, _MappingPlan.DELETED, None),
            )
        ]
    raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)


def _prepare_remove_array_element(
    document: Document, target: NodeRef
) -> list[_PreparedEdit]:
    index = _resolve_target(document, target, NodeRole.TOML_ARRAY_ELEMENT)
    parent = _parent_array(document, index)
    if parent is None:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    container, elements, ordinal = parent
    return _prepare_delimited_removal(
        document,
        target,
        index,
        elements,
        ordinal,
        document._entity(container).span.end_byte,
    )


def _prepare_delimited_removal(
    document: Document,
    target: NodeRef,
    index: int,
    associations: tuple[int, ...],
    ordinal: int,
    container_end: int,
) -> list[_PreparedEdit]:
    target_span = document._entity(index).span
    edits: list[_PreparedEdit] = []
    comma = _removal_comma(document, associations, ordinal, container_end)
    if comma is not None:
        if comma.end_byte == target_span.start_byte or comma.start_byte == target_span.end_byte:
            edits.append(
                _PreparedEdit(
                    old_span=document.span(
                        min(comma.start_byte, target_span.start_byte),
                        max(comma.end_byte, target_span.end_byte),
                    ),
                    replacement=b"",
                    mapping=(target, _MappingPlan.DELETED, None),
                )
            )
            return edits
        edits.append(
            _PreparedEdit(
                old_span=target_span,
                replacement=b"",
                mapping=(target, _MappingPlan.DELETED, None),
            )
        )
        edits.append(_PreparedEdit(old_span=comma, replacement=b"", mapping=None))
    else:
        edits.append(
            _PreparedEdit(
                old_span=target_span,
                replacement=b"",
                mapping=(target, _MappingPlan.DELETED, None),
            )
        )
    return edits


def _prepare_rename_entry(document: Document, operation: EditOperation) -> _PreparedEdit:
    index = _resolve_target(document, operation.target, NodeRole.TOML_ENTRY)
    if _is_table_kind(_entry_item_kind(document, index)):
        raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)
    parent = _parent_table(document, index)
    if parent is None:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    container, entries, _ = parent
    if document._item_entity(container).kind.public_kind() not in (
        TomlItemKind.ROOT_TABLE,
        TomlItemKind.STANDARD_TABLE,
        TomlItemKind.INLINE_TABLE,
    ):
        raise TomlEditFailure(TomlEditFailureKind.UNSUPPORTED_OPERATION)
    if any(
        candidate != index and _entry_name(document, candidate) == operation.key
        for candidate in entries
    ):
        raise TomlEditFailure(TomlEditFailureKind.DUPLICATE_KEY)
    entry = document._entity(index).kind
    return _PreparedEdit(
        old_span=document._entity(entry.key).span,
        replacement=_canonical_string(operation.key).encode("utf-8"),
        mapping=(operation.target, _MappingPlan.UNMAPPED, "entry-reparsed-after-key-rename"),
    )


def _resolve_target(document: Document, target: NodeRef, role: NodeRole) -> int:
    if target.snapshot != document.snapshot_identity():
        raise TomlEditFailure(TomlEditFailureKind.WRONG_SNAPSHOT)
    if target.role is not role:
        raise TomlEditFailure(TomlEditFailureKind.WRONG_ROLE)
    if target.index >= len(document._entities):
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    return target.index


def _resolve_anchor(
    document: Document, anchor: NodeRef, role: NodeRole, associations: tuple[int, ...]
) -> int:
    index = _resolve_target(document, anchor, role)
    if index not in associations:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    return index


def _entry_name(document: Document, entry_index: int) -> str:
    entry = document._entity(entry_index).kind
    key = document._entity(entry.key).kind
    return key.name


def _entry_item_kind(document: Document, entry_index: int) -> TomlItemKind:
    entry = document._entity(entry_index).kind
    return document._item_entity(entry.item).kind.public_kind()


def _parent_table(
    document: Document, entry: int
) -> tuple[int, tuple[int, ...], int] | None:
    for index, entity in enumerate(document._entities):
        item = entity.kind
        if not isinstance(item, _ItemEntity):
            continue
        if item.kind.name in ("Table", "InlineTable"):
            children = item.kind.children
            position = children.index(entry) if entry in children else -1
            if position >= 0:
                return index, children, position
    return None


def _parent_array(
    document: Document, element: int
) -> tuple[int, tuple[int, ...], int] | None:
    for index, entity in enumerate(document._entities):
        item = entity.kind
        if not isinstance(item, _ItemEntity):
            continue
        if item.kind.name == "Array":
            children = item.kind.children
            position = children.index(element) if element in children else -1
            if position >= 0:
                return index, children, position
    return None


def _table_end_insertion(
    document: Document, entries: tuple[int, ...], table_index: int
) -> int:
    for entry in entries:
        if _is_table_kind(_entry_item_kind(document, entry)):
            return _line_start(document, document._entity(entry).span.start_byte)
    if entries:
        return _line_after(document, document._entity(entries[-1]).span.end_byte)
    if document._item_entity(table_index).kind.public_kind() is TomlItemKind.STANDARD_TABLE:
        return _line_after(document, document._entity(table_index).span.start_byte)
    return document._entity(table_index).span.end_byte


def _line_start(document: Document, position: int) -> int:
    raw = document.render()
    index = raw.rfind(b"\n", 0, position)
    return index + 1


def _line_after(document: Document, position: int) -> int:
    raw = document.render()
    index = raw.find(b"\n", position)
    if index < 0:
        return len(raw)
    return index + 1


def _line_fragment(document: Document, position: int, fragment: bytes) -> bytes:
    raw = document.render()
    newline = _newline_bytes(document)
    needs_prefix = position > 0 and raw[position - 1] != ord("\n")
    needs_suffix = position < len(raw)
    replacement = bytearray()
    if needs_prefix:
        replacement.extend(newline)
    replacement.extend(fragment)
    if needs_suffix:
        replacement.extend(newline)
    return bytes(replacement)


def _newline_bytes(document: Document) -> bytes:
    """The first Newline piece's exact bytes, or LF when the source has no
    newline (edit.rs:985-994)."""
    raw = document.render()
    for piece, kind in zip(
        document.lossless_structural_index().pieces, document.lossless_syntax_kinds()
    ):
        if kind.value == "Newline":
            return raw[piece.span.start_byte : piece.span.end_byte]
    return b"\n"


def _removal_comma(
    document: Document,
    associations: tuple[int, ...],
    ordinal: int,
    container_end: int,
) -> Span | None:
    """edit.rs:996-1026: prefer the comma after the removed association,
    else the comma before it (only when not first)."""
    current = document._entity(associations[ordinal]).span
    following_end = (
        document._entity(associations[ordinal + 1]).span.start_byte
        if ordinal + 1 < len(associations)
        else container_end
    )
    comma = _syntax_between(document, "Comma", current.end_byte, following_end, False)
    if comma is not None:
        return comma
    if ordinal == 0:
        return None
    previous = document._entity(associations[ordinal - 1]).span
    comma = _syntax_between(document, "Comma", previous.end_byte, current.start_byte, True)
    if comma is None:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    return comma


def _delimiter(
    document: Document, kind: str, container: Span, last: bool
) -> int:
    span = _syntax_between(document, kind, container.start_byte, container.end_byte, last)
    if span is None:
        raise TomlEditFailure(TomlEditFailureKind.TARGET_NOT_FOUND)
    return span.end_byte if not last else span.start_byte


def _syntax_between(
    document: Document, kind: str, start: int, end: int, last: bool
) -> Span | None:
    matches = [
        piece.span
        for piece, syntax_kind in zip(
            document.lossless_structural_index().pieces, document.lossless_syntax_kinds()
        )
        if syntax_kind.value == kind
        and piece.span.start_byte >= start
        and piece.span.end_byte <= end
    ]
    if not matches:
        return None
    return matches[-1] if last else matches[0]


def _fragment(document: Document, value: PortableValue) -> bytes:
    """Canonical TOML value fragment (edit.rs:858-878)."""
    limits = MaterializationLimits(
        max_input_nodes=document.parse_limits().max_node_count,
        max_output_bytes=document.parse_limits().max_source_bytes,
        max_depth=document.parse_limits().max_nesting_depth,
        max_report_entries=document.parse_limits().max_diagnostics,
        max_provenance_entries=document.parse_limits().max_node_count * 4,
    )
    try:
        return canonical_fragment(value, limits)
    except MaterializationFailure as failure:
        if failure.kind is MaterializationFailureKind.UNREPRESENTABLE:
            raise TomlEditFailure(
                TomlEditFailureKind.UNREPRESENTABLE_VALUE, value_kind="portable-value"
            ) from None
        if failure.kind is MaterializationFailureKind.RESOURCE_LIMIT:
            raise TomlEditFailure(
                TomlEditFailureKind.RESOURCE_LIMIT, limit_name=failure.name
            ) from None
        raise TomlEditFailure(
            TomlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED
        ) from None


def _source_patch_limits(parse_limits: ParseLimits, operation_count: int) -> SourcePatchLimits:
    """edit.rs:1117-1130."""
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=parse_limits.max_source_bytes,
            max_decoded_utf8_bytes=parse_limits.max_source_bytes,
            max_decoded_scalars=parse_limits.max_source_bytes,
        ),
        max_replacements=operation_count,
        max_patch_bytes=parse_limits.max_source_bytes * 2,
    )


def _operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """edit.rs:1132-1154."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


def _operation_id(operation: EditOperation) -> str:
    if operation.kind is EditOperationKind.REPLACE_SCALAR:
        if operation.replacement.literal is not None:
            return "toml.edit.replace-scalar-literal@1"
        return "toml.edit.replace-scalar-semantic@1"
    if operation.kind is EditOperationKind.INSERT_ENTRY:
        return "toml.edit.insert-entry@1"
    if operation.kind is EditOperationKind.REMOVE_ENTRY:
        return "toml.edit.remove-entry@1"
    if operation.kind is EditOperationKind.RENAME_ENTRY:
        return "toml.edit.rename-entry@1"
    if operation.kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
        return "toml.edit.insert-array-element@1"
    return "toml.edit.remove-array-element@1"


def _operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """edit.rs:1156-1240."""
    summaries: list[EditOperationSummary] = []
    for operation in transaction.operations:
        arguments: dict[str, str] = {}
        if operation.kind is EditOperationKind.REPLACE_SCALAR:
            replacement = operation.replacement
            if replacement.literal is not None:
                op_id = "toml.edit.replace-scalar-literal"
                arguments["literal_bytes"] = str(len(replacement.literal))
            else:
                op_id = "toml.edit.replace-scalar-semantic"
                arguments["representation_policy"] = replacement.policy.value
                arguments["value_kind"] = _value_kind_name(replacement.value.kind.value)
            arguments["target_role"] = "toml.scalar-item@1"
        elif operation.kind is EditOperationKind.INSERT_ENTRY:
            op_id = "toml.edit.insert-entry"
            arguments["key_bytes"] = str(len(operation.key.encode("utf-8")))
            arguments["placement"] = operation.placement.kind.lower()
            arguments["value_kind"] = _value_kind_name(operation.value.kind.value)
            arguments["target_role"] = "toml.table-item@1"
        elif operation.kind is EditOperationKind.REMOVE_ENTRY:
            op_id = "toml.edit.remove-entry"
            arguments["target_role"] = "toml.entry@1"
        elif operation.kind is EditOperationKind.RENAME_ENTRY:
            op_id = "toml.edit.rename-entry"
            arguments["key_bytes"] = str(len(operation.key.encode("utf-8")))
            arguments["target_role"] = "toml.entry@1"
        elif operation.kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
            op_id = "toml.edit.insert-array-element"
            arguments["placement"] = operation.placement.kind.lower()
            arguments["value_kind"] = _value_kind_name(operation.value.kind.value)
            arguments["target_role"] = "toml.array-item@1"
        else:
            op_id = "toml.edit.remove-array-element"
            arguments["target_role"] = "toml.array-element@1"
        summaries.append(
            EditOperationSummary.new(FormatOperationId.new(op_id, 1), arguments)
        )
    return summaries


def _value_kind_name(kind_value: str) -> str:
    """edit.rs:1260-1278 (kebab-case value-kind names)."""
    return {
        "Null": "null",
        "Boolean": "boolean",
        "Integer": "integer",
        "Decimal": "decimal",
        "BinaryFloat32": "binary-float32",
        "BinaryFloat64": "binary-float64",
        "String": "string",
        "Bytes": "bytes",
        "Date": "date",
        "Time": "time",
        "LocalDateTime": "local-date-time",
        "OffsetDateTime": "offset-date-time",
        "Sequence": "sequence",
        "Object": "object",
        "EntryMapping": "entry-mapping",
    }[kind_value]


def _is_scalar_kind(kind: TomlItemKind) -> bool:
    """edit.rs:1354-1366."""
    return kind in (
        TomlItemKind.STRING,
        TomlItemKind.INTEGER,
        TomlItemKind.FLOAT,
        TomlItemKind.BOOLEAN,
        TomlItemKind.OFFSET_DATE_TIME,
        TomlItemKind.LOCAL_DATE_TIME,
        TomlItemKind.LOCAL_DATE,
        TomlItemKind.LOCAL_TIME,
    )


def _is_table_kind(kind: TomlItemKind) -> bool:
    """edit.rs:1368-1377."""
    return kind in (
        TomlItemKind.ROOT_TABLE,
        TomlItemKind.STANDARD_TABLE,
        TomlItemKind.IMPLICIT_TABLE,
        TomlItemKind.DOTTED_TABLE,
        TomlItemKind.ARRAY_OF_TABLES,
    )


def _validate_exact_scalar(literal: bytes) -> None:
    """edit.rs:1379-1413: the candidate must parse as exactly one complete
    TOML 1.0 scalar whose value span is the entire candidate."""
    try:
        text = literal.decode("utf-8")
    except UnicodeDecodeError:
        raise TomlEditFailure(TomlEditFailureKind.INVALID_LITERAL) from None
    prefix = "_ = "
    source = (prefix + text).encode("utf-8")
    try:
        from consema.toml.document import TomlProfile

        document = parse(source, TomlProfile.TOML10_V1, ParseLimits())
    except Exception:
        raise TomlEditFailure(TomlEditFailureKind.INVALID_LITERAL) from None
    root = document.root()
    entries = root.table_entries()
    if entries is None or len(entries) != 1 or entries[0].name() != "_":
        raise TomlEditFailure(TomlEditFailureKind.INVALID_LITERAL)
    item = entries[0].item()
    if not _is_scalar_kind(item.kind()):
        raise TomlEditFailure(TomlEditFailureKind.INVALID_LITERAL)
    value_span = item.span()
    if value_span.start_byte != len(prefix) or value_span.end_byte != len(source):
        raise TomlEditFailure(TomlEditFailureKind.INVALID_LITERAL)


def _semantic_literal(
    value: PortableValue,
    old_kind: TomlItemKind,
    policy: RepresentationPolicy,
    target_span: Span,
    diagnostics: list[TomlDiagnostic],
) -> bytes:
    """edit.rs:1415-1456."""
    if policy is RepresentationPolicy.EXACT_LITERAL:
        raise TomlEditFailure(TomlEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL)
    new_kind = _portable_toml_kind(value)
    if new_kind is None:
        raise TomlEditFailure(
            TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=value.kind.value
        )
    compatible = old_kind is new_kind
    if policy is RepresentationPolicy.PRESERVE_COMPATIBLE and not compatible:
        raise TomlEditFailure(TomlEditFailureKind.REPRESENTATION_INCOMPATIBLE)
    if policy is RepresentationPolicy.PRESERVE_ELSE_CANONICAL and not compatible:
        diagnostics.append(
            TomlDiagnostic(
                code="toml.edit.representation-fallback@1",
                category=DiagnosticCategory.EDIT,
                severity=Severity.WARNING,
                primary=target_span,
                arguments={"old_kind": old_kind.value, "new_kind": new_kind.value},
                occurrence=len(diagnostics),
            )
        )
    literal = _canonical_literal(value)
    validated_kind = _validate_exact_scalar_kind(literal)
    if validated_kind is not new_kind:
        raise TomlEditFailure(
            TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=value.kind.value
        )
    return literal


def _validate_exact_scalar_kind(literal: bytes) -> TomlItemKind | None:
    """Revalidates a canonical literal and returns its scalar category
    (edit.rs:1451-1454)."""
    try:
        text = literal.decode("utf-8")
    except UnicodeDecodeError:
        return None
    prefix = "_ = "
    source = (prefix + text).encode("utf-8")
    try:
        from consema.toml.document import TomlProfile

        document = parse(source, TomlProfile.TOML10_V1, ParseLimits())
    except Exception:
        return None
    entries = document.root().table_entries()
    if entries is None or len(entries) != 1:
        return None
    item = entries[0].item()
    if not _is_scalar_kind(item.kind()):
        return None
    value_span = item.span()
    if value_span.start_byte != len(prefix) or value_span.end_byte != len(source):
        return None
    return item.kind()


def _portable_toml_kind(value: PortableValue) -> TomlItemKind | None:
    """edit.rs:1458-1470."""
    kind_value = value.kind.value
    return {
        "String": TomlItemKind.STRING,
        "Integer": TomlItemKind.INTEGER,
        "BinaryFloat64": TomlItemKind.FLOAT,
        "Boolean": TomlItemKind.BOOLEAN,
        "Date": TomlItemKind.LOCAL_DATE,
        "Time": TomlItemKind.LOCAL_TIME,
        "LocalDateTime": TomlItemKind.LOCAL_DATE_TIME,
        "OffsetDateTime": TomlItemKind.OFFSET_DATE_TIME,
    }.get(kind_value)


def _canonical_literal(value: PortableValue) -> bytes:
    """edit.rs:1472-1514."""
    kind_value = value.kind.value
    if kind_value == "String":
        return _canonical_string(value.as_string()).encode("utf-8")
    if kind_value == "Integer":
        integer = value.as_integer()
        if not -(2**63) <= integer <= 2**63 - 1:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return str(integer).encode("utf-8")
    if kind_value == "BinaryFloat64":
        text = _canonical_float(value.as_binary_float64())
        if text is None:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return text.encode("utf-8")
    if kind_value == "Boolean":
        return b"true" if value.as_boolean() else b"false"
    if kind_value == "Date":
        text = _canonical_date(value.as_date())
        if text is None:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return text.encode("utf-8")
    if kind_value == "Time":
        text = _canonical_time(value.as_time())
        if text is None:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return text.encode("utf-8")
    if kind_value == "LocalDateTime":
        local, time = value.as_local_date_time()
        date_text = _canonical_date(local.as_date())
        time_text = _canonical_time(time.as_time())
        if date_text is None or time_text is None:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return (date_text + "T" + time_text).encode("utf-8")
    if kind_value == "OffsetDateTime":
        local, offset_seconds = value.as_offset_date_time()
        date_value, time_value = local.as_local_date_time()
        date_text = _canonical_date(date_value.as_date())
        time_text = _canonical_time(time_value.as_time())
        offset_text = _canonical_offset(offset_seconds)
        if date_text is None or time_text is None or offset_text is None:
            raise TomlEditFailure(
                TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
            )
        return (date_text + "T" + time_text + offset_text).encode("utf-8")
    raise TomlEditFailure(
        TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=kind_value
    )


def _canonical_string(value: str) -> str:
    """edit.rs:1516-1537."""
    output = ['"']
    for character in value:
        code = ord(character)
        if character == "\b":
            output.append("\\b")
        elif character == "\t":
            output.append("\\t")
        elif character == "\n":
            output.append("\\n")
        elif character == "\f":
            output.append("\\f")
        elif character == "\r":
            output.append("\\r")
        elif character == '"':
            output.append('\\"')
        elif character == "\\":
            output.append("\\\\")
        elif code <= 0x1F or code == 0x7F:
            output.append(f"\\u{code:04X}")
        else:
            output.append(character)
    output.append('"')
    return "".join(output)


def _canonical_float(bits: int) -> str | None:
    """edit.rs:1539-1560."""
    if bits == 0x7FF8000000000000:
        return "nan"
    if bits == 0xFFF8000000000000:
        return "-nan"
    if bits == 0x7FF0000000000000:
        return "inf"
    if bits == 0xFFF0000000000000:
        return "-inf"
    if bits & 0x7FF0000000000000 == 0x7FF0000000000000:
        return None
    import struct

    value = struct.unpack(">d", struct.pack(">Q", bits))[0]
    text = repr(value)
    if not any(character in text for character in ".eE"):
        text += ".0"
    return text


def _canonical_date(date: tuple[int, int, int]) -> str | None:
    """edit.rs:1562-1568."""
    year, month, day = date
    if not 0 <= year <= 9999:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _canonical_time(time: tuple[int, int, int, Decimal]) -> str | None:
    """edit.rs:1570-1587."""
    hour, minute, second, fraction = time
    nanoseconds = _exact_nanoseconds(fraction)
    if nanoseconds is None:
        return None
    output = f"{hour:02d}:{minute:02d}:{second:02d}"
    if nanoseconds != 0:
        fraction_text = f"{nanoseconds:09d}"
        while fraction_text.endswith("0"):
            fraction_text = fraction_text[:-1]
        output += "." + fraction_text
    return output


def _canonical_offset(offset_seconds: int) -> str | None:
    """edit.rs:1597-1616."""
    if offset_seconds == 0:
        return "Z"
    if offset_seconds % 60 != 0:
        return None
    minutes = offset_seconds // 60
    if abs(minutes) >= 24 * 60:
        return None
    sign = "-" if minutes < 0 else "+"
    magnitude = abs(minutes)
    return f"{sign}{magnitude // 60:02d}:{magnitude % 60:02d}"


def _exact_nanoseconds(fraction: Decimal) -> int | None:
    if fraction.coefficient == 0:
        return 0
    exponent = fraction.exponent
    if not -9 <= exponent < 0:
        return None
    nanoseconds = fraction.coefficient
    if nanoseconds < 0:
        return None
    for _ in range(exponent + 9):
        nanoseconds *= 10
    if nanoseconds >= 1_000_000_000:
        return None
    return nanoseconds


def _find_item_by_span(document: Document, start: int, end: int) -> NodeRef | None:
    """edit.rs:1638-1651."""
    matches = [
        index
        for index, entity in enumerate(document._entities)
        if isinstance(entity.kind, _ItemEntity)
        and entity.span.start_byte == start
        and entity.span.end_byte == end
    ]
    if len(matches) != 1:
        return None
    return document.node_ref(matches[0], NodeRole.TOML_ITEM)
