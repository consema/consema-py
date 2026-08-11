"""The Java Properties natural/logical-line parser producing the immutable
document.

Authority (Rust arbitration for exact semantics):

- Parse entry and profile/source contract: crates/consema-properties/src/
  parser.rs:17-91 - encoding request construction (parser.rs:38-55),
  snapshot construction under PropertiesParseLimits (parser.rs:24-33),
  profile/encoding validation with java-properties.source.profile-
  encoding@1 (parser.rs:57-91).
- Atom model and natural-line scanning: parser.rs:93-108, 230-298 (limits
  natural-lines / natural-line-scalars / natural-line-bytes; CR, LF, CRLF,
  EOF terminators).
- Logical-line assembly: parser.rs:352-469 - odd trailing backslash runs
  continue across the terminator (the final backslash, terminator, and
  leading Properties whitespace of the following natural line contribute
  no code units; RFC 0010 section 5, docs/rfcs/0010-...:132-158); the EOF
  unmatched-backslash rule retains the byte as a ContinuationMarker.
- Key/separator/element grammar: parser.rs:471-507 - first unescaped
  ``=``, ``:``, or Properties whitespace terminates the raw key; optional
  ``=``/``:`` plus surrounding whitespace forms the separator (RFC 0010
  section 6, docs/rfcs/0010-...:159-181).
- Escape processing: parser.rs:909-996 - named, backslash, Unicode (one
  lowercase ``u`` + exactly four hex digits), and dropped-backslash kinds;
  no recursive decoding (RFC 0010 section 7, docs/rfcs/0010-...:183-206).
- Recovery: parser.rs:626-666 - a malformed Unicode escape forms one
  deterministic error record with stable diagnostic
  java-properties.parse.malformed-unicode-escape@1; valid records before
  and after remain inspectable but cannot be projected as a partial
  completed property list (RFC 0010 section 8, docs/rfcs/0010-...:208-234).
- Duplicate groups: parser.rs:668-696 - deterministic exact-code-unit
  groups numbered from 1 in sorted-key order.
- Structural pieces: parser.rs:698-729 with the Token/Trivia/ErrorRegion
  classification parser.rs:1002-1017; exhaustive, non-overlapping syntax
  coverage over every raw byte (RFC 0010 section 9).
- Natural/logical/comment/property/escape records: parser.rs:310-350
  (comments), 509-624 (property records with key/value anchors,
  fragments, value state, escapes), and the lib.rs record accessors
  (lib.rs:309-588).

Golden transcription targets: conformance/vectors/java-properties-v1.json
cases formation.* (lines 5-59). go/properties is a cross-reference only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from consema.document.source import (
    BomPolicy,
    DecodedOffset,
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    Span,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.properties.errors import (
    PropertiesDiagnostic,
    PropertiesFormationFailure,
    PropertiesFormationFailureKind,
    PropertiesSeverity,
    sort_diagnostics,
)
from consema.properties.java_string import JavaString
from consema.properties.kinds import (
    PropertiesEscapeKind,
    PropertiesLogicalLineKind,
    PropertiesProfile,
    PropertiesSyntaxKind,
    PropertiesValueState,
)
from consema.properties.limits import (
    PropertiesEncodingSelection,
    PropertiesEncodingSelectionKind,
    PropertiesParseLimits,
)
from consema.protocol.error_registry import DiagnosticCategory

MALFORMED_UNICODE_ESCAPE_CODE = "java-properties.parse.malformed-unicode-escape@1"

# Properties whitespace is exactly space, tab, and form feed (RFC 0010
# section 5, docs/rfcs/0010-...:140-141).
_PROPERTIES_WHITESPACE = frozenset((" ", "\t", ""))


def is_properties_whitespace(character: str) -> bool:
    """Exact space/tab/form-feed test (parser.rs:998-1000)."""
    return character in _PROPERTIES_WHITESPACE


@dataclass(frozen=True, slots=True)
class _Atom:
    """One decoded scalar with its exact raw span and pending syntax."""

    ch: str
    raw_start: int
    raw_end: int
    syntax: PropertiesSyntaxKind | None = None


@dataclass(frozen=True, slots=True)
class _ScannedLine:
    """One natural line expressed over the atom list."""

    atom_start: int
    atom_content_end: int
    atom_end: int
    natural_index: int


@dataclass(frozen=True, slots=True)
class _EscapeSpec:
    """One escape occurrence: its logical atoms, kind, and output range."""

    atom_indices: tuple[int, ...]
    kind: PropertiesEscapeKind
    output_start: int
    output_end: int


@dataclass(frozen=True, slots=True)
class _DecodedJavaString:
    """Decoded key or element content."""

    units: tuple[int, ...]
    escapes: tuple[_EscapeSpec, ...]
    unicode_escapes: int


@dataclass(frozen=True, slots=True)
class _DecodeError:
    """Malformed escape region over the global atom list."""

    atom_start: int
    atom_end: int


def parse(
    raw: bytes,
    profile: PropertiesProfile,
    selection: PropertiesEncodingSelection,
    limits: PropertiesParseLimits,
) -> object:
    """Parses one immutable Java Properties snapshot under one exact
    profile/source contract (parser.rs:17-36).

    Returns the ``PropertiesDocument`` from :mod:`consema.properties.
    document`; raises :class:`PropertiesFormationFailure` for fatal limits
    or the profile/source mismatch, and the typed :class:`SourceError` for
    source snapshot failures (invalid sequences, unsupported BOM, source
    resource limits).
    """
    request = _encoding_request(profile, selection)
    try:
        source = SourceSnapshot.from_raw(
            raw,
            request,
            SourceLimits(
                max_raw_bytes=limits.common.max_source_bytes,
                max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
                max_decoded_scalars=limits.max_decoded_scalars,
            ),
        )
    except SourceError:
        raise
    _validate_profile_encoding(source, profile, selection)
    return _Parser(source, profile, limits).parse()


def parse_reader(
    raw: bytes,
    encoding: SourceEncoding,
    limits: PropertiesParseLimits,
) -> object:
    """Parses Reader input using one explicit published text encoding
    (lib.rs:788-799)."""
    return parse(
        raw,
        PropertiesProfile.READER_V1,
        PropertiesEncodingSelection.reader(encoding),
        limits,
    )


def parse_latin1(raw: bytes, limits: PropertiesParseLimits) -> object:
    """Parses InputStream-compatible Latin-1 bytes with marker bytes as
    content (lib.rs:802-812)."""
    return parse(
        raw,
        PropertiesProfile.LATIN1_V1,
        PropertiesEncodingSelection.latin1(),
        limits,
    )


def _encoding_request(
    profile: PropertiesProfile, selection: PropertiesEncodingSelection
) -> EncodingRequest:
    """Explicit source request (parser.rs:38-55)."""
    if (
        profile is PropertiesProfile.READER_V1
        and selection.kind is PropertiesEncodingSelectionKind.READER
        and selection.encoding is not None
    ):
        return EncodingRequest.new(selection.encoding).with_caller_override(
            selection.encoding
        )
    if (
        profile is PropertiesProfile.LATIN1_V1
        and selection.kind is PropertiesEncodingSelectionKind.LATIN1
    ):
        return (
            EncodingRequest.new(SourceEncoding.latin1())
            .with_caller_override(SourceEncoding.latin1())
            .with_bom_policy(BomPolicy.TREAT_AS_CONTENT)
        )
    raise _profile_failure()


def _validate_profile_encoding(
    source: SourceSnapshot,
    profile: PropertiesProfile,
    selection: PropertiesEncodingSelection,
) -> None:
    """Profile/source contract validation (parser.rs:57-81)."""
    facts = source.encoding_facts()
    if profile is PropertiesProfile.READER_V1:
        valid = (
            selection.kind is PropertiesEncodingSelectionKind.READER
            and selection.encoding is not None
            and facts.selected == selection.encoding
            and facts.bom_policy is BomPolicy.DETECT_UNICODE
        )
    else:
        valid = (
            selection.kind is PropertiesEncodingSelectionKind.LATIN1
            and facts.selected == SourceEncoding.latin1()
            and facts.bom_policy is BomPolicy.TREAT_AS_CONTENT
            and facts.bom is None
        )
    if not valid:
        raise _profile_failure()


def _profile_failure() -> PropertiesFormationFailure:
    """Fatal profile/source mismatch carrying the format-owned code
    (parser.rs:83-91)."""
    return PropertiesFormationFailure(PropertiesFormationFailureKind.PROFILE_ENCODING)


class _Parser:
    """One deterministic parse over an immutable snapshot (parser.rs:130-880)."""

    def __init__(
        self,
        source: SourceSnapshot,
        profile: PropertiesProfile,
        limits: PropertiesParseLimits,
    ) -> None:
        self.source = source
        self.profile = profile
        self.limits = limits
        self.authority = DocumentAuthority.fresh()
        self.root_node = self.authority.node_ref(0, NodeRole.PROPERTIES_DOCUMENT)
        self.next_node = 1
        self.atoms = _build_atoms(source)
        self.lines: list[_ScannedLine] = []
        self.natural_lines = []
        self.logical_lines = []
        self.properties = []
        self.comments = []
        self.escapes = []
        self.error_lines = []
        self.diagnostics: list[PropertiesDiagnostic] = []
        self.occurrence = 0
        self.recovered = False
        self.total_java_units = 0
        self.total_unicode_escapes = 0
        self._scan_natural_lines()

    # -- entry --------------------------------------------------------------

    def parse(self) -> object:
        """Complete the document after exhaustive processing
        (parser.rs:186-228)."""
        line_index = 0
        while line_index < len(self.lines):
            if self._is_blank(line_index):
                self._mark_line_content(line_index, PropertiesSyntaxKind.WHITESPACE)
                line_index += 1
            elif self._is_comment(line_index):
                self._add_comment(line_index)
                line_index += 1
            else:
                line_index = self._add_logical_line(line_index)
        self._assign_duplicate_groups()
        pieces, syntax_kinds = self._build_structural_pieces()
        structural_index = LosslessStructuralIndex.new(
            self.authority.identity, len(self.source.raw), pieces
        )
        sort_diagnostics(self.diagnostics)
        from consema.properties.document import PropertiesDocument

        return PropertiesDocument(
            authority=self.authority,
            source=self.source,
            profile=self.profile,
            structural_index=structural_index,
            syntax_kinds=tuple(syntax_kinds),
            _formation_status=(
                FormationStatus.RECOVERED if self.recovered else FormationStatus.COMPLETE
            ),
            diagnostics=tuple(self.diagnostics),
            natural_lines=tuple(self.natural_lines),
            logical_lines=tuple(self.logical_lines),
            properties=tuple(self.properties),
            comments=tuple(self.comments),
            escapes=tuple(self.escapes),
            error_lines=tuple(self.error_lines),
            parse_limits=self.limits,
            root_node=self.root_node,
        )

    # -- natural lines ------------------------------------------------------

    def _scan_natural_lines(self) -> None:
        """Splits the atom list into natural lines with exact terminators
        (parser.rs:230-298)."""
        start = 0
        if (
            self.source.encoding_facts().bom is not None
            and self.atoms
            and self.atoms[0].ch == "﻿"
        ):
            self.atoms[0] = _replace(self.atoms[0], syntax=PropertiesSyntaxKind.BOM)
            start = 1
        cursor = start
        while cursor < len(self.atoms):
            line_start = cursor
            while cursor < len(self.atoms) and self.atoms[cursor].ch not in ("\r", "\n"):
                cursor += 1
            content_end = cursor
            if cursor < len(self.atoms):
                if (
                    self.atoms[cursor].ch == "\r"
                    and cursor + 1 < len(self.atoms)
                    and self.atoms[cursor + 1].ch == "\n"
                ):
                    cursor += 2
                else:
                    cursor += 1
            end = cursor
            self._check_limit(
                "natural-lines",
                len(self.lines) + 1,
                self.limits.max_natural_lines,
            )
            scalar_count = content_end - line_start
            self._check_limit(
                "natural-line-scalars",
                scalar_count,
                self.limits.max_natural_line_scalars,
            )
            span = self._atom_span(line_start, end)
            self._check_limit(
                "natural-line-bytes",
                span.len(),
                self.limits.max_natural_line_bytes,
            )
            content_span = self._atom_span(line_start, content_end)
            line_break_span = None
            if content_end < end:
                self._mark_atoms(content_end, end, PropertiesSyntaxKind.LINE_BREAK)
                line_break_span = self._atom_span(content_end, end)
            node = self._issue_node(NodeRole.PROPERTIES_NATURAL_LINE)
            natural_index = len(self.natural_lines)
            from consema.properties.document import PropertiesNaturalLine

            self.natural_lines.append(
                PropertiesNaturalLine(
                    node=node,
                    span=span,
                    content_span=content_span,
                    line_break_span=line_break_span,
                )
            )
            self.lines.append(
                _ScannedLine(
                    atom_start=line_start,
                    atom_content_end=content_end,
                    atom_end=end,
                    natural_index=natural_index,
                )
            )

    def _is_blank(self, line_index: int) -> bool:
        """A natural line whose content is all Properties whitespace
        (parser.rs:300-305)."""
        line = self.lines[line_index]
        return all(
            is_properties_whitespace(atom.ch)
            for atom in self.atoms[line.atom_start : line.atom_content_end]
        )

    def _is_comment(self, line_index: int) -> bool:
        """A comment line has ``#`` or ``!`` as its first non-whitespace
        character (parser.rs:307-313; RFC 0010 section 5)."""
        line = self.lines[line_index]
        for atom in self.atoms[line.atom_start : line.atom_content_end]:
            if not is_properties_whitespace(atom.ch):
                return atom.ch in ("#", "!")
        return False

    def _mark_line_content(self, line_index: int, syntax: PropertiesSyntaxKind) -> None:
        line = self.lines[line_index]
        self._mark_atoms(line.atom_start, line.atom_content_end, syntax)

    def _add_comment(self, line_index: int) -> None:
        """One comment occurrence; a comment line never continues even if
        it ends in backslash (parser.rs:320-350; RFC 0010 section 5)."""
        self._check_limit("comments", len(self.comments) + 1, self.limits.max_comments)
        line = self.lines[line_index]
        marker_index = line.atom_start
        while marker_index < line.atom_content_end and is_properties_whitespace(
            self.atoms[marker_index].ch
        ):
            marker_index += 1
        self._mark_atoms(line.atom_start, marker_index, PropertiesSyntaxKind.WHITESPACE)
        self._mark_atoms(
            marker_index, marker_index + 1, PropertiesSyntaxKind.COMMENT_MARKER
        )
        self._mark_atoms(
            marker_index + 1, line.atom_content_end, PropertiesSyntaxKind.COMMENT_TEXT
        )
        from consema.properties.document import PropertiesComment

        node = self._issue_node(NodeRole.PROPERTIES_COMMENT)
        self.comments.append(
            PropertiesComment(
                node=node,
                natural_line=self.natural_lines[line.natural_index].node,
                span=self._atom_span(line.atom_start, line.atom_content_end),
                marker=self.atoms[marker_index].ch,
            )
        )

    # -- logical lines ------------------------------------------------------

    def _add_logical_line(self, first_line: int) -> int:
        """Assembles one property/error logical line from its natural-line
        constituents (parser.rs:352-469)."""
        self._check_limit(
            "logical-lines", len(self.logical_lines) + 1, self.limits.max_logical_lines
        )
        line_index = first_line
        natural_indices: list[int] = []
        logical_atoms: list[int] = []
        while True:
            line = self.lines[line_index]
            natural_indices.append(line.natural_index)
            self._check_limit(
                "logical-line-natural-lines",
                len(natural_indices),
                self.limits.max_logical_line_natural_lines,
            )
            leading = 0
            if line_index != first_line:
                for atom in self.atoms[line.atom_start : line.atom_content_end]:
                    if not is_properties_whitespace(atom.ch):
                        break
                    leading += 1
            if leading > 0:
                self._mark_atoms(
                    line.atom_start,
                    line.atom_start + leading,
                    PropertiesSyntaxKind.WHITESPACE,
                )
            slash_run = 0
            for atom in reversed(
                self.atoms[line.atom_start + leading : line.atom_content_end]
            ):
                if atom.ch != "\\":
                    break
                slash_run += 1
            has_break = line.atom_content_end < line.atom_end
            remove_terminal_slash = slash_run % 2 == 1
            logical_end = (
                line.atom_content_end - 1 if remove_terminal_slash else line.atom_content_end
            )
            logical_atoms.extend(range(line.atom_start + leading, logical_end))
            self._check_limit(
                "logical-line-scalars",
                len(logical_atoms),
                self.limits.max_logical_line_scalars,
            )
            if remove_terminal_slash:
                self._mark_atoms(
                    logical_end,
                    line.atom_content_end,
                    PropertiesSyntaxKind.CONTINUATION_MARKER,
                )
            if remove_terminal_slash and has_break and line_index + 1 < len(self.lines):
                line_index += 1
                continue
            break

        next_line = line_index + 1
        natural_nodes = tuple(self.natural_lines[index].node for index in natural_indices)
        logical_node = self._issue_node(NodeRole.PROPERTIES_LOGICAL_LINE)
        leading = 0
        for position in logical_atoms:
            if not is_properties_whitespace(self.atoms[position].ch):
                break
            leading += 1
        self._mark_logical_positions(
            logical_atoms, 0, leading, PropertiesSyntaxKind.WHITESPACE
        )
        key_start, key_end, value_start, had_separator = self._split_property(
            logical_atoms, leading
        )
        self._mark_logical_positions(logical_atoms, key_start, key_end, PropertiesSyntaxKind.KEY)
        self._mark_logical_positions(
            logical_atoms, key_end, value_start, PropertiesSyntaxKind.SEPARATOR
        )
        self._mark_logical_positions(
            logical_atoms, value_start, len(logical_atoms), PropertiesSyntaxKind.VALUE
        )

        key = _decode_java_string(self.atoms, logical_atoms[key_start:key_end])
        value = _decode_java_string(self.atoms, logical_atoms[value_start:])
        if not isinstance(key, _DecodeError) and not isinstance(value, _DecodeError):
            self._finish_property(
                logical_node,
                natural_nodes,
                logical_atoms,
                key_start,
                key_end,
                value_start,
                had_separator,
                key,
                value,
                first_line,
                line_index,
            )
        else:
            error = key if isinstance(key, _DecodeError) else value
            assert isinstance(error, _DecodeError)
            self._recover_logical_line(
                logical_node, natural_nodes, logical_atoms, first_line, line_index, error
            )
        return next_line

    def _split_property(
        self, logical_atoms: list[int], key_start: int
    ) -> tuple[int, int, int, bool]:
        """Key/separator/element split with escaped-terminator tracking
        (parser.rs:471-507; RFC 0010 section 6)."""
        cursor = key_start
        escaped = False
        while cursor < len(logical_atoms):
            ch = self.atoms[logical_atoms[cursor]].ch
            if not escaped and (ch in ("=", ":") or is_properties_whitespace(ch)):
                break
            if ch == "\\":
                escaped = not escaped
            else:
                escaped = False
            cursor += 1
        key_end = cursor
        had_separator = cursor < len(logical_atoms)
        while cursor < len(logical_atoms) and is_properties_whitespace(
            self.atoms[logical_atoms[cursor]].ch
        ):
            cursor += 1
        if cursor < len(logical_atoms) and self.atoms[logical_atoms[cursor]].ch in ("=", ":"):
            cursor += 1
        while cursor < len(logical_atoms) and is_properties_whitespace(
            self.atoms[logical_atoms[cursor]].ch
        ):
            cursor += 1
        return key_start, key_end, cursor, had_separator

    def _finish_property(
        self,
        logical_node: NodeRef,
        natural_nodes: tuple[NodeRef, ...],
        logical_atoms: list[int],
        key_start: int,
        key_end: int,
        value_start: int,
        had_separator: bool,
        key: _DecodedJavaString,
        value: _DecodedJavaString,
        first_line: int,
        last_line: int,
    ) -> None:
        """Property record construction with every format limit
        (parser.rs:509-624)."""
        self._check_limit("properties", len(self.properties) + 1, self.limits.max_properties)
        self._check_limit(
            "java-code-units-per-string",
            len(key.units),
            self.limits.max_java_code_units_per_string,
        )
        self._check_limit(
            "java-code-units-per-string",
            len(value.units),
            self.limits.max_java_code_units_per_string,
        )
        added_units = len(key.units) + len(value.units)
        self._check_limit(
            "total-java-code-units",
            self.total_java_units + added_units,
            self.limits.max_total_java_code_units,
        )
        added_escapes = len(key.escapes) + len(value.escapes)
        added_unicode_escapes = key.unicode_escapes + value.unicode_escapes
        self._check_limit(
            "escapes", len(self.escapes) + added_escapes, self.limits.max_escapes
        )
        self._check_limit(
            "unicode-escapes",
            self.total_unicode_escapes + added_unicode_escapes,
            self.limits.max_unicode_escapes,
        )

        from consema.properties.document import PropertiesEscape

        property_node = self._issue_node(NodeRole.PROPERTIES_PROPERTY)
        escape_nodes: list[NodeRef] = []
        for in_key, spec in [(True, escape) for escape in key.escapes] + [
            (False, escape) for escape in value.escapes
        ]:
            node = self._issue_node(NodeRole.PROPERTIES_ESCAPE)
            self.atoms[spec.atom_indices[0]] = _replace(
                self.atoms[spec.atom_indices[0]], syntax=PropertiesSyntaxKind.ESCAPE_MARKER
            )
            for atom_index in spec.atom_indices[1:]:
                self.atoms[atom_index] = _replace(
                    self.atoms[atom_index], syntax=PropertiesSyntaxKind.ESCAPE_BODY
                )
            escape_start = spec.atom_indices[0]
            escape_end = spec.atom_indices[-1] + 1
            self.escapes.append(
                PropertiesEscape(
                    node=node,
                    property=property_node,
                    in_key=in_key,
                    kind=spec.kind,
                    span=self._atom_span(escape_start, escape_end),
                    output_start=spec.output_start,
                    output_end=spec.output_end,
                )
            )
            escape_nodes.append(node)
        if not value.units:
            value_state = (
                PropertiesValueState.EXPLICIT_EMPTY
                if had_separator
                else PropertiesValueState.IMPLICIT_EMPTY
            )
        else:
            value_state = PropertiesValueState.PRESENT
        span = self._logical_source_span(first_line, last_line)
        key_anchor = self._logical_anchor_span(logical_atoms, key_start, span.start_byte)
        value_anchor = self._logical_anchor_span(
            logical_atoms, value_start, span.end_byte
        )
        key_fragments = self._fragment_spans(logical_atoms, key_start, key_end)
        value_fragments = self._fragment_spans(logical_atoms, value_start, len(logical_atoms))

        from consema.properties.document import PropertiesLogicalLine, Property

        self.logical_lines.append(
            PropertiesLogicalLine(
                node=logical_node,
                kind=PropertiesLogicalLineKind.PROPERTY,
                natural_lines=natural_nodes,
            )
        )
        self.properties.append(
            Property(
                node=property_node,
                logical_line=logical_node,
                span=span,
                key_anchor=key_anchor,
                value_anchor=value_anchor,
                key_fragments=key_fragments,
                value_fragments=value_fragments,
                key=JavaString.from_code_units(key.units),
                value=JavaString.from_code_units(value.units),
                value_state=value_state,
                escapes=tuple(escape_nodes),
                duplicate_group=None,
            )
        )
        self.total_java_units += added_units
        self.total_unicode_escapes += added_unicode_escapes

    def _recover_logical_line(
        self,
        logical_node: NodeRef,
        natural_nodes: tuple[NodeRef, ...],
        logical_atoms: list[int],
        first_line: int,
        last_line: int,
        error: _DecodeError,
    ) -> None:
        """One recovered malformed logical line with its stable diagnostic
        (parser.rs:626-666)."""
        self._check_limit(
            "recovery-regions",
            len(self.error_lines) + 1,
            self.limits.max_recovery_regions,
        )
        for position in logical_atoms:
            self.atoms[position] = _replace(
                self.atoms[position], syntax=PropertiesSyntaxKind.ERROR_REGION
            )
        from consema.properties.document import (
            PropertiesErrorLine,
            PropertiesLogicalLine,
        )

        span = self._logical_source_span(first_line, last_line)
        error_span = self._atom_span(error.atom_start, error.atom_end)
        error_node = self._issue_node(NodeRole.PROPERTIES_ERROR_LINE)
        self.logical_lines.append(
            PropertiesLogicalLine(
                node=logical_node,
                kind=PropertiesLogicalLineKind.ERROR,
                natural_lines=natural_nodes,
            )
        )
        self.error_lines.append(
            PropertiesErrorLine(
                node=error_node,
                logical_line=logical_node,
                natural_lines=natural_nodes,
                span=span,
                code=MALFORMED_UNICODE_ESCAPE_CODE,
            )
        )
        self._diagnostic(MALFORMED_UNICODE_ESCAPE_CODE, error_span)

    # -- duplicate groups ---------------------------------------------------

    def _assign_duplicate_groups(self) -> None:
        """Deterministic exact-code-unit duplicate groups numbered from 1
        in sorted-key order (parser.rs:668-696)."""
        groups: dict[tuple[int, ...], list[int]] = {}
        for index, property in enumerate(self.properties):
            groups.setdefault(property.key.code_units(), []).append(index)
        next_group = 1
        for key in sorted(groups):
            indices = groups[key]
            if len(indices) <= 1:
                continue
            self._check_limit(
                "duplicate-group-members",
                len(indices),
                self.limits.max_duplicate_group_members,
            )
            for index in indices:
                self.properties[index] = _replace_property(
                    self.properties[index], duplicate_group=next_group
                )
            next_group += 1

    # -- structural pieces --------------------------------------------------

    def _build_structural_pieces(
        self,
    ) -> tuple[list[StructuralPiece], list[PropertiesSyntaxKind]]:
        """Exhaustive ordered coverage; contiguous atoms with one syntax
        merge into one piece (parser.rs:698-729)."""
        pieces: list[StructuralPiece] = []
        syntax_kinds: list[PropertiesSyntaxKind] = []
        cursor = 0
        while cursor < len(self.atoms):
            syntax = _atom_syntax(self.atoms[cursor])
            kind = _structural_kind(syntax)
            start = cursor
            cursor += 1
            while (
                cursor < len(self.atoms)
                and _atom_syntax(self.atoms[cursor]) is syntax
                and self.atoms[cursor].raw_start == self.atoms[cursor - 1].raw_end
            ):
                cursor += 1
            self._check_limit(
                "syntax-pieces", len(pieces) + 1, self.limits.common.max_token_count
            )
            pieces.append(
                StructuralPiece(
                    span=self._atom_span(start, cursor),
                    kind=kind,
                )
            )
            syntax_kinds.append(syntax)
        return pieces, syntax_kinds

    # -- marking helpers ----------------------------------------------------

    def _mark_atoms(self, start: int, end: int, syntax: PropertiesSyntaxKind) -> None:
        for index in range(start, end):
            self.atoms[index] = _replace(self.atoms[index], syntax=syntax)

    def _mark_logical_positions(
        self,
        logical_atoms: list[int],
        start: int,
        end: int,
        syntax: PropertiesSyntaxKind,
    ) -> None:
        for position in range(start, end):
            atom_index = logical_atoms[position]
            self.atoms[atom_index] = _replace(self.atoms[atom_index], syntax=syntax)

    # -- span helpers -------------------------------------------------------

    def _fragment_spans(
        self, logical_atoms: list[int], start: int, end: int
    ) -> tuple[Span, ...]:
        """Ordered raw spans of the key/element content, split at every raw
        discontinuity (parser.rs:748-769)."""
        if start >= end:
            return ()
        spans: list[Span] = []
        fragment_start = logical_atoms[start]
        previous = fragment_start
        for position in range(start + 1, end):
            current = logical_atoms[position]
            if self.atoms[current].raw_start != self.atoms[previous].raw_end:
                spans.append(self._atom_span(fragment_start, previous + 1))
                fragment_start = current
            previous = current
        spans.append(self._atom_span(fragment_start, previous + 1))
        return tuple(spans)

    def _logical_source_span(self, first_line: int, last_line: int) -> Span:
        """Complete first-to-last property source range (parser.rs:771-779)."""
        first = self.lines[first_line]
        last = self.lines[last_line]
        return self._atom_span(first.atom_start, last.atom_content_end)

    def _logical_anchor_span(
        self, logical_atoms: list[int], position: int, empty_fallback: int
    ) -> Span:
        """Zero-width anchor at the start of the decoded key or value
        (parser.rs:781-798)."""
        if position < len(logical_atoms):
            raw = self.atoms[logical_atoms[position]].raw_start
        elif logical_atoms:
            raw = self.atoms[logical_atoms[-1]].raw_end
        else:
            raw = empty_fallback
        return self.authority.span(raw, raw)

    def _atom_span(self, start: int, end: int) -> Span:
        """Raw span of atom indices [start, end) (parser.rs:800-816)."""
        if start < len(self.atoms):
            raw_start = self.atoms[start].raw_start
        else:
            raw_start = len(self.source.raw)
        if start == end:
            raw_end = raw_start
        elif end - 1 < len(self.atoms):
            raw_end = self.atoms[end - 1].raw_end
        else:
            raw_end = len(self.source.raw)
        return self.authority.span(raw_start, raw_end)

    # -- nodes, limits, diagnostics -----------------------------------------

    def _issue_node(self, role: NodeRole) -> NodeRef:
        """Issues one snapshot-bound handle under the node-count limit
        (parser.rs:818-828)."""
        self._check_limit("nodes", self.next_node + 1, self.limits.common.max_node_count)
        node = self.authority.node_ref(self.next_node, role)
        self.next_node += 1
        return node

    def _check_limit(self, name: str, observed: int, limit: int) -> None:
        if observed > limit:
            kind = _KIND_BY_LIMIT_NAME.get(
                name, PropertiesFormationFailureKind.TOKEN_COUNT
            )
            raise PropertiesFormationFailure(
                kind,
                observed=observed,
                limit=limit,
            )

    def _diagnostic(self, code: str, span: Span) -> None:
        """Pushes one error record under the diagnostic limit; recovery
        becomes explicit (parser.rs:847-879)."""
        self._check_limit(
            "diagnostics", len(self.diagnostics) + 1, self.limits.common.max_diagnostics
        )
        self.diagnostics.append(
            PropertiesDiagnostic(
                code=code,
                category=DiagnosticCategory.SYNTAX,
                severity=PropertiesSeverity.ERROR,
                primary=span,
                occurrence=self.occurrence,
            )
        )
        self.occurrence += 1
        self.recovered = True


# Exact limit-name to failure-kind mapping; the resource names are the Rust
# spellings used by parser.rs check_limit calls.
_KIND_BY_LIMIT_NAME = {
    "source-bytes": PropertiesFormationFailureKind.SOURCE_BYTES,
    "token-count": PropertiesFormationFailureKind.TOKEN_COUNT,
    "syntax-pieces": PropertiesFormationFailureKind.TOKEN_COUNT,
    "nodes": PropertiesFormationFailureKind.NODE_COUNT,
    "diagnostics": PropertiesFormationFailureKind.DIAGNOSTICS,
    "decoded-utf8-bytes": PropertiesFormationFailureKind.DECODED_UTF8_BYTES,
    "decoded-scalars": PropertiesFormationFailureKind.DECODED_SCALARS,
    "natural-lines": PropertiesFormationFailureKind.NATURAL_LINES,
    "natural-line-bytes": PropertiesFormationFailureKind.NATURAL_LINE_BYTES,
    "natural-line-scalars": PropertiesFormationFailureKind.NATURAL_LINE_SCALARS,
    "logical-lines": PropertiesFormationFailureKind.LOGICAL_LINES,
    "logical-line-natural-lines": PropertiesFormationFailureKind.LOGICAL_LINE_NATURAL_LINES,
    "logical-line-scalars": PropertiesFormationFailureKind.LOGICAL_LINE_SCALARS,
    "properties": PropertiesFormationFailureKind.PROPERTIES,
    "comments": PropertiesFormationFailureKind.COMMENTS,
    "escapes": PropertiesFormationFailureKind.ESCAPES,
    "unicode-escapes": PropertiesFormationFailureKind.UNICODE_ESCAPES,
    "java-code-units-per-string": PropertiesFormationFailureKind.JAVA_CODE_UNITS_PER_STRING,
    "total-java-code-units": PropertiesFormationFailureKind.TOTAL_JAVA_CODE_UNITS,
    "duplicate-group-members": PropertiesFormationFailureKind.DUPLICATE_GROUP_MEMBERS,
    "recovery-regions": PropertiesFormationFailureKind.RECOVERY_REGIONS,
}


def _atom_syntax(atom: _Atom) -> PropertiesSyntaxKind:
    """Explicit syntax with the ErrorRegion fallback (parser.rs:704-716)."""
    return (
        atom.syntax
        if atom.syntax is not None
        else PropertiesSyntaxKind.ERROR_REGION
    )


def _replace(atom: _Atom, syntax: PropertiesSyntaxKind) -> _Atom:
    """Copies one atom with an updated syntax classification."""
    return _Atom(
        ch=atom.ch,
        raw_start=atom.raw_start,
        raw_end=atom.raw_end,
        syntax=syntax,
    )


def _replace_property(property, duplicate_group: int):
    """Copies one property record with an assigned duplicate group
    (``dataclasses.replace`` constructs a fresh slots instance)."""
    return replace(property, duplicate_group=duplicate_group)


def _build_atoms(source: SourceSnapshot) -> list[_Atom]:
    """One atom per decoded scalar with its exact raw span
    (parser.rs:882-907)."""
    text = source.decoded_text()
    assert text is not None, "Properties source profiles always select text decoding"
    atoms: list[_Atom] = []
    decoded_utf8 = 0
    for ch in text:
        raw_start = source.raw_byte_at(DecodedOffset.utf8_byte(decoded_utf8))
        decoded_utf8 += len(ch.encode("utf-8"))
        raw_end = source.raw_byte_at(DecodedOffset.utf8_byte(decoded_utf8))
        atoms.append(_Atom(ch=ch, raw_start=raw_start, raw_end=raw_end))
    return atoms


def _decode_java_string(
    atoms: list[_Atom], atom_indices: list[int]
) -> _DecodedJavaString | _DecodeError:
    """Decodes one key/element into exact Java code units plus escapes
    (parser.rs:909-996; RFC 0010 section 7)."""
    units: list[int] = []
    escapes: list[_EscapeSpec] = []
    unicode_escapes = 0
    cursor = 0
    while cursor < len(atom_indices):
        atom_index = atom_indices[cursor]
        ch = atoms[atom_index].ch
        if ch != "\\":
            units.extend(_utf16_units(ch))
            cursor += 1
            continue
        if cursor + 1 >= len(atom_indices):
            return _DecodeError(atom_start=atom_index, atom_end=atom_index + 1)
        next_index = atom_indices[cursor + 1]
        next_ch = atoms[next_index].ch
        output_start = len(units)
        if next_ch == "u":
            if cursor + 6 > len(atom_indices):
                return _DecodeError(
                    atom_start=atom_index,
                    atom_end=atom_indices[-1] + 1,
                )
            value = 0
            for digit_position in range(cursor + 2, cursor + 6):
                digit_index = atom_indices[digit_position]
                digit = _hex_digit(atoms[digit_index].ch)
                if digit is None:
                    return _DecodeError(atom_start=atom_index, atom_end=digit_index + 1)
                value = (value << 4) | digit
            units.append(value)
            unicode_escapes += 1
            kind = PropertiesEscapeKind.UNICODE
            consumed = 6
        elif next_ch == "t":
            units.append(0x0009)
            kind = PropertiesEscapeKind.NAMED
            consumed = 2
        elif next_ch == "n":
            units.append(0x000A)
            kind = PropertiesEscapeKind.NAMED
            consumed = 2
        elif next_ch == "r":
            units.append(0x000D)
            kind = PropertiesEscapeKind.NAMED
            consumed = 2
        elif next_ch == "f":
            units.append(0x000C)
            kind = PropertiesEscapeKind.NAMED
            consumed = 2
        elif next_ch == "\\":
            units.append(0x005C)
            kind = PropertiesEscapeKind.BACKSLASH
            consumed = 2
        else:
            units.extend(_utf16_units(next_ch))
            kind = PropertiesEscapeKind.DROPPED_BACKSLASH
            consumed = 2
        escapes.append(
            _EscapeSpec(
                atom_indices=tuple(atom_indices[cursor : cursor + consumed]),
                kind=kind,
                output_start=output_start,
                output_end=len(units),
            )
        )
        cursor += consumed
    return _DecodedJavaString(
        units=tuple(units),
        escapes=tuple(escapes),
        unicode_escapes=unicode_escapes,
    )


def _utf16_units(ch: str) -> tuple[int, ...]:
    """Exact UTF-16 code units of one decoded scalar."""
    encoded = ch.encode("utf-16-be")
    return tuple(
        int.from_bytes(encoded[index : index + 2], "big")
        for index in range(0, len(encoded), 2)
    )


def _hex_digit(ch: str) -> int | None:
    """One hexadecimal digit, or None for a non-hex character."""
    if "0" <= ch <= "9":
        return ord(ch) - ord("0")
    if "a" <= ch <= "f":
        return ord(ch) - ord("a") + 10
    if "A" <= ch <= "F":
        return ord(ch) - ord("A") + 10
    return None


def _structural_kind(syntax: PropertiesSyntaxKind) -> StructuralPieceKind:
    """Token/Trivia/ErrorRegion classification (parser.rs:1002-1017)."""
    if syntax in (
        PropertiesSyntaxKind.WHITESPACE,
        PropertiesSyntaxKind.LINE_BREAK,
        PropertiesSyntaxKind.COMMENT_MARKER,
        PropertiesSyntaxKind.COMMENT_TEXT,
    ):
        return StructuralPieceKind.TRIVIA
    if syntax is PropertiesSyntaxKind.ERROR_REGION:
        return StructuralPieceKind.ERROR_REGION
    return StructuralPieceKind.TOKEN
