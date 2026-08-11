"""The canonical tagged JSON transport `core.portable-value-json@1`.

Authority: RFC 0015 §3.2 and RFC 0016 §4.2; the wire behavior is frozen by
crates/consema-protocol/src/value_transport.rs (the byte arbitration
source); conformance/vectors/protocol-v1.json and protocol-v2.json carry the
shared transport cases. Go (go/protocol/canonical.go) is a cross-reference.

The decoder is a strict JSON parser (no comments, no trailing commas,
duplicate members rejected, canonical string/number forms only) followed by
a byte-exact canonicality re-encode check: any valid-but-non-canonical form
(whitespace, alternate escapes, reordered fields, non-minimal numbers)
fails with the NON_CANONICAL_JSON kind. The encoder emits the exact
canonical bytes: no whitespace, minimal string escapes, integer values as
decimal strings, binary float bits and byte hex lowercased.

The parse tree is represented by immutable tuples:
``("null",)`` / ``("bool", value)`` / ``("str", text)`` /
``("num", raw_token)`` / ``("arr", (items...))`` /
``("obj", ((key, node), ...))`` with fields in source order.
"""

from __future__ import annotations

from consema.core.errors import PVCEError, PVCEErrorKind
from consema.core.pvce import DecodeLimits, EncodeLimits, decode, encode_bounded
from consema.core.value import (
    Decimal,
    DuplicateKeyError,
    EntryMappingBuilder,
    Kind,
    ObjectBuilder,
    PortableValue,
)
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.errors import invalid, resource
from consema.protocol.limits import ProtocolLimits

PortableValueJSONSchema = "core.portable-value-json@1"

# JSON tree node kinds (tuples, see module docstring).
_NULL = ("null",)


# --------------------------------------------------------------------------
# strict JSON parser
# --------------------------------------------------------------------------

