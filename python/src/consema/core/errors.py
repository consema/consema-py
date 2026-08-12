"""Typed PVCE/1 codec failures with frozen `core.pvce.*@1` codes.

Authority: crates/consema-pvce/src/lib.rs:1062-1108 (the StableFailure
diagnostic-code mapping); error registry arbitration: the same codes appear
verbatim in the v1 registry of crates/consema-protocol/src/error_registry.rs.
Go (go/core/errors.go) is a cross-reference only; the code set below is the
Rust decoder's full surface, including the two extension-related kinds
(NestedExtendedValue, ExpectedCoreValue) that the Go fifteen-kind model
cannot reach.

The exception text is human presentation only and never participates in
conformance comparison (RFC 0016 §6).
"""

from __future__ import annotations

import enum


class PVCEErrorKind(enum.Enum):
    """The closed set of strict PVCE/1 encode/decode failures."""

    INVALID_MAGIC = "invalid-magic"
    UNSUPPORTED_VERSION = "unsupported-version"
    UNEXPECTED_END = "unexpected-end"
    TRAILING_BYTES = "trailing-bytes"
    TRAILING_PAYLOAD = "trailing-payload"
    TRAILING_FIELD = "trailing-field"
    NON_CANONICAL_VARINT = "non-canonical-varint"
    VARINT_OVERFLOW = "varint-overflow"
    LENGTH_OVERFLOW = "length-overflow"
    RESOURCE_LIMIT = "resource-limit"
    UNKNOWN_TAG = "unknown-tag"
    INVALID_PAYLOAD = "invalid-payload"
    INVALID_INTEGER_SIGN = "invalid-integer-sign"
    NON_CANONICAL_INTEGER = "non-canonical-integer"
    NON_CANONICAL_DECIMAL = "non-canonical-decimal"
    INVALID_UTF8 = "invalid-utf8"
    OBJECT_KEY_NOT_STRING = "object-key-not-string"
    DUPLICATE_OBJECT_KEY = "duplicate-object-key"
    INVALID_TEMPORAL = "invalid-temporal"
    INVALID_VALUE = "invalid-value"
    NESTED_EXTENDED = "nested-extended"
    EXPECTED_CORE = "expected-core"


_CODE_BY_KIND = {
    PVCEErrorKind.INVALID_MAGIC: "core.pvce.invalid-magic@1",
    PVCEErrorKind.UNSUPPORTED_VERSION: "core.pvce.unsupported-version@1",
    PVCEErrorKind.UNEXPECTED_END: "core.pvce.unexpected-end@1",
    PVCEErrorKind.TRAILING_BYTES: "core.pvce.trailing-bytes@1",
    PVCEErrorKind.TRAILING_PAYLOAD: "core.pvce.trailing-payload@1",
    PVCEErrorKind.TRAILING_FIELD: "core.pvce.trailing-field@1",
    PVCEErrorKind.NON_CANONICAL_VARINT: "core.pvce.non-canonical-varint@1",
    PVCEErrorKind.VARINT_OVERFLOW: "core.pvce.varint-overflow@1",
    PVCEErrorKind.LENGTH_OVERFLOW: "core.pvce.length-overflow@1",
    PVCEErrorKind.RESOURCE_LIMIT: "core.pvce.resource-limit@1",
    PVCEErrorKind.UNKNOWN_TAG: "core.pvce.unknown-tag@1",
    PVCEErrorKind.INVALID_PAYLOAD: "core.pvce.invalid-payload@1",
    PVCEErrorKind.INVALID_INTEGER_SIGN: "core.pvce.invalid-integer-sign@1",
    PVCEErrorKind.NON_CANONICAL_INTEGER: "core.pvce.non-canonical-integer@1",
    PVCEErrorKind.NON_CANONICAL_DECIMAL: "core.pvce.non-canonical-decimal@1",
    PVCEErrorKind.INVALID_UTF8: "core.pvce.invalid-utf8@1",
    PVCEErrorKind.OBJECT_KEY_NOT_STRING: "core.pvce.object-key-not-string@1",
    PVCEErrorKind.DUPLICATE_OBJECT_KEY: "core.pvce.duplicate-object-key@1",
    PVCEErrorKind.INVALID_TEMPORAL: "core.pvce.invalid-temporal@1",
    PVCEErrorKind.INVALID_VALUE: "core.pvce.invalid-value@1",
    PVCEErrorKind.NESTED_EXTENDED: "core.pvce.nested-extended@1",
    PVCEErrorKind.EXPECTED_CORE: "core.pvce.expected-core@1",
}


class PVCEError(Exception):
    """A strict PVCE/1 codec failure (encode or decode).

    ``kind`` is the closed :class:`PVCEErrorKind`; ``field`` names the
    resource-limit field when the kind is RESOURCE_LIMIT; ``value`` carries
    the offending tag, version, or sign octet for context.
    """

    def __init__(self, kind: PVCEErrorKind, field: str | None = None, value: int | None = None):
        super().__init__(kind.value, field, value)
        self.kind = kind
        self.field = field
        self.value = value

    @property
    def code(self) -> str:
        """The frozen registered `core.pvce.*@1` code (RFC 0016 §6)."""
        return _CODE_BY_KIND[self.kind]

    def __str__(self) -> str:
        if self.kind is PVCEErrorKind.RESOURCE_LIMIT:
            return f"PVCE/1 resource limit: {self.field}"
        if self.kind is PVCEErrorKind.UNSUPPORTED_VERSION:
            return f"PVCE/1 unsupported version {self.value} (want 1)"
        if self.kind is PVCEErrorKind.TRAILING_PAYLOAD:
            return f"PVCE/1 trailing payload bytes after record tag 0x{self.value:x}"
        if self.kind is PVCEErrorKind.UNKNOWN_TAG:
            return f"PVCE/1 unknown core tag 0x{self.value:x}"
        if self.kind is PVCEErrorKind.INVALID_PAYLOAD:
            return f"PVCE/1 invalid payload for record tag 0x{self.value:x}"
        if self.kind is PVCEErrorKind.INVALID_INTEGER_SIGN:
            return f"PVCE/1 invalid integer sign octet {self.value}"
        return f"PVCE/1 {self.kind.value}"


class DuplicateKeyError(Exception):
    """A duplicate object key was rejected at construction time.

    RFC 0016 §4.1: objects reject duplicate keys at construction; the
    failure maps to the frozen registered code `core.pvce.duplicate-object-key@1`.
    """

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    @property
    def code(self) -> str:
        return _CODE_BY_KIND[PVCEErrorKind.DUPLICATE_OBJECT_KEY]

    def __str__(self) -> str:
        return f"duplicate object key: {self.key}"
