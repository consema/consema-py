"""Structural locations, formation status, and exhaustive source coverage.

Authority (language-neutral first; Rust only for arbitration):

- RFC 0016 §5.1 (docs/rfcs/0016-go-api-mapping-v1.md:171-176): FormStatus is
  a closed two-value enum (Complete, Recovered) — the 0.13.0 F10 disposition.
- RFC 0003 §5 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:124-141):
  Span is the half-open [start_byte, end_byte) range over original raw bytes;
  §7 (lines 162-171): text Documents retain exhaustive ordered
  Token/Trivia/ErrorRegion coverage; binary Documents use a
  BinaryStructuralIndex with snapshot-bound raw Span, process-local NodeRef,
  and non-empty format-owned region kind; no-gap/no-overlap/final-length
  invariant; empty source has an empty valid index.
- crates/consema-document/src/lib.rs — arbitration: SnapshotIdentity
  lib.rs:41-51; DocumentAuthority lib.rs:54-110; the closed NodeRole
  vocabulary lib.rs:113-251; NodeRef lib.rs:254-292; Span lib.rs:295-342;
  AssociationPlacement lib.rs:262-272; FormationStatus lib.rs:405-411;
  StructuralPieceKind/StructuralPiece lib.rs:414-449; LosslessStructuralIndex
  lib.rs:452-490; BinaryRegion/BinaryStructuralIndex lib.rs:493-579;
  LocationError lib.rs:582-604.

Vector coverage: conformance/vectors/source-v1.json cases
``source.binary.empty-coverage`` (lines 101-106), ``source.binary.region-
coverage`` (lines 107-112), ``source.binary.reject-gap`` (lines 113-118) —
their expected values reference the LocationError variant names
("NoDecodedText", "IncompleteStructuralCoverage").

go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field

_SNAPSHOT_COUNTER = itertools.count(1)


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    """Opaque identity of exactly one immutable document snapshot
    (crates/consema-document/src/lib.rs:41-51; RFC 0003 §3, lines 60-62).

    A fresh opaque process-local identity for every formed Document; parsing
    the same bytes twice produces equal content digests and distinct snapshot
    identities. SnapshotIdentity is never serialized.
    """

    _value: int = field(repr=False)

    @property
    def as_u64(self) -> int:
        """Stable process-local representation for protocol diagnostics."""
        return self._value


@dataclass(frozen=True, slots=True)
class DocumentAuthority:
    """Authority owned by one document implementation for issuing
    snapshot-bound handles (lib.rs:54-110).

    Allocates fresh snapshot identities, issues opaque node handles, and
    validates snapshot-bound spans.
    """

    identity: SnapshotIdentity

    @classmethod
    def fresh(cls) -> DocumentAuthority:
        """Allocates a fresh snapshot identity (lib.rs:61-65)."""
        return cls(identity=SnapshotIdentity(next(_SNAPSHOT_COUNTER)))

    def node_ref(self, index: int, role: NodeRole) -> NodeRef:
        """Issues one opaque node handle (lib.rs:75-81)."""
        return NodeRef(snapshot=self.identity, index=index, role=role)

    def span(self, start_byte: int, end_byte: int) -> Span:
        """Creates a snapshot-bound span after range validation (lib.rs:84-93)."""
        if start_byte > end_byte:
            raise LocationError(LocationErrorKind.INVERTED_SPAN)
        return Span(snapshot=self.identity, start_byte=start_byte, end_byte=end_byte)

    def verify(self, node: NodeRef) -> None:
        """Verifies that a node handle belongs to this snapshot (lib.rs:96-102)."""
        if node.snapshot != self.identity:
            raise LocationError(LocationErrorKind.WRONG_SNAPSHOT)


class NodeRole(enum.Enum):
    """Semantic role of a document structural identity
    (crates/consema-document/src/lib.rs:113-251 — closed vocabulary,
    transcribed verbatim)."""

    SYNTAX_NODE = "SyntaxNode"
    TOKEN = "Token"
    OBJECT_MEMBER = "ObjectMember"
    OBJECT_KEY = "ObjectKey"
    ARRAY_ELEMENT = "ArrayElement"
    VALUE = "Value"
    TOML_ITEM = "TomlItem"
    TOML_ENTRY = "TomlEntry"
    TOML_KEY = "TomlKey"
    TOML_ARRAY_ELEMENT = "TomlArrayElement"
    BINARY_REGION = "BinaryRegion"
    JSON_SYNTAX_PIECE = "JsonSyntaxPiece"
    TOML_SYNTAX_PIECE = "TomlSyntaxPiece"
    YAML_STREAM = "YamlStream"
    YAML_DOCUMENT = "YamlDocument"
    YAML_NODE = "YamlNode"
    YAML_SEQUENCE_ELEMENT = "YamlSequenceElement"
    YAML_MAPPING_ENTRY = "YamlMappingEntry"
    YAML_ALIAS = "YamlAlias"
    YAML_ANCHOR_DEFINITION = "YamlAnchorDefinition"
    YAML_SYNTAX_PIECE = "YamlSyntaxPiece"
    INI_DOCUMENT = "IniDocument"
    INI_PHYSICAL_LINE = "IniPhysicalLine"
    INI_LOGICAL_LINE = "IniLogicalLine"
    INI_SECTION = "IniSection"
    INI_DEFAULT_SECTION = "IniDefaultSection"
    INI_ENTRY = "IniEntry"
    INI_ERROR_LINE = "IniErrorLine"
    INI_SYNTAX_PIECE = "IniSyntaxPiece"
    PROPERTIES_DOCUMENT = "PropertiesDocument"
    PROPERTIES_NATURAL_LINE = "PropertiesNaturalLine"
    PROPERTIES_LOGICAL_LINE = "PropertiesLogicalLine"
    PROPERTIES_PROPERTY = "PropertiesProperty"
    PROPERTIES_COMMENT = "PropertiesComment"
    PROPERTIES_ESCAPE = "PropertiesEscape"
    PROPERTIES_ERROR_LINE = "PropertiesErrorLine"
    PROPERTIES_SYNTAX_PIECE = "PropertiesSyntaxPiece"
    XML_DOCUMENT = "XmlDocument"
    XML_DECLARATION = "XmlDeclaration"
    XML_DOCTYPE = "XmlDoctype"
    XML_ELEMENT = "XmlElement"
    XML_ATTRIBUTE = "XmlAttribute"
    XML_NAMESPACE_BINDING = "XmlNamespaceBinding"
    XML_TEXT = "XmlText"
    XML_CDATA = "XmlCdata"
    XML_COMMENT = "XmlComment"
    XML_PROCESSING_INSTRUCTION = "XmlProcessingInstruction"
    XML_ENTITY_REFERENCE = "XmlEntityReference"
    XML_ERROR_REGION = "XmlErrorRegion"
    XML_SYNTAX_PIECE = "XmlSyntaxPiece"
    PLIST_DOCUMENT = "PlistDocument"
    PLIST_DICT_ENTRY = "PlistDictEntry"
    PLIST_KEY = "PlistKey"
    PLIST_ARRAY_ELEMENT = "PlistArrayElement"
    PLIST_VALUE = "PlistValue"
    PLIST_SYNTAX_PIECE = "PlistSyntaxPiece"
    HCL_DOCUMENT = "HclDocument"
    HCL_BODY = "HclBody"
    HCL_ATTRIBUTE = "HclAttribute"
    HCL_BLOCK = "HclBlock"
    HCL_BLOCK_LABEL = "HclBlockLabel"
    HCL_EXPRESSION = "HclExpression"
    HCL_TEMPLATE_PART = "HclTemplatePart"
    HCL_ERROR_REGION = "HclErrorRegion"
    HCL_SYNTAX_PIECE = "HclSyntaxPiece"


@dataclass(frozen=True, slots=True)
class NodeRef:
    """Opaque handle to one structural identity in exactly one snapshot
    (lib.rs:254-292).

    Carries the owning snapshot identity, a process-local ordinal, and the
    structural role; identity is never cross-snapshot. The three fields are
    the accessors (snapshot, index, role).
    """

    snapshot: SnapshotIdentity
    index: int
    role: NodeRole


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open byte range bound to one snapshot (lib.rs:295-342).

    [start_byte, end_byte) over original raw bytes; offsets never become
    UTF-8 indices after decoding UTF-16 or Latin-1 (RFC 0003 §5,
    lines 126-127).
    """

    snapshot: SnapshotIdentity
    start_byte: int
    end_byte: int

    def len(self) -> int:
        """Byte length."""
        return self.end_byte - self.start_byte

    def is_empty(self) -> bool:
        """Whether the range is an insertion point."""
        return self.start_byte == self.end_byte


