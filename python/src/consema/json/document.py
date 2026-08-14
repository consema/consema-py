"""The immutable JSON-family document and its native value views.

Authority (Rust arbitration for the public surface):

- Document fields and accessors: https://github.com/consema/consema-rs/blob/main/consema-json/src/lib.rs —
  snapshot identity, exact source, render() (exact current source bytes,
  lib.rs), format family (lib.rs), profile (lib.rs),
  formation status (lib.rs), diagnostics (lib.rs), lossless
  structural index (lib.rs), syntax kinds (lib.rs), root
  (lib.rs).
- Native value views: lib.rs — JsonValue (kind/typed accessors),
  JsonObjectMember (ordinal, key/value node refs, name), JsonArrayElement;
  the regional availability model lib.rs; node roles
  (NodeRole::Value / ObjectMember / ObjectKey / ArrayElement / JsonSyntaxPiece,
  consema-document lib.rs).
- Span/nodes/identity: consema-document (Span lib.rs, NodeRef
  lib.rs, DocumentAuthority lib.rs) — reused as-is from
  consema.document.structural.

The document is logically immutable; every NodeRef and Span is bound to one
snapshot identity. Recovered documents retain exact bytes and explicit
recovery structure but never fabricate native semantics
(RFC 0005 §2, https://github.com/consema/consema/blob/main/docs/rfcs/0005-json-family-production-v1.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.source import SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    Span,
)
from consema.json.errors import JsonDiagnostic
from consema.json.kinds import (
    JsonProfile,
    JsonSyntaxKind,
    JsonValueKind,
    SemanticAvailability,
    SemanticUnavailable,
)
from consema.json.parser import (
    ElementEntity,
    InternalKind,
    MemberEntity,
    ValueEntity,
)


@dataclass(frozen=True, slots=True)
class JsonDocument:
    """Complete immutable JSON/JSONC/JSON5 document snapshot
    (https://github.com/consema/consema-rs/blob/main/consema-json/src/lib.rs)."""

    authority: DocumentAuthority
    source: SourceSnapshot
    profile: JsonProfile
    structural_index: LosslessStructuralIndex
    syntax_kinds: tuple[JsonSyntaxKind, ...]
    _formation_status: FormationStatus
    diagnostics: tuple[JsonDiagnostic, ...]
    entities: tuple[object, ...]
    root_index: int
    parse_limits: ParseLimits

    # -- identity and source -----------------------------------------------

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every NodeRef and Span belongs
        (lib.rs)."""
        return self.authority.identity

    def render(self) -> bytes:
        """Exact current source bytes; the default rendering
        (lib.rs)."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """JSON format family contract (lib.rs)."""
        return FormatFamilyId.new("json", 1)

    def profile_id(self) -> ProfileId:
        """Exact language profile (lib.rs)."""
        return self.profile.id()

    def formation_status(self) -> FormationStatus:
        """Whether recovery structure was required (lib.rs)."""
        return self._formation_status

    def diagnostic_records(self) -> tuple[JsonDiagnostic, ...]:
        """Deterministically ordered document diagnostics (lib.rs)."""
        return self.diagnostics

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive token/trivia/error-region byte coverage
        (lib.rs)."""
        return self.structural_index

    def lossless_syntax_kinds(self) -> tuple[JsonSyntaxKind, ...]:
        """Format-specific kind for every structural piece in source order
        (lib.rs)."""
        return self.syntax_kinds

    def root(self) -> JsonValue:
        """Root native semantic value (lib.rs)."""
        return JsonValue(self, self.root_index)

    # -- internal entity access ---------------------------------------------

    def entity(self, index: int) -> object:
        return self.entities[index]

    def value_entity(self, index: int) -> ValueEntity:
        entity = self.entities[index]
        assert isinstance(entity, ValueEntity)
        return entity

    def node_ref(self, index: int, role: NodeRole) -> NodeRef:
        return self.authority.node_ref(index, role)

    def span(self, index: int) -> Span:
        return self.entities[index].span

    def validate_ref(self, node: NodeRef, roles: tuple[NodeRole, ...]) -> int:
        """Resolves one snapshot-bound handle to an entity index
        (lib.rs)."""
        if node.snapshot != self.authority.identity:
            raise JsonAccessError(JsonAccessErrorKind.WRONG_SNAPSHOT)
        if node.role not in roles:
            raise JsonAccessError(JsonAccessErrorKind.WRONG_ROLE)
        if node.index < 0 or node.index >= len(self.entities):
            raise JsonAccessError(JsonAccessErrorKind.UNKNOWN_NODE)
        return node.index


class JsonAccessErrorKind:
    """Stable node-resolution failures (consema-json lib.rs ``JsonAccessError``)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    UNKNOWN_NODE = "UnknownNode"


class JsonAccessError(Exception):
    """Node resolution failure (consema-json lib.rs ``JsonAccessError``)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class JsonValue:
    """Immutable view of one native value entity (lib.rs)."""

    __slots__ = ("document", "index")

    def __init__(self, document: JsonDocument, index: int) -> None:
        self.document = document
        self.index = index

    def node_ref(self) -> NodeRef:
        return self.document.node_ref(self.index, NodeRole.VALUE)

    def span(self) -> Span:
        return self.document.span(self.index)

    def kind(self) -> SemanticAvailability[JsonValueKind]:
        """Native category when locally available (lib.rs)."""
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        return SemanticAvailability.available(_KIND_BY_INTERNAL[internal.kind])

    def as_boolean(self) -> SemanticAvailability[bool | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is InternalKind.BOOLEAN:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.available(None)

    def as_integer(self) -> SemanticAvailability[int | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is InternalKind.INTEGER:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.available(None)

    def as_decimal(self) -> SemanticAvailability[object | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is InternalKind.DECIMAL:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.available(None)

    def as_binary_float64(self) -> SemanticAvailability[int | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is InternalKind.BINARY_FLOAT64:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.available(None)

    def as_string(self) -> SemanticAvailability[str | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is InternalKind.STRING:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.available(None)

    def array_elements(self) -> SemanticAvailability[list[JsonArrayElement] | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is not InternalKind.ARRAY:
            return SemanticAvailability.available(None)
        elements = []
        for entity_index in internal.payload:
            element = self.document.entities[entity_index]
            assert isinstance(element, ElementEntity)
            elements.append(JsonArrayElement(self.document, entity_index))
        return SemanticAvailability.available(elements)

    def object_members(self) -> SemanticAvailability[list[JsonObjectMember] | None]:
        internal = self.document.value_entity(self.index).internal
        if internal.kind is InternalKind.UNAVAILABLE:
            return SemanticAvailability.unavailable(internal.payload)
        if internal.kind is not InternalKind.OBJECT:
            return SemanticAvailability.available(None)
        members = []
        for entity_index in internal.payload:
            member = self.document.entities[entity_index]
            assert isinstance(member, MemberEntity)
            members.append(JsonObjectMember(self.document, entity_index))
        return SemanticAvailability.available(members)


class JsonObjectMember:
    """One ordered object member with duplicate identity preserved
    (lib.rs)."""

    __slots__ = ("document", "index")

    def __init__(self, document: JsonDocument, index: int) -> None:
        self.document = document
        self.index = index

    def node_ref(self) -> NodeRef:
        return self.document.node_ref(self.index, NodeRole.OBJECT_MEMBER)

    def span(self) -> Span:
        return self.document.span(self.index)

    def ordinal(self) -> int:
        return self.entity().ordinal

    def key_node_ref(self) -> NodeRef:
        return self.document.node_ref(self.entity().key, NodeRole.OBJECT_KEY)

    def value_node_ref(self) -> NodeRef:
        return self.document.node_ref(self.entity().value, NodeRole.VALUE)

    def name(self) -> SemanticAvailability[str]:
        """Decoded member name when locally available (lib.rs)."""
        internal = self.document.value_entity(self.entity().key).internal
        if internal.kind is InternalKind.STRING:
            return SemanticAvailability.available(internal.payload)
        return SemanticAvailability.unavailable(SemanticUnavailable.INVALID_LITERAL)

    def value(self) -> JsonValue:
        return JsonValue(self.document, self.entity().value)

    def entity(self) -> MemberEntity:
        entity = self.document.entities[self.index]
        assert isinstance(entity, MemberEntity)
        return entity


class JsonArrayElement:
    """One ordered array element (lib.rs)."""

    __slots__ = ("document", "index")

    def __init__(self, document: JsonDocument, index: int) -> None:
        self.document = document
        self.index = index

    def node_ref(self) -> NodeRef:
        return self.document.node_ref(self.index, NodeRole.ARRAY_ELEMENT)

    def span(self) -> Span:
        return self.document.span(self.index)

    def ordinal(self) -> int:
        return self.entity().ordinal

    def value_node_ref(self) -> NodeRef:
        return self.document.node_ref(self.entity().value, NodeRole.VALUE)

    def value(self) -> JsonValue:
        return JsonValue(self.document, self.entity().value)

    def entity(self) -> ElementEntity:
        entity = self.document.entities[self.index]
        assert isinstance(entity, ElementEntity)
        return entity


_KIND_BY_INTERNAL = {
    InternalKind.NULL: JsonValueKind.NULL,
    InternalKind.BOOLEAN: JsonValueKind.BOOLEAN,
    InternalKind.INTEGER: JsonValueKind.INTEGER,
    InternalKind.DECIMAL: JsonValueKind.DECIMAL,
    InternalKind.BINARY_FLOAT64: JsonValueKind.BINARY_FLOAT64,
    InternalKind.STRING: JsonValueKind.STRING,
    InternalKind.ARRAY: JsonValueKind.ARRAY,
    InternalKind.OBJECT: JsonValueKind.OBJECT,
}
