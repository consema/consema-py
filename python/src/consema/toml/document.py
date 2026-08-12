"""The immutable TOML 1.0 document snapshot and its native model.

Authority:

- RFC 0001 (docs/rfcs/0001-toml-1.0-profile.md) is the language-neutral
  contract: §2 (lines 17-49) freezes the four public roles (TomlItem,
  TomlEntry, TomlKey, TomlArrayElement), the closed native item set
  (String, Integer, Float, Boolean, OffsetDateTime, LocalDateTime,
  LocalDate, LocalTime, Array, InlineTable, RootTable, StandardTable,
  ImplicitTable, DottedTable, ArrayOfTables), the dotted-key layering rule
  (§2.1, lines 38-45), and the span rules (§2.2, lines 47-49: implicit
  logical nodes may use the span of their creating key segment, never a
  fabricated range). §1 (line 12) and IMPLEMENTATION.md:102: TOML tables
  are not JSON objects; they meet only in explicit projection.
- The entity spans transcribe the Rust entity builder
  (crates/consema-toml/src/parser.rs:84-338): the root table spans the
  whole source; a table spans its header/body range; a key spans its key
  literal; an entry spans from the key start to the value end; an array
  element spans its value.
- The temporal datum shape (TomlDate/TomlTime/TomlOffset/TomlDateTime)
  transcribes crates/consema-toml/src/lib.rs:307-349.
- Formation status is always Complete for this family
  (lib.rs:175-179: TOML 0.2 forms only complete valid documents; RFC 0016
  §5.1 F10 — the closed two-value enum).
- Node handles and spans are snapshot-bound (consema.document structural
  NodeRef/Span; RFC 0001 §2 line 18).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.source import SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    SnapshotIdentity,
    Span,
)
from consema.document.limits import ParseLimits

from consema.toml.syntax import TomlSyntaxKind


class TomlProfile(enum.Enum):
    """Frozen TOML language profiles (crates/consema-toml/src/lib.rs:34-39)."""

    TOML10_V1 = "toml.1.0@1"

    def profile_id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:111-119)."""
        return ProfileId.new("toml.1.0", 1)


class TomlItemKind(enum.Enum):
    """Native TOML item category (crates/consema-toml/src/lib.rs:272-305,
    transcribed verbatim)."""

    STRING = "String"
    INTEGER = "Integer"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    OFFSET_DATE_TIME = "OffsetDateTime"
    LOCAL_DATE_TIME = "LocalDateTime"
    LOCAL_DATE = "LocalDate"
    LOCAL_TIME = "LocalTime"
    ARRAY = "Array"
    INLINE_TABLE = "InlineTable"
    ROOT_TABLE = "RootTable"
    STANDARD_TABLE = "StandardTable"
    IMPLICIT_TABLE = "ImplicitTable"
    DOTTED_TABLE = "DottedTable"
    ARRAY_OF_TABLES = "ArrayOfTables"


class TableFlavor(enum.Enum):
    """Internal table flavor deciding the public category
    (lib.rs:639-645 and parser.rs:232-241)."""

    ROOT = "Root"
    STANDARD = "Standard"
    IMPLICIT = "Implicit"
    DOTTED = "Dotted"


@dataclass(frozen=True, slots=True)
class TomlDate:
    """Parsed TOML date fields (lib.rs:307-316)."""

    year: int
    month: int
    day: int


@dataclass(frozen=True, slots=True)
class TomlTime:
    """Parsed TOML time fields (lib.rs:317-329); the fractional second is
    truncated to nanoseconds by the profile parser."""

    hour: int
    minute: int
    second: int
    nanosecond: int


class TomlOffset(enum.Enum):
    """Parsed TOML UTC offset (lib.rs:331-338)."""

    Z = "Z"
    CUSTOM_MINUTES = "CustomMinutes"


@dataclass(frozen=True, slots=True)
class TomlDateTime:
    """Complete native TOML date/time datum (lib.rs:340-349)."""

    date: TomlDate | None = None
    time: TomlTime | None = None
    offset_minutes: int | None = None