@dataclass(frozen=True, slots=True)
class AssociationPlacement:
    """Placement of a new association relative to one container or exact anchor
    (lib.rs:262-272)."""

    kind: str  # "Start" | "End" | "Before" | "After"
    anchor: NodeRef | None = None


class FormationStatus(enum.Enum):
    """Successful document formation state (lib.rs:405-411; RFC 0016 §5.1).

    A closed two-value enum: Complete (entire syntax formed without recovery)
    and Recovered (a complete snapshot with explicit recovery structure was
    formed). The 0.13.0 F10 disposition pins this as the only formation
    status surface.
    """

    COMPLETE = "Complete"
    RECOVERED = "Recovered"


class StructuralPieceKind(enum.Enum):
    """One exhaustive source-byte classification (lib.rs:414-422)."""

    TOKEN = "Token"
    TRIVIA = "Trivia"
    ERROR_REGION = "ErrorRegion"


@dataclass(frozen=True, slots=True)
class StructuralPiece:
    """One source byte interval and its lossless class (lib.rs:425-449)."""

    span: Span
    kind: StructuralPieceKind


@dataclass(frozen=True, slots=True)
class LosslessStructuralIndex:
    """Exhaustive ordered token/trivia/error-region coverage (lib.rs:452-490;
    RFC 0003 §7)."""

    pieces: tuple[StructuralPiece, ...]

    @classmethod
    def new(
        cls,
        identity: SnapshotIdentity,
        source_len: int,
        pieces: list[StructuralPiece],
    ) -> LosslessStructuralIndex:
        """Validates exact source coverage and stores pieces in structural
        order (lib.rs:459-483)."""
        next_byte = 0
        for piece in pieces:
            if piece.span.snapshot != identity:
                raise LocationError(LocationErrorKind.WRONG_SNAPSHOT)
            if (
                piece.span.start_byte != next_byte
                or piece.span.end_byte <= piece.span.start_byte
                or piece.span.end_byte > source_len
            ):
                raise LocationError(LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE)
            next_byte = piece.span.end_byte
        if next_byte != source_len:
            raise LocationError(LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE)
        return cls(pieces=tuple(pieces))


