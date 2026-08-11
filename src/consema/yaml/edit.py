"""YAML edit transactions: scalar, structural, and anchor operations.

Authority (Rust arbitration for exact byte semantics):

- Operation and policy model: crates/consema-yaml/src/edit.rs:21-53
  (RepresentationPolicy, ScalarReplacement), 63-114 (EditOperation),
  116-258 (EditTransaction/Builder).
- Failure algebra and codes: edit.rs:275-343 (EditFailure and the code
  mapping).
- Atomic commit: edit.rs:401-551 — WrongSnapshot gate (edit.rs:404-406),
  dependency validation (edit.rs:407, 1974-2014), prepared-edit ownership
  conflicts (edit.rs:2454-2467), bounded target length (edit.rs:417-428),
  rendering and reparse (edit.rs:429-441), candidate validation
  (edit.rs:442, 1682-1764), ChangeSet source edits and node mappings
  (edit.rs:444-523), SourcePatch derivation (edit.rs:531-538),
  UntouchedByteProof (edit.rs:539-544). Dry-run produces the identical
  patch and target digest (edit.rs:553-568; RFC 0004 s14).
- Scalar preparation: edit.rs:603-706 (canonical_scalar_fragment via the
  canonical-flow value materializer edit.rs:1569-1614), the anchor-safe
  rules (RFC 0007 s12: a scalar edit of an anchored node changes the shared
  graph node; aliases are not rewritten).
- Structural preparation: edit.rs:742-920 (insertion fragments
  ``? {key} : {value}`` and block lines, placement and comma ownership
  edit.rs:1053-1158, removal spans edit.rs:1160-1200, block-owned spans
  edit.rs:1226-1344), anchor visibility edit.rs:1346-1396.
- Anchor-dependency validation: edit.rs:1398-1442 — ``collect_owned_nodes``
  collects only the deleted subtrees (never crossing alias edges), then any
  alias outside the removed span whose target was collected fails with
  yaml.edit.anchor-dependency@1.
- Candidate isomorphism: edit.rs:1766-1947 (structural ValidationModel) and
  2017-2324 (ValidationModel/compare).
- The v1 vector goldens this module must reproduce byte-for-byte:
  conformance/vectors/yaml-v1.json edit.scalar-atomic, edit.anchor-rename,
  edit.structural-insert, edit.anchor-dependency.

Frozen operation ids (crates/consema-yaml/src/operation_registry.rs:16-82):
yaml.edit.insert-alias@1, insert-mapping-entry@1, insert-sequence-element@1,
remove-mapping-entry@1, remove-sequence-element@1, rename-anchor@1,
replace-scalar-literal@1, replace-scalar-semantic@1.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import Kind, PortableValue
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
from consema.document.materialization import (
    FailedMaterializationAttempt,
    MaterializationLimits,
    MaterializationRequest,
    MaterializationStyleId,
)
from consema.document.source import SourceEncoding, SourceEncodingKind, SourceLimits
from consema.document.source_patch import SourcePatch, SourcePatchLimits
from consema.document.structural import (
    AssociationPlacement,
    NodeRef,
    NodeRole,
    Span,
)
from consema.document.untouched_proof import UntouchedByteProof
from consema.yaml.document import Document, YamlNode
from consema.yaml.errors import (
    YamlDiagnostic,
    YamlEditFailure,
    YamlEditFailureKind,
    YamlSeverity,
)
from consema.yaml.kinds import (
    YamlProfile,
    YamlScalarKind,
    YamlScalarStyle,
    YamlSyntaxKind,
)
from consema.yaml.materialization import (
    YAML_CANONICAL_FLOW_STYLE,
    materialize_value,
)
from consema.yaml.parser import (
    NativeMappingEntry,
    NativeScalar,
    NativeSequenceItem,
    parse,
)
from consema.protocol.error_registry import DiagnosticCategory


class RepresentationPolicy(enum.Enum):
    """Explicit semantic scalar representation policy (edit.rs:21-32)."""

    EXACT_LITERAL = "ExactLiteral"
    PRESERVE_COMPATIBLE = "PreserveCompatible"
    CANONICAL_FOR_PROFILE = "CanonicalForProfile"
    PRESERVE_ELSE_CANONICAL = "PreserveElseCanonical"


class ScalarReplacementKind(enum.Enum):
    """Scalar operation kind (edit.rs:34-53)."""

    SEMANTIC = "Semantic"
    LITERAL = "Literal"


@dataclass(frozen=True, slots=True)
class ScalarReplacement:
    """One scalar operation bound to the transaction base snapshot
    (edit.rs:34-53)."""

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
    """Typed edit operation kinds (edit.rs:63-114)."""

    REPLACE_SCALAR = "ReplaceScalar"
    RENAME_ANCHOR = "RenameAnchor"
    INSERT_MAPPING_ENTRY = "InsertMappingEntry"
    REMOVE_MAPPING_ENTRY = "RemoveMappingEntry"
    INSERT_SEQUENCE_ELEMENT = "InsertSequenceElement"
    REMOVE_SEQUENCE_ELEMENT = "RemoveSequenceElement"
    INSERT_ALIAS = "InsertAlias"


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One typed YAML edit operation bound to one immutable base snapshot
    (edit.rs:63-114)."""

    kind: EditOperationKind
    scalar: ScalarReplacement | None = None
    target: NodeRef | None = None
    name: str | None = None
    mapping: NodeRef | None = None
    sequence: NodeRef | None = None
    anchor: NodeRef | None = None
    key: PortableValue | None = None
    value: PortableValue | None = None
    placement: AssociationPlacement | None = None


@dataclass(frozen=True, slots=True)
class EditTransaction:
    """Immutable transaction; every operation resolves against one base
    snapshot (edit.rs:116-135)."""

    base: object
    operations: tuple[EditOperation, ...] = ()


class EditTransactionBuilder:
    """Builder that is not a committed edit (edit.rs:137-258)."""

    def __init__(self, document: Document) -> None:
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

    def rename_anchor(self, target: NodeRef, name: str) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.RENAME_ANCHOR, target=target, name=name)
        )
        return self

    def insert_mapping_entry(
        self,
        mapping: NodeRef,
        key: PortableValue,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_MAPPING_ENTRY,
                mapping=mapping,
                key=key,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_mapping_entry(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_MAPPING_ENTRY, target=target)
        )
        return self

    def insert_sequence_element(
        self,
        sequence: NodeRef,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_SEQUENCE_ELEMENT,
                sequence=sequence,
                value=value,
                placement=placement,
            )
        )
        return self

    def remove_sequence_element(self, target: NodeRef) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(kind=EditOperationKind.REMOVE_SEQUENCE_ELEMENT, target=target)
        )
        return self

    def insert_alias(
        self,
        sequence: NodeRef,
        anchor: NodeRef,
        placement: AssociationPlacement,
    ) -> EditTransactionBuilder:
        self._operations.append(
            EditOperation(
                kind=EditOperationKind.INSERT_ALIAS,
                sequence=sequence,
                anchor=anchor,
                placement=placement,
            )
        )
        return self

    def build(self) -> EditTransaction:
        return EditTransaction(base=self._base, operations=tuple(self._operations))


@dataclass(frozen=True, slots=True)
class EditCommit:
    """Atomic edit success (edit.rs:260-271)."""

    document: Document
    change_set: ChangeSet
    source_patch: SourcePatch
    untouched_proof: UntouchedByteProof


# -- internal preparation records --------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    old_span: Span
    replacement: bytes
    mapping: tuple[NodeRef, object] | None = None


class _MappingPlanKind(enum.Enum):
    NODE = "Node"
    ANCHOR = "Anchor"
    ALIAS = "Alias"
    REMOVED = "Removed"


@dataclass(frozen=True, slots=True)
class _MappingPlan:
    kind: _MappingPlanKind
    index: int | None = None


@dataclass(frozen=True, slots=True)
class _CanonicalScalar:
    tag: str
    literal: str
    canonical: str


