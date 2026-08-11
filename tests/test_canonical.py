"""Intent documents for the canonical tagged JSON transport.

The transport is `core.portable-value-json@1` (RFC 0015 §3.2; RFC 0016
§4.2): an envelope ``{"schema": ..., "value": <tagged>}`` where every value
is an object whose first member is ``type``. The decoder is strict (no
comments, no trailing commas, duplicate members rejected) and re-encodes
the parsed tree to require byte-exact canonical form; any valid-but-
non-canonical input fails with `core.protocol.non-canonical-json@1`.
"""

import pytest

from consema.core import Kind, PortableValue, decimal
from consema.protocol import (
    PortableValueJSONSchema,
    ProtocolError,
    ProtocolErrorKind,
    decode_json,
    decode_pvce,
    encode_json,
    encode_pvce,
)
from consema.protocol.limits import ProtocolLimits

LIMITS = ProtocolLimits()


def test_schema_constant_is_frozen():
    assert PortableValueJSONSchema == "core.portable-value-json@1"


def test_scalar_round_trips():
    for value in (
        PortableValue.null(),
        PortableValue.boolean(True),
        PortableValue.boolean(False),
        PortableValue.integer(-256),
        PortableValue.string("中"),
        PortableValue.bytes_value(b"\x00\xff"),
    ):
        stream = encode_json(value, LIMITS)
        assert decode_json(stream, LIMITS) == value


def test_integer_and_decimal_are_decimal_strings():
    stream = encode_json(PortableValue.integer(340282366920938463463374607431768211457), LIMITS)
    assert b'"340282366920938463463374607431768211457"' in stream
    stream = encode_json(PortableValue.decimal(decimal(125, -3)), LIMITS)
    assert b'"coefficient":' in stream and b'"125"' in stream and b'"-3"' in stream


def test_full_value_round_trip():
    value = PortableValue.object(
        [
            ("schema", PortableValue.string("example.test@1")),
            ("n", PortableValue.integer(42)),
            ("nested", PortableValue.sequence(
                [PortableValue.null(), PortableValue.binary_float64(0x8000000000000000)]
            )),
            ("when", PortableValue.offset_date_time(
                PortableValue.local_date_time(
                    PortableValue.date(-400, 2, 29),
                    PortableValue.time(23, 59, 58, decimal(125, -3)),
                ),
                3600,
            )),
        ]
    )
    stream = encode_json(value, LIMITS)
    assert decode_json(stream, LIMITS) == value