# ---------------------------------------------------------------------------
# Entity model (parser.rs:577-663)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _InternalItemKind:
    """The closed internal item payload vocabulary (lib.rs:597-610)."""

    name: str
    value: object = None
    flavor: TableFlavor | None = None
    children: tuple[int, ...] = ()

    @classmethod
    def string(cls, value: str) -> _InternalItemKind:
        return cls(name="String", value=value)

    @classmethod
    def integer(cls, value: int) -> _InternalItemKind:
        return cls(name="Integer", value=value)

    @classmethod
    def float_bits(cls, value: int) -> _InternalItemKind:
        return cls(name="Float", value=value)

    @classmethod
    def boolean(cls, value: bool) -> _InternalItemKind:
        return cls(name="Boolean", value=value)

    @classmethod
    def date_time(cls, value: TomlDateTime) -> _InternalItemKind:
        return cls(name="DateTime", value=value)

    @classmethod
    def array(cls, elements: list[int]) -> _InternalItemKind:
        return cls(name="Array", children=tuple(elements))

    @classmethod
    def inline_table(cls, entries: list[int]) -> _InternalItemKind:
        return cls(name="InlineTable", children=tuple(entries))

    @classmethod
    def table(cls, flavor: TableFlavor, entries: list[int]) -> _InternalItemKind:
        return cls(name="Table", flavor=flavor, children=tuple(entries))

    @classmethod
    def array_of_tables(cls, elements: list[int]) -> _InternalItemKind:
        return cls(name="ArrayOfTables", children=tuple(elements))

    def public_kind(self) -> TomlItemKind:
        """Public category (lib.rs:612-637)."""
        if self.name == "String":
            return TomlItemKind.STRING
        if self.name == "Integer":
            return TomlItemKind.INTEGER
        if self.name == "Float":
            return TomlItemKind.FLOAT
        if self.name == "Boolean":
            return TomlItemKind.BOOLEAN
        if self.name == "DateTime":
            assert isinstance(self.value, TomlDateTime)
            if self.value.date is not None and self.value.time is not None and self.value.offset_minutes is not None:
                return TomlItemKind.OFFSET_DATE_TIME
            if self.value.date is not None and self.value.time is not None:
                return TomlItemKind.LOCAL_DATE_TIME
            if self.value.date is not None:
                return TomlItemKind.LOCAL_DATE
            return TomlItemKind.LOCAL_TIME
        if self.name == "Array":
            return TomlItemKind.ARRAY
        if self.name == "InlineTable":
            return TomlItemKind.INLINE_TABLE
        if self.name == "ArrayOfTables":
            return TomlItemKind.ARRAY_OF_TABLES
        if self.flavor is TableFlavor.ROOT:
            return TomlItemKind.ROOT_TABLE
        if self.flavor is TableFlavor.STANDARD:
            return TomlItemKind.STANDARD_TABLE
        if self.flavor is TableFlavor.IMPLICIT:
            return TomlItemKind.IMPLICIT_TABLE
        return TomlItemKind.DOTTED_TABLE


@dataclass(frozen=True, slots=True)
class _KeyEntity:
    name: str


@dataclass(frozen=True, slots=True)
class _EntryEntity:
    ordinal: int
    key: int
    item: int


@dataclass(frozen=True, slots=True)
class _ElementEntity:
    ordinal: int
    item: int


@dataclass(frozen=True, slots=True)
class _Entity:
    span: Span
    kind: object  # _ItemEntity | _EntryEntity | _KeyEntity | _ElementEntity


@dataclass(frozen=True, slots=True)
class _ItemEntity:
    kind: _InternalItemKind


