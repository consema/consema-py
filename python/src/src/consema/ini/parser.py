"""Physical-line scanning and record formation for the three INI profiles.

Authority (Rust arbitration for exact byte semantics):

- Parse entry, encoding selection, and profile encoding gates:
  crates/consema-ini/src/parser.rs:16-104 — encoding_request
  (parser.rs:37-59; ProfileDefault defaults to UTF-8, explicit caller
  overrides, code pages force BomPolicy::TreatAsContent, Portable accepts
  only UTF-8, Binary is rejected with ini.profile.encoding@1) and
  validate_profile_encoding (parser.rs:61-94: Portable requires UTF-8
  without BOM; Windows accepts UTF-16LE-with-BOM or an explicitly
  selected code page, or an ASCII-only no-BOM UTF-8 source; Python
  accepts any non-Binary selected encoding).
- Physical-line scanning: parser.rs:228-301 (line split on LF with CRLF
  handling, per-line byte/scalar limits, BOM skip when the decoded text
  starts with U+FEFF).
- Profile line grammars: parser.rs:303-346 (common line gates), 348-399
  (portable), 401-467 (windows), 469-578 (python), 580-747 (python
  continuation joins and limits), 749-867 (section/entry/logical records),
  869-905 (recovery), 907-949 (BOM/comment/section pieces), 1039-1107
  (line break/whitespace/value pieces).
- Comparison and duplicate groups: parser.rs:1197-1304 (section/key
  comparison per profile; duplicate/case-collision groups with stable
  group identities; diagnostic codes ini.formation.duplicate-section@1 /
  duplicate-entry@1 / case-collision@1; recovery only for non-Windows).
- Limits and fatal failures: parser.rs:1132-1195 (nodes, syntax pieces,
  diagnostics); the resource names are pinned by
  conformance/vectors/ini-v1.json:108-128 (resource.formation-limit-
  matrix: 17 fatal outcomes, no partial documents).
- Python optionxform: crate python_case.rs (transcribed in
  consema.ini.python_case).
- Formation status: Complete means every physical line is accounted for,
  every logical record is valid, section/entry ownership is unambiguous,
  and every profile invariant and configured limit holds; Recovered
  retains the complete source with exhaustive syntax/error-region
  coverage and every independently proven section or entry (RFC 0009 §4,
  docs/rfcs/0009-...:118-147).

The parser indexes the decoded text by UTF-8 byte offsets and maps every
decoded boundary back to exact raw bytes via SourceSnapshot.raw_byte_at
(RFC 0009 §3, docs/rfcs/0009-...:70-73).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from consema.document.source import (
    BomPolicy,
    DecodedOffset,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
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
    StructuralPiece,
    StructuralPieceKind,
)
from consema.ini.errors import (
    IniDiagnostic,
    IniFormationFailure,
    IniFormationFailureKind,
    IniSeverity,
    sort_diagnostics,
)
from consema.ini.kinds import (
    IniEncodingSelection,
    IniLogicalLineKind,
    IniParseLimits,
    IniProfile,
    IniQuoteStyle,
    IniSyntaxKind,
    IniValueState,
    is_horizontal,
    is_portable_name,
    is_portable_value,
    is_windows_name,
)
from consema.ini.python_case import optionxform
from consema.protocol.error_registry import DiagnosticCategory

_FEFF_UTF8_LEN = len("﻿".encode("utf-8"))


# ---------------------------------------------------------------------------
# Snapshot-bound INI records (RFC 0009 §8, docs/rfcs/0009-...:273-283)
# ---------------------------------------------------------------------------
# The immutable records below are the native model handles
# (IniDocument / IniPhysicalLine / IniLogicalLine / IniSection /
# IniDefaultSection / IniEntry / IniErrorLine). Every field is a direct
# accessor (Python-idiomatic; the Rust public accessors they correspond to
# are cited in each field comment): ``node`` is the snapshot-bound NodeRef
# handle, ``span``/``content_span``/``line_break_span`` are raw-byte
# ranges, and the name/value/state/group facts are decoded strings and
# enums (lib.rs:239-263, 273-291, 306-354, 373-445, 457-487).


@dataclass(frozen=True, slots=True)
class IniPhysicalLine:
    """One exact physical source line (lib.rs:231-237).

    ``node`` is the snapshot-bound physical-line identity (lib.rs:242-245),
    ``span`` the complete raw line including its line break (lib.rs:248-
    251), ``content_span`` the raw content excluding the break (lib.rs:254-
    257), and ``line_break_span`` the exact LF/CRLF range, absent at EOF
    (lib.rs:260-263).
    """

    node: NodeRef
    span: object  # consema.document.Span, full raw line including break
    content_span: object  # raw content excluding break
    line_break_span: object  # exact LF/CRLF range, None at EOF


@dataclass(frozen=True, slots=True)
class IniLogicalLine:
    """One logical record and its ordered physical constituents
    (lib.rs:266-271).

    ``node`` is the logical-line identity (lib.rs:276-279), ``kind`` the
    Section | Entry | Error record kind (lib.rs:282-285), and
    ``physical_nodes`` the ordered physical-line identities (lib.rs:288-
    291).
    """

    node: NodeRef
    kind: IniLogicalLineKind
    physical_nodes: tuple[NodeRef, ...]


@dataclass(frozen=True, slots=True)
class IniSection:
    """One distinct section-header occurrence (lib.rs:294-304).

    ``node`` is the section occurrence identity (lib.rs:309-312),
    ``logical_line`` the owning logical line (lib.rs:315-318), ``span``
    the complete header content span excluding the line break
    (lib.rs:321-324), ``name_span`` the exact section-name span
    (lib.rs:327-330), ``name`` the original decoded spelling (lib.rs:333-
    336), ``comparison_name`` the profile-specific comparison name
    (lib.rs:339-342), ``is_default`` whether this is Python's exact
    ``DEFAULT`` section (lib.rs:345-348), and ``duplicate_group`` the
    deterministic duplicate/case-equivalence group identity (lib.rs:351-
    354).
    """

    node: NodeRef
    logical_line: NodeRef
    span: object  # complete header content span excluding the line break
    name_span: object
    name: str
    comparison_name: str
    is_default: bool
    duplicate_group: int | None


@dataclass(frozen=True, slots=True)
class IniEntry:
    """One distinct key/value occurrence (lib.rs:357-371).

    ``node`` is the entry occurrence identity (lib.rs:376-379),
    ``logical_line`` the owning logical line (lib.rs:382-385), ``section``
    the owning section occurrence (lib.rs:388-391), ``span`` the complete
    first physical-line content span (lib.rs:394-397), ``key_span`` the
    exact original key span (lib.rs:400-403), ``value_span`` the exact
    first-line semantic value span (lib.rs:406-409), ``key`` the original
    decoded key spelling (lib.rs:412-415), ``comparison_key`` the profile-
    specific comparison key (lib.rs:418-421), ``value`` the stored
    semantic string including deterministic continuation joins
    (lib.rs:424-427), ``state`` the Missing | Empty | Present value fact
    (lib.rs:430-433), ``quote_style`` the profile-recognized outer quote
    style (lib.rs:436-439), and ``duplicate_group`` the deterministic
    duplicate/case-equivalence group identity (lib.rs:442-445).
    """

    node: NodeRef
    logical_line: NodeRef
    section: NodeRef
    span: object  # complete first physical-line content span
    key_span: object
    value_span: object  # first-line semantic value span
    key: str
    comparison_key: str
    value: str
    state: IniValueState
    quote_style: IniQuoteStyle
    duplicate_group: int | None


@dataclass(frozen=True, slots=True)
class IniErrorLine:
    """One recovered physical error record (lib.rs:448-455).

    ``node`` is the error identity (lib.rs:460-463), ``logical_line`` the
    owning logical line (lib.rs:466-469), ``physical_line`` the physical
    line retained by recovery (lib.rs:472-475), ``span`` the exact
    malformed content span (lib.rs:478-481), and ``code`` the stable
    diagnostic code (lib.rs:484-487).
    """

    node: NodeRef
    logical_line: NodeRef
    physical_line: NodeRef
    span: object  # malformed content span
    code: str


@dataclass(frozen=True, slots=True)
class _ScannedLine:
    """One scanned physical line in decoded UTF-8 byte coordinates."""

    decoded_start: int
    decoded_content_end: int
    decoded_break_start: int
    decoded_end: int
    physical_index: int


@dataclass(slots=True)
class _PythonEntryState:
    """Active Python multiline entry accumulation (parser.rs:116-124)."""

    entry_index: int
    logical_index: int
    indent: int
    continuation_lines: int
    logical_bytes: int
    logical_scalars: int
    pending_blank_lines: list[int]


class _Parser:
    def __init__(
        self,
        source: SourceSnapshot,
        profile: IniProfile,
        limits: IniParseLimits,
    ) -> None:
        self.source = source
        self.profile = profile
        self.limits = limits
        self.authority = DocumentAuthority.fresh()
        self.root_node = self.authority.node_ref(0, NodeRole.INI_DOCUMENT)
        self.next_node = 1
        self.text = source.decoded_text()
        assert self.text is not None, "INI profiles reject Binary before parsing"
        self.text_utf8 = self.text.encode("utf-8")
        self.lines: list[_ScannedLine] = []
        self.physical_lines: list[IniPhysicalLine] = []
        self.logical_lines: list[IniLogicalLine] = []
        self.sections: list[IniSection] = []
        self.entries: list[IniEntry] = []
        self.entry_section_indices: list[int] = []
        self.error_lines: list[IniErrorLine] = []
        self.pieces: list[StructuralPiece] = []
        self.syntax_kinds: list[IniSyntaxKind] = []
        self.diagnostics: list[IniDiagnostic] = []
        self.occurrence = 0
        self.recovered = False
        self.current_section: int | None = None
        self.python_entry: _PythonEntryState | None = None

    # -- scan ---------------------------------------------------------------

    def scan_physical_lines(self) -> None:
        """Splits the decoded text into physical lines (parser.rs:228-301)."""
        start = _FEFF_UTF8_LEN if (
            self.source.encoding_facts().bom is not None and self.text.startswith("﻿")
        ) else 0
        decoded_lines: list[tuple[int, int, int, int, int]] = []
        while start < len(self.text_utf8):
            newline = self.text_utf8.find(b"\n", start)
            if newline != -1:
                break_start = newline - 1 if newline > start and self.text_utf8[newline - 1] == 0x0D else newline
                content_end, end = break_start, newline + 1
            else:
                content_end = break_start = end = len(self.text_utf8)
            observed = len(decoded_lines) + 1
            self.check_limit("physical-lines", observed, self.limits.max_physical_lines)
            content = self.text_utf8[start:content_end]
            decoded_lines.append((start, content_end, break_start, end, len(content.decode("utf-8"))))
            start = end
        for start, content_end, break_start, end, scalar_count in decoded_lines:
            full_span = self.raw_span(start, end)
            content_span = self.raw_span(start, content_end)
            self.check_limit("physical-line-bytes", full_span.len(), self.limits.max_physical_line_bytes)
            self.check_limit(
                "physical-line-scalars", scalar_count, self.limits.max_physical_line_scalars
            )
            node = self.issue_node(NodeRole.INI_PHYSICAL_LINE)
            line_break_span = self.raw_span(break_start, end) if break_start < end else None
            physical_index = len(self.physical_lines)
            self.physical_lines.append(
                IniPhysicalLine(
                    node=node,
                    span=full_span,
                    content_span=content_span,
                    line_break_span=line_break_span,
                )
            )
            self.lines.append(
                _ScannedLine(
                    decoded_start=start,
                    decoded_content_end=content_end,
                    decoded_break_start=break_start,
                    decoded_end=end,
                    physical_index=physical_index,
                )
            )

    # -- line dispatch ------------------------------------------------------

    def parse_line(self, line_index: int) -> None:
        """One physical line: common gates, comments, profile dispatch
        (parser.rs:303-346)."""
        line = self.lines[line_index]
        content = self.decoded(line)
        if "\0" in content or "\r" in content:
            return self.recover_line(line_index, "ini.parse.invalid-character@1")
        if self.profile is IniProfile.PORTABLE_V1 and any(
            byte != 0x09 and not (0x20 <= byte <= 0x7E) for byte in content.encode("utf-8")
        ):
            return self.recover_line(line_index, "ini.parse.invalid-character@1")
        if all(is_horizontal(byte) for byte in content.encode("utf-8")):
            if content:
                self.push_piece(
                    line.decoded_start,
                    line.decoded_content_end,
                    StructuralPieceKind.TRIVIA,
                    IniSyntaxKind.WHITESPACE,
                )
            if self.profile is IniProfile.PYTHON_CONFIGPARSER_V1:
                if self.python_entry is not None:
                    self.python_entry.pending_blank_lines.append(line_index)
            return
        leading = _leading_horizontal(content)
        marker = content.encode("utf-8")[leading] if leading < len(content.encode("utf-8")) else None
        if self.profile is IniProfile.PYTHON_CONFIGPARSER_V1:
            is_comment = marker in (0x3B, 0x23)
        else:
            is_comment = marker == 0x3B
        if is_comment:
            self.push_comment(line, leading)
            return
        if self.profile is IniProfile.PORTABLE_V1:
            self.parse_portable_line(line_index)
        elif self.profile is IniProfile.WINDOWS_V1:
            self.parse_windows_line(line_index)
        else:
            self.parse_python_line(line_index)

    # -- profile line grammars ----------------------------------------------

    def parse_portable_line(self, line_index: int) -> None:
        """Portable grammar (parser.rs:348-399; RFC 0009 §5)."""
        self.python_entry = None
        line = self.lines[line_index]
        content = self.decoded(line)
        if content.startswith("["):
            if (
                line.decoded_break_start == line.decoded_end
                or not content.endswith("]")
                or len(content) < 3
            ):
                return self.recover_line(line_index, "ini.parse.malformed-section@1")
            name = content[1 : len(content) - 1]
            if not all(is_portable_name(byte) for byte in name.encode("utf-8")):
                return self.recover_line(line_index, "ini.parse.invalid-character@1")
            self.push_section_syntax(line, 0, 1, len(content) - 1, len(content))
            self.add_section(line_index, 1, len(content) - 1, name, False)
        else:
            delimiter = content.find("=")
            if delimiter == -1:
                return self.recover_line(line_index, "ini.parse.missing-delimiter@1")
            key = content[:delimiter]
            value = content[delimiter + 1 :]
            if not key or not all(is_portable_name(byte) for byte in key.encode("utf-8")):
                return self.recover_line(line_index, "ini.parse.invalid-character@1")
            if not all(is_portable_value(byte) for byte in value.encode("utf-8")):
                return self.recover_line(line_index, "ini.parse.invalid-character@1")
            if self.current_section is None:
                return self.recover_line(line_index, "ini.parse.missing-section@1")
            self.push_entry_syntax(
                line,
                0,
                delimiter,
                delimiter,
                delimiter + 1,
                delimiter + 1,
                len(content),
                None,
            )
            self.add_entry(
                line_index,
                self.current_section,
                0,
                delimiter,
                delimiter + 1,
                len(content),
                key,
                value,
                IniQuoteStyle.NONE,
            )

    def parse_windows_line(self, line_index: int) -> None:
        """Windows grammar (parser.rs:401-467; RFC 0009 §6).

        All offsets are byte offsets into the decoded line content, exactly
        like the Rust byte slicing (parser.rs:404-465).
        """
        self.python_entry = None
        line = self.lines[line_index]
        content = self.decoded(line)
        content_bytes = content.encode("utf-8")
        trim_start, trim_end = _trim_horizontal_bounds(content)
        core = content_bytes[trim_start:trim_end].decode("utf-8")
        if core.startswith("["):
            if not core.endswith("]") or len(core) < 3:
                return self.recover_line(line_index, "ini.parse.malformed-section@1")
            name = core[1 : len(core) - 1]
            if not all(is_windows_name(byte) for byte in name.encode("utf-8")):
                return self.recover_line(line_index, "ini.parse.invalid-character@1")
            self.push_optional_whitespace(line, 0, trim_start)
            self.push_section_syntax(line, trim_start, trim_start + 1, trim_end - 1, trim_end)
            self.push_optional_whitespace(line, trim_end, len(content_bytes))
            self.add_section(line_index, trim_start + 1, trim_end - 1, name, False)
        else:
            delimiter = _find_byte(content_bytes, (0x3D,), trim_start)
            if delimiter is None:
                return self.recover_line(line_index, "ini.parse.missing-delimiter@1")
            relative_key_start, relative_key_end = _trim_horizontal_bounds(
                content_bytes[trim_start:delimiter]
            )
            key_start = trim_start + relative_key_start
            key_end = trim_start + relative_key_end
            key = content_bytes[key_start:key_end].decode("utf-8")
            if not key or not all(is_windows_name(byte) for byte in key.encode("utf-8")):
                return self.recover_line(line_index, "ini.parse.invalid-character@1")
            if self.current_section is None:
                return self.recover_line(line_index, "ini.parse.missing-section@1")
            literal_start = delimiter + 1
            literal = content_bytes[literal_start:]
            value_start, value_end, quote_style = _quoted_windows_value(literal, literal_start)
            value = content_bytes[value_start:value_end].decode("utf-8")
            self.push_optional_whitespace(line, 0, key_start)
            self.push_piece_local(
                line, key_start, key_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_KEY
            )
            self.push_optional_whitespace(line, key_end, delimiter)
            self.push_piece_local(
                line, delimiter, delimiter + 1, StructuralPieceKind.TOKEN, IniSyntaxKind.DELIMITER
            )
            self.push_windows_value_syntax(
                line, literal_start, len(content_bytes), value_start, value_end, quote_style
            )
            self.add_entry(
                line_index,
                self.current_section,
                key_start,
                key_end,
                value_start,
                value_end,
                key,
                value,
                quote_style,
            )

    def parse_python_line(self, line_index: int) -> None:
        """Python grammar (parser.rs:469-578; RFC 0009 §7).

        All offsets are byte offsets into the decoded line content, exactly
        like the Rust byte slicing (parser.rs:471-577).
        """
        line = self.lines[line_index]
        content = self.decoded(line)
        content_bytes = content.encode("utf-8")
        indent = _leading_horizontal(content)
        if self.python_entry is not None and indent > self.python_entry.indent:
            return self.add_python_continuation(line_index, indent)
        if self.python_entry is not None:
            self.python_entry.pending_blank_lines.clear()
        self.python_entry = None
        trim_start, trim_end = _trim_horizontal_bounds(content)
        core = content_bytes[trim_start:trim_end].decode("utf-8")
        if core.startswith("["):
            if not core.endswith("]") or len(core) < 3:
                return self.recover_line(line_index, "ini.parse.malformed-section@1")
            name = core[1 : len(core) - 1]
            self.push_optional_whitespace(line, 0, trim_start)
            self.push_section_syntax(line, trim_start, trim_start + 1, trim_end - 1, trim_end)
            self.push_optional_whitespace(line, trim_end, len(content_bytes))
            return self.add_section(line_index, trim_start + 1, trim_end - 1, name, name == "DEFAULT")
        delimiter = _first_python_delimiter(content_bytes[trim_start:])
        if delimiter is None:
            code = "ini.parse.invalid-continuation@1" if indent > 0 else "ini.parse.missing-delimiter@1"
            return self.recover_line(line_index, code)
        delimiter += trim_start
        relative_key_start, relative_key_end = _trim_horizontal_bounds(
            content_bytes[trim_start:delimiter]
        )
        key_start = trim_start + relative_key_start
        key_end = trim_start + relative_key_end
        if key_start == key_end:
            return self.recover_line(line_index, "ini.parse.malformed-line@1")
        if self.current_section is None:
            return self.recover_line(line_index, "ini.parse.missing-section@1")
        relative_value_start, relative_value_end = _trim_horizontal_bounds(
            content_bytes[delimiter + 1 :]
        )
        value_start = delimiter + 1 + relative_value_start
        value_end = delimiter + 1 + relative_value_end
        key = content_bytes[key_start:key_end].decode("utf-8")
        value = content_bytes[value_start:value_end].decode("utf-8")
        self.push_optional_whitespace(line, 0, key_start)
        self.push_piece_local(
            line, key_start, key_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_KEY
        )
        self.push_optional_whitespace(line, key_end, delimiter)
        self.push_piece_local(
            line, delimiter, delimiter + 1, StructuralPieceKind.TOKEN, IniSyntaxKind.DELIMITER
        )
        self.push_optional_whitespace(line, delimiter + 1, value_start)
        if value_start < value_end:
            self.push_piece_local(
                line, value_start, value_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_VALUE
            )
        self.push_optional_whitespace(line, value_end, len(content_bytes))
        entry_index = self.add_entry(
            line_index,
            self.current_section,
            key_start,
            key_end,
            value_start,
            value_end,
            key,
            value,
            IniQuoteStyle.NONE,
        )
        logical_node = self.entries[entry_index].logical_line
        logical_index = next(
            index
            for index, record in enumerate(self.logical_lines)
            if record.node == logical_node
        )
        physical = self.physical_lines[line.physical_index]
        self.python_entry = _PythonEntryState(
            entry_index=entry_index,
            logical_index=logical_index,
            indent=indent,
            continuation_lines=0,
            logical_bytes=physical.span.len(),
            logical_scalars=len(content),
            pending_blank_lines=[],
        )

    def add_python_continuation(self, line_index: int, indent: int) -> None:
        """Joins one more-indented physical line into the active entry
        (parser.rs:580-747)."""
        line = self.lines[line_index]
        content = self.decoded(line)
        content_bytes = content.encode("utf-8")
        _, relative_value_end = _trim_horizontal_bounds(content_bytes[indent:])
        value_start = indent
        value_end = indent + relative_value_end
        state = self.python_entry
        assert state is not None, "continuation requires an active entry"
        self.python_entry = None
        added_lines = len(state.pending_blank_lines) + 1
        continuation_lines = state.continuation_lines + added_lines
        self.check_limit("continuation-lines", continuation_lines, self.limits.max_continuation_lines)

        pending_bytes = 0
        pending_scalars = 0
        for pending_index in state.pending_blank_lines:
            pending_line = self.lines[pending_index]
            pending_physical = self.physical_lines[pending_line.physical_index]
            pending_bytes += pending_physical.span.len()
            pending_scalars += len(self.decoded(pending_line))
        physical = self.physical_lines[line.physical_index]
        logical_bytes = state.logical_bytes + pending_bytes + physical.span.len()
        self.check_limit("logical-line-bytes", logical_bytes, self.limits.max_logical_line_bytes)
        logical_scalars = state.logical_scalars + pending_scalars + len(content)
        self.check_limit("logical-line-scalars", logical_scalars, self.limits.max_logical_line_scalars)

        fragment = content[value_start:value_end]
        value_storage_bytes = (
            len(self.entries[state.entry_index].value.encode("utf-8"))
            + added_lines
            + len(fragment.encode("utf-8"))
        )
        self.check_limit(
            "logical-value-storage-bytes",
            value_storage_bytes,
            self.limits.max_decoded_utf8_bytes,
        )
        joined = [self.entries[state.entry_index].value]
        pending_nodes = []
        for pending_index in state.pending_blank_lines:
            pending_line = self.lines[pending_index]
            pending_physical = self.physical_lines[pending_line.physical_index]
            pending_nodes.append(pending_physical.node)
            joined.append("\n")
        pending_nodes.append(physical.node)
        joined.append("\n")
        joined.append(fragment)
        new_value = "".join(joined)
        logical = self.logical_lines[state.logical_index]
        self.logical_lines[state.logical_index] = IniLogicalLine(
            node=logical.node,
            kind=logical.kind,
            physical_nodes=logical.physical_nodes + tuple(pending_nodes),
        )
        entry = self.entries[state.entry_index]
        self.entries[state.entry_index] = replace(
            entry,
            value=new_value,
            state=IniValueState.EMPTY if not new_value else IniValueState.PRESENT,
        )
        self.push_piece_local(
            line, 0, indent, StructuralPieceKind.TRIVIA, IniSyntaxKind.CONTINUATION_MARKER
        )
        if value_start < value_end:
            self.push_piece_local(
                line, value_start, value_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_VALUE
            )
        self.push_optional_whitespace(line, value_end, len(content))
        state.continuation_lines = continuation_lines
        state.logical_bytes = logical_bytes
        state.logical_scalars = logical_scalars
        state.pending_blank_lines.clear()
        self.python_entry = state

    # -- records ------------------------------------------------------------

    def add_section(
        self, line_index: int, name_start: int, name_end: int, name: str, is_default: bool
    ) -> None:
        """One section occurrence (parser.rs:749-785)."""
        self.check_limit("sections", len(self.sections) + 1, self.limits.max_sections)
        line = self.lines[line_index]
        logical_index = self.add_logical(line_index, IniLogicalLineKind.SECTION)
        role = NodeRole.INI_DEFAULT_SECTION if is_default else NodeRole.INI_SECTION
        node = self.issue_node(role)
        physical = self.physical_lines[line.physical_index]
        self.sections.append(
            IniSection(
                node=node,
                logical_line=self.logical_lines[logical_index].node,
                span=physical.content_span,
                name_span=self.raw_span(
                    line.decoded_start + name_start, line.decoded_start + name_end
                ),
                name=name,
                comparison_name=self.section_comparison(name),
                is_default=is_default,
                duplicate_group=None,
            )
        )
        self.current_section = len(self.sections) - 1
        self.python_entry = None

    def add_entry(
        self,
        line_index: int,
        section_index: int,
        key_start: int,
        key_end: int,
        value_start: int,
        value_end: int,
        key: str,
        value: str,
        quote_style: IniQuoteStyle,
    ) -> int:
        """One entry occurrence (parser.rs:788-834)."""
        self.check_limit("entries", len(self.entries) + 1, self.limits.max_entries)
        line = self.lines[line_index]
        logical_index = self.add_logical(line_index, IniLogicalLineKind.ENTRY)
        node = self.issue_node(NodeRole.INI_ENTRY)
        state = IniValueState.EMPTY if not value else IniValueState.PRESENT
        physical = self.physical_lines[line.physical_index]
        self.entries.append(
            IniEntry(
                node=node,
                logical_line=self.logical_lines[logical_index].node,
                section=self.sections[section_index].node,
                span=physical.content_span,
                key_span=self.raw_span(
                    line.decoded_start + key_start, line.decoded_start + key_end
                ),
                value_span=self.raw_span(
                    line.decoded_start + value_start, line.decoded_start + value_end
                ),
                key=key,
                comparison_key=self.key_comparison(key),
                value=value,
                state=state,
                quote_style=quote_style,
                duplicate_group=None,
            )
        )
        entry_index = len(self.entries) - 1
        self.entry_section_indices.append(section_index)
        return entry_index

    def add_logical(self, line_index: int, kind: IniLogicalLineKind) -> int:
        """One logical record over the first physical line (parser.rs:836-867)."""
        self.check_limit("logical-lines", len(self.logical_lines) + 1, self.limits.max_logical_lines)
        line = self.lines[line_index]
        physical = self.physical_lines[line.physical_index]
        self.check_limit("logical-line-bytes", physical.span.len(), self.limits.max_logical_line_bytes)
        self.check_limit(
            "logical-line-scalars", len(self.decoded(line)), self.limits.max_logical_line_scalars
        )
        node = self.issue_node(NodeRole.INI_LOGICAL_LINE)
        index = len(self.logical_lines)
        self.logical_lines.append(
            IniLogicalLine(node=node, kind=kind, physical_nodes=(physical.node,))
        )
        return index

    def recover_line(self, line_index: int, code: str) -> None:
        """One recovered physical error record (parser.rs:869-905)."""
        self.check_limit("recovery-regions", len(self.error_lines) + 1, self.limits.max_recovery_regions)
        self.python_entry = None
        line = self.lines[line_index]
        if line.decoded_start < line.decoded_content_end:
            self.push_piece(
                line.decoded_start,
                line.decoded_content_end,
                StructuralPieceKind.ERROR_REGION,
                IniSyntaxKind.ERROR_REGION,
            )
        logical_index = self.add_logical(line_index, IniLogicalLineKind.ERROR)
        node = self.issue_node(NodeRole.INI_ERROR_LINE)
        physical = self.physical_lines[line.physical_index]
        self.error_lines.append(
            IniErrorLine(
                node=node,
                logical_line=self.logical_lines[logical_index].node,
                physical_line=physical.node,
                span=physical.content_span,
                code=code,
            )
        )
        self.diagnostic(
            code,
            DiagnosticCategory.SYNTAX,
            physical.content_span.start_byte,
            physical.content_span.end_byte,
            True,
        )

    # -- syntax pieces ------------------------------------------------------

    def push_bom(self) -> None:
        """The BOM scalar is trivia with kind Bom (parser.rs:907-919)."""
        if self.source.encoding_facts().bom is not None and self.text.startswith("﻿"):
            self.push_piece(0, _FEFF_UTF8_LEN, StructuralPieceKind.TRIVIA, IniSyntaxKind.BOM)

    def push_comment(self, line: _ScannedLine, leading: int) -> None:
        """Comment marker and payload trivia (parser.rs:921-943)."""
        self.push_optional_whitespace(line, 0, leading)
        self.push_piece_local(
            line, leading, leading + 1, StructuralPieceKind.TRIVIA, IniSyntaxKind.COMMENT_MARKER
        )
        length = line.decoded_content_end - line.decoded_start
        if leading + 1 < length:
            self.push_piece_local(
                line,
                leading + 1,
                length,
                StructuralPieceKind.TRIVIA,
                IniSyntaxKind.COMMENT_TEXT,
            )

    def push_section_syntax(
        self, line: _ScannedLine, open_start: int, name_start: int, name_end: int, close_end: int
    ) -> None:
        """SectionOpen / SectionName / SectionClose tokens (parser.rs:945-971)."""
        self.push_piece_local(line, open_start, name_start, StructuralPieceKind.TOKEN, IniSyntaxKind.SECTION_OPEN)
        self.push_piece_local(line, name_start, name_end, StructuralPieceKind.TOKEN, IniSyntaxKind.SECTION_NAME)
        self.push_piece_local(line, name_end, close_end, StructuralPieceKind.TOKEN, IniSyntaxKind.SECTION_CLOSE)

    def push_entry_syntax(
        self,
        line: _ScannedLine,
        key_start: int,
        key_end: int,
        delimiter_start: int,
        delimiter_end: int,
        value_start: int,
        value_end: int,
        quote: tuple[int, int, int, int] | None,
    ) -> None:
        """EntryKey / Delimiter / [Quote] EntryValue [Quote] pieces
        (parser.rs:973-1018)."""
        self.push_piece_local(line, key_start, key_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_KEY)
        self.push_piece_local(
            line, delimiter_start, delimiter_end, StructuralPieceKind.TOKEN, IniSyntaxKind.DELIMITER
        )
        if quote is not None:
            open_start, open_end, close_start, close_end = quote
            self.push_piece_local(line, open_start, open_end, StructuralPieceKind.TOKEN, IniSyntaxKind.QUOTE)
            if value_start < value_end:
                self.push_piece_local(
                    line,
                    value_start,
                    value_end,
                    StructuralPieceKind.TOKEN,
                    IniSyntaxKind.ENTRY_VALUE,
                )
            self.push_piece_local(
                line, close_start, close_end, StructuralPieceKind.TOKEN, IniSyntaxKind.QUOTE
            )
        elif value_start < value_end:
            self.push_piece_local(
                line, value_start, value_end, StructuralPieceKind.TOKEN, IniSyntaxKind.ENTRY_VALUE
            )

    def push_windows_value_syntax(
        self,
        line: _ScannedLine,
        literal_start: int,
        literal_end: int,
        value_start: int,
        value_end: int,
        quote_style: IniQuoteStyle,
    ) -> None:
        """Windows value pieces (parser.rs:1020-1037)."""
        if quote_style is IniQuoteStyle.NONE:
            return self.push_entry_syntax(
                line, 0, 0, 0, 0, literal_start, literal_end, None
            )
        self.push_entry_syntax(
            line,
            0,
            0,
            0,
            0,
            value_start,
            value_end,
            (literal_start, value_start, value_end, literal_end),
        )

    def push_line_break(self, line_index: int) -> None:
        """Exact LF/CRLF trivia (parser.rs:1039-1049)."""
        line = self.lines[line_index]
        if line.decoded_break_start < line.decoded_end:
            self.push_piece(
                line.decoded_break_start,
                line.decoded_end,
                StructuralPieceKind.TRIVIA,
                IniSyntaxKind.LINE_BREAK,
            )

    def push_optional_whitespace(self, line: _ScannedLine, start: int, end: int) -> None:
        """Optional horizontal whitespace trivia (parser.rs:1051-1065)."""
        if start < end:
            self.push_piece_local(line, start, end, StructuralPieceKind.TRIVIA, IniSyntaxKind.WHITESPACE)

    def push_piece_local(
        self,
        line: _ScannedLine,
        start: int,
        end: int,
        kind: StructuralPieceKind,
        syntax: IniSyntaxKind,
    ) -> None:
        """One line-relative piece (parser.rs:1067-1082)."""
        if start == end:
            return
        self.push_piece(line.decoded_start + start, line.decoded_start + end, kind, syntax)

    def push_piece(
        self, decoded_start: int, decoded_end: int, kind: StructuralPieceKind, syntax: IniSyntaxKind
    ) -> None:
        """One exhaustive source piece (parser.rs:1084-1107)."""
        observed = len(self.pieces) + 1
        self.check_limit("syntax-pieces", observed, self.limits.common.max_token_count)
        span = self.raw_span(decoded_start, decoded_end)
        if span.is_empty():
            raise IniFormationFailure(
                IniFormationFailureKind.SYNTAX_PIECES,
                resource_name="source-coordinate-coverage",
                observed=1,
                limit=0,
            )
        self.pieces.append(StructuralPiece(span=span, kind=kind))
        self.syntax_kinds.append(syntax)

    # -- coordinates and helpers --------------------------------------------

    def raw_span(self, decoded_start: int, decoded_end: int) -> object:
        """Maps decoded UTF-8 byte offsets to raw-byte spans
        (parser.rs:1109-1125)."""
        try:
            start = self.source.raw_byte_at(DecodedOffset.utf8_byte(decoded_start))
            end = self.source.raw_byte_at(DecodedOffset.utf8_byte(decoded_end))
        except Exception:
            raise IniFormationFailure(
                IniFormationFailureKind.SOURCE_BYTES,
                resource_name="source-coordinate-boundary",
                observed=1,
                limit=0,
            ) from None
        try:
            return self.authority.span(start, end)
        except Exception:
            raise IniFormationFailure(
                IniFormationFailureKind.SOURCE_BYTES,
                resource_name="source-coordinate-boundary",
                observed=1,
                limit=0,
            ) from None

    def decoded(self, line: _ScannedLine) -> str:
        """Decoded content of one scanned line."""
        return self.text_utf8[line.decoded_start : line.decoded_content_end].decode("utf-8")

    def issue_node(self, role: NodeRole) -> NodeRef:
        """One fresh node identity (parser.rs:1132-1142)."""
        observed = self.next_node + 1
        self.check_limit("nodes", observed, self.limits.common.max_node_count)
        node = self.authority.node_ref(self.next_node, role)
        self.next_node += 1
        return node

    def check_limit(self, name: str, observed: int, limit: int) -> None:
        """Fatal when a configured limit is exceeded (parser.rs:1144-1156)."""
        if observed > limit:
            raise IniFormationFailure(
                IniFormationFailureKind.DIAGNOSTICS if name == "diagnostics" else
                IniFormationFailureKind.SOURCE_BYTES if name == "source-bytes" else
                IniFormationFailureKind.TOKEN_COUNT if name == "syntax-pieces" else
                IniFormationFailureKind.NODE_COUNT if name == "nodes" else
                IniFormationFailureKind.PHYSICAL_LINES if name == "physical-lines" else
                IniFormationFailureKind.PHYSICAL_LINE_BYTES if name == "physical-line-bytes" else
                IniFormationFailureKind.PHYSICAL_LINE_SCALARS if name == "physical-line-scalars" else
                IniFormationFailureKind.LOGICAL_LINES if name == "logical-lines" else
                IniFormationFailureKind.LOGICAL_LINE_BYTES if name == "logical-line-bytes" else
                IniFormationFailureKind.LOGICAL_LINE_SCALARS if name == "logical-line-scalars" else
                IniFormationFailureKind.CONTINUATION_LINES if name == "continuation-lines" else
                IniFormationFailureKind.SECTIONS if name == "sections" else
                IniFormationFailureKind.ENTRIES if name == "entries" else
                IniFormationFailureKind.DUPLICATE_GROUP_MEMBERS if name == "duplicate-group-members" else
                IniFormationFailureKind.RECOVERY_REGIONS if name == "recovery-regions" else
                IniFormationFailureKind.DECODED_UTF8_BYTES if name == "decoded-utf8-bytes" else
                IniFormationFailureKind.DECODED_SCALARS if name == "decoded-scalars" else
                IniFormationFailureKind.TOKEN_COUNT,
                resource_name=name,
                observed=observed,
                limit=limit,
            )

    def diagnostic(
        self, code: str, category: DiagnosticCategory, start: int, end: int, recovered: bool
    ) -> None:
        """One ordered diagnostic (parser.rs:1158-1195)."""
        self.check_limit("diagnostics", len(self.diagnostics) + 1, self.limits.common.max_diagnostics)
        try:
            primary = self.authority.span(start, end)
        except Exception:
            raise IniFormationFailure(
                IniFormationFailureKind.DIAGNOSTICS,
                resource_name="diagnostic-coordinate",
                observed=start,
                limit=0,
            ) from None
        self.diagnostics.append(
            IniDiagnostic(
                code=code,
                category=category,
                severity=IniSeverity.ERROR if recovered else IniSeverity.WARNING,
                primary=primary,
                occurrence=self.occurrence,
            )
        )
        self.occurrence += 1
        self.recovered = self.recovered or recovered

    # -- comparison and duplicate groups -------------------------------------

    def section_comparison(self, name: str) -> str:
        """Profile-specific section comparison name (parser.rs:1197-1202)."""
        if self.profile is IniProfile.WINDOWS_V1:
            return name.lower()
        return name

    def key_comparison(self, key: str) -> str:
        """Profile-specific key comparison name (parser.rs:1204-1210)."""
        if self.profile is IniProfile.WINDOWS_V1:
            return key.lower()
        if self.profile is IniProfile.PYTHON_CONFIGPARSER_V1:
            return optionxform(key)
        return key

    def assign_duplicate_groups(self) -> None:
        """Deterministic duplicate/case-equivalence groups
        (parser.rs:1212-1304; RFC 0009 §5/§6/§7 duplicate rules)."""
        section_groups: dict[str, list[int]] = {}
        for index, section in enumerate(self.sections):
            section_groups.setdefault(section.comparison_name, []).append(index)
        next_group = 1
        for name in sorted(section_groups):
            indices = section_groups[name]
            if len(indices) <= 1:
                continue
            self.check_limit(
                "duplicate-group-members", len(indices), self.limits.max_duplicate_group_members
            )
            group = next_group
            next_group += 1
            first_name = self.sections[indices[0]].name
            for index in indices:
                section = self.sections[index]
                self.sections[index] = replace(section, duplicate_group=group)
            for index in indices[1:]:
                section = self.sections[index]
                code = (
                    "ini.formation.duplicate-section@1"
                    if section.name == first_name
                    else "ini.formation.case-collision@1"
                )
                self.diagnostic(
                    code,
                    DiagnosticCategory.SEMANTIC,
                    section.span.start_byte,
                    section.span.end_byte,
                    self.profile is not IniProfile.WINDOWS_V1,
                )

        entry_groups: dict[tuple[str, str], list[int]] = {}
        for index, entry in enumerate(self.entries):
            section_index = self.entry_section_indices[index]
            section_identity = (
                self.sections[section_index].comparison_name
                if self.profile is IniProfile.WINDOWS_V1
                else str(section_index)
            )
            entry_groups.setdefault((section_identity, entry.comparison_key), []).append(index)
        for key in sorted(entry_groups):
            indices = entry_groups[key]
            if len(indices) <= 1:
                continue
            self.check_limit(
                "duplicate-group-members", len(indices), self.limits.max_duplicate_group_members
            )
            group = next_group
            next_group += 1
            first_key = self.entries[indices[0]].key
            for index in indices:
                entry = self.entries[index]
                self.entries[index] = replace(entry, duplicate_group=group)
            for index in indices[1:]:
                entry = self.entries[index]
                code = (
                    "ini.formation.duplicate-entry@1"
                    if entry.key == first_key
                    else "ini.formation.case-collision@1"
                )
                self.diagnostic(
                    code,
                    DiagnosticCategory.SEMANTIC,
                    entry.span.start_byte,
                    entry.span.end_byte,
                    self.profile is not IniProfile.WINDOWS_V1,
                )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse(
    raw: bytes,
    profile: IniProfile,
    selection: IniEncodingSelection,
    limits: IniParseLimits,
) -> object:
    """Parses one immutable INI snapshot under exactly one selected profile
    (crates/consema-ini/src/lib.rs:664-671, parser.rs:16-35).

    Returns the ``IniDocument`` from :mod:`consema.ini.document`; raises
    :class:`IniFormationFailure` for fatal encoding, source, or limit
    failures — no Document exists then (RFC 0009 §3, docs/rfcs/0009-...:70-
    73).
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
    except SourceError as error:
        raise IniFormationFailure(IniFormationFailureKind.SOURCE, source=error) from None
    _validate_profile_encoding(source, profile, selection)
    parser = _Parser(source, profile, limits)
    parser.scan_physical_lines()
    parser.push_bom()
    for line_index in range(len(parser.lines)):
        parser.parse_line(line_index)
        parser.push_line_break(line_index)
    if profile is IniProfile.PORTABLE_V1 and not parser.sections:
        at = source.len()
        parser.diagnostic(
            "ini.parse.missing-section@1", DiagnosticCategory.CONFORMANCE, at, at, True
        )
    parser.assign_duplicate_groups()
    structural_index = LosslessStructuralIndex.new(
        parser.authority.identity, source.len(), parser.pieces
    )
    sort_diagnostics(parser.diagnostics)
    from consema.ini.document import IniDocument

    return IniDocument(
        authority=parser.authority,
        source=source,
        profile=profile,
        structural_index=structural_index,
        syntax_kinds=tuple(parser.syntax_kinds),
        _formation_status=(
            FormationStatus.RECOVERED if parser.recovered else FormationStatus.COMPLETE
        ),
        diagnostics=tuple(parser.diagnostics),
        physical_lines=tuple(parser.physical_lines),
        logical_lines=tuple(parser.logical_lines),
        sections=tuple(parser.sections),
        entries=tuple(parser.entries),
        error_lines=tuple(parser.error_lines),
        parse_limits=limits,
        root_node=parser.root_node,
    )