class _EditPlanner:
    """One planner bound to the base document (mirror of the Rust
    Document::prepare_* methods, edit.rs:570-2014)."""

    def __init__(self, document: Document) -> None:
        self.document = document

    # -- resolution ---------------------------------------------------------

    def resolve_node(self, target: NodeRef, role: NodeRole) -> int:
        if target.snapshot != self.document.snapshot_identity():
            raise YamlEditFailure(YamlEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not role:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        index = target.index
        if index < 0 or index >= len(self.document.native.nodes):
            raise YamlEditFailure(YamlEditFailureKind.TARGET_NOT_FOUND)
        if role is NodeRole.YAML_ANCHOR_DEFINITION:
            if self.document.native.nodes[index].anchor is None:
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        return index

    def resolve_mapping_entry(self, target: NodeRef) -> tuple[int, int]:
        if target.snapshot != self.document.snapshot_identity():
            raise YamlEditFailure(YamlEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.YAML_MAPPING_ENTRY:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        identity = target.index
        for container, node in enumerate(self.document.native.nodes):
            content = node.content
            if isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry):
                for ordinal, entry in enumerate(content):
                    if entry.identity == identity:
                        return (container, ordinal)
        raise YamlEditFailure(YamlEditFailureKind.TARGET_NOT_FOUND)

    def resolve_sequence_item(self, target: NodeRef) -> tuple[int, int]:
        if target.snapshot != self.document.snapshot_identity():
            raise YamlEditFailure(YamlEditFailureKind.WRONG_SNAPSHOT)
        if target.role is not NodeRole.YAML_SEQUENCE_ELEMENT:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        identity = target.index
        for container, node in enumerate(self.document.native.nodes):
            content = node.content
            if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
                for ordinal, item in enumerate(content):
                    if item.identity == identity:
                        return (container, ordinal)
        raise YamlEditFailure(YamlEditFailureKind.TARGET_NOT_FOUND)

    # -- operation preparation ----------------------------------------------

    def prepare_operation(
        self, operation: EditOperation, diagnostics: list[YamlDiagnostic]
    ) -> list[_PreparedEdit]:
        kind = operation.kind
        if kind is EditOperationKind.REPLACE_SCALAR:
            return self.prepare_scalar(operation.scalar, diagnostics)
        if kind is EditOperationKind.RENAME_ANCHOR:
            return self.prepare_anchor_rename(operation.target, operation.name)
        if kind is EditOperationKind.INSERT_MAPPING_ENTRY:
            return self.prepare_mapping_insertion(
                operation.mapping, operation.key, operation.value, operation.placement
            )
        if kind is EditOperationKind.REMOVE_MAPPING_ENTRY:
            return self.prepare_mapping_removal(operation.target)
        if kind is EditOperationKind.INSERT_SEQUENCE_ELEMENT:
            return self.prepare_sequence_insertion(
                operation.sequence, operation.value, operation.placement
            )
        if kind is EditOperationKind.REMOVE_SEQUENCE_ELEMENT:
            return self.prepare_sequence_removal(operation.target)
        return self.prepare_alias_insertion(
            operation.sequence, operation.anchor, operation.placement
        )

    def prepare_scalar(
        self, operation: ScalarReplacement, diagnostics: list[YamlDiagnostic]
    ) -> list[_PreparedEdit]:
        index = self.resolve_node(operation.target, NodeRole.YAML_NODE)
        node = self.document.native.nodes[index]
        scalar = node.content
        if not isinstance(scalar, NativeScalar):
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        literal_span = self.scalar_literal_span(index)
        if literal_span is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        if operation.kind is ScalarReplacementKind.LITERAL:
            self.validate_literal(operation.literal)
            return [
                _PreparedEdit(
                    old_span=literal_span,
                    replacement=bytes(operation.literal),
                    mapping=(operation.target, _MappingPlan(_MappingPlanKind.NODE, index)),
                )
            ]
        value = operation.value
        policy = operation.policy
        if not _is_scalar_value(value.kind):
            raise YamlEditFailure(
                YamlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=value.kind
            )
        if policy is RepresentationPolicy.EXACT_LITERAL:
            raise YamlEditFailure(YamlEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION)
        canonical = self.canonical_scalar_fragment(value)

        def preserve():
            return _preserved_literal(
                scalar.kind,
                scalar.style,
                node.tag,
                self.tag_span(index) is not None,
                canonical,
                value.kind,
                self.document.profile,
            )

        if policy is RepresentationPolicy.PRESERVE_COMPATIBLE:
            text = preserve()
            if text is None:
                raise YamlEditFailure(YamlEditFailureKind.REPRESENTATION_INCOMPATIBLE)
            return [
                _PreparedEdit(
                    old_span=literal_span,
                    replacement=self.encode_fragment(text),
                    mapping=(operation.target, _MappingPlan(_MappingPlanKind.NODE, index)),
                )
            ]
        if policy is RepresentationPolicy.CANONICAL_FOR_PROFILE:
            return self.canonical_scalar_edits(index, operation.target, literal_span, canonical)
        # PreserveElseCanonical.
        text = preserve()
        if text is not None:
            return [
                _PreparedEdit(
                    old_span=literal_span,
                    replacement=self.encode_fragment(text),
                    mapping=(operation.target, _MappingPlan(_MappingPlanKind.NODE, index)),
                )
            ]
        diagnostics.append(
            YamlDiagnostic(
                code="yaml.edit.canonical-fallback@1",
                category=DiagnosticCategory.EDIT,
                severity=YamlSeverity.INFO,
                primary=literal_span,
                occurrence=len(diagnostics),
            )
        )
        return self.canonical_scalar_edits(index, operation.target, literal_span, canonical)

    def canonical_scalar_edits(
        self, index: int, target: NodeRef, literal_span: Span, canonical: _CanonicalScalar
    ) -> list[_PreparedEdit]:
        edits: list[_PreparedEdit] = []
        tag_span = self.tag_span(index)
        if tag_span is not None:
            edits.append(
                _PreparedEdit(
                    old_span=tag_span,
                    replacement=self.encode_fragment(canonical.tag),
                    mapping=None,
                )
            )
            edits.append(
                _PreparedEdit(
                    old_span=literal_span,
                    replacement=self.encode_fragment(canonical.literal),
                    mapping=(target, _MappingPlan(_MappingPlanKind.NODE, index)),
                )
            )
        else:
            edits.append(
                _PreparedEdit(
                    old_span=literal_span,
                    replacement=self.encode_fragment(f"{canonical.tag} {canonical.literal}"),
                    mapping=(target, _MappingPlan(_MappingPlanKind.NODE, index)),
                )
            )
        return edits

    def prepare_anchor_rename(self, target: NodeRef, name: str) -> list[_PreparedEdit]:
        index = self.resolve_node(target, NodeRole.YAML_ANCHOR_DEFINITION)
        self.validate_anchor_name(name)
        node = self.document.native.nodes[index]
        if node.anchor is None:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        old_name = node.anchor
        if node.anchor_span is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        edits: list[_PreparedEdit] = [
            _PreparedEdit(
                old_span=node.anchor_span,
                replacement=self.encode_fragment(f"&{name}"),
                mapping=(target, _MappingPlan(_MappingPlanKind.ANCHOR, index)),
            )
        ]
        for ordinal, alias in enumerate(self.document.native.aliases):
            if alias.target == index and alias.name == old_name:
                edits.append(
                    _PreparedEdit(
                        old_span=alias.span,
                        replacement=self.encode_fragment(f"*{name}"),
                        mapping=(
                            self.document.authority.node_ref(alias.identity, NodeRole.YAML_ALIAS),
                            _MappingPlan(_MappingPlanKind.ALIAS, ordinal),
                        ),
                    )
                )
        return edits

    def prepare_mapping_insertion(
        self,
        mapping: NodeRef,
        key: PortableValue,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> list[_PreparedEdit]:
        index = self.resolve_node(mapping, NodeRole.YAML_NODE)
        content = self.document.native.nodes[index].content
        if not (isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry)):
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        entries = content
        ordinal = self.mapping_placement(index, entries, placement)
        key_fragment = self.canonical_value_fragment(key)
        value_fragment = self.canonical_value_fragment(value)
        fragment = f"? {key_fragment} : {value_fragment}"
        block_lines = [f"? {key_fragment}", f": {value_fragment}"]
        old_span, replacement = self.prepare_collection_insertion(
            index,
            [self.association_span(entry.span) for entry in entries],
            ordinal,
            fragment,
            block_lines,
            YamlSyntaxKind.FLOW_MAPPING_START,
            YamlSyntaxKind.FLOW_MAPPING_END,
        )
        return [
            _PreparedEdit(
                old_span=old_span,
                replacement=replacement,
                mapping=(mapping, _MappingPlan(_MappingPlanKind.NODE, index)),
            )
        ]

    def prepare_sequence_insertion(
        self,
        sequence: NodeRef,
        value: PortableValue,
        placement: AssociationPlacement,
    ) -> list[_PreparedEdit]:
        index = self.resolve_node(sequence, NodeRole.YAML_NODE)
        content = self.document.native.nodes[index].content
        if not (isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem)):
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        items = content
        ordinal = self.sequence_placement(index, items, placement)
        fragment = self.canonical_value_fragment(value)
        old_span, replacement = self.prepare_collection_insertion(
            index,
            [self.association_span(item.span) for item in items],
            ordinal,
            fragment,
            [f"- {fragment}"],
            YamlSyntaxKind.FLOW_SEQUENCE_START,
            YamlSyntaxKind.FLOW_SEQUENCE_END,
        )
        return [
            _PreparedEdit(
                old_span=old_span,
                replacement=replacement,
                mapping=(sequence, _MappingPlan(_MappingPlanKind.NODE, index)),
            )
        ]

    def prepare_alias_insertion(
        self,
        sequence: NodeRef,
        anchor: NodeRef,
        placement: AssociationPlacement,
    ) -> list[_PreparedEdit]:
        sequence_index = self.resolve_node(sequence, NodeRole.YAML_NODE)
        anchor_index = self.resolve_node(anchor, NodeRole.YAML_ANCHOR_DEFINITION)
        content = self.document.native.nodes[sequence_index].content
        if not (isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem)):
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        items = content
        ordinal = self.sequence_placement(sequence_index, items, placement)
        spans = [self.association_span(item.span) for item in items]
        insertion = self.collection_insertion_point(
            sequence_index,
            spans,
            ordinal,
            YamlSyntaxKind.FLOW_SEQUENCE_START,
            YamlSyntaxKind.FLOW_SEQUENCE_END,
        )
        self.validate_visible_anchor(sequence_index, anchor_index, insertion)
        anchor_node = self.document.native.nodes[anchor_index]
        if anchor_node.anchor is None:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        name = anchor_node.anchor
        old_span, replacement = self.prepare_collection_insertion_at(
            sequence_index,
            spans,
            ordinal,
            f"*{name}",
            [f"- *{name}"],
            YamlSyntaxKind.FLOW_SEQUENCE_START,
            YamlSyntaxKind.FLOW_SEQUENCE_END,
            insertion,
        )
        return [
            _PreparedEdit(
                old_span=old_span,
                replacement=replacement,
                mapping=(sequence, _MappingPlan(_MappingPlanKind.NODE, sequence_index)),
            )
        ]

    def prepare_mapping_removal(self, target: NodeRef) -> list[_PreparedEdit]:
        container, ordinal = self.resolve_mapping_entry(target)
        content = self.document.native.nodes[container].content
        entries = content
        spans = [self.association_span(entry.span) for entry in entries]
        owned = self.collection_removal_span(
            container,
            spans,
            ordinal,
            YamlSyntaxKind.FLOW_MAPPING_START,
            YamlSyntaxKind.FLOW_MAPPING_END,
        )
        self.validate_removal_dependencies(
            owned,
            [(entries[ordinal].key, entries[ordinal].key_alias),
             (entries[ordinal].value, entries[ordinal].value_alias)],
        )
        replacement = b""
        if (
            len(entries) == 1
            and not self.collection_is_flow(container, YamlSyntaxKind.FLOW_MAPPING_START)
        ):
            replacement = self.empty_block_replacement(owned, spans[ordinal], "{}")
        return [
            _PreparedEdit(
                old_span=owned,
                replacement=replacement,
                mapping=(target, _MappingPlan(_MappingPlanKind.REMOVED)),
            )
        ]

    def prepare_sequence_removal(self, target: NodeRef) -> list[_PreparedEdit]:
        container, ordinal = self.resolve_sequence_item(target)
        content = self.document.native.nodes[container].content
        items = content
        spans = [self.association_span(item.span) for item in items]
        owned = self.collection_removal_span(
            container,
            spans,
            ordinal,
            YamlSyntaxKind.FLOW_SEQUENCE_START,
            YamlSyntaxKind.FLOW_SEQUENCE_END,
        )
        self.validate_removal_dependencies(
            owned, [(items[ordinal].node, items[ordinal].alias)]
        )
        replacement = b""
        if (
            len(items) == 1
            and not self.collection_is_flow(container, YamlSyntaxKind.FLOW_SEQUENCE_START)
        ):
            replacement = self.empty_block_replacement(owned, spans[ordinal], "[]")
        return [
            _PreparedEdit(
                old_span=owned,
                replacement=replacement,
                mapping=(target, _MappingPlan(_MappingPlanKind.REMOVED)),
            )
        ]

    # -- placement and span helpers -----------------------------------------

    def mapping_placement(
        self, expected: int, entries, placement: AssociationPlacement
    ) -> int:
        if placement.kind == "Start":
            return 0
        if placement.kind == "End":
            return len(entries)
        container, ordinal = self.resolve_mapping_entry(placement.anchor)
        if container != expected:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_PLACEMENT)
        return ordinal + 1 if placement.kind == "After" else ordinal

    def sequence_placement(
        self, expected: int, items, placement: AssociationPlacement
    ) -> int:
        if placement.kind == "Start":
            return 0
        if placement.kind == "End":
            return len(items)
        container, ordinal = self.resolve_sequence_item(placement.anchor)
        if container != expected:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_PLACEMENT)
        return ordinal + 1 if placement.kind == "After" else ordinal

    def association_span(self, span: Span) -> Span:
        """Expands an association span backwards over tag/anchor/explicit-key
        pieces (edit.rs:1018-1051)."""
        pieces = self.document.structural_index.pieces
        kinds = self.document.syntax_kinds
        start = span.start_byte
        while True:
            index = None
            for piece_index, piece in enumerate(pieces):
                if piece.span.end_byte == start:
                    index = piece_index
            if index is None:
                break
            kind = kinds[index]
            if kind in (YamlSyntaxKind.TAG, YamlSyntaxKind.ANCHOR, YamlSyntaxKind.EXPLICIT_KEY):
                start = pieces[index].span.start_byte
                continue
            if kind is not YamlSyntaxKind.WHITESPACE or index == 0:
                break
            property_index = index - 1
            if (
                pieces[property_index].span.end_byte == pieces[index].span.start_byte
                and kinds[property_index]
                in (YamlSyntaxKind.TAG, YamlSyntaxKind.ANCHOR, YamlSyntaxKind.EXPLICIT_KEY)
            ):
                start = pieces[property_index].span.start_byte
                continue
            break
        return self.document.authority.span(start, span.end_byte)

    def prepare_collection_insertion(
        self,
        container: int,
        spans: list[Span],
        ordinal: int,
        flow_fragment: str,
        block_lines: list[str],
        flow_start: YamlSyntaxKind,
        flow_end: YamlSyntaxKind,
    ) -> tuple[Span, bytes]:
        insertion = self.collection_insertion_point(container, spans, ordinal, flow_start, flow_end)
        return self.prepare_collection_insertion_at(
            container, spans, ordinal, flow_fragment, block_lines, flow_start, flow_end, insertion
        )

    def collection_insertion_point(
        self,
        container: int,
        spans: list[Span],
        ordinal: int,
        flow_start: YamlSyntaxKind,
        flow_end: YamlSyntaxKind,
    ) -> int:
        if ordinal > len(spans):
            raise YamlEditFailure(YamlEditFailureKind.INVALID_PLACEMENT)
        if self.collection_is_flow(container, flow_start):
            if ordinal < len(spans):
                return spans[ordinal].start_byte
            if spans:
                return spans[-1].end_byte
            end_piece = self.syntax_within(
                self.document.native.nodes[container].span, flow_end, True
            )
            if end_piece is None:
                raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
            return end_piece.start_byte
        if ordinal < len(spans):
            return self.block_owned_span(spans[ordinal]).start_byte
        if spans:
            return self.block_owned_span(spans[-1]).end_byte
        raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)

    def prepare_collection_insertion_at(
        self,
        container: int,
        spans: list[Span],
        ordinal: int,
        flow_fragment: str,
        block_lines: list[str],
        flow_start: YamlSyntaxKind,
        flow_end: YamlSyntaxKind,
        insertion: int,
    ) -> tuple[Span, bytes]:
        span = self.document.authority.span(insertion, insertion)
        if self.collection_is_flow(container, flow_start):
            if not spans:
                text = flow_fragment
            elif ordinal < len(spans):
                text = f"{flow_fragment}, "
            else:
                text = f", {flow_fragment}"
            return (span, self.encode_fragment(text))
        reference = spans[ordinal] if ordinal < len(spans) else (spans[-1] if spans else None)
        if reference is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        owned = self.block_owned_span(reference)
        indent = self.line_indent(owned.start_byte)
        newline = self.nearest_newline(insertion)
        suffix_newline = ordinal < len(spans) or self.raw_decoded(
            owned.start_byte, owned.end_byte
        ).endswith(("\r", "\n"))
        text = ""
        if ordinal == len(spans) and not suffix_newline:
            text += newline
        for index, line in enumerate(block_lines):
            text += indent
            text += line
            if index + 1 < len(block_lines) or suffix_newline:
                text += newline
        return (span, self.encode_fragment(text))

    def collection_removal_span(
        self,
        container: int,
        spans: list[Span],
        ordinal: int,
        flow_start: YamlSyntaxKind,
        flow_end: YamlSyntaxKind,
    ) -> Span:
        if ordinal >= len(spans):
            raise YamlEditFailure(YamlEditFailureKind.TARGET_NOT_FOUND)
        target = spans[ordinal]
        if not self.collection_is_flow(container, flow_start):
            return self.block_owned_span(target)
        if len(spans) == 1:
            return target
        if ordinal + 1 < len(spans):
            comma = self.syntax_between(
                YamlSyntaxKind.FLOW_ENTRY, target.end_byte, spans[ordinal + 1].start_byte, False
            )
            if comma is None:
                raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
            return self.document.authority.span(target.start_byte, spans[ordinal + 1].start_byte)
        comma = self.syntax_between(
            YamlSyntaxKind.FLOW_ENTRY, spans[ordinal - 1].end_byte, target.start_byte, True
        )
        if comma is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        return self.document.authority.span(comma.start_byte, target.end_byte)

    def collection_is_flow(self, container: int, flow_start: YamlSyntaxKind) -> bool:
        node_span = self.document.native.nodes[container].span
        pieces = self.document.structural_index.pieces
        kinds = self.document.syntax_kinds
        for piece, kind in zip(pieces, kinds):
            if piece.span.start_byte < node_span.start_byte or piece.span.end_byte > node_span.end_byte:
                continue
            if kind not in (
                YamlSyntaxKind.WHITESPACE,
                YamlSyntaxKind.NEWLINE,
                YamlSyntaxKind.COMMENT,
                YamlSyntaxKind.TAG,
                YamlSyntaxKind.ANCHOR,
            ):
                return kind is flow_start
        return False

    def block_owned_span(self, occurrence: Span) -> Span:
        start = self.line_start(occurrence.start_byte)
        if self.line_start(occurrence.end_byte) == occurrence.end_byte and occurrence.end_byte > start:
            end = occurrence.end_byte
        else:
            end = self.line_end(occurrence.end_byte)
        return self.document.authority.span(start, end)

    def line_start(self, raw: int) -> int:
        position = self.document.source.decoded_position(raw)
        text = self.document.source.decoded_text()
        if text is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        prefix = text[: position.decoded_utf8_byte]
        offset = prefix.rfind("\r")
        offset = max(offset, prefix.rfind("\n"))
        start = offset + 1
        return self.document.source.raw_byte_at(
            _utf8_offset(start)
        )

    def line_end(self, raw: int) -> int:
        position = self.document.source.decoded_position(raw)
        text = self.document.source.decoded_text()
        if text is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        suffix = text[position.decoded_utf8_byte :]
        end = position.decoded_utf8_byte
        for index, character in enumerate(suffix):
            if character in ("\r", "\n"):
                end = position.decoded_utf8_byte + index
                break
        else:
            end = len(text)
        if end < len(text):
            if text[end] == "\r" and end + 1 < len(text) and text[end + 1] == "\n":
                end += 2
            else:
                end += 1
        return self.document.source.raw_byte_at(_utf8_offset(end))

    def line_indent(self, raw_line_start: int) -> str:
        end = self.line_end(raw_line_start)
        text = self.raw_decoded(raw_line_start, end)
        count = 0
        for character in text:
            if character != " ":
                break
            count += 1
        return " " * count

    def raw_decoded(self, start: int, end: int) -> str:
        start_position = self.document.source.decoded_position(start)
        end_position = self.document.source.decoded_position(end)
        text = self.document.source.decoded_text()
        if text is None:
            raise YamlEditFailure(YamlEditFailureKind.INCOMPLETE_TARGET)
        return text[start_position.decoded_utf8_byte : end_position.decoded_utf8_byte]

    def nearest_newline(self, raw: int) -> str:
        pieces = self.document.structural_index.pieces
        kinds = self.document.syntax_kinds
        best = None
        best_distance = None
        for piece, kind in zip(pieces, kinds):
            if kind is YamlSyntaxKind.NEWLINE:
                distance = abs(piece.span.start_byte - raw)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best = piece.span
        if best is None:
            return "\n"
        return self.raw_decoded(best.start_byte, best.end_byte)

    def empty_block_replacement(self, owned: Span, occurrence: Span, empty: str) -> bytes:
        indent = self.line_indent(owned.start_byte)
        whole = self.raw_decoded(owned.start_byte, owned.end_byte)
        if occurrence.end_byte < owned.end_byte:
            tail = self.raw_decoded(occurrence.end_byte, owned.end_byte)
        elif whole.endswith("\r\n"):
            tail = "\r\n"
        elif whole.endswith("\n"):
            tail = "\n"
        elif whole.endswith("\r"):
            tail = "\r"
        else:
            tail = ""
        return self.encode_fragment(f"{indent}{empty}{tail}")

    # -- anchor safety (edit.rs:1346-1442) ----------------------------------

    def validate_visible_anchor(self, sequence: int, anchor: int, insertion: int) -> None:
        anchor_node = self.document.native.nodes[anchor]
        if anchor_node.anchor_span is None or anchor_node.anchor is None:
            raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
        anchor_span = anchor_node.anchor_span
        sequence_span = self.document.native.nodes[sequence].span
        document_record = None
        for record in self.document.native.documents:
            if record.span.start_byte <= sequence_span.start_byte and sequence_span.end_byte <= record.span.end_byte:
                document_record = record
                break
        if document_record is None:
            raise YamlEditFailure(YamlEditFailureKind.ANCHOR_NOT_VISIBLE)
        if (
            anchor_span.end_byte > insertion
            or anchor_span.start_byte < document_record.span.start_byte
            or anchor_span.end_byte > document_record.span.end_byte
        ):
            raise YamlEditFailure(YamlEditFailureKind.ANCHOR_NOT_VISIBLE)
        name = anchor_node.anchor
        visible = None
        for index, node in enumerate(self.document.native.nodes):
            if (
                node.anchor == name
                and node.anchor_span is not None
                and node.anchor_span.start_byte >= document_record.span.start_byte
                and node.anchor_span.end_byte <= insertion
            ):
                if visible is None or node.anchor_span.end_byte > self.document.native.nodes[visible].anchor_span.end_byte:
                    visible = index
        if visible != anchor:
            raise YamlEditFailure(YamlEditFailureKind.ANCHOR_NOT_VISIBLE)

    def validate_removal_dependencies(self, owned: Span, roots) -> None:
        """Anchor-dependency validation: only the deleted subtrees are
        collected (alias edges are never crossed), then any alias outside
        the removed span whose target was collected fails (edit.rs:1398-1418,
        RFC 0007 s12: removing an anchored definition while aliases remain
        is rejected)."""
        removed: set[int] = set()
        for node_index, alias in roots:
            if alias is None:
                self.collect_owned_nodes(node_index, removed)
        for alias in self.document.native.aliases:
            if alias.target in removed and not (
                alias.span.start_byte >= owned.start_byte and alias.span.end_byte <= owned.end_byte
            ):
                raise YamlEditFailure(YamlEditFailureKind.ANCHOR_DEPENDENCY)

    def collect_owned_nodes(self, node: int, output: set[int]) -> None:
        if node in output:
            return
        output.add(node)
        content = self.document.native.nodes[node].content
        if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            for item in content:
                if item.alias is None:
                    self.collect_owned_nodes(item.node, output)
        elif isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry):
            for entry in content:
                if entry.key_alias is None:
                    self.collect_owned_nodes(entry.key, output)
                if entry.value_alias is None:
                    self.collect_owned_nodes(entry.value, output)

    # -- syntax piece helpers ------------------------------------------------

    def scalar_literal_span(self, index: int) -> Span | None:
        node = self.document.native.nodes[index]
        scalar = node.content
        if not isinstance(scalar, NativeScalar):
            return None
        expected = {
            YamlScalarStyle.PLAIN: YamlSyntaxKind.PLAIN_SCALAR,
            YamlScalarStyle.SINGLE_QUOTED: YamlSyntaxKind.SINGLE_QUOTED_SCALAR,
            YamlScalarStyle.DOUBLE_QUOTED: YamlSyntaxKind.DOUBLE_QUOTED_SCALAR,
            YamlScalarStyle.LITERAL: YamlSyntaxKind.LITERAL_BLOCK_HEADER,
            YamlScalarStyle.FOLDED: YamlSyntaxKind.FOLDED_BLOCK_HEADER,
        }[scalar.style]
        header = self.syntax_within(node.span, expected, False)
        if header is None:
            return None
        if scalar.style in (YamlScalarStyle.LITERAL, YamlScalarStyle.FOLDED):
            content = self.syntax_between(
                YamlSyntaxKind.BLOCK_SCALAR_CONTENT, header.end_byte, node.span.end_byte, True
            )
            end = content.end_byte if content is not None else header.end_byte
            return self.document.authority.span(header.start_byte, end)
        return header

    def tag_span(self, index: int) -> Span | None:
        node = self.document.native.nodes[index]
        return self.syntax_within(node.span, YamlSyntaxKind.TAG, False)

    def syntax_within(self, span: Span, kind: YamlSyntaxKind, last: bool) -> Span | None:
        return self.syntax_between(kind, span.start_byte, span.end_byte, last)

    def syntax_between(
        self, kind: YamlSyntaxKind, start: int, end: int, last: bool
    ) -> Span | None:
        matches = [
            piece.span
            for piece, candidate in zip(
                self.document.structural_index.pieces, self.document.syntax_kinds
            )
            if candidate is kind
            and piece.span.start_byte >= start
            and piece.span.end_byte <= end
        ]
        if not matches:
            return None
        return matches[-1] if last else matches[0]

    # -- literal and fragment validation ------------------------------------

    def validate_literal(self, literal: bytes) -> None:
        if not literal:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL)
        source = _standalone_source(literal, self.document.source.encoding_facts().selected)
        try:
            candidate = parse(source, self.document.profile, self.document.parse_limits)
        except Exception:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL) from None
        root = candidate.document(0) if candidate.document_count() == 1 else None
        if root is None or root.root().kind().value != "Scalar":
            raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL)
        if root.root().anchor() is not None or any(
            kind
            in (
                YamlSyntaxKind.TAG,
                YamlSyntaxKind.ANCHOR,
                YamlSyntaxKind.ALIAS,
                YamlSyntaxKind.DIRECTIVE,
                YamlSyntaxKind.DOCUMENT_START,
                YamlSyntaxKind.DOCUMENT_END,
                YamlSyntaxKind.COMMENT,
                YamlSyntaxKind.ERROR_REGION,
            )
            for kind in candidate.lossless_syntax_kinds()
        ):
            raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL)

    def canonical_scalar_fragment(self, value: PortableValue) -> _CanonicalScalar:
        text = self._canonical_fragment_text(value, "scalar")
        parts = text.split(" ", 1)
        if len(parts) != 2:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        tag, literal = parts
        document = self._fragment_document(value)
        scalar = document.document(0).root().scalar()
        return _CanonicalScalar(tag=tag, literal=literal, canonical=scalar.canonical())

    def canonical_value_fragment(self, value: PortableValue) -> str:
        return self._canonical_fragment_text(value, "value")

    def _canonical_fragment_text(self, value: PortableValue, kind: str) -> str:
        request = MaterializationRequest.new(
            self.document.profile_id(),
            MaterializationStyleId.new("yaml.canonical-flow", 1),
        ).with_limits(_edit_materialization_limits(self.document.parse_limits))
        result = materialize_value(value, request)
        if isinstance(result, FailedMaterializationAttempt):
            raise YamlEditFailure(
                YamlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE
                if kind == "scalar"
                else YamlEditFailureKind.UNSUPPORTED_INSERTED_VALUE,
                value_kind=value.kind,
            )
        text = result.document.source.decoded_text()
        if text is None or not text.startswith("--- ") or not text.endswith("\n"):
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return text[4:-1]

    def _fragment_document(self, value: PortableValue) -> Document:
        request = MaterializationRequest.new(
            self.document.profile_id(),
            MaterializationStyleId.new("yaml.canonical-flow", 1),
        ).with_limits(_edit_materialization_limits(self.document.parse_limits))
        result = materialize_value(value, request)
        if isinstance(result, FailedMaterializationAttempt):
            raise YamlEditFailure(
                YamlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE, value_kind=value.kind
            )
        return result.document

    def validate_anchor_name(self, name: str) -> None:
        if not name or len(name) > self.document.parse_limits.max_source_bytes:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_ANCHOR_NAME)
        source = f"--- &{name} !!str \"x\"\n".encode("utf-8")
        try:
            candidate = parse(
                source,
                self.document.profile,
                ParseLimits(
                    max_source_bytes=self.document.parse_limits.max_source_bytes,
                    max_nesting_depth=2,
                    max_token_count=32,
                    max_node_count=8,
                    max_diagnostics=self.document.parse_limits.max_diagnostics,
                ),
            )
        except Exception:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_ANCHOR_NAME) from None
        document = candidate.document(0)
        if document is None or document.root().anchor() != name:
            raise YamlEditFailure(YamlEditFailureKind.INVALID_ANCHOR_NAME)

    def encode_fragment(self, text: str) -> bytes:
        return _encode_fragment(
            text,
            self.document.source.encoding_facts().selected,
            self.document.parse_limits.max_source_bytes,
        )


