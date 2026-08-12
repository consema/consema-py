"""Intent documents for the closed fifteen-kind value model.

Golden facts pinned here come from conformance/vectors/v1.json (the
`value.decimal-normalization` and `value.float-signed-zero` cases) and the
kind registry of crates/consema-core/src/value.rs:622-653. These tests run
once the Python toolchain is ready (multi-language-implementation-plan §3/§7).
"""

import pytest

from consema.core import (
    Decimal,
    DuplicateKeyError,
    EntryMappingBuilder,
    Kind,
    ObjectBuilder,
    PortableValue,
    decimal,
    equal,
    hash_value,
)
from consema.core.errors import PVCEError, PVCEErrorKind


def test_fifteen_kinds_are_closed_and_named():
    # The language-neutral kind list (RFC 0016 §4.1; value.rs:622-653).
    assert [kind.value for kind in Kind] == [
        "Null", "Boolean", "Integer", "Decimal", "BinaryFloat32", "BinaryFloat64",
        "String", "Bytes", "Date", "Time", "LocalDateTime", "OffsetDateTime",
        "Sequence", "Object", "EntryMapping",
    ]
    assert len(list(Kind)) == 15


def test_constructors_cover_all_fifteen_kinds():
    values = [
        PortableValue.null(),
        PortableValue.boolean(True),
        PortableValue.integer(123456789012345678901234567890),
        PortableValue.decimal(decimal(1, -3)),
        PortableValue.binary_float32(0x7FC00001),
        PortableValue.binary_float64(0x8000000000000000),
        PortableValue.string("é"),
        PortableValue.bytes_value(b"\x00\xff"),
        PortableValue.date(2024, 2, 29),
        PortableValue.time(23, 59, 58, decimal(125, -3)),
        PortableValue.local_date_time(
            PortableValue.date(2024, 1, 1), PortableValue.time(0, 0, 0, decimal(0, 0))
        ),
        PortableValue.offset_date_time(
            PortableValue.local_date_time(
                PortableValue.date(2024, 1, 1), PortableValue.time(0, 0, 0, decimal(0, 0))
            ),
            -23 * 60 * 60,
        ),
        PortableValue.sequence([PortableValue.integer(1)]),
        PortableValue.object([("a", PortableValue.integer(1))]),
        PortableValue.entry_mapping(
            [(PortableValue.boolean(True), PortableValue.null())]
        ),
    ]
    assert len(values) == 15
    assert [value.kind for value in values] == list(Kind)


def test_object_rejects_duplicate_keys_at_construction():
    # RFC 0016 §4.1 / RFC 0002 object contract.
    with pytest.raises(DuplicateKeyError) as caught:
        PortableValue.object([("a", PortableValue.integer(1)), ("a", PortableValue.integer(2))])
    assert caught.value.key == "a"
    assert caught.value.code == "core.pvce.duplicate-object-key@1"

    builder = ObjectBuilder()
    builder.insert("a", PortableValue.integer(1))
    with pytest.raises(DuplicateKeyError):
        builder.insert("a", PortableValue.integer(2))


def test_entry_mapping_allows_duplicate_and_arbitrary_keys():
    builder = EntryMappingBuilder()
    builder.push(PortableValue.boolean(True), PortableValue.null())
    builder.push(PortableValue.boolean(True), PortableValue.null())
    mapping = builder.build()
    assert mapping.kind is Kind.ENTRY_MAPPING
    assert len(mapping.as_entry_mapping()) == 2


def test_decimal_normalization_strips_trailing_zeros():
    # value.rs:277-292: 10 × 10^0 → 1 × 10^1; zero coefficient → exponent 0;
    # -1200 × 10^-2 → -12 × 10^0.
    assert decimal(10, 0) == decimal(1, 1)
    assert decimal(0, 5) == decimal(0, 0)
    assert decimal(-1200, -2) == decimal(-12, 0)
    assert decimal(-1200, -2) != decimal(-12, -1)


def test_decimal_normalization_vector():
    # conformance/vectors/v1.json value.decimal-normalization:
    # "1.00" == "10e-1" strictly and by hash.
    one_hundredth = Decimal(100, -2)  # 1.00
    ten_e_minus_one = Decimal(10, -1)  # 10e-1
    assert one_hundredth == ten_e_minus_one


def test_float_signed_zero_vector():
    # conformance/vectors/v1.json value.float-signed-zero:
    # positive and negative zero are strict-unequal.
    positive = PortableValue.binary_float64(0x0000000000000000)
    negative = PortableValue.binary_float64(0x8000000000000000)
    assert not equal(positive, negative)


def test_integer_arbitrary_precision():
    # conformance/vectors/v1.json value.integer-arbitrary-precision.
    huge = 340282366920938463463374607431768211457  # 2^128 + 1
    value = PortableValue.integer(huge)
    assert value.as_integer() == huge


def test_strict_equality_is_order_dependent_for_containers():
    first = PortableValue.object(
        [("a", PortableValue.integer(1)), ("b", PortableValue.null())]
    )
    second = PortableValue.object(
        [("b", PortableValue.null()), ("a", PortableValue.integer(1))]
    )
    assert not equal(first, second)
    assert not equal(PortableValue.sequence([PortableValue.integer(1)]),
                     PortableValue.sequence([PortableValue.integer(2)]))


def test_equal_values_hash_equal():
    left = PortableValue.object([("a", PortableValue.integer(1))])
    right = PortableValue.object([("a", PortableValue.integer(1))])
    assert equal(left, right)
    assert hash_value(left) == hash_value(right)
    assert hash(left) == hash(right)


def test_date_leap_rule_uses_absolute_magnitude():
    # value.rs:433-434: year -400 is a leap year, year -100 is not.
    assert PortableValue.date(-400, 2, 29).kind is Kind.DATE
    with pytest.raises(PVCEError) as caught:
        PortableValue.date(-100, 2, 29)
    assert caught.value.kind is PVCEErrorKind.INVALID_TEMPORAL
    assert caught.value.code == "core.pvce.invalid-temporal@1"


def test_time_rejects_invalid_fields_and_fractions():
    with pytest.raises(PVCEError):
        PortableValue.time(24, 0, 0, decimal(0, 0))
    with pytest.raises(PVCEError):
        PortableValue.time(0, 0, 0, decimal(1, 0))  # fraction 1.0 not in [0, 1)
    with pytest.raises(PVCEError):
        PortableValue.time(0, 0, 0, decimal(-1, -1))
    # 125 × 10^-3 = 0.125 is a valid fraction.
    assert PortableValue.time(0, 0, 0, decimal(125, -3)).kind is Kind.TIME


def test_offset_date_time_range():
    local = PortableValue.local_date_time(
        PortableValue.date(2024, 1, 1), PortableValue.time(0, 0, 0, decimal(0, 0))
    )
    with pytest.raises(PVCEError):
        PortableValue.offset_date_time(local, 24 * 60 * 60)
    assert PortableValue.offset_date_time(local, -23 * 60 * 60).kind is Kind.OFFSET_DATE_TIME