class TomlAccessErrorKind(enum.Enum):
    """Stable TOML native handle failure (lib.rs:261-270)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    UNKNOWN_NODE = "UnknownNode"


class TomlAccessError(Exception):
    """Stable TOML native handle failure (lib.rs:261-270). Carries no
    registered code; location errors are internal format-layer errors."""

    def __init__(self, kind: TomlAccessErrorKind) -> None:
        super().__init__(kind.value)
        self.kind = kind


# ---------------------------------------------------------------------------
# Native handles
# ---------------------------------------------------------------------------


class TomlItem:
    """Borrowed native TOML item bound to one document snapshot
    (lib.rs:351-459)."""

    __slots__ = ("document", "index")

    def __init__(self, document: "Document", index: int):
        self.document = document
        self.index = index

    def node_ref(self) -> NodeRef:
        """Exact item identity (lib.rs:360-363)."""
        return self.document.node_ref(self.index, NodeRole.TOML_ITEM)

    def span(self) -> Span:
        """Exact or contract-authorized logical source span (lib.rs:365-369)."""
        return self.document._entity(self.index).span

    def kind(self) -> TomlItemKind:
        """Native item category (lib.rs:371-375)."""
        return self.document._item_entity(self.index).kind.public_kind()

    def as_string(self) -> str | None:
        kind = self.document._item_entity(self.index).kind
        return kind.value if kind.name == "String" else None

    def as_integer(self) -> int | None:
        kind = self.document._item_entity(self.index).kind
        return kind.value if kind.name == "Integer" else None

    def as_float_bits(self) -> int | None:
        """Exact IEEE-754 binary64 bit pattern (lib.rs:395-402)."""
        kind = self.document._item_entity(self.index).kind
        return kind.value if kind.name == "Float" else None

    def as_boolean(self) -> bool | None:
        kind = self.document._item_entity(self.index).kind
        return kind.value if kind.name == "Boolean" else None

    def as_date_time(self) -> TomlDateTime | None:
        """Native temporal datum for any TOML date/time category
        (lib.rs:413-420)."""
        kind = self.document._item_entity(self.index).kind
        return kind.value if kind.name == "DateTime" else None

    def table_entries(self) -> list["TomlEntry"] | None:
        """Direct ordered entries for any table category or inline table
        (lib.rs:422-439)."""
        kind = self.document._item_entity(self.index).kind
        if kind.name not in ("Table", "InlineTable"):
            return None
        return [TomlEntry(self.document, index) for index in kind.children]

    def array_elements(self) -> list["TomlArrayElement"] | None:
        """Direct ordered elements for arrays and arrays-of-tables
        (lib.rs:441-458)."""
        kind = self.document._item_entity(self.index).kind
        if kind.name not in ("Array", "ArrayOfTables"):
            return None
        return [TomlArrayElement(self.document, index) for index in kind.children]


class TomlEntry:
    """Borrowed direct table entry association (lib.rs:461-524)."""

    __slots__ = ("document", "index")

    def __init__(self, document: "Document", index: int):
        self.document = document
        self.index = index

    def _entity(self) -> _EntryEntity:
        entity = self.document._entity(self.index)
        assert isinstance(entity.kind, _EntryEntity)
        return entity.kind

    def ordinal(self) -> int:
        """Zero-based direct entry ordinal (lib.rs:476-480)."""
        return self._entity().ordinal

    def node_ref(self) -> NodeRef:
        """Association identity (lib.rs:482-486)."""
        return self.document.node_ref(self.index, NodeRole.TOML_ENTRY)

    def key_node_ref(self) -> NodeRef:
        """Direct key segment identity (lib.rs:488-492)."""
        return self.document.node_ref(self._entity().key, NodeRole.TOML_KEY)

    def item_node_ref(self) -> NodeRef:
        """Associated item identity (lib.rs:494-499)."""
        return self.document.node_ref(self._entity().item, NodeRole.TOML_ITEM)

    def span(self) -> Span:
        """Association source span (lib.rs:501-505)."""
        return self.document._entity(self.index).span

    def name(self) -> str:
        """Decoded direct key segment without normalization (lib.rs:507-514)."""
        key = self.document._entity(self._entity().key).kind
        assert isinstance(key, _KeyEntity)
        return key.name

    def key_span(self) -> Span:
        """Exact source span of the direct key segment."""
        return self.document._entity(self._entity().key).span

    def item(self) -> TomlItem:
        """Associated native item (lib.rs:516-523)."""
        return TomlItem(self.document, self._entity().item)


class TomlArrayElement:
    """Borrowed array or array-of-tables element association (lib.rs:526-575)."""

    __slots__ = ("document", "index")

    def __init__(self, document: "Document", index: int):
        self.document = document
        self.index = index

    def _entity(self) -> _ElementEntity:
        entity = self.document._entity(self.index)
        assert isinstance(entity.kind, _ElementEntity)
        return entity.kind

    def ordinal(self) -> int:
        """Zero-based direct element ordinal (lib.rs:541-545)."""
        return self._entity().ordinal

    def node_ref(self) -> NodeRef:
        """Association identity (lib.rs:547-552)."""
        return self.document.node_ref(self.index, NodeRole.TOML_ARRAY_ELEMENT)

    def item_node_ref(self) -> NodeRef:
        """Associated item identity (lib.rs:554-559)."""
        return self.document.node_ref(self._entity().item, NodeRole.TOML_ITEM)

    def span(self) -> Span:
        """Association source span (lib.rs:561-565)."""
        return self.document._entity(self.index).span

    def item(self) -> TomlItem:
        """Associated native item (lib.rs:567-574)."""
        return TomlItem(self.document, self._entity().item)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class Document:
    """Opaque immutable TOML document snapshot (lib.rs:130-259).

    Formed by :func:`consema.toml.parser.parse`; default rendering is
    byte-for-byte identical to the source. All native handles and spans are
    bound to this snapshot's identity.
    """

    def __init__(
        self,
        authority: DocumentAuthority,
        source: SourceSnapshot,
        profile: TomlProfile,
        structural_index: LosslessStructuralIndex,
        syntax_kinds: list[TomlSyntaxKind],
        entities: list[_Entity],
        root: int,
        parse_limits: ParseLimits,
    ) -> None:
        self._authority = authority
        self._source = source
        self._profile = profile
        self._structural_index = structural_index
        self._syntax_kinds = tuple(syntax_kinds)
        self._entities = tuple(entities)
        self._root = root
        self._parse_limits = parse_limits

    # -- identity and source ---------------------------------------------

    def snapshot_identity(self) -> SnapshotIdentity:
        """Snapshot identity to which every native handle and span belongs
        (lib.rs:145-149)."""
        return self._authority.identity

    def source(self) -> SourceSnapshot:
        """Exact immutable UTF-8 source (lib.rs:151-155)."""
        return self._source

    def render(self) -> bytes:
        """Default rendering is byte-for-byte identical to the source
        (lib.rs:157-161)."""
        return self._source.bytes()

    def format_family(self) -> FormatFamilyId:
        """TOML format family contract (lib.rs:163-167)."""
        return FormatFamilyId.new("toml", 1)

    def profile(self) -> ProfileId:
        """Exact language profile (lib.rs:169-173)."""
        return self._profile.profile_id()

    def formation_status(self) -> FormationStatus:
        """TOML forms only complete valid documents (lib.rs:175-179)."""
        return FormationStatus.COMPLETE

    def diagnostics(self) -> tuple:
        """Deterministically ordered non-fatal diagnostics; TOML 1.0
        formation has none (lib.rs:181-185)."""
        return ()

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive token/trivia byte coverage (lib.rs:187-191)."""
        return self._structural_index

    def lossless_syntax_kinds(self) -> tuple[TomlSyntaxKind, ...]:
        """Format-specific kind for every structural piece in source order
        (lib.rs:193-197)."""
        return self._syntax_kinds

    def parse_limits(self) -> ParseLimits:
        """Resource contract used to form this snapshot and any edit
        successor (lib.rs:199-203)."""
        return self._parse_limits

    def root(self) -> TomlItem:
        """Root native item, always a RootTable (lib.rs:205-212)."""
        return TomlItem(self, self._root)

    def item(self, node: NodeRef) -> TomlItem:
        """Resolves a snapshot-bound TOML item handle (lib.rs:214-224)."""
        index = self._validate_ref(node, NodeRole.TOML_ITEM)
        entity = self._entities[index]
        assert isinstance(entity.kind, _ItemEntity)
        return TomlItem(self, index)

    # -- internal accessors ----------------------------------------------

    def node_ref(self, index: int, role: NodeRole) -> NodeRef:
        return self._authority.node_ref(index, role)

    def span(self, start_byte: int, end_byte: int) -> Span:
        return self._authority.span(start_byte, end_byte)

    def _entity(self, index: int) -> _Entity:
        return self._entities[index]

    def _item_entity(self, index: int) -> _ItemEntity:
        entity = self._entities[index]
        assert isinstance(entity.kind, _ItemEntity)
        return entity.kind

    def _validate_ref(self, node: NodeRef, role: NodeRole) -> int:
        if node.snapshot != self.snapshot_identity():
            raise TomlAccessError(TomlAccessErrorKind.WRONG_SNAPSHOT)
        if node.role is not role:
            raise TomlAccessError(TomlAccessErrorKind.WRONG_ROLE)
        if node.index >= len(self._entities):
            raise TomlAccessError(TomlAccessErrorKind.UNKNOWN_NODE)
        return node.index

    # -- explicit operations ---------------------------------------------

    def project(self, request: "object"):
        """Applies an immutable explicit projection request
        (projection.rs:202-227). See consema.toml.projection."""
        from consema.toml import projection

        return projection.project_document(self, request)

    def commit(self, transaction: "object"):
        """Atomically commits scalar and structural operations; a failure
        never changes this snapshot (edit.rs:281-430)."""
        from consema.toml import edits

        return edits.commit_document(self, transaction)

    def dry_run(self, transaction: "object", source_id: "object"):
        """Fully validates and plans an edit without returning a new
        Document (edit.rs:432-447)."""
        from consema.toml import edits

        return edits.dry_run_document(self, transaction, source_id)