class _Parser:
    """Strict JSON parser over one byte document (canonical.go parser)."""

    __slots__ = ("data", "pos", "limits", "nodes")

    def __init__(self, data: bytes, limits: ProtocolLimits):
        self.data = data
        self.pos = 0
        self.limits = limits
        self.nodes = 0

    def _skip_whitespace(self) -> None:
        while self.pos < len(self.data):
            byte = self.data[self.pos]
            if byte in (0x20, 0x09, 0x0A, 0x0D):
                self.pos += 1
            else:
                return

    def value(self, depth: int) -> tuple:
        if depth > self.limits.max_depth * 4 + 8:
            raise resource("$", "nesting depth")
        self.nodes += 1
        if self.nodes > self.limits.max_nodes * 16 + 32:
            raise resource("$", "value nodes")
        self._skip_whitespace()
        if self.pos >= len(self.data):
            raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "expected a value")
        byte = self.data[self.pos]
        if byte == 0x7B:  # '{'
            return self._object(depth)
        if byte == 0x5B:  # '['
            return self._array(depth)
        if byte == 0x22:  # '"'
            text = self._string_token()
            if len(text.encode("utf-8")) > self.limits.max_blob_bytes:
                raise resource("$", "string bytes")
            return ("str", text)
        if byte == 0x74 and self._literal(b"true"):
            return ("bool", True)
        if byte == 0x66 and self._literal(b"false"):
            return ("bool", False)
        if byte == 0x6E and self._literal(b"null"):
            return _NULL
        if byte == 0x2D or 0x30 <= byte <= 0x39:  # '-' or digit
            return ("num", self._number_token())
        raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "unexpected character")

    def _literal(self, word: bytes) -> bool:
        if len(self.data) - self.pos < len(word):
            return False
        if self.data[self.pos : self.pos + len(word)] != word:
            return False
        self.pos += len(word)
        return True

    def _object(self, depth: int) -> tuple:
        self.pos += 1  # '{'
        self._skip_whitespace()
        fields: list[tuple[str, tuple]] = []
        if self.pos < len(self.data) and self.data[self.pos] == 0x7D:  # '}'
            self.pos += 1
            return ("obj", tuple(fields))
        seen: set[str] = set()
        while True:
            self._skip_whitespace()
            key = self._string_token()
            if key in seen:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "duplicate member name"
                )
            seen.add(key)
            if len(key.encode("utf-8")) > self.limits.max_blob_bytes:
                raise resource("$", "string bytes")
            self._skip_whitespace()
            if self.pos >= len(self.data) or self.data[self.pos] != 0x3A:  # ':'
                raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "expected ':'")
            self.pos += 1
            fields.append((key, self.value(depth + 1)))
            self._skip_whitespace()
            if self.pos >= len(self.data):
                raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "unterminated object")
            byte = self.data[self.pos]
            if byte == 0x2C:  # ','
                self.pos += 1
            elif byte == 0x7D:  # '}'
                self.pos += 1
                return ("obj", tuple(fields))
            else:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "expected ',' or '}'"
                )

    def _array(self, depth: int) -> tuple:
        self.pos += 1  # '['
        self._skip_whitespace()
        items: list[tuple] = []
        if self.pos < len(self.data) and self.data[self.pos] == 0x5D:  # ']'
            self.pos += 1
            return ("arr", tuple(items))
        while True:
            items.append(self.value(depth + 1))
            self._skip_whitespace()
            if self.pos >= len(self.data):
                raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "unterminated array")
            byte = self.data[self.pos]
            if byte == 0x2C:  # ','
                self.pos += 1
            elif byte == 0x5D:  # ']'
                self.pos += 1
                return ("arr", tuple(items))
            else:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "expected ',' or ']'"
                )

    def _string_token(self) -> str:
        """Parses one JSON string token (escapes, surrogate pairs) and
        returns the decoded text (canonical.go stringToken/unicodeEscape)."""
        self.pos += 1  # opening quote
        parts: list[str] = []
        while True:
            if self.pos >= len(self.data):
                raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "unterminated string")
            byte = self.data[self.pos]
            if byte == 0x22:  # '"'
                self.pos += 1
                return "".join(parts)
            if byte == 0x5C:  # '\\'
                self.pos += 1
                if self.pos >= len(self.data):
                    raise protocol_error(
                        ProtocolErrorKind.INVALID_JSON, "$", "unterminated escape"
                    )
                escape = self.data[self.pos]
                simple = {
                    0x22: '"',
                    0x5C: "\\",
                    0x2F: "/",
                    0x62: "\b",
                    0x66: "\f",
                    0x6E: "\n",
                    0x72: "\r",
                    0x74: "\t",
                }
                if escape in simple:
                    parts.append(simple[escape])
                    self.pos += 1
                elif escape == 0x75:  # 'u'
                    self.pos += 1
                    parts.append(chr(self._unicode_escape()))
                else:
                    raise protocol_error(
                        ProtocolErrorKind.INVALID_JSON, "$", "invalid escape"
                    )
            elif byte < 0x20:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "raw control character"
                )
            else:
                # Copy one full UTF-8 sequence; partial sequences are invalid
                # JSON text and must not be silently replaced.
                start = self.pos
                self.pos += 1
                while self.pos < len(self.data) and self.data[self.pos] & 0xC0 == 0x80:
                    self.pos += 1
                try:
                    parts.append(self.data[start : self.pos].decode("utf-8"))
                except UnicodeDecodeError:
                    raise protocol_error(
                        ProtocolErrorKind.INVALID_JSON, "$", "invalid UTF-8"
                    ) from None

    def _unicode_escape(self) -> int:
        """Decodes one \\uXXXX escape, combining surrogate pairs."""
        value = self._hex_quad()
        if 0xD800 <= value <= 0xDBFF:
            # High surrogate: require a following \uDC00-\uDFFF.
            if (
                self.pos + 1 < len(self.data)
                and self.data[self.pos] == 0x5C
                and self.data[self.pos + 1] == 0x75
            ):
                self.pos += 2
                low = self._hex_quad()
                if not 0xDC00 <= low <= 0xDFFF:
                    raise protocol_error(
                        ProtocolErrorKind.INVALID_JSON, "$", "invalid surrogate pair"
                    )
                return 0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)
            raise protocol_error(
                ProtocolErrorKind.INVALID_JSON, "$", "lone high surrogate"
            )
        if 0xDC00 <= value <= 0xDFFF:
            raise protocol_error(
                ProtocolErrorKind.INVALID_JSON, "$", "lone low surrogate"
            )
        return value

    def _hex_quad(self) -> int:
        if self.pos + 4 > len(self.data):
            raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "truncated \\u escape")
        value = 0
        for _ in range(4):
            digit = self.data[self.pos]
            value <<= 4
            if 0x30 <= digit <= 0x39:
                value |= digit - 0x30
            elif 0x61 <= digit <= 0x66:
                value |= digit - 0x61 + 10
            elif 0x41 <= digit <= 0x46:
                value |= digit - 0x41 + 10
            else:
                raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "invalid \\u escape")
            self.pos += 1
        return value

    def _number_token(self) -> str:
        """Parses one strict JSON number token and returns its raw text."""
        start = self.pos
        if self.data[self.pos] == 0x2D:  # '-'
            self.pos += 1
        if self.pos >= len(self.data):
            raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "invalid number")
        byte = self.data[self.pos]
        if byte == 0x30:  # '0'
            self.pos += 1
        elif 0x31 <= byte <= 0x39:
            while self.pos < len(self.data) and 0x30 <= self.data[self.pos] <= 0x39:
                self.pos += 1
        else:
            raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "invalid number")
        if self.pos < len(self.data) and self.data[self.pos] == 0x2E:  # '.'
            self.pos += 1
            if self.pos >= len(self.data) or not 0x30 <= self.data[self.pos] <= 0x39:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "invalid number fraction"
                )
            while self.pos < len(self.data) and 0x30 <= self.data[self.pos] <= 0x39:
                self.pos += 1
        if self.pos < len(self.data) and self.data[self.pos] in (0x65, 0x45):  # 'e'/'E'
            self.pos += 1
            if self.pos < len(self.data) and self.data[self.pos] in (0x2B, 0x2D):  # '+'/'-'
                self.pos += 1
            if self.pos >= len(self.data) or not 0x30 <= self.data[self.pos] <= 0x39:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_JSON, "$", "invalid number exponent"
                )
            while self.pos < len(self.data) and 0x30 <= self.data[self.pos] <= 0x39:
                self.pos += 1
        return self.data[start : self.pos].decode("ascii")


def parse_json_document(data: bytes, limits: ProtocolLimits) -> tuple:
    """Strictly parses one complete JSON document (value_transport.rs:26-53)."""
    if len(data) > limits.max_bytes:
        raise resource("$", "transport bytes")
    parser = _Parser(data, limits)
    node = parser.value(0)
    parser._skip_whitespace()
    if parser.pos != len(data):
        raise protocol_error(ProtocolErrorKind.INVALID_JSON, "$", "trailing content")
    return node


# --------------------------------------------------------------------------
# parse-tree helpers
# --------------------------------------------------------------------------

