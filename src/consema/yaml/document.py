"""The immutable YAML document and its snapshot-bound native views.

Authority (Rust arbitration for the public surface):

- Document fields and accessors: crates/consema-yaml/src/lib.rs:322-461 —
  snapshot identity, exact source, render() (byte-for-byte identical to the
  input, lib.rs:363-367), format family (lib.rs:369-373), profile
  (lib.rs:375-379), formation status (lib.rs:381-385: Complete streams
  publish no recovery diagnostics), lossless structural index
  (lib.rs:393-397), lossless syntax kinds (lib.rs:399-403), document
  (lib.rs:405-415), alias_count/alias (lib.rs:418-431), project_graph
  (lib.rs:433-448), document_count (lib.rs:450-454).
- Native view classes: lib.rs:463-787 — YamlDocument (ordinal, node_ref,
  span, root), YamlNode (node_ref, span, tag, anchor, anchor_node_ref,
  anchor_span, kind, scalar, sequence_len, sequence_item, mapping_len,
  mapping_entry), YamlScalar (decoded, canonical, kind, style),
  YamlSequenceItem (node_ref, span, node, alias), YamlMappingEntry
  (node_ref, span, key, value, key_alias, value_alias), YamlAlias
  (node_ref, span, name, target).
- Node roles: consema-document lib.rs:113-251 (YamlStream, YamlDocument,
  YamlNode, YamlSequenceElement, YamlMappingEntry, YamlAlias,
  YamlAnchorDefinition, YamlSyntaxPiece) — the same closed vocabulary the
  protocol query layer binds.

The document is logically immutable; every NodeRef and Span is bound to one
snapshot identity. Only Complete documents exist (RFC 0007 s4: syntax,
directive, tag, scalar-resolution, undefined-alias, and parse-limit failures
are fatal and return no Document; bounded recovery indexing is not published
by this implementation).
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
from consema.yaml.kinds import (
    YamlNodeKind,
    YamlProfile,
    YamlScalarKind,
    YamlScalarStyle,
    YamlSyntaxKind,
)
from consema.yaml.parser import (
    NativeMappingEntry,
    NativeNode,
    NativeScalar,
    NativeSequenceItem,
    node_ref as _node_ref,
)


@dataclass(frozen=True, slots=True)
class Document:
    """Complete immutable YAML stream snapshot (lib.rs:322-333)."""

    authority: DocumentAuthority
    source: SourceSnapshot
    profile: YamlProfile
    structural_index: LosslessStructuralIndex
    syntax_kinds: tuple[YamlSyntaxKind, ...]
    native: object
    stream_documents: int
    parse_limits: ParseLimits

    # -- identity and source -----------------------------------------------

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every NodeRef and Span belongs
        (lib.rs:351-355)."""
        return self.authority.identity

    def stream_node_ref(self) -> NodeRef:
        """Snapshot-bound identity of the complete serialization stream
        (lib.rs:337-341)."""
        return self.authority.node_ref(0, NodeRole.YAML_STREAM)

    def stream_span(self) -> Span:
        """Exact raw span of the complete serialization stream
        (lib.rs:343-349)."""
        return self.authority.span(0, self.source.len())

    def render(self) -> bytes:
        """Default rendering is byte-for-byte identical to the input
        (lib.rs:363-367)."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """YAML format-family contract (lib.rs:369-373)."""
        return FormatFamilyId.new("yaml", 1)

    def profile_id(self) -> ProfileId:
        """Exact selected YAML profile (lib.rs:375-379)."""
        name, version = self.profile.id()
        return ProfileId.new(name, version)

    def formation_status(self) -> FormationStatus:
        """Complete valid streams require no recovered semantic claims
        (lib.rs:381-385)."""
        return FormationStatus.COMPLETE

    def diagnostics(self) -> tuple:
        """Complete YAML formation publishes no recovery diagnostics
        (lib.rs:387-391)."""
        return ()

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive token/trivia byte coverage (lib.rs:393-397)."""
        return self.structural_index

    def lossless_syntax_kinds(self) -> tuple[YamlSyntaxKind, ...]:
        """Format-specific kind for every structural piece in source order
        (lib.rs:399-403)."""
        return self.syntax_kinds

    def document(self, ordinal: int) -> YamlDocument | None:
        """Returns one independent YAML document by stream ordinal
        (lib.rs:405-415)."""
        if 0 <= ordinal < len(self.native.documents):
            return YamlDocument(self, ordinal, self.native.documents[ordinal])
        return None

    def alias_count(self) -> int:
        """Number of alias serialization occurrences; aliases are never
        expanded (lib.rs:418-421)."""
        return len(self.native.aliases)

    def alias(self, ordinal: int) -> YamlAlias | None:
        """Returns one alias occurrence in serialization order
        (lib.rs:423-431)."""
        if 0 <= ordinal < len(self.native.aliases):
            return YamlAlias(self, ordinal, self.native.aliases[ordinal])
        return None

    def document_count(self) -> int:
        """Number of independent YAML documents in this stream
        (lib.rs:450-454)."""
        return self.stream_documents

    def parse_limits(self) -> ParseLimits:
        """Resource contract used to form this stream (lib.rs:456-460)."""
        return self.parse_limits


