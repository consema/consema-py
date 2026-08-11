"""Typed protocol failures shared by the JSON/PVCE transports and records.

Authority: crates/consema-protocol/src/error.rs (ProtocolErrorKind and its
code mapping); RFC 0016 §6 (typed errors carrying the registered code).
Go (go/protocol/errors.go) is a cross-reference only.

The exception text is human presentation only and never participates in
conformance comparison.
"""

from __future__ import annotations

import enum


class ProtocolErrorKind(enum.Enum):
    INVALID_JSON = "invalid-json"
    NON_CANONICAL_JSON = "non-canonical-json"
    INVALID_PVCE = "invalid-pvce"
    UNKNOWN_CONTRACT = "unknown-contract"
    SCHEMA_MISMATCH = "schema-mismatch"
    UNKNOWN_FIELD = "unknown-field"
    MISSING_FIELD = "missing-field"
    WRONG_TYPE = "wrong-type"
    INVALID_VALUE = "invalid-value"
    RESOURCE_LIMIT = "resource-limit"
    PROCESS_LOCAL_HANDLE = "process-local-handle"


_CODE_BY_KIND = {
    ProtocolErrorKind.INVALID_JSON: "core.protocol.invalid-json@1",
    ProtocolErrorKind.NON_CANONICAL_JSON: "core.protocol.non-canonical-json@1",
    ProtocolErrorKind.INVALID_PVCE: "core.protocol.invalid-pvce@1",
    ProtocolErrorKind.UNKNOWN_CONTRACT: "core.protocol.unknown-contract@1",
    ProtocolErrorKind.SCHEMA_MISMATCH: "core.protocol.schema-mismatch@1",
    ProtocolErrorKind.UNKNOWN_FIELD: "core.protocol.unknown-field@1",
    ProtocolErrorKind.MISSING_FIELD: "core.protocol.missing-field@1",
    ProtocolErrorKind.WRONG_TYPE: "core.protocol.wrong-type@1",
    ProtocolErrorKind.INVALID_VALUE: "core.protocol.invalid-value@1",
    ProtocolErrorKind.RESOURCE_LIMIT: "core.protocol.resource-limit@1",
    ProtocolErrorKind.PROCESS_LOCAL_HANDLE: "core.protocol.process-local-handle@1",
}


class ProtocolError(Exception):
    """A typed protocol failure (transport or record level).

    ``kind`` is the closed :class:`ProtocolErrorKind`; ``path`` names the
    failing JSON-pointer-ish location (``"$.files[0].source_digest"``),
    mirroring the shared vectors' error_path facts.
    """

    def __init__(self, kind: ProtocolErrorKind, path: str, detail: str):
        super().__init__(kind.value, path, detail)
        self.kind = kind
        self.path = path
        self.detail = detail

    @property
    def code(self) -> str:
        """The frozen registered code (error.rs:35-49; RFC 0016 §6)."""
        return _CODE_BY_KIND[self.kind]

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.detail}"


def invalid(path: str, detail: str) -> ProtocolError:
    """Builds the InvalidValue protocol error (the Rust crate::schema::invalid)."""
    return ProtocolError(ProtocolErrorKind.INVALID_VALUE, path, detail)


def resource(path: str, detail: str) -> ProtocolError:
    """Builds the ResourceLimit protocol error."""
    return ProtocolError(ProtocolErrorKind.RESOURCE_LIMIT, path, detail)


def protocol_error(kind: ProtocolErrorKind, path: str, detail: str) -> ProtocolError:
    """Builds a protocol error with an explicit kind."""
    return ProtocolError(kind, path, detail)