def _encoding_request(
    profile: IniProfile, selection: IniEncodingSelection
) -> EncodingRequest:
    """Builds the source resolution request (parser.rs:37-59)."""
    encoding = SourceEncoding.utf8() if selection.kind == "ProfileDefault" else selection.encoding
    if encoding is not None and encoding.kind is SourceEncodingKind.BINARY:
        raise IniFormationFailure(IniFormationFailureKind.PROFILE_ENCODING)
    request = EncodingRequest.new(SourceEncoding.utf8())
    if selection.kind == "Explicit":
        assert encoding is not None
        request = request.with_caller_override(encoding)
    if encoding is not None and encoding.kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
        request = request.with_bom_policy(BomPolicy.TREAT_AS_CONTENT)
    if profile is IniProfile.PORTABLE_V1 and encoding != SourceEncoding.utf8():
        raise IniFormationFailure(IniFormationFailureKind.PROFILE_ENCODING)
    return request


def _validate_profile_encoding(
    source: SourceSnapshot, profile: IniProfile, selection: IniEncodingSelection
) -> None:
    """Profile-specific encoding acceptance (parser.rs:61-94; RFC 0009 §3)."""
    facts = source.encoding_facts()
    if profile is IniProfile.PORTABLE_V1:
        valid = facts.selected == SourceEncoding.utf8() and facts.bom is None
    elif profile is IniProfile.WINDOWS_V1:
        if selection.kind == "ProfileDefault":
            valid = (
                facts.selected == SourceEncoding.utf16le()
                and facts.bom is not None
                and facts.bom.value == "Utf16Le"
            ) or (
                facts.selected == SourceEncoding.utf8()
                and facts.bom is None
                and all(byte < 0x80 for byte in source.bytes())
            )
        elif selection.encoding == SourceEncoding.utf16le():
            valid = (
                facts.selected == SourceEncoding.utf16le()
                and facts.bom is not None
                and facts.bom.value == "Utf16Le"
            )
        elif (
            selection.encoding is not None
            and selection.encoding.kind is SourceEncodingKind.WINDOWS_CODE_PAGE
        ):
            valid = (
                facts.selected == selection.encoding
                and facts.bom_policy is BomPolicy.TREAT_AS_CONTENT
                and facts.bom is None
            )
        else:
            valid = False
    else:
        valid = facts.selected.kind is not SourceEncodingKind.BINARY
    if not valid:
        raise IniFormationFailure(IniFormationFailureKind.PROFILE_ENCODING)