def _utf8_offset(value: int):
    from consema.document.source import DecodedOffset

    return DecodedOffset.utf8_byte(value)


# ---------------------------------------------------------------------------
# Candidate validation (edit.rs:1682-1947, 2017-2324)
# ---------------------------------------------------------------------------


class _ValidationContentKind(enum.Enum):
    SCALAR = "Scalar"
    SEQUENCE = "Sequence"
    MAPPING = "Mapping"


@dataclass(slots=True)
class _ValidationEdge:
    target: int
    alias_name: str | None = None
    source_alias: int | None = None


@dataclass(slots=True)
class _ValidationEntry:
    key: _ValidationEdge
    value: _ValidationEdge


@dataclass(slots=True)
class _ValidationNode:
    tag: str
    anchor: str | None
    content_kind: _ValidationContentKind
    scalar_kind: YamlScalarKind | None = None
    canonical: str | None = None
    edges: list = field(default_factory=list)  # sequence: _ValidationEdge
    entries: list = field(default_factory=list)  # mapping: _ValidationEntry
    source_node: int | None = None
    scalar_wildcard: bool = False


class _ValidationModel:
    """Cycle-safe representation-graph isomorphism (edit.rs:2017-2324)."""

    def __init__(self, roots: list[int], nodes: list[_ValidationNode]) -> None:
        self.roots = roots
        self.nodes = nodes

    @classmethod
    def from_document(cls, document: Document, retain_source: bool) -> _ValidationModel:
        nodes: list[_ValidationNode] = []
        for index, node in enumerate(document.native.nodes):
            content = node.content
            if isinstance(content, NativeScalar):
                record = _ValidationNode(
                    tag=node.tag,
                    anchor=node.anchor,
                    content_kind=_ValidationContentKind.SCALAR,
                    scalar_kind=content.kind,
                    canonical=content.canonical,
                    source_node=index if retain_source else None,
                )
            elif isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
                record = _ValidationNode(
                    tag=node.tag,
                    anchor=node.anchor,
                    content_kind=_ValidationContentKind.SEQUENCE,
                    source_node=index if retain_source else None,
                    edges=[
                        _ValidationEdge(
                            target=item.node,
                            alias_name=document.native.aliases[item.alias].name
                            if item.alias is not None
                            else None,
                            source_alias=item.alias if retain_source else None,
                        )
                        for item in content
                    ],
                )
            else:
                record = _ValidationNode(
                    tag=node.tag,
                    anchor=node.anchor,
                    content_kind=_ValidationContentKind.MAPPING,
                    source_node=index if retain_source else None,
                    entries=[
                        _ValidationEntry(
                            key=_ValidationEdge(
                                target=entry.key,
                                alias_name=document.native.aliases[entry.key_alias].name
                                if entry.key_alias is not None
                                else None,
                                source_alias=entry.key_alias if retain_source else None,
                            ),
                            value=_ValidationEdge(
                                target=entry.value,
                                alias_name=document.native.aliases[entry.value_alias].name
                                if entry.value_alias is not None
                                else None,
                                source_alias=entry.value_alias if retain_source else None,
                            ),
                        )
                        for entry in content
                    ],
                )
            nodes.append(record)
        roots = [record.root for record in document.native.documents]
        return cls(roots=roots, nodes=nodes)

    def append_root(self, imported: _ValidationModel) -> int:
        if len(imported.roots) != 1:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        offset = len(self.nodes)
        for node in imported.nodes:
            node.source_node = None
            if node.content_kind is _ValidationContentKind.SEQUENCE:
                for edge in node.edges:
                    edge.target += offset
                    edge.source_alias = None
            elif node.content_kind is _ValidationContentKind.MAPPING:
                for entry in node.entries:
                    entry.key.target += offset
                    entry.key.source_alias = None
                    entry.value.target += offset
                    entry.value.source_alias = None
        root = imported.roots[0] + offset
        self.nodes.extend(imported.nodes)
        return root

    def compare(self, candidate: _ValidationModel) -> tuple[dict[int, int], dict[int, int]]:
        if len(self.roots) != len(candidate.roots):
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        state = _ValidationComparison()
        for expected, actual in zip(self.roots, candidate.roots):
            self.compare_node(candidate, expected, actual, state)
        if len(state.node_pairs) != self.reachable_count():
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        if len(state.actual_nodes) != candidate.reachable_count():
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        return state.output_nodes, state.output_aliases

    def compare_node(
        self, candidate: _ValidationModel, expected: int, actual: int, state: _ValidationComparison
    ) -> None:
        if expected in state.node_pairs:
            if state.node_pairs[expected] != actual:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            return
        if actual in state.actual_nodes:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        state.actual_nodes.add(actual)
        expected_node = self.nodes[expected]
        actual_node = candidate.nodes[actual]
        state.node_pairs[expected] = actual
        if expected_node.source_node is not None:
            state.output_nodes[expected_node.source_node] = actual
        if expected_node.anchor != actual_node.anchor:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        if expected_node.scalar_wildcard:
            if (
                expected_node.content_kind is _ValidationContentKind.SCALAR
                and actual_node.content_kind is _ValidationContentKind.SCALAR
            ):
                return
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        if expected_node.tag != actual_node.tag:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        if (
            expected_node.content_kind is _ValidationContentKind.SCALAR
            and actual_node.content_kind is _ValidationContentKind.SCALAR
            and expected_node.scalar_kind == actual_node.scalar_kind
            and expected_node.canonical == actual_node.canonical
        ):
            return
        if (
            expected_node.content_kind is _ValidationContentKind.SEQUENCE
            and actual_node.content_kind is _ValidationContentKind.SEQUENCE
            and len(expected_node.edges) == len(actual_node.edges)
        ):
            for expected_edge, actual_edge in zip(expected_node.edges, actual_node.edges):
                self.compare_edge(candidate, expected_edge, actual_edge, state)
            return
        if (
            expected_node.content_kind is _ValidationContentKind.MAPPING
            and actual_node.content_kind is _ValidationContentKind.MAPPING
            and len(expected_node.entries) == len(actual_node.entries)
        ):
            for expected_entry, actual_entry in zip(expected_node.entries, actual_node.entries):
                self.compare_edge(candidate, expected_entry.key, actual_entry.key, state)
                self.compare_edge(candidate, expected_entry.value, actual_entry.value, state)
            return
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)

    def compare_edge(
        self,
        candidate: _ValidationModel,
        expected: _ValidationEdge,
        actual: _ValidationEdge,
        state: _ValidationComparison,
    ) -> None:
        if expected.alias_name is None and actual.alias_name is None:
            pass
        elif (
            expected.alias_name is not None
            and actual.alias_name is not None
            and expected.alias_name == actual.alias_name
        ):
            if expected.source_alias is not None and actual.source_alias is not None:
                state.output_aliases[expected.source_alias] = actual.source_alias
        else:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
        self.compare_node(candidate, expected.target, actual.target, state)

    def reachable_count(self) -> int:
        reached: set[int] = set()
        pending = list(self.roots)
        while pending:
            index = pending.pop()
            if index in reached:
                continue
            reached.add(index)
            if index >= len(self.nodes):
                continue
            node = self.nodes[index]
            if node.content_kind is _ValidationContentKind.SEQUENCE:
                pending.extend(edge.target for edge in node.edges)
            elif node.content_kind is _ValidationContentKind.MAPPING:
                for entry in node.entries:
                    pending.append(entry.key.target)
                    pending.append(entry.value.target)
        return len(reached)