def json_object_exact(node: tuple, expected: list[str] | None, path: str) -> list[tuple]:
    """Returns the member values of an object node in source order,
    validating the declared name set (if any) and canonical order."""
    if node[0] != "obj":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON object")
    names = [key for key, _ in node[1]]
    values = [value for _, value in node[1]]
    if expected is not None:
        for name in names:
            if name not in expected:
                raise protocol_error(
                    ProtocolErrorKind.UNKNOWN_FIELD,
                    f"{path}.{name}",
                    "field is not declared by the fixed schema",
                )
        for name in expected:
            if name not in names:
                raise protocol_error(
                    ProtocolErrorKind.MISSING_FIELD,
                    f"{path}.{name}",
                    "required field is absent",
                )
        if names != expected:
            raise protocol_error(
                ProtocolErrorKind.SCHEMA_MISMATCH,
                path,
                "fields are duplicated or not in canonical order",
            )
    return values


def json_object_fields_exact(node: tuple, expected: list[str], path: str) -> None:
    """Validates the exact member set of a tagged value object."""
    if node[0] != "obj":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON object")
    names = [key for key, _ in node[1]]
    for name in names:
        if name not in expected:
            raise protocol_error(
                ProtocolErrorKind.UNKNOWN_FIELD,
                f"{path}.{name}",
                "field is not declared by the fixed schema",
            )
    for name in expected:
        if name not in names:
            raise protocol_error(
                ProtocolErrorKind.MISSING_FIELD, f"{path}.{name}", "required field is absent"
            )
    if names != expected:
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH,
            path,
            "fields are duplicated or not in canonical order",
        )


def json_string_of(node: tuple, path: str) -> str:
    if node[0] != "str":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON string")
    return node[1]


def json_boolean_of(node: tuple, path: str) -> bool:
    if node[0] != "bool":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON boolean")
    return node[1]


def json_parse_integer(node: tuple, path: str, limits: ProtocolLimits) -> int:
    """Parses a decimal string into an int with the protocol integer limits."""
    if node[0] != "str":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON string")
    max_digits = limits.max_integer_bytes * 3 + 2
    if len(node[1]) > max_digits:
        raise resource(path, "integer decimal digits")
    if not node[1].lstrip("-").isdigit():
        raise invalid(path, "invalid integer")
    value = int(node[1], 10)
    if (value.bit_length() + 7) // 8 > limits.max_integer_bytes:
        raise resource(path, "integer magnitude")
    return value


def json_parse_hex_u32(node: tuple, path: str) -> int:
    """Parses exactly eight hexadecimal digits."""
    text = json_string_of(node, path)
    if len(text) != 8:
        raise invalid(path, "binary32 bits require 8 hexadecimal digits")
    return _parse_hex(text, 8, path, "invalid binary32 bits")


def json_parse_hex_u64(node: tuple, path: str) -> int:
    """Parses exactly sixteen hexadecimal digits."""
    text = json_string_of(node, path)
    if len(text) != 16:
        raise invalid(path, "binary64 bits require 16 hexadecimal digits")
    return _parse_hex(text, 16, path, "invalid binary64 bits")


def _parse_hex(text: str, length: int, path: str, detail: str) -> int:
    value = 0
    for character in text:
        value <<= 4
        if "0" <= character <= "9":
            value |= ord(character) - 0x30
        elif "a" <= character <= "f":
            value |= ord(character) - 0x61 + 10
        elif "A" <= character <= "F":
            value |= ord(character) - 0x41 + 10
        else:
            raise invalid(path, detail)
    return value


def json_parse_u8(node: tuple, path: str, limits: ProtocolLimits) -> int:
    value = json_parse_integer(node, path, limits)
    if not 0 <= value <= 0xFF:
        raise invalid(path, "integer is outside u8")
    return value


def json_parse_i32(node: tuple, path: str, limits: ProtocolLimits) -> int:
    value = json_parse_integer(node, path, limits)
    if not -0x80000000 <= value <= 0x7FFFFFFF:
        raise invalid(path, "integer is outside i32")
    return value


# --------------------------------------------------------------------------
# canonical encoder (re-emits the canonical byte form from a tree node)
# --------------------------------------------------------------------------

