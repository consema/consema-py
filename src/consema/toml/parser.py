"""TOML 1.0 parser forming immutable Document snapshots.

Authority:

- RFC 0001 (docs/rfcs/0001-toml-1.0-profile.md) §3: formation runs
  max_source_bytes, UTF-8 validation, TOML 1.0 syntax/semantic validation,
  then max_token_count / max_node_count / max_nesting_depth; any limit hit
  is a fatal ``core.parse.resource-limit@1``; syntax failure is
  ``toml.parse.syntax@1`` with the backend-provable minimal span and stable
  arguments; no truncated success documents.
- The parse pipeline order transcribes crates/consema-toml/src/parser.rs:17-63.
- The value grammar transcribes the pinned backend toml_edit 0.22.27
  (crates/consema-toml/src/lib.rs:104 pins toml_edit 0.22.27; the backend
  is not the spec, RFC 0001 §1, but the arbiter for byte facts). All
  edge-case behaviors below were verified empirically against toml_edit
  0.22.27 before transcription:
  - value dispatch order string -> array -> inline-table -> date-time ->
    float -> integer (toml_edit parser/value.rs:17-85);
  - datetime semantics (toml_edit 0.22.27 parser/datetime.rs:25-249):
    exactly four-digit year, two-digit month/day/hour/minute/second,
    second 00-60 (leap second accepted), fractional second truncated to
    nine digits and scaled to nanoseconds, separator ``T``/``t``/space,
    offset ``Z``/``z`` or ``±HH:MM`` with hours <= 23 and minutes <= 59;
    the parser consumes a valid prefix (the statement layer rejects
    trailing junk), a failed time part backtracks to a local date (opt
    semantics), and a failed offset after a matched sign commits the
    whole datetime to failure (cut_err semantics);
  - number semantics (toml_edit parser/numbers.rs:40-305): signed decimal
    with digit1-9 leading rule (no leading zeros, ``01``/``0_1``
    rejected), unsigned 0x/0o/0b, underscores only between digits, i64
    range enforced, float requires frac or exp (``3.e5``/``5.``/``.5``
    rejected), decimal overflow to +inf rejected while overflow to -inf is
    accepted (numbers.rs:202-204; ``-1e99999`` parses to -inf);
  - string grammar (toml_edit parser/strings.rs:46-362): basic escapes
    \\b \\t \\n \\f \\r \\" \\\\ \\uXXXX \\UXXXXXXXX with valid scalar
    values only; control characters must be escaped; multiline basic
    strings trim the first newline, normalize CRLF to LF, trim backslash-
    whitespace-newline runs, and treat runs of one or two quotes as
    content only when followed by non-quote content or by the closing
    delimiter (mlb_quotes/mll_quotes, strings.rs:213-233, 168-194,
    310-327); inline tables reject trailing commas while arrays allow them;
  - the table state machine (toml_edit parser/state.rs:55-269): dotted
    keys cannot redefine tables defined in [table] form, headers cannot
    redefine dotted tables, [a.b] headers may be adopted by a later [a]
    header (span overwritten by the adopting header, state.rs:138-173),
    keys cannot extend a value, array-of-tables descends into its last
    element, dotted keys inside inline tables create nested inline tables.
- Table flavors transcribe parser.rs:232-241; the entity/span model
  transcribes parser.rs:84-338 (root spans the source; a table spans its
  header through its last value; a key spans its literal; an entry spans
  key start through value end; implicit logical nodes use their creating
  key segment span, RFC 0001 §2.2).
"""

from __future__ import annotations

import struct
from collections import OrderedDict

from consema.document.limits import ParseLimits
from consema.document.source import SourceSnapshot
from consema.document.structural import DocumentAuthority, LosslessStructuralIndex

from consema.toml.document import (
    Document,
    TableFlavor,
    TomlDate,
    TomlDateTime,
    TomlProfile,
    TomlTime,
    _ElementEntity,
    _Entity,
    _EntryEntity,
    _ItemEntity,
    _InternalItemKind,
    _KeyEntity,
)
from consema.toml.errors import TomlDiagnostic, TomlFormationFailure
from consema.toml.syntax import preflight_delimiter_nesting, tokenize

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# IEEE-754 binary64 canonical special bits (numbers.rs:286-305)
_FLOAT_INF_BITS = 0x7FF0000000000000
_FLOAT_NEG_INF_BITS = 0xFFF0000000000000
_FLOAT_NAN_BITS = 0x7FF8000000000000
_FLOAT_NEG_NAN_BITS = 0xFFF8000000000000


class _SyntaxError(Exception):
    """Internal syntax failure carrying raw byte offsets; the public
    entry point binds the snapshot span."""

    __slots__ = ("start", "end", "reason")

    def __init__(self, start: int, end: int, reason: str) -> None:
        super().__init__(reason)
        self.start = start
        self.end = end
        self.reason = reason


# ---------------------------------------------------------------------------
# Byte-offset map
# ---------------------------------------------------------------------------


class _ByteMap:
    """Cumulative UTF-8 byte offsets per decoded scalar index (the source
    is UTF-8, so every scalar boundary maps to exactly one raw offset;
    RFC 0003 §5 keeps spans raw-byte)."""

    __slots__ = ("offsets",)

    def __init__(self, text: str) -> None:
        offsets = [0]
        total = 0
        for character in text:
            total += len(character.encode("utf-8"))
            offsets.append(total)
        self.offsets = offsets

    def byte(self, index: int) -> int:
        return self.offsets[index]


# ---------------------------------------------------------------------------
# Value node tree
# ---------------------------------------------------------------------------


class _Node:
    __slots__ = ("start", "end")

    def __init__(self, start: int, end: int) -> None:
        self.start = start
        self.end = end


class _ScalarNode(_Node):
    __slots__ = ("kind",)

    def __init__(self, kind: _InternalItemKind, start: int, end: int) -> None:
        super().__init__(start, end)
        self.kind = kind


