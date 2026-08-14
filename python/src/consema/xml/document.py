"""Immutable namespace-aware native XML tree (RFC 0012 §4-7).

Authority:

- RFC 0012 §4-§7 (https://github.com/consema/consema/blob/main/docs/rfcs/0012-xml-1.0-safe-profile-v1.md):
  the immutable Document retains prolog order, one document element, epilog
  order, and every exact source span; the native roles
  (XmlDocument/XmlDeclaration/XmlDoctype/XmlElement/XmlAttribute/
  XmlNamespaceBinding/XmlText/XmlCdata/XmlComment/XmlProcessingInstruction/
  XmlEntityReference/XmlErrorRegion/XmlSyntaxPiece) are snapshot-bound;
  one lexical QName retains prefix/local/spans/resolved expanded name;
  namespace declarations are ordered native associations with independent
  identity; text and attribute values retain ordered fragments
  (Literal/CharacterReference/PredefinedEntityReference/
  GeneralEntityReference) with line-end-normalized semantic content;
  adjacent Text occurrences are never merged across markup boundaries.
- The record shapes transcribe https://github.com/consema/consema-rs/blob/main/consema-xml/src/document.rs
  (QNameFacts:96-120, ReferenceFragment:135-172, XmlNamespaceBindingData:
  XmlAttributeData, XmlTextData, XmlCdataData
  XmlCommentData, XmlPiData, XmlErrorRegionData
  XmlElementData, XmlContent, XmlPrologItem
  XmlDeclarationData, EntityDeclarationData,
  XmlDoctypeData:375-387, Document:388-568) — byte/registry arbitration
  only; this module is a Python-idiomatic reimplementation.
- Text semantic concatenation and line-end normalization transcribe
  document.rs (text_semantic, push_normalized).
- Snapshot-bound identity roles are the closed consema.document NodeRole
  vocabulary (document/structural.py).

https://github.com/consema/consema-go/blob/main/go/xml is a cross-reference only; no code structure is copied.
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
    StructuralPiece,
    StructuralPieceKind,
)

from consema.xml.kinds import XmlSyntaxKind
from consema.xml.namespaces import ExpandedName, NamespaceError, NamespaceScope, QName


class XmlProfile(enum.Enum):
    """Frozen XML formation profiles (https://github.com/consema/consema-rs/blob/main/consema-xml/src/lib.rs)."""

    SAFE_V1 = "xml.1.0-safe@1"

    def profile_id(self) -> ProfileId:
        """Stable profile identifier (lib.rs)."""
        return ProfileId.new("xml.1.0-safe", 1)


@dataclass(frozen=True, slots=True)
class QNameFacts:
    """One lexical QName with its source-derived facts (document.rs)."""

    prefix: str | None
    local: str
    span: Span
    prefix_span: Span | None
    local_span: Span

    def qname(self) -> QName:
        """The plain lexical QName (document.rs)."""
        return QName(prefix=self.prefix, local=self.local)


class ReferenceFragmentKind(enum.Enum):
    """Closed fragment category (document.rs)."""

    LITERAL = "literal"
    CHARACTER_REFERENCE = "character-reference"
    PREDEFINED_ENTITY = "predefined-entity"
    GENERAL_ENTITY = "general-entity"


@dataclass(frozen=True, slots=True)
class ReferenceFragment:
    """One ordered text or attribute-value fragment (document.rs)."""

    kind: ReferenceFragmentKind
    span: Span
    text: str | None = None
    name: str | None = None
    resolved: str | None = None
    declaration_span: Span | None = None

    @classmethod
    def literal(cls, span: Span, text: str) -> ReferenceFragment:
        return cls(kind=ReferenceFragmentKind.LITERAL, span=span, text=text)

    @classmethod
    def character_reference(cls, span: Span, resolved: str) -> ReferenceFragment:
        return cls(kind=ReferenceFragmentKind.CHARACTER_REFERENCE, span=span, resolved=resolved)

    @classmethod
    def predefined_entity(cls, span: Span, name: str, resolved: str) -> ReferenceFragment:
        return cls(
            kind=ReferenceFragmentKind.PREDEFINED_ENTITY, span=span, name=name, resolved=resolved
        )

    @classmethod
    def general_entity(
        cls, span: Span, name: str, resolved: str, declaration_span: Span
    ) -> ReferenceFragment:
        return cls(
            kind=ReferenceFragmentKind.GENERAL_ENTITY,
            span=span,
            name=name,
            resolved=resolved,
            declaration_span=declaration_span,
        )


@dataclass(frozen=True, slots=True)
class XmlNamespaceBindingData:
    """One XML namespace declaration association (document.rs)."""

    ordinal: int
    span: Span
    prefix: str | None
    uri_span: Span
    uri: str


@dataclass(frozen=True, slots=True)
class XmlAttributeData:
    """One XML attribute association (document.rs)."""

    ordinal: int
    span: Span
    qname: QNameFacts
    expanded: ExpandedName | None
    namespace_error: NamespaceError | None
    single_quote: bool
    value_span: Span
    fragments: tuple[ReferenceFragment, ...]
    normalized_value: str


@dataclass(frozen=True, slots=True)
class XmlTextData:
    """One text occurrence with ordered fragments (document.rs)."""

    ordinal: int
    span: Span
    fragments: tuple[ReferenceFragment, ...]


@dataclass(frozen=True, slots=True)
class XmlCdataData:
    """One CDATA occurrence (document.rs); content is never
    entity-expanded."""

    ordinal: int
    span: Span
    text_span: Span
    text: str


@dataclass(frozen=True, slots=True)
class XmlCommentData:
    """One comment occurrence (document.rs); content is never
    entity-expanded."""

    ordinal: int
    span: Span
    text_span: Span
    text: str


@dataclass(frozen=True, slots=True)
class XmlPiData:
    """One processing instruction (document.rs); content is never
    entity-expanded."""

    ordinal: int
    span: Span
    target_span: Span
    target: str
    content: tuple[Span, str] | None = None


@dataclass(frozen=True, slots=True)
class XmlErrorRegionData:
    """One recovered error region (document.rs)."""

    ordinal: int
    span: Span


@dataclass(frozen=True, slots=True)
class XmlElementData:
    """One element occurrence (document.rs)."""

    index: int
    span: Span
    qname: QNameFacts
    expanded: ExpandedName | None
    namespace_error: NamespaceError | None
    scope: NamespaceScope
    namespaces: tuple[XmlNamespaceBindingData, ...]
    attributes: tuple[XmlAttributeData, ...]
    children: tuple[int, ...]


class XmlContentKind(enum.Enum):
    """Closed child content category (document.rs)."""

    ELEMENT = "element"
    TEXT = "text"
    CDATA = "cdata"
    COMMENT = "comment"
    PROCESSING_INSTRUCTION = "processing-instruction"
    ERROR_REGION = "error-region"


@dataclass(frozen=True, slots=True)
class XmlContent:
    """One child content occurrence (document.rs); the payload is
    the family-specific record named by ``kind``."""

    kind: XmlContentKind
    data: object

    @property
    def span(self) -> Span:
        """Exact source span of this occurrence (document.rs)."""
        return {
            XmlContentKind.ELEMENT: lambda: self.data.span,
            XmlContentKind.TEXT: lambda: self.data.span,
            XmlContentKind.CDATA: lambda: self.data.span,
            XmlContentKind.COMMENT: lambda: self.data.span,
            XmlContentKind.PROCESSING_INSTRUCTION: lambda: self.data.span,
            XmlContentKind.ERROR_REGION: lambda: self.data.span,
        }[self.kind]()


class XmlPrologItemKind(enum.Enum):
    """Closed prolog/epilog occurrence category (document.rs)."""

    DECLARATION = "declaration"
    DOCTYPE = "doctype"
    PROCESSING_INSTRUCTION = "processing-instruction"
    COMMENT = "comment"
    BOM = "bom"
    WHITESPACE = "whitespace"


@dataclass(frozen=True, slots=True)
class XmlPrologItem:
    """One prolog or epilog occurrence (document.rs); payload is
    the record named by ``kind``, or a Span for Bom/Whitespace trivia."""

    kind: XmlPrologItemKind
    data: object


@dataclass(frozen=True, slots=True)
class XmlDeclarationData:
    """XML declaration facts (document.rs; RFC 0012 §2)."""

    span: Span
    version_span: Span
    version: str
    encoding: tuple[Span, str] | None = None
    standalone: tuple[Span, bool] | None = None


@dataclass(frozen=True, slots=True)
class EntityDeclarationData:
    """One admitted internal general entity declaration (document.rs)."""

    span: Span
    name: str
    replacement_span: Span
    replacement: str


@dataclass(frozen=True, slots=True)
class XmlDoctypeData:
    """DOCTYPE facts (document.rs; RFC 0012 §3)."""

    span: Span
    name: QNameFacts
    entities: tuple[EntityDeclarationData, ...]
    recovered: bool


class Document:
    """The immutable XML document (document.rs).

    The Document retains prolog order, one document element, epilog order,
    and every exact source span. The document element owns an ordered child
    content sequence; child items are never sorted or grouped by type.
    """

    __slots__ = (
        "_source",
        "_authority",
        "_status",
        "_declaration",
        "_doctype",
        "_prolog",
        "_root",
        "_epilog",
        "_syntax",
        "_syntax_kinds",
        "_diagnostics",
        "_nodes",
        "_parent_of",
        "_parse_limits",
    )

    def __init__(
        self,
        source: SourceSnapshot,
        authority: DocumentAuthority,
        status: FormationStatus,
        declaration: XmlDeclarationData | None,
        doctype: XmlDoctypeData | None,
        prolog: tuple[XmlPrologItem, ...],
        root: int | None,
        epilog: tuple[XmlPrologItem, ...],
        syntax: LosslessStructuralIndex,
        syntax_kinds: tuple[XmlSyntaxKind, ...],
        diagnostics: tuple[object, ...],
        nodes: tuple[XmlContent, ...],
        parent_of: tuple[int | None, ...],
        parse_limits: "XmlParseLimits",
    ) -> None:
        self._source = source
        self._authority = authority
        self._status = status
        self._declaration = declaration
        self._doctype = doctype
        self._prolog = prolog
        self._root = root
        self._epilog = epilog
        self._syntax = syntax
        self._syntax_kinds = syntax_kinds
        self._diagnostics = diagnostics
        self._nodes = nodes
        self._parent_of = parent_of
        self._parse_limits = parse_limits

    # -- formation facts ---------------------------------------------------

    @property
    def status(self) -> FormationStatus:
        """Formation status (document.rs)."""
        return self._status

    @property
    def formation_status(self) -> FormationStatus:
        return self._status

    def source(self) -> SourceSnapshot:
        """Immutable raw source (document.rs)."""
        return self._source

    def render(self) -> bytes:
        """Exact original bytes; unmodified rendering is byte-exact
        (document.rs)."""
        return self._source.bytes()

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive ordered lossless syntax coverage (document.rs)."""
        return self._syntax

    def lossless_syntax_kinds(self) -> tuple[XmlSyntaxKind, ...]:
        """Parallel format-owned syntax kind for every structural piece
        (document.rs)."""
        return self._syntax_kinds

    def diagnostics(self) -> tuple[object, ...]:
        """Ordered diagnostics from formation (document.rs)."""
        return self._diagnostics

    def declaration(self) -> XmlDeclarationData | None:
        return self._declaration

    def doctype(self) -> XmlDoctypeData | None:
        return self._doctype

    def prolog(self) -> tuple[XmlPrologItem, ...]:
        """Ordered prolog items before the document element."""
        return self._prolog

    def epilog(self) -> tuple[XmlPrologItem, ...]:
        """Ordered epilog items after the document element."""
        return self._epilog

    def root(self) -> XmlElement | None:
        """The one document element, when formation proved it
        (document.rs)."""
        if self._root is None:
            return None
        return XmlElement(self, self._root)

    def nodes(self) -> tuple[XmlContent, ...]:
        """All arena nodes; child content of every element is reachable here
        (document.rs)."""
        return self._nodes

    def snapshot_identity(self) -> SnapshotIdentity:
        """Snapshot identity (document.rs)."""
        return self._authority.identity

    def format_family(self) -> FormatFamilyId:
        """XML format family contract (document.rs)."""
        return FormatFamilyId.new("xml", 1)

    def profile(self) -> ProfileId:
        """Stable profile identifier (document.rs)."""
        return ProfileId.new("xml.1.0-safe", 1)

    def node_ref(self, index: int = 0, role: NodeRole = NodeRole.XML_DOCUMENT) -> NodeRef:
        """Snapshot-bound identity of one arena/ordinal-scoped occurrence
        (document.rs); the defaults address the whole document."""
        return self._authority.node_ref(index, role)

    def occurrence_node_ref(self, ordinal: int, role: NodeRole) -> NodeRef:
        """Snapshot-bound identity of one ordinal-scoped occurrence
        (document.rs)."""
        return self._authority.node_ref(ordinal, role)

    def parse_limits(self) -> "XmlParseLimits":
        """The parse limits this document was formed under."""
        return self._parse_limits

    # -- internal helpers (parser/query/edit/projection) --------------------

    def _element_data(self, index: int) -> XmlElementData:
        content = self._nodes[index]
        if content.kind is not XmlContentKind.ELEMENT:
            raise AssertionError("arena index is not an element")
        return content.data

    def _parent_of_index(self, index: int) -> int | None:
        if index >= len(self._parent_of):
            return None
        return self._parent_of[index]

    def _attributes(self):
        """All attribute associations in document order."""
        for content in self._nodes:
            if content.kind is XmlContentKind.ELEMENT:
                yield from content.data.attributes

    def _texts(self):
        for content in self._nodes:
            if content.kind is XmlContentKind.TEXT:
                yield content.data

    def _cdatas(self):
        for content in self._nodes:
            if content.kind is XmlContentKind.CDATA:
                yield content.data

    def _comments(self):
        for content in self._nodes:
            if content.kind is XmlContentKind.COMMENT:
                yield content.data

    def _pis(self):
        for content in self._nodes:
            if content.kind is XmlContentKind.PROCESSING_INSTRUCTION:
                yield content.data

    def _error_regions(self):
        for content in self._nodes:
            if content.kind is XmlContentKind.ERROR_REGION:
                yield content.data


class XmlElement:
    """Snapshot-bound element handle (document.rs)."""

    __slots__ = ("_owner", "_index")

    def __init__(self, owner: Document, index: int) -> None:
        self._owner = owner
        self._index = index

    @property
    def index(self) -> int:
        """Arena index for stable identity."""
        return self._index

    def node_ref(self) -> NodeRef:
        """Snapshot-bound stable identity (document.rs)."""
        return self._owner._authority.node_ref(self._index, NodeRole.XML_ELEMENT)

    def span(self) -> Span:
        """Full start-tag or empty-element span (document.rs)."""
        return self._data().span

    def qname(self) -> QNameFacts:
        """Lexical QName facts (document.rs)."""
        return self._data().qname

    def expanded(self) -> ExpandedName | None:
        """Resolved expanded name, when provable (document.rs)."""
        return self._data().expanded

    def namespace_bindings(self) -> tuple[XmlNamespaceBindingData, ...]:
        """Ordered namespace declarations on this element."""
        return self._data().namespaces

    def attributes(self) -> tuple[XmlAttributeData, ...]:
        """Ordered attributes, excluding namespace declarations."""
        return self._data().attributes

    def children(self) -> tuple[XmlContent, ...]:
        """Ordered child content occurrences; mixed-content order is
        retained (document.rs)."""
        return tuple(self._owner._nodes[index] for index in self._data().children)

    def is_empty(self) -> bool:
        """Whether the element has no child content."""
        return not self._data().children

    def _data(self) -> XmlElementData:
        return self._owner._element_data(self._index)


def text_semantic(data: XmlTextData) -> str:
    """Semantic concatenation of one text occurrence after XML line-end
    normalization to LF (document.rs; RFC 0012 §6)."""
    output: list[str] = []
    for fragment in data.fragments:
        if fragment.kind is ReferenceFragmentKind.LITERAL:
            assert fragment.text is not None
            output.append(_normalize_line_ends(fragment.text))
        elif fragment.kind is ReferenceFragmentKind.CHARACTER_REFERENCE:
            output.append(fragment.resolved or "")
        else:
            output.append(_normalize_line_ends(fragment.resolved or ""))
    return "".join(output)


def _normalize_line_ends(text: str) -> str:
    """XML 1.0 line-end normalization: CRLF and CR become LF
    (document.rs)."""
    output: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            output.append("\n")
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
        else:
            output.append(character)
        index += 1
    return "".join(output)