class _ValidationComparison:
    def __init__(self) -> None:
        self.node_pairs: dict[int, int] = {}
        self.actual_nodes: set[int] = set()
        self.output_nodes: dict[int, int] = {}
        self.output_aliases: dict[int, int] = {}


# ---------------------------------------------------------------------------
# Transaction commit and dry run (edit.rs:401-568)
# ---------------------------------------------------------------------------


def commit(document: Document, transaction: EditTransaction) -> EditCommit:
    """Atomically commits validated YAML scalar, collection, anchor, and
    alias operations; on failure the base document remains unchanged
    (edit.rs:401-551)."""
    if transaction.base != document.snapshot_identity():
        raise YamlEditFailure(YamlEditFailureKind.WRONG_SNAPSHOT)
    validate_dependencies(document, transaction)
    diagnostics: list[YamlDiagnostic] = []
    planner = _EditPlanner(document)
    prepared: list[_PreparedEdit] = []
    for operation in transaction.operations:
        prepared.extend(planner.prepare_operation(operation, diagnostics))
    prepared.sort(key=lambda edit: (edit.old_span.start_byte, edit.old_span.end_byte))
    validate_prepared_ownership(prepared)
    raw = document.source.bytes()
    target_len = len(raw)
    for edit in prepared:
        target_len = target_len - edit.old_span.len() + len(edit.replacement)
        if target_len < 0:
            raise YamlEditFailure(
                YamlEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
            )
    if target_len > document.parse_limits.max_source_bytes:
        raise YamlEditFailure(
            YamlEditFailureKind.RESOURCE_LIMIT, resource_name="target-bytes"
        )
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
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    candidate_nodes, candidate_aliases = validate_candidate(document, new_document, transaction)

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
                new_node = None
                status = NodeMappingStatus.REPLACED
                reason = None
                if plan.kind is _MappingPlanKind.REMOVED:
                    status = NodeMappingStatus.DELETED
                    reason = "association-removed-by-declared-operation"
                elif plan.kind is _MappingPlanKind.NODE:
                    candidate_index = candidate_nodes.get(plan.index)
                    if candidate_index is not None:
                        new_node = new_document.authority.node_ref(
                            candidate_index, NodeRole.YAML_NODE
                        )
                    else:
                        reason = "reparsed-node-not-uniquely-located"
                elif plan.kind is _MappingPlanKind.ANCHOR:
                    candidate_index = candidate_nodes.get(plan.index)
                    if candidate_index is not None:
                        candidate_node = new_document.native.nodes[candidate_index]
                        if candidate_node.anchor is not None:
                            new_node = new_document.authority.node_ref(
                                candidate_index, NodeRole.YAML_ANCHOR_DEFINITION
                            )
                        else:
                            reason = "reparsed-node-not-uniquely-located"
                    else:
                        reason = "reparsed-node-not-uniquely-located"
                elif plan.kind is _MappingPlanKind.ALIAS:
                    candidate_alias = candidate_aliases.get(plan.index)
                    if candidate_alias is not None:
                        alias = new_document.alias(candidate_alias)
                        if alias is not None:
                            new_node = alias.node_ref()
                        else:
                            reason = "reparsed-node-not-uniquely-located"
                    else:
                        reason = "reparsed-node-not-uniquely-located"
                mappings.append(
                    NodeMapping(old=old, new=new_node, status=status, reason=reason)
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
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    try:
        untouched_proof = UntouchedByteProof.create(
            document.source, new_document.source, list(source_patch.replacements)
        )
    except Exception:
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None
    return EditCommit(
        document=new_document,
        change_set=change_set,
        source_patch=source_patch,
        untouched_proof=untouched_proof,
    )


def dry_run(
    document: Document,
    transaction: EditTransaction,
    source_id: EditPlanSourceId,
) -> EditPlan:
    """Fully validates and plans an edit without returning a new Document
    (edit.rs:553-568)."""
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
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED) from None