class _ArrayNode(_Node):
    __slots__ = ("elements",)

    def __init__(self, elements: list[_Node], start: int, end: int) -> None:
        super().__init__(start, end)
        self.elements = elements


class _InlineNode(_Node):
    __slots__ = ("entries",)

    def __init__(self, entries: OrderedDict[str, tuple[int, int, _Node]], start: int, end: int) -> None:
        super().__init__(start, end)
        self.entries = entries


class _TableNode:
    """One logical TOML table with ordered items."""

    __slots__ = ("flavor", "items", "span_start", "span_end")

    def __init__(self, flavor: TableFlavor) -> None:
        self.flavor = flavor
        # name -> (key_start, key_end, node) where node is a _Node leaf or
        # a nested _TableNode / _ArrayOfTablesNode
        self.items: OrderedDict[str, tuple[int, int, object]] = OrderedDict()
        self.span_start: int | None = None
        self.span_end: int | None = None

    def extend_span(self, start: int, end: int) -> None:
        """Key-value span extension (state.rs:74-76: only the current
        table extends)."""
        if self.span_start is None:
            self.span_start = start
        self.span_end = end

    def set_header_span(self, start: int, end: int) -> None:
        """Header span, overwriting any adopted span (state.rs:163-168)."""
        self.span_start = start
        self.span_end = end