@dataclass(frozen=True, slots=True)
class BinaryRegion:
    """One format-owned region in an opaque binary source (lib.rs:493-528)."""

    node: NodeRef
    span: Span
    kind: str


@dataclass(frozen=True, slots=True)
class BinaryStructuralIndex:
    """Exhaustive ordered format-owned region coverage for one opaque binary
    source (lib.rs:531-579; RFC 0003 §7, lines 162-171).

    Binary coverage obeys the same no-gap/no-overlap/final-length invariant
    but does not call bytes tokens or trivia. Empty source has an empty valid
    index; non-empty source requires at least one non-empty region.
    """

    regions: tuple[BinaryRegion, ...]

    @classmethod
    def new(
        cls,
        identity: SnapshotIdentity,
        source_len: int,
        regions: list[BinaryRegion],
    ) -> BinaryStructuralIndex:
        """Validates exact raw-byte coverage, snapshot binding, roles, kinds,
        and unique identities (lib.rs:538-572)."""
        next_byte = 0
        identities: set[NodeRef] = set()
        for region in regions:
            if region.span.snapshot != identity or region.node.snapshot != identity:
                raise LocationError(LocationErrorKind.WRONG_SNAPSHOT)
            if region.node.role is not NodeRole.BINARY_REGION:
                raise LocationError(LocationErrorKind.WRONG_ROLE)
            if not region.kind:
                raise LocationError(LocationErrorKind.INVALID_BINARY_REGION_KIND)
            if region.node in identities:
                raise LocationError(LocationErrorKind.DUPLICATE_STRUCTURAL_IDENTITY)
            identities.add(region.node)
            if (
                region.span.start_byte != next_byte
                or region.span.end_byte <= region.span.start_byte
                or region.span.end_byte > source_len
            ):
                raise LocationError(LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE)
            next_byte = region.span.end_byte
        if next_byte != source_len:
            raise LocationError(LocationErrorKind.INCOMPLETE_STRUCTURAL_COVERAGE)
        return cls(regions=tuple(regions))

    def region_count(self) -> int:
        """Number of ordered regions (vector field ``region_count``,
        conformance/vectors/source-v1.json:105,111)."""
        return len(self.regions)


class LocationErrorKind(enum.Enum):
    """Span, identity, or coverage failure (lib.rs:582-604).

    The variant names are the exact Rust spellings; the conformance vectors
    reference them by name (conformance/vectors/source-v1.json lines 99 and
    117: "NoDecodedText", "IncompleteStructuralCoverage").
    """

    INVERTED_SPAN = "InvertedSpan"
    WRONG_SNAPSHOT = "WrongSnapshot"
    INCOMPLETE_STRUCTURAL_COVERAGE = "IncompleteStructuralCoverage"
    OUT_OF_BOUNDS = "OutOfBounds"
    NO_DECODED_TEXT = "NoDecodedText"
    NOT_DECODED_BOUNDARY = "NotDecodedBoundary"
    DECODED_OFFSET_NOT_BOUNDARY = "DecodedOffsetNotBoundary"
    WRONG_ROLE = "WrongRole"
    INVALID_BINARY_REGION_KIND = "InvalidBinaryRegionKind"
    DUPLICATE_STRUCTURAL_IDENTITY = "DuplicateStructuralIdentity"


class LocationError(Exception):
    """Span, identity, or coverage failure (lib.rs:582-604).

    Location failures are internal (format-layer) errors; they carry no
    registered error code — the registered source codes belong to content
    construction and patch application (RFC 0003 §11, lines 307-309). Error
    text is human presentation only.
    """

    def __init__(self, kind: LocationErrorKind) -> None:
        super().__init__(kind.value)
        self.kind = kind

    @property
    def name(self) -> str:
        """Exact variant spelling referenced by the conformance vectors."""
        return self.kind.value