class YamlDocument:
    """One independent document in a YAML stream (lib.rs:463-501)."""

    __slots__ = ("owner", "ordinal", "record")

    def __init__(self, owner: Document, ordinal: int, record) -> None:
        self.owner = owner
        self.ordinal = ordinal
        self.record = record

    def node_ref(self) -> NodeRef:
        """Snapshot-bound document identity (lib.rs:478-485)."""
        return self.owner.authority.node_ref(self.ordinal, NodeRole.YAML_DOCUMENT)

    def span(self) -> Span:
        """Backend-validated raw document presentation span
        (lib.rs:487-491)."""
        return self.record.span

    def root(self) -> YamlNode:
        """Representation root; alias occurrences already share target
        identity (lib.rs:493-499)."""
        return YamlNode(self.owner, self.record.root)


class YamlNode:
    """Snapshot-bound YAML representation node (lib.rs:503-615)."""

    __slots__ = ("owner", "index")

    def __init__(self, owner: Document, index: int) -> None:
        self.owner = owner
        self.index = index

    def record(self) -> NativeNode:
        return self.owner.native.nodes[self.index]

    def node_ref(self) -> NodeRef:
        """Process-local stable identity within this snapshot
        (lib.rs:510-514)."""
        return _node_ref(self.owner.authority, self.index)

    def span(self) -> Span:
        """Exact raw representation occurrence span (lib.rs:516-521)."""
        return self.record().span

    def tag(self) -> str:
        """Resolved tag identifier (lib.rs:523-527)."""
        return self.record().tag

    def anchor(self) -> str | None:
        """Exact anchor name on the defining occurrence, if present
        (lib.rs:529-533)."""
        return self.record().anchor

    def anchor_node_ref(self) -> NodeRef | None:
        """Snapshot-bound anchor-definition identity, when this node
        defines one (lib.rs:535-547)."""
        if self.record().anchor is not None:
            return self.owner.authority.node_ref(self.index, NodeRole.YAML_ANCHOR_DEFINITION)
        return None

    def anchor_span(self) -> Span | None:
        """Exact raw ``&name`` span, when this node defines an anchor
        (lib.rs:549-553)."""
        return self.record().anchor_span

    def kind(self) -> YamlNodeKind:
        """Native node kind (lib.rs:555-563)."""
        content = self.record().content
        if isinstance(content, NativeScalar):
            return YamlNodeKind.SCALAR
        if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            return YamlNodeKind.SEQUENCE
        return YamlNodeKind.MAPPING

    def scalar(self) -> YamlScalar | None:
        """Scalar facts, when this is a scalar node (lib.rs:565-573)."""
        content = self.record().content
        if isinstance(content, NativeScalar):
            return YamlScalar(content)
        return None

    def sequence_len(self) -> int | None:
        """Ordered sequence association count (lib.rs:575-583)."""
        content = self.record().content
        if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            return len(content)
        return None

    def sequence_item(self, ordinal: int) -> YamlSequenceItem | None:
        """One exact sequence association (lib.rs:585-593)."""
        content = self.record().content
        if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            if 0 <= ordinal < len(content):
                return YamlSequenceItem(self.owner, content[ordinal])
        return None

    def mapping_len(self) -> int | None:
        """Ordered mapping association count (lib.rs:595-603)."""
        content = self.record().content
        if isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry):
            return len(content)
        return None

    def mapping_entry(self, ordinal: int) -> YamlMappingEntry | None:
        """One exact arbitrary key/value association (lib.rs:605-615)."""
        content = self.record().content
        if isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry):
            if 0 <= ordinal < len(content):
                return YamlMappingEntry(self.owner, content[ordinal])
        return None


