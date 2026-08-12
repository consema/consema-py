"""Strict PortableValue equality and deterministic hashing.

Authority: RFC 0016 §4.1 and the vector cases of conformance/vectors/v1.json
(`value.decimal-normalization`, `value.float-signed-zero`). The hash is
defined as FNV-1a (64-bit) over the canonical PVCE/1 encoding of the value,
so equal values always hash equal and the hash is order-dependent; this is
the same contract as go/core/equal.go (cross-reference only).

``equal`` is total: it never raises on valid values and never accepts an
unknown kind (the kind set is closed by construction).
"""

from __future__ import annotations

from consema.core.value import Kind, PortableValue

_FNV_OFFSET_BASIS = 14695981039346656037
_FNV_PRIME = 1099511628211
_MASK64 = (1 << 64) - 1


def equal(left: PortableValue, right: PortableValue) -> bool:
    """Strict equality: kind identity plus canonical content equality.

    Objects compare entry-by-entry in stored order; entry mappings
    association-by-association in stored order (duplicates included);
    sequences item-by-item in stored order. Because values are canonical by
    construction (Decimal normalization at construction, unique ordered
    object keys), structural equality coincides with byte equality of the
    PVCE/1 encodings.
    """
    if left.kind is not right.kind:
        return False
    kind = left.kind
    if kind is Kind.NULL:
        return True
    if kind is Kind.BOOLEAN:
        return left.as_boolean() == right.as_boolean()
    if kind is Kind.INTEGER:
        return left.as_integer() == right.as_integer()
    if kind is Kind.STRING:
        return left.as_string() == right.as_string()
    if kind is Kind.DECIMAL:
        return left.as_decimal() == right.as_decimal()
    if kind is Kind.BINARY_FLOAT32:
        return left.as_binary_float32() == right.as_binary_float32()
    if kind is Kind.BINARY_FLOAT64:
        return left.as_binary_float64() == right.as_binary_float64()
    if kind is Kind.BYTES:
        return left.as_bytes() == right.as_bytes()
    if kind is Kind.DATE:
        return left.as_date() == right.as_date()
    if kind is Kind.TIME:
        return left.as_time() == right.as_time()
    if kind is Kind.LOCAL_DATE_TIME:
        left_date, left_time = left.as_local_date_time()
        right_date, right_time = right.as_local_date_time()
        return equal(left_date, right_date) and equal(left_time, right_time)
    if kind is Kind.OFFSET_DATE_TIME:
        left_local, left_offset = left.as_offset_date_time()
        right_local, right_offset = right.as_offset_date_time()
        return left_offset == right_offset and equal(left_local, right_local)
    if kind is Kind.SEQUENCE:
        left_items = left.as_sequence()
        right_items = right.as_sequence()
        return len(left_items) == len(right_items) and all(
            equal(item_left, item_right)
            for item_left, item_right in zip(left_items, right_items)
        )
    if kind is Kind.OBJECT:
        left_entries = left.as_object()
        right_entries = right.as_object()
        if len(left_entries) != len(right_entries):
            return False
        for (left_key, left_value), (right_key, right_value) in zip(
            left_entries, right_entries
        ):
            if left_key != right_key or not equal(left_value, right_value):
                return False
        return True
    if kind is Kind.ENTRY_MAPPING:
        left_entries = left.as_entry_mapping()
        right_entries = right.as_entry_mapping()
        if len(left_entries) != len(right_entries):
            return False
        for (left_key, left_value), (right_key, right_value) in zip(
            left_entries, right_entries
        ):
            if not equal(left_key, right_key) or not equal(left_value, right_value):
                return False
        return True
    raise AssertionError(f"unreachable kind {kind!r}")  # closed set


def hash_value(value: PortableValue) -> int:
    """The deterministic 64-bit hash: FNV-1a over the canonical PVCE/1 bytes.

    Equal values always hash equal; the hash is order-dependent (objects,
    sequences, and entry mappings hash by ordered content). The PVCE/1
    encoding of a valid value never fails, so the hash is total.
    """
    from consema.core.pvce import encode

    hasher = _FNV_OFFSET_BASIS
    for octet in encode(value):
        hasher ^= octet
        hasher = (hasher * _FNV_PRIME) & _MASK64
    return hasher
