"""Intent documents for the PVCE/1 byte codec.

Golden byte vectors are copied verbatim from conformance/vectors/v1.json
(`pvce.null-vector`, `pvce.negative-integer-vector`, `pvce.object-vector`,
`pvce.reject-nonminimal-varint`) and re-pinned by the Rust tests
(crates/consema-pvce/src/lib.rs:1191-1342).
"""

import pytest

from consema.core import (
    DecodeLimits,
    EncodeLimits,
    PortableValue,
    decode,
    decode_value,
    encode,
    encode_bounded,
    encode_value,
)
from consema.core.errors import PVCEError, PVCEErrorKind
from consema.core.value import Decimal, ExtendedValue, decimal


def _hex(data: bytes) -> str:
    return data.hex()


def test_null_vector():
    # conformance/vectors/v1.json pvce.null-vector (and lib.rs:1337-1338).
    assert _hex(encode(PortableValue.null())) == "50564345010000"
    assert decode(bytes.fromhex("50564345010000")) == PortableValue.null()


def test_negative_integer_vector():
    # conformance/vectors/v1.json pvce.negative-integer-vector (lib.rs:1339-1341).
    assert _hex(encode(PortableValue.integer(-256))) == "5056434501100402020100"
    assert decode(bytes.fromhex("5056434501100402020100")) == PortableValue.integer(-256)


def test_object_vector():
    # conformance/vectors/v1.json pvce.object-vector (lib.rs:1191-1201):
    # {"a": 1} → 50 56 43 45 01 41 0a 01 20 02 01 61 10 03 01 01 01.
    value = PortableValue.object([("a", PortableValue.integer(1))])
    assert _hex(encode(value)) == "5056434501410a01200201611003010101"
    decoded = decode(bytes.fromhex("5056434501410a01200201611003010101"))
    assert decoded == value
    # Object order is strict: {"b": .., "a": ..} differs byte-wise.
    assert _hex(
        encode(PortableValue.object([("b", PortableValue.null()), ("a", PortableValue.integer(1))]))
    ) != _hex(encode(value))


def test_reject_nonminimal_varint_vector():
    # conformance/vectors/v1.json pvce.reject-nonminimal-varint.
    with pytest.raises(PVCEError) as caught:
        decode(bytes.fromhex("5056434581000000"))
    assert caught.value.kind is PVCEErrorKind.NON_CANONICAL_VARINT
    assert caught.value.code == "core.pvce.non-canonical-varint@1"


def test_decode_rejects_noncanonical_zero_integer():
    # lib.rs:1327-1333: [PVCE, 1, 0x10, 3, 1, 1, 0] fails NonCanonicalInteger.
    with pytest.raises(PVCEError) as caught:
        decode(b"PVCE\x01\x10\x03\x01\x01\x00")
    assert caught.value.kind is PVCEErrorKind.NON_CANONICAL_INTEGER


def test_all_fifteen_kinds_round_trip():
    date = PortableValue.date(-12345, 2, 28)
    time = PortableValue.time(23, 59, 58, decimal(125, -3))
    local = PortableValue.local_date_time(date, time)
    mapping = PortableValue.entry_mapping(
        [(PortableValue.boolean(True), PortableValue.null())]
    )
    value = PortableValue.sequence(
        [
            PortableValue.null(),
            PortableValue.boolean(False),
            PortableValue.integer(123456789012345678901234567890),
            PortableValue.decimal(Decimal(1, -999)),
            PortableValue.binary_float32(0x7FC00001),
            PortableValue.binary_float64(0x8000000000000000),
            PortableValue.string("é"),
            PortableValue.bytes_value(b"\x00\xff"),
            date,
            time,
            local,
            PortableValue.offset_date_time(local, -23 * 60 * 60),
            mapping,
        ]
    )
    wrapped = PortableValue.sequence(
        [
            value,
            PortableValue.object([("a", PortableValue.integer(1)), ("b", PortableValue.string("中"))]),
        ]
    )
    stream = encode(wrapped)
    assert decode(stream) == wrapped
    assert encode(decode(stream)) == stream


