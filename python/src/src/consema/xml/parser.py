"""XML formation: source facts, tokenization, native tree, safe DTD subset,
bounded entity expansion, recovery, and exhaustive piece coverage
(RFC 0012 §2-4, §6-7, §12-13).

Authority:

- RFC 0012 §2 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:46-82) — source
  and encoding: UTF-8 (optional BOM) / UTF-16LE / UTF-16BE (required BOM);
  no-BOM defaults to UTF-8; the declaration uses version exactly ``1.0``
  and agrees with the selected encoding; raw CR/CRLF/LF spelling remains in
  source pieces while native character data follows XML 1.0 line-end
  normalization to LF.
- RFC 0012 §3 (lines 87-131) — safe DTD and entity boundary: internal-only
  DOCTYPE; external subset/entity, parameter entity, notation, validation
  declarations and markup-creating entity text recover; five predefined
  entities always available; expansion guarded before and during allocation
  across the whole document.
- RFC 0012 §4 (lines 132-167) — formation and recovery: Complete requires
  one document element, matched tags, legal characters/names, namespace
  constraints, admitted resolved DTD subset, exhaustive source coverage and
  every configured limit; Recovered retains the immutable source,
  exhaustive coverage, ordered diagnostics, and never invents a closing
  tag, binding, attribute value, or second root.
- RFC 0012 §7 (lines 258-283) — the v1 kind set with exhaustive raw-byte
  coverage; decoded tokenizer spans convert back to exact raw-byte spans.
- The formation pipeline and every recovery code transcribe
  crates/consema-xml/src/parser.rs (encoding request 56-80; profile
  validation 82-108; declaration 334-503; PI 505-579; comment 581-644;
  DOCTYPE 646-911; element/attribute 913-1305; text/CDATA 1307-1422;
  fragments and reference resolution 1460-1729; recovery 1731-1790;
  finish 1792-1914) — byte/registry arbitration only. The XML name grammar
  follows the normative XML 1.0 Fifth Edition productions (RFC 0012 §1).
- The frozen xml.* diagnostic codes are registered by RFC 0012 §12
  (lines 428-434: they are RFC-registered and do not enter the
  consema-protocol core error registry).

go/xml is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.limits import ParseLimits
from consema.document.source import (
    BomKind,
    BomPolicy,
    DecodedOffset,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    Span,
    StructuralPiece,
    StructuralPieceKind,
)

from consema.protocol.diagnostic import Severity
from consema.protocol.error_registry import DiagnosticCategory

from consema.xml.document import (
    Document,
    EntityDeclarationData,
    QNameFacts,
    ReferenceFragment,
    ReferenceFragmentKind,
    XmlAttributeData,
    XmlCdataData,
    XmlCommentData,
    XmlContent,
    XmlContentKind,
    XmlDeclarationData,
    XmlDoctypeData,
    XmlElementData,
    XmlNamespaceBindingData,
    XmlPiData,
    XmlPrologItem,
    XmlPrologItemKind,
    XmlProfile,
    XmlTextData,
)
from consema.xml.entities import (
    EntityExpansionLimits,
    EntityExpansionState,
    ReplacementError,
    ReplacementErrorKind,
    expansion_breach_code,
    is_xml_char,
    predefined_value,
    validate_replacement_text,
)
from consema.xml.errors import XmlDiagnostic, XmlFormationFailure
from consema.xml.kinds import XmlSyntaxKind
from consema.xml.namespaces import NamespaceError, NamespaceScope

# ---------------------------------------------------------------------------
# Encoding selection (crates/consema-xml/src/lib.rs:69-79)
# ---------------------------------------------------------------------------


class XmlEncodingSelectionKind(enum.Enum):
    """How the caller selects the document-entity encoding."""

    PROFILE_DEFAULT = "ProfileDefault"
    EXPLICIT = "Explicit"


@dataclass(frozen=True, slots=True)
class XmlEncodingSelection:
    """Explicit document-entity encoding selection (lib.rs:69-79).

    No-BOM source defaults to UTF-8. An explicit caller choice is evidence,
    not permission to contradict a BOM or a declaration.
    """

    kind: XmlEncodingSelectionKind
    encoding: SourceEncoding | None = None

    @classmethod
    def profile_default(cls) -> XmlEncodingSelection:
        """Apply only the frozen profile default and BOM rules."""
        return cls(kind=XmlEncodingSelectionKind.PROFILE_DEFAULT)

    @classmethod
    def explicit(cls, encoding: SourceEncoding) -> XmlEncodingSelection:
        """Use one caller-selected document-entity encoding."""
        return cls(kind=XmlEncodingSelectionKind.EXPLICIT, encoding=encoding)


# ---------------------------------------------------------------------------
# Parse limits (crates/consema-xml/src/lib.rs:81-157)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_DECODED_UTF8_BYTES = 128 * 1024 * 1024
_DEFAULT_MAX_DECODED_SCALARS = 64 * 1024 * 1024
_DEFAULT_MAX_ELEMENT_COUNT = 1_000_000
_DEFAULT_MAX_ATTRIBUTE_COUNT = 100_000
_DEFAULT_MAX_NAMESPACE_DECLARATION_COUNT = 100_000
_DEFAULT_MAX_MIXED_CONTENT_ITEMS = 2_000_000
_DEFAULT_MAX_QNAME_LENGTH = 4 * 1024
_DEFAULT_MAX_NAMESPACE_URI_LENGTH = 8 * 1024
_DEFAULT_MAX_ATTRIBUTE_VALUE_LENGTH = 4 * 1024 * 1024
_DEFAULT_MAX_COMMENT_LENGTH = 4 * 1024 * 1024
_DEFAULT_MAX_PI_LENGTH = 4 * 1024 * 1024
_DEFAULT_MAX_CDATA_LENGTH = 4 * 1024 * 1024
_DEFAULT_MAX_TEXT_LENGTH = 4 * 1024 * 1024
_DEFAULT_MAX_DTD_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_ENTITY_DECLARATIONS = 10_000
_DEFAULT_MAX_ENTITY_REFERENCES = 1_000_000
_DEFAULT_MAX_ENTITY_EXPANSION_DEPTH = 100
_DEFAULT_MAX_EXPANDED_ENTITY_BYTES = 32 * 1024 * 1024
_DEFAULT_MAX_EXPANDED_ENTITY_SCALARS = 16 * 1024 * 1024
_DEFAULT_MAX_ENTITY_AMPLIFICATION_RATIO = 1_000
_DEFAULT_MAX_RECOVERY_REGIONS = 100_000


@dataclass(frozen=True, slots=True)
class XmlParseLimits:
    """XML-specific formation, entity, and recovery limits (RFC 0012 §12;
    lib.rs:81-157)."""

    common: ParseLimits = field(default_factory=ParseLimits)
    max_decoded_utf8_bytes: int = _DEFAULT_MAX_DECODED_UTF8_BYTES
    max_decoded_scalars: int = _DEFAULT_MAX_DECODED_SCALARS
    max_element_count: int = _DEFAULT_MAX_ELEMENT_COUNT
    max_attribute_count: int = _DEFAULT_MAX_ATTRIBUTE_COUNT
    max_namespace_declaration_count: int = _DEFAULT_MAX_NAMESPACE_DECLARATION_COUNT
    max_mixed_content_items: int = _DEFAULT_MAX_MIXED_CONTENT_ITEMS
    max_qname_length: int = _DEFAULT_MAX_QNAME_LENGTH
    max_namespace_uri_length: int = _DEFAULT_MAX_NAMESPACE_URI_LENGTH
    max_attribute_value_length: int = _DEFAULT_MAX_ATTRIBUTE_VALUE_LENGTH
    max_comment_length: int = _DEFAULT_MAX_COMMENT_LENGTH
    max_pi_length: int = _DEFAULT_MAX_PI_LENGTH
    max_cdata_length: int = _DEFAULT_MAX_CDATA_LENGTH
    max_text_length: int = _DEFAULT_MAX_TEXT_LENGTH
    max_dtd_bytes: int = _DEFAULT_MAX_DTD_BYTES
    max_entity_declarations: int = _DEFAULT_MAX_ENTITY_DECLARATIONS
    max_entity_references: int = _DEFAULT_MAX_ENTITY_REFERENCES
    max_entity_expansion_depth: int = _DEFAULT_MAX_ENTITY_EXPANSION_DEPTH
    max_expanded_entity_bytes: int = _DEFAULT_MAX_EXPANDED_ENTITY_BYTES
    max_expanded_entity_scalars: int = _DEFAULT_MAX_EXPANDED_ENTITY_SCALARS
    max_entity_amplification_ratio: int = _DEFAULT_MAX_ENTITY_AMPLIFICATION_RATIO
    max_recovery_regions: int = _DEFAULT_MAX_RECOVERY_REGIONS

    def entity_limits(self) -> EntityExpansionLimits:
        """Entity expansion limits derived from these parse limits
        (lib.rs:159-172)."""
        return EntityExpansionLimits(
            max_declarations=self.max_entity_declarations,
            max_references=self.max_entity_references,
            max_expansion_depth=self.max_entity_expansion_depth,
            max_expanded_bytes=self.max_expanded_entity_bytes,
            max_expanded_scalars=self.max_expanded_entity_scalars,
            max_amplification_ratio=self.max_entity_amplification_ratio,
        )


# ---------------------------------------------------------------------------
# XML 1.0 name productions (normative grammar, RFC 0012 §1)
# ---------------------------------------------------------------------------


def _is_name_start(character: str) -> bool:
    value = ord(character)
    return (
        character in (":", "_")
        or 0x41 <= value <= 0x5A
        or 0x61 <= value <= 0x7A
        or 0xC0 <= value <= 0xD6
        or 0xD8 <= value <= 0xF6
        or 0xF8 <= value <= 0x2FF
        or 0x370 <= value <= 0x37D
        or 0x37F <= value <= 0x1FFF
        or 0x200C <= value <= 0x200D
        or 0x2070 <= value <= 0x218F
        or 0x2C00 <= value <= 0x2FEF
        or 0x3001 <= value <= 0xD7FF
        or 0xF900 <= value <= 0xFDCF
        or 0xFDF0 <= value <= 0xFFFD
        or 0x10000 <= value <= 0xEFFFF
    )


def _is_name_char(character: str) -> bool:
    if _is_name_start(character):
        return True
    value = ord(character)
    return (
        character in ("-", ".")
        or 0x30 <= value <= 0x39
        or value == 0xB7
        or 0x0300 <= value <= 0x036F
        or 0x203F <= value <= 0x2040
    )


# ---------------------------------------------------------------------------
# Tokenizer / parser state
# ---------------------------------------------------------------------------


class _Frame:
    __slots__ = (
        "start",
        "span",
        "qname",
        "expanded",
        "namespace_error",
        "scope",
        "namespaces",
        "attributes",
        "children",
        "pending_declarations",
        "pending_attributes",
    )

    def __init__(self, start: int, span: Span, qname: QNameFacts) -> None:
        self.start = start
        self.span = span
        self.qname = qname
        self.expanded = None
        self.namespace_error = None
        self.scope = NamespaceScope.new()
        self.namespaces: list[XmlNamespaceBindingData] = []
        self.attributes: list[XmlAttributeData] = []
        self.children: list[int] = []
        self.pending_declarations: list[tuple[QNameFacts, str, Span]] = []
        self.pending_attributes: list[XmlAttributeData] = []


class _ScanFailure(Exception):
    """Internal tokenization failure that triggers error-region recovery."""


class _Parser:
    """One-pass deterministic XML formation scanner over the decoded text.

    The scanner works in decoded scalar coordinates; every span is
    converted back to an exact raw-byte span through the source index, so
    UTF-16 pieces cover original code units rather than a temporary UTF-8
    buffer (RFC 0012 §2/§7).
    """

    def __init__(
        self,
        source: SourceSnapshot,
        profile: XmlProfile,
        limits: XmlParseLimits,
        text: str,
    ) -> None:
        if profile is not XmlProfile.SAFE_V1:
            raise XmlFormationFailure.fatal(
                "xml.profile.unknown@1", DiagnosticCategory.CONFORMANCE
            )
        self.source = source
        self.profile = profile
        self.limits = limits
        self.text = text
        self.authority = DocumentAuthority.fresh()
        self.diagnostics: list[XmlDiagnostic] = []
        self.pieces: list[tuple[Span, XmlSyntaxKind, StructuralPieceKind]] = []
        self.nodes: list[XmlContent] = []
        self.parent_of: list[int | None] = []
        self.next_ordinal = 0
        self.entity_state = EntityExpansionState()
        self.entities: list[EntityDeclarationData] = []
        self.stack: list[_Frame] = []
        self.prolog: list[XmlPrologItem] = []
        self.epilog: list[XmlPrologItem] = []
        self.declaration: XmlDeclarationData | None = None
        self.doctype: XmlDoctypeData | None = None
        self.doctype_name: QNameFacts | None = None
        self.doctype_span_start: int | None = None
        self.external_subset_recovered = False
        self.dtd_subset_start: int | None = None
        self.root: int | None = None
        self.recovered = False
        self.error_regions = 0
        # Scalar boundary index: utf8_offsets[i] is the decoded UTF-8 byte
        # offset of scalar i; raw_offsets[i] is the raw byte offset of the
        # same boundary (identity for UTF-8 sources).
        self._utf8_offsets: list[int] = []
        self._raw_offsets: list[int] = []
        self._build_offsets()

    # -- offset machinery ---------------------------------------------------

    def _build_offsets(self) -> None:
        total = 0
        utf8_offsets = [0]
        for character in self.text:
            total += len(character.encode("utf-8"))
            utf8_offsets.append(total)
        self._utf8_offsets = utf8_offsets
        selected = self.source.encoding_facts().selected
        if selected.kind is SourceEncodingKind.UTF8:
            self._raw_offsets = list(utf8_offsets)
            return
        self._raw_offsets = [
            self.source.raw_byte_at(DecodedOffset.utf8_byte(offset)) for offset in utf8_offsets
        ]

    def _raw_offset(self, scalar: int) -> int:
        return self._raw_offsets[scalar]

    def _span(self, start_scalar: int, end_scalar: int) -> Span:
        return self.authority.span(self._raw_offset(start_scalar), self._raw_offset(end_scalar))

    def _span_raw(self, start_raw: int, end_raw: int) -> Span:
        return self.authority.span(start_raw, end_raw)

    # -- pieces --------------------------------------------------------------

    def _push_piece(self, span: Span, kind: XmlSyntaxKind, structural: StructuralPieceKind) -> None:
        self.pieces.append((span, kind, structural))

    # -- diagnostics and recovery --------------------------------------------

    def _recover(self, code: str, span: Span, category: DiagnosticCategory) -> None:
        """Records a recovery diagnostic with its exact failing span
        (parser.rs:1736-1749). The span lies inside token-covered bytes, so
        it is not pushed as an additional structural piece."""
        self.recovered = True
        if self.error_regions >= self.limits.max_recovery_regions:
            return
        self.error_regions += 1
        self.diagnostics.append(
            XmlDiagnostic(
                code=code,
                category=category,
                severity=Severity.ERROR,
                primary=span,
                occurrence=len(self.diagnostics),
            )
        )

    def _recover_error_region(self, start_scalar: int, end_scalar: int) -> None:
        """Recovers one tokenizer failure as an error region
        (parser.rs:1759-1786)."""
        self.recovered = True
        if self.error_regions >= self.limits.max_recovery_regions:
            return
        self.error_regions += 1
        span = self._span(start_scalar, end_scalar)
        if span.start_byte == span.end_byte:
            return
        self._push_piece(span, XmlSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
        self.diagnostics.append(
            XmlDiagnostic(
                code="xml.syntax.well-formedness@1",
                category=DiagnosticCategory.SYNTAX,
                severity=Severity.ERROR,
                primary=span,
                occurrence=len(self.diagnostics),
            )
        )

    def _entity_limit(self, breach, span: Span) -> None:
        """Records one expansion breach recovery (parser.rs:1751-1757)."""
        self._recover(expansion_breach_code(breach), span, DiagnosticCategory.CONFORMANCE)

    # -- ordinal accounting ---------------------------------------------------

    def _ordinal(self) -> int:
        ordinal = self.next_ordinal
        self.next_ordinal += 1
        return ordinal

    # -- content placement -----------------------------------------------------

    def _push_content(self, item: XmlContent) -> None:
        """Attaches one child content occurrence (parser.rs:1403-1422)."""
        if self.stack:
            frame = self.stack[-1]
            if len(frame.children) >= self.limits.max_mixed_content_items:
                self._recover(
                    "xml.limit.mixed-content@1", item.span, DiagnosticCategory.CONFORMANCE
                )
                return
            frame.children.append(len(self.nodes))
        self.parent_of.append(None)
        self.nodes.append(item)

    def _push_whitespace_pieces(self, start_scalar: int, end_scalar: int) -> None:
        """Splits one whitespace-only text run into Whitespace and LineBreak
        pieces; CRLF counts as one line break (parser.rs:1426-1458)."""
        segment = self.text[start_scalar:end_scalar]
        index = 0
        while index < len(segment):
            is_line_break = segment[index] in ("\n", "\r")
            run_start = index
            if (
                is_line_break
                and segment[index] == "\r"
                and index + 1 < len(segment)
                and segment[index + 1] == "\n"
            ):
                index += 2
            else:
                index += 1
            while index < len(segment) and (segment[index] in ("\n", "\r")) == is_line_break:
                index += 1
            span = self._span(start_scalar + run_start, start_scalar + index)
            self._push_piece(
                span,
                XmlSyntaxKind.LINE_BREAK if is_line_break else XmlSyntaxKind.WHITESPACE,
                StructuralPieceKind.TRIVIA,
            )

    # -- text and references ---------------------------------------------------

    def _text_fragments(
        self, start_scalar: int, end_scalar: int, literal_kind: XmlSyntaxKind
    ) -> list[ReferenceFragment]:
        """Splits one text or attribute-value occurrence into reference
        fragments (parser.rs:1467-1555)."""
        segment = self.text[start_scalar:end_scalar]
        if "&" not in segment:
            span = self._span(start_scalar, end_scalar)
            self._push_piece(span, literal_kind, StructuralPieceKind.TOKEN)
            return [ReferenceFragment.literal(span, segment)]
        fragments: list[ReferenceFragment] = []
        cursor = 0
        index = 0
        while index < len(segment):
            relative = segment[index:].find("&")
            if relative < 0:
                break
            at = index + relative
            if at > cursor:
                literal = segment[cursor:at]
                span = self._span(start_scalar + cursor, start_scalar + at)
                self._push_piece(span, literal_kind, StructuralPieceKind.TOKEN)
                fragments.append(ReferenceFragment.literal(span, literal))
            semi = segment[at + 1 :].find(";")
            if semi < 0:
                # Unterminated reference: recover and keep the rest literal.
                span = self._span(start_scalar + at, end_scalar)
                self._recover("xml.reference.malformed@1", span, DiagnosticCategory.SYNTAX)
                self._push_piece(span, literal_kind, StructuralPieceKind.TOKEN)
                fragments.append(ReferenceFragment.literal(span, segment[at:]))
                cursor = len(segment)
                index = len(segment)
                continue
            semi = at + 1 + semi
            body = segment[at + 1 : semi]
            ref_span = self._span(start_scalar + at, start_scalar + semi + 1)
            fragment = self._resolve_reference(body, ref_span, 0)
            if fragment is not None:
                if fragment.kind is ReferenceFragmentKind.CHARACTER_REFERENCE:
                    kind = XmlSyntaxKind.CHARACTER_REFERENCE
                elif fragment.kind is ReferenceFragmentKind.LITERAL:
                    kind = literal_kind
                else:
                    kind = XmlSyntaxKind.ENTITY_REFERENCE
                self._push_piece(ref_span, kind, StructuralPieceKind.TOKEN)
                fragments.append(fragment)
            cursor = semi + 1
            index = semi + 1
        if cursor < len(segment):
            literal = segment[cursor:]
            span = self._span(start_scalar + cursor, end_scalar)
            self._push_piece(span, literal_kind, StructuralPieceKind.TOKEN)
            fragments.append(ReferenceFragment.literal(span, literal))
        return fragments

    def _resolve_reference(
        self, body: str, ref_span: Span, depth: int
    ) -> ReferenceFragment | None:
        """Resolves one ``&…;`` reference body into a fragment
        (parser.rs:1558-1645)."""
        if body.startswith("#"):
            digits = body[1:]
            hex_digits = None
            if digits.startswith("x") or digits.startswith("X"):
                hex_digits = digits[1:]
                valid = bool(hex_digits) and all(
                    c in "0123456789abcdefABCDEF" for c in hex_digits
                )
            else:
                valid = bool(digits) and digits.isascii() and digits.isdigit()
            if valid and hex_digits is not None:
                value = int(hex_digits, 16)
            elif valid:
                value = int(digits)
            else:
                value = None
            resolved = None
            if value is not None and value <= 0x10FFFF:
                character = chr(value)
                if is_xml_char(character):
                    resolved = character
            if resolved is not None:
                return ReferenceFragment.character_reference(ref_span, resolved)
            self._recover("xml.reference.invalid-character@1", ref_span, DiagnosticCategory.SYNTAX)
            return None
        predefined = predefined_value(body)
        if predefined is not None:
            return ReferenceFragment.predefined_entity(ref_span, body, predefined)
        declared = next((entity for entity in self.entities if entity.name == body), None)
        if declared is None:
            self._recover("xml.entity.unknown@1", ref_span, DiagnosticCategory.CONFORMANCE)
            return None
        try:
            self.entity_state.enter_reference(
                len(declared.replacement),
                len(declared.replacement),
                self.limits.entity_limits(),
            )
        except Exception as breach:
            self._entity_limit(breach, ref_span)
            return None
        nested = self._resolve_nested(declared.replacement, ref_span, depth + 1)
        self.entity_state.leave_reference()
        if nested is None:
            self._recover("xml.entity.cyclic@1", ref_span, DiagnosticCategory.CONFORMANCE)
            return None
        return ReferenceFragment.general_entity(ref_span, body, nested, declared.span)

    def _resolve_nested(self, replacement: str, source_span: Span, depth: int) -> str | None:
        """Resolves nested references inside one replacement text
        (parser.rs:1647-1692). Unknown references, cycles, or limit
        breaches inside replacement text produce no partial native text."""
        if depth > self.limits.max_entity_expansion_depth:
            return None
        output: list[str] = []
        cursor = 0
        index = 0
        while index < len(replacement):
            relative = replacement[index:].find("&")
            if relative < 0:
                break
            at = index + relative
            output.append(replacement[cursor:at])
            semi = replacement[at + 1 :].find(";")
            if semi < 0:
                return None
            semi = at + 1 + semi
            body = replacement[at + 1 : semi]
            fragment = self._resolve_reference(body, source_span, depth)
            if fragment is None:
                return None
            output.append(fragment.resolved or "")
            cursor = semi + 1
            index = semi + 1
        output.append(replacement[cursor:])
        return "".join(output)

    def _value_fragments(
        self, start_scalar: int, end_scalar: int
    ) -> tuple[list[ReferenceFragment], str]:
        """Splits an attribute value into fragments and applies XML 1.0
        CDATA normalization (parser.rs:1696-1729)."""
        fragments = self._text_fragments(start_scalar, end_scalar, XmlSyntaxKind.ATTRIBUTE_VALUE)
        normalized: list[str] = []
        for fragment in fragments:
            if fragment.kind is ReferenceFragmentKind.CHARACTER_REFERENCE:
                normalized.append(fragment.resolved or "")
            else:
                text = (
                    fragment.text
                    if fragment.kind is ReferenceFragmentKind.LITERAL
                    else (fragment.resolved or "")
                )
                for character in text:
                    normalized.append(
                        " " if character in ("\t", "\n", "\r", " ") else character
                    )
        return fragments, "".join(normalized)

    # -- scanning -------------------------------------------------------------

    def parse(self) -> Document:
        self._cover_bom()
        pos = 1 if self.source.encoding_facts().bom is not None else 0
        while pos < len(self.text):
            if self.text[pos] == "<":
                pos = self._markup(pos)
            else:
                pos = self._text_run(pos)
        return self._finish()

    def _cover_bom(self) -> None:
        """Covers a leading BOM as trivia (parser.rs:275-285)."""
        bom = self.source.encoding_facts().bom
        if bom is None:
            return
        length = {BomKind.UTF8: 3, BomKind.UTF16LE: 2, BomKind.UTF16BE: 2}[bom]
        self._push_piece(self._span_raw(0, length), XmlSyntaxKind.BOM, StructuralPieceKind.TRIVIA)

    def _recover_and_resync(self, pos: int) -> int:
        """Recovers one tokenizer failure with a one-scalar error region and
        resumes at the next ``<`` after the failure point (parser.rs:255-269)."""
        start = pos - 1 if pos > 0 else 0
        self._recover_error_region(start, pos)
        next_markup = self.text.find("<", pos + 1)
        if next_markup < 0:
            return len(self.text)
        return next_markup

    def _markup(self, pos: int) -> int:
        text = self.text
        if text.startswith("<?", pos):
            if text.startswith("<?xml", pos) and (
                pos + 5 >= len(text) or text[pos + 5] in (" ", "\t", "\n", "\r", "?")
            ):
                return self._declaration(pos)
            return self._processing_instruction(pos)
        if text.startswith("<!--", pos):
            return self._comment(pos)
        if text.startswith("<![CDATA[", pos):
            return self._cdata(pos)
        if text.startswith("<!DOCTYPE", pos):
            return self._doctype(pos)
        if text.startswith("<!ENTITY", pos):
            if self.dtd_subset_start is not None:
                return self._entity_declaration(pos)
            return self._recover_and_resync(pos)
        if text.startswith("</", pos):
            return self._end_tag(pos)
        if text.startswith("<!", pos):
            return self._markup_declaration(pos)
        return self._start_tag(pos)

    def _declaration(self, pos: int) -> int:
        """`<?xml …?>` declaration (parser.rs:334-503)."""
        text = self.text
        end = text.find("?>", pos + 5)
        if end < 0:
            return self._recover_and_resync(pos)
        span = self._span(pos, end + 2)
        self._push_piece(
            self._span(pos, pos + 5), XmlSyntaxKind.DECLARATION_OPEN, StructuralPieceKind.TOKEN
        )
        cursor = pos + 5
        version: str | None = None
        version_span: Span | None = None
        encoding: tuple[Span, str] | None = None
        standalone: tuple[Span, bool] | None = None
        while cursor < end:
            cursor = self._skip_spaces(cursor, end)
            if text.startswith("?>", cursor):
                break
            name_end = cursor
            while name_end < end and _is_name_char(text[name_end]):
                name_end += 1
            name = text[cursor:name_end]
            if name not in ("version", "encoding", "standalone"):
                return self._recover_and_resync(pos)
            name_span = self._span(cursor, name_end)
            self._push_piece(name_span, XmlSyntaxKind.DECLARATION_NAME, StructuralPieceKind.TOKEN)
            cursor = self._skip_spaces(name_end, end)
            if cursor >= end or text[cursor] != "=":
                return self._recover_and_resync(pos)
            cursor = self._skip_spaces(cursor + 1, end)
            if cursor >= end or text[cursor] not in ('"', "'"):
                return self._recover_and_resync(pos)
            quote = text[cursor]
            value_start = cursor + 1
            value_end = text.find(quote, value_start)
            if value_end < 0 or value_end > end:
                return self._recover_and_resync(pos)
            value_span = self._span(value_start, value_end)
            self._push_piece(value_span, XmlSyntaxKind.DECLARATION_VALUE, StructuralPieceKind.TOKEN)
            value = text[value_start:value_end]
            if name == "version":
                version = value
                version_span = value_span
                if value != "1.0":
                    self._recover(
                        "xml.declaration.version@1", value_span, DiagnosticCategory.SYNTAX
                    )
            elif name == "encoding":
                upper = value.upper()
                selected = self.source.encoding_facts().selected
                agrees = {
                    SourceEncodingKind.UTF8: upper == "UTF-8",
                    SourceEncodingKind.UTF16LE: upper in ("UTF-16", "UTF-16LE"),
                    SourceEncodingKind.UTF16BE: upper in ("UTF-16", "UTF-16BE"),
                }.get(selected.kind, False)
                if not agrees:
                    self._recover(
                        "xml.declaration.conflict@1", value_span, DiagnosticCategory.ENCODING
                    )
                encoding = (value_span, value)
            elif name == "standalone":
                standalone = (value_span, value == "yes")
            cursor = self._skip_spaces(value_end + 1, end)
        if version is None:
            return self._recover_and_resync(pos)
        self._push_piece(
            self._span(end, end + 2), XmlSyntaxKind.DECLARATION_CLOSE, StructuralPieceKind.TOKEN
        )
        declared = XmlDeclarationData(
            span=span,
            version_span=version_span or span,
            version=version,
            encoding=encoding,
            standalone=standalone,
        )
        if self.declaration is not None:
            self._recover("xml.declaration.duplicate@1", span, DiagnosticCategory.SYNTAX)
        self.declaration = declared
        return end + 2

    def _skip_spaces(self, pos: int, end: int) -> int:
        text = self.text
        while pos < end and text[pos] in (" ", "\t", "\n", "\r"):
            pos += 1
        return pos

    def _processing_instruction(self, pos: int) -> int:
        """`<? …?>` (parser.rs:505-579)."""
        text = self.text
        end = text.find("?>", pos + 2)
        if end < 0:
            return self._recover_and_resync(pos)
        content_start = pos + 2
        target_end = content_start
        while target_end < end and _is_name_char(text[target_end]):
            target_end += 1
        if target_end == content_start:
            return self._recover_and_resync(pos)
        span = self._span(pos, end + 2)
        target_span = self._span(content_start, target_end)
        target = text[content_start:target_end]
        if target.lower() == "xml":
            self._recover("xml.pi.target@1", target_span, DiagnosticCategory.SYNTAX)
        content: tuple[Span, str] | None = None
        if target_end < end:
            content_text = text[target_end:end]
            if len(content_text) > self.limits.max_pi_length:
                raise XmlFormationFailure.fatal("xml.limit.pi@1", DiagnosticCategory.CONFORMANCE)
            content = (self._span(target_end, end), content_text)
        if self.dtd_subset_start is not None:
            self._push_piece(span, XmlSyntaxKind.DTD_MARKUP, StructuralPieceKind.TOKEN)
            return end + 2
        self._push_piece(
            self._span(pos, pos + 2),
            XmlSyntaxKind.PROCESSING_INSTRUCTION_OPEN,
            StructuralPieceKind.TOKEN,
        )
        self._push_piece(
            target_span, XmlSyntaxKind.PROCESSING_INSTRUCTION_TARGET, StructuralPieceKind.TOKEN
        )
        if content is not None:
            self._push_piece(
                content[0],
                XmlSyntaxKind.PROCESSING_INSTRUCTION_CONTENT,
                StructuralPieceKind.TOKEN,
            )
        self._push_piece(
            self._span(end, end + 2),
            XmlSyntaxKind.PROCESSING_INSTRUCTION_CLOSE,
            StructuralPieceKind.TOKEN,
        )
        item = XmlPiData(
            ordinal=self._ordinal(),
            span=span,
            target_span=target_span,
            target=target,
            content=content,
        )
        if not self.stack:
            if self.root is None:
                self.prolog.append(XmlPrologItem(XmlPrologItemKind.PROCESSING_INSTRUCTION, item))
            else:
                self.epilog.append(XmlPrologItem(XmlPrologItemKind.PROCESSING_INSTRUCTION, item))
        else:
            self._push_content(XmlContent(XmlContentKind.PROCESSING_INSTRUCTION, item))
        return end + 2

    def _comment(self, pos: int) -> int:
        """`<!-- … -->` (parser.rs:581-644)."""
        text = self.text
        end = text.find("-->", pos + 4)
        if end < 0:
            return self._recover_and_resync(pos)
        span = self._span(pos, end + 3)
        value = text[pos + 4 : end]
        value_span = self._span(pos + 4, end)
        if "--" in value or value.endswith("-"):
            self._recover("xml.comment.content@1", value_span, DiagnosticCategory.SYNTAX)
        if len(value) > self.limits.max_comment_length:
            raise XmlFormationFailure.fatal("xml.limit.comment@1", DiagnosticCategory.CONFORMANCE)
        if self.dtd_subset_start is not None:
            self._push_piece(span, XmlSyntaxKind.DTD_MARKUP, StructuralPieceKind.TRIVIA)
            return end + 3
        self._push_piece(self._span(pos, pos + 4), XmlSyntaxKind.COMMENT_OPEN, StructuralPieceKind.TRIVIA)
        self._push_piece(value_span, XmlSyntaxKind.COMMENT_TEXT, StructuralPieceKind.TRIVIA)
        self._push_piece(self._span(end, end + 3), XmlSyntaxKind.COMMENT_CLOSE, StructuralPieceKind.TRIVIA)
        item = XmlCommentData(
            ordinal=self._ordinal(), span=span, text_span=value_span, text=value
        )
        if not self.stack:
            if self.root is None:
                self.prolog.append(XmlPrologItem(XmlPrologItemKind.COMMENT, item))
            else:
                self.epilog.append(XmlPrologItem(XmlPrologItemKind.COMMENT, item))
        else:
            self._push_content(XmlContent(XmlContentKind.COMMENT, item))
        return end + 3

    def _cdata(self, pos: int) -> int:
        """`<![CDATA[ … ]]>` (parser.rs:1371-1401)."""
        text = self.text
        end = text.find("]]>", pos + 9)
        if end < 0:
            return self._recover_and_resync(pos)
        span = self._span(pos, end + 3)
        value_span = self._span(pos + 9, end)
        value = text[pos + 9 : end]
        if len(value) > self.limits.max_cdata_length:
            raise XmlFormationFailure.fatal("xml.limit.cdata@1", DiagnosticCategory.CONFORMANCE)
        self._push_piece(self._span(pos, pos + 9), XmlSyntaxKind.CDATA_OPEN, StructuralPieceKind.TOKEN)
        self._push_piece(value_span, XmlSyntaxKind.CDATA_TEXT, StructuralPieceKind.TOKEN)
        self._push_piece(self._span(end, end + 3), XmlSyntaxKind.CDATA_CLOSE, StructuralPieceKind.TOKEN)
        item = XmlCdataData(
            ordinal=self._ordinal(), span=span, text_span=value_span, text=value
        )
        self._push_content(XmlContent(XmlContentKind.CDATA, item))
        return end + 3

    def _doctype(self, pos: int) -> int:
        """`<!DOCTYPE …>` (parser.rs:646-911)."""
        text = self.text
        cursor = self._skip_spaces(pos + 9, len(text))
        name_start = cursor
        while cursor < len(text) and _is_name_char(text[cursor]):
            cursor += 1
        if cursor == name_start:
            return self._recover_and_resync(pos)
        name_span = self._span(name_start, cursor)
        self._push_piece(self._span(pos, pos + 9), XmlSyntaxKind.DOCTYPE_OPEN, StructuralPieceKind.TOKEN)
        try:
            qname = self._qname_facts(name_start, cursor, name_span)
        except _ScanFailure:
            return self._recover_and_resync(pos)
        if qname.span.len() > self.limits.max_qname_length:
            raise XmlFormationFailure.fatal("xml.limit.qname@1", DiagnosticCategory.CONFORMANCE)
        self._push_piece(name_span, XmlSyntaxKind.DOCTYPE_NAME, StructuralPieceKind.TOKEN)
        cursor = self._skip_spaces(cursor, len(text))
        self.doctype_name = qname
        external = False
        if text.startswith("SYSTEM", cursor) or text.startswith("PUBLIC", cursor):
            external = True
            cursor = self._skip_spaces(cursor + 6, len(text))
            while cursor < len(text) and text[cursor] not in ("[", ">", "<"):
                cursor += 1
        subset_start: int | None = None
        if cursor < len(text) and text[cursor] == "[":
            subset_start = cursor + 1
            self.dtd_subset_start = subset_start
            cursor = self._scan_dtd_subset(cursor)
            if cursor < 0:
                self.dtd_subset_start = None
                return self._recover_and_resync(pos)
            subset_end = cursor - 1
            subset_length = len(text[subset_start:subset_end])
            if subset_length > self.limits.max_dtd_bytes:
                raise XmlFormationFailure.fatal("xml.limit.dtd@1", DiagnosticCategory.CONFORMANCE)
        if cursor >= len(text) or text[cursor] != ">":
            return self._recover_and_resync(pos)
        raw_span = self._span(pos, cursor + 1)
        if external:
            self.external_subset_recovered = True
            self._recover("xml.dtd.external-subset@1", raw_span, DiagnosticCategory.CONFORMANCE)
        if self.doctype is not None:
            self._recover("xml.dtd.multiple-doctype@1", raw_span, DiagnosticCategory.SYNTAX)
        self._push_piece(
            self._span(cursor, cursor + 1), XmlSyntaxKind.DOCTYPE_CLOSE, StructuralPieceKind.TOKEN
        )
        self.doctype_span_start = self._raw_offset(pos)
        self.dtd_subset_start = None
        self._build_doctype(raw_span)
        return cursor + 1

    def _scan_dtd_subset(self, bracket_pos: int) -> int:
        """Scans the internal subset between ``[`` and ``]>``, admitting
        entity declarations and comments, and flagging excluded declarations
        (parser.rs:747-911). Returns the position of the closing ``>``, or
        -1 when the subset cannot be proven."""
        text = self.text
        cursor = bracket_pos + 1
        while cursor < len(text):
            if text[cursor] == "]":
                if cursor + 1 < len(text) and text[cursor + 1] == ">":
                    return cursor + 1
                return -1
            if text.startswith("<!--", cursor):
                end = text.find("-->", cursor + 4)
                if end < 0:
                    return -1
                span = self._span(cursor, end + 3)
                self._push_piece(span, XmlSyntaxKind.DTD_MARKUP, StructuralPieceKind.TRIVIA)
                cursor = end + 3
                continue
            if text.startswith("<!ENTITY", cursor):
                cursor = self._entity_declaration(cursor)
                continue
            if text.startswith("<!", cursor):
                end = text.find(">", cursor)
                if end < 0:
                    return -1
                if text.startswith("<![", cursor):
                    self._recover(
                        "xml.dtd.conditional-section@1",
                        self._span(cursor, cursor + 2),
                        DiagnosticCategory.CONFORMANCE,
                    )
                elif (
                    text.startswith("<!ELEMENT", cursor)
                    or text.startswith("<!ATTLIST", cursor)
                    or text.startswith("<!NOTATION", cursor)
                ):
                    self._recover(
                        "xml.dtd.validation-declaration@1",
                        self._span(cursor, cursor + 9),
                        DiagnosticCategory.CONFORMANCE,
                    )
                cursor = end + 1
                continue
            cursor += 1
        return -1

    def _entity_declaration(self, pos: int) -> int:
        """`<!ENTITY name "value">` (parser.rs:747-849).

        The closing ``>`` is searched after the value's closing quote, so
        a ``>`` inside the replacement text cannot truncate the
        declaration."""
        text = self.text
        rest_scan = pos + 8
        quote_at = None
        while rest_scan < len(text):
            if text[rest_scan] in ('"', "'"):
                quote_at = rest_scan
                break
            rest_scan += 1
        if quote_at is None:
            return self._recover_and_resync(pos)
        quote = text[quote_at]
        value_end = text.find(quote, quote_at + 1)
        if value_end < 0:
            return self._recover_and_resync(pos)
        end = text.find(">", value_end + 1)
        if end < 0:
            return self._recover_and_resync(pos)
        span = self._span(pos, end + 1)
        self._push_piece(span, XmlSyntaxKind.DTD_MARKUP, StructuralPieceKind.TOKEN)
        body = text[pos + 8 : end]
        stripped_offset = len(body) - len(body.lstrip(" \t\n\r"))
        stripped = body[stripped_offset:]
        if stripped.startswith("%"):
            self._recover("xml.dtd.parameter-entity@1", span, DiagnosticCategory.CONFORMANCE)
            return end + 1
        name_end = 0
        while name_end < len(stripped) and _is_name_char(stripped[name_end]):
            name_end += 1
        if name_end == 0:
            return end + 1
        name = stripped[:name_end]
        tail = stripped[name_end:]
        tail_offset = len(tail) - len(tail.lstrip(" \t\n\r"))
        rest = tail[tail_offset:]
        if rest.startswith("SYSTEM") or rest.startswith("PUBLIC"):
            self._recover("xml.dtd.external-entity@1", span, DiagnosticCategory.CONFORMANCE)
            return end + 1
        if not rest or rest[0] not in ('"', "'"):
            return end + 1
        quote = rest[0]
        value_end = rest.find(quote, 1)
        if value_end < 0:
            return end + 1
        value = rest[1:value_end]
        if len(value) > self.limits.max_attribute_value_length:
            raise XmlFormationFailure.fatal(
                "xml.limit.entity-replacement@1", DiagnosticCategory.CONFORMANCE
            )
        try:
            validate_replacement_text(value)
        except ReplacementError as error:
            if error.kind is ReplacementErrorKind.CONTAINS_MARKUP:
                self._recover(
                    "xml.entity.markup@1", span, DiagnosticCategory.CONFORMANCE
                )
            else:
                self._recover(
                    "xml.entity.illegal-character@1", span, DiagnosticCategory.SYNTAX
                )
            return end + 1
        if "%" in value:
            self._recover("xml.dtd.parameter-entity@1", span, DiagnosticCategory.CONFORMANCE)
            return end + 1
        if predefined_value(name) is not None or name in ("xml", "xmlns"):
            self._recover("xml.entity.reserved-name@1", span, DiagnosticCategory.CONFORMANCE)
            return end + 1
        if any(entity.name == name for entity in self.entities):
            self._recover("xml.entity.duplicate@1", span, DiagnosticCategory.SYNTAX)
            return end + 1
        # The value's exact scalar span: body start + stripped offset +
        # name end + tail offset + opening quote + 1.
        value_start = pos + 8 + stripped_offset + name_end + tail_offset + 1
        declared = EntityDeclarationData(
            span=span,
            name=name,
            replacement_span=self._span(value_start, value_start + len(value)),
            replacement=value,
        )
        try:
            self.entity_state.record_declaration(
                len(value), len(value), self.limits.entity_limits()
            )
        except Exception as breach:
            self._entity_limit(breach, span)
            return end + 1
        self.entities.append(declared)
        return end + 1

    def _markup_declaration(self, pos: int) -> int:
        """Any other ``<!…`` construct outside the subset."""
        text = self.text
        end = text.find(">", pos)
        if end < 0:
            return self._recover_and_resync(pos)
        if text.startswith("<![", pos):
            self._recover(
                "xml.dtd.conditional-section@1",
                self._span(pos, pos + 2),
                DiagnosticCategory.CONFORMANCE,
            )
        elif (
            text.startswith("<!ELEMENT", pos)
            or text.startswith("<!ATTLIST", pos)
            or text.startswith("<!NOTATION", pos)
        ):
            self._recover(
                "xml.dtd.validation-declaration@1",
                self._span(pos, pos + 9),
                DiagnosticCategory.CONFORMANCE,
            )
        return end + 1

    def _qname_facts(self, start: int, end: int, span: Span) -> QNameFacts:
        """Builds QName facts from a scanned name span (parser.rs:1916-1942)."""
        text = self.text[start:end]
        colon = text.find(":")
        if colon < 0:
            return QNameFacts(
                prefix=None, local=text, span=span, prefix_span=None, local_span=span
            )
        prefix = text[:colon]
        local = text[colon + 1 :]
        if ":" in local:
            raise _ScanFailure("multiple colons in one QName")
        return QNameFacts(
            prefix=prefix,
            local=local,
            span=span,
            prefix_span=self._span(start, start + colon),
            local_span=self._span(start + colon + 1, end),
        )

    def _start_tag(self, pos: int) -> int:
        """`<name …>` / `<name …/>` (parser.rs:913-961)."""
        text = self.text
        cursor = pos + 1
        name_start = cursor
        while cursor < len(text) and _is_name_char(text[cursor]):
            cursor += 1
        if cursor == name_start:
            return self._recover_and_resync(pos)
        name_span = self._span(name_start, cursor)
        self._push_piece(self._span(pos, pos + 1), XmlSyntaxKind.TAG_OPEN, StructuralPieceKind.TOKEN)
        self._push_qname_parts(name_start, cursor)
        try:
            qname = self._qname_facts(name_start, cursor, name_span)
        except _ScanFailure:
            return self._recover_and_resync(pos)
        if qname.span.len() > self.limits.max_qname_length:
            raise XmlFormationFailure.fatal("xml.limit.qname@1", DiagnosticCategory.CONFORMANCE)
        if len(self.nodes) >= self.limits.common.max_node_count:
            raise XmlFormationFailure.fatal("xml.limit.node@1", DiagnosticCategory.CONFORMANCE)
        if len(self.nodes) >= self.limits.max_element_count:
            raise XmlFormationFailure.fatal("xml.limit.element@1", DiagnosticCategory.CONFORMANCE)
        if len(self.stack) >= self.limits.common.max_nesting_depth:
            raise XmlFormationFailure.fatal("xml.limit.depth@1", DiagnosticCategory.CONFORMANCE)
        scope = self.stack[-1].scope if self.stack else NamespaceScope.new()
        frame = _Frame(pos, name_span, qname)
        frame.scope = scope
        self.stack.append(frame)
        cursor = self._scan_attributes(cursor)
        if cursor < 0:
            return self._recover_and_resync(pos)
        if self.text[cursor : cursor + 2] == "/>":
            self._push_piece(
                self._span(cursor, cursor + 2),
                XmlSyntaxKind.EMPTY_ELEMENT_CLOSE,
                StructuralPieceKind.TOKEN,
            )
            frame.span = self._span(pos, cursor + 2)
            self._finalize_start_tag()
            self._close_frame(self._span(pos, cursor + 2))
            return cursor + 2
        self._push_piece(
            self._span(cursor, cursor + 1), XmlSyntaxKind.TAG_CLOSE, StructuralPieceKind.TOKEN
        )
        frame.span = self._span(pos, cursor + 1)
        self._finalize_start_tag()
        return cursor + 1

    def _scan_attributes(self, cursor: int) -> int:
        """Scans attributes until ``>`` or ``/>`` (parser.rs:963-1063).
        Returns the position of the tag close, or -1 on a scan failure."""
        text = self.text
        while True:
            cursor = self._skip_spaces(cursor, len(text))
            if cursor >= len(text):
                return -1
            if text[cursor] in (">", "/"):
                return cursor
            name_start = cursor
            while cursor < len(text) and _is_name_char(text[cursor]):
                cursor += 1
            if cursor == name_start:
                return -1
            name_span = self._span(name_start, cursor)
            try:
                qname = self._qname_facts(name_start, cursor, name_span)
            except _ScanFailure:
                return -1
            cursor = self._skip_spaces(cursor, len(text))
            if cursor >= len(text) or text[cursor] != "=":
                return -1
            eq_span = self._span(cursor, cursor + 1)
            cursor = self._skip_spaces(cursor + 1, len(text))
            if cursor >= len(text) or text[cursor] not in ('"', "'"):
                return -1
            quote = text[cursor]
            quote_span = self._span(cursor, cursor + 1)
            value_start = cursor + 1
            value_end = text.find(quote, value_start)
            if value_end < 0:
                return -1
            value_span = self._span(value_start, value_end)
            close_quote_span = self._span(value_end, value_end + 1)
            attr_span = self._span(name_start, value_end + 1)
            is_declaration = qname.prefix == "xmlns" or (
                qname.prefix is None and qname.local == "xmlns"
            )
            self._push_piece(
                name_span,
                XmlSyntaxKind.NAMESPACE_DECLARATION
                if is_declaration
                else XmlSyntaxKind.ATTRIBUTE_NAME,
                StructuralPieceKind.TOKEN,
            )
            self._push_piece(eq_span, XmlSyntaxKind.EQUALS, StructuralPieceKind.TOKEN)
            self._push_piece(quote_span, XmlSyntaxKind.QUOTE, StructuralPieceKind.TOKEN)
            fragments, normalized = self._value_fragments(value_start, value_end)
            self._push_piece(close_quote_span, XmlSyntaxKind.QUOTE, StructuralPieceKind.TOKEN)
            frame = self.stack[-1]
            declaration_count = len(frame.pending_declarations) + len(frame.namespaces)
            attribute_count = len(frame.pending_attributes) + len(frame.attributes)
            if attribute_count >= self.limits.max_attribute_count or (
                declaration_count >= self.limits.max_namespace_declaration_count
            ):
                raise XmlFormationFailure.fatal(
                    "xml.limit.attribute@1", DiagnosticCategory.CONFORMANCE
                )
            if is_declaration:
                if len(normalized) > self.limits.max_namespace_uri_length:
                    raise XmlFormationFailure.fatal(
                        "xml.limit.namespace-uri@1", DiagnosticCategory.CONFORMANCE
                    )
                frame.pending_declarations.append((qname, normalized, value_span))
            else:
                if len(normalized) > self.limits.max_attribute_value_length:
                    raise XmlFormationFailure.fatal(
                        "xml.limit.attribute-value@1", DiagnosticCategory.CONFORMANCE
                    )
                frame.pending_attributes.append(
                    XmlAttributeData(
                        ordinal=self._ordinal(),
                        span=attr_span,
                        qname=qname,
                        expanded=None,
                        namespace_error=None,
                        single_quote=quote == "'",
                        value_span=value_span,
                        fragments=tuple(fragments),
                        normalized_value=normalized,
                    )
                )
            cursor = value_end + 1

    def _finalize_start_tag(self) -> None:
        """Resolves element and attribute names once the whole start tag has
        been read (parser.rs:1065-1174)."""
        frame = self.stack[-1]
        pending_declarations = frame.pending_declarations
        pending_attributes = frame.pending_attributes
        frame.pending_declarations = []
        frame.pending_attributes = []
        scope = frame.scope
        namespaces: list[XmlNamespaceBindingData] = []
        for qname, uri, uri_span in pending_declarations:
            prefix = qname.local if qname.prefix == "xmlns" else None
            try:
                scope = scope.declare(prefix, uri)
            except NamespaceError as error:
                self._recover(error.code, qname.span, DiagnosticCategory.SEMANTIC)
                continue
            namespaces.append(
                XmlNamespaceBindingData(
                    ordinal=self._ordinal(),
                    span=qname.span,
                    prefix=prefix,
                    uri_span=uri_span,
                    uri=uri,
                )
            )
        element_qname = frame.qname.qname()
        try:
            frame.expanded = scope.resolve_element(element_qname)
            frame.namespace_error = None
        except NamespaceError as error:
            frame.expanded = None
            frame.namespace_error = error
            self._recover(error.code, frame.qname.span, DiagnosticCategory.SEMANTIC)
        attributes: list[XmlAttributeData] = []
        for pending in pending_attributes:
            expanded_name = None
            namespace_error = None
            try:
                expanded_name = scope.resolve_attribute(pending.qname.qname())
            except NamespaceError as error:
                namespace_error = error
                self._recover(error.code, pending.qname.span, DiagnosticCategory.SEMANTIC)
            duplicate = False
            if expanded_name is not None:
                duplicate = any(
                    attribute.expanded == expanded_name for attribute in attributes
                ) or any(
                    NamespaceScope.declaration_expanded_name(binding.prefix) == expanded_name
                    for binding in namespaces
                )
            if duplicate:
                self._recover(
                    "xml.namespace.duplicate-attribute@1",
                    pending.qname.span,
                    DiagnosticCategory.SEMANTIC,
                )
            attribute = XmlAttributeData(
                ordinal=pending.ordinal,
                span=pending.span,
                qname=pending.qname,
                expanded=expanded_name,
                namespace_error=namespace_error,
                single_quote=pending.single_quote,
                value_span=pending.value_span,
                fragments=pending.fragments,
                normalized_value=pending.normalized_value,
            )
            attributes.append(attribute)
        frame.scope = scope
        frame.namespaces.extend(namespaces)
        frame.attributes.extend(attributes)

    def _end_tag(self, pos: int) -> int:
        """`</name>` (parser.rs:1215-1246)."""
        text = self.text
        cursor = pos + 2
        name_start = cursor
        while cursor < len(text) and _is_name_char(text[cursor]):
            cursor += 1
        if cursor == name_start:
            return self._recover_and_resync(pos)
        name_span = self._span(name_start, cursor)
        self._push_piece(
            self._span(pos, pos + 2), XmlSyntaxKind.END_TAG_OPEN, StructuralPieceKind.TOKEN
        )
        self._push_qname_parts(name_start, cursor)
        try:
            end_qname = self._qname_facts(name_start, cursor, name_span)
        except _ScanFailure:
            return self._recover_and_resync(pos)
        cursor = self._skip_spaces(cursor, len(text))
        if cursor >= len(text) or text[cursor] != ">":
            return self._recover_and_resync(pos)
        self._push_piece(
            self._span(cursor, cursor + 1), XmlSyntaxKind.TAG_CLOSE, StructuralPieceKind.TOKEN
        )
        if self.stack and self.stack[-1].qname.qname() != end_qname.qname():
            self._recover(
                "xml.tree.mismatched-end-tag@1", name_span, DiagnosticCategory.SYNTAX
            )
        self._close_frame(self._span(pos, cursor + 1))
        return cursor + 1

    def _push_qname_parts(self, start: int, end: int) -> None:
        """Pushes the QName part pieces for one element or end-tag name
        (parser.rs:1945-1976)."""
        text = self.text[start:end]
        colon = text.find(":")
        if colon < 0:
            self._push_piece(
                self._span(start, end), XmlSyntaxKind.LOCAL_NAME, StructuralPieceKind.TOKEN
            )
            return
        self._push_piece(
            self._span(start, start + colon), XmlSyntaxKind.PREFIX, StructuralPieceKind.TOKEN
        )
        self._push_piece(
            self._span(start + colon, start + colon + 1), XmlSyntaxKind.COLON, StructuralPieceKind.TOKEN
        )
        self._push_piece(
            self._span(start + colon + 1, end), XmlSyntaxKind.LOCAL_NAME, StructuralPieceKind.TOKEN
        )

    def _close_frame(self, end_tag_span: Span) -> None:
        """Closes one element frame (parser.rs:1250-1305)."""
        if not self.stack:
            self._recover("xml.tree.extra-end-tag@1", end_tag_span, DiagnosticCategory.SYNTAX)
            return
        frame = self.stack.pop()
        index = len(self.nodes)
        element = XmlElementData(
            index=index,
            span=frame.span,
            qname=frame.qname,
            expanded=frame.expanded,
            namespace_error=frame.namespace_error,
            scope=frame.scope,
            namespaces=tuple(frame.namespaces),
            attributes=tuple(frame.attributes),
            children=tuple(frame.children),
        )
        for child in element.children:
            self.parent_of[child] = index
        self.parent_of.append(None)
        self.nodes.append(XmlContent(XmlContentKind.ELEMENT, element))
        if self.stack:
            parent = self.stack[-1]
            if len(parent.children) >= self.limits.max_mixed_content_items:
                self._recover(
                    "xml.limit.mixed-content@1",
                    self.nodes[index].span,
                    DiagnosticCategory.CONFORMANCE,
                )
            else:
                parent.children.append(index)
        elif self.root is None:
            self.root = index
        else:
            self._recover(
                "xml.tree.multiple-roots@1",
                self.nodes[index].span,
                DiagnosticCategory.SYNTAX,
            )

    def _text_run(self, pos: int) -> int:
        """One character-data run (parser.rs:1307-1369)."""
        text = self.text
        cursor = pos
        while cursor < len(text) and text[cursor] != "<":
            cursor += 1
        value = text[pos:cursor]
        whitespace_only = all(character in (" ", "\t", "\n", "\r") for character in value)
        raw_span = self._span(pos, cursor)
        if not self.stack:
            if whitespace_only:
                self._push_whitespace_pieces(pos, cursor)
                item = XmlPrologItem(XmlPrologItemKind.WHITESPACE, raw_span)
                if self.root is None:
                    self.prolog.append(item)
                else:
                    self.epilog.append(item)
                return cursor
            # Non-whitespace character data outside the document element is
            # recovered; the piece is an error region and the literal text is
            # still preserved as an orphan text occurrence (parser.rs:1322-1344).
            self._recover("xml.syntax.text-outside-root@1", raw_span, DiagnosticCategory.SYNTAX)
            self._push_piece(
                raw_span, XmlSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION
            )
            self._push_content(
                XmlContent(
                    XmlContentKind.TEXT,
                    XmlTextData(
                        ordinal=self._ordinal(),
                        span=raw_span,
                        fragments=(ReferenceFragment.literal(raw_span, value),),
                    ),
                )
            )
            return cursor
        if whitespace_only:
            self._push_whitespace_pieces(pos, cursor)
        else:
            if len(value) > self.limits.max_text_length:
                raise XmlFormationFailure.fatal(
                    "xml.limit.text@1", DiagnosticCategory.CONFORMANCE
                )
            fragments = self._text_fragments(pos, cursor, XmlSyntaxKind.TEXT)
            self._push_content(
                XmlContent(
                    XmlContentKind.TEXT,
                    XmlTextData(
                        ordinal=self._ordinal(), span=raw_span, fragments=tuple(fragments)
                    ),
                )
            )
            return cursor
        self._push_content(
            XmlContent(
                XmlContentKind.TEXT,
                XmlTextData(
                    ordinal=self._ordinal(),
                    span=raw_span,
                    fragments=(ReferenceFragment.literal(raw_span, value),),
                ),
            )
        )
        return cursor

    # -- finish ----------------------------------------------------------------

    def _build_doctype(self, end_span: Span) -> None:
        """Assembles the immutable DOCTYPE facts once its end is known
        (parser.rs:691-711)."""
        if self.doctype_span_start is None or self.doctype_name is None:
            return
        span = self._span_raw(self.doctype_span_start, end_span.end_byte)
        self.doctype = XmlDoctypeData(
            span=span,
            name=self.doctype_name,
            entities=tuple(self.entities),
            recovered=self.external_subset_recovered,
        )

    def _finish(self) -> Document:
        """Completes formation (parser.rs:1792-1914)."""
        if self.stack:
            self.recovered = True
            self.diagnostics.append(
                XmlDiagnostic(
                    code="xml.tree.unclosed-element@1",
                    category=DiagnosticCategory.SYNTAX,
                    severity=Severity.ERROR,
                    primary=None,
                    occurrence=len(self.diagnostics),
                )
            )
        if self.root is None:
            self.recovered = True
            self.diagnostics.append(
                XmlDiagnostic(
                    code="xml.tree.missing-root@1",
                    category=DiagnosticCategory.SYNTAX,
                    severity=Severity.ERROR,
                    primary=None,
                    occurrence=len(self.diagnostics),
                )
            )
        if self.root is not None and self.doctype_name is not None:
            root_data = self.nodes[self.root].data
            if root_data.qname.qname() != self.doctype_name.qname():
                self._recover(
                    "xml.doctype.root-mismatch@1",
                    root_data.qname.span,
                    DiagnosticCategory.SYNTAX,
                )
        status = FormationStatus.RECOVERED if self.recovered else FormationStatus.COMPLETE
        source_len = self.source.len()
        pieces = sorted(self.pieces, key=lambda entry: entry[0].start_byte)
        final_pieces: list[tuple[Span, XmlSyntaxKind, StructuralPieceKind]] = []
        next_byte = 0
        for span, kind, structural in pieces:
            start = span.start_byte
            if start > next_byte:
                gap = self._span_raw(next_byte, start)
                final_pieces.append(
                    (
                        gap,
                        XmlSyntaxKind.ERROR_REGION
                        if self.recovered
                        else XmlSyntaxKind.WHITESPACE,
                        StructuralPieceKind.ERROR_REGION
                        if self.recovered
                        else StructuralPieceKind.TRIVIA,
                    )
                )
            next_byte = span.end_byte
            final_pieces.append((span, kind, structural))
        if next_byte < source_len:
            gap = self._span_raw(next_byte, source_len)
            final_pieces.append(
                (
                    gap,
                    XmlSyntaxKind.ERROR_REGION if self.recovered else XmlSyntaxKind.WHITESPACE,
                    StructuralPieceKind.ERROR_REGION
                    if self.recovered
                    else StructuralPieceKind.TRIVIA,
                )
            )
        final_pieces.sort(key=lambda entry: entry[0].start_byte)
        structural_pieces: list[StructuralPiece] = []
        syntax_kinds: list[XmlSyntaxKind] = []
        for span, kind, structural in final_pieces:
            structural_pieces.append(StructuralPiece(span=span, kind=structural))
            syntax_kinds.append(kind)
        try:
            index = LosslessStructuralIndex.new(self.authority.identity, source_len, structural_pieces)
        except Exception:
            raise XmlFormationFailure.fatal(
                "xml.source.coverage@1", DiagnosticCategory.CONFORMANCE
            ) from None
        diagnostics = sorted(
            self.diagnostics,
            key=lambda diagnostic: (
                diagnostic.primary.start_byte if diagnostic.primary is not None else -1,
                diagnostic.code,
                diagnostic.occurrence,
            ),
        )
        return Document(
            source=self.source,
            authority=self.authority,
            status=status,
            declaration=self.declaration,
            doctype=self.doctype,
            prolog=tuple(self.prolog),
            root=self.root,
            epilog=tuple(self.epilog),
            syntax=index,
            syntax_kinds=tuple(syntax_kinds),
            diagnostics=tuple(diagnostics),
            nodes=tuple(self.nodes),
            parent_of=tuple(self.parent_of),
            parse_limits=self.limits,
        )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse(
    source_bytes: bytes,
    profile: XmlProfile,
    selection: XmlEncodingSelection,
    limits: XmlParseLimits,
) -> Document:
    """Forms one `xml.1.0-safe@1` document from a complete document entity
    (lib.rs:174-186; parser.rs:22-46).

    The Profile is selected before formation and never by extension. The
    parser consumes the supplied bytes and opens no other entity, file, URI,
    network connection, registry, classpath, or catalog (RFC 0012 §1).
    Raises :class:`XmlFormationFailure` on any fatal failure; never returns
    a partial document."""
    request = _encoding_request(selection)
    from consema.document.source import SourceError

    try:
        source = SourceSnapshot.from_raw(
            source_bytes,
            request,
            SourceLimits(
                max_raw_bytes=limits.common.max_source_bytes,
                max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
                max_decoded_scalars=limits.max_decoded_scalars,
            ),
        )
    except SourceError as error:
        raise XmlFormationFailure(
            [
                XmlDiagnostic(
                    code=error.code,
                    category=DiagnosticCategory.ENCODING,
                    severity=Severity.ERROR,
                    primary=None,
                    occurrence=0,
                )
            ]
        ) from None
    _validate_profile_encoding(source, selection)
    decoded = source.decoded_text()
    if decoded is None:
        raise XmlFormationFailure.fatal(
            "xml.source.decoding@1", DiagnosticCategory.ENCODING
        )
    return _Parser(source, profile, limits, decoded).parse()


def parse_with_profile(
    source_bytes: bytes,
    selection: XmlEncodingSelection | None = None,
    limits: XmlParseLimits | None = None,
) -> Document:
    """Convenience formation for the single frozen profile
    ``xml.1.0-safe@1`` (lib.rs:61-66)."""
    return parse(
        source_bytes,
        XmlProfile.SAFE_V1,
        selection or XmlEncodingSelection.profile_default(),
        limits or XmlParseLimits(),
    )


def _encoding_request(selection: XmlEncodingSelection) -> EncodingRequest:
    """Resolves the source encoding request under the RFC 0012 §2 table
    (parser.rs:56-80)."""
    if selection.kind is XmlEncodingSelectionKind.PROFILE_DEFAULT:
        return EncodingRequest.new(SourceEncoding.utf8()).with_bom_policy(
            BomPolicy.DETECT_UNICODE
        )
    encoding = selection.encoding
    if encoding is None or encoding.kind not in (
        SourceEncodingKind.UTF8,
        SourceEncodingKind.UTF16LE,
        SourceEncodingKind.UTF16BE,
    ):
        # UTF-32, Latin-1, Windows code pages, and other IANA encodings are
        # explicit v1 Profile exclusions (RFC 0012 §2, lines 62-67).
        raise XmlFormationFailure.fatal("xml.profile.encoding@1", DiagnosticCategory.CONFORMANCE)
    return EncodingRequest.new(SourceEncoding.utf8()).with_caller_override(encoding)


def _validate_profile_encoding(source: SourceSnapshot, selection: XmlEncodingSelection) -> None:
    """Validates the resolved encoding under the RFC 0012 §2 table
    (parser.rs:82-108)."""
    facts = source.encoding_facts()
    if selection.kind is XmlEncodingSelectionKind.PROFILE_DEFAULT:
        valid = facts.selected.kind in (
            SourceEncodingKind.UTF8,
            SourceEncodingKind.UTF16LE,
            SourceEncodingKind.UTF16BE,
        )
    else:
        encoding = selection.encoding
        if encoding is None:
            valid = False
        elif encoding.kind is SourceEncodingKind.UTF8:
            valid = facts.selected == encoding
        elif encoding.kind is SourceEncodingKind.UTF16LE:
            valid = facts.selected == encoding and facts.bom is BomKind.UTF16LE
        elif encoding.kind is SourceEncodingKind.UTF16BE:
            valid = facts.selected == encoding and facts.bom is BomKind.UTF16BE
        else:
            valid = False
    if not valid:
        raise XmlFormationFailure.fatal("xml.profile.encoding@1", DiagnosticCategory.CONFORMANCE)
