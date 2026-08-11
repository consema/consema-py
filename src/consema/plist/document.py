"""Unified ``plist.xml@1`` / ``plist.binary@1`` document layer
(RFC 0013 §3, §7).

Authority (Rust arbitration for the public surface):

- Document shape and accessors: crates/consema-plist/src/document.rs:38-222
  — PlistRepresentation (document.rs:38-49), the unified Document with
  representation-specific accessors (lossless index and syntax kinds only
  for Xml; binary facts and structural regions only for Binary; hard gate
  1), snapshot identity, profile, format family "plist@1", formation
  status, and the native value arena.
- Conversion: document.rs:224-312 (Document::convert_to; the atomic
  cross-representation transform of RFC 0013 §7); the conversion report
  event surface document.rs:314-434 (ConvertedDocument / ConversionReport /
  ConversionReportEvent / ConversionEventKind); failures document.rs:436-
  459. The concrete serializers live in :mod:`consema.plist.conversion`.
- Parse dispatch: document.rs:68-93 plus the lib.rs entry points (parse
  lib.rs:214-221, parse_xml 279-300, parse_binary 241-260).
- Native model and arena: RFC 0013 §6 (:mod:`consema.plist.native`).

The two representations share one native value model but have disjoint
syntax systems: representation-specific facts are only reachable through
representation-specific accessors, so an XML document can never expose
binary structure facts and vice versa (RFC 0013 §7, hard gate 1). Every
returned fact is an immutable snapshot fact.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.structural import (
    BinaryStructuralIndex,
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
)
from consema.plist.errors import PlistDiagnostic
from consema.plist.kinds import PlistEncodingSelection, PlistParseLimits, PlistProfile
from consema.plist.native import PlistDocument
from consema.plist.parser_binary import BinaryFacts, PlistFormedBinary, parse_binary
from consema.plist.parser_xml import PlistFormedXml, PlistSyntaxKind, parse_xml


class PlistRepresentation(enum.Enum):
    """The two plist representations (RFC 0013 §1, §7; document.rs:38-49).

    The representations share one native value model and are format
    identities, not dialects of one format; a ``.plist`` extension never
    selects one.
    """

    XML = "Xml"
    BINARY = "Binary"


class PlistAccessErrorKind:
    """Stable node-resolution failures (the LocationError vocabulary of
    consema-document)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    OUT_OF_BOUNDS = "OutOfBounds"