def test_whitespace_is_non_canonical():
    canonical = encode_json(PortableValue.integer(1), LIMITS)
    # Whitespace after the member colon is valid JSON but not the canonical
    # byte form; the field names stay intact so the envelope check passes
    # and the re-encode canonicality check reports the difference.
    spaced = canonical.replace(b'","value":', b'","value": ')
    with pytest.raises(ProtocolError) as caught:
        decode_json(spaced, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.NON_CANONICAL_JSON
    assert caught.value.code == "core.protocol.non-canonical-json@1"


def test_uppercase_hex_bits_are_non_canonical():
    # BinaryFloat64 bits must be lowercase hex (value_transport.rs:215-224).
    canonical = encode_json(PortableValue.binary_float64(0x000000000000000A), LIMITS)
    assert b"000000000000000a" in canonical
    uppercase = canonical.replace(b"000000000000000a", b"000000000000000A")
    with pytest.raises(ProtocolError) as caught:
        decode_json(uppercase, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.NON_CANONICAL_JSON


def test_reordered_fields_are_rejected():
    canonical = encode_json(PortableValue.boolean(True), LIMITS)
    # {"type":"Boolean","value":true} -> {"value":true,"type":"Boolean"}:
    # the tagged-value decode requires "type" to be the first field, so the
    # reordered form fails at the record decode (schema-mismatch), before
    # the canonicality re-encode check (value_transport.rs).
    reordered = canonical.replace(
        b'{"type":"Boolean","value":true}', b'{"value":true,"type":"Boolean"}'
    )
    with pytest.raises(ProtocolError) as caught:
        decode_json(reordered, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.SCHEMA_MISMATCH


def test_strict_parser_rejects_duplicate_members_comments_and_trailing():
    for bad in (
        b'{"schema":"core.portable-value-json@1","value":{"type":"Null"},"value":{"type":"Null"}}',
        b'{"schema":"core.portable-value-json@1","value":{"type":"Null"}} /*x*/',
    ):
        with pytest.raises(ProtocolError) as caught:
            decode_json(bad, LIMITS)
        assert caught.value.kind is ProtocolErrorKind.INVALID_JSON
        assert caught.value.code == "core.protocol.invalid-json@1"


def test_unicode_escapes_and_surrogate_pairs_decode():
    # "中" and a surrogate pair "😀" both decode to the same
    # text; the escape spelling is non-canonical (minimal escapes only).
    escaped = b'{"schema":"core.portable-value-json@1","value":{"type":"String","value":"\\u4e2d"}}'
    with pytest.raises(ProtocolError) as caught:
        decode_json(escaped, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.NON_CANONICAL_JSON
    pair = (
        b'{"schema":"core.portable-value-json@1",'
        b'"value":{"type":"String","value":"\\ud83d\\ude00"}}'
    )
    with pytest.raises(ProtocolError) as caught:
        decode_json(pair, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.NON_CANONICAL_JSON
    # A lone high surrogate is invalid JSON text.
    lone = (
        b'{"schema":"core.portable-value-json@1",'
        b'"value":{"type":"String","value":"\\ud83d"}}'
    )
    with pytest.raises(ProtocolError) as caught:
        decode_json(lone, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.INVALID_JSON


def test_number_tokens_are_strict():
    # Leading zeros and bare exponents are not valid JSON numbers.
    for token in (b"01", b"1.", b".5", b"1e"):
        document = (
            b'{"schema":"core.portable-value-json@1",'
            b'"value":{"type":"String","value":' + token + b"}}"
        )
        with pytest.raises(ProtocolError) as caught:
            decode_json(document, LIMITS)
        assert caught.value.kind is ProtocolErrorKind.INVALID_JSON


def test_envelope_schema_mismatch():
    wrong = b'{"schema":"core.other@1","value":{"type":"Null"}}'
    with pytest.raises(ProtocolError) as caught:
        decode_json(wrong, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.SCHEMA_MISMATCH


def test_unknown_value_type_and_unknown_fields_fail():
    unknown_type = b'{"schema":"core.portable-value-json@1","value":{"type":"Mystery"}}'
    with pytest.raises(ProtocolError) as caught:
        decode_json(unknown_type, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE
    extra_field = (
        b'{"schema":"core.portable-value-json@1",'
        b'"value":{"type":"Null","extra":1}}'
    )
    with pytest.raises(ProtocolError) as caught:
        decode_json(extra_field, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.UNKNOWN_FIELD


def test_duplicate_object_keys_fail_on_decode():
    duplicate = (
        b'{"schema":"core.portable-value-json@1",'
        b'"value":{"type":"Object","entries":['
        b'{"key":"a","value":{"type":"Integer","value":"1"}},'
        b'{"key":"a","value":{"type":"Integer","value":"2"}}]}}'
    )
    with pytest.raises(ProtocolError) as caught:
        decode_json(duplicate, LIMITS)
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def test_pvce_transport_round_trips_and_maps_errors():
    value = PortableValue.object(
        [("a", PortableValue.integer(1)), ("b", PortableValue.string("x"))]
    )
    stream = encode_pvce(value, LIMITS)
    assert decode_pvce(stream, LIMITS) == value
    with pytest.raises(ProtocolError) as caught:
        decode_pvce(b"PVCE\x02\x00\x00", LIMITS)
    assert caught.value.kind is ProtocolErrorKind.INVALID_PVCE
    assert caught.value.code == "core.protocol.invalid-pvce@1"
    tiny = ProtocolLimits(max_bytes=4)
    with pytest.raises(ProtocolError) as caught:
        encode_pvce(value, tiny)
    assert caught.value.kind is ProtocolErrorKind.RESOURCE_LIMIT
    assert caught.value.code == "core.protocol.resource-limit@1"


def test_entry_mapping_and_object_keep_order():
    mapping = PortableValue.entry_mapping(
        [
            (PortableValue.boolean(True), PortableValue.null()),
            (PortableValue.boolean(True), PortableValue.null()),
        ]
    )
    stream = encode_json(mapping, LIMITS)
    decoded = decode_json(stream, LIMITS)
    assert decoded.kind is Kind.ENTRY_MAPPING
    assert len(decoded.as_entry_mapping()) == 2