def validate_dependencies(document: Document, transaction: EditTransaction) -> None:
    """Duplicate targets and single-mutation-per-container rules
    (edit.rs:1974-2014; RFC 0007 s12: v1 accepts at most one structural
    mutation per base container in a transaction)."""
    targets: set[NodeRef] = set()
    structural_containers: set[int] = set()
    planner = _EditPlanner(document)
    for operation in transaction.operations:
        target = _operation_target(operation)
        if target in targets:
            raise YamlEditFailure(YamlEditFailureKind.DUPLICATE_TARGET)
        targets.add(target)
        container = _structural_container(operation, planner)
        if container is not None:
            if container in structural_containers:
                raise YamlEditFailure(YamlEditFailureKind.STRUCTURAL_CONTAINER_CONFLICT)
            structural_containers.add(container)


def _operation_target(operation: EditOperation) -> NodeRef:
    kind = operation.kind
    if kind is EditOperationKind.REPLACE_SCALAR:
        return operation.scalar.target
    if kind in (
        EditOperationKind.RENAME_ANCHOR,
        EditOperationKind.REMOVE_MAPPING_ENTRY,
        EditOperationKind.REMOVE_SEQUENCE_ELEMENT,
    ):
        return operation.target
    if kind is EditOperationKind.INSERT_MAPPING_ENTRY:
        return operation.mapping
    return operation.sequence