class _JsonEncoder:
    """Canonical encoder with protocol resource checks."""

    __slots__ = ("limits", "nodes", "_parts", "_bytes")

    def __init__(self, limits: ProtocolLimits):
        self.limits = limits
        self.nodes = 0
        self._parts: list[str] = []
        self._bytes = 0

    def push(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        if self._bytes + size > self.limits.max_bytes:
            raise resource("$", "transport bytes")
        self._parts.append(text)
        self._bytes += size

    def _node(self, depth: int, path: str) -> None:
        if depth > self.limits.max_depth:
            raise resource(path, "nesting depth")
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise resource(path, "value nodes")

    def _container(self, count: int, path: str) -> None:
        if count > self.limits.max_container_entries:
            raise resource(path, "container entries")

    def quoted(self, text: str, path: str) -> None:
        if len(text.encode("utf-8")) > self.limits.max_blob_bytes:
            raise resource(path, "string bytes")
        self.push('"')
        for character in text:
            simple = {
                '"': '\\"',
                "\\": "\\\\",
                "\b": "\\b",
                "\t": "\\t",
                "\n": "\\n",
                "\f": "\\f",
                "\r": "\\r",
            }
            if character in simple:
                self.push(simple[character])
            elif ord(character) < 0x20:
                self.push(f"\\u{ord(character):04x}")
            else:
                self.push(character)
        self.push('"')

    def integer(self, value: int, path: str) -> None:
        if (value.bit_length() + 7) // 8 > self.limits.max_integer_bytes:
            raise resource(path, "integer magnitude")
        self.quoted(str(value), path)

    def value(self, node: tuple, depth: int, path: str) -> None:
        self._node(depth, path)
        # A tagged value is a JSON object whose first member is "type".
        if node[0] != "obj" or not node[1] or node[1][0][0] != "type":
            raise invalid(path, "unrepresentable value")
        kind = node[1][0][1]
        if kind[0] != "str":
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.type", "expected String")
        kind_text = kind[1]

        def find(name: str):
            for key, value in node[1]:
                if key == name:
                    return value
            return None

        if kind_text == "Null":
            self.push('{"type":"Null"}')
        elif kind_text == "Boolean":
            value_node = find("value")
            if value_node is None or value_node[0] != "bool":
                raise invalid(path, "unrepresentable value")
            self.push('{"type":"Boolean","value":true}' if value_node[1] else '{"type":"Boolean","value":false}')
        elif kind_text == "String":
            text = json_string_of(find("value"), f"{path}.value")
            self.push('{"type":"String","value":')
            self.quoted(text, path)
            self.push("}")
        elif kind_text == "Integer":
            text = json_string_of(find("value"), f"{path}.value")
            value = _parse_int_text(text, f"{path}.value")
            self.push('{"type":"Integer","value":')
            self.integer(value, path)
            self.push("}")
        elif kind_text == "Decimal":
            coefficient = _parse_int_text(
                json_string_of(find("coefficient"), f"{path}.coefficient"), f"{path}.coefficient"
            )
            exponent = _parse_int_text(
                json_string_of(find("exponent"), f"{path}.exponent"), f"{path}.exponent"
            )
            self.push('{"type":"Decimal","coefficient":')
            self.integer(coefficient, path)
            self.push(',"exponent":')
            self.integer(exponent, path)
            self.push("}")
        elif kind_text == "BinaryFloat32":
            bits = json_string_of(find("bits"), f"{path}.bits")
            self.push('{"type":"BinaryFloat32","bits":')
            self.quoted(bits.lower(), path)
            self.push("}")
        elif kind_text == "BinaryFloat64":
            bits = json_string_of(find("bits"), f"{path}.bits")
            self.push('{"type":"BinaryFloat64","bits":')
            self.quoted(bits.lower(), path)
            self.push("}")
        elif kind_text == "Bytes":
            hex_text = json_string_of(find("hex"), f"{path}.hex")
            self.push('{"type":"Bytes","hex":')
            self.quoted(hex_text.lower(), path)
            self.push("}")
        elif kind_text == "Date":
            year = _parse_int_text(json_string_of(find("year"), f"{path}.year"), f"{path}.year")
            month = json_parse_u8(find("month"), f"{path}.month", self.limits)
            day = json_parse_u8(find("day"), f"{path}.day", self.limits)
            self.push('{"type":"Date","year":')
            self.integer(year, path)
            self.push(',"month":')
            self.quoted(str(month), path)
            self.push(',"day":')
            self.quoted(str(day), path)
            self.push("}")
        elif kind_text == "Time":
            hour = json_parse_u8(find("hour"), f"{path}.hour", self.limits)
            minute = json_parse_u8(find("minute"), f"{path}.minute", self.limits)
            second = json_parse_u8(find("second"), f"{path}.second", self.limits)
            fraction = find("fraction")
            if fraction is None:
                raise invalid(f"{path}.fraction", "unrepresentable value")
            self.push('{"type":"Time","hour":')
            self.quoted(str(hour), path)
            self.push(',"minute":')
            self.quoted(str(minute), path)
            self.push(',"second":')
            self.quoted(str(second), path)
            self.push(',"fraction":')
            self.value(fraction, depth + 1, path)
            self.push("}")
        elif kind_text == "LocalDateTime":
            date = find("date")
            time = find("time")
            if date is None or time is None:
                raise invalid(path, "unrepresentable value")
            self.push('{"type":"LocalDateTime","date":')
            self.value(date, depth + 1, path)
            self.push(',"time":')
            self.value(time, depth + 1, path)
            self.push("}")
        elif kind_text == "OffsetDateTime":
            local = find("local")
            if local is None:
                raise invalid(path, "unrepresentable value")
            offset = json_parse_i32(find("offset_seconds"), f"{path}.offset_seconds", self.limits)
            self.push('{"type":"OffsetDateTime","local":')
            self.value(local, depth + 1, path)
            self.push(',"offset_seconds":')
            self.quoted(str(offset), path)
            self.push("}")
        elif kind_text == "Sequence":
            items = find("items")
            if items is None or items[0] != "arr":
                raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.items", "expected JSON array")
            self._container(len(items[1]), path)
            self.push('{"type":"Sequence","items":[')
            for index, item in enumerate(items[1]):
                if index != 0:
                    self.push(",")
                self.value(item, depth + 1, f"{path}.items[{index}]")
            self.push("]}")
        elif kind_text == "Object":
            entries = find("entries")
            if entries is None or entries[0] != "arr":
                raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
            self._container(len(entries[1]), path)
            self.push('{"type":"Object","entries":[')
            for index, item in enumerate(entries[1]):
                if index != 0:
                    self.push(",")
                entry_fields = json_object_exact(item, ["key", "value"], f"{path}.entries[{index}]")
                key = json_string_of(entry_fields[0], f"{path}.entries[{index}].key")
                self.push('{"key":')
                self.quoted(key, f"{path}.entries[{index}].key")
                self.push(',"value":')
                self.value(entry_fields[1], depth + 1, f"{path}.entries[{index}].value")
                self.push("}")
            self.push("]}")
        elif kind_text == "EntryMapping":
            entries = find("entries")
            if entries is None or entries[0] != "arr":
                raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
            self._container(len(entries[1]), path)
            self.push('{"type":"EntryMapping","entries":[')
            for index, item in enumerate(entries[1]):
                if index != 0:
                    self.push(",")
                entry_fields = json_object_exact(item, ["key", "value"], f"{path}.entries[{index}]")
                self.push('{"key":')
                self.value(entry_fields[0], depth + 1, f"{path}.entries[{index}].key")
                self.push(',"value":')
                self.value(entry_fields[1], depth + 1, f"{path}.entries[{index}].value")
                self.push("}")
            self.push("]}")
        else:
            raise invalid(f"{path}.type", "unknown value type")

    def result(self) -> bytes:
        return "".join(self._parts).encode("utf-8")


def _member_of(fields: tuple, name: str) -> tuple | None:
    for key, value in fields:
        if key == name:
            return value
    return None


def _parse_int_text(text: str, path: str) -> int:
    if not text.lstrip("-").isdigit():
        raise invalid(path, "invalid integer")
    return int(text, 10)


def _encode_transport(value_node: tuple, limits: ProtocolLimits) -> bytes:
    """Writes the full canonical envelope around one tagged value tree."""
    encoder = _JsonEncoder(limits)
    encoder.push('{"schema":"' + PortableValueJSONSchema + '","value":')
    encoder.value(value_node, 0, "$.value")
    encoder.push("}")
    return encoder.result()


# --------------------------------------------------------------------------
# value <-> tree conversion
# --------------------------------------------------------------------------

def value_to_node(value: PortableValue) -> tuple:
    """Converts a value into its tagged tree form (the canonical form)."""
    kind = value.kind
    if kind is Kind.NULL:
        return ("obj", (("type", ("str", "Null")),))
    if kind is Kind.BOOLEAN:
        return ("obj", (("type", ("str", "Boolean")), ("value", ("bool", value.as_boolean()))))
    if kind is Kind.STRING:
        return ("obj", (("type", ("str", "String")), ("value", ("str", value.as_string()))))
    if kind is Kind.INTEGER:
        return ("obj", (("type", ("str", "Integer")), ("value", ("str", str(value.as_integer())))))
    if kind is Kind.DECIMAL:
        decimal_value = value.as_decimal()
        return (
            "obj",
            (
                ("type", ("str", "Decimal")),
                ("coefficient", ("str", str(decimal_value.coefficient))),
                ("exponent", ("str", str(decimal_value.exponent))),
            ),
        )
    if kind is Kind.BINARY_FLOAT32:
        return (
            "obj",
            (("type", ("str", "BinaryFloat32")), ("bits", ("str", f"{value.as_binary_float32():08x}"))),
        )
    if kind is Kind.BINARY_FLOAT64:
        return (
            "obj",
            (("type", ("str", "BinaryFloat64")), ("bits", ("str", f"{value.as_binary_float64():016x}"))),
        )
    if kind is Kind.BYTES:
        return ("obj", (("type", ("str", "Bytes")), ("hex", ("str", value.as_bytes().hex()))))
    if kind is Kind.DATE:
        year, month, day = value.as_date()
        return (
            "obj",
            (
                ("type", ("str", "Date")),
                ("year", ("str", str(year))),
                ("month", ("str", str(month))),
                ("day", ("str", str(day))),
            ),
        )
    if kind is Kind.TIME:
        hour, minute, second, fraction = value.as_time()
        return (
            "obj",
            (
                ("type", ("str", "Time")),
                ("hour", ("str", str(hour))),
                ("minute", ("str", str(minute))),
                ("second", ("str", str(second))),
                ("fraction", value_to_node(PortableValue.decimal(fraction))),
            ),
        )
    if kind is Kind.LOCAL_DATE_TIME:
        date, time = value.as_local_date_time()
        return (
            "obj",
            (
                ("type", ("str", "LocalDateTime")),
                ("date", value_to_node(date)),
                ("time", value_to_node(time)),
            ),
        )
    if kind is Kind.OFFSET_DATE_TIME:
        local, offset_seconds = value.as_offset_date_time()
        return (
            "obj",
            (
                ("type", ("str", "OffsetDateTime")),
                ("local", value_to_node(local)),
                ("offset_seconds", ("str", str(offset_seconds))),
            ),
        )
    if kind is Kind.SEQUENCE:
        return (
            "obj",
            (
                ("type", ("str", "Sequence")),
                ("items", ("arr", tuple(value_to_node(item) for item in value.as_sequence()))),
            ),
        )
    if kind is Kind.OBJECT:
        entries = tuple(
            (
                "obj",
                (
                    ("key", ("str", key)),
                    ("value", value_to_node(entry_value)),
                ),
            )
            for key, entry_value in value.as_object()
        )
        return ("obj", (("type", ("str", "Object")), ("entries", ("arr", entries))))
    if kind is Kind.ENTRY_MAPPING:
        entries = tuple(
            (
                "obj",
                (
                    ("key", value_to_node(key)),
                    ("value", value_to_node(entry_value)),
                ),
            )
            for key, entry_value in value.as_entry_mapping()
        )
        return ("obj", (("type", ("str", "EntryMapping")), ("entries", ("arr", entries))))
    raise AssertionError(f"unreachable kind {kind!r}")


class _DecodeState:
    """Tracks the resource counts of a value-tree decode."""

    __slots__ = ("limits", "nodes")

    def __init__(self, limits: ProtocolLimits):
        self.limits = limits
        self.nodes = 0

    def node(self, depth: int, path: str) -> None:
        if depth > self.limits.max_depth:
            raise resource(path, "nesting depth")
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise resource(path, "value nodes")

    def container(self, count: int, path: str) -> None:
        if count > self.limits.max_container_entries:
            raise resource(path, "container entries")


def node_to_value(node: tuple, depth: int, path: str, state: _DecodeState) -> PortableValue:
    """Decodes a tagged tree node into a value, covering all fifteen kinds."""
    state.node(depth, path)
    if node[0] != "obj":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected JSON object")
    if not node[1]:
        raise protocol_error(ProtocolErrorKind.MISSING_FIELD, f"{path}.type", "missing value type")
    if node[1][0][0] != "type":
        raise protocol_error(ProtocolErrorKind.SCHEMA_MISMATCH, path, "type must be the first field")
    kind = json_string_of(node[1][0][1], f"{path}.type")
    if kind == "Null":
        json_object_fields_exact(node, ["type"], path)
        return PortableValue.null()
    if kind == "Boolean":
        json_object_fields_exact(node, ["type", "value"], path)
        return PortableValue.boolean(json_boolean_of(node[1][1][1], f"{path}.value"))
    if kind == "Integer":
        json_object_fields_exact(node, ["type", "value"], path)
        return PortableValue.integer(json_parse_integer(node[1][1][1], f"{path}.value", state.limits))
    if kind == "Decimal":
        json_object_fields_exact(node, ["type", "coefficient", "exponent"], path)
        coefficient = json_parse_integer(node[1][1][1], f"{path}.coefficient", state.limits)
        exponent = json_parse_integer(node[1][2][1], f"{path}.exponent", state.limits)
        return PortableValue.decimal(Decimal(coefficient, exponent))
    if kind == "BinaryFloat32":
        json_object_fields_exact(node, ["type", "bits"], path)
        return PortableValue.binary_float32(json_parse_hex_u32(node[1][1][1], f"{path}.bits"))
    if kind == "BinaryFloat64":
        json_object_fields_exact(node, ["type", "bits"], path)
        return PortableValue.binary_float64(json_parse_hex_u64(node[1][1][1], f"{path}.bits"))
    if kind == "Bytes":
        json_object_fields_exact(node, ["type", "hex"], path)
        hex_text = json_string_of(node[1][1][1], f"{path}.hex")
        if len(hex_text) % 2 != 0:
            raise invalid(f"{path}.hex", "byte hex length must be even")
        if len(hex_text) // 2 > state.limits.max_blob_bytes:
            raise resource(f"{path}.hex", "bytes")
        blob = _parse_hex_bytes(hex_text, f"{path}.hex")
        return PortableValue.bytes_value(blob)
    if kind == "Date":
        json_object_fields_exact(node, ["type", "year", "month", "day"], path)
        year = json_parse_integer(node[1][1][1], f"{path}.year", state.limits)
        month = json_parse_u8(node[1][2][1], f"{path}.month", state.limits)
        day = json_parse_u8(node[1][3][1], f"{path}.day", state.limits)
        try:
            return PortableValue.date(year, month, day)
        except PVCEError:
            raise invalid(path, "invalid date") from None
    if kind == "Time":
        json_object_fields_exact(node, ["type", "hour", "minute", "second", "fraction"], path)
        hour = json_parse_u8(node[1][1][1], f"{path}.hour", state.limits)
        minute = json_parse_u8(node[1][2][1], f"{path}.minute", state.limits)
        second = json_parse_u8(node[1][3][1], f"{path}.second", state.limits)
        fraction_value = node_to_value(node[1][4][1], depth + 1, f"{path}.fraction", state)
        if fraction_value.kind is not Kind.DECIMAL:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, f"{path}.fraction", "expected Decimal"
            )
        try:
            return PortableValue.time(hour, minute, second, fraction_value.as_decimal())
        except PVCEError:
            raise invalid(path, "invalid time") from None
    if kind == "LocalDateTime":
        json_object_fields_exact(node, ["type", "date", "time"], path)
        date_value = node_to_value(node[1][1][1], depth + 1, f"{path}.date", state)
        if date_value.kind is not Kind.DATE:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.date", "expected Date")
        time_value = node_to_value(node[1][2][1], depth + 1, f"{path}.time", state)
        if time_value.kind is not Kind.TIME:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.time", "expected Time")
        return PortableValue.local_date_time(date_value, time_value)
    if kind == "OffsetDateTime":
        json_object_fields_exact(node, ["type", "local", "offset_seconds"], path)
        local_value = node_to_value(node[1][1][1], depth + 1, f"{path}.local", state)
        if local_value.kind is not Kind.LOCAL_DATE_TIME:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, f"{path}.local", "expected LocalDateTime"
            )
        offset = json_parse_i32(node[1][2][1], f"{path}.offset_seconds", state.limits)
        try:
            return PortableValue.offset_date_time(local_value, offset)
        except PVCEError:
            raise invalid(path, "invalid offset date-time") from None
    if kind == "String":
        json_object_fields_exact(node, ["type", "value"], path)
        return PortableValue.string(json_string_of(node[1][1][1], f"{path}.value"))
    if kind == "Sequence":
        json_object_fields_exact(node, ["type", "items"], path)
        items_node = node[1][1][1]
        if items_node[0] != "arr":
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.items", "expected JSON array")
        state.container(len(items_node[1]), path)
        items = [
            node_to_value(item, depth + 1, f"{path}.items[{index}]", state)
            for index, item in enumerate(items_node[1])
        ]
        return PortableValue.sequence(items)
    if kind == "Object":
        json_object_fields_exact(node, ["type", "entries"], path)
        entries_node = node[1][1][1]
        if entries_node[0] != "arr":
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
        state.container(len(entries_node[1]), path)
        builder = ObjectBuilder()
        for index, item in enumerate(entries_node[1]):
            entry_path = f"{path}.entries[{index}]"
            entry_fields = json_object_exact(item, ["key", "value"], entry_path)
            key = json_string_of(entry_fields[0], f"{entry_path}.key")
            entry_value = node_to_value(entry_fields[1], depth + 1, f"{entry_path}.value", state)
            try:
                builder.insert(key, entry_value)
            except DuplicateKeyError:
                raise invalid(entry_path, "duplicate object key") from None
        return builder.build()
    if kind == "EntryMapping":
        json_object_fields_exact(node, ["type", "entries"], path)
        entries_node = node[1][1][1]
        if entries_node[0] != "arr":
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
        state.container(len(entries_node[1]), path)
        builder = EntryMappingBuilder()
        for index, item in enumerate(entries_node[1]):
            entry_path = f"{path}.entries[{index}]"
            entry_fields = json_object_exact(item, ["key", "value"], entry_path)
            key = node_to_value(entry_fields[0], depth + 1, f"{entry_path}.key", state)
            entry_value = node_to_value(entry_fields[1], depth + 1, f"{entry_path}.value", state)
            builder.push(key, entry_value)
        return builder.build()
    raise invalid(f"{path}.type", "unknown value type")


