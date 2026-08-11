"""PVCE/1 — Portable Value Canonical Encoding / 1.

Wire constants are frozen by the Rust reference codec
(crates/consema-pvce/src/lib.rs), which is the byte arbitration source:

- stream magic is the ASCII octets ``PVCE`` (lib.rs:23);
- version is minimal unsigned LEB128 ``1`` (lib.rs:25);
- integer sign octets are 0 (zero), 1 (positive), 2 (negative) (lib.rs:9-12);
- all unsigned lengths/counts/tags are minimal unsigned LEB128 (lib.rs:11);
- record framing is tag varint, payload-length varint, payload
  (``write_record``, lib.rs:610-614);
- integer payload: sign octet + magnitude-length varint + minimal big-endian
  magnitude (lib.rs:545-554); every nested field is additionally
  length-prefixed (lib.rs:556-561);
- decimal payload: two length-prefixed integer fields, coefficient then
  exponent (lib.rs:563-566); date: year field + month/day octets
  (lib.rs:580-584); time: hour/minute/second octets + fractional-second
  decimal field (lib.rs:593-596); local date-time: date field + time field;
  offset date-time: local date-time + offset-seconds integer field
  (lib.rs:605-608);
- float32/float64 payloads are the exact IEEE-754 bit patterns, big-endian;
- sequence: count varint + records; object: count varint + (string-key
  record, value record)*; entry mapping: count varint + (key record, value
  record)* (lib.rs:506-531).

Golden byte vectors are pinned by conformance/vectors/v1.json
(`pvce.null-vector`, `pvce.negative-integer-vector`, `pvce.object-vector`,
`pvce.reject-nonminimal-varint`) and re-pinned in the Rust tests
(lib.rs:1191-1342). The decoder is strict: non-minimal varints,
non-canonical integers/decimals, trailing bytes, and resource-limit
violations are rejected with the typed :class:`PVCEError`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from consema.core.errors import DuplicateKeyError, PVCEError, PVCEErrorKind
from consema.core.value import (
    Decimal,
    EntryMappingBuilder,
    ExtendedValue,
    Kind,
    ObjectBuilder,
    PortableValue,
)

MAGIC = b"PVCE"
VERSION = 1

TAG_NULL = 0x00
TAG_FALSE = 0x01
TAG_TRUE = 0x02
TAG_INTEGER = 0x10
TAG_DECIMAL = 0x11
TAG_FLOAT32 = 0x12
TAG_FLOAT64 = 0x13
TAG_STRING = 0x20
TAG_BYTES = 0x21
TAG_DATE = 0x30
TAG_TIME = 0x31
TAG_LOCAL_DATE_TIME = 0x32
TAG_OFFSET_DATE_TIME = 0x33
TAG_SEQUENCE = 0x40
TAG_OBJECT = 0x41
TAG_ENTRY_MAPPING = 0x42
TAG_EXTENDED = 0x7F

_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_DEPTH = 256
_DEFAULT_MAX_NODES = 1_000_000
_DEFAULT_MAX_CONTAINER_ENTRIES = 1_000_000
_DEFAULT_MAX_INTEGER_BYTES = 1024 * 1024
_DEFAULT_MAX_BLOB_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class DecodeLimits:
    """Strict decoder resource limits (lib.rs:56-82)."""

    max_bytes: int = _DEFAULT_MAX_BYTES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_nodes: int = _DEFAULT_MAX_NODES
    max_container_entries: int = _DEFAULT_MAX_CONTAINER_ENTRIES
    max_integer_bytes: int = _DEFAULT_MAX_INTEGER_BYTES
    max_blob_bytes: int = _DEFAULT_MAX_BLOB_BYTES


@dataclass(frozen=True)
class EncodeLimits:
    """Bounded encoder resource limits (lib.rs:111-138)."""

    max_bytes: int = _DEFAULT_MAX_BYTES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_nodes: int = _DEFAULT_MAX_NODES
    max_container_entries: int = _DEFAULT_MAX_CONTAINER_ENTRIES
    max_integer_bytes: int = _DEFAULT_MAX_INTEGER_BYTES
    max_blob_bytes: int = _DEFAULT_MAX_BLOB_BYTES


# --------------------------------------------------------------------------
# varint helpers (minimal unsigned LEB128, lib.rs:616-628)
# --------------------------------------------------------------------------

def varint_size(value: int) -> int:
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


def _append_varint(output: bytearray, value: int) -> None:
    while True:
        octet = value & 0x7F
        value >>= 7
        if value != 0:
            octet |= 0x80
        output.append(octet)
        if value == 0:
            return


# --------------------------------------------------------------------------
# encoder
# --------------------------------------------------------------------------

def encode(value: PortableValue) -> bytes:
    """Encodes one core value as a complete canonical PVCE/1 stream."""
    return _encode_root(value)


def encode_value(value: PortableValue | ExtendedValue) -> bytes:
    """Encodes a core or extension root (lib.rs encode_value, 92-101)."""
    return _encode_root(value)


def _encode_root(root: PortableValue | ExtendedValue) -> bytes:
    output = bytearray()
    output.extend(MAGIC)
    _append_varint(output, VERSION)
    if isinstance(root, ExtendedValue):
        _encode_extended_record(root, output)
    else:
        _encode_record(root, output)
    return bytes(output)


def encode_bounded(value: PortableValue, limits: EncodeLimits) -> bytes:
    """Encodes one core value after exact size measurement; never truncates.

    Exceeding any limit raises :class:`PVCEError` with the RESOURCE_LIMIT
    kind and the limit field name (lib.rs encode_bounded, 150-156).
    """
    if _measure_root(value, limits) > limits.max_bytes:
        raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="stream-bytes")
    return encode(value)


def _encode_record(value: PortableValue, output: bytearray) -> None:
    tag, payload = _encode_payload(value)
    _write_record(tag, payload, output)


def _encode_payload(value: PortableValue) -> tuple[int, bytearray]:
    kind = value.kind
    if kind is Kind.NULL:
        return TAG_NULL, bytearray()
    if kind is Kind.BOOLEAN:
        return (TAG_TRUE if value.as_boolean() else TAG_FALSE), bytearray()
    if kind is Kind.INTEGER:
        payload = bytearray()
        _encode_integer_payload(value.as_integer(), payload)
        return TAG_INTEGER, payload
    if kind is Kind.DECIMAL:
        payload = bytearray()
        _encode_decimal_payload(value.as_decimal(), payload)
        return TAG_DECIMAL, payload
    if kind is Kind.BINARY_FLOAT32:
        return TAG_FLOAT32, bytearray(struct.pack(">I", value.as_binary_float32()))
    if kind is Kind.BINARY_FLOAT64:
        return TAG_FLOAT64, bytearray(struct.pack(">Q", value.as_binary_float64()))
    if kind is Kind.STRING:
        payload = bytearray()
        _encode_blob(value.as_string().encode("utf-8"), payload)
        return TAG_STRING, payload
    if kind is Kind.BYTES:
        payload = bytearray()
        _encode_blob(value.as_bytes(), payload)
        return TAG_BYTES, payload
    if kind is Kind.DATE:
        year, month, day = value.as_date()
        payload = bytearray()
        _encode_integer_field(year, payload)
        payload.append(month)
        payload.append(day)
        return TAG_DATE, payload
    if kind is Kind.TIME:
        hour, minute, second, fraction = value.as_time()
        payload = bytearray((hour, minute, second))
        _encode_decimal_field(fraction, payload)
        return TAG_TIME, payload
    if kind is Kind.LOCAL_DATE_TIME:
        date, time = value.as_local_date_time()
        payload = bytearray()
        _encode_date_field(date, payload)
        _encode_time_field(time, payload)
        return TAG_LOCAL_DATE_TIME, payload
    if kind is Kind.OFFSET_DATE_TIME:
        local, offset_seconds = value.as_offset_date_time()
        payload = bytearray()
        _encode_date_field(local.as_local_date_time()[0], payload)
        _encode_time_field(local.as_local_date_time()[1], payload)
        _encode_integer_field(offset_seconds, payload)
        return TAG_OFFSET_DATE_TIME, payload
    if kind is Kind.SEQUENCE:
        payload = bytearray()
        _append_varint(payload, len(value.as_sequence()))
        for item in value.as_sequence():
            _encode_record(item, payload)
        return TAG_SEQUENCE, payload
    if kind is Kind.OBJECT:
        payload = bytearray()
        entries = value.as_object()
        _append_varint(payload, len(entries))
        for key, entry_value in entries:
            _encode_record(PortableValue.string(key), payload)
            _encode_record(entry_value, payload)
        return TAG_OBJECT, payload
    if kind is Kind.ENTRY_MAPPING:
        payload = bytearray()
        entries = value.as_entry_mapping()
        _append_varint(payload, len(entries))
        for key, entry_value in entries:
            _encode_record(key, payload)
            _encode_record(entry_value, payload)
        return TAG_ENTRY_MAPPING, payload
    raise AssertionError(f"unreachable kind {kind!r}")


def _encode_extended_record(value: ExtendedValue, output: bytearray) -> None:
    payload = bytearray()
    _encode_blob(value.type_id.encode("utf-8"), payload)
    _append_varint(payload, value.semantic_version)
    _encode_blob(value.payload_codec_id.encode("utf-8"), payload)
    _encode_blob(value.canonical_payload, payload)
    _write_record(TAG_EXTENDED, payload, output)


def _write_record(tag: int, payload: bytearray, output: bytearray) -> None:
    _append_varint(output, tag)
    _append_varint(output, len(payload))
    output.extend(payload)


def _encode_integer_payload(value: int, output: bytearray) -> None:
    if value < 0:
        output.append(2)
        magnitude = (-value).to_bytes(((-value).bit_length() + 7) // 8, "big")
    elif value == 0:
        output.append(0)
        magnitude = b""
    else:
        output.append(1)
        magnitude = value.to_bytes((value.bit_length() + 7) // 8, "big")
    _append_varint(output, len(magnitude))
    output.extend(magnitude)


def _encode_integer_field(value: int, output: bytearray) -> None:
    field = bytearray()
    _encode_integer_payload(value, field)
    _append_varint(output, len(field))
    output.extend(field)


def _encode_decimal_payload(value: Decimal, output: bytearray) -> None:
    _encode_integer_field(value.coefficient, output)
    _encode_integer_field(value.exponent, output)


def _encode_decimal_field(value: Decimal, output: bytearray) -> None:
    field = bytearray()
    _encode_decimal_payload(value, field)
    _append_varint(output, len(field))
    output.extend(field)


def _encode_blob(blob: bytes, output: bytearray) -> bytearray:
    _append_varint(output, len(blob))
    output.extend(blob)
    return output


def _encode_date_field(value: PortableValue, output: bytearray) -> None:
    field = bytearray()
    year, month, day = value.as_date()
    _encode_integer_field(year, field)
    field.append(month)
    field.append(day)
    _append_varint(output, len(field))
    output.extend(field)


def _encode_time_field(value: PortableValue, output: bytearray) -> None:
    field = bytearray()
    hour, minute, second, fraction = value.as_time()
    field.extend((hour, minute, second))
    _encode_decimal_field(fraction, field)
    _append_varint(output, len(field))
    output.extend(field)


# --------------------------------------------------------------------------
# bounded-encode size measurement (lib.rs Sizer, 170-364)
# --------------------------------------------------------------------------

def _measure_root(value: PortableValue, limits: EncodeLimits) -> int:
    sizer = _Sizer(limits)
    record = sizer.record_size(value, 0)
    return len(MAGIC) + 1 + record


class _Sizer:
    def __init__(self, limits: EncodeLimits):
        self.limits = limits
        self.nodes = 0

    def _record(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="nesting-depth")
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="value-nodes")

    def _check(self, observed: int, limit: int, name: str) -> None:
        if observed > limit:
            raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field=name)

    def record_size(self, value: PortableValue, depth: int) -> int:
        self._record(depth)
        kind = value.kind
        if kind is Kind.NULL:
            tag, payload = TAG_NULL, 0
        elif kind is Kind.BOOLEAN:
            tag, payload = (TAG_TRUE if value.as_boolean() else TAG_FALSE), 0
        elif kind is Kind.INTEGER:
            integer_value = value.as_integer()
            magnitude_len = (
                0 if integer_value == 0 else (abs(integer_value).bit_length() + 7) // 8
            )
            self._check(magnitude_len, self.limits.max_integer_bytes, "integer-bytes")
            tag, payload = TAG_INTEGER, 1 + varint_size(magnitude_len) + magnitude_len
        elif kind is Kind.DECIMAL:
            decimal_value = value.as_decimal()
            tag, payload = TAG_DECIMAL, self._integer_field_size(
                decimal_value.coefficient
            ) + self._integer_field_size(decimal_value.exponent)
        elif kind is Kind.BINARY_FLOAT32:
            tag, payload = TAG_FLOAT32, 4
        elif kind is Kind.BINARY_FLOAT64:
            tag, payload = TAG_FLOAT64, 8
        elif kind is Kind.STRING:
            text = value.as_string().encode("utf-8")
            self._check(len(text), self.limits.max_blob_bytes, "blob-bytes")
            tag, payload = TAG_STRING, varint_size(len(text)) + len(text)
        elif kind is Kind.BYTES:
            blob = value.as_bytes()
            self._check(len(blob), self.limits.max_blob_bytes, "blob-bytes")
            tag, payload = TAG_BYTES, varint_size(len(blob)) + len(blob)
        elif kind is Kind.DATE:
            year, _month, _day = value.as_date()
            tag, payload = TAG_DATE, self._integer_field_size(year) + 2
        elif kind is Kind.TIME:
            _hour, _minute, _second, fraction = value.as_time()
            tag, payload = TAG_TIME, 3 + self._decimal_field_size(fraction)
        elif kind is Kind.LOCAL_DATE_TIME:
            date, time = value.as_local_date_time()
            tag, payload = TAG_LOCAL_DATE_TIME, self._date_field_size(
                date
            ) + self._time_field_size(time)
        elif kind is Kind.OFFSET_DATE_TIME:
            local, offset_seconds = value.as_offset_date_time()
            date, time = local.as_local_date_time()
            tag, payload = TAG_OFFSET_DATE_TIME, self._date_field_size(
                date
            ) + self._time_field_size(time) + self._integer_field_size(offset_seconds)
        elif kind is Kind.SEQUENCE:
            items = value.as_sequence()
            self._check(len(items), self.limits.max_container_entries, "container-entries")
            payload = varint_size(len(items))
            for item in items:
                payload += self.record_size(item, depth + 1)
            tag = TAG_SEQUENCE
        elif kind is Kind.OBJECT:
            entries = value.as_object()
            self._check(len(entries), self.limits.max_container_entries, "container-entries")
            payload = varint_size(len(entries))
            for key, entry_value in entries:
                # Object keys are encoded as String records and count as nodes
                # (lib.rs:332-341).
                payload += self.record_size(PortableValue.string(key), depth + 1)
                payload += self.record_size(entry_value, depth + 1)
            tag = TAG_OBJECT
        elif kind is Kind.ENTRY_MAPPING:
            entries = value.as_entry_mapping()
            self._check(len(entries), self.limits.max_container_entries, "container-entries")
            payload = varint_size(len(entries))
            for key, entry_value in entries:
                payload += self.record_size(key, depth + 1)
                payload += self.record_size(entry_value, depth + 1)
            tag = TAG_ENTRY_MAPPING
        else:
            raise AssertionError(f"unreachable kind {kind!r}")
        return varint_size(tag) + varint_size(payload) + payload

    def _integer_field_size(self, value: int) -> int:
        magnitude_len = max(1, (abs(value).bit_length() + 7) // 8)
        if value == 0:
            magnitude_len = 0
        self._check(magnitude_len, self.limits.max_integer_bytes, "integer-bytes")
        payload = 1 + varint_size(magnitude_len) + magnitude_len
        return varint_size(payload) + payload

    def _decimal_field_size(self, value: Decimal) -> int:
        payload = self._integer_field_size(value.coefficient) + self._integer_field_size(
            value.exponent
        )
        return varint_size(payload) + payload

    def _date_field_size(self, value: PortableValue) -> int:
        year, _month, _day = value.as_date()
        payload = self._integer_field_size(year) + 2
        return varint_size(payload) + payload

    def _time_field_size(self, value: PortableValue) -> int:
        _hour, _minute, _second, fraction = value.as_time()
        payload = 3 + self._decimal_field_size(fraction)
        return varint_size(payload) + payload


# --------------------------------------------------------------------------
# decoder
# --------------------------------------------------------------------------

def decode(stream: bytes, limits: DecodeLimits | None = None) -> PortableValue:
    """Strictly decodes one core PortableValue stream (lib.rs decode, 104-108).

    An extension root fails with the EXPECTED_CORE kind.
    """
    root = decode_value(stream, limits)
    if isinstance(root, ExtendedValue):
        raise PVCEError(PVCEErrorKind.EXPECTED_CORE)
    return root


def decode_value(stream: bytes, limits: DecodeLimits | None = None) -> PortableValue | ExtendedValue:
    """Strictly decodes a core or extension root (lib.rs decode_value, 404-426)."""
    limits = limits or DecodeLimits()
    if len(stream) > limits.max_bytes:
        raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="stream-bytes")
    reader = _Reader(stream, limits)
    if reader.take(len(MAGIC)) != MAGIC:
        raise PVCEError(PVCEErrorKind.INVALID_MAGIC)
    version = reader.varint()
    if version != VERSION:
        raise PVCEError(PVCEErrorKind.UNSUPPORTED_VERSION, value=version)
    tag, payload = reader.record()
    if tag == TAG_EXTENDED:
        root = _decode_extended(payload, reader)
    else:
        root = _decode_core_record(tag, payload, reader, 0)
    if not reader.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_BYTES)
    return root


class _Reader:
    """Strict streaming reader over one PVCE/1 stream or payload (lib.rs:630-723)."""

    __slots__ = ("data", "offset", "limits", "nodes")

    def __init__(self, data: bytes, limits: DecodeLimits, nodes: int = 0):
        self.data = data
        self.offset = 0
        self.limits = limits
        self.nodes = nodes

    def take(self, length: int) -> bytes:
        end = self.offset + length
        if end > len(self.data):
            raise PVCEError(PVCEErrorKind.UNEXPECTED_END)
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def octet(self) -> int:
        return self.take(1)[0]

    def varint(self) -> int:
        start = self.offset
        value = 0
        for shift in range(0, 64, 7):
            octet = self.octet()
            low = octet & 0x7F
            if shift == 63 and low > 1:
                raise PVCEError(PVCEErrorKind.VARINT_OVERFLOW)
            value |= low << shift
            if octet & 0x80 == 0:
                if self.offset - start > 1 and low == 0:
                    raise PVCEError(PVCEErrorKind.NON_CANONICAL_VARINT)
                return value
        raise PVCEError(PVCEErrorKind.VARINT_OVERFLOW)

    def length(self, limit: int, name: str) -> int:
        value = self.varint()
        if value > limit:
            raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field=name)
        return value

    def record(self) -> tuple[int, bytes]:
        tag = self.varint()
        length = self.length(self.limits.max_bytes, "record-bytes")
        return tag, self.take(length)

    def is_empty(self) -> bool:
        return self.offset == len(self.data)

    def child(self, payload: bytes) -> "_Reader":
        return _Reader(payload, self.limits, self.nodes)

    def absorb(self, child: "_Reader") -> None:
        self.nodes = child.nodes

    def count_node(self) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="value-nodes")


def _decode_core_record(tag: int, payload: bytes, parent: _Reader, depth: int) -> PortableValue:
    if depth > parent.limits.max_depth:
        raise PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="nesting-depth")
    parent.count_node()
    reader = parent.child(payload)
    if tag == TAG_NULL:
        if payload:
            raise PVCEError(PVCEErrorKind.INVALID_PAYLOAD, value=tag)
        value = PortableValue.null()
    elif tag == TAG_FALSE:
        if payload:
            raise PVCEError(PVCEErrorKind.INVALID_PAYLOAD, value=tag)
        value = PortableValue.boolean(False)
    elif tag == TAG_TRUE:
        if payload:
            raise PVCEError(PVCEErrorKind.INVALID_PAYLOAD, value=tag)
        value = PortableValue.boolean(True)
    elif tag == TAG_INTEGER:
        value = PortableValue.integer(_decode_integer_payload(reader))
    elif tag == TAG_DECIMAL:
        value = PortableValue.decimal(_decode_decimal_payload(reader))
    elif tag == TAG_FLOAT32:
        if len(payload) != 4:
            raise PVCEError(PVCEErrorKind.INVALID_PAYLOAD, value=tag)
        value = PortableValue.binary_float32(struct.unpack(">I", reader.take(4))[0])
    elif tag == TAG_FLOAT64:
        if len(payload) != 8:
            raise PVCEError(PVCEErrorKind.INVALID_PAYLOAD, value=tag)
        value = PortableValue.binary_float64(struct.unpack(">Q", reader.take(8))[0])
    elif tag == TAG_STRING:
        blob = _decode_blob(reader)
        try:
            value = PortableValue.string(blob.decode("utf-8"))
        except UnicodeDecodeError:
            raise PVCEError(PVCEErrorKind.INVALID_UTF8) from None
    elif tag == TAG_BYTES:
        value = PortableValue.bytes_value(_decode_blob(reader))
    elif tag == TAG_DATE:
        value = _decode_date_payload(reader)
    elif tag == TAG_TIME:
        value = _decode_time_payload(reader)
    elif tag == TAG_LOCAL_DATE_TIME:
        value = PortableValue.local_date_time(
            _decode_date_field(reader), _decode_time_field(reader)
        )
    elif tag == TAG_OFFSET_DATE_TIME:
        date = _decode_date_field(reader)
        time = _decode_time_field(reader)
        offset = _decode_integer_field(reader)
        if not -0x80000000 <= offset <= 0x7FFFFFFF:
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL)
        try:
            value = PortableValue.offset_date_time(
                PortableValue.local_date_time(date, time), offset
            )
        except PVCEError:
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL) from None
    elif tag == TAG_SEQUENCE:
        count = reader.length(reader.limits.max_container_entries, "container-entries")
        items = []
        for _ in range(count):
            child_tag, child_payload = reader.record()
            if child_tag == TAG_EXTENDED:
                raise PVCEError(PVCEErrorKind.NESTED_EXTENDED)
            items.append(_decode_core_record(child_tag, child_payload, reader, depth + 1))
        value = PortableValue.sequence(items)
    elif tag == TAG_OBJECT:
        count = reader.length(reader.limits.max_container_entries, "container-entries")
        builder = ObjectBuilder()
        for _ in range(count):
            key_tag, key_payload = reader.record()
            if key_tag != TAG_STRING:
                raise PVCEError(PVCEErrorKind.OBJECT_KEY_NOT_STRING)
            key_value = _decode_core_record(key_tag, key_payload, reader, depth + 1)
            value_tag, value_payload = reader.record()
            if value_tag == TAG_EXTENDED:
                raise PVCEError(PVCEErrorKind.NESTED_EXTENDED)
            item = _decode_core_record(value_tag, value_payload, reader, depth + 1)
            try:
                builder.insert(key_value.as_string(), item)
            except DuplicateKeyError:
                raise PVCEError(PVCEErrorKind.DUPLICATE_OBJECT_KEY) from None
        value = builder.build()
    elif tag == TAG_ENTRY_MAPPING:
        count = reader.length(reader.limits.max_container_entries, "container-entries")
        builder = EntryMappingBuilder()
        for _ in range(count):
            key_tag, key_payload = reader.record()
            key = _decode_core_record(key_tag, key_payload, reader, depth + 1)
            value_tag, value_payload = reader.record()
            entry_value = _decode_core_record(value_tag, value_payload, reader, depth + 1)
            builder.push(key, entry_value)
        value = builder.build()
    elif tag == TAG_EXTENDED:
        raise PVCEError(PVCEErrorKind.NESTED_EXTENDED)
    else:
        raise PVCEError(PVCEErrorKind.UNKNOWN_TAG, value=tag)
    if not reader.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_PAYLOAD, value=tag)
    parent.absorb(reader)
    return value


def _decode_integer_payload(reader: _Reader) -> int:
    sign = reader.octet()
    length = reader.length(reader.limits.max_integer_bytes, "integer-bytes")
    magnitude = reader.take(length)
    if sign == 0:
        if magnitude:
            raise PVCEError(PVCEErrorKind.NON_CANONICAL_INTEGER)
        return 0
    if sign != 1 and sign != 2:
        raise PVCEError(PVCEErrorKind.INVALID_INTEGER_SIGN, value=sign)
    if not magnitude or magnitude[0] == 0:
        raise PVCEError(PVCEErrorKind.NON_CANONICAL_INTEGER)
    value = int.from_bytes(magnitude, "big")
    return -value if sign == 2 else value


def _decode_integer_field(reader: _Reader) -> int:
    length = reader.length(reader.limits.max_integer_bytes + 16, "integer-field")
    payload = reader.take(length)
    field = reader.child(payload)
    value = _decode_integer_payload(field)
    if not field.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_FIELD)
    return value


def _decode_decimal_payload(reader: _Reader) -> Decimal:
    coefficient = _decode_integer_field(reader)
    exponent = _decode_integer_field(reader)
    decimal_value = Decimal(coefficient, exponent)
    if decimal_value.coefficient != coefficient or decimal_value.exponent != exponent:
        raise PVCEError(PVCEErrorKind.NON_CANONICAL_DECIMAL)
    return decimal_value


def _decode_decimal_field(reader: _Reader) -> Decimal:
    length = reader.length(
        reader.limits.max_integer_bytes * 2 + 32, "decimal-field"
    )
    payload = reader.take(length)
    field = reader.child(payload)
    value = _decode_decimal_payload(field)
    if not field.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_FIELD)
    return value


def _decode_blob(reader: _Reader) -> bytes:
    length = reader.length(reader.limits.max_blob_bytes, "blob-bytes")
    return reader.take(length)


def _decode_date_payload(reader: _Reader) -> PortableValue:
    year = _decode_integer_field(reader)
    month = reader.octet()
    day = reader.octet()
    try:
        return PortableValue.date(year, month, day)
    except PVCEError:
        raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL) from None


def _decode_date_field(reader: _Reader) -> PortableValue:
    length = reader.length(reader.limits.max_integer_bytes + 32, "date-field")
    payload = reader.take(length)
    field = reader.child(payload)
    value = _decode_date_payload(field)
    if not field.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_FIELD)
    return value


def _decode_time_payload(reader: _Reader) -> PortableValue:
    hour = reader.octet()
    minute = reader.octet()
    second = reader.octet()
    fraction = _decode_decimal_field(reader)
    try:
        return PortableValue.time(hour, minute, second, fraction)
    except PVCEError:
        raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL) from None


def _decode_time_field(reader: _Reader) -> PortableValue:
    length = reader.length(reader.limits.max_integer_bytes * 2 + 64, "time-field")
    payload = reader.take(length)
    field = reader.child(payload)
    value = _decode_time_payload(field)
    if not field.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_FIELD)
    return value


def _decode_extended(payload: bytes, parent: _Reader) -> ExtendedValue:
    reader = parent.child(payload)
    try:
        type_id = _decode_blob(reader).decode("utf-8")
    except UnicodeDecodeError:
        raise PVCEError(PVCEErrorKind.INVALID_UTF8) from None
    semantic_version = reader.varint()
    try:
        payload_codec_id = _decode_blob(reader).decode("utf-8")
    except UnicodeDecodeError:
        raise PVCEError(PVCEErrorKind.INVALID_UTF8) from None
    canonical_payload = _decode_blob(reader)
    if not reader.is_empty():
        raise PVCEError(PVCEErrorKind.TRAILING_PAYLOAD, value=TAG_EXTENDED)
    parent.absorb(reader)
    return ExtendedValue(type_id, semantic_version, payload_codec_id, canonical_payload)
