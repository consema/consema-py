"""The immutable Java Properties document and its snapshot-bound records.

Authority (Rust arbitration for the public surface):

- Document fields and accessors: crates/consema-properties/src/lib.rs:
  590-775 - snapshot identity (lib.rs:613-615), exact source
  (lib.rs:618-621), render() (byte-for-byte source identity, lib.rs:624-628),
  format family java-properties@1 (lib.rs:631-633), profile (lib.rs:636-639),
  formation status (lib.rs:654-657), diagnostics (lib.rs:659-663), lossless
  structural index (lib.rs:665-669), syntax kinds (lib.rs:671-675),
  natural/logical lines, properties, comments, escapes, error lines
  (lib.rs:677-711), parse limits (lib.rs:713-717), and the snapshot-bound
  record resolvers property/natural_line/logical_line/escape
  (lib.rs:719-774).
- Record shapes: PropertiesNaturalLine (lib.rs:309-342), PropertiesLogicalLine
  (lib.rs:344-370), PropertiesComment (lib.rs:372-405), PropertiesEscape
  (lib.rs:407-455), Property (lib.rs:457-546), PropertiesErrorLine
  (lib.rs:548-588); the seven native roles are frozen by RFC 0010 section 9
  (docs/rfcs/0010-java-properties-profiles-v1.md:253-267) and the NodeRole
  vocabulary consema-document lib.rs:113-251.
- Spans/nodes/identity: consema-document (Span lib.rs:295-342, NodeRef
  lib.rs:254-292, DocumentAuthority lib.rs:54-110) - reused as-is from
  consema.document.structural.

Design: records are frozen dataclasses whose public fields are the
accessors (the Python-idiomatic shape; the JSON family follows the same
convention). The document is logically immutable; every NodeRef and Span is
bound to one snapshot identity. Recovered documents retain exact bytes and
explicit recovery structure (error lines) but never fabricate native
semantics (RFC 0010 section 8).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.source import SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    LocationError,
    LocationErrorKind,
    NodeRef,
    NodeRole,
    Span,
)
from consema.properties.errors import PropertiesDiagnostic
from consema.properties.java_string import JavaString
from consema.properties.kinds import (
    PropertiesEscapeKind,
    PropertiesLogicalLineKind,
    PropertiesProfile,
    PropertiesSyntaxKind,
    PropertiesValueState,
)
from consema.properties.limits import PropertiesParseLimits


@dataclass(frozen=True, slots=True)
class PropertiesNaturalLine:
    """One exact natural source line (lib.rs:309-342).

    ``span`` covers the complete line including its terminator;
    ``content_span`` excludes it; ``line_break_span`` is absent for an EOF
    line.
    """

    node: NodeRef
    span: Span
    content_span: Span
    line_break_span: Span | None


@dataclass(frozen=True, slots=True)
class PropertiesLogicalLine:
    """One property/error logical line and its natural-line constituents
    (lib.rs:344-370)."""

    node: NodeRef
    kind: PropertiesLogicalLineKind
    natural_lines: tuple[NodeRef, ...]


@dataclass(frozen=True, slots=True)
class PropertiesComment:
    """One comment natural line (lib.rs:372-405).

    ``marker`` is the exact ``#`` or ``!`` comment character.
    """

    node: NodeRef
    natural_line: NodeRef
    span: Span
    marker: str


@dataclass(frozen=True, slots=True)
class PropertiesEscape:
    """One source escape and its exact Java-string output range
    (lib.rs:407-455)."""

    node: NodeRef
    property: NodeRef
    in_key: bool
    kind: PropertiesEscapeKind
    span: Span
    output_start: int
    output_end: int

    def output_range(self) -> range:
        """Half-open output code-unit range in the owning key or value
        (lib.rs:450-454)."""
        return range(self.output_start, self.output_end)


@dataclass(frozen=True, slots=True)
class Property:
    """One distinct source-ordered property association (lib.rs:457-546).

    ``key_fragments``/``value_fragments`` are the ordered raw source spans
    contributing to the decoded strings (escape spellings are owned by the
    escape records); ``key_anchor``/``value_anchor`` are the zero-width
    anchors used when a fragment list is empty (RFC 0010 section 9).
    """

    node: NodeRef
    logical_line: NodeRef
    span: Span
    key_anchor: Span
    value_anchor: Span
    key_fragments: tuple[Span, ...]
    value_fragments: tuple[Span, ...]
    key: JavaString
    value: JavaString
    value_state: PropertiesValueState
    escapes: tuple[NodeRef, ...]
    duplicate_group: int | None


@dataclass(frozen=True, slots=True)
class PropertiesErrorLine:
    """One recovered malformed logical line (lib.rs:548-588)."""

    node: NodeRef
    logical_line: NodeRef
    natural_lines: tuple[NodeRef, ...]
    span: Span
    code: str


@dataclass(frozen=True, slots=True)
class PropertiesDocument:
    """Immutable, duplicate-preserving Java Properties document
    (lib.rs:590-608).

    Public fields mirror the Rust accessors one-to-one (lib.rs:610-775):
    ``natural_lines``, ``logical_lines``, ``properties``, ``comments``,
    ``escapes``, ``error_lines`` are the ordered record tuples; ``source``,
    ``profile``, ``structural_index``, ``syntax_kinds``, ``diagnostics``,
    ``parse_limits`` are the document facts; ``root_node`` is the document
    identity.
    """

    authority: DocumentAuthority
    source: SourceSnapshot
    profile: PropertiesProfile
    structural_index: LosslessStructuralIndex
    syntax_kinds: tuple[PropertiesSyntaxKind, ...]
    _formation_status: FormationStatus
    diagnostics: tuple[PropertiesDiagnostic, ...]
    natural_lines: tuple[PropertiesNaturalLine, ...]
    logical_lines: tuple[PropertiesLogicalLine, ...]
    properties: tuple[Property, ...]
    comments: tuple[PropertiesComment, ...]
    escapes: tuple[PropertiesEscape, ...]
    error_lines: tuple[PropertiesErrorLine, ...]
    parse_limits: PropertiesParseLimits
    root_node: NodeRef

    # -- identity and source -----------------------------------------------

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every Properties handle and span
        belongs (lib.rs:613-615)."""
        return self.authority.identity

    def render(self) -> bytes:
        """Default rendering is byte-for-byte source identity
        (lib.rs:624-628)."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """Stable Java Properties format family (lib.rs:631-633)."""
        return FormatFamilyId.new("java-properties", 1)

    def profile_id(self) -> ProfileId:
        """Exact selected profile (lib.rs:636-639)."""
        return self.profile.id()

    def selected_profile(self) -> PropertiesProfile:
        """Concrete selected profile (lib.rs:641-645)."""
        return self.profile

    def node_ref(self) -> NodeRef:
        """Root Properties document identity (lib.rs:647-651)."""
        return self.root_node

    def formation_status(self) -> FormationStatus:
        """Complete or explicitly recovered formation state (lib.rs:654-657)."""
        return self._formation_status

    def diagnostic_records(self) -> tuple[PropertiesDiagnostic, ...]:
        """Stable ordered diagnostics (lib.rs:659-663)."""
        return self.diagnostics

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive ordered source coverage (lib.rs:665-669)."""
        return self.structural_index

    def lossless_syntax_kinds(self) -> tuple[PropertiesSyntaxKind, ...]:
        """Format kind aligned with every structural piece (lib.rs:671-675)."""
        return self.syntax_kinds

    # -- snapshot-bound resolution -----------------------------------------

    def property(self, node: NodeRef) -> Property:
        """Resolves one property handle only within this snapshot
        (lib.rs:719-729)."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_PROPERTY:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for property in self.properties:
            if property.node == node:
                return property
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)

    def natural_line(self, node: NodeRef) -> PropertiesNaturalLine:
        """Resolves one natural-line handle only within this snapshot
        (lib.rs:731-744)."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_NATURAL_LINE:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for line in self.natural_lines:
            if line.node == node:
                return line
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)

    def logical_line(self, node: NodeRef) -> PropertiesLogicalLine:
        """Resolves one logical-line handle only within this snapshot
        (lib.rs:746-759)."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_LOGICAL_LINE:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for line in self.logical_lines:
            if line.node == node:
                return line
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)

    def escape(self, node: NodeRef) -> PropertiesEscape:
        """Resolves one escape handle only within this snapshot
        (lib.rs:761-774)."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_ESCAPE:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for escape in self.escapes:
            if escape.node == node:
                return escape
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)

    def comment(self, node: NodeRef) -> PropertiesComment:
        """Resolves one comment handle only within this snapshot."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_COMMENT:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for comment in self.comments:
            if comment.node == node:
                return comment
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)

    def error_line(self, node: NodeRef) -> PropertiesErrorLine:
        """Resolves one error-line handle only within this snapshot."""
        self.authority.verify(node)
        if node.role is not NodeRole.PROPERTIES_ERROR_LINE:
            raise LocationError(LocationErrorKind.WRONG_ROLE)
        for error_line in self.error_lines:
            if error_line.node == node:
                return error_line
        raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)
