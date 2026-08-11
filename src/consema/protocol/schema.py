"""Fixed-field record helpers over PortableValue.

Authority: crates/consema-protocol/src/schema.rs (exact_fields /
schema_fields / the typed field readers); Go (go/protocol/schema.go) is a
cross-reference only. Every helper reports :class:`ProtocolError` with the
matching kind and path, so the shared vectors' error_path facts match.
"""

from __future__ import annotations

from consema.core.value import Kind, PortableValue
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error


def exact_fields(value: PortableValue, expected: list[str], path: str) -> list[PortableValue]:
    """Validates a fixed-field Object record.

    The value must be an Object; every field must be declared by the
    schema, every declared field must be present, and the fields must
    appear exactly in the canonical order. Returns the field values in
    schema order (schema.rs:16-53).
    """
    if value.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Object")
    entries = value.as_object()
    names = [key for key, _ in entries]
    values = [entry_value for _, entry_value in entries]
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
            ProtocolErrorKind.SCHEMA_MISMATCH, path, "fields are not in canonical order"
        )
    return values


def schema_fields(
    value: PortableValue, schema: str, expected: list[str], path: str
) -> list[PortableValue]:
    """Validates a fixed-field record whose first field is the schema
    discriminator and returns all field values (schema.rs:55-70)."""
    fields = exact_fields(value, expected, path)
    observed = string_of(fields[0], f"{path}.schema")
    if observed != schema:
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, f"{path}.schema", f"expected {schema}"
        )
    return fields


def string_of(value: PortableValue, path: str) -> str:
    if value.kind is not Kind.STRING:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected String")
    return value.as_string()


def boolean_of(value: PortableValue, path: str) -> bool:
    if value.kind is not Kind.BOOLEAN:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Boolean")
    return value.as_boolean()


def sequence_of(value: PortableValue, path: str) -> tuple[PortableValue, ...]:
    if value.kind is not Kind.SEQUENCE:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Sequence")
    return value.as_sequence()


def unsigned32(value: PortableValue, path: str) -> int:
    if value.kind is not Kind.INTEGER:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Integer")
    number = value.as_integer()
    if number < 0 or number > 0xFFFFFFFF:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "expected an unsigned 32-bit Integer"
        )
    return number


def unsigned64(value: PortableValue, path: str) -> int:
    if value.kind is not Kind.INTEGER:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Integer")
    number = value.as_integer()
    if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "expected an unsigned 64-bit Integer"
        )
    return number


def signed32(value: PortableValue, path: str) -> int:
    if value.kind is not Kind.INTEGER:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Integer")
    number = value.as_integer()
    if number < -0x80000000 or number > 0x7FFFFFFF:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "expected a signed 32-bit Integer"
        )
    return number


def integer_value(value: int) -> PortableValue:
    """Builds the Integer record for an unsigned integer."""
    return PortableValue.integer(value)


def nullable_string(value: str | None) -> PortableValue:
    if value is None:
        return PortableValue.null()
    return PortableValue.string(value)


def optional_string(value: PortableValue, path: str) -> str | None:
    """Null yields None; any other value must be a String."""
    if value.kind is Kind.NULL:
        return None
    return string_of(value, path)


def string_map_object(values: dict[str, str]) -> PortableValue:
    """Encodes a deterministic sorted Object<String, String>."""
    entries = [(key, PortableValue.string(values[key])) for key in sorted(values)]
    return PortableValue.object(entries)


def string_map_from_object(value: PortableValue, path: str) -> dict[str, str]:
    if value.kind is not Kind.OBJECT:
        raise protocol_error(
            ProtocolErrorKind.WRONG_TYPE, path, "expected Object<String, String>"
        )
    output: dict[str, str] = {}
    for key, entry_value in value.as_object():
        output[key] = string_of(entry_value, f"{path}.{key}")
    return output


def contains_string(items: list[str], element: str) -> bool:
    return element in items