def _structural_container(operation: EditOperation, planner: _EditPlanner) -> int | None:
    kind = operation.kind
    if kind is EditOperationKind.INSERT_MAPPING_ENTRY:
        return planner.resolve_node(operation.mapping, NodeRole.YAML_NODE)
    if kind is EditOperationKind.REMOVE_MAPPING_ENTRY:
        return planner.resolve_mapping_entry(operation.target)[0]
    if kind in (
        EditOperationKind.INSERT_SEQUENCE_ELEMENT,
        EditOperationKind.INSERT_ALIAS,
    ):
        return planner.resolve_node(operation.sequence, NodeRole.YAML_NODE)
    if kind is EditOperationKind.REMOVE_SEQUENCE_ELEMENT:
        return planner.resolve_sequence_item(operation.target)[0]
    return None


def validate_prepared_ownership(prepared: list[_PreparedEdit]) -> None:
    """Overlap and reuse checks on the ordered prepared edits
    (edit.rs:2454-2467)."""
    for index in range(len(prepared) - 1):
        left, right = prepared[index], prepared[index + 1]
        if (
            not left.old_span.is_empty()
            and not right.old_span.is_empty()
            and left.old_span.end_byte > right.old_span.start_byte
        ):
            raise YamlEditFailure(YamlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT)
        if left.old_span == right.old_span:
            raise YamlEditFailure(YamlEditFailureKind.OVERLAPPING_OWNERSHIP)


