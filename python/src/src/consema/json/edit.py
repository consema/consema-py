"""JSON-family edit transactions: scalar and structural operations.

Authority (Rust arbitration for exact byte semantics):

- Operation and policy model: crates/consema-json/src/edit.rs:17-58
  (RepresentationPolicy, ScalarReplacement), 59-108 (EditOperation),
  110-243 (EditTransaction/Builder).
- Failure algebra and codes: edit.rs:260-299 (EditFailure), 1269-1324
  (StableFailure; code mapping 1299-1323).
- Atomic commit: edit.rs:301-451 — Recovered/WrongSnapshot gates
  (edit.rs:304-309), dependency validation (edit.rs:310, 1025-1078),
  prepared-edit overlap/ownership conflicts (edit.rs:319-334), bounded
  target length (edit.rs:336-346), rendering and reparse
  (edit.rs:347-359), ChangeSet source edits and node mappings
  (edit.rs:361-422), SourcePatch derivation (edit.rs:430-438),
  UntouchedByteProof (edit.rs:439-444). Dry-run produces the identical
  patch and target digest (edit.rs:453-468; RFC 0004 §14).
- Scalar preparation: edit.rs:505-556 (targets Value or ObjectKey; literal
  validation edit.rs:1831-1862; semantic_literal edit.rs:1346-1386 with
  the fallback diagnostic json.edit.representation-fallback@1).
- Structural preparation: edit.rs:558-623 (insert member/array element
  fragments), 625-696 (insertion placement and comma ownership),
  697-849 (removal and removal_comma), 851-872 (rename), 874-900
  (resolve target/anchor), 925-1022 (parent lookup and comma/delimiter
  discovery), 1326-1344 (PreparedEdit/InsertionSyntax/MappingPlan).
- PreserveCompatible lexical styles: edit.rs:1388-1504 (style analysis),
  1506-1579 (string escape style), 1581-1753 (preserving renderers,
  including MAX_PRESERVED_FRACTION_DIGITS = 1_000_000 at edit.rs:1389 and
  the decimal fixed-fraction text edit.rs:1727-1739), 1755-1829
  (canonical literals with UPPERCASE \\uXXXX escapes, edit.rs:1797-1829).
- Operation metadata: edit.rs:1110-1133 (operation.{index} =
  "json.edit.*@1" forms) and operation summaries edit.rs:1135-1230.
- The v1/v2 vector goldens this module must reproduce byte-for-byte:
  conformance/vectors/v1.json:107-141 (scalar edits) and
  json-family-v2.json:174-190 (move-member and preserve-scalars).

Frozen operation ids (crates/consema-json/src/operation_registry.rs:16-79):
json.edit.insert-member@1, remove-member@1, move-member@1, rename-member@1,
insert-array-element@1, remove-array-element@1, replace-scalar-semantic@1,
replace-scalar-literal@1.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.core.value import PortableValue, Kind
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
from consema.document.limits import ParseLimits
from consema.document.materialization import MaterializationLimits
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
from consema.json.document import JsonDocument
from consema.json.errors import (
    JsonDiagnostic,
    JsonEditFailure,
    JsonEditFailureKind,
    JsonSeverity,
)
from consema.json.kinds import JsonProfile, JsonSyntaxKind
from consema.json.materialization import canonical_fragment
from consema.json.parser import (
    InternalKind,
    InternalValue,
    MemberEntity,
    ValueEntity,
    parse,
)
from consema.protocol.error_registry import DiagnosticCategory

# Maximum digits a preserved fixed-fraction rendering may produce
# (edit.rs:1389).
MAX_PRESERVED_FRACTION_DIGITS = 1_000_000


class RepresentationPolicy(enum.Enum):
    """Explicit semantic scalar representation policy (edit.rs:19-28)."""

    EXACT_LITERAL = "ExactLiteral"
    PRESERVE_COMPATIBLE = "PreserveCompatible"
    CANONICAL_FOR_PROFILE = "CanonicalForProfile"
    PRESERVE_ELSE_CANONICAL = "PreserveElseCanonical"


class ScalarReplacementKind(enum.Enum):
    """Scalar operation kind (edit.rs:30-49)."""

    SEMANTIC = "Semantic"
    LITERAL = "Literal"


@dataclass(frozen=True, slots=True)
class ScalarReplacement:
    """One scalar operation bound to the transaction base snapshot
    (edit.rs:30-49)."""

    target: NodeRef
    value: PortableValue | None = None
    policy: RepresentationPolicy | None = None
    literal: bytes | None = None

    @property
    def kind(self) -> ScalarReplacementKind:
        if self.value is not None:
            return ScalarReplacementKind.SEMANTIC
        return ScalarReplacementKind.LITERAL


class EditOperationKind(enum.Enum):
    """Typed edit operation kinds (edit.rs:59-108)."""

    REPLACE_SCALAR = "ReplaceScalar"
    INSERT_MEMBER = "InsertMember"
    REMOVE_MEMBER = "RemoveMember"
    MOVE_MEMBER = "MoveMember"
    RENAME_MEMBER = "RenameMember"
    INSERT_ARRAY_ELEMENT = "InsertArrayElement"
    REMOVE_ARRAY_ELEMENT = "RemoveArrayElement"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One typed JSON edit operation bound to one immutable base snapshot
    (edit.rs:59-108)."""

    kind: EditOperationKind
    scalar: ScalarReplacement | None = None
    object: NodeRef | None = None
    array: NodeRef | None = None
    target: NodeRef | None = None
    name: str | None = None
    value: PortableValue | None = None
    placement: AssociationPlacement | None = None


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable transaction; every operation resolves against one base
    snapshot (edit.rs:110-129)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs:131-243)."""

    def __init__(self, document: JsonDocument) -> None:
        self._base = document.snapshot_identity()
        self._operations: list[EditOperation] = []

    def semantic_scalar(
        self,
        target: NodeRef,
        value: PortableValue,
        policy: RepresentationPolicy,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_SCALAR,
                scalar=ScalarReplacement(target=target, value=value, policy=policy),
            )
        )
        return self

    def literal_scalar(self, target: NodeRef, literal: bytes) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.REPLACE_SCALAR,
                scalar=ScalarReplacement(target=target, literal=bytes(literal)),
            )
        )
        return self

    def insert_member(
        self,
        object_value: NodeRef,
        name: str,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_MEMBER,
                object=object_value,
                name=name,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_member(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_MEMBER, target=target)
        )
        return self

    def move_member(
        self, target: NodeRef, placement: AssociationPlacement
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.MOVE_MEMBER, target=target, placement=placement)
        )
        return self

    def rename_member(self, target: NodeRef, name: str) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.RENAME_MEMBER, target=target, name=name)
        )
        return self

    def insert_array_element(
        self,
        array: NodeRef,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ARRAY_ELEMENT,
                array=array,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_array_element(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_ARRAY_ELEMENT, target=target)
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """Atomic edit success (edit.rs:245-258)."""

    document: JsonDocument
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# -- internal preparation records --------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    old_span: Span
    replacement: bytes
    mapping: tuple[NodeRef, _MappingPlan] | None = None


class _MappingPlanKind(enum.Enum):
    REPLACED_LITERAL = "ReplacedLiteral"
    DELETED = "Deleted"
    UNMAPPED = "Unmapped"


@dataclass(frozen=True, slots=True)
class _MappingPlan:
    kind: _MappingPlanKind
    role: NodeRole | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _InsertionSyntax:
    anchor_role: NodeRole
    open: JsonSyntaxKind
    close: JsonSyntaxKind


class _EditPlanner:
    """One planner bound to the base document (mirror of the Rust
    Document::prepare_* methods, edit.rs:471-1022)."""

    def __init__(self, document: JsonDocument) -> None:
        self.document = document

    # -- resolution ---------------------------------------------------------

    def resolve_target(self, target: NodeRef, roles: tuple[NodeRole, ...]) -> int:
        if target.snapshot != self.document.snapshot_identity():
            raise JsonEditFailure(JsonEditFailureKind.WRONG_SNAPSHOT)
        if target.role not in roles:
            raise JsonEditFailure(JsonEditFailureKind.WRONG_ROLE)
        if target.index < 0 or target.index >= len(self.document.entities):
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        return target.index

    def resolve_anchor(
        self, anchor: NodeRef, role: NodeRole, associations: list[int]
    ) -> int:
        index = self.resolve_target(anchor, (role,))
        if index not in associations:
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        return index

    def value_entity(self, index: int) -> ValueEntity:
        entity = self.document.entities[index]
        assert isinstance(entity, ValueEntity)
        return entity

    def span(self, index: int) -> Span:
        return self.document.entities[index].span

    def parent_object(
        self, member: int
    ) -> tuple[int, list[int], int] | None:
        for index, entity in enumerate(self.document.entities):
            if isinstance(entity, ValueEntity) and entity.internal.kind is InternalKind.OBJECT:
                members = list(entity.internal.payload)
                if member in members:
                    return index, members, members.index(member)
        return None

    def parent_array(
        self, element: int
    ) -> tuple[int, list[int], int] | None:
        for index, entity in enumerate(self.document.entities):
            if isinstance(entity, ValueEntity) and entity.internal.kind is InternalKind.ARRAY:
                elements = list(entity.internal.payload)
                if element in elements:
                    return index, elements, elements.index(element)
        return None

    def fragment(self, value: PortableValue) -> bytes:
        try:
            return canonical_fragment(
                value,
                self.document.profile,
                MaterializationLimits(
                    max_input_nodes=self.document.parse_limits.max_node_count,
                    max_output_bytes=self.document.parse_limits.max_source_bytes,
                    max_depth=self.document.parse_limits.max_nesting_depth,
                    max_report_entries=self.document.parse_limits.max_diagnostics,
                    max_provenance_entries=self.document.parse_limits.max_node_count * 4,
                ),
            )
        except Exception as failure:
            raise _fragment_failure(failure) from None

    # -- preparation --------------------------------------------------------

    def prepare_operation(
        self, operation: EditOperation, diagnostics: list[JsonDiagnostic]
    ) -> list[_PreparedEdit]:
        if operation.kind is EditOperationKind.REPLACE_SCALAR:
            return [self.prepare_scalar(operation.scalar, diagnostics)]
        if operation.kind is EditOperationKind.INSERT_MEMBER:
            return self.prepare_insert_member(
                operation.object, operation.name, operation.value, operation.placement
            )
        if operation.kind is EditOperationKind.REMOVE_MEMBER:
            return self.prepare_remove_member(operation.target)
        if operation.kind is EditOperationKind.MOVE_MEMBER:
            return self.prepare_move_member(operation.target, operation.placement)
        if operation.kind is EditOperationKind.RENAME_MEMBER:
            return [self.prepare_rename_member(operation.target, operation.name)]
        if operation.kind is EditOperationKind.INSERT_ARRAY_ELEMENT:
            return self.prepare_insert_array_element(
                operation.array, operation.value, operation.placement
            )
        return self.prepare_remove_array_element(operation.target)

    def prepare_scalar(
        self, operation: ScalarReplacement, diagnostics: list[JsonDiagnostic]
    ) -> _PreparedEdit:
        index = self.resolve_target(operation.target, (NodeRole.VALUE, NodeRole.OBJECT_KEY))
        entity = self.value_entity(index)
        if not entity.complete or entity.literal_span is None:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        if entity.internal.kind is InternalKind.UNAVAILABLE:
            raise JsonEditFailure(JsonEditFailureKind.SEMANTIC_UNAVAILABLE)
        if entity.internal.kind in (InternalKind.ARRAY, InternalKind.OBJECT):
            raise JsonEditFailure(JsonEditFailureKind.WRONG_ROLE)
        if operation.kind is ScalarReplacementKind.LITERAL:
            literal = operation.literal
            literal_kind = validate_literal(literal, self.document.profile, self.document.parse_limits)
            if operation.target.role is NodeRole.OBJECT_KEY and literal_kind is not _LITERAL_STRING:
                raise JsonEditFailure(JsonEditFailureKind.INVALID_LITERAL)
            replacement = literal
        else:
            value = operation.value
            if operation.target.role is NodeRole.OBJECT_KEY and value.kind is not Kind.STRING:
                raise JsonEditFailure(
                    JsonEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, detail=value.kind.value
                )
            old_span = entity.literal_span
            old_bytes = self.document.source.bytes()
            old_literal = old_bytes[old_span.start_byte : old_span.end_byte]
            replacement = semantic_literal(
                value,
                entity.internal,
                old_literal,
                self.document.profile,
                operation.policy,
                old_span,
                diagnostics,
            )
        return _PreparedEdit(
            old_span=entity.literal_span,
            replacement=replacement,
            mapping=(
                operation.target,
                _MappingPlan(_MappingPlanKind.REPLACED_LITERAL, operation.target.role),
            ),
        )

    def prepare_insert_member(
        self,
        object_value: NodeRef,
        name: str,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> list[_PreparedEdit]:
        index = self.resolve_target(object_value, (NodeRole.VALUE,))
        entity = self.value_entity(index)
        if not entity.complete:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        if entity.internal.kind is not InternalKind.OBJECT:
            raise JsonEditFailure(JsonEditFailureKind.WRONG_ROLE)
        fragment = bytearray(self.fragment(PortableValue.string(name)))
        fragment.append(ord(":"))
        fragment.extend(self.fragment(value))
        return [
            self.prepare_insertion(
                object_value,
                entity.span,
                list(entity.internal.payload),
                _InsertionSyntax(
                    anchor_role=NodeRole.OBJECT_MEMBER,
                    open=JsonSyntaxKind.LEFT_BRACE,
                    close=JsonSyntaxKind.RIGHT_BRACE,
                ),
                placement,
                bytes(fragment),
            )
        ]

    def prepare_insert_array_element(
        self,
        array: NodeRef,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> list[_PreparedEdit]:
        index = self.resolve_target(array, (NodeRole.VALUE,))
        entity = self.value_entity(index)
        if not entity.complete:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        if entity.internal.kind is not InternalKind.ARRAY:
            raise JsonEditFailure(JsonEditFailureKind.WRONG_ROLE)
        return [
            self.prepare_insertion(
                array,
                entity.span,
                list(entity.internal.payload),
                _InsertionSyntax(
                    anchor_role=NodeRole.ARRAY_ELEMENT,
                    open=JsonSyntaxKind.LEFT_BRACKET,
                    close=JsonSyntaxKind.RIGHT_BRACKET,
                ),
                placement,
                self.fragment(value),
            )
        ]

    def prepare_insertion(
        self,
        container: NodeRef,
        container_span: Span,
        associations: list[int],
        syntax: _InsertionSyntax,
        placement: AssociationPlacement,
        fragment: bytes,
    ) -> _PreparedEdit:
        if not associations:
            if placement.kind == "Start":
                position = self.delimiter(syntax.open, container_span, False).end_byte
                prefix_comma = False
                suffix_comma = False
            elif placement.kind == "End":
                position = self.delimiter(syntax.close, container_span, True).start_byte
                prefix_comma = False
                suffix_comma = False
            else:
                raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        else:
            if placement.kind == "Start":
                position = self.span(associations[0]).start_byte
                prefix_comma = False
                suffix_comma = True
            elif placement.kind == "End":
                position = self.span(associations[-1]).end_byte
                prefix_comma = True
                suffix_comma = False
            elif placement.kind == "Before":
                anchor = self.resolve_anchor(placement.anchor, syntax.anchor_role, associations)
                position = self.span(anchor).start_byte
                prefix_comma = False
                suffix_comma = True
            else:  # After
                anchor = self.resolve_anchor(placement.anchor, syntax.anchor_role, associations)
                position = self.span(anchor).end_byte
                prefix_comma = True
                suffix_comma = False
        replacement = bytearray()
        if prefix_comma:
            replacement.append(ord(","))
        replacement.extend(fragment)
        if suffix_comma:
            replacement.append(ord(","))
        return _PreparedEdit(
            old_span=self.document.authority.span(position, position),
            replacement=bytes(replacement),
        )

    def delimiter(
        self, kind: JsonSyntaxKind, container: Span, last: bool
    ) -> Span:
        span = self.syntax_between(kind, container.start_byte, container.end_byte, last)
        if span is None:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        return span

    def syntax_between(
        self,
        kind: JsonSyntaxKind,
        start: int,
        end: int,
        last: bool,
    ) -> Span | None:
        pieces = self.document.lossless_structural_index().pieces
        kinds = self.document.lossless_syntax_kinds()
        matches = [
            piece.span
            for piece, candidate in zip(pieces, kinds)
            if candidate is kind
            and piece.span.start_byte >= start
            and piece.span.end_byte <= end
        ]
        if not matches:
            return None
        return matches[-1] if last else matches[0]

    def prepare_remove_member(self, target: NodeRef) -> list[_PreparedEdit]:
        index = self.resolve_target(target, (NodeRole.OBJECT_MEMBER,))
        found = self.parent_object(index)
        if found is None:
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        container, members, ordinal = found
        return self.prepare_removal(
            target,
            index,
            members,
            ordinal,
            self.span(container).end_byte,
        )

    def prepare_remove_array_element(self, target: NodeRef) -> list[_PreparedEdit]:
        index = self.resolve_target(target, (NodeRole.ARRAY_ELEMENT,))
        found = self.parent_array(index)
        if found is None:
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        container, elements, ordinal = found
        return self.prepare_removal(
            target,
            index,
            elements,
            ordinal,
            self.span(container).end_byte,
        )

    def prepare_removal(
        self,
        target: NodeRef,
        index: int,
        associations: list[int],
        ordinal: int,
        container_end: int,
    ) -> list[_PreparedEdit]:
        target_span = self.span(index)
        edits: list[_PreparedEdit] = []
        comma = self.removal_comma(associations, ordinal, container_end)
        if comma is not None:
            if comma.end_byte == target_span.start_byte or comma.start_byte == target_span.end_byte:
                edits.append(
                    _PreparedEdit(
                        old_span=self.document.authority.span(
                            min(comma.start_byte, target_span.start_byte),
                            max(comma.end_byte, target_span.end_byte),
                        ),
                        replacement=b"",
                        mapping=(target, _MappingPlan(_MappingPlanKind.DELETED)),
                    )
                )
                return edits
            edits.append(
                _PreparedEdit(
                    old_span=target_span,
                    replacement=b"",
                    mapping=(target, _MappingPlan(_MappingPlanKind.DELETED)),
                )
            )
            edits.append(
                _PreparedEdit(old_span=comma, replacement=b"")
            )
        else:
            edits.append(
                _PreparedEdit(
                    old_span=target_span,
                    replacement=b"",
                    mapping=(target, _MappingPlan(_MappingPlanKind.DELETED)),
                )
            )
        return edits

    def removal_comma(
        self, associations: list[int], ordinal: int, container_end: int
    ) -> Span | None:
        current = self.span(associations[ordinal])
        if ordinal + 1 < len(associations):
            following_end = self.span(associations[ordinal + 1]).start_byte
        else:
            following_end = container_end
        comma = self.syntax_between(
            JsonSyntaxKind.COMMA, current.end_byte, following_end, False
        )
        if comma is not None:
            return comma
        if ordinal == 0:
            return None
        previous = self.span(associations[ordinal - 1])
        comma = self.syntax_between(
            JsonSyntaxKind.COMMA, previous.end_byte, current.start_byte, True
        )
        if comma is None:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        return comma

    def prepare_move_member(
        self, target: NodeRef, placement: AssociationPlacement
    ) -> list[_PreparedEdit]:
        index = self.resolve_target(target, (NodeRole.OBJECT_MEMBER,))
        found = self.parent_object(index)
        if found is None:
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        container, members, ordinal = found
        remaining = [member for member in members if member != index]
        if placement.kind == "Start":
            destination = 0
        elif placement.kind == "End":
            destination = len(remaining)
        else:
            if placement.anchor == target:
                raise JsonEditFailure(JsonEditFailureKind.PLACEMENT_ANCHOR_MODIFIED)
            anchor = self.resolve_anchor(placement.anchor, NodeRole.OBJECT_MEMBER, remaining)
            position = remaining.index(anchor)
            destination = position + 1 if placement.kind == "After" else position
        if destination == ordinal:
            return []
        target_span = self.span(index)
        raw = self.document.source.bytes()
        fragment = raw[target_span.start_byte : target_span.end_byte]
        edits = self.prepare_removal(
            target,
            index,
            members,
            ordinal,
            self.span(container).end_byte,
        )
        new_edits = []
        for edit in edits:
            if edit.mapping is not None and edit.mapping[0] == target:
                edit = _PreparedEdit(
                    old_span=edit.old_span,
                    replacement=edit.replacement,
                    mapping=(
                        target,
                        _MappingPlan(
                            _MappingPlanKind.UNMAPPED, reason="member-reparsed-after-move"
                        ),
                    ),
                )
            new_edits.append(edit)
        new_edits.append(
            self.prepare_insertion(
                self.document.node_ref(container, NodeRole.VALUE),
                self.span(container),
                remaining,
                _InsertionSyntax(
                    anchor_role=NodeRole.OBJECT_MEMBER,
                    open=JsonSyntaxKind.LEFT_BRACE,
                    close=JsonSyntaxKind.RIGHT_BRACE,
                ),
                placement,
                fragment,
            )
        )
        return new_edits

    def prepare_rename_member(self, target: NodeRef, name: str) -> _PreparedEdit:
        index = self.resolve_target(target, (NodeRole.OBJECT_MEMBER,))
        if self.parent_object(index) is None:
            raise JsonEditFailure(JsonEditFailureKind.TARGET_NOT_FOUND)
        entity = self.document.entities[index]
        if not isinstance(entity, MemberEntity):
            raise JsonEditFailure(JsonEditFailureKind.WRONG_ROLE)
        key = self.value_entity(entity.key)
        if key.literal_span is None:
            raise JsonEditFailure(JsonEditFailureKind.INCOMPLETE_TARGET)
        return _PreparedEdit(
            old_span=key.literal_span,
            replacement=self.fragment(PortableValue.string(name)),
            mapping=(
                target,
                _MappingPlan(
                    _MappingPlanKind.UNMAPPED, reason="member-reparsed-after-key-rename"
                ),
            ),
        )


def _fragment_failure(failure: Exception) -> JsonEditFailure:
    from consema.document.materialization import MaterializationFailure

    if isinstance(failure, MaterializationFailure):
        if failure.kind.value == "unrepresentable":
            return JsonEditFailure(
                JsonEditFailureKind.UNREPRESENTABLE_VALUE, detail=failure.name
            )
        if failure.kind.value == "resource-limit":
            return JsonEditFailure(
                JsonEditFailureKind.RESOURCE_LIMIT, resource_name=failure.name
            )
    return JsonEditFailure(JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)


# ---------------------------------------------------------------------------
# Dependency validation (edit.rs:1025-1078)
# ---------------------------------------------------------------------------


def validate_dependencies(transaction: EditTransaction) -> None:
    destructive: set[NodeRef] = set()
    removed: set[NodeRef] = set()
    anchors: list[NodeRef] = []
    moved: set[NodeRef] = set()
    move_anchors: list[NodeRef] = []
    for operation in transaction.operations:
        if operation.kind is EditOperationKind.REPLACE_SCALAR:
            target = operation.scalar.target
        elif operation.kind in (
            EditOperationKind.REMOVE_MEMBER,
            EditOperationKind.MOVE_MEMBER,
            EditOperationKind.RENAME_MEMBER,
            EditOperationKind.REMOVE_ARRAY_ELEMENT,
        ):
            target = operation.target
        else:
            target = None
        if target is not None:
            if target in destructive:
                raise JsonEditFailure(JsonEditFailureKind.DUPLICATE_TARGET)
            destructive.add(target)
        if operation.kind in (
            EditOperationKind.REMOVE_MEMBER,
            EditOperationKind.REMOVE_ARRAY_ELEMENT,
        ):
            removed.add(operation.target)
        if operation.kind in (
            EditOperationKind.INSERT_MEMBER,
            EditOperationKind.INSERT_ARRAY_ELEMENT,
            EditOperationKind.MOVE_MEMBER,
        ):
            placement = operation.placement
            if placement.kind in ("Before", "After"):
                anchors.append(placement.anchor)
                if operation.kind is EditOperationKind.MOVE_MEMBER:
                    move_anchors.append(placement.anchor)
        if operation.kind is EditOperationKind.MOVE_MEMBER:
            moved.add(operation.target)
    if any(anchor in removed for anchor in anchors):
        raise JsonEditFailure(JsonEditFailureKind.PLACEMENT_ANCHOR_REMOVED)
    if any(anchor in moved for anchor in anchors) or any(
        anchor in destructive for anchor in move_anchors
    ):
        raise JsonEditFailure(JsonEditFailureKind.PLACEMENT_ANCHOR_MODIFIED)


# ---------------------------------------------------------------------------
# Commit and dry-run (edit.rs:301-468)
# ---------------------------------------------------------------------------


def commit(document: JsonDocument, transaction: EditTransaction) -> EditCommit:
    """Atomically commits scalar and structural operations; on failure the
    base document remains unchanged (edit.rs:301-451)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise JsonEditFailure(JsonEditFailureKind.RECOVERED_DOCUMENT)
    if transaction.base != document.snapshot_identity():
        raise JsonEditFailure(JsonEditFailureKind.WRONG_SNAPSHOT)
    validate_dependencies(transaction)
    diagnostics: list[JsonDiagnostic] = []
    planner = _EditPlanner(document)
    prepared: list[_PreparedEdit] = []
    for operation in transaction.operations:
        prepared.extend(planner.prepare_operation(operation, diagnostics))
    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    for index in range(len(prepared) - 1):
        left, right = prepared[index], prepared[index + 1]
        if (
            not left.old_span.is_empty()
            and not right.old_span.is_empty()
            and (
                left.old_span.end_byte > right.old_span.start_byte
                or left.old_span == right.old_span
            )
        ):
            raise JsonEditFailure(JsonEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
        if left.old_span == right.old_span or (
            left.old_span.is_empty()
            and right.old_span.is_empty()
            and left.old_span.start_byte == right.old_span.start_byte
        ):
            raise JsonEditFailure(JsonEditFailureKind.OVERLAPPING_OWNERSHIP)
    raw = document.source.bytes()
    target_len = len(raw)
    for edit in prepared:
        target_len = target_len - edit.old_span.len() + len(edit.replacement)
        if target_len < 0:
            raise JsonEditFailure(JsonEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    if target_len > document.parse_limits.max_source_bytes:
        raise JsonEditFailure(JsonEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes")
    rendered = bytearray()
    cursor = 0
    for edit in prepared:
        rendered.extend(raw[cursor : edit.old_span.start_byte])
        rendered.extend(edit.replacement)
        cursor = edit.old_span.end_byte
    rendered.extend(raw[cursor:])
    try:
        new_document = parse(bytes(rendered), document.profile, document.parse_limits)
    except Exception:
        raise JsonEditFailure(JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None

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
        if edit.mapping is not None:
            old, plan = edit.mapping
            if old not in mapped_old:
                mapped_old.add(old)
                if plan.kind is _MappingPlanKind.REPLACED_LITERAL:
                    new_index = find_value_by_literal_span(
                        new_document, new_start, new_end
                    )
                    if new_index is not None:
                        new_node = new_document.node_ref(new_index, plan.role)
                        status = NodeMappingStatus.REPLACED
                        reason = None
                    else:
                        new_node = None
                        status = NodeMappingStatus.REPLACED
                        reason = "reparsed-node-not-uniquely-located"
                    mappings.append(NodeMapping(old=old, new=new_node, status=status, reason=reason))
                elif plan.kind is _MappingPlanKind.DELETED:
                    mappings.append(
                        NodeMapping(old=old, new=None, status=NodeMappingStatus.DELETED, reason=None)
                    )
                else:
                    mappings.append(
                        NodeMapping(
                            old=old, new=None, status=NodeMappingStatus.UNMAPPED, reason=plan.reason
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
        raise JsonEditFailure(JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    try:
        untouched_proof = UntouchedByteProof.create(
            document.source, new_document.source, list(source_patch.replacements)
        )
    except Exception:
        raise JsonEditFailure(JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def dry_run(
    document: JsonDocument,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:453-468)."""
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
        raise JsonEditFailure(JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


def _source_patch_limits(
    parse_limits: ParseLimits, operation_count: int
) -> SourcePatchLimits:
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=parse_limits.max_source_bytes,
            max_decoded_utf8_bytes=parse_limits.max_source_bytes,
            max_decoded_scalars=parse_limits.max_source_bytes,
        ),
        max_replacements=operation_count,
        max_patch_bytes=parse_limits.max_source_bytes * 2,
    )


def operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Operation metadata keys: operation.{index} = "id@version"
    (edit.rs:1110-1133)."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


_OPERATION_ID_BY_KIND = {
    EditOperationKind.INSERT_MEMBER: "json.edit.insert-member@1",
    EditOperationKind.REMOVE_MEMBER: "json.edit.remove-member@1",
    EditOperationKind.MOVE_MEMBER: "json.edit.move-member@1",
    EditOperationKind.RENAME_MEMBER: "json.edit.rename-member@1",
    EditOperationKind.INSERT_ARRAY_ELEMENT: "json.edit.insert-array-element@1",
    EditOperationKind.REMOVE_ARRAY_ELEMENT: "json.edit.remove-array-element@1",
}


def _operation_id(operation: EditOperation) -> str:
    if operation.kind is EditOperationKind.REPLACE_SCALAR:
        if operation.scalar.kind is ScalarReplacementKind.SEMANTIC:
            return "json.edit.replace-scalar-semantic@1"
        return "json.edit.replace-scalar-literal@1"
    return _OPERATION_ID_BY_KIND[operation.kind]


def operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe, content-free operation summaries (edit.rs:1135-1230)."""
    summaries = []
    for index, operation in enumerate(transaction.operations):
        summary = EditOperationSummary.new(
            FormatOperationId.new(_operation_id(operation).rsplit("@", 1)[0], 1),
            {},
        )
        summaries.append(summary)
    return summaries


# ---------------------------------------------------------------------------
# Scalar literal machinery (edit.rs:1346-1862)
# ---------------------------------------------------------------------------


_LITERAL_STRING = "String"


def semantic_literal(
    value: PortableValue,
    old: InternalValue,
    old_literal: bytes,
    profile: JsonProfile,
    policy: RepresentationPolicy,
    target_span: Span,
    diagnostics: list[JsonDiagnostic],
) -> bytes:
    """Renders the replacement literal under the explicit policy
    (edit.rs:1346-1386)."""
    if policy is RepresentationPolicy.EXACT_LITERAL:
        raise JsonEditFailure(JsonEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION)
    if portable_json_kind(value, profile) is None:
        raise JsonEditFailure(
            JsonEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, detail=value.kind.value
        )
    preserved = None
    style = analyze_lexical_style(old_literal, old)
    if style is not None:
        preserved = render_preserving_style(value, style)
    if policy is RepresentationPolicy.PRESERVE_COMPATIBLE:
        if preserved is None:
            raise JsonEditFailure(JsonEditFailureKind.REPRESENTATION_INCOMPATIBLE)
        return preserved
    if policy is RepresentationPolicy.CANONICAL_FOR_PROFILE:
        return canonical_literal(value, profile)
    # PreserveElseCanonical
    if preserved is not None:
        return preserved
    diagnostics.append(
        JsonDiagnostic(
            code="json.edit.representation-fallback@1",
            category=DiagnosticCategory.EDIT,
            severity=JsonSeverity.WARNING,
            primary=target_span,
        )
    )
    return canonical_literal(value, profile)


class _IntegerRadixKind(enum.Enum):
    DECIMAL = "Decimal"
    HEX = "Hex"


@dataclass(frozen=True, slots=True)
class _IntegerLexicalStyle:
    radix: _IntegerRadixKind
    uppercase_prefix: bool = False
    uppercase_digits: bool = False
    explicit_plus: bool = False


@dataclass(frozen=True, slots=True)
class _DecimalLexicalStyle:
    fraction_scale: int | None
    exponent_marker: str | None
    exponent_plus: bool
    leading_plus: bool
    leading_point: bool


@dataclass(frozen=True, slots=True)
class _NonFiniteLexicalStyle:
    explicit_plus: bool


@dataclass(frozen=True, slots=True)
class _StringLexicalStyle:
    quote: str
    escapes: dict[str, str]


class _ScalarStyleKind(enum.Enum):
    NULL = "Null"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    NON_FINITE = "NonFinite"
    STRING = "String"


@dataclass(frozen=True, slots=True)
class _JsonScalarLexicalStyle:
    kind: _ScalarStyleKind
    integer: _IntegerLexicalStyle | None = None
    decimal: _DecimalLexicalStyle | None = None
    non_finite: _NonFiniteLexicalStyle | None = None
    string: _StringLexicalStyle | None = None


def analyze_lexical_style(
    literal: bytes, old: InternalValue
) -> _JsonScalarLexicalStyle | None:
    """Bounded lexical style retained by PreserveCompatible edits
    (edit.rs:1442-1504)."""
    if old.kind is InternalKind.NULL:
        return _JsonScalarLexicalStyle(_ScalarStyleKind.NULL)
    if old.kind is InternalKind.BOOLEAN:
        return _JsonScalarLexicalStyle(_ScalarStyleKind.BOOLEAN)
    if old.kind is InternalKind.INTEGER:
        try:
            text = literal.decode("utf-8")
        except UnicodeDecodeError:
            return None
        unsigned = text[1:] if text[:1] in ("+", "-") else text
        if unsigned.startswith("0x") or unsigned.startswith("0X"):
            radix = _IntegerRadixKind.HEX
            uppercase_prefix = unsigned.startswith("0X")
            digits = unsigned[2:]
            uppercase_digits = any(character.isupper() for character in digits)
        else:
            radix = _IntegerRadixKind.DECIMAL
            uppercase_prefix = False
            uppercase_digits = False
        return _JsonScalarLexicalStyle(
            _ScalarStyleKind.INTEGER,
            integer=_IntegerLexicalStyle(
                radix=radix,
                uppercase_prefix=uppercase_prefix,
                uppercase_digits=uppercase_digits,
                explicit_plus=text.startswith("+"),
            ),
        )
    if old.kind is InternalKind.DECIMAL:
        try:
            text = literal.decode("utf-8")
        except UnicodeDecodeError:
            return None
        unsigned = text[1:] if text[:1] in ("+", "-") else text
        exponent_index = None
        for marker in ("e", "E"):
            if marker in unsigned:
                exponent_index = unsigned.index(marker)
                break
        mantissa = unsigned if exponent_index is None else unsigned[:exponent_index]
        if "." in mantissa:
            fraction_scale = len(mantissa) - mantissa.index(".") - 1
        else:
            fraction_scale = None
        if exponent_index is not None:
            after = unsigned[exponent_index + 1 :]
            exponent_plus = after.startswith("+")
            exponent_marker = unsigned[exponent_index]
        else:
            exponent_plus = False
            exponent_marker = None
        return _JsonScalarLexicalStyle(
            _ScalarStyleKind.DECIMAL,
            decimal=_DecimalLexicalStyle(
                fraction_scale=fraction_scale,
                exponent_marker=exponent_marker,
                exponent_plus=exponent_plus,
                leading_plus=text.startswith("+"),
                leading_point=mantissa.startswith("."),
            ),
        )
    if old.kind is InternalKind.BINARY_FLOAT64:
        try:
            text = literal.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return _JsonScalarLexicalStyle(
            _ScalarStyleKind.NON_FINITE,
            non_finite=_NonFiniteLexicalStyle(explicit_plus=text.startswith("+")),
        )
    if old.kind is InternalKind.STRING:
        style = analyze_string_style(literal)
        if style is None:
            return None
        return _JsonScalarLexicalStyle(_ScalarStyleKind.STRING, string=style)
    return None


def analyze_string_style(literal: bytes) -> _StringLexicalStyle | None:
    """Per-character string escape choices (edit.rs:1506-1579)."""
    try:
        text = literal.decode("utf-8")
    except UnicodeDecodeError:
        return None
    quote = text[0] if text else ""
    if quote not in ("'", '"') or not text.endswith(quote):
        return None
    style = _StringLexicalStyle(quote=quote, escapes={})
    end = len(text) - len(quote)
    offset = len(quote)
    while offset < end:
        character = text[offset]
        if character != "\\":
            offset += 1
            continue
        escape_start = offset
        offset += 1
        if offset >= end:
            return None
        escaped = text[offset]
        offset += 1
        if escaped == '"':
            decoded = '"'
        elif escaped == "'":
            decoded = "'"
        elif escaped == "\\":
            decoded = "\\"
        elif escaped == "/":
            decoded = "/"
        elif escaped == "b":
            decoded = "\b"
        elif escaped == "f":
            decoded = "\f"
        elif escaped == "n":
            decoded = "\n"
        elif escaped == "r":
            decoded = "\r"
        elif escaped == "t":
            decoded = "\t"
        elif escaped == "v":
            decoded = "\v"
        elif escaped == "0":
            decoded = "\0"
        elif escaped == "x":
            pair = text[offset : offset + 2]
            if len(pair) != 2:
                return None
            try:
                decoded = chr(int(pair, 16))
            except ValueError:
                return None
            offset += 2
        elif escaped == "u":
            first_quad = text[offset : offset + 4]
            if len(first_quad) != 4:
                return None
            try:
                first = int(first_quad, 16)
            except ValueError:
                return None
            offset += 4
            if 0xD800 <= first <= 0xDBFF:
                if text[offset : offset + 2] != "\\u":
                    return None
                second_quad = text[offset + 2 : offset + 6]
                if len(second_quad) != 4:
                    return None
                try:
                    second = int(second_quad, 16)
                except ValueError:
                    return None
                if not 0xDC00 <= second <= 0xDFFF:
                    return None
                offset += 6
                scalar = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
            else:
                scalar = first
            try:
                decoded = chr(scalar)
            except ValueError:
                return None
        elif escaped == "\r":
            if text[offset:].startswith("\n"):
                offset += 1
            decoded = None
        elif escaped in ("\n", "\u2028", "\u2029"):
            decoded = None
        else:
            decoded = escaped
        if decoded is not None:
            style.escapes[decoded] = text[escape_start:offset]
    return style


def render_preserving_style(
    value: PortableValue, style: _JsonScalarLexicalStyle
) -> bytes | None:
    """Renders the new value in the preserved style (edit.rs:1581-1613)."""
    if style.kind is _ScalarStyleKind.NULL and value.kind is Kind.NULL:
        return b"null"
    if style.kind is _ScalarStyleKind.BOOLEAN and value.kind is Kind.BOOLEAN:
        return b"true" if value.as_boolean() else b"false"
    if style.kind is _ScalarStyleKind.INTEGER and value.kind is Kind.INTEGER:
        return render_integer_style(value.as_integer(), style.integer)
    if (
        style.kind is _ScalarStyleKind.DECIMAL
        and value.kind in (Kind.DECIMAL, Kind.INTEGER)
    ):
        return render_decimal_style(value, style.decimal)
    if (
        style.kind is _ScalarStyleKind.NON_FINITE
        and value.kind is Kind.BINARY_FLOAT64
    ):
        return render_non_finite_style(value.as_binary_float64(), style.non_finite)
    if style.kind is _ScalarStyleKind.STRING and value.kind is Kind.STRING:
        return render_string_style(value.as_string(), style.string).encode("utf-8")
    return None


def render_integer_style(value: int, style: _IntegerLexicalStyle) -> bytes | None:
    """Preserving integer rendering (edit.rs:1615-1651)."""
    output = ""
    if value < 0:
        output += "-"
    elif style.explicit_plus:
        output += "+"
    if style.radix is _IntegerRadixKind.DECIMAL:
        output += str(abs(value))
    else:
        output += "0X" if style.uppercase_prefix else "0x"
        magnitude = abs(value)
        if magnitude == 0:
            output += "0"
        else:
            octets = magnitude.to_bytes((magnitude.bit_length() + 7) // 8, "big")
            for index, octet in enumerate(octets):
                if style.uppercase_digits:
                    output += f"{octet:X}" if index == 0 else f"{octet:02X}"
                else:
                    output += f"{octet:x}" if index == 0 else f"{octet:02x}"
    return output.encode("ascii")


def render_decimal_style(
    value: PortableValue, style: _DecimalLexicalStyle
) -> bytes | None:
    """Preserving decimal rendering (edit.rs:1653-1702)."""
    if value.kind is Kind.DECIMAL:
        coefficient = value.as_decimal().coefficient
        exponent = value.as_decimal().exponent
    elif value.kind is Kind.INTEGER:
        coefficient = value.as_integer()
        exponent = 0
    else:
        return None
    if style.exponent_marker is not None:
        scale = style.fraction_scale or 0
        if style.fraction_scale is not None:
            mantissa = decimal_fixed_text(coefficient, scale)
        else:
            mantissa = str(coefficient)
        if style.leading_point:
            mantissa = remove_leading_zero(mantissa)
        if exponent is None:
            return None
        total = exponent + scale
        if total < -(2**63) or total >= 2**63:
            return None
        mantissa += style.exponent_marker
        if total >= 0 and style.exponent_plus:
            mantissa += "+"
        mantissa += str(total)
        output = mantissa
    else:
        if style.fraction_scale is None:
            return None
        scale = style.fraction_scale
        if exponent >= 0:
            shift = exponent + scale
        else:
            shift = scale - (-exponent)
            if shift < 0:
                return None
        if shift > MAX_PRESERVED_FRACTION_DIGITS:
            return None
        mantissa = coefficient * (10**shift)
        output = decimal_fixed_text(mantissa, scale)
        if style.leading_point:
            output = remove_leading_zero(output)
    if style.leading_plus and not output.startswith("-"):
        output = "+" + output
    return output.encode("ascii")


def remove_leading_zero(text: str) -> str:
    """Drops the leading zero of a fixed fraction (edit.rs:1704-1709)."""
    zero = 1 if text.startswith("-0.") else 0
    if text[zero : zero + 2] != "0.":
        raise ValueError("no leading zero")
    return text[:zero] + text[zero + 1 :]


def render_non_finite_style(
    bits: int, style: _NonFiniteLexicalStyle
) -> bytes | None:
    """Preserving non-finite rendering (edit.rs:1711-1725)."""
    if bits == 0x7FF0000000000000:
        text = "+Infinity" if style.explicit_plus else "Infinity"
    elif bits == 0xFFF0000000000000:
        text = "-Infinity"
    elif bits == 0x7FF8000000000000:
        text = "+NaN" if style.explicit_plus else "NaN"
    elif bits == 0xFFF8000000000000:
        text = "-NaN"
    else:
        return None
    return text.encode("ascii")


def decimal_fixed_text(mantissa: int, scale: int) -> str:
    """Fixed-fraction rendering (edit.rs:1727-1739)."""
    text = str(mantissa)
    if text.startswith("-"):
        sign, digits = "-", text[1:]
    else:
        sign, digits = "", text
    if len(digits) <= scale:
        return f"{sign}0.{'0' * (scale - len(digits))}{digits}"
    split = len(digits) - scale
    return f"{sign}{digits[:split]}.{digits[split:]}"


def render_string_style(value: str, style: _StringLexicalStyle) -> str:
    """Preserving string rendering (edit.rs:1741-1753)."""
    output = style.quote
    for character in value:
        if character in style.escapes:
            output += style.escapes[character]
        else:
            output += push_json_string_char(character, style.quote, False)
    return output + style.quote


def portable_json_kind(value: PortableValue, profile: JsonProfile) -> str | None:
    """Core kind admitted as a JSON scalar (edit.rs:1755-1767)."""
    if value.kind is Kind.NULL:
        return "Null"
    if value.kind is Kind.BOOLEAN:
        return "Boolean"
    if value.kind is Kind.INTEGER:
        return "Integer"
    if value.kind is Kind.DECIMAL:
        return "Decimal"
    if value.kind is Kind.BINARY_FLOAT64 and profile.is_json5():
        return "BinaryFloat64"
    if value.kind is Kind.STRING:
        return "String"
    return None


def canonical_literal(value: PortableValue, profile: JsonProfile) -> bytes:
    """Deterministic profile-canonical JSON literal (edit.rs:1769-1795)."""
    if value.kind is Kind.NULL:
        text = "null"
    elif value.kind is Kind.BOOLEAN:
        text = "true" if value.as_boolean() else "false"
    elif value.kind is Kind.INTEGER:
        text = str(value.as_integer())
    elif value.kind is Kind.DECIMAL:
        decimal = value.as_decimal()
        text = f"{decimal.coefficient}e{decimal.exponent}"
    elif value.kind is Kind.BINARY_FLOAT64 and profile.is_json5():
        rendered = render_non_finite_style(
            value.as_binary_float64(), _NonFiniteLexicalStyle(explicit_plus=False)
        )
        if rendered is None:
            raise JsonEditFailure(
                JsonEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, detail="BinaryFloat64"
            )
        return rendered
    elif value.kind is Kind.STRING:
        text = encode_json_string(value.as_string(), profile.is_json5())
    else:
        raise JsonEditFailure(
            JsonEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, detail=value.kind.value
        )
    return text.encode("utf-8")


def encode_json_string(value: str, json5: bool) -> str:
    """Canonical double-quoted string with UPPERCASE \\uXXXX escapes
    (edit.rs:1797-1805)."""
    output = '"'
    for character in value:
        output += push_json_string_char(character, '"', json5)
    return output + '"'


def push_json_string_char(
    character: str, quote: str, canonical_json5: bool
) -> str:
    """One escaped string character (edit.rs:1807-1829)."""
    if character == quote:
        return "\\" + character
    if character == "\\":
        return "\\\\"
    if character == "\b":
        return "\\b"
    if character == "\f":
        return "\\f"
    if character == "\n":
        return "\\n"
    if character == "\r":
        return "\\r"
    if character == "\t":
        return "\\t"
    if "\u0000" <= character <= "\u001f":
        return f"\\u{ord(character):04X}"
    if character in ("\u2028", "\u2029") and canonical_json5:
        return f"\\u{ord(character):04X}"
    return character


def validate_literal(literal: bytes, profile: JsonProfile, limits: ParseLimits) -> str:
    """Requires one complete legal scalar literal for the profile
    (edit.rs:1831-1862)."""
    if not literal:
        raise JsonEditFailure(JsonEditFailureKind.INVALID_LITERAL)
    try:
        text = literal.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonEditFailure(JsonEditFailureKind.INVALID_LITERAL) from None
    try:
        document = parse(literal, profile, limits)
    except Exception:
        raise JsonEditFailure(JsonEditFailureKind.INVALID_LITERAL) from None
    root = document.root()
    availability = root.kind()
    if (
        document.formation_status().value != "Complete"
        or root.span().start_byte != 0
        or root.span().end_byte != len(literal)
        or not availability.is_available
    ):
        raise JsonEditFailure(JsonEditFailureKind.INVALID_LITERAL)
    return availability.value.value


def find_value_by_literal_span(
    document: JsonDocument, start: int, end: int
) -> int | None:
    """Locates the uniquely reparsed literal (edit.rs:1864-1882)."""
    matches = []
    for index, entity in enumerate(document.entities):
        if (
            isinstance(entity, ValueEntity)
            and entity.literal_span is not None
            and entity.literal_span.start_byte == start
            and entity.literal_span.end_byte == end
        ):
            matches.append(index)
    if len(matches) == 1:
        return matches[0]
    return None
