"""Runner-side comparison helpers.

These helpers mirror the Go runner's comparison conventions
(go/conformance/*): byte equality is lowercase-hex string equality, ordered
sequences compare element-wise in order, membership facts are unordered set
membership, and error codes compare by string equality. No expectation
literal lives here; every comparison is driven by the vector's expected
facts.
"""

from __future__ import annotations

from consema.core.equal import equal as strict_equal
from consema.core.value import Kind, PortableValue


def hex_text(data: bytes) -> str:
    """Lowercase hex spelling of exact bytes."""
    return data.hex()


def parse_hex(text: str) -> bytes:
    """Decodes a lowercase-hex vector fact."""
    return bytes.fromhex(text)


def require_equal(actual: object, expected: object, what: str) -> str | None:
    """Compares two scalar facts; returns a failure message or None."""
    if actual != expected:
        return f"{what}: expected {expected!r}, got {actual!r}"
    return None


def require_bytes_equal(actual: bytes, expected_hex: str, what: str) -> str | None:
    """Byte equality against a lowercase-hex expected fact."""
    return require_equal(hex_text(actual), expected_hex, what)


def require_ordered(actual: list, expected: list, what: str) -> str | None:
    """Ordered element-wise sequence equality."""
    if len(actual) != len(expected):
        return f"{what}: expected {len(expected)} items, got {len(actual)}"
    for index, (left, right) in enumerate(zip(actual, expected)):
        if left != right:
            return f"{what}[{index}]: expected {right!r}, got {left!r}"
    return None


def require_membership(codes: list[str], expected_code: str, what: str) -> str | None:
    """Unordered membership of one code in a code list."""
    if expected_code not in codes:
        return f"{what}: {expected_code!r} not found in {codes!r}"
    return None


def require_strict_equal(actual: PortableValue, expected: PortableValue, what: str) -> str | None:
    """Strict PortableValue equality (RFC 0016 §4.1)."""
    if not strict_equal(actual, expected):
        return f"{what}: values are not strictly equal"
    return None


def object_field(value: PortableValue, name: str) -> PortableValue | None:
    """One named field of an Object value."""
    if value.kind is not Kind.OBJECT:
        return None
    for key, item in value.as_object():
        if key == name:
            return item
    return None


def string_field(value: PortableValue, name: str) -> str | None:
    """One String field of an Object value."""
    field = object_field(value, name)
    if field is None or field.kind is not Kind.STRING:
        return None
    return field.as_string()


def boolean_field(value: PortableValue, name: str) -> bool | None:
    """One Boolean field of an Object value."""
    field = object_field(value, name)
    if field is None or field.kind is not Kind.BOOLEAN:
        return None
    return field.as_boolean()


def integer_field(value: PortableValue, name: str) -> int | None:
    """One non-negative Integer field of an Object value."""
    field = object_field(value, name)
    if field is None or field.kind is not Kind.INTEGER:
        return None
    number = field.as_integer()
    if number < 0:
        return None
    return number


def sequence_field(value: PortableValue, name: str) -> tuple[PortableValue, ...] | None:
    """One Sequence field of an Object value."""
    field = object_field(value, name)
    if field is None or field.kind is not Kind.SEQUENCE:
        return None
    return field.as_sequence()


def string_sequence(value: PortableValue, name: str) -> list[str] | None:
    """One String-sequence field of an Object value."""
    items = sequence_field(value, name)
    if items is None:
        return None
    result: list[str] = []
    for item in items:
        if item.kind is not Kind.STRING:
            return None
        result.append(item.as_string())
    return result


def integer_sequence(value: PortableValue, name: str) -> list[int] | None:
    """One Integer-sequence field of an Object value."""
    items = sequence_field(value, name)
    if items is None:
        return None
    result: list[int] = []
    for item in items:
        if item.kind is not Kind.INTEGER:
            return None
        result.append(item.as_integer())
    return result


def kind_spelling(kind) -> str:
    """The vector fact spelling of one value kind.

    The vectors freeze the Rust/Go internal kind spellings (for example
    ``Array`` for the sequence kind); the Python value model uses the RFC
    0016 §4.1 language-neutral spellings (``Sequence``), which are the PVCE
    wire names. This adaptation is runner-side only.
    """
    if kind.value == "Sequence":
        return "Array"
    return kind.value


def value_string(value: PortableValue) -> str:
    """Human rendering of a PortableValue for failure messages."""
    return repr(value)