def _parse_hex_bytes(text: str, path: str) -> bytes:
    if len(text) % 2 != 0:
        raise invalid(path, "byte hex length must be even")
    output = bytearray()
    for index in range(0, len(text), 2):
        high = _hex_digit(text[index])
        low = _hex_digit(text[index + 1])
        if high < 0 or low < 0:
            raise invalid(path, "invalid byte hex")
        output.append((high << 4) | low)
    return bytes(output)


def _hex_digit(character: str) -> int:
    if "0" <= character <= "9":
        return ord(character) - 0x30
    if "a" <= character <= "f":
        return ord(character) - 0x61 + 10
    if "A" <= character <= "F":
        return ord(character) - 0x41 + 10
    return -1


# --------------------------------------------------------------------------
# public transport API
# --------------------------------------------------------------------------

def encode_json(value: PortableValue, limits: ProtocolLimits) -> bytes:
    """Encodes a value as canonical `core.portable-value-json@1` bytes."""
    return _encode_transport(value_to_node(value), limits)


def decode_json(data: bytes, limits: ProtocolLimits) -> PortableValue:
    """Strictly decodes canonical `core.portable-value-json@1` bytes.

    The record decode runs before the canonicality re-encode check,
    matching the arbitration ordering (a resource-limit or field error is
    reported before a non-canonical form).
    """
    node = parse_json_document(data, limits)
    fields = json_object_exact(node, ["schema", "value"], "$")
    schema = json_string_of(fields[0], "$.schema")
    if schema != PortableValueJSONSchema:
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, "$.schema", "unexpected transport schema"
        )
    state = _DecodeState(limits)
    value = node_to_value(fields[1], 0, "$.value", state)
    ensure_canonical(node, data, limits)
    return value