class _ArrayOfTablesNode:
    __slots__ = ("elements", "key_start", "key_end")

    def __init__(self, key_start: int, key_end: int) -> None:
        self.elements: list[_TableNode] = []
        self.key_start = key_start
        self.key_end = key_end


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class Parser:
    """Cursor-based TOML 1.0 parser over the decoded text; spans are raw
    byte offsets via ``bm``."""

    def __init__(self, text: str, bm: _ByteMap) -> None:
        self.text = text
        self.bm = bm
        self.pos = 0
        self.root = _TableNode(TableFlavor.ROOT)
        self.current = self.root

    # -- helpers ----------------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        index = self.pos + offset
        return self.text[index] if index < len(self.text) else ""

    def _starts(self, prefix: str, at: int | None = None) -> bool:
        at = self.pos if at is None else at
        return self.text.startswith(prefix, at)

    def _error(self, start: int, end: int, reason: str):
        """Raises a syntax failure at character indices."""
        raise _SyntaxError(self.bm.byte(start), self.bm.byte(end), reason)

    def _error_bytes(self, start: int, end: int, reason: str):
        """Raises a syntax failure at raw byte offsets (key segments are
        parsed with byte spans)."""
        raise _SyntaxError(start, end, reason)

    def _consume_newline(self) -> None:
        """newline = LF | CRLF (trivia.rs:63-72); a lone CR is not a
        newline and is a syntax error."""
        if self._peek() == "\r":
            self._advance(1)
            if self._peek() != "\n":
                self._error(self.pos - 1, self.pos + 1, "expected newline")
            self._advance(1)
            return
        self._advance(1)

    def _after_newline(self, index: int) -> int:
        if index < len(self.text) and self.text[index] == "\r":
            if index + 1 < len(self.text) and self.text[index + 1] == "\n":
                return index + 2
            return index + 1
        return index + 1

    def _advance(self, count: int) -> None:
        self.pos += count

    def _skip_ws(self) -> None:
        while self._peek() in (" ", "\t"):
            self._advance(1)

    def _skip_comment(self) -> None:
        if self._peek() == "#":
            while self._peek() not in ("", "\n", "\r"):
                self._advance(1)

    def _skip_ws_comment(self) -> None:
        while True:
            self._skip_ws()
            if self._peek() == "#":
                self._skip_comment()
                continue
            return

    def _skip_ws_comment_newline(self) -> None:
        while True:
            self._skip_ws_comment()
            if self._peek() in ("\n", "\r"):
                self._consume_newline()
                continue
            return

    def _expect_newline_or_eof(self) -> None:
        """Only whitespace/comments may follow a statement before the
        newline (or EOF)."""
        self._skip_ws_comment()
        if self._peek() in ("", "\n", "\r"):
            return
        self._error(self.pos, self.pos + 1, "expected newline, `#`")

    # -- top level --------------------------------------------------------

    def parse(self) -> _TableNode:
        while True:
            self._skip_ws_comment_newline()
            if self._peek() == "":
                return self.root
            if self._peek() == "[":
                self._parse_header()
            else:
                self._parse_key_value()
            self._expect_newline_or_eof()

    # -- key grammar ------------------------------------------------------

    def _parse_key_segment(self) -> tuple[str, int, int]:
        """One bare or quoted key segment; returns (decoded, start, end)."""
        start = self.pos
        char = self._peek()
        if char == '"':
            value, end = self._parse_basic_string()
            return value, self._byte(start), self._byte(end)
        if char == "'":
            value, end = self._parse_literal_string()
            return value, self._byte(start), self._byte(end)
        if char == "" or not (char.isascii() and (char.isalnum() or char in "_-")):
            self._error(self.pos, self.pos + 1, "invalid key")
        while self._peek() and (
            self._peek().isascii() and (self._peek().isalnum() or self._peek() in "_-")
        ):
            self._advance(1)
        return self.text[start : self.pos], self._byte(start), self._byte(self.pos)

    def _parse_dotted_key(self) -> tuple[list[tuple[str, int, int]], tuple[str, int, int]]:
        """Dotted key with optional whitespace around dots; returns
        (path segments, (leaf, start, end))."""
        path: list[tuple[str, int, int]] = []
        name, start, end = self._parse_key_segment()
        while True:
            self._skip_ws()
            if self._peek() != ".":
                break
            self._advance(1)
            self._skip_ws()
            path.append((name, start, end))
            name, start, end = self._parse_key_segment()
        return path, (name, start, end)

    # -- statements -------------------------------------------------------

    def _parse_key_value(self) -> None:
        path, (leaf, leaf_start, leaf_end) = self._parse_dotted_key()
        self._skip_ws()
        if self._peek() != "=":
            self._error(self.pos, self.pos + 1, "expected `=`")
        self._advance(1)
        self._skip_ws()
        node = self._parse_value()
        parent = self._descend(self.current, path, dotted=True)
        if leaf in parent.items:
            self._error_bytes(leaf_start, leaf_end, "duplicate key")
        parent.items[leaf] = (leaf_start, leaf_end, node)
        self.current.extend_span(leaf_start, node.end)

    def _parse_header(self) -> None:
        header_start = self._byte(self.pos)
        assert self._peek() == "["
        self._advance(1)
        is_array = False
        if self._peek() == "[":
            is_array = True
            self._advance(1)
        self._skip_ws()
        path: list[tuple[str, int, int]] = []
        name, start, end = self._parse_key_segment()
        while True:
            self._skip_ws()
            if self._peek() != ".":
                break
            self._advance(1)
            self._skip_ws()
            path.append((name, start, end))
            name, start, end = self._parse_key_segment()
        path.append((name, start, end))
        self._skip_ws()
        if self._peek() != "]" or (is_array and self._peek(1) != "]"):
            self._error(self.pos, self.pos + 1, "expected `]`")
        self._advance(1)
        if is_array:
            self._advance(1)
        header_end = self._byte(self.pos)
        self._apply_header(path, is_array, header_start, header_end)
        self._expect_newline_or_eof()

    def _apply_header(
        self,
        path: list[tuple[str, int, int]],
        is_array: bool,
        header_start: int,
        header_end: int,
    ) -> None:
        """state.rs start_table 138-173 / start_array_table 105-136."""
        leaf_name, leaf_start, leaf_end = path[-1]
        parent = self._descend(self.root, path[:-1], dotted=False)
        entry = parent.items.get(leaf_name)
        if is_array:
            if entry is None:
                array = _ArrayOfTablesNode(leaf_start, leaf_end)
                parent.items[leaf_name] = (leaf_start, leaf_end, array)
            elif not isinstance(entry[2], _ArrayOfTablesNode):
                self._error_bytes(leaf_start, leaf_end, "duplicate key")
            else:
                array = entry[2]
            self.current = _TableNode(TableFlavor.STANDARD)
            array.elements.append(self.current)
        else:
            if entry is None:
                self.current = _TableNode(TableFlavor.STANDARD)
                parent.items[leaf_name] = (leaf_start, leaf_end, self.current)
            elif isinstance(entry[2], _TableNode) and entry[2].flavor is TableFlavor.IMPLICIT:
                # [a.b] created an implicit table; a later [a] adopts it
                entry[2].flavor = TableFlavor.STANDARD
                self.current = entry[2]
            else:
                self._error_bytes(leaf_start, leaf_end, "duplicate key")
        # the header span runs from '[' through ']' (state.rs:163-168)
        self.current.set_header_span(header_start, header_end)

    def _descend(
        self, table: _TableNode, path: list[tuple[str, int, int]], dotted: bool
    ) -> _TableNode:
        """state.rs descend_path 228-269."""
        for name, start, end in path:
            entry = table.items.get(name)
            if entry is None:
                child = _TableNode(TableFlavor.DOTTED if dotted else TableFlavor.IMPLICIT)
                child.set_header_span(start, end)
                table.items[name] = (start, end, child)
                table = child
            else:
                item = entry[2]
                if isinstance(item, _Node):
                    self._error_bytes(start, end, "dotted key extends a value")
                elif isinstance(item, _ArrayOfTablesNode):
                    table = item.elements[-1]
                else:
                    if dotted and item.flavor is TableFlavor.STANDARD:
                        self._error_bytes(start, end, "dotted key redefines a [table]")
                    table = item
        return table

    # -- values -----------------------------------------------------------

    def _parse_value(self) -> _Node:
        """value dispatch (toml_edit parser/value.rs:17-85)."""
        start = self.pos
        char = self._peek()
        if char == '"':
            if self._starts('"""'):
                value, end = self._parse_multiline_basic_string()
            else:
                value, end = self._parse_basic_string()
            return _ScalarNode(_InternalItemKind.string(value), self._byte(start), self._byte(end))
        if char == "'":
            if self._starts("'''"):
                value, end = self._parse_multiline_literal_string()
            else:
                value, end = self._parse_literal_string()
            return _ScalarNode(_InternalItemKind.string(value), self._byte(start), self._byte(end))
        if char == "[":
            return self._parse_array()
        if char == "{":
            return self._parse_inline_table()
        if char in "+-0123456789":
            datetime = self._try_datetime()
            if datetime is not None:
                value, end = datetime
                return _ScalarNode(value, self._byte(start), self._byte(end))
            number = self._try_number()
            if number is not None:
                value, end = number
                return _ScalarNode(value, self._byte(start), self._byte(end))
        if char == "t" and self._starts("true"):
            self._advance(4)
            return _ScalarNode(_InternalItemKind.boolean(True), self._byte(start), self._byte(self.pos))
        if char == "f" and self._starts("false"):
            self._advance(5)
            return _ScalarNode(_InternalItemKind.boolean(False), self._byte(start), self._byte(self.pos))
        if char == "i" and self._starts("inf"):
            self._advance(3)
            return _ScalarNode(_InternalItemKind.float_bits(_FLOAT_INF_BITS), self._byte(start), self._byte(self.pos))
        if char == "n" and self._starts("nan"):
            self._advance(3)
            return _ScalarNode(_InternalItemKind.float_bits(_FLOAT_NAN_BITS), self._byte(start), self._byte(self.pos))
        self._error(start, start + 1, "invalid value")

    # -- numbers ----------------------------------------------------------

    def _scan_bare_run(self) -> str:
        start = self.pos
        while self._peek() and self._peek() not in (
            " ", "\t", "\r", "\n", "#", ",", "]", "}", "'", '"',
        ):
            self._advance(1)
        return self.text[start : self.pos]

    def _try_number(self) -> tuple[_InternalItemKind, int] | None:
        """float -> integer (numbers.rs:197-305)."""
        token = self._scan_bare_run()
        float_value = self._parse_float_token(token)
        if float_value is not None:
            return float_value, self.pos
        integer_value = self._parse_integer_token(token)
        if integer_value is not None:
            return integer_value, self.pos
        return None

    def _parse_float_token(self, token: str) -> _InternalItemKind | None:
        if token in ("inf", "+inf", "-inf", "nan", "+nan", "-nan"):
            if token == "-inf":
                return _InternalItemKind.float_bits(_FLOAT_NEG_INF_BITS)
            if token == "-nan":
                return _InternalItemKind.float_bits(_FLOAT_NEG_NAN_BITS)
            if token == "nan":
                return _InternalItemKind.float_bits(_FLOAT_NAN_BITS)
            return _InternalItemKind.float_bits(_FLOAT_INF_BITS)
        if not _float_grammar_ok(token):
            return None
        try:
            value = float(token.replace("_", ""))
        except ValueError:
            return None
        if value == float("inf"):
            # numbers.rs:202-204: decimal overflow to +inf is rejected;
            # overflow to -inf is accepted (verified against the backend)
            return None
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        return _InternalItemKind.float_bits(bits)

    def _parse_integer_token(self, token: str) -> _InternalItemKind | None:
        """integer = dec-int / hex-int / oct-int / bin-int (numbers.rs:40-50).

        The radix forms are unsigned: the value parser only dispatches
        0x/0o/0b when the token has no sign (numbers.rs:42-46 peek on the
        first two bytes; ``-0x1`` fails)."""
        sign = 1
        index = 0
        if token and token[0] in "+-":
            sign = -1 if token[0] == "-" else 1
            index = 1
        rest = token[index:]
        if not rest:
            return None
        value: int | None = None
        if index == 0 and rest[0] == "0" and len(rest) > 1 and rest[1] in "xXoObB":
            prefix = rest[1].lower()
            digits = rest[2:]
            radix = {"x": 16, "o": 8, "b": 2}[prefix]
            if not _radix_ok(digits, radix):
                return None
            value = int(digits.replace("_", ""), radix)
        else:
            int_end = _scan_dec_int(token, 0)
            if int_end is None or int_end != len(token):
                return None
            value = int(token.replace("_", ""), 10)
        if sign < 0:
            value = -value
        if not _INT64_MIN <= value <= _INT64_MAX:
            return None
        return _InternalItemKind.integer(value)

    # -- strings ----------------------------------------------------------

    def _parse_basic_string(self) -> tuple[str, int]:
        """basic-string (strings.rs:46-145)."""
        assert self._peek() == '"'
        start = self.pos
        self._advance(1)
        parts: list[str] = []
        while True:
            char = self._peek()
            if char == "":
                self._error(start, self.pos, "unterminated string")
            if char == '"':
                self._advance(1)
                return "".join(parts), self.pos
            if char == "\\":
                parts.append(self._parse_escape())
                continue
            if char in ("\n", "\r"):
                self._error(start, self.pos, "unterminated string")
            if not _basic_unescaped_ok(char):
                self._error(self.pos, self.pos + 1, "invalid basic string")
            parts.append(char)
            self._advance(1)

    def _parse_escape(self) -> str:
        """escape-seq-char (strings.rs:93-145)."""
        start = self.pos
        assert self._peek() == "\\"
        self._advance(1)
        char = self._peek()
        if char == "":
            self._error(start, self.pos, "invalid escape sequence")
        mapping = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
        if char in mapping:
            self._advance(1)
            return mapping[char]
        if char == "u":
            return self._parse_hex_escape(4)
        if char == "U":
            return self._parse_hex_escape(8)
        self._error(start, self.pos + 1, "invalid escape sequence")

    def _parse_hex_escape(self, width: int) -> str:
        start = self.pos
        self._advance(1)
        digits = self.text[self.pos : self.pos + width]
        if len(digits) != width or not all(
            c.isascii() and (c.isdigit() or c in "abcdefABCDEF") for c in digits
        ):
            self._error(start, self.pos, "invalid unicode escape")
        self._advance(width)
        scalar = int(digits, 16)
        if scalar > 0x10FFFF or 0xD800 <= scalar <= 0xDFFF:
            self._error(start, self.pos, "invalid unicode escape")
        return chr(scalar)

    def _parse_literal_string(self) -> tuple[str, int]:
        """literal-string (strings.rs:256-282)."""
        assert self._peek() == "'"
        start = self.pos
        self._advance(1)
        parts: list[str] = []
        while True:
            char = self._peek()
            if char == "":
                self._error(start, self.pos, "unterminated string")
            if char == "'":
                self._advance(1)
                return "".join(parts), self.pos
            if char in ("\n", "\r"):
                self._error(start, self.pos, "unterminated string")
            if not _literal_char_ok(char):
                self._error(self.pos, self.pos + 1, "invalid literal string")
            parts.append(char)
            self._advance(1)

    def _parse_multiline_basic_string(self) -> tuple[str, int]:
        """ml-basic-string (strings.rs:147-254)."""
        start = self.pos
        assert self._starts('"""')
        self._advance(3)
        if self._peek() in ("\n", "\r"):
            self._consume_newline()
        parts: list[str] = []
        while True:
            content, advanced = self._mlb_content()
            parts.append(content)
            if not advanced:
                break
        while True:
            run = self._mlb_quote_run()
            if run == 0:
                break
            self._advance(run)
            content, advanced = self._mlb_content()
            if not advanced:
                # the quote run is not content without following content;
                # roll it back (mlb_body's else-break, strings.rs:184-187)
                self._advance(-run)
                break
            parts.append('"' * run)
            parts.append(content)
            while True:
                content, advanced = self._mlb_content()
                parts.append(content)
                if not advanced:
                    break
        for run in (2, 1):
            if self._starts('"' * run) and self._starts('"""', self.pos + run):
                parts.append('"' * run)
                self._advance(run)
                break
        if not self._starts('"""'):
            self._error(start, self.pos, "invalid multiline basic string")
        self._advance(3)
        return "".join(parts), self.pos

    def _mlb_quote_run(self) -> int:
        """One or two quotes followed by a non-quote, non-EOF byte
        (mlb_quotes, strings.rs:213-233: peek(none_of('"')) fails at
        end of input)."""
        if self._starts('""') and self._peek(2) not in ('"', ""):
            return 2
        if self._peek() == '"' and self._peek(1) not in ('"', ""):
            return 1
        return 0

    def _mlb_content(self) -> tuple[str, bool]:
        """mlb-content (strings.rs:196-211); a quote is not content and
        ends the body."""
        char = self._peek()
        if char in ("", '"'):
            return "", False
        if char == "\\":
            index = self.pos + 1
            while index < len(self.text) and self.text[index] in (" ", "\t"):
                index += 1
            if index < len(self.text) and self.text[index] in ("\n", "\r"):
                index = self._after_newline(index)
                while index < len(self.text) and (
                    self.text[index] in (" ", "\t") or self.text[index] in ("\n", "\r")
                ):
                    if self.text[index] in ("\n", "\r"):
                        index = self._after_newline(index)
                    else:
                        index += 1
                self.pos = index
                return "", True
            return self._parse_escape(), True
        if char in ("\n", "\r"):
            self._consume_newline()
            return "\n", True
        if not _multiline_basic_unescaped_ok(char):
            self._error(self.pos, self.pos + 1, "invalid multiline basic string")
        start = self.pos
        while self._peek() and _multiline_basic_unescaped_ok(self._peek()):
            self._advance(1)
        return self.text[start : self.pos], True

    def _parse_multiline_literal_string(self) -> tuple[str, int]:
        """ml-literal-string (strings.rs:284-362)."""
        start = self.pos
        assert self._starts("'''")
        self._advance(3)
        if self._peek() in ("\n", "\r"):
            self._consume_newline()
        parts: list[str] = []
        run_start = self.pos
        while True:
            char = self._peek()
            if char == "":
                self._error(start, self.pos, "invalid multiline literal string")
            if char == "'":
                run = self._mll_quote_run()
                if run == 0:
                    break
                if not _mll_content_ok(self._peek(run)):
                    break
                parts.append(self.text[run_start : self.pos])
                parts.append("'" * run)
                self._advance(run)
                run_start = self.pos
                continue
            if char in ("\n", "\r"):
                newline_start = self.pos
                self._consume_newline()
                parts.append(self.text[run_start:newline_start])
                parts.append("\n")
                run_start = self.pos
                continue
            if not _multiline_literal_char_ok(char):
                self._error(self.pos, self.pos + 1, "invalid multiline literal string")
            self._advance(1)
        parts.append(self.text[run_start : self.pos])
        for run in (2, 1):
            if self._starts("'" * run) and self._starts("'''", self.pos + run):
                parts.append("'" * run)
                self._advance(run)
                break
        if not self._starts("'''"):
            self._error(start, self.pos, "invalid multiline literal string")
        self._advance(3)
        return "".join(parts), self.pos

    def _mll_quote_run(self) -> int:
        if self._starts("''") and self._peek(2) not in ("'", ""):
            return 2
        if self._peek() == "'" and self._peek(1) not in ("'", ""):
            return 1
        return 0

    # -- arrays and inline tables -----------------------------------------

    def _parse_array(self) -> _ArrayNode:
        start = self.pos
        assert self._peek() == "["
        self._advance(1)
        elements: list[_Node] = []
        self._skip_ws_comment_newline()
        while True:
            if self._peek() == "]":
                self._advance(1)
                return _ArrayNode(elements, self._byte(start), self._byte(self.pos))
            elements.append(self._parse_value())
            self._skip_ws_comment_newline()
            if self._peek() == ",":
                self._advance(1)
                self._skip_ws_comment_newline()
                continue
            if self._peek() == "]":
                self._advance(1)
                return _ArrayNode(elements, self._byte(start), self._byte(self.pos))
            self._error(self.pos, self.pos + 1, "expected `,` or `]`")

    def _parse_inline_table(self) -> _InlineNode:
        start = self.pos
        assert self._peek() == "{"
        self._advance(1)
        entries: OrderedDict[str, tuple[int, int, _Node]] = OrderedDict()
        self._skip_ws()
        if self._peek() == "}":
            self._advance(1)
            return _InlineNode(entries, self._byte(start), self._byte(self.pos))
        while True:
            if self._peek() == "}":
                self._advance(1)
                return _InlineNode(entries, self._byte(start), self._byte(self.pos))
            if self._peek() in ("\n", "\r"):
                self._error(self.pos, self.pos + 1, "invalid inline table")
            path, (leaf, leaf_start, leaf_end) = self._parse_dotted_key()
            self._skip_ws()
            if self._peek() != "=":
                self._error(self.pos, self.pos + 1, "expected `=`")
            self._advance(1)
            self._skip_ws()
            node = self._parse_value()
            target = self._inline_descend(entries, path)
            if leaf in target:
                self._error_bytes(leaf_start, leaf_end, "duplicate key")
            target[leaf] = (leaf_start, leaf_end, node)
            self._skip_ws()
            if self._peek() == ",":
                self._advance(1)
                self._skip_ws()
                if self._peek() == "}":
                    # inline tables reject trailing commas
                    # (inline_table.rs: inline-table-keyvals has no
                    # trailing comma; verified against the backend)
                    self._error(self.pos, self.pos + 1, "invalid inline table")
                continue
            if self._peek() == "}":
                self._advance(1)
                return _InlineNode(entries, self._byte(start), self._byte(self.pos))
            self._error(self.pos, self.pos + 1, "expected `,` or `}`")

    @staticmethod
    def _inline_descend(
        entries: OrderedDict[str, tuple[int, int, _Node]],
        path: list[tuple[str, int, int]],
    ) -> OrderedDict[str, tuple[int, int, _Node]]:
        """Dotted keys inside inline tables create nested inline tables."""
        current = entries
        for name, start, end in path:
            entry = current.get(name)
            if entry is None:
                nested: OrderedDict[str, tuple[int, int, _Node]] = OrderedDict()
                current[name] = (start, end, _InlineNode(nested, start, end))
                current = nested
            else:
                node = entry[2]
                if not isinstance(node, _InlineNode):
                    raise _SyntaxError(start, end, "dotted key extends a value")
                current = node.entries
        return current

    # -- datetimes --------------------------------------------------------

    def _try_datetime(self) -> tuple[_InternalItemKind, int] | None:
        """date-time (toml_edit 0.22.27 parser/datetime.rs:25-249)."""
        result = _parse_datetime_core(self.text, self.pos)
        if result is None:
            return None
        value, end = result
        self.pos = end
        return _InternalItemKind.date_time(value), end

    def _byte(self, index: int) -> int:
        return self.bm.byte(index)