def validate_candidate(
    document: Document, candidate: Document, transaction: EditTransaction
) -> tuple[dict[int, int], dict[int, int]]:
    """Validates the reparsed candidate against the declared operations
    (edit.rs:1682-1947); returns the old-to-new node and alias maps."""
    if any(_is_structural_operation(operation) for operation in transaction.operations):
        return _validate_structural_candidate(document, candidate, transaction)
    scalar_targets: set[int] = set()
    renames: dict[int, str] = {}
    planner = _EditPlanner(document)
    for operation in transaction.operations:
        if operation.kind is EditOperationKind.REPLACE_SCALAR:
            scalar_targets.add(planner.resolve_node(operation.scalar.target, NodeRole.YAML_NODE))
        elif operation.kind is EditOperationKind.RENAME_ANCHOR:
            renames[planner.resolve_node(operation.target, NodeRole.YAML_ANCHOR_DEFINITION)] = (
                operation.name
            )
    if (
        len(document.native.documents) != len(candidate.native.documents)
        or len(document.native.nodes) != len(candidate.native.nodes)
        or len(document.native.aliases) != len(candidate.native.aliases)
    ):
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    for old, new in zip(document.native.documents, candidate.native.documents):
        if old.root != new.root:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    for index, (old, new) in enumerate(zip(document.native.nodes, candidate.native.nodes)):
        expected_anchor = renames.get(index, old.anchor)
        if (
            new.anchor != expected_anchor
            or not _same_topology(old.content, new.content)
            or (
                index not in scalar_targets
                and (old.tag != new.tag or not _same_scalar_semantics(old.content, new.content))
            )
        ):
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    for old, new in zip(document.native.aliases, candidate.native.aliases):
        expected_name = renames.get(old.target, old.name)
        if old.target != new.target or new.name != expected_name:
            raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    return (
        {index: index for index in range(len(document.native.nodes))},
        {index: index for index in range(len(document.native.aliases))},
    )


def _is_structural_operation(operation: EditOperation) -> bool:
    return operation.kind in (
        EditOperationKind.INSERT_MAPPING_ENTRY,
        EditOperationKind.REMOVE_MAPPING_ENTRY,
        EditOperationKind.INSERT_SEQUENCE_ELEMENT,
        EditOperationKind.REMOVE_SEQUENCE_ELEMENT,
        EditOperationKind.INSERT_ALIAS,
    )


def _validate_structural_candidate(
    document: Document, candidate: Document, transaction: EditTransaction
) -> tuple[dict[int, int], dict[int, int]]:
    """Cycle-safe representation-graph isomorphism validation
    (edit.rs:1766-1947): the exact tags, scalar canonical values, ordered
    associations, anchors, alias names, sharing, and cycles are compared
    without relying on reparsed node ordinals (RFC 0007 s12)."""
    if len(document.native.documents) != len(candidate.native.documents):
        raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
    expected = _ValidationModel.from_document(document, True)
    planner = _EditPlanner(document)
    for operation in transaction.operations:
        kind = operation.kind
        if kind is EditOperationKind.REPLACE_SCALAR:
            target = planner.resolve_node(operation.scalar.target, NodeRole.YAML_NODE)
            if expected.nodes[target].content_kind is not _ValidationContentKind.SCALAR:
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            if operation.scalar.kind is ScalarReplacementKind.SEMANTIC:
                imported = expected.append_root(
                    _validation_model_for_value(document, operation.scalar.value)
                )
                replacement = expected.nodes[imported]
                expected.nodes[target].tag = replacement.tag
                expected.nodes[target].content_kind = replacement.content_kind
                expected.nodes[target].scalar_kind = replacement.scalar_kind
                expected.nodes[target].canonical = replacement.canonical
                expected.nodes[target].scalar_wildcard = False
            else:
                expected.nodes[target].scalar_wildcard = True
        elif kind is EditOperationKind.RENAME_ANCHOR:
            target = planner.resolve_node(operation.target, NodeRole.YAML_ANCHOR_DEFINITION)
            old_name = expected.nodes[target].anchor
            if old_name is None:
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            expected.nodes[target].anchor = operation.name
            for node in expected.nodes:
                if node.content_kind is _ValidationContentKind.SEQUENCE:
                    for edge in node.edges:
                        if edge.target == target and edge.alias_name == old_name:
                            edge.alias_name = operation.name
                elif node.content_kind is _ValidationContentKind.MAPPING:
                    for entry in node.entries:
                        for edge in (entry.key, entry.value):
                            if edge.target == target and edge.alias_name == old_name:
                                edge.alias_name = operation.name
        elif kind is EditOperationKind.INSERT_MAPPING_ENTRY:
            container = planner.resolve_node(operation.mapping, NodeRole.YAML_NODE)
            content = document.native.nodes[container].content
            if not (isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry)):
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            ordinal = planner.mapping_placement(container, content, operation.placement)
            key = expected.append_root(_validation_model_for_value(document, operation.key))
            value = expected.append_root(_validation_model_for_value(document, operation.value))
            if expected.nodes[container].content_kind is not _ValidationContentKind.MAPPING:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            expected.nodes[container].entries.insert(
                ordinal,
                _ValidationEntry(
                    key=_ValidationEdge(target=key),
                    value=_ValidationEdge(target=value),
                ),
            )
        elif kind is EditOperationKind.REMOVE_MAPPING_ENTRY:
            container, ordinal = planner.resolve_mapping_entry(operation.target)
            if expected.nodes[container].content_kind is not _ValidationContentKind.MAPPING:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            del expected.nodes[container].entries[ordinal]
        elif kind is EditOperationKind.INSERT_SEQUENCE_ELEMENT:
            container = planner.resolve_node(operation.sequence, NodeRole.YAML_NODE)
            content = document.native.nodes[container].content
            if not (isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem)):
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            ordinal = planner.sequence_placement(container, content, operation.placement)
            target = expected.append_root(_validation_model_for_value(document, operation.value))
            if expected.nodes[container].content_kind is not _ValidationContentKind.SEQUENCE:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            expected.nodes[container].edges.insert(ordinal, _ValidationEdge(target=target))
        elif kind is EditOperationKind.REMOVE_SEQUENCE_ELEMENT:
            container, ordinal = planner.resolve_sequence_item(operation.target)
            if expected.nodes[container].content_kind is not _ValidationContentKind.SEQUENCE:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            del expected.nodes[container].edges[ordinal]
        elif kind is EditOperationKind.INSERT_ALIAS:
            container = planner.resolve_node(operation.sequence, NodeRole.YAML_NODE)
            target = planner.resolve_node(operation.anchor, NodeRole.YAML_ANCHOR_DEFINITION)
            content = document.native.nodes[container].content
            if not (isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem)):
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            ordinal = planner.sequence_placement(container, content, operation.placement)
            name = document.native.nodes[target].anchor
            if name is None:
                raise YamlEditFailure(YamlEditFailureKind.WRONG_ROLE)
            if expected.nodes[container].content_kind is not _ValidationContentKind.SEQUENCE:
                raise YamlEditFailure(YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED)
            expected.nodes[container].edges.insert(
                ordinal, _ValidationEdge(target=target, alias_name=name)
            )
    return expected.compare(_ValidationModel.from_document(candidate, True))


def _validation_model_for_value(document: Document, value: PortableValue) -> _ValidationModel:
    request = MaterializationRequest.new(
        document.profile_id(),
        MaterializationStyleId.new("yaml.canonical-flow", 1),
    ).with_limits(_edit_materialization_limits(document.parse_limits))
    result = materialize_value(value, request)
    if isinstance(result, FailedMaterializationAttempt):
        raise YamlEditFailure(
            YamlEditFailureKind.UNSUPPORTED_INSERTED_VALUE, value_kind=value.kind
        )
    return _ValidationModel.from_document(result.document, False)


def _same_topology(old, new) -> bool:
    if isinstance(old, NativeScalar) and isinstance(new, NativeScalar):
        return True
    if old and isinstance(old[0], NativeSequenceItem) and new and isinstance(new[0], NativeSequenceItem):
        return len(old) == len(new) and all(
            left.node == right.node and (left.alias is None) == (right.alias is None)
            for left, right in zip(old, new)
        )
    if old and isinstance(old[0], NativeMappingEntry) and new and isinstance(new[0], NativeMappingEntry):
        return len(old) == len(new) and all(
            left.key == right.key
            and left.value == right.value
            and (left.key_alias is None) == (right.key_alias is None)
            and (left.value_alias is None) == (right.value_alias is None)
            for left, right in zip(old, new)
        )
    return False


def _same_scalar_semantics(old, new) -> bool:
    if isinstance(old, NativeScalar) and isinstance(new, NativeScalar):
        return old.canonical == new.canonical and old.kind == new.kind
    return True


# ---------------------------------------------------------------------------
# Operation metadata and summaries (edit.rs:2577-2697)
# ---------------------------------------------------------------------------


def operation_metadata(transaction: EditTransaction) -> dict[str, str]:
    """Operation metadata keys: operation.{index} = "id@version"
    (edit.rs:2577-2604)."""
    metadata: dict[str, str] = {}
    for index, operation in enumerate(transaction.operations):
        metadata[f"operation.{index}"] = _operation_id(operation)
    return metadata