# ---------------------------------------------------------------------------
# Character helpers (parser.rs:1307-1362)
# ---------------------------------------------------------------------------


def _leading_horizontal(value: str) -> int:
    """Byte count of the leading space/tab run (parser.rs:1307-1312)."""
    count = 0
    for byte in value.encode("utf-8"):
        if not is_horizontal(byte):
            break
        count += 1
    return count


def _trim_horizontal_bounds(value) -> tuple[int, int]:
    """Byte range of the content with horizontal edges removed
    (parser.rs:1314-1321); accepts str or utf-8 bytes."""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    start = 0
    for byte in raw:
        if not is_horizontal(byte):
            break
        start += 1
    end = start
    for index in range(len(raw) - 1, -1, -1):
        if not is_horizontal(raw[index]):
            end = index + 1
            break
    return (min(start, end), end)


def _find_byte(raw: bytes, targets: tuple[int, ...], start: int = 0) -> int | None:
    """First byte in ``targets`` at or after ``start``."""
    for index in range(start, len(raw)):
        if raw[index] in targets:
            return index
    return None


def _quoted_windows_value(value, absolute_start: int) -> tuple[int, int, IniQuoteStyle]:
    """Exact outer-quote recognition (parser.rs:1341-1358; RFC 0009 §6:
    an exactly single- or double-quoted value has a semantic content span
    without the outer marks)."""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    if len(raw) >= 2:
        first = raw[0]
        last = raw[-1]
        if first == last and first in (0x27, 0x22):
            style = IniQuoteStyle.SINGLE if first == 0x27 else IniQuoteStyle.DOUBLE
            return (
                absolute_start + 1,
                absolute_start + len(raw) - 1,
                style,
            )
    return (absolute_start, absolute_start + len(raw), IniQuoteStyle.NONE)


def _first_python_delimiter(value) -> int | None:
    """First ``=`` or ``:`` position (parser.rs:1360-1362; RFC 0009 §7:
    the first configured delimiter occurrence splits option and value)."""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    for index, byte in enumerate(raw):
        if byte in (0x3D, 0x3A):
            return index
    return None