def _parse_datetime_core(text: str, pos: int) -> tuple[TomlDateTime, int] | None:
    """The toml_edit 0.22.27 datetime grammar (parser/datetime.rs:25-249),
    transcribed with its exact consumption semantics:

    - ``date-time = (full-date [time-delim partial-time [time-offset]]) /
      partial-time``; the parser consumes a valid prefix and the caller
      handles the remainder (no full-value consumption requirement);
    - a missing or failed time part after a full date backtracks to a
      local date (``opt`` semantics, datetime.rs:29-46);
    - a failed offset after a matched sign is a cut error that fails the
      whole datetime (``cut_err``, datetime.rs:110-128);
    - pure local times never parse an offset (datetime.rs:47-49);
    - the fractional second truncates to nine digits and scales to
      nanoseconds (time_secfrac, datetime.rs:215-249);
    - time-hour 00-23, time-minute 00-59, time-second 00-60, offset
      ±HH:MM within [-24h, +24h] (datetime.rs:173-213).
    """
    index = pos
    length = len(text)

    def digit_run(start: int) -> tuple[str, int]:
        end = start
        while end < length and text[end].isdigit():
            end += 1
        return text[start:end], end

    def full_date(start: int) -> tuple[TomlDate, int] | None:
        year_text, index = digit_run(start)
        if len(year_text) != 4 or index >= length or text[index] != "-":
            return None
        index += 1
        month_text, index = digit_run(index)
        if len(month_text) != 2 or index >= length or text[index] != "-":
            return None
        index += 1
        day_text, index = digit_run(index)
        if len(day_text) != 2:
            return None
        year = int(year_text)
        month = int(month_text)
        day = int(day_text)
        if not (1 <= month <= 12) or not (1 <= day <= _days_in_month(year, month)):
            return None
        return TomlDate(year=year, month=month, day=day), index

    def partial_time(start: int) -> tuple[TomlTime, int] | None:
        hour_text, index = digit_run(start)
        if len(hour_text) != 2 or index >= length or text[index] != ":":
            return None
        index += 1
        minute_text, index = digit_run(index)
        if len(minute_text) != 2 or index >= length or text[index] != ":":
            return None
        index += 1
        second_text, index = digit_run(index)
        if len(second_text) != 2:
            return None
        nanosecond = 0
        if index < length and text[index] == ".":
            index += 1
            fraction, index = digit_run(index)
            if not fraction:
                return None
            nanosecond = int(fraction[:9].ljust(9, "0"))
        hour = int(hour_text)
        minute = int(minute_text)
        second = int(second_text)
        if hour > 23 or minute > 59 or second > 60:
            return None
        return TomlTime(hour=hour, minute=minute, second=second, nanosecond=nanosecond), index

    # full-date branch
    date_result = full_date(pos)
    if date_result is not None:
        date, index = date_result
        if index < length and text[index] in ("T", "t", " "):
            time_result = partial_time(index + 1)
            if time_result is not None:
                time, index = time_result
                offset_minutes: int | None = None
                if index < length and text[index] in ("Z", "z"):
                    index += 1
                    offset_minutes = 0
                elif index < length and text[index] in ("+", "-"):
                    sign = text[index]
                    offset_start = index + 1
                    hours_text, index = digit_run(offset_start)
                    if (
                        len(hours_text) != 2
                        or index >= length
                        or text[index] != ":"
                    ):
                        # cut_err: a matched sign commits the offset branch
                        return None
                    index += 1
                    minutes_text, index = digit_run(index)
                    if len(minutes_text) != 2:
                        return None
                    hours = int(hours_text)
                    minutes = int(minutes_text)
                    if hours > 23 or minutes > 59:
                        return None
                    total = hours * 60 + minutes
                    if sign == "-":
                        total = -total
                    offset_minutes = total
                return TomlDateTime(
                    date=date, time=time, offset_minutes=offset_minutes
                ), index
        # opt semantics: no (valid) time part -> local date
        return TomlDateTime(date=date, time=None, offset_minutes=None), index

    # partial-time branch (no offset is parsed for pure local times)
    time_result = partial_time(pos)
    if time_result is not None:
        time, index = time_result
        return TomlDateTime(date=None, time=time, offset_minutes=None), index
    return None