def ensure_canonical(node: tuple, input_bytes: bytes, limits: ProtocolLimits) -> None:
    """Re-encodes the parsed document's value and requires byte equality.

    Any valid-but-non-canonical form (whitespace, alternate escapes,
    reordered fields, non-minimal numbers) therefore differs and fails
    with the NON_CANONICAL_JSON kind.
    """
    value_node = None
    for key, value in node[1]:
        if key == "value":
            value_node = value
    if value_node is None:
        # Malformed envelope; the record decode reports the missing field.
        return
    encoded = _encode_transport(value_node, limits)
    if encoded != input_bytes:
        raise protocol_error(
            ProtocolErrorKind.NON_CANONICAL_JSON,
            "$",
            "input is valid but not the canonical JSON byte form",
        )


# --------------------------------------------------------------------------
# PVCE transport under protocol limits
# --------------------------------------------------------------------------

def encode_pvce(value: PortableValue, limits: ProtocolLimits) -> bytes:
    """Encodes a value as canonical PVCE/1 under protocol limits."""
    try:
        return encode_bounded(
            value,
            EncodeLimits(
                max_bytes=limits.max_bytes,
                max_depth=limits.max_depth,
                max_nodes=limits.max_nodes,
                max_container_entries=limits.max_container_entries,
                max_integer_bytes=limits.max_integer_bytes,
                max_blob_bytes=limits.max_blob_bytes,
            ),
        )
    except PVCEError as error:
        raise _map_pvce_error(error) from None


