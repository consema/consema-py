"""The YAML grammar engine: presentation events, native composition, scalar
resolution, and the public parse entry point.

Authority (language-neutral first; Rust only for arbitration):

- RFC 0007 (docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md): source
  and encoding s3 (lines 56-72), formation and recovery s4 (73-97), the
  1.2 Core profile s5 (98-139), the 1.1 compatibility profile s6 (140-166),
  graph composition and alias safety s8 (194-213: reserve identity, register
  anchor before descending, most-recent-preceding rule, backward cycles,
  no expansion), and the security rules s13 (400-429: no evaluation, no
  network, no alias expansion).
- The scalar resolution grammar is frozen by crates/consema-yaml/src/
  native.rs:565-716 (resolve_scalar/resolve_explicit/resolve_implicit),
  746-766 (null/bool), 768-801 (integer), 803-846 (float), 848-912
  (sexagesimal), 969-1075 (timestamp), 1077-1111 (binary).
- The event grammar mirrors the saphyr event model through
  crates/consema-yaml/src/backend.rs:24-57 (event kinds) and the native
  composition order crates/consema-yaml/src/native.rs:224-508.
- The profile version directive gate is lib.rs:789-831; fatal limit mapping
  is backend.rs:147-156 and lib.rs:833-858.
- Scalar resolution surface: conformance/vectors/yaml-v1.json cases
  profile.yaml12-scalars and profile.yaml11-scalars (kinds and canonical
  spellings), formation.undefined-alias (yaml.parse.syntax@1),
  stream.empty and stream.multi-document (document_count/alias_count),
  native.arbitrary-duplicate-mapping (entry order and duplicate keys),
  regression.plain-property-characters (plain scalar content).

go/yaml/parser.go is a cross-reference only (never a template).

The parser is intentionally private: Consema owns profile decisions, source
identity, diagnostics, resource limits, native semantics, and graph
composition (lib.rs:1-8).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.core.value import Decimal
from consema.document.limits import ParseLimits
from consema.document.source import (
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.structural import (
    DocumentAuthority,
    NodeRole,
    Span,
)
from consema.yaml.errors import (
    YamlFormationFailure,
    YamlFormationFailureKind,
    resource_limit_failure,
    semantic_failure,
)
from consema.yaml.kinds import (
    YamlProfile,
    YamlScalarKind,
    YamlScalarStyle,
)
from consema.yaml.syntax import tokenize

# -- standard resolved tags (native.rs:17-31) --------------------------------

TAG_NULL = "tag:yaml.org,2002:null"
TAG_BOOL = "tag:yaml.org,2002:bool"
TAG_INT = "tag:yaml.org,2002:int"
TAG_FLOAT = "tag:yaml.org,2002:float"
TAG_STR = "tag:yaml.org,2002:str"
TAG_SEQ = "tag:yaml.org,2002:seq"
TAG_MAP = "tag:yaml.org,2002:map"
TAG_TIMESTAMP = "tag:yaml.org,2002:timestamp"
TAG_BINARY = "tag:yaml.org,2002:binary"
TAG_MERGE = "tag:yaml.org,2002:merge"
TAG_OMAP = "tag:yaml.org,2002:omap"
TAG_PAIRS = "tag:yaml.org,2002:pairs"
TAG_SET = "tag:yaml.org,2002:set"
TAG_VALUE = "tag:yaml.org,2002:value"
TAG_YAML = "tag:yaml.org,2002:yaml"

_STANDARD_COLLECTION_TAGS = frozenset((TAG_SEQ, TAG_MAP, TAG_OMAP, TAG_PAIRS, TAG_SET))
_STANDARD_SCALAR_TAGS = frozenset(
    (
        TAG_NULL,
        TAG_BOOL,
        TAG_INT,
        TAG_FLOAT,
        TAG_STR,
        TAG_TIMESTAMP,
        TAG_BINARY,
        TAG_MERGE,
        TAG_VALUE,
        TAG_YAML,
    )
)


# -- native model records (native.rs:33-94) ----------------------------------


@dataclass(frozen=True, slots=True)
class NativeScalar:
    decoded: str
    canonical: str
    kind: YamlScalarKind
    style: YamlScalarStyle


@dataclass(frozen=True, slots=True)
class NativeSequenceItem:
    identity: int
    node: int
    span: Span
    alias: int | None


@dataclass(frozen=True, slots=True)
class NativeMappingEntry:
    identity: int
    key: int
    value: int
    span: Span
    key_alias: int | None
    value_alias: int | None


@dataclass(frozen=True, slots=True)
class NativeAlias:
    identity: int
    name: str
    target: int
    span: Span


@dataclass(slots=True)
class NativeNode:
    tag: str
    anchor: str | None
    anchor_span: Span | None
    span: Span
    content: object  # NativeScalar | tuple[NativeSequenceItem] | tuple[NativeMappingEntry]


@dataclass(frozen=True, slots=True)
class NativeDocument:
    root: int
    span: Span


@dataclass(slots=True)
class NativeStream:
    nodes: list[NativeNode]
    documents: list[NativeDocument]
    aliases: list[NativeAlias]


# -- backend events (backend.rs:24-57) ---------------------------------------


class _EventKind(enum.Enum):
    STREAM_START = "stream-start"
    STREAM_END = "stream-end"
    DOCUMENT_START = "document-start"
    DOCUMENT_END = "document-end"
    ALIAS = "alias"
    SCALAR = "scalar"
    SEQUENCE_START = "sequence-start"
    SEQUENCE_END = "sequence-end"
    MAPPING_START = "mapping-start"
    MAPPING_END = "mapping-end"


@dataclass(frozen=True, slots=True)
class _Event:
    kind: _EventKind
    start: int
    end: int
    decoded: str | None = None
    style: YamlScalarStyle | None = None
    anchor_id: int | None = None
    tag: str | None = None


class _ParseError(Exception):
    """Syntax failure at one decoded scalar offset."""

    def __init__(self, scalar_offset: int) -> None:
        super().__init__(f"syntax error at scalar offset {scalar_offset}")
        self.scalar_offset = scalar_offset


# ---------------------------------------------------------------------------
# Scalar resolution (native.rs:746-1111)
# ---------------------------------------------------------------------------


def parse_null(value: str) -> str | None:
    return "" if value in ("", "~", "null", "Null", "NULL") else None


def parse_bool(value: str, profile: YamlProfile) -> str | None:
    if value in ("true", "True", "TRUE"):
        return "true"
    if value in ("false", "False", "FALSE"):
        return "false"
    if profile is YamlProfile.YAML11_COMPAT_V1:
        if value in ("y", "Y", "yes", "Yes", "YES", "on", "On", "ON"):
            return "true"
        if value in ("n", "N", "no", "No", "NO", "off", "Off", "OFF"):
            return "false"
    return None


def _split_sign(value: str) -> tuple[int, str] | None:
    if not value:
        return None
    if value[0] == "-":
        return (-1, value[1:])
    if value[0] == "+":
        return (1, value[1:])
    return (1, value)


def _valid_underscored(value: str) -> str | None:
    """1.1 underscore rule: only between alphanumerics (native.rs:930-943)."""
    for index, item in enumerate(value):
        if item == "_" and (
            index == 0
            or index + 1 == len(value)
            or not value[index - 1].isalnum()
            or not value[index + 1].isalnum()
        ):
            return None
    return value


def parse_integer(value: str, profile: YamlProfile) -> str | None:
    sign_unsigned = _split_sign(value)
    if sign_unsigned is None:
        return None
    sign, unsigned = sign_unsigned
    if profile is YamlProfile.YAML11_COMPAT_V1:
        cleaned = _valid_underscored(unsigned)
        if cleaned is None:
            return None
        cleaned = cleaned.replace("_", "")
    elif "_" in unsigned:
        return None
    else:
        cleaned = unsigned
    if cleaned.startswith("0b"):
        base = 2
        digits = cleaned[2:]
    elif cleaned.startswith("0o"):
        if profile is YamlProfile.YAML11_COMPAT_V1:
            return None
        base = 8
        digits = cleaned[2:]
    elif cleaned.startswith("0x"):
        base = 16
        digits = cleaned[2:]
    elif (
        profile is YamlProfile.YAML11_COMPAT_V1
        and len(cleaned) > 1
        and cleaned.startswith("0")
    ):
        base = 8
        digits = cleaned
    elif profile is YamlProfile.YAML11_COMPAT_V1 and ":" in cleaned:
        return _parse_sexagesimal_integer(sign, cleaned)
    else:
        base = 10
        digits = cleaned
    if not digits:
        return None
    magnitude = 0
    for character in digits:
        digit = _digit_value(character, base)
        if digit < 0 or digit >= base:
            return None
        magnitude = magnitude * base + digit
    return str(sign * magnitude)


def _digit_value(character: str, base: int) -> int:
    """One base-N digit value, or -1 for an invalid digit (the Rust
    ``char::to_digit`` semantics)."""
    try:
        return int(character, base)
    except ValueError:
        return -1


def _normalize_decimal_lexeme(value: str) -> str:
    """JSON-number normalization before exact decimal parse
    (native.rs:831-846)."""
    if value.startswith("+"):
        value = value[1:]
    if value.startswith("-."):
        value = "-0" + value[1:]
    elif value.startswith("."):
        value = "0" + value
    exponent = len(value)
    for marker in ("e", "E"):
        index = value.find(marker)
        if index != -1:
            exponent = index
            break
    if value[:exponent].endswith("."):
        value = value[:exponent] + "0" + value[exponent:]
    return value


def _parse_json_number(text: str) -> Decimal | None:
    """Exact finite decimal parse of one normalized JSON-number lexeme
    (Decimal::parse_json_number; the same semantics as
    consema.json.parser.parse_json_decimal)."""
    sign = -1 if text.startswith("-") else 1
    unsigned = text[1:] if text[:1] in ("+", "-") else text
    if not unsigned:
        return None
    mantissa = unsigned
    exponent_text = ""
    for marker in ("e", "E"):
        index = unsigned.find(marker)
        if index != -1:
            mantissa, exponent_text = unsigned.split(marker, 1)
            break
    if not mantissa:
        return None
    if exponent_text and not _is_signed_digits(exponent_text):
        return None
    scale = 0
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
        if (not whole and not fraction) or not whole.isdigit() or not fraction.isdigit():
            return None
        scale = len(fraction)
        digits = whole + fraction
    else:
        if not mantissa.isdigit():
            return None
        digits = mantissa
    coefficient = sign * int(digits) if digits else 0
    exponent = int(exponent_text) - scale if exponent_text else -scale
    return Decimal(coefficient, exponent)


def _is_signed_digits(value: str) -> bool:
    unsigned = value[1:] if value[:1] in ("+", "-") else value
    return bool(unsigned) and unsigned.isdigit()


def _decimal_canonical(value: Decimal) -> str:
    if value.exponent == 0:
        return str(value.coefficient)
    return f"{value.coefficient}e{value.exponent}"


def parse_float(value: str, profile: YamlProfile) -> str | None:
    if value in (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"):
        return ".inf"
    if value in ("-.inf", "-.Inf", "-.INF"):
        return "-.inf"
    if value in (".nan", ".NaN", ".NAN"):
        return ".nan"
    if profile is YamlProfile.YAML11_COMPAT_V1:
        cleaned = _valid_underscored(value)
        if cleaned is None:
            return None
        cleaned = cleaned.replace("_", "")
    elif "_" in value:
        return None
    else:
        cleaned = value
    if profile is YamlProfile.YAML11_COMPAT_V1 and ":" in cleaned:
        return _parse_sexagesimal_float(cleaned)
    if not any(marker in cleaned for marker in (".", "e", "E")):
        return None
    decimal = _parse_json_number(_normalize_decimal_lexeme(cleaned))
    if decimal is None:
        return None
    return _decimal_canonical(decimal)


def _parse_sexagesimal_integer(sign: int, value: str) -> str | None:
    parts = value.split(":")
    first = parts[0]
    if not first or not first.isdigit():
        return None
    magnitude = 0
    for digit in first:
        magnitude = magnitude * 10 + int(digit)
    count = 0
    for part in parts[1:]:
        if not part or len(part) > 2 or not part.isdigit():
            return None
        component = int(part)
        if component > 59:
            return None
        magnitude = magnitude * 60 + component
        count += 1
    if count == 0:
        return None
    return str(sign * magnitude)


def _parse_sexagesimal_float(value: str) -> str | None:
    sign_unsigned = _split_sign(value)
    if sign_unsigned is None:
        return None
    sign, unsigned = sign_unsigned
    parts = unsigned.split(":")
    last = parts[-1]
    if "." not in last:
        return None
    whole, fraction = last.split(".", 1)
    if not fraction or not fraction.isdigit():
        return None
    if len(parts) < 2:
        return None
    magnitude = 0
    for index, part in enumerate(parts[:-1]):
        if not part.isdigit():
            return None
        component = int(part)
        if index > 0 and component > 59:
            return None
        magnitude = magnitude * 60 + component
    if not whole.isdigit():
        return None
    whole_value = int(whole)
    if whole_value > 59:
        return None
    magnitude = magnitude * 60 + whole_value
    coefficient = sign * int(f"{magnitude}{fraction}")
    return _decimal_canonical(Decimal(coefficient, -len(fraction)))


def _valid_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    try:
        year = int(value[:4])
        month = int(value[5:7])
        day = int(value[8:10])
    except ValueError:
        return False
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    max_day = {
        1: 31, 3: 31, 5: 31, 7: 31, 8: 31, 10: 31, 12: 31,
        4: 30, 6: 30, 9: 30, 11: 30,
        2: 29 if leap else 28,
    }.get(month)
    if max_day is None:
        return False
    return day != 0 and day <= max_day


def parse_timestamp(value: str) -> str | None:
    if value.isascii() and len(value) >= 10 and _valid_date(value[:10]):
        if len(value) == 10:
            return value
        return _canonical_timestamp(value)
    return None


def _take_two_digits(value: str) -> tuple[int, str] | None:
    if len(value) < 2 or not value[:2].isdigit():
        return None
    return (int(value[:2]), value[2:])


def _take_one_or_two_digits(value: str) -> tuple[int, str] | None:
    count = 0
    for character in value[:2]:
        if not character.isdigit():
            break
        count += 1
    if count == 0:
        return None
    return (int(value[:count]), value[count:])


def _canonical_zone(value: str) -> str | None:
    if not value:
        return None
    if value[0] not in ("+", "-"):
        return None
    sign = value[0]
    rest = value[1:]
    zone = _take_one_or_two_digits(rest)
    if zone is None:
        return None
    hour, tail = zone
    if tail.startswith(":"):
        tail = tail[1:]
    if not tail:
        minute = 0
    else:
        pair = _take_two_digits(tail)
        if pair is None or pair[1]:
            return None
        minute = pair[0]
    if hour > 23 or minute > 59:
        return None
    return f"{sign}{hour:02d}:{minute:02d}"


def _canonical_timestamp(value: str) -> str | None:
    rest = value[10:]
    rest = rest.lstrip(" \tTt")
    hour_pair = _take_one_or_two_digits(rest)
    if hour_pair is None:
        return None
    hour, tail = hour_pair
    if not tail.startswith(":"):
        return None
    tail = tail[1:]
    minute_pair = _take_two_digits(tail)
    if minute_pair is None:
        return None
    minute, tail = minute_pair
    if not tail.startswith(":"):
        return None
    tail = tail[1:]
    second_pair = _take_two_digits(tail)
    if second_pair is None:
        return None
    second, tail = second_pair
    if hour > 23 or minute > 59 or second > 60:
        return None
    fraction = ""
    if tail.startswith("."):
        after_dot = tail[1:]
        length = 0
        for character in after_dot:
            if not character.isdigit():
                break
            length += 1
        if length == 0:
            return None
        fraction = after_dot[:length]
        tail = after_dot[length:]
    tail = tail.lstrip(" \t")
    if not tail or tail in ("Z", "z"):
        zone = "Z"
    else:
        zone = _canonical_zone(tail)
        if zone is None:
            return None
    fraction_text = f".{fraction}" if fraction else ""
    return (
        f"{value[:10]}T{hour:02d}:{minute:02d}:{second:02d}"
        f"{fraction_text}{zone}"
    )


def _base64_value(value: str) -> int | None:
    if "A" <= value <= "Z":
        return ord(value) - ord("A")
    if "a" <= value <= "z":
        return ord(value) - ord("a") + 26
    if "0" <= value <= "9":
        return ord(value) - ord("0") + 52
    if value == "+":
        return 62
    if value == "/":
        return 63
    return None


def canonical_base64(value: str) -> str | None:
    """Validates and canonicalizes one YAML base64 scalar (native.rs:1077-1111)."""
    cleaned = "".join(character for character in value if not character.isspace())
    padding = 0
    for character in reversed(cleaned):
        if character != "=":
            break
        padding += 1
    if (
        len(cleaned) % 4 != 0
        or padding > 2
        or any(
            not (character.isalnum() or character in ("+", "/", "="))
            for character in cleaned
        )
        or any(character == "=" for character in cleaned[: len(cleaned) - padding])
    ):
        return None
    if padding > 0:
        last_significant = _base64_value(cleaned[len(cleaned) - padding - 1])
        if last_significant is None:
            return None
        unused_mask = 0b0000_0011 if padding == 1 else 0b0000_1111
        if last_significant & unused_mask != 0:
            return None
    return cleaned


# -- tag resolution ----------------------------------------------------------


class _TagTable:
    """Document-scoped %TAG handle table (RFC 0007 s5: tag directives reset
    at each document boundary)."""

    def __init__(self) -> None:
        # The default handles (saphyr resolve_tag: local tags keep the "!"
        # prefix — "!suffix" resolves to "!suffix", never the bare suffix).
        self.handles: dict[str, str] = {"!!": "tag:yaml.org,2002:", "!": "!"}

    def resolve(self, handle: str, suffix: str) -> str:
        prefix = self.handles.get(handle)
        if prefix is None:
            raise _ParseError(0)
        return prefix + suffix


# ---------------------------------------------------------------------------
# The grammar engine
# ---------------------------------------------------------------------------


def _is_separation(character: str) -> bool:
    return character in (" ", "\t", "\r", "\n")


def _is_break(character: str) -> bool:
    return character in ("\r", "\n")


def _is_flow_indicator(character: str) -> bool:
    return character in ("[", "]", "{", "}", ",")


def _break_length(text: str, offset: int) -> int:
    if offset >= len(text):
        return 0
    if text[offset] == "\r":
        if offset + 1 < len(text) and text[offset + 1] == "\n":
            return 2
        return 1
    if text[offset] == "\n":
        return 1
    return 0


class _EventParser:
    """One pass over the BOM-stripped decoded text producing backend events.

    The parser mirrors the saphyr event grammar through backend.rs:24-57 and
    enforces the nesting/event limits (backend.rs:147-156). Span offsets are
    Unicode scalar offsets in the FULL decoded text (scalar_offset_base
    applied at event creation), matching backend.rs:139-142.
    """

    def __init__(
        self,
        text: str,
        profile: YamlProfile,
        max_events: int,
        max_depth: int,
        scalar_offset_base: int,
    ) -> None:
        self.text = text
        self.profile = profile
        self.max_events = max_events
        self.max_depth = max_depth
        self.base = scalar_offset_base
        self.offset = 0
        self.line_start = 0
        self.depth = 0
        self.events: list[_Event] = []
        self.tags = _TagTable()
        self.anchor_ids: dict[str, int] = {}
        self.next_anchor_id = 0
        self.yaml_directive_seen = False

    # -- event emission -----------------------------------------------------

    def _push(self, kind: _EventKind, start: int, end: int, **extra) -> None:
        observed = len(self.events) + 1
        if observed > self.max_events:
            raise resource_limit_failure("syntax-events", observed, self.max_events)
        self.events.append(_Event(kind, start + self.base, end + self.base, **extra))

    def _enter_collection(self) -> None:
        observed = self.depth + 1
        if observed > self.max_depth:
            raise resource_limit_failure("nesting-depth", observed, self.max_depth)
        self.depth = observed

    def _leave_collection(self) -> None:
        self.depth = max(0, self.depth - 1)

    # -- lexical helpers ----------------------------------------------------

    def _peek(self, ahead: int = 0) -> str | None:
        index = self.offset + ahead
        if index >= len(self.text):
            return None
        return self.text[index]

    def _skip_spaces(self) -> None:
        while self._peek() in (" ", "\t"):
            self.offset += 1

    def _line_start_of(self, offset: int) -> int:
        start = offset
        while start > 0 and self.text[start - 1] not in ("\r", "\n"):
            start -= 1
        return start

    def _line_end_of(self, offset: int) -> int:
        end = offset
        while end < len(self.text) and not _is_break(self.text[end]):
            end += 1
        return end + _break_length(self.text, end)

    def _content_end_of(self, offset: int) -> int:
        """Content end of the line containing ``offset``: up to a comment
        (a ``#`` preceded by separation) or the line end."""
        cursor = offset
        previous_was_separation = True
        while cursor < len(self.text) and not _is_break(self.text[cursor]):
            character = self.text[cursor]
            if character == "#" and previous_was_separation:
                break
            if character in (" ", "\t"):
                previous_was_separation = True
            else:
                previous_was_separation = False
            cursor += 1
        return cursor

    def _current_line_indent(self) -> int:
        line_start = self._line_start_of(self.offset)
        indent = 0
        while (
            line_start + indent < len(self.text)
            and self.text[line_start + indent] in (" ", "\t")
        ):
            indent += 1
        return indent

    def _current_column(self) -> int:
        """Character column of the current offset within its line (the
        block-mapping/sequence indentation unit is one character, not one
        space)."""
        return self.offset - self._line_start_of(self.offset)

    def _line_rest_empty(self) -> bool:
        """True when only separation or a comment follows on the current
        line."""
        cursor = self.offset
        while cursor < len(self.text) and self.text[cursor] in (" ", "\t"):
            cursor += 1
        if cursor >= len(self.text):
            return True
        if self.text[cursor] in ("\r", "\n"):
            return True
        if self.text[cursor] == "#" and (
            cursor == self._line_start_of(cursor) or self.text[cursor - 1] in (" ", "\t")
        ):
            return True
        return False

    def _rest_of_line(self) -> str:
        end = self._line_end_of(self.offset)
        return self.text[self.offset : end]

    def _content_line(self, from_offset: int) -> tuple[int, int, int, int, int] | None:
        """Next content line at or after ``from_offset``: (line_start,
        indent, content_start, content_end, line_end). Blank and
        comment-only lines are skipped; a directive at column 0 reports
        indent -1."""
        cursor = from_offset
        while cursor < len(self.text):
            while cursor < len(self.text) and self.text[cursor] in (" ", "\t"):
                cursor += 1
            if cursor >= len(self.text):
                return None
            if self.text[cursor] in ("\r", "\n"):
                cursor += _break_length(self.text, cursor)
                continue
            if self.text[cursor] == "#":
                cursor = self._line_end_of(cursor)
                continue
            line_start = self._line_start_of(cursor)
            content_end = self._content_end_of(cursor)
            line_end = self._line_end_of(cursor)
            if self.text[cursor] == "%" and cursor == line_start:
                return (line_start, -1, cursor, content_end, line_end)
            return (line_start, cursor - line_start, cursor, content_end, line_end)
        return None

    def _at_marker(self, a: str, b: str, c: str) -> bool:
        if self.offset + 3 > len(self.text):
            return False
        if self.text[self.offset : self.offset + 3] != a + b + c:
            return False
        following = self._peek(3)
        return following is None or _is_separation(following)

    def _at_marker_at(self, offset: int, a: str, b: str, c: str) -> bool:
        if offset + 3 > len(self.text):
            return False
        if self.text[offset : offset + 3] != a + b + c:
            return False
        following = offset + 3
        return following >= len(self.text) or _is_separation(self.text[following])

    def _at_column_zero_marker(self, a: str, b: str, c: str) -> bool:
        """A document marker at column 0 (indented markers are plain
        scalars, matching the tokenizer's at_document_indicator)."""
        if self.offset + 3 > len(self.text):
            return False
        if self.text[self.offset : self.offset + 3] != a + b + c:
            return False
        if self._line_start_of(self.offset) != self.offset:
            return False
        following = self._peek(3)
        return following is None or _is_separation(following)

    # -- main loop ----------------------------------------------------------

    def parse(self) -> list[_Event]:
        self._push(_EventKind.STREAM_START, 0, 0)
        while True:
            line = self._content_line(self.offset)
            if line is None:
                break
            line_start, indent, content_start, content_end, line_end = line
            self.offset = content_start
            self.line_start = line_start
            if indent == -1:
                self._parse_directive(line_end)
                continue
            if (
                content_start == line_start
                and self.text[content_start] == "."
                and self._at_marker(".", ".", ".")
            ):
                break
            self._parse_document()
        self._push(_EventKind.STREAM_END, len(self.text), len(self.text))
        return self.events

    def _parse_directive(self, line_end: int) -> None:
        rest = self._rest_of_line()
        if rest.startswith("%YAML"):
            if len(rest) == 5 or rest[5] not in (" ", "\t"):
                raise _ParseError(self.offset)
            if self.yaml_directive_seen:
                raise _ParseError(self.offset)
            self.yaml_directive_seen = True
            parts = rest.split()
            if len(parts) > 2 and not parts[2].startswith("#"):
                raise _ParseError(self.offset)
            self.offset = line_end
            return
        if rest.startswith("%TAG"):
            parts = rest.split()
            if len(parts) != 3 or not parts[1].endswith("!") or len(parts[1]) < 2:
                raise _ParseError(self.offset)
            handle = parts[1]
            if handle in self.tags.handles and handle not in ("!", "!!"):
                raise _ParseError(self.offset)
            self.tags.handles[handle] = parts[2]
            self.offset = line_end
            return
        # Reserved directives are ignored (YAML 1.2.2 s5.2).
        self.offset = line_end

    def _parse_document(self) -> None:
        explicit = False
        if self.text[self.offset] == "-" and self._at_marker("-", "-", "-"):
            explicit = True
            self._push(_EventKind.DOCUMENT_START, self.offset, self.offset + 3)
            self.offset += 3
            self.line_start = self.offset
        else:
            self._push(_EventKind.DOCUMENT_START, self.offset, self.offset)
        self.anchor_ids = {}
        self.next_anchor_id = 0
        if explicit:
            self._skip_spaces()
        line = self._content_line(self.offset)
        if line is None or line[1] == -1:
            # The empty document: a null root (RFC 0007 s8).
            self.tags = _TagTable()
            self._push(
                _EventKind.SCALAR, self.offset, self.offset, decoded="~",
                style=YamlScalarStyle.PLAIN,
            )
            self._push(_EventKind.DOCUMENT_END, self.offset, self.offset)
            return
        line_start, indent, content_start, content_end, line_end = line
        if (
            content_start == line_start
            and self.text[content_start] == "."
            and self._at_marker_at(content_start, ".", ".", ".")
        ):
            self.tags = _TagTable()
            self._push(
                _EventKind.SCALAR, self.offset, self.offset, decoded="~",
                style=YamlScalarStyle.PLAIN,
            )
            self._push(_EventKind.DOCUMENT_END, content_start, content_start + 3)
            self.offset = content_start + 3
            self.line_start = self.offset
            return
        self.offset = content_start
        self.line_start = line_start
        self._parse_block_node("root")
        self._finish_document()

    def _finish_document(self) -> None:
        """Emits the document-end event at the current offset unless a
        ``...`` marker at column 0 follows (then the marker span is used).

        The tag table resets at the document boundary so the %TAG
        directives of the following document start fresh (RFC 0007 s5:
        tag directives reset at each document boundary)."""
        self.tags = _TagTable()
        line = self._content_line(self.offset)
        if line is None:
            self._push(_EventKind.DOCUMENT_END, self.offset, self.offset)
            return
        line_start, indent, content_start, content_end, line_end = line
        if (
            content_start == line_start
            and self.text[content_start] == "."
            and self._at_marker_at(content_start, ".", ".", ".")
        ):
            self._push(_EventKind.DOCUMENT_END, content_start, content_start + 3)
            self.offset = content_start + 3
            self.line_start = self.offset
        else:
            self._push(_EventKind.DOCUMENT_END, self.offset, self.offset)

    # -- block node parsing -------------------------------------------------

    def _starts_structure(self, content_start: int, content_end: int) -> bool:
        """The tokenizer's starts_indented_structure rule (syntax.rs:257-282):
        ``-``/``?`` followed by separation, or ``:`` followed by separation
        anywhere in the line."""
        if self.text[content_start] in ("-", "?") and (
            content_start + 1 >= content_end
            or _is_separation(self.text[content_start + 1])
        ):
            return True
        cursor = content_start
        while cursor < content_end:
            character = self.text[cursor]
            if character == "#":
                return False
            if character == ":" and (
                cursor + 1 >= content_end or _is_separation(self.text[cursor + 1])
            ):
                return True
            cursor += 1
        return False

    def _is_content_line_start(self) -> bool:
        """The current offset is the first non-separation character of its
        line."""
        return self.offset == self._line_content_start()

    def _line_content_start(self) -> int:
        line_start = self._line_start_of(self.offset)
        cursor = line_start
        while cursor < len(self.text) and self.text[cursor] in (" ", "\t"):
            cursor += 1
        return cursor

    def _parse_block_node(
        self,
        position: str,
        anchor_id: int | None = None,
        anchor_name: str | None = None,
        tag: str | None = None,
    ) -> None:
        """Parses one block-context node at the current offset.

        ``position`` is "root", "item" (after ``- ``), "value" (after a key
        indicator), or "key". Emits the node events. Properties may precede
        the node; when the rest of the line is empty the node is a nested
        block node on the following lines. Inherited properties (parsed on an
        earlier line) attach to the nested node, per RFC 0007 s8: an anchor
        labels the first serialization occurrence.
        """
        if anchor_id is None and anchor_name is None and tag is None:
            anchor_id, anchor_name, tag = self._parse_properties()
        c = self._peek()
        if c in ("[", "{"):
            self._parse_flow_collection(c, anchor_id, tag)
            return
        if c in ("|", ">"):
            self._parse_block_scalar(c, anchor_id, tag)
            return
        if c == "*":
            self._parse_alias(anchor_id, anchor_name)
            return
        if c in ("'", '"'):
            self._parse_quoted_scalar(c, anchor_id, tag)
            return
        if self._line_rest_empty():
            self._parse_nested_or_empty(
                anchor_id, anchor_name, tag, root_context=(position == "root")
            )
            return
        if self._is_content_line_start() and c == "-" and (
            self._peek(1) is None or _is_separation(self._peek(1))
        ):
            if position == "item":
                raise _ParseError(self.offset)
            self._parse_block_sequence(self._current_line_indent(), anchor_id, tag)
            return
        if c == "?" and (self._peek(1) is None or _is_separation(self._peek(1))):
            if position == "value":
                raise _ParseError(self.offset)
            self._parse_block_mapping(self._current_line_indent(), anchor_id, tag)
            return
        scan_end = self._scan_plain_block()
        if self._plain_ends_at_key_indicator(scan_end):
            if position == "value":
                raise _ParseError(scan_end)
            if position in ("root", "item"):
                self._parse_block_mapping(self._current_column(), anchor_id, tag)
                return
            # A key position: the plain scalar is the key; the caller
            # handles the value indicator.
            self._parse_plain_scalar(scan_end, anchor_id, tag, rest_ok=True)
            return
        self._parse_plain_scalar(scan_end, anchor_id, tag, rest_ok=False)

    def _parse_properties(self) -> tuple[int | None, str | None, str | None]:
        anchor_id: int | None = None
        anchor_name: str | None = None
        tag: str | None = None
        while True:
            self._skip_spaces()
            c = self._peek()
            if c == "&":
                self.offset += 1
                start = self.offset
                while (
                    self._peek() is not None
                    and not _is_separation(self._peek())
                    and not _is_flow_indicator(self._peek())
                ):
                    self.offset += 1
                if self.offset == start:
                    raise _ParseError(start)
                name = self.text[start : self.offset]
                anchor_id = self.next_anchor_id
                self.next_anchor_id += 1
                anchor_name = name
                self.anchor_ids[name] = anchor_id
            elif c == "!":
                tag = self._parse_tag()
            else:
                break
        return (anchor_id, anchor_name, tag)

    def _parse_tag(self) -> str:
        start = self.offset
        self.offset += 1
        c = self._peek()
        if c == "<":
            self.offset += 1
            verbatim_start = self.offset
            while self._peek() is not None and self._peek() != ">":
                self.offset += 1
            if self._peek() != ">" or self.offset == verbatim_start:
                raise _ParseError(start)
            verbatim = self.text[verbatim_start : self.offset]
            self.offset += 1
            return verbatim
        scanned_start = self.offset
        while (
            self._peek() is not None
            and not _is_separation(self._peek())
            and not _is_flow_indicator(self._peek())
        ):
            self.offset += 1
        scanned = self.text[scanned_start : self.offset]
        if scanned.startswith("!"):
            # ``!!suffix``: the scanned text begins with the second ``!``.
            handle = "!!"
            suffix = scanned[1:]
        else:
            second = scanned.find("!")
            if second == -1:
                # ``!suffix`` local tag.
                handle = "!"
                suffix = scanned
            else:
                # ``!handle!suffix``: the handle is up to and including the
                # first ``!`` in the scanned text.
                handle = "!" + scanned[: second + 1]
                suffix = scanned[second + 1 :]
        return self.tags.resolve(handle, suffix)

    def _scan_plain_block(self) -> int:
        """Scans a plain scalar on the current line without moving the
        parser offset; returns the end offset.

        Plain scalars may contain spaces and tabs mid-line (``name: Rust
        CI`` is one scalar); the scan terminates at a line break, at ``#``
        preceded by separation, or at ``:`` followed by separation."""
        cursor = self.offset
        while cursor < len(self.text):
            character = self.text[cursor]
            if character in ("\r", "\n"):
                return cursor
            if character == ":":
                nxt = cursor + 1
                if nxt >= len(self.text) or _is_separation(self.text[nxt]):
                    return cursor
            if character == "#" and cursor > self.offset and self.text[cursor - 1] in (" ", "\t"):
                return cursor
            cursor += 1
        return cursor

    def _scan_plain_flow(self) -> int:
        """Flow plain scan: same mid-line space rule, plus the flow
        indicators and ``:`` followed by separation or a flow indicator."""
        cursor = self.offset
        while cursor < len(self.text):
            character = self.text[cursor]
            if character in ("\r", "\n") or _is_flow_indicator(character):
                return cursor
            if character == ":":
                nxt = cursor + 1
                if nxt >= len(self.text) or _is_separation(self.text[nxt]) or _is_flow_indicator(self.text[nxt]):
                    return cursor
            if character == "#" and cursor > self.offset and self.text[cursor - 1] in (" ", "\t"):
                return cursor
            cursor += 1
        return cursor

    def _plain_ends_at_key_indicator(self, scan_end: int) -> bool:
        if scan_end >= len(self.text):
            return False
        if self.text[scan_end] != ":":
            return False
        nxt = scan_end + 1
        return nxt >= len(self.text) or _is_separation(self.text[nxt])

    def _parse_plain_scalar(
        self,
        scan_end: int,
        anchor_id: int | None,
        tag: str | None,
        rest_ok: bool = False,
    ) -> None:
        """One plain scalar starting at the current offset; ``scan_end`` is
        the first-line end. Continuation lines follow the syntax.rs rule:
        more indented than the scalar's first line and not starting an
        indented structure."""
        start = self.offset
        if not rest_ok:
            # In block context the rest of the line must be separation then
            # end-of-line/comment; anything else is a syntax error.
            rest = self.text[scan_end : self._line_end_of(scan_end)]
            stripped = rest.lstrip(" \t\r\n")
            if stripped and not stripped.startswith("#"):
                raise _ParseError(scan_end)
        # Continuation lines must be indented more than the line where the
        # plain scalar started (the syntax.rs plain_parent_indent rule).
        first_line_indent = self._current_line_indent()
        decoded = [self.text[start:scan_end]]
        self.offset = scan_end
        if not rest_ok:
            while True:
                line = self._content_line(self.offset)
                if line is None:
                    break
                line_start, indent, content_start, content_end, line_end = line
                if indent <= first_line_indent:
                    break
                if self._starts_structure(content_start, content_end):
                    break
                decoded.append(self.text[content_start:content_end])
                self.offset = line_end
        text = " ".join(decoded)
        self._push(
            _EventKind.SCALAR,
            start,
            scan_end,
            decoded=text,
            style=YamlScalarStyle.PLAIN,
            anchor_id=anchor_id,
            tag=tag,
        )

    def _parse_nested_or_empty(
        self,
        anchor_id: int | None,
        anchor_name: str | None,
        tag: str | None,
        root_context: bool = False,
    ) -> None:
        """The node is null, or a nested block node on the following lines.

        In ``root_context`` (the document root after a ``---`` marker) the
        node may start on the next content line at any indentation."""
        position = self.offset
        line = self._content_line(self.offset)
        if line is None or line[1] == -1:
            self._push(
                _EventKind.SCALAR, position, position, decoded="~",
                style=YamlScalarStyle.PLAIN, anchor_id=anchor_id, tag=tag,
            )
            return
        line_start, indent, content_start, content_end, line_end = line
        if not root_context and indent <= self._current_line_indent():
            self._push(
                _EventKind.SCALAR, position, position, decoded="~",
                style=YamlScalarStyle.PLAIN, anchor_id=anchor_id, tag=tag,
            )
            return
        self.offset = content_start
        self.line_start = line_start
        self._parse_block_node("root", anchor_id, anchor_name, tag)

    # -- block collections --------------------------------------------------

    def _parse_block_mapping(
        self, indent: int, anchor_id: int | None, tag: str | None
    ) -> None:
        """One block mapping whose entries sit at column ``indent``. The
        first entry is already at the current offset."""
        mapping_start = self.offset
        self._enter_collection()
        self._push(
            _EventKind.MAPPING_START, mapping_start, mapping_start,
            anchor_id=anchor_id, tag=tag,
        )
        while True:
            line = self._content_line(self.offset)
            if line is None:
                break
            line_start, line_indent, content_start, content_end, line_end = line
            if line_indent == -1 or line_indent < indent:
                break
            if line_indent > indent:
                raise _ParseError(content_start)
            if (
                content_start == line_start
                and self.text[content_start : content_start + 3] in ("---", "...")
                and (
                    content_start + 3 >= line_end
                    or _is_separation(self.text[content_start + 3])
                )
            ):
                break
            if self.text[content_start] == "?" and (
                content_start + 1 >= content_end
                or _is_separation(self.text[content_start + 1])
            ):
                self.offset = content_start + 1
                self.line_start = line_start
                self._skip_spaces()
                self._parse_explicit_key_value()
                continue
            if self.text[content_start] == ":" and (
                content_start + 1 >= content_end
                or _is_separation(self.text[content_start + 1])
            ):
                raise _ParseError(content_start)
            self.offset = content_start
            self.line_start = line_start
            self._parse_implicit_key_value()
        self._push(_EventKind.MAPPING_END, self.offset, self.offset)
        self._leave_collection()

    def _parse_explicit_key_value(self) -> None:
        """Parses ``? key`` then ``: value`` (same line or following lines)."""
        key_anchor, key_anchor_name, key_tag = self._parse_properties()
        c = self._peek()
        if c is None or _is_separation(c):
            if self._line_rest_empty():
                self._push(
                    _EventKind.SCALAR, self.offset, self.offset, decoded="~",
                    style=YamlScalarStyle.PLAIN, anchor_id=key_anchor, tag=key_tag,
                )
                self._skip_to_value_indicator()
                return
        self._parse_block_node("key", key_anchor, key_anchor_name, key_tag)
        self._skip_to_value_indicator()

    def _skip_to_value_indicator(self) -> None:
        """Skips separation (spaces, comments, blank lines) to a ``:``
        value indicator, then parses the value."""
        while True:
            self._skip_spaces()
            c = self._peek()
            if c == "#":
                self.offset = self._line_end_of(self.offset)
                continue
            if c is None or _is_break(c):
                line = self._content_line(self.offset)
                if line is None or line[1] == -1:
                    raise _ParseError(self.offset)
                self.offset = line[2]
                self.line_start = line[0]
                continue
            if c == ":":
                nxt = self._peek(1)
                if nxt is None or _is_separation(nxt):
                    self.offset += 1
                    self._skip_spaces()
                    self._parse_block_node("value")
                    return
            raise _ParseError(self.offset)

    def _parse_implicit_key_value(self) -> None:
        """Parses ``key: value`` where the key is a single-line node."""
        key_anchor, key_anchor_name, key_tag = self._parse_properties()
        c = self._peek()
        if c in ("[", "{"):
            self._parse_flow_collection(c, key_anchor, key_tag)
        elif c in ("'", '"'):
            self._parse_quoted_scalar(c, key_anchor, key_tag)
        elif c == "*":
            self._parse_alias(key_anchor, key_anchor_name)
        else:
            if self._line_rest_empty():
                raise _ParseError(self.offset)
            scan_end = self._scan_plain_block()
            if not self._plain_ends_at_key_indicator(scan_end):
                raise _ParseError(scan_end)
            self._parse_plain_scalar(scan_end, key_anchor, key_tag, rest_ok=True)
        self._skip_spaces()
        c = self._peek()
        if c != ":":
            raise _ParseError(self.offset)
        nxt = self._peek(1)
        if nxt is not None and not _is_separation(nxt):
            raise _ParseError(self.offset)
        self.offset += 1
        self._skip_spaces()
        if self._line_rest_empty():
            self._parse_nested_or_empty(None, None, None)
        else:
            self._parse_block_node("value")

    # -- block sequences ----------------------------------------------------

    def _parse_block_sequence(
        self, indent: int, anchor_id: int | None, tag: str | None
    ) -> None:
        sequence_start = self.offset
        self._enter_collection()
        self._push(
            _EventKind.SEQUENCE_START, sequence_start, sequence_start,
            anchor_id=anchor_id, tag=tag,
        )
        while True:
            line = self._content_line(self.offset)
            if line is None:
                break
            line_start, line_indent, content_start, content_end, line_end = line
            if line_indent == -1 or line_indent < indent:
                break
            if line_indent > indent:
                raise _ParseError(content_start)
            if (
                content_start == line_start
                and self.text[content_start : content_start + 3] in ("---", "...")
                and (
                    content_start + 3 >= line_end
                    or _is_separation(self.text[content_start + 3])
                )
            ):
                break
            if not (
                self.text[content_start] == "-"
                and (
                    content_start + 1 >= content_end
                    or _is_separation(self.text[content_start + 1])
                )
            ):
                break
            self.offset = content_start + 1
            self.line_start = line_start
            self._skip_spaces()
            if self._line_rest_empty():
                self._parse_nested_or_empty(None, None, None)
            else:
                self._parse_block_node("item")
        self._push(_EventKind.SEQUENCE_END, self.offset, self.offset)
        self._leave_collection()

    # -- flow collections ---------------------------------------------------

    def _parse_flow_collection(
        self, opener: str, anchor_id: int | None, tag: str | None
    ) -> None:
        close = "]" if opener == "[" else "}"
        seq = opener == "["
        start = self.offset
        self.offset += 1
        self._enter_collection()
        self._push(
            _EventKind.SEQUENCE_START if seq else _EventKind.MAPPING_START,
            start, start, anchor_id=anchor_id, tag=tag,
        )
        implicit = False
        while True:
            self._skip_flow_separation()
            c = self._peek()
            if c is None:
                raise _ParseError(self.offset)
            if c == close:
                self.offset += 1
                self._close_flow(seq, implicit)
                return
            if c == ",":
                raise _ParseError(self.offset)
            if c == "?" and self._peek(1) is not None and _is_separation(self._peek(1)):
                if seq and not implicit:
                    self._open_implicit_mapping()
                    implicit = True
                self.offset += 1
                self._skip_flow_separation()
                self._parse_flow_node()
                self._skip_flow_separation()
                if self._peek() != ":":
                    raise _ParseError(self.offset)
                self.offset += 1
                self._skip_flow_separation()
                if self._peek() in (",", close):
                    self._push(
                        _EventKind.SCALAR, self.offset, self.offset, decoded="~",
                        style=YamlScalarStyle.PLAIN,
                    )
                else:
                    self._parse_flow_node()
                if self._after_flow_entry(close, seq, implicit):
                    return
                continue
            self._parse_flow_node()
            self._skip_flow_separation()
            c = self._peek()
            if c == ":" and (self._peek(1) is None or _is_separation(self._peek(1))):
                if seq and not implicit:
                    self._open_implicit_mapping()
                    implicit = True
                self.offset += 1
                self._skip_flow_separation()
                if self._peek() in (",", close):
                    self._push(
                        _EventKind.SCALAR, self.offset, self.offset, decoded="~",
                        style=YamlScalarStyle.PLAIN,
                    )
                else:
                    self._parse_flow_node()
                if self._after_flow_entry(close, seq, implicit):
                    return
                continue
            if opener == "{":
                raise _ParseError(self.offset)
            if implicit:
                raise _ParseError(self.offset)
            if self._after_flow_entry(close, seq, implicit):
                return

    def _open_implicit_mapping(self) -> None:
        self._enter_collection()
        self._push(_EventKind.MAPPING_START, self.offset, self.offset)

    def _close_flow(self, seq: bool, implicit: bool) -> None:
        if implicit:
            self._push(_EventKind.MAPPING_END, self.offset, self.offset)
            self._leave_collection()
        self._push(
            _EventKind.SEQUENCE_END if seq else _EventKind.MAPPING_END,
            self.offset, self.offset,
        )
        self._leave_collection()

    def _after_flow_entry(self, close: str, seq: bool, implicit: bool) -> bool:
        """Consumes the entry separator; returns True when the collection
        closed."""
        self._skip_flow_separation()
        c = self._peek()
        if c == ",":
            self.offset += 1
            return False
        if c == close:
            self.offset += 1
            self._close_flow(seq, implicit)
            return True
        raise _ParseError(self.offset)

    def _skip_flow_separation(self) -> None:
        while True:
            c = self._peek()
            if c in (" ", "\t"):
                self.offset += 1
            elif _is_break(c):
                self.offset += _break_length(self.text, self.offset)
                self.line_start = self.offset
            elif c == "#":
                self.offset = self._line_end_of(self.offset)
            else:
                return

    def _parse_flow_node(self) -> None:
        """One node inside a flow collection."""
        anchor_id, anchor_name, tag = self._parse_properties()
        c = self._peek()
        if c in ("[", "{"):
            self._parse_flow_collection(c, anchor_id, tag)
            return
        if c in ("|", ">"):
            raise _ParseError(self.offset)
        if c == "*":
            self._parse_alias(anchor_id, anchor_name)
            return
        if c in ("'", '"'):
            self._parse_quoted_scalar(c, anchor_id, tag)
            return
        if c is None or _is_separation(c) or _is_flow_indicator(c):
            raise _ParseError(self.offset)
        scan_end = self._scan_plain_flow()
        self._parse_plain_scalar(scan_end, anchor_id, tag, rest_ok=True)

    # -- scalars ------------------------------------------------------------

    def _parse_quoted_scalar(
        self, quote: str, anchor_id: int | None, tag: str | None
    ) -> None:
        start = self.offset
        self.offset += 1
        content: list[str] = []
        while True:
            c = self._peek()
            if c is None:
                raise _ParseError(start)
            if c == quote:
                if quote == "'" and self._peek(1) == "'":
                    content.append("'")
                    self.offset += 2
                    continue
                self.offset += 1
                break
            if quote == '"' and c == "\\":
                content.append(self._parse_double_escape())
                continue
            if _is_break(c):
                # Quoted line folding (YAML 1.2.2 s7.3.3): n consecutive
                # breaks fold to (n-1) newlines plus one space.
                breaks = 0
                while self._peek() is not None and _is_break(self._peek()):
                    self.offset += _break_length(self.text, self.offset)
                    self.line_start = self.offset
                    breaks += 1
                if breaks > 1:
                    content.append("\n" * (breaks - 1))
                content.append(" ")
                continue
            content.append(c)
            self.offset += 1
        decoded = "".join(content)
        self._push(
            _EventKind.SCALAR, start, self.offset,
            decoded=decoded,
            style=YamlScalarStyle.SINGLE_QUOTED
            if quote == "'"
            else YamlScalarStyle.DOUBLE_QUOTED,
            anchor_id=anchor_id, tag=tag,
        )

    def _parse_double_escape(self) -> str:
        """One double-quoted escape sequence; the backslash is at offset."""
        start = self.offset
        self.offset += 1
        c = self._peek()
        if c is None:
            raise _ParseError(start)
        self.offset += 1
        simple = {
            "0": "\0", "a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v",
            "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/",
            "\\": "\\",
            "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
        }
        if c in simple:
            return simple[c]
        if _is_break(c):
            self.offset -= 1
            self.offset += _break_length(self.text, self.offset)
            self.line_start = self.offset
            return ""
        if c == "x":
            return self._parse_hex_escape(2, start)
        if c == "u":
            return self._parse_hex_escape(4, start)
        if c == "U":
            return self._parse_hex_escape(8, start)
        raise _ParseError(start)

    def _parse_hex_escape(self, digits: int, start: int) -> str:
        text = self.text[self.offset : self.offset + digits]
        if len(text) != digits:
            raise _ParseError(start)
        try:
            value = int(text, 16)
        except ValueError:
            raise _ParseError(start) from None
        self.offset += digits
        try:
            return chr(value)
        except ValueError:
            raise _ParseError(start) from None

    def _parse_block_scalar(
        self, header: str, anchor_id: int | None, tag: str | None
    ) -> None:
        start = self.offset
        self.offset += 1
        chomping = "clip"
        indent_indicator: int | None = None
        while True:
            c = self._peek()
            if c in ("+", "-"):
                if chomping != "clip":
                    raise _ParseError(self.offset)
                chomping = "keep" if c == "+" else "strip"
                self.offset += 1
            elif c in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
                if indent_indicator is not None:
                    raise _ParseError(self.offset)
                indent_indicator = int(c)
                self.offset += 1
            else:
                break
        if self._peek() == "#":
            self.offset = self._line_end_of(self.offset)
        self._skip_spaces()
        header_end = self.offset
        if self._peek() is not None and not _is_break(self._peek()):
            raise _ParseError(self.offset)
        break_length = _break_length(self.text, self.offset)
        self.offset += break_length
        self.line_start = self.offset
        parent_indent = self._header_line_column(start)
        lines: list[tuple[int, str, bool]] = []
        while True:
            line = self._block_content_line(self.offset)
            if line is None:
                break
            line_start, indent, text, line_end = line
            has_break = line_end > line_start + len(text)
            blank = not text.strip(" \t")
            if not blank and indent <= parent_indent:
                break
            # The full line text keeps the leading indentation so the
            # content detent can strip exactly ``content_indent`` spaces.
            lines.append((indent, text, has_break))
            self.offset = line_end
            self.line_start = line_end
        if indent_indicator is not None:
            content_indent = parent_indent + indent_indicator
            for line_indent, text, has_break in lines:
                if text.strip(" \t") and line_indent < content_indent:
                    raise _ParseError(self.offset)
        else:
            non_blank = [
                line_indent
                for line_indent, text, has_break in lines
                if text.strip(" \t")
            ]
            content_indent = min(non_blank) if non_blank else None
        decoded = _decode_block_content(
            lines, content_indent, chomping, folded=(header == ">")
        )
        end = self.offset if lines else header_end
        self._push(
            _EventKind.SCALAR, start, end,
            decoded=decoded,
            style=YamlScalarStyle.LITERAL if header == "|" else YamlScalarStyle.FOLDED,
            anchor_id=anchor_id, tag=tag,
        )

    def _header_line_column(self, start: int) -> int:
        """Indentation column (leading spaces) of the line containing
        ``start``."""
        line_start = self._line_start_of(start)
        column = 0
        while line_start + column < len(self.text) and self.text[line_start + column] == " ":
            column += 1
        return column

    def _block_content_line(self, from_offset: int) -> tuple[int, int, str, int] | None:
        """One raw block-scalar content line at or after ``from_offset``:
        (line_start, indent, full line text without the break, line_end).

        Blank and comment-looking lines are preserved verbatim: block
        scalar content has no comment syntax (YAML 1.2.2 s8.1)."""
        if from_offset >= len(self.text):
            return None
        line_start = self._line_start_of(from_offset)
        content_end = line_start
        while content_end < len(self.text) and not _is_break(self.text[content_end]):
            content_end += 1
        line_end = content_end + _break_length(self.text, content_end)
        text = self.text[line_start:content_end]
        indent = 0
        while indent < len(text) and text[indent] == " ":
            indent += 1
        return (line_start, indent, text, line_end)

    def _parse_alias(self, anchor_id: int | None, anchor_name: str | None) -> None:
        start = self.offset
        self.offset += 1
        name_start = self.offset
        while (
            self._peek() is not None
            and not _is_separation(self._peek())
            and not _is_flow_indicator(self._peek())
        ):
            self.offset += 1
        name = self.text[name_start : self.offset]
        if not name:
            raise _ParseError(start)
        resolved = self.anchor_ids.get(name)
        if resolved is None:
            # Undefined aliases fail at parse time (the vector
            # formation.undefined-alias pins yaml.parse.syntax@1).
            raise _ParseError(start)
        self._push(_EventKind.ALIAS, start, self.offset, anchor_id=resolved)


def _decode_block_content(
    lines: list[tuple[int, str, bool]],
    content_indent: int | None,
    chomping: str,
    folded: bool,
) -> str:
    """Decodes literal/folded block content with the frozen chomping rules
    (block scalars; the decoded content is pinned by lib.rs:1235-1261:
    ``a: |`` with content ``  ~`` decodes to ``"~\\n"``)."""
    if not lines:
        return ""
    if content_indent is None:
        return "\n" * len(lines) if chomping == "keep" else ""
    entries: list[str] = []
    for line_indent, text, has_break in lines:
        entries.append(text[content_indent:] if text.strip(" \t") else "")
    last = -1
    for index in range(len(entries) - 1, -1, -1):
        if entries[index]:
            last = index
            break
    if last == -1:
        return "\n" * len(lines) if chomping == "keep" else ""
    if folded:
        # YAML 1.2.2 s8.1 folding: a break between two normal lines folds
        # to a space; a run of n blank lines between content lines yields
        # exactly n newlines; breaks adjacent to more-indented lines are
        # preserved.
        body: list[str] = []
        index = 0
        while index <= last:
            entry = entries[index]
            if not entry:
                index += 1
                continue
            body.append(entry)
            if index < last:
                run = 0
                cursor = index + 1
                while cursor <= last and not entries[cursor]:
                    run += 1
                    cursor += 1
                if run > 0:
                    body.append("\n" * run)
                    index = cursor
                    continue
                prev_more = lines[index][0] > content_indent
                next_more = lines[index + 1][0] > content_indent
                if prev_more or next_more:
                    body.append("\n")
                else:
                    body.append(" ")
            index += 1
        content = "".join(body)
    else:
        content = "\n".join(entries[: last + 1])
    trailing = len(lines) - (last + 1)
    if chomping == "keep":
        extra = trailing + (1 if lines[last][2] else 0)
        return content + "\n" * extra
    if chomping == "strip":
        return content
    return content + ("\n" if lines[last][2] else "")


# ---------------------------------------------------------------------------
# Raw byte resolution (offsets.rs)
# ---------------------------------------------------------------------------


class _RawByteResolver:
    """One-pass decoded-scalar to raw-byte offset resolution (offsets.rs:13-80).

    Event and lexeme boundaries arrive in non-decreasing order, so a single
    forward walk reproduces the exact raw offsets in O(source + lookups).
    """

    def __init__(self, source: SourceSnapshot) -> None:
        self.text = source.decoded_text() or ""
        self.encoding = source.encoding_facts().selected
        self.scalar = 0
        self.raw_byte = 0
        self.utf8_byte = 0

    def resolve(self, scalar: int) -> int:
        self._advance_to(scalar)
        return self.raw_byte

    def decoded_byte_at(self, scalar: int) -> int:
        self._advance_to(scalar)
        return self.utf8_byte

    def _advance_to(self, scalar: int) -> None:
        if scalar < self.scalar:
            self.scalar = 0
            self.raw_byte = 0
            self.utf8_byte = 0
        kind = self.encoding.kind
        for character in self.text[self.utf8_byte :]:
            if self.scalar >= scalar:
                break
            if kind is SourceEncodingKind.UTF8:
                self.raw_byte += len(character.encode("utf-8"))
            elif kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
                self.raw_byte += len(character.encode("utf-16-be")) // 2 * 2
            else:
                raise AssertionError("YAML parse selects only UTF-8 and UTF-16")
            self.utf8_byte += len(character.encode("utf-8"))
            self.scalar += 1


# ---------------------------------------------------------------------------
# Native composition (native.rs:224-508)
# ---------------------------------------------------------------------------


def _compose(
    events: list[_Event],
    source: SourceSnapshot,
    authority: DocumentAuthority,
    profile: YamlProfile,
    anchors: list,
    aliases: list,
    limits: ParseLimits,
) -> NativeStream:
    nodes: list[NativeNode | None] = []
    documents: list[NativeDocument] = []
    composed_aliases: list[NativeAlias] = []
    anchor_nodes: dict[int, int] = {}
    anchor_name_by_id: dict[int, str] = {}
    anchor_occurrences = iter(anchors)
    alias_occurrences = iter(aliases)
    next_association = 0
    raw = _RawByteResolver(source)
    position = 0

    def association_identity() -> int:
        nonlocal next_association
        identity = next_association
        next_association += 1
        return identity

    def reserve_node() -> int:
        observed = len(nodes) + 1
        if observed > limits.max_node_count:
            raise resource_limit_failure("native-nodes", observed, limits.max_node_count)
        nodes.append(None)
        return len(nodes) - 1

    def raw_span(start: int, end: int) -> Span:
        return authority.span(raw.resolve(start), raw.resolve(end))

    def register_anchor(
        anchor_id: int | None, node: int
    ) -> tuple[str | None, Span | None]:
        if anchor_id is None:
            return (None, None)
        try:
            occurrence = next(anchor_occurrences)
        except StopIteration:
            raise semantic_failure("yaml.anchor.name-unavailable@1") from None
        anchor_nodes[anchor_id] = node
        anchor_name_by_id[anchor_id] = occurrence.name
        return (occurrence.name, occurrence.span)

    def node() -> tuple[int, int, Span, bool]:
        """Returns (alias index or node index, node index, span, is_alias)."""
        nonlocal position
        if position >= len(events):
            raise semantic_failure("yaml.native.unexpected-end@1")
        event = events[position]
        position += 1
        if event.kind is _EventKind.ALIAS:
            target = anchor_nodes.get(event.anchor_id) if event.anchor_id is not None else None
            if target is None:
                raise semantic_failure("yaml.anchor.unknown@1")
            try:
                occurrence = next(alias_occurrences)
            except StopIteration:
                raise semantic_failure("yaml.alias.name-unavailable@1") from None
            if anchor_name_by_id.get(event.anchor_id) != occurrence.name:
                raise semantic_failure("yaml.alias.name-mismatch@1")
            identity = association_identity()
            alias_index = len(composed_aliases)
            composed_aliases.append(
                NativeAlias(
                    identity=identity, name=occurrence.name,
                    target=target, span=occurrence.span,
                )
            )
            return (alias_index, target, occurrence.span, True)
        if event.kind is _EventKind.SCALAR:
            index = reserve_node()
            anchor, anchor_span = register_anchor(event.anchor_id, index)
            decoded = _exact_empty_scalar(event, source, raw)
            tag, scalar = _resolve_scalar(decoded, event.style, event.tag, profile)
            span = raw_span(event.start, event.end)
            nodes[index] = NativeNode(
                tag=tag, anchor=anchor, anchor_span=anchor_span, span=span,
                content=scalar,
            )
            return (index, index, span, False)
        if event.kind is _EventKind.SEQUENCE_START:
            index = reserve_node()
            anchor, anchor_span = register_anchor(event.anchor_id, index)
            tag = _resolve_collection_tag(event.tag, TAG_SEQ)
            start = raw_span(event.start, event.end)
            items: list[NativeSequenceItem] = []
            while position < len(events) and events[position].kind is not _EventKind.SEQUENCE_END:
                child_alias, child_node, child_span, is_alias = node()
                items.append(
                    NativeSequenceItem(
                        identity=association_identity(),
                        node=child_node,
                        span=child_span,
                        alias=child_alias if is_alias else None,
                    )
                )
            if position >= len(events) or events[position].kind is not _EventKind.SEQUENCE_END:
                raise semantic_failure("yaml.native.unexpected-end@1")
            end_event = events[position]
            position += 1
            end = raw_span(end_event.start, end_event.end)
            span = authority.span(start.start_byte, end.end_byte)
            nodes[index] = NativeNode(
                tag=tag, anchor=anchor, anchor_span=anchor_span, span=span,
                content=tuple(items),
            )
            return (index, index, span, False)
        if event.kind is _EventKind.MAPPING_START:
            index = reserve_node()
            anchor, anchor_span = register_anchor(event.anchor_id, index)
            tag = _resolve_collection_tag(event.tag, TAG_MAP)
            start = raw_span(event.start, event.end)
            entries: list[NativeMappingEntry] = []
            while position < len(events) and events[position].kind is not _EventKind.MAPPING_END:
                key_alias, key_node, key_span, key_is_alias = node()
                if position >= len(events) or events[position].kind is _EventKind.MAPPING_END:
                    raise semantic_failure("yaml.mapping.missing-value@1")
                value_alias, value_node, value_span, value_is_alias = node()
                entry_span = authority.span(key_span.start_byte, value_span.end_byte)
                entries.append(
                    NativeMappingEntry(
                        identity=association_identity(),
                        key=key_node,
                        value=value_node,
                        span=entry_span,
                        key_alias=key_alias if key_is_alias else None,
                        value_alias=value_alias if value_is_alias else None,
                    )
                )
            if position >= len(events) or events[position].kind is not _EventKind.MAPPING_END:
                raise semantic_failure("yaml.native.unexpected-end@1")
            end_event = events[position]
            position += 1
            end = raw_span(end_event.start, end_event.end)
            span = authority.span(start.start_byte, end.end_byte)
            nodes[index] = NativeNode(
                tag=tag, anchor=anchor, anchor_span=anchor_span, span=span,
                content=tuple(entries),
            )
            return (index, index, span, False)
        raise semantic_failure("yaml.native.unexpected-event@1")

    # Stream composition (native.rs:224-257).
    if not events or events[0].kind is not _EventKind.STREAM_START:
        raise semantic_failure("yaml.native.unexpected-event@1")
    position = 1
    while position < len(events) and events[position].kind is not _EventKind.STREAM_END:
        if events[position].kind is not _EventKind.DOCUMENT_START:
            raise semantic_failure("yaml.native.unexpected-event@1")
        document_start = events[position]
        position += 1
        anchor_nodes = {}
        anchor_name_by_id = {}
        root_alias, root_node, root_span, _ = node()
        if position >= len(events) or events[position].kind is not _EventKind.DOCUMENT_END:
            raise semantic_failure("yaml.native.unexpected-end@1")
        document_end = events[position]
        position += 1
        document_span = authority.span(
            raw.resolve(document_start.start), raw.resolve(document_end.end)
        )
        documents.append(NativeDocument(root=root_node, span=document_span))
    if position >= len(events) or events[position].kind is not _EventKind.STREAM_END:
        raise semantic_failure("yaml.native.trailing-events@1")
    if next(anchor_occurrences, None) is not None or next(alias_occurrences, None) is not None:
        raise semantic_failure("yaml.native.trailing-named-occurrence@1")
    return NativeStream(
        nodes=[node for node in nodes if node is not None],
        documents=documents,
        aliases=composed_aliases,
    )


def _exact_empty_scalar(
    event: _Event, source: SourceSnapshot, raw: _RawByteResolver
) -> str:
    """Rewrites the empty-plain-scalar placeholder back to the empty string
    (native.rs:510-539): a plain decoded ``~`` whose presentation is not
    literally ``~`` is the empty scalar."""
    if event.style is YamlScalarStyle.PLAIN and event.decoded == "~":
        start = raw.decoded_byte_at(event.start)
        end = raw.decoded_byte_at(event.end)
        presentation = (source.decoded_text() or "")[start:end]
        if presentation != "~":
            return ""
    return event.decoded or ""


def _resolve_collection_tag(explicit: str | None, expected: str) -> str:
    """Kind validation for explicit collection tags (native.rs:541-563)."""
    if explicit is None:
        return expected
    if explicit == "!":
        return expected
    valid = (
        explicit in (TAG_SEQ, TAG_OMAP, TAG_PAIRS)
        if expected == TAG_SEQ
        else explicit in (TAG_MAP, TAG_SET)
    )
    if (
        (explicit in _STANDARD_COLLECTION_TAGS and not valid)
        or explicit in _STANDARD_SCALAR_TAGS
    ):
        raise semantic_failure("yaml.tag.kind-mismatch@1")
    return explicit


def _resolve_scalar(
    decoded: str,
    style: YamlScalarStyle | None,
    explicit: str | None,
    profile: YamlProfile,
) -> tuple[str, NativeScalar]:
    """Profile scalar resolution (native.rs:565-716)."""
    public_style = style if style is not None else YamlScalarStyle.PLAIN
    if explicit is not None:
        tag = explicit
        if tag in _STANDARD_COLLECTION_TAGS:
            raise semantic_failure("yaml.tag.kind-mismatch@1")
        if tag == "!" or tag == TAG_STR:
            return (TAG_STR, NativeScalar(decoded, decoded, YamlScalarKind.STRING, public_style))
        if tag == TAG_NULL:
            return _resolve_explicit(decoded, public_style, TAG_NULL, YamlScalarKind.NULL, profile)
        if tag == TAG_BOOL:
            return _resolve_explicit(decoded, public_style, TAG_BOOL, YamlScalarKind.BOOLEAN, profile)
        if tag == TAG_INT:
            return _resolve_explicit(decoded, public_style, TAG_INT, YamlScalarKind.INTEGER, profile)
        if tag == TAG_FLOAT:
            return _resolve_explicit(decoded, public_style, TAG_FLOAT, YamlScalarKind.FLOAT, profile)
        if tag == TAG_TIMESTAMP:
            canonical = parse_timestamp(decoded)
            if canonical is None:
                raise semantic_failure("yaml.scalar.invalid-explicit-tag@1")
            return (tag, NativeScalar(decoded, canonical, YamlScalarKind.TIMESTAMP, public_style))
        if tag == TAG_BINARY:
            canonical = canonical_base64(decoded)
            if canonical is None:
                raise semantic_failure("yaml.scalar.invalid-explicit-tag@1")
            return (tag, NativeScalar(decoded, canonical, YamlScalarKind.BINARY, public_style))
        if tag in (TAG_MERGE, TAG_VALUE, TAG_YAML):
            return (tag, NativeScalar(decoded, decoded, YamlScalarKind.TAGGED, public_style))
        return (tag, NativeScalar(decoded, decoded, YamlScalarKind.CUSTOM, public_style))
    if style is not YamlScalarStyle.PLAIN:
        return (
            TAG_STR,
            NativeScalar(decoded, decoded, YamlScalarKind.STRING, public_style),
        )
    return _resolve_implicit(decoded, public_style, profile)


def _resolve_explicit(
    decoded: str,
    style: YamlScalarStyle,
    tag: str,
    kind: YamlScalarKind,
    profile: YamlProfile,
) -> tuple[str, NativeScalar]:
    canonical: str | None = None
    if kind is YamlScalarKind.NULL:
        canonical = parse_null(decoded)
    elif kind is YamlScalarKind.BOOLEAN:
        canonical = parse_bool(decoded, profile)
    elif kind is YamlScalarKind.INTEGER:
        canonical = parse_integer(decoded, profile)
    elif kind is YamlScalarKind.FLOAT:
        canonical = parse_float(decoded, profile)
    if canonical is None:
        raise semantic_failure("yaml.scalar.invalid-explicit-tag@1")
    return (tag, NativeScalar(decoded, canonical, kind, style))


def _resolve_implicit(
    decoded: str, style: YamlScalarStyle, profile: YamlProfile
) -> tuple[str, NativeScalar]:
    if parse_null(decoded) is not None:
        return (TAG_NULL, NativeScalar(decoded, "", YamlScalarKind.NULL, style))
    bool_value = parse_bool(decoded, profile)
    if bool_value is not None:
        return (TAG_BOOL, NativeScalar(decoded, bool_value, YamlScalarKind.BOOLEAN, style))
    integer = parse_integer(decoded, profile)
    if integer is not None:
        return (TAG_INT, NativeScalar(decoded, integer, YamlScalarKind.INTEGER, style))
    float_value = parse_float(decoded, profile)
    if float_value is not None:
        return (TAG_FLOAT, NativeScalar(decoded, float_value, YamlScalarKind.FLOAT, style))
    if profile is YamlProfile.YAML11_COMPAT_V1:
        timestamp = parse_timestamp(decoded)
        if timestamp is not None:
            return (
                TAG_TIMESTAMP,
                NativeScalar(decoded, timestamp, YamlScalarKind.TIMESTAMP, style),
            )
    return (TAG_STR, NativeScalar(decoded, decoded, YamlScalarKind.STRING, style))


# ---------------------------------------------------------------------------
# Version directive gate (lib.rs:789-831)
# ---------------------------------------------------------------------------


def _validate_version_directives(text: str, profile: YamlProfile) -> None:
    for line_index, line in enumerate(text.split("\n")):
        if line.endswith("\r"):
            line = line[:-1]
        if line.startswith("﻿"):
            line = line[1:]
        if not line.startswith("%YAML"):
            continue
        if len(line) == 5 or line[5] not in (" ", "\t"):
            continue
        version = line[5:].strip(" \t").split("#", 1)[0].strip()
        if version != profile.accepted_version():
            raise YamlFormationFailure(
                YamlFormationFailureKind.PROFILE_VERSION,
                code="yaml.profile.version-directive@1",
                arguments={
                    "selected_profile": profile.id()[0],
                    "declared_version": version,
                    "line": str(line_index + 1),
                },
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse(
    source: bytes,
    profile: YamlProfile,
    limits: ParseLimits,
) -> object:
    """Parses one exact YAML stream using BOM-detected UTF-8/UTF-16 source
    rules (lib.rs:259-320). Returns the immutable YAML Document or raises
    YamlFormationFailure (fatal; no Document exists)."""
    from consema.yaml.document import Document

    if len(source) > limits.max_source_bytes:
        raise resource_limit_failure("source-bytes", len(source), limits.max_source_bytes)
    snapshot = SourceSnapshot.from_raw(
        source,
        EncodingRequest.new(SourceEncoding.utf8()),
        SourceLimits(max_raw_bytes=limits.max_source_bytes),
    )
    text = snapshot.decoded_text()
    if text is None:
        raise YamlFormationFailure(
            YamlFormationFailureKind.SYNTAX, code="yaml.native.invalid-source-span@1"
        )
    backend_text = text[1:] if text.startswith("﻿") else text
    scalar_offset_base = 1 if text.startswith("﻿") else 0
    _validate_version_directives(backend_text, profile)
    try:
        events = _EventParser(
            backend_text,
            profile,
            limits.max_token_count,
            limits.max_nesting_depth,
            scalar_offset_base,
        ).parse()
    except _ParseError as error:
        raise _syntax_failure(snapshot, error.scalar_offset + scalar_offset_base) from None
    authority = DocumentAuthority.fresh()
    tokenized = tokenize(snapshot, authority, limits.max_token_count)
    native = _compose(
        events,
        snapshot,
        authority,
        profile,
        list(tokenized.anchors),
        list(tokenized.aliases),
        limits,
    )
    document_count = sum(1 for event in events if event.kind is _EventKind.DOCUMENT_START)
    return Document(
        authority=authority,
        source=snapshot,
        profile=profile,
        structural_index=tokenized.index,
        syntax_kinds=tokenized.kinds,
        native=native,
        stream_documents=document_count,
        parse_limits=limits,
    )


def _syntax_failure(snapshot: SourceSnapshot, scalar_offset: int) -> YamlFormationFailure:
    failure = YamlFormationFailure(YamlFormationFailureKind.SYNTAX, code="yaml.parse.syntax@1")
    failure._scalar_offset = scalar_offset
    failure._snapshot = snapshot
    return failure


def node_ref(authority: DocumentAuthority, index: int) -> object:
    """One YamlNode handle (native.rs:1159-1161)."""
    return authority.node_ref(index, NodeRole.YAML_NODE)