class YamlScalar:
    """Native scalar facts with exact decoded and canonical content
    (lib.rs:617-647)."""

    __slots__ = ("scalar",)

    def __init__(self, scalar: NativeScalar) -> None:
        self.scalar = scalar

    def decoded(self) -> str:
        """Decoded YAML scalar content before schema canonicalization."""
        return self.scalar.decoded

    def canonical(self) -> str:
        """Profile-defined canonical scalar content."""
        return self.scalar.canonical

    def kind(self) -> YamlScalarKind:
        """Resolved scalar category."""
        return self.scalar.kind

    def style(self) -> YamlScalarStyle:
        """Source presentation style."""
        return self.scalar.style


class YamlSequenceItem:
    """One ordered sequence association (lib.rs:649-689)."""

    __slots__ = ("owner", "item")

    def __init__(self, owner: Document, item: NativeSequenceItem) -> None:
        self.owner = owner
        self.item = item

    def node_ref(self) -> NodeRef:
        """Snapshot-bound association identity (lib.rs:657-664)."""
        return self.owner.authority.node_ref(self.item.identity, NodeRole.YAML_SEQUENCE_ELEMENT)

    def span(self) -> Span:
        """Exact raw element occurrence span, including an alias spelling
        when used (lib.rs:666-670)."""
        return self.item.span

    def node(self) -> YamlNode:
        """Referenced representation node (lib.rs:672-678)."""
        return YamlNode(self.owner, self.item.node)

    def alias(self) -> YamlAlias | None:
        """Alias occurrence that supplied this element edge, when present
        (lib.rs:680-688)."""
        if self.item.alias is not None:
            return YamlAlias(self.owner, self.item.alias, self.owner.native.aliases[self.item.alias])
        return None


class YamlMappingEntry:
    """One ordered YAML mapping association with an arbitrary key node
    (lib.rs:691-749)."""

    __slots__ = ("owner", "entry")

    def __init__(self, owner: Document, entry: NativeMappingEntry) -> None:
        self.owner = owner
        self.entry = entry

    def node_ref(self) -> NodeRef:
        """Snapshot-bound association identity (lib.rs:699-706)."""
        return self.owner.authority.node_ref(self.entry.identity, NodeRole.YAML_MAPPING_ENTRY)

    def span(self) -> Span:
        """Raw span from the key occurrence through the value occurrence
        (lib.rs:708-712)."""
        return self.entry.span

    def key(self) -> YamlNode:
        """Arbitrary key node (lib.rs:714-720)."""
        return YamlNode(self.owner, self.entry.key)

    def value(self) -> YamlNode:
        """Value node (lib.rs:722-728)."""
        return YamlNode(self.owner, self.entry.value)

    def key_alias(self) -> YamlAlias | None:
        """Alias occurrence that supplied the key edge, when present
        (lib.rs:730-738)."""
        if self.entry.key_alias is not None:
            return YamlAlias(
                self.owner, self.entry.key_alias, self.owner.native.aliases[self.entry.key_alias]
            )
        return None

    def value_alias(self) -> YamlAlias | None:
        """Alias occurrence that supplied the value edge, when present
        (lib.rs:740-748)."""
        if self.entry.value_alias is not None:
            return YamlAlias(
                self.owner, self.entry.value_alias, self.owner.native.aliases[self.entry.value_alias]
            )
        return None


class YamlAlias:
    """One alias serialization occurrence pointing at an existing
    representation node (lib.rs:751-787)."""

    __slots__ = ("owner", "ordinal", "alias")

    def __init__(self, owner: Document, ordinal: int, alias) -> None:
        self.owner = owner
        self.ordinal = ordinal
        self.alias = alias

    def node_ref(self) -> NodeRef:
        """Snapshot-bound occurrence identity (lib.rs:759-766)."""
        return self.owner.authority.node_ref(self.alias.identity, NodeRole.YAML_ALIAS)

    def span(self) -> Span:
        """Exact raw ``*name`` occurrence span (lib.rs:768-772)."""
        return self.alias.span

    def name(self) -> str:
        """Exact alias name without ``*`` (lib.rs:774-778)."""
        return self.alias.name

    def target(self) -> YamlNode:
        """Shared target representation node; no expansion occurs
        (lib.rs:780-786)."""
        return YamlNode(self.owner, self.alias.target)