def decode_pvce(data: bytes, limits: ProtocolLimits) -> PortableValue:
    """Strictly decodes canonical PVCE/1 under protocol limits."""
    try:
        return decode(
            data,
            DecodeLimits(
                max_bytes=limits.max_bytes,
                max_depth=limits.max_depth,
                max_nodes=limits.max_nodes,
                max_container_entries=limits.max_container_entries,
                max_integer_bytes=limits.max_integer_bytes,
                max_blob_bytes=limits.max_blob_bytes,
            ),
        )
    except PVCEError as error:
        raise _map_pvce_error(error) from None


def _map_pvce_error(error: PVCEError) -> ProtocolError:
    if error.kind is PVCEErrorKind.RESOURCE_LIMIT:
        return resource("$", str(error))
    return protocol_error(ProtocolErrorKind.INVALID_PVCE, "$", str(error))


# --------------------------------------------------------------------------
# tagged-tree builders/readers shared with the CLI record tree codec
# --------------------------------------------------------------------------

def tagged_string(text: str) -> tuple:
    return ("obj", (("type", ("str", "String")), ("value", ("str", text))))


def tagged_integer(value: int) -> tuple:
    return ("obj", (("type", ("str", "Integer")), ("value", ("str", str(value)))))


def tagged_boolean(value: bool) -> tuple:
    return ("obj", (("type", ("str", "Boolean")), ("value", ("bool", value))))


def tagged_null() -> tuple:
    return ("obj", (("type", ("str", "Null")),))


def tagged_array(items: list[tuple]) -> tuple:
    return ("obj", (("type", ("str", "Sequence")), ("items", ("arr", tuple(items)))))


