"""The immutable INI document snapshot.

Authority (Rust arbitration for the public surface):

- Document fields and accessors: https://github.com/consema/consema-rs/blob/main/consema-ini/src/lib.rs —
  snapshot identity (lib.rs), source (lib.rs), render()
  (byte-for-byte source identity, lib.rs), format family "ini@1"
  (lib.rs), profile (lib.rs), root document identity
  (lib.rs), formation status (lib.rs), diagnostics
  (lib.rs), lossless structural index (lib.rs), syntax
  kinds (lib.rs), ordered physical/logical lines, sections,
  entries, error records (lib.rs), parse limits (lib.rs),
  and the snapshot-bound handle resolution methods (lib.rs).
- Native model: RFC 0009 §8 (https://github.com/consema/consema/blob/main/docs/rfcs/0009-ini-family-profiles-v1.md
) — ordered physical lines with exact raw and decoded ranges,
  ordered logical lines with constituent physical-line identities, BOM/
  newline/indentation/delimiter/quote/comment facts, section header
  identity and original/comparison names, entry identity with owning
  section and Missing | Empty | Present value state, continuation joins,
  duplicate/case-collision groups without collapsing occurrences,
  error-line identities with ordered stable diagnostics, and exhaustive
  non-overlapping syntax pieces over the raw source.
- The record types (IniPhysicalLine / IniLogicalLine / IniSection /
  IniEntry / IniErrorLine) are defined in consema.ini.parser and carry
  snapshot-bound NodeRefs with the INI-specific roles (NodeRole of
  consema.document.structural: IniDocument, IniPhysicalLine,
  IniLogicalLine, IniSection, IniDefaultSection, IniEntry, IniErrorLine,
  IniSyntaxPiece — consema-document lib.rs).

The document is logically immutable; every NodeRef and Span is bound to
one snapshot identity. Recovered documents retain exact bytes and explicit
recovery structure but never fabricate native semantics (RFC 0009 §4,
https://github.com/consema/consema/blob/main/docs/rfcs/0009-ini-family-profiles-v1.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
)
from consema.ini.errors import IniDiagnostic
from consema.ini.kinds import IniParseLimits, IniProfile, IniSyntaxKind
from consema.ini.parser import (
    IniEntry,
    IniErrorLine,
    IniLogicalLine,
    IniPhysicalLine,
    IniSection,
)


class IniAccessErrorKind:
    """Stable node-resolution failures (mirror of the document resolution
    errors; the LocationError vocabulary of consema-document)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    OUT_OF_BOUNDS = "OutOfBounds"


class IniAccessError(Exception):
    """Node resolution failure; carries no registered error code
    (location failures are internal, RFC 0003 §11)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class IniDocument:
    """Complete immutable INI document snapshot (lib.rs)."""

    authority: DocumentAuthority
    source: object  # consema.document.source.SourceSnapshot
    profile: IniProfile
    structural_index: LosslessStructuralIndex
    syntax_kinds: tuple[IniSyntaxKind, ...]
    _formation_status: FormationStatus
    diagnostics: tuple[IniDiagnostic, ...]
    physical_lines: tuple[IniPhysicalLine, ...]
    logical_lines: tuple[IniLogicalLine, ...]
    sections: tuple[IniSection, ...]
    entries: tuple[IniEntry, ...]
    error_lines: tuple[IniErrorLine, ...]
    parse_limits: IniParseLimits
    root_node: NodeRef

    # -- identity and source -----------------------------------------------

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every INI handle and span belongs
        (lib.rs)."""
        return self.authority.identity

    def source_snapshot(self) -> object:
        """Exact immutable source snapshot (lib.rs)."""
        return self.source

    def render(self) -> bytes:
        """Default rendering is byte-for-byte source identity
        (lib.rs)."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """Stable INI format family (lib.rs)."""
        return FormatFamilyId.new("ini", 1)

    def profile_id(self) -> ProfileId:
        """Exact selected profile (lib.rs)."""
        return self.profile.id()

    def node_ref(self) -> NodeRef:
        """Root INI document identity (lib.rs)."""
        return self.root_node

    def formation_status(self) -> FormationStatus:
        """Complete or explicitly recovered formation state (lib.rs)."""
        return self._formation_status

    def diagnostic_records(self) -> tuple[IniDiagnostic, ...]:
        """Stable ordered diagnostics (lib.rs)."""
        return self.diagnostics

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        """Exhaustive ordered source coverage (lib.rs)."""
        return self.structural_index

    def lossless_syntax_kinds(self) -> tuple[IniSyntaxKind, ...]:
        """Format kind aligned with each structural piece (lib.rs)."""
        return self.syntax_kinds

    # -- native records -----------------------------------------------------
    # The tuple fields are the ordered native-record accessors: physical
    # lines (lib.rs), logical records (lib.rs), distinct
    # section occurrences (lib.rs), distinct entry occurrences
    # (lib.rs), recovered error records (lib.rs), and the
    # resource contract used to form this snapshot (lib.rs). They
    # are dataclass fields (Python-idiomatic access) with the same frozen
    # spellings as the Rust accessors.

    # -- handle resolution --------------------------------------------------

    def resolve_physical_line(self, node: NodeRef) -> IniPhysicalLine:
        """Resolves one physical-line handle only within this snapshot
        (lib.rs)."""
        self._verify(node, NodeRole.INI_PHYSICAL_LINE)
        for line in self.physical_lines:
            if line.node == node:
                return line
        raise IniAccessError(IniAccessErrorKind.OUT_OF_BOUNDS)

    def resolve_logical_line(self, node: NodeRef) -> IniLogicalLine:
        """Resolves one logical-line handle only within this snapshot
        (lib.rs)."""
        self._verify(node, NodeRole.INI_LOGICAL_LINE)
        for line in self.logical_lines:
            if line.node == node:
                return line
        raise IniAccessError(IniAccessErrorKind.OUT_OF_BOUNDS)

    def resolve_section(self, node: NodeRef) -> IniSection:
        """Resolves one section/default-section handle only within this
        snapshot (lib.rs)."""
        self._verify(node, NodeRole.INI_SECTION, NodeRole.INI_DEFAULT_SECTION)
        for section in self.sections:
            if section.node == node:
                return section
        raise IniAccessError(IniAccessErrorKind.OUT_OF_BOUNDS)

    def resolve_entry(self, node: NodeRef) -> IniEntry:
        """Resolves one entry handle only within this snapshot
        (lib.rs)."""
        self._verify(node, NodeRole.INI_ENTRY)
        for entry in self.entries:
            if entry.node == node:
                return entry
        raise IniAccessError(IniAccessErrorKind.OUT_OF_BOUNDS)

    def _verify(self, node: NodeRef, *roles: NodeRole) -> None:
        if node.snapshot != self.authority.identity:
            raise IniAccessError(IniAccessErrorKind.WRONG_SNAPSHOT)
        if node.role not in roles:
            raise IniAccessError(IniAccessErrorKind.WRONG_ROLE)