def test_decode_is_strict_about_trailing_bytes_and_payloads():
    null_stream = bytes.fromhex("50564345010000")
    with pytest.raises(PVCEError) as caught:
        decode(null_stream + b"\x00")
    assert caught.value.kind is PVCEErrorKind.TRAILING_BYTES
    # Null record with a non-empty payload.
    with pytest.raises(PVCEError) as caught:
        decode(b"PVCE\x01\x00\x01\x00")
    assert caught.value.kind is PVCEErrorKind.INVALID_PAYLOAD
    # Unknown tag.
    with pytest.raises(PVCEError) as caught:
        decode(b"PVCE\x01\x7e\x00")
    assert caught.value.kind is PVCEErrorKind.UNKNOWN_TAG


def test_decode_limits_enforce_resource_bounds():
    limits = DecodeLimits(max_bytes=4)
    with pytest.raises(PVCEError) as caught:
        decode(bytes.fromhex("50564345010000"), limits)
    assert caught.value.kind is PVCEErrorKind.RESOURCE_LIMIT
    assert caught.value.field == "stream-bytes"

    limits = DecodeLimits(max_nodes=1)
    with pytest.raises(PVCEError) as caught:
        decode(bytes.fromhex("5056434501410a01200201611003010101"), limits)
    assert caught.value.kind is PVCEErrorKind.RESOURCE_LIMIT
    assert caught.value.field == "value-nodes"


def test_bounded_encode_rejects_limits_without_partial_output():
    value = PortableValue.sequence(
        [PortableValue.string("12345"), PortableValue.string("67890"), PortableValue.string("abcde")]
    )
    with pytest.raises(PVCEError) as caught:
        encode_bounded(value, EncodeLimits(max_bytes=4))
    assert caught.value.field == "stream-bytes"
    with pytest.raises(PVCEError) as caught:
        encode_bounded(value, EncodeLimits(max_nodes=2))
    assert caught.value.field == "value-nodes"
    with pytest.raises(PVCEError) as caught:
        encode_bounded(value, EncodeLimits(max_container_entries=2))
    assert caught.value.field == "container-entries"
    with pytest.raises(PVCEError) as caught:
        encode_bounded(PortableValue.string("12345"), EncodeLimits(max_blob_bytes=4))
    assert caught.value.field == "blob-bytes"
    with pytest.raises(PVCEError) as caught:
        encode_bounded(PortableValue.integer(0x0102), EncodeLimits(max_integer_bytes=1))
    assert caught.value.field == "integer-bytes"


def test_encode_and_decode_error_codes_are_frozen():
    assert PVCEError(PVCEErrorKind.INVALID_MAGIC).code == "core.pvce.invalid-magic@1"
    assert PVCEError(PVCEErrorKind.UNSUPPORTED_VERSION, value=2).code == "core.pvce.unsupported-version@1"
    assert PVCEError(PVCEErrorKind.RESOURCE_LIMIT, field="x").code == "core.pvce.resource-limit@1"
    assert PVCEError(PVCEErrorKind.NESTED_EXTENDED).code == "core.pvce.nested-extended@1"
    assert PVCEError(PVCEErrorKind.EXPECTED_CORE).code == "core.pvce.expected-core@1"


def test_extension_roots_round_trip_opaquely():
    # lib.rs:1287-1299.
    extension = ExtendedValue("example.uuid", 1, "example.raw@1", b"\x01\x02\x03")
    stream = encode_value(extension)
    assert decode_value(stream) == extension
    # The core-only decoder rejects extension roots (lib.rs:1302-1314).
    with pytest.raises(PVCEError) as caught:
        decode(stream)
    assert caught.value.kind is PVCEErrorKind.EXPECTED_CORE