def tagged_object(fields: list[tuple[str, tuple]]) -> tuple:
    entries = tuple(
        ("obj", (("key", ("str", key)), ("value", value))) for key, value in fields
    )
    return ("obj", (("type", ("str", "Object")), ("entries", ("arr", entries))))


def tagged_bytes(blob: bytes) -> tuple:
    return ("obj", (("type", ("str", "Bytes")), ("hex", ("str", blob.hex()))))


def tagged_kind_fields(node: tuple, path: str) -> tuple[str, tuple]:
    """Validates a tagged value object and returns the kind plus the
    remaining members (cli_json.go taggedKindFields)."""
    if node[0] != "obj":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Object")
    if not node[1] or node[1][0][0] != "type":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, path, "type must be the first field"
        )
    kind = json_string_of(node[1][0][1], f"{path}.type")
    return kind, node[1][1:]


def member_of(fields: tuple, name: str) -> tuple | None:
    for key, value in fields:
        if key == name:
            return value
    return None


def json_record_fields(node: tuple, expected: list[str], path: str) -> list[tuple]:
    """Validates a tagged Object value whose members are a fixed record
    (or an arbitrary mapping when expected is None) and returns the member
    values in member order (cli_json.go jsonRecordFields)."""
    kind, fields = tagged_kind_fields(node, path)
    if kind != "Object":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Object")
    entries = member_of(fields, "entries")
    if entries is None or entries[0] != "arr":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
    names: list[str] = []
    values: list[tuple] = []
    for item in entries[1]:
        if item[0] != "obj":
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON object entry"
            )
        entry_fields = json_object_exact(item, ["key", "value"], f"{path}.entries")
        key = json_string_of(entry_fields[0], f"{path}.entries.key")
        names.append(key)
        values.append(entry_fields[1])
    if expected is not None:
        for name in names:
            if name not in expected:
                raise protocol_error(
                    ProtocolErrorKind.UNKNOWN_FIELD,
                    f"{path}.{name}",
                    "field is not declared by the fixed schema",
                )
        for name in expected:
            if name not in names:
                raise protocol_error(
                    ProtocolErrorKind.MISSING_FIELD,
                    f"{path}.{name}",
                    "required field is absent",
                )
        if names != expected:
            raise protocol_error(
                ProtocolErrorKind.SCHEMA_MISMATCH,
                path,
                "fields are duplicated or not in canonical order",
            )
    return values


def json_tagged_string(node: tuple, path: str) -> str:
    kind, fields = tagged_kind_fields(node, path)
    if kind != "String":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected String")
    value = member_of(fields, "value")
    if value is None:
        raise protocol_error(ProtocolErrorKind.MISSING_FIELD, f"{path}.value", "required field is absent")
    return json_string_of(value, f"{path}.value")


def json_tagged_boolean(node: tuple, path: str) -> bool:
    kind, fields = tagged_kind_fields(node, path)
    if kind != "Boolean":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Boolean")
    value = member_of(fields, "value")
    if value is None:
        raise protocol_error(ProtocolErrorKind.MISSING_FIELD, f"{path}.value", "required field is absent")
    return json_boolean_of(value, f"{path}.value")


def json_is_tagged_null(node: tuple) -> bool:
    return node[0] == "obj" and len(node[1]) == 1 and node[1][0] == ("type", ("str", "Null"))


def json_tagged_uint32(node: tuple, path: str) -> int:
    value = json_tagged_uint64(node, path)
    if value > 0xFFFFFFFF:
        raise invalid(path, "expected an unsigned 32-bit Integer")
    return value


def json_tagged_uint64(node: tuple, path: str) -> int:
    kind, fields = tagged_kind_fields(node, path)
    if kind != "Integer":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Integer")
    value = member_of(fields, "value")
    if value is None:
        raise protocol_error(ProtocolErrorKind.MISSING_FIELD, f"{path}.value", "required field is absent")
    text = json_string_of(value, f"{path}.value")
    if not text or not text.isdigit():
        raise invalid(path, "expected an unsigned 64-bit Integer")
    number = int(text, 10)
    if number > 0xFFFFFFFFFFFFFFFF:
        raise invalid(path, "expected an unsigned 64-bit Integer")
    return number


def json_tagged_array(node: tuple, path: str) -> tuple:
    kind, fields = tagged_kind_fields(node, path)
    if kind != "Sequence":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Sequence")
    items = member_of(fields, "items")
    if items is None or items[0] != "arr":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.items", "expected JSON array")
    return items[1]


def json_tagged_bytes(node: tuple, path: str) -> bytes:
    kind, fields = tagged_kind_fields(node, path)
    if kind != "Bytes":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Bytes")
    hex_field = member_of(fields, "hex")
    if hex_field is None:
        raise protocol_error(ProtocolErrorKind.MISSING_FIELD, f"{path}.hex", "required field is absent")
    hex_text = json_string_of(hex_field, f"{path}.hex")
    return _parse_hex_bytes(hex_text, path)


def json_string_map(node: tuple, path: str) -> dict[str, str]:
    """Decodes a tagged Object<String, String> from the tree."""
    if node[0] != "obj":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Object")
    json_object_fields_exact(node, ["type", "entries"], path)
    entries = node[1][1][1]
    if entries[0] != "arr":
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, f"{path}.entries", "expected JSON array")
    output: dict[str, str] = {}
    for item in entries[1]:
        entry_fields = json_object_exact(item, ["key", "value"], f"{path}.entries")
        key = json_string_of(entry_fields[0], f"{path}.entries.key")
        output[key] = json_tagged_string(entry_fields[1], f"{path}.{key}")
    return output