def _operation_id(operation: EditOperation) -> str:
    if operation.kind is EditOperationKind.REPLACE_SCALAR:
        if operation.scalar.kind is ScalarReplacementKind.SEMANTIC:
            return "yaml.edit.replace-scalar-semantic@1"
        return "yaml.edit.replace-scalar-literal@1"
    return {
        EditOperationKind.RENAME_ANCHOR: "yaml.edit.rename-anchor@1",
        EditOperationKind.INSERT_MAPPING_ENTRY: "yaml.edit.insert-mapping-entry@1",
        EditOperationKind.REMOVE_MAPPING_ENTRY: "yaml.edit.remove-mapping-entry@1",
        EditOperationKind.INSERT_SEQUENCE_ELEMENT: "yaml.edit.insert-sequence-element@1",
        EditOperationKind.REMOVE_SEQUENCE_ELEMENT: "yaml.edit.remove-sequence-element@1",
        EditOperationKind.INSERT_ALIAS: "yaml.edit.insert-alias@1",
    }[operation.kind]


def operation_summaries(transaction: EditTransaction) -> list[EditOperationSummary]:
    """Safe, content-free operation summaries (edit.rs:2606-2679)."""
    summaries: list[EditOperationSummary] = []
    for index, operation in enumerate(transaction.operations):
        summaries.append(
            EditOperationSummary.new(
                FormatOperationId.new(_operation_id(operation).rsplit("@", 1)[0], 1),
                _summary_arguments(operation),
            )
        )
    return summaries


def _summary_arguments(operation: EditOperation) -> dict[str, str]:
    arguments: dict[str, str] = {}
    kind = operation.kind
    if kind is EditOperationKind.REPLACE_SCALAR:
        if operation.scalar.kind is ScalarReplacementKind.SEMANTIC:
            arguments["policy"] = _policy_name(operation.scalar.policy)
            arguments["value_kind"] = operation.scalar.value.kind.value
        else:
            arguments["literal_bytes"] = str(len(operation.scalar.literal))
        arguments["target_role"] = "yaml.scalar@1"
    elif kind is EditOperationKind.RENAME_ANCHOR:
        arguments["name_bytes"] = str(len(operation.name))
        arguments["target_role"] = "yaml.anchor-definition@1"
    elif kind is EditOperationKind.INSERT_MAPPING_ENTRY:
        arguments["key_kind"] = operation.key.kind.value
        arguments["value_kind"] = operation.value.kind.value
        arguments["placement"] = _placement_name(operation.placement)
        arguments["target_role"] = "yaml.mapping@1"
    elif kind is EditOperationKind.REMOVE_MAPPING_ENTRY:
        arguments["target_role"] = "yaml.mapping-entry@1"
    elif kind is EditOperationKind.INSERT_SEQUENCE_ELEMENT:
        arguments["value_kind"] = operation.value.kind.value
        arguments["placement"] = _placement_name(operation.placement)
        arguments["target_role"] = "yaml.sequence@1"
    elif kind is EditOperationKind.REMOVE_SEQUENCE_ELEMENT:
        arguments["target_role"] = "yaml.sequence-element@1"
    elif kind is EditOperationKind.INSERT_ALIAS:
        arguments["placement"] = _placement_name(operation.placement)
        arguments["target_role"] = "yaml.sequence@1"
    return arguments


def _placement_name(placement: AssociationPlacement) -> str:
    if placement.kind == "Start":
        return "start"
    if placement.kind == "End":
        return "end"
    if placement.kind == "Before":
        return "before"
    return "after"


def _policy_name(policy: RepresentationPolicy) -> str:
    return {
        RepresentationPolicy.EXACT_LITERAL: "exact-literal",
        RepresentationPolicy.PRESERVE_COMPATIBLE: "preserve-compatible",
        RepresentationPolicy.CANONICAL_FOR_PROFILE: "canonical-for-profile",
        RepresentationPolicy.PRESERVE_ELSE_CANONICAL: "preserve-else-canonical",
    }[policy]


# ---------------------------------------------------------------------------
# Limits and encoding (edit.rs:2491-2575)
# ---------------------------------------------------------------------------


def _source_patch_limits(parse_limits: ParseLimits, operation_count: int) -> SourcePatchLimits:
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=parse_limits.max_source_bytes,
            max_decoded_utf8_bytes=parse_limits.max_source_bytes * 2,
            max_decoded_scalars=parse_limits.max_source_bytes,
        ),
        max_replacements=operation_count,
        max_patch_bytes=parse_limits.max_source_bytes * 2,
    )


def _edit_materialization_limits(limits: ParseLimits) -> MaterializationLimits:
    return MaterializationLimits(
        max_input_nodes=limits.max_node_count,
        max_output_bytes=limits.max_source_bytes,
        max_depth=limits.max_nesting_depth,
        max_report_entries=limits.max_diagnostics,
        max_provenance_entries=limits.max_node_count * 4,
    )


def _standalone_source(fragment: bytes, encoding: SourceEncoding) -> bytes:
    if encoding.kind is SourceEncodingKind.UTF8:
        bom = b""
    elif encoding.kind is SourceEncodingKind.UTF16LE:
        bom = b"\xff\xfe"
    elif encoding.kind is SourceEncodingKind.UTF16BE:
        bom = b"\xfe\xff"
    else:
        raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL)
    return bom + fragment


def _encode_fragment(text: str, encoding: SourceEncoding, max_bytes: int) -> bytes:
    if encoding.kind is SourceEncodingKind.UTF8:
        if len(text) > max_bytes:
            raise YamlEditFailure(
                YamlEditFailureKind.RESOURCE_LIMIT, resource_name="replacement-bytes"
            )
        return text.encode("utf-8")
    if encoding.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        length = len(text.encode("utf-16-be"))
        if length > max_bytes:
            raise YamlEditFailure(
                YamlEditFailureKind.RESOURCE_LIMIT, resource_name="replacement-bytes"
            )
        if encoding.kind is SourceEncodingKind.UTF16LE:
            return text.encode("utf-16-le")
        return text.encode("utf-16-be")
    raise YamlEditFailure(YamlEditFailureKind.INVALID_LITERAL)


# ---------------------------------------------------------------------------
# Preserved literals (edit.rs:2326-2385)
# ---------------------------------------------------------------------------


def _shorthand_tag_uri(tag: str) -> str | None:
    return {
        "!!null": "tag:yaml.org,2002:null",
        "!!bool": "tag:yaml.org,2002:bool",
        "!!int": "tag:yaml.org,2002:int",
        "!!float": "tag:yaml.org,2002:float",
        "!!str": "tag:yaml.org,2002:str",
        "!!timestamp": "tag:yaml.org,2002:timestamp",
        "!!binary": "tag:yaml.org,2002:binary",
    }.get(tag)


def _decode_canonical_literal(literal: str) -> str | None:
    try:
        candidate = parse(literal.encode("utf-8"), YamlProfile.YAML12_CORE_V1, ParseLimits())
    except Exception:
        return None
    document = candidate.document(0)
    if document is None:
        return None
    scalar = document.root().scalar()
    if scalar is None:
        return None
    return scalar.decoded()


def _preserved_literal(
    old_kind: YamlScalarKind,
    old_style: YamlScalarStyle,
    old_tag: str,
    explicit_tag: bool,
    canonical: _CanonicalScalar,
    value_kind: Kind,
    profile: YamlProfile,
) -> str | None:
    """The preserve-compatible rendering (edit.rs:2326-2362)."""
    if old_kind != _yaml_kind(value_kind) or old_tag != _shorthand_tag_uri(canonical.tag):
        return None
    decoded = _decode_canonical_literal(canonical.literal)
    if decoded is None:
        return None
    if old_style is YamlScalarStyle.DOUBLE_QUOTED:
        return canonical.literal
    if old_style is YamlScalarStyle.SINGLE_QUOTED:
        if "\n" in decoded or "\r" in decoded:
            return None
        return f"'{decoded.replace(chr(39), chr(39) * 2)}'"
    if old_style is YamlScalarStyle.PLAIN:
        source = f"{canonical.tag} {decoded}" if explicit_tag else decoded
        try:
            candidate = parse(source.encode("utf-8"), profile, ParseLimits())
        except Exception:
            return None
        document = candidate.document(0)
        if document is None:
            return None
        scalar = document.root().scalar()
        if scalar is None:
            return None
        if scalar.kind() == old_kind and scalar.canonical() == canonical.canonical:
            return decoded
        return None
    return None


def _yaml_kind(kind: Kind) -> YamlScalarKind:
    return {
        Kind.NULL: YamlScalarKind.NULL,
        Kind.BOOLEAN: YamlScalarKind.BOOLEAN,
        Kind.INTEGER: YamlScalarKind.INTEGER,
        Kind.DECIMAL: YamlScalarKind.FLOAT,
        Kind.BINARY_FLOAT64: YamlScalarKind.FLOAT,
        Kind.STRING: YamlScalarKind.STRING,
        Kind.BYTES: YamlScalarKind.BINARY,
        Kind.DATE: YamlScalarKind.TIMESTAMP,
        Kind.OFFSET_DATE_TIME: YamlScalarKind.TIMESTAMP,
    }.get(kind, YamlScalarKind.CUSTOM)


def _is_scalar_value(kind: Kind) -> bool:
    return kind not in (Kind.SEQUENCE, Kind.OBJECT, Kind.ENTRY_MAPPING)