def _days_in_month(year: int, month: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    if month == 2:
        return 29 if leap else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _scan_dec_int(token: str, index: int) -> int | None:
    """dec-int (numbers.rs:52-88): [sign] (digit1-9 [digits/underscores] | 0)."""
    if index >= len(token):
        return None
    if token[index] in "+-":
        index += 1
    if index >= len(token):
        return None
    if token[index] == "0":
        if index + 1 < len(token) and (token[index + 1].isdigit() or token[index + 1] == "_"):
            return None
        return index + 1
    if not ("1" <= token[index] <= "9"):
        return None
    index += 1
    while index < len(token):
        char = token[index]
        if char.isdigit():
            index += 1
        elif char == "_" and index + 1 < len(token) and token[index + 1].isdigit():
            index += 2
        else:
            break
    return index


def _radix_ok(digits: str, radix: int) -> bool:
    """Radix digits with underscores only between digits (numbers.rs:92-189)."""
    if not digits:
        return False
    if radix == 16:
        ok = lambda c: c.isdigit() or c in "abcdefABCDEF"
    elif radix == 8:
        ok = lambda c: c in "01234567"
    else:
        ok = lambda c: c in "01"
    for i, c in enumerate(digits):
        if c == "_":
            if i == 0 or i == len(digits) - 1 or not ok(digits[i - 1]) or not ok(digits[i + 1]):
                return False
        elif not ok(c):
            return False
    return True


def _float_grammar_ok(token: str) -> bool:
    """float = [sign] dec-int ( exp | frac [exp] ) (numbers.rs:192-284)."""
    if not token:
        return False
    index = 1 if token[0] in "+-" else 0
    int_end = _scan_dec_int(token, index)
    if int_end is None or int_end >= len(token):
        return False
    char = token[int_end]
    if char in "eE":
        return _scan_exp(token, int_end) == len(token)
    if char == ".":
        frac_end = _scan_frac(token, int_end)
        if frac_end is None:
            return False
        if frac_end == len(token):
            return True
        if token[frac_end] in "eE":
            return _scan_exp(token, frac_end) == len(token)
        return False
    return False


def _scan_frac(token: str, index: int) -> int | None:
    """frac = "." zero-prefixable-int (numbers.rs:227-266)."""
    if index >= len(token) or token[index] != ".":
        return None
    index += 1
    if index >= len(token) or not token[index].isdigit():
        return None
    index += 1
    while index < len(token):
        char = token[index]
        if char.isdigit():
            index += 1
        elif char == "_" and index + 1 < len(token) and token[index + 1].isdigit():
            index += 2
        else:
            break
    return index


def _scan_exp(token: str, index: int) -> int | None:
    """exp = (e|E) [sign] zero-prefixable-int (numbers.rs:268-284)."""
    if index >= len(token) or token[index] not in "eE":
        return None
    index += 1
    if index < len(token) and token[index] in "+-":
        index += 1
    if index >= len(token) or not token[index].isdigit():
        return None
    index += 1
    while index < len(token):
        char = token[index]
        if char.isdigit():
            index += 1
        elif char == "_" and index + 1 < len(token) and token[index + 1].isdigit():
            index += 2
        else:
            break
    return index


def _basic_unescaped_ok(char: str) -> bool:
    """basic-unescaped (strings.rs:84-91)."""
    code = ord(char)
    if code < 0x80:
        return (
            code in (0x20, 0x09)
            or code == 0x21
            or 0x23 <= code <= 0x5B
            or 0x5D <= code <= 0x7E
        )
    return True


def _literal_char_ok(char: str) -> bool:
    """literal-char (strings.rs:276-282)."""
    code = ord(char)
    if code < 0x80:
        return code == 0x09 or 0x20 <= code <= 0x26 or 0x28 <= code <= 0x7E
    return True


def _multiline_basic_unescaped_ok(char: str) -> bool:
    """mlb-unescaped (strings.rs:235-242)."""
    return _basic_unescaped_ok(char)


def _multiline_literal_char_ok(char: str) -> bool:
    """mll-char (strings.rs:334-341)."""
    return _literal_char_ok(char)


def _mll_content_ok(char: str) -> bool:
    return char != "'"


# ---------------------------------------------------------------------------
# Entity building (parser.rs:84-338)
# ---------------------------------------------------------------------------


class _EntityBuilder:
    """Builds the flat entity list with snapshot-bound spans."""

    def __init__(
        self,
        authority: DocumentAuthority,
        source_len: int,
        limits: ParseLimits,
    ) -> None:
        self.authority = authority
        self.source_len = source_len
        self.limits = limits
        self.entities: list[_Entity] = []

    def add(self, entity: _Entity) -> int:
        observed = len(self.entities) + 1
        if observed > self.limits.max_node_count:
            raise TomlFormationFailure.resource_limit(
                "node_count", observed, self.limits.max_node_count
            )
        index = len(self.entities)
        self.entities.append(entity)
        return index

    def check_depth(self, depth: int) -> None:
        if depth > self.limits.max_nesting_depth:
            raise TomlFormationFailure.resource_limit(
                "nesting_depth", depth, self.limits.max_nesting_depth
            )

    def build_table(self, table: _TableNode, depth: int, fallback: tuple[int, int]) -> int:
        self.check_depth(depth)
        if (
            table.flavor is TableFlavor.ROOT
            or table.span_start is None
            or table.span_end is None
        ):
            # the root table always spans the whole source
            # (parser.rs:198-199: root => 0..source_len)
            table_range = fallback
        else:
            table_range = (table.span_start, table.span_end)
        item_index = self.add(
            _Entity(
                span=self.authority.span(table_range[0], table_range[1]),
                kind=_ItemEntity(kind=_InternalItemKind.table(table.flavor, [])),
            )
        )
        entries: list[int] = []
        for ordinal, (name, (key_start, key_end, item)) in enumerate(table.items.items()):
            key_index = self.add(
                _Entity(
                    span=self.authority.span(key_start, key_end),
                    kind=_KeyEntity(name=name),
                )
            )
            child_index = self.build_item(item, depth + 1, (key_start, key_end))
            child_span = self.entities[child_index].span
            entry_range = (
                min(key_start, child_span.start_byte),
                max(key_end, child_span.end_byte),
            )
            entry_index = self.add(
                _Entity(
                    span=self.authority.span(entry_range[0], entry_range[1]),
                    kind=_EntryEntity(ordinal=ordinal, key=key_index, item=child_index),
                )
            )
            entries.append(entry_index)
        self.entities[item_index] = _Entity(
            span=self.entities[item_index].span,
            kind=_ItemEntity(kind=_InternalItemKind.table(table.flavor, entries)),
        )
        return item_index

    def build_item(self, item: object, depth: int, fallback: tuple[int, int]) -> int:
        if isinstance(item, _ScalarNode):
            self.check_depth(depth)
            return self.add(
                _Entity(
                    span=self.authority.span(item.start, item.end),
                    kind=_ItemEntity(kind=item.kind),
                )
            )
        if isinstance(item, _ArrayNode):
            return self.build_array(item, depth)
        if isinstance(item, _InlineNode):
            return self.build_inline(item, depth)
        if isinstance(item, _TableNode):
            return self.build_table(item, depth, fallback)
        if isinstance(item, _ArrayOfTablesNode):
            return self.build_array_of_tables(item, depth, fallback)
        raise AssertionError("unreachable item type")

    def build_array(self, array: _ArrayNode, depth: int) -> int:
        self.check_depth(depth)
        item_index = self.add(
            _Entity(
                span=self.authority.span(array.start, array.end),
                kind=_ItemEntity(kind=_InternalItemKind.array([])),
            )
        )
        elements: list[int] = []
        for ordinal, value in enumerate(array.elements):
            child_index = self.build_item(value, depth + 1, (value.start, value.end))
            element_index = self.add(
                _Entity(
                    span=self.authority.span(value.start, value.end),
                    kind=_ElementEntity(ordinal=ordinal, item=child_index),
                )
            )
            elements.append(element_index)
        self.entities[item_index] = _Entity(
            span=self.entities[item_index].span,
            kind=_ItemEntity(kind=_InternalItemKind.array(elements)),
        )
        return item_index

    def build_inline(self, inline: _InlineNode, depth: int) -> int:
        self.check_depth(depth)
        item_index = self.add(
            _Entity(
                span=self.authority.span(inline.start, inline.end),
                kind=_ItemEntity(kind=_InternalItemKind.inline_table([])),
            )
        )
        entries: list[int] = []
        for ordinal, (name, (key_start, key_end, value)) in enumerate(inline.entries.items()):
            key_index = self.add(
                _Entity(
                    span=self.authority.span(key_start, key_end),
                    kind=_KeyEntity(name=name),
                )
            )
            child_index = self.build_item(value, depth + 1, (key_start, key_end))
            child_span = self.entities[child_index].span
            entry_range = (
                min(key_start, child_span.start_byte),
                max(key_end, child_span.end_byte),
            )
            entry_index = self.add(
                _Entity(
                    span=self.authority.span(entry_range[0], entry_range[1]),
                    kind=_EntryEntity(ordinal=ordinal, key=key_index, item=child_index),
                )
            )
            entries.append(entry_index)
        self.entities[item_index] = _Entity(
            span=self.entities[item_index].span,
            kind=_ItemEntity(kind=_InternalItemKind.inline_table(entries)),
        )
        return item_index

    def build_array_of_tables(
        self, array: _ArrayOfTablesNode, depth: int, fallback: tuple[int, int]
    ) -> int:
        self.check_depth(depth)
        if array.elements:
            first = array.elements[0]
            last = array.elements[-1]
            span_start = first.span_start if first.span_start is not None else fallback[0]
            span_end = last.span_end if last.span_end is not None else fallback[1]
        else:
            span_start, span_end = fallback
        item_index = self.add(
            _Entity(
                span=self.authority.span(span_start, span_end),
                kind=_ItemEntity(kind=_InternalItemKind.array_of_tables([])),
            )
        )
        elements: list[int] = []
        for ordinal, table in enumerate(array.elements):
            child_index = self.build_table(table, depth + 1, (span_start, span_end))
            element_index = self.add(
                _Entity(
                    span=self.entities[child_index].span,
                    kind=_ElementEntity(ordinal=ordinal, item=child_index),
                )
            )
            elements.append(element_index)
        self.entities[item_index] = _Entity(
            span=self.entities[item_index].span,
            kind=_ItemEntity(kind=_InternalItemKind.array_of_tables(elements)),
        )
        return item_index


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse(source_bytes: bytes, profile: TomlProfile, limits: ParseLimits) -> Document:
    """Parses one complete immutable TOML 1.0 document snapshot
    (parser.rs:17-63). Raises :class:`TomlFormationFailure` on any fatal
    formation failure; never returns a partial document."""
    if len(source_bytes) > limits.max_source_bytes:
        raise TomlFormationFailure.resource_limit(
            "source_bytes", len(source_bytes), limits.max_source_bytes
        )
    from consema.document.source import SourceError

    try:
        source = SourceSnapshot.from_utf8(source_bytes)
    except SourceError as error:
        from consema.protocol.error_registry import DiagnosticCategory
        from consema.protocol.diagnostic import Severity

        raise TomlFormationFailure(
            [
                TomlDiagnostic(
                    code=error.code,
                    category=DiagnosticCategory.ENCODING,
                    severity=Severity.ERROR,
                    primary=None,
                    arguments={},
                    occurrence=0,
                )
            ]
        ) from None
    authority = DocumentAuthority.fresh()
    text = source.decoded_text()
    assert text is not None
    bm = _ByteMap(text)
    pieces, kinds = tokenize(text, authority, limits.max_token_count)
    preflight_delimiter_nesting(text, pieces, limits.max_nesting_depth)
    structural_index = LosslessStructuralIndex.new(authority.identity, source.len(), pieces)
    try:
        parser = Parser(text, bm)
        root_node = parser.parse()
        builder = _EntityBuilder(authority, source.len(), limits)
        root = builder.build_table(root_node, 0, (0, source.len()))
        entities = builder.entities
    except _SyntaxError as error:
        raise TomlFormationFailure.syntax(
            authority.span(error.start, error.end), error.reason
        ) from None
    return Document(
        authority=authority,
        source=source,
        profile=profile,
        structural_index=structural_index,
        syntax_kinds=kinds,
        entities=entities,
        root=root,
        parse_limits=limits,
    )


def parse_with_profile(source_bytes: bytes, limits: ParseLimits) -> Document:
    """Convenience formation for the single frozen profile ``toml.1.0@1``
    (lib.rs:111-119)."""
    return parse(source_bytes, TomlProfile.TOML10_V1, limits)