class PlistAccessError(Exception):
    """Node resolution failure; carries no registered error code
    (location failures are internal, RFC 0003 §11)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class PlistDocument:
    """One formed plist document under either representation
    (document.rs:51-67).

    The concrete representation is private; representation-specific facts
    are reachable only through the representation-specific accessors, so an
    XML document can never expose binary structure facts and vice versa
    (hard gate 1).
    """

    authority: DocumentAuthority
    source: object  # consema.document.source.SourceSnapshot
    profile: PlistProfile
    _representation: PlistRepresentation
    _formation_status: FormationStatus
    diagnostics: tuple[PlistDiagnostic, ...]
    native: PlistDocument | None
    formed_xml: PlistFormedXml | None = None
    formed_binary: PlistFormedBinary | None = None
    root_node: NodeRef = None  # type: ignore[assignment]

    # -- construction --------------------------------------------------------

    @classmethod
    def parse(
        cls,
        raw: bytes,
        profile: PlistProfile,
        selection: PlistEncodingSelection,
        limits: PlistParseLimits,
    ) -> PlistDocument:
        """Forms one document from raw bytes under one exact profile
        (RFC 0013 §1, §3; document.rs:68-93).

        The profile is selected by the caller before formation; neither the
        ``bplist00`` magic number nor a ``.plist`` extension selects
        semantics. An encoding selection inconsistent with the profile is a
        fatal source-contract conflict at formation."""
        if profile is PlistProfile.XML_V1:
            formed = parse_xml(raw, selection, limits)
            return cls(
                authority=formed.authority,
                source=formed.source,
                profile=profile,
                _representation=PlistRepresentation.XML,
                _formation_status=formed.status,
                diagnostics=formed.diagnostics,
                native=formed.document,
                formed_xml=formed,
                root_node=formed.root_node,
            )
        formed = parse_binary(raw, selection, limits)
        return cls(
            authority=formed.authority,
            source=formed.source,
            profile=profile,
            _representation=PlistRepresentation.BINARY,
            _formation_status=formed.status,
            diagnostics=formed.diagnostics,
            native=formed.document,
            formed_binary=formed,
            root_node=formed.root_node,
        )

    # -- identity and source -------------------------------------------------

    def representation(self) -> PlistRepresentation:
        """Representation of the formed document (document.rs:96-102)."""
        return self._representation

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every handle and span of this document
        belongs (document.rs:148-154)."""
        return self.authority.identity

    def source_snapshot(self) -> object:
        return self.source

    def render(self) -> bytes:
        """Exact original bytes; unmodified rendering is byte-exact."""
        return self.source.bytes()

    def format_family(self) -> FormatFamilyId:
        """Stable plist format family (document.rs:165-169)."""
        return FormatFamilyId.new("plist", 1)

    def profile_id(self) -> ProfileId:
        """Exact source profile of the formed document (document.rs:156-
        163)."""
        return self.profile.id()

    def node_ref(self) -> NodeRef:
        """Root plist document identity."""
        return self.root_node

    def formation_status(self) -> FormationStatus:
        """Complete or explicitly recovered formation state (RFC 0013 §3)."""
        return self._formation_status

    def diagnostic_records(self) -> tuple[PlistDiagnostic, ...]:
        """Ordered diagnostics from formation (document.rs:137-144)."""
        return self.diagnostics

    # -- native model --------------------------------------------------------

    def document(self) -> PlistDocument | None:
        """Native value arena, when the root value is provable (RFC 0013
        §6; document.rs:170-182). Both representations share the same
        native value model; not None exactly when formation proved the
        complete root value."""
        return self.native

    # -- representation-specific facts (hard gate 1) -------------------------

    def lossless_structural_index(self) -> LosslessStructuralIndex | None:
        """Exhaustive ordered lossless piece coverage; ``plist.xml@1`` only
        (RFC 0013 §8.2; document.rs:184-192)."""
        if self.formed_xml is None:
            return None
        return self.formed_xml.lossless_structural_index()

    def lossless_syntax_kinds(self) -> tuple[PlistSyntaxKind, ...] | None:
        """Ordered XML syntax kinds, parallel to the lossless structural
        pieces; ``plist.xml@1`` only (RFC 0013 §8.2; document.rs:193-202)."""
        if self.formed_xml is None:
            return None
        return self.formed_xml.lossless_syntax_kinds()

    def binary_facts(self) -> BinaryFacts | None:
        """Binary object/offset/reference/trailer facts; ``plist.binary@1``
        only (RFC 0013 §8.3; document.rs:204-212)."""
        if self.formed_binary is None:
            return None
        return self.formed_binary.binary_facts()

    def binary_structural_index(self) -> BinaryStructuralIndex | None:
        """Exhaustive ordered binary region coverage; ``plist.binary@1``
        only (RFC 0013 §2.2, §8.3; document.rs:213-222)."""
        if self.formed_binary is None:
            return None
        return self.formed_binary.binary_structural_index()

    # -- conversion ----------------------------------------------------------

    def convert_to(self, target: PlistProfile, limits: PlistParseLimits) -> object:
        """Converts the document to the other representation (RFC 0013 §7;
        document.rs:224-289). See :mod:`consema.plist.conversion`."""
        from consema.plist.conversion import convert

        return convert(self, target, limits)

    # -- limits --------------------------------------------------------------

    def parse_limits(self) -> PlistParseLimits:
        if self.formed_xml is not None:
            return self.formed_xml.limits
        return self.formed_binary.limits

    # -- handle resolution ---------------------------------------------------

    def _verify(self, node: NodeRef, *roles) -> None:
        if node.snapshot != self.authority.identity:
            raise PlistAccessError(PlistAccessErrorKind.WRONG_SNAPSHOT)
        if node.role not in roles:
            raise PlistAccessError(PlistAccessErrorKind.WRONG_ROLE)


def parse(
    raw: bytes,
    profile: PlistProfile,
    selection: PlistEncodingSelection,
    limits: PlistParseLimits,
) -> PlistDocument:
    """Forms one ``plist.xml@1`` or ``plist.binary@1`` document from raw
    bytes (RFC 0013 §1, §3; lib.rs:214-221)."""
    return PlistDocument.parse(raw, profile, selection, limits)
