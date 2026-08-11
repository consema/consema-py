"""Contract identifiers, registry, and the common protocol envelope.

The registry records are transcribed VERBATIM from
crates/consema-protocol/src/contract.rs (CONTRACTS_V1..CONTRACTS_V7,
contract.rs:71-273), the registry arbitration source; the v1-v7 counts are
16/18/25/25/30/38/41 (contract.rs:696-702). Note that v4 shares the v3 set
(contract.rs:381). Go (go/protocol/contract.go) is a cross-reference only.

RFC 0016 §3.2 defines the envelope shape (`core.protocol-message@1` with
schema / contract_id / contract_version / payload); the envelope itself is
implemented in consema.protocol.envelope.
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.protocol.errors import ProtocolErrorKind, protocol_error


class ContractStability(enum.Enum):
    """Compatibility status of one frozen contract (contract.rs:52-58)."""

    STABLE = "Stable"
    TRANSPORT = "Transport"


class ContractId:
    """A stable versioned protocol contract identifier (contract.rs:12-49)."""

    __slots__ = ("id", "version")

    def __init__(self, id: str, version: int):
        # contract.rs:18-30 plus validate_identifier (contract.rs:559-578):
        # version non-zero; id at most 255 bytes, dotted, every segment
        # starts with a lowercase letter and continues with lowercase
        # letters, digits, or '-'.
        if version == 0:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.contract.version",
                "version must be non-zero",
            )
        validate_identifier(id, "$.contract.id")
        self.id = id
        self.version = version

    def schema(self) -> str:
        """The canonical ``id@version`` schema discriminator."""
        return f"{self.id}@{self.version}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ContractId):
            return NotImplemented
        return self.id == other.id and self.version == other.version

    def __hash__(self) -> int:
        return hash((self.id, self.version))

    def __lt__(self, other: "ContractId") -> bool:
        return (self.id, self.version) < (other.id, other.version)

    def __repr__(self) -> str:
        return f"ContractId({self.schema()!r})"


def validate_identifier(identifier: str, path: str) -> None:
    """Validates a dotted lowercase identifier (contract.rs:559-578)."""
    if len(identifier.encode("utf-8")) > 255 or "." not in identifier:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE,
            path,
            "identifier must contain multiple segments and be at most 255 bytes",
        )
    for segment in identifier.split("."):
        if not segment or not ("a" <= segment[0] <= "z"):
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE, path, "identifier contains an invalid segment"
            )
        for character in segment[1:]:
            if not (
                ("a" <= character <= "z")
                or ("0" <= character <= "9")
                or character == "-"
            ):
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    path,
                    "identifier contains an invalid segment",
                )


def validate_namespace(identifier: str, require_dot: bool, path: str) -> None:
    """Validates a profile/namespace identifier (contract.go:148-168).

    Unlike contract identifiers, non-first segments may start with a digit
    (profile IDs such as ``json.strict``); used by profile references and
    profile descriptors.
    """
    if (
        not identifier
        or len(identifier.encode("utf-8")) > 255
        or (require_dot and "." not in identifier)
    ):
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "invalid namespaced identifier"
        )
    for index, segment in enumerate(identifier.split(".")):
        if not segment:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE, path, "invalid identifier segment"
            )
        first = segment[0]
        if not ("a" <= first <= "z") and not (index != 0 and "0" <= first <= "9"):
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE, path, "invalid identifier segment"
            )
        for character in segment[1:]:
            if not (
                ("a" <= character <= "z")
                or ("0" <= character <= "9")
                or character == "-"
            ):
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE, path, "invalid identifier segment"
                )


def _descriptor(id: str, version: int = 1) -> tuple[str, int, ContractStability]:
    return (id, version, ContractStability.STABLE)


def _transport(id: str) -> tuple[str, int, ContractStability]:
    return (id, 1, ContractStability.TRANSPORT)


# Verbatim transcription of CONTRACTS_V1 (contract.rs:71-88).
_CONTRACTS_V1 = [
    _descriptor("core.cancellation-request"),
    _descriptor("core.capability-declaration"),
    _descriptor("core.change-set"),
    _descriptor("core.completion"),
    _descriptor("core.diagnostic"),
    _descriptor("core.error-code-registry"),
    _descriptor("core.execution-policy"),
    _descriptor("core.profile-descriptor"),
    _descriptor("core.projection-report"),
    _descriptor("core.projection-request"),
    _descriptor("core.projection-result"),
    _transport("core.protocol-message"),
    _descriptor("core.provenance-map"),
    _descriptor("core.query-definition"),
    _descriptor("core.query-result"),
    _descriptor("core.registry-manifest"),
]

# Verbatim transcription of CONTRACTS_V2 (contract.rs:90-109).
_CONTRACTS_V2 = _CONTRACTS_V1 + [
    _descriptor("core.source-patch"),
    _descriptor("core.source-snapshot"),
]

# Verbatim transcription of CONTRACTS_V3 (contract.rs:111-140); V4 shares it
# (contract.rs:381).
_CONTRACTS_V3 = [
    _descriptor("core.cancellation-request"),
    _descriptor("core.capability-declaration"),
    _descriptor("core.change-set"),
    _descriptor("core.completion"),
    _descriptor("core.conversion-report"),
    _descriptor("core.diagnostic"),
    _descriptor("core.edit-plan"),
    _descriptor("core.error-code-registry"),
    _descriptor("core.execution-policy"),
    _descriptor("core.format-operation-registry"),
    _descriptor("core.materialization-provenance-map"),
    _descriptor("core.materialization-report"),
    _descriptor("core.materialization-request"),
    _descriptor("core.materialization-result"),
    _descriptor("core.profile-descriptor"),
    _descriptor("core.projection-report"),
    _descriptor("core.projection-request"),
    _descriptor("core.projection-result"),
    _transport("core.protocol-message"),
    _descriptor("core.provenance-map"),
    _descriptor("core.query-definition"),
    _descriptor("core.query-result"),
    _descriptor("core.registry-manifest"),
    _descriptor("core.source-patch"),
    _descriptor("core.source-snapshot"),
]

# Verbatim transcription of CONTRACTS_V5 (contract.rs:142-176).
_CONTRACTS_V5 = [
    _descriptor("core.cancellation-request"),
    _descriptor("core.capability-declaration"),
    _descriptor("core.change-set"),
    _descriptor("core.completion"),
    _descriptor("core.conversion-report"),
    _descriptor("core.diagnostic"),
    _descriptor("core.edit-plan"),
    _descriptor("core.error-code-registry"),
    _descriptor("core.execution-policy"),
    _descriptor("core.format-operation-registry"),
    _descriptor("core.graph-projection-result"),
    _descriptor("core.graph-provenance-map"),
    _descriptor("core.graph-query-result"),
    _descriptor("core.materialization-provenance-map"),
    _descriptor("core.materialization-report"),
    _descriptor("core.materialization-request"),
    _descriptor("core.materialization-result"),
    _descriptor("core.portable-graph"),
    _descriptor("core.profile-descriptor"),
    _descriptor("core.projection-report"),
    _descriptor("core.projection-request"),
    _descriptor("core.projection-result"),
    _transport("core.protocol-message"),
    _descriptor("core.provenance-map"),
    _descriptor("core.query-definition"),
    _descriptor("core.query-result"),
    _descriptor("core.registry-manifest"),
    _descriptor("core.source-patch"),
    _descriptor("core.source-snapshot"),
    _descriptor("core.yaml-query-result"),
]

# Verbatim transcription of CONTRACTS_V6 (contract.rs:178-223).
_CONTRACTS_V6 = [
    _descriptor("core.cancellation-request"),
    _descriptor("core.capability-declaration"),
    _descriptor("core.change-set"),
    _descriptor("core.completion"),
    _descriptor("core.conversion-report"),
    _descriptor("core.diagnostic"),
    _descriptor("core.edit-plan"),
    _descriptor("core.error-code-registry"),
    _descriptor("core.execution-policy"),
    _descriptor("core.format-operation-registry"),
    _descriptor("core.graph-projection-result"),
    _descriptor("core.graph-provenance-map"),
    _descriptor("core.graph-query-result"),
    _descriptor("core.ini-query-result"),
    _descriptor("core.java-properties-query-result"),
    _descriptor("core.java-utf16-string"),
    _descriptor("core.materialization-provenance-map"),
    _descriptor("core.materialization-report"),
    _descriptor("core.materialization-request"),
    _descriptor("core.materialization-request", 2),
    _descriptor("core.materialization-result"),
    _descriptor("core.materialization-result", 2),
    _descriptor("core.portable-graph"),
    _descriptor("core.profile-descriptor"),
    _descriptor("core.projection-report"),
    _descriptor("core.projection-request"),
    _descriptor("core.projection-result"),
    _transport("core.protocol-message"),
    _descriptor("core.provenance-map"),
    _descriptor("core.query-definition"),
    _descriptor("core.query-result"),
    _descriptor("core.registry-manifest"),
    _descriptor("core.source-encoding"),
    _descriptor("core.source-patch"),
    _descriptor("core.source-patch", 2),
    _descriptor("core.source-snapshot"),
    _descriptor("core.source-snapshot", 2),
    _descriptor("core.yaml-query-result"),
]

# Verbatim transcription of CONTRACTS_V7 (contract.rs:225-273).
_CONTRACTS_V7 = [
    _descriptor("core.batch-plan"),
    _descriptor("core.batch-result"),
    _descriptor("core.cancellation-request"),
    _descriptor("core.capability-declaration"),
    _descriptor("core.change-set"),
    _descriptor("core.cli-output"),
    _descriptor("core.completion"),
    _descriptor("core.conversion-report"),
    _descriptor("core.diagnostic"),
    _descriptor("core.edit-plan"),
    _descriptor("core.error-code-registry"),
    _descriptor("core.execution-policy"),
    _descriptor("core.format-operation-registry"),
    _descriptor("core.graph-projection-result"),
    _descriptor("core.graph-provenance-map"),
    _descriptor("core.graph-query-result"),
    _descriptor("core.ini-query-result"),
    _descriptor("core.java-properties-query-result"),
    _descriptor("core.java-utf16-string"),
    _descriptor("core.materialization-provenance-map"),
    _descriptor("core.materialization-report"),
    _descriptor("core.materialization-request"),
    _descriptor("core.materialization-request", 2),
    _descriptor("core.materialization-result"),
    _descriptor("core.materialization-result", 2),
    _descriptor("core.portable-graph"),
    _descriptor("core.profile-descriptor"),
    _descriptor("core.projection-report"),
    _descriptor("core.projection-request"),
    _descriptor("core.projection-result"),
    _transport("core.protocol-message"),
    _descriptor("core.provenance-map"),
    _descriptor("core.query-definition"),
    _descriptor("core.query-result"),
    _descriptor("core.registry-manifest"),
    _descriptor("core.source-encoding"),
    _descriptor("core.source-patch"),
    _descriptor("core.source-patch", 2),
    _descriptor("core.source-snapshot"),
    _descriptor("core.source-snapshot", 2),
    _descriptor("core.yaml-query-result"),
]


class ContractRegistry:
    """A closed, explicitly versioned contract registry (contract.rs:295-415)."""

    def __init__(self, version: int):
        if not 1 <= version <= 7:
            raise ValueError("contract registry version must be 1..7")
        self.version = version

    def contracts(self) -> list[tuple[str, int, ContractStability]]:
        """The sorted registered (id, version, stability) records."""
        return {
            1: _CONTRACTS_V1,
            2: _CONTRACTS_V2,
            3: _CONTRACTS_V3,
            4: _CONTRACTS_V3,  # v4 shares the v3 set (contract.rs:381)
            5: _CONTRACTS_V5,
            6: _CONTRACTS_V6,
            7: _CONTRACTS_V7,
        }[self.version]

    def recognizes(self, contract: ContractId) -> bool:
        return self.descriptor(contract) is not None

    def descriptor(
        self, contract: ContractId
    ) -> tuple[str, int, ContractStability] | None:
        for record in self.contracts():
            if record[0] == contract.id and record[1] == contract.version:
                return record
        return None


class ProtocolMessage:
    """One validated protocol payload in the common envelope.

    The envelope is `core.protocol-message@1` with fields schema /
    contract_id / contract_version / payload (contract.rs:419-521).
    Transport envelopes cannot be nested as payload contracts; the payload
    must be an Object whose first field is the schema discriminator matching
    the contract; registered payloads are then validated by
    :func:`consema.protocol.envelope.validate_registered_payload`.
    """

    __slots__ = ("contract", "payload")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProtocolMessage):
            return NotImplemented
        return self.contract == other.contract and self.payload == other.payload

    def __hash__(self) -> int:
        return hash((self.contract, self.payload))

    def __init__(self, contract: ContractId, payload: PortableValue, registry: ContractRegistry):
        descriptor = registry.descriptor(contract)
        if descriptor is None:
            raise protocol_error(
                ProtocolErrorKind.UNKNOWN_CONTRACT, "$.contract", contract.schema()
            )
        if descriptor[2] is ContractStability.TRANSPORT:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.contract",
                "transport envelopes cannot be nested as payload contracts",
            )
        _validate_payload_schema(payload, contract)
        from consema.protocol.envelope import validate_registered_payload

        validate_registered_payload(contract, payload, registry)
        self.contract = contract
        self.payload = payload

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.protocol-message@1")),
                ("contract_id", PortableValue.string(self.contract.id)),
                ("contract_version", PortableValue.integer(self.contract.version)),
                ("payload", self.payload),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue, registry: ContractRegistry) -> "ProtocolMessage":
        from consema.protocol.schema import schema_fields, string_of, unsigned32

        fields = schema_fields(
            value,
            "core.protocol-message@1",
            ["schema", "contract_id", "contract_version", "payload"],
            "$",
        )
        contract = ContractId(
            string_of(fields[1], "$.contract_id"),
            unsigned32(fields[2], "$.contract_version"),
        )
        return ProtocolMessage(contract, fields[3], registry)

    # -- transports ---------------------------------------------------------

    def to_json(self, limits) -> bytes:
        """Encodes the envelope through canonical tagged JSON."""
        from consema.protocol.canonical import encode_json

        return encode_json(self.to_value(), limits)

    @staticmethod
    def from_json(data: bytes, limits, registry: ContractRegistry) -> "ProtocolMessage":
        """Decodes canonical tagged JSON and validates the registry contract."""
        from consema.protocol.canonical import decode_json

        return ProtocolMessage.from_value(decode_json(data, limits), registry)

    def to_pvce(self, limits) -> bytes:
        """Encodes the envelope through canonical PVCE/1."""
        from consema.protocol.canonical import encode_pvce

        return encode_pvce(self.to_value(), limits)

    @staticmethod
    def from_pvce(data: bytes, limits, registry: ContractRegistry) -> "ProtocolMessage":
        """Decodes canonical PVCE/1 and validates the registry contract."""
        from consema.protocol.canonical import decode_pvce

        return ProtocolMessage.from_value(decode_pvce(data, limits), registry)


def _validate_payload_schema(payload: PortableValue, contract: ContractId) -> None:
    """The payload must be an Object whose first field is ``schema`` carrying
    the contract discriminator (contract.rs:523-557)."""
    if payload.kind is not Kind.OBJECT:
        raise protocol_error(
            ProtocolErrorKind.WRONG_TYPE, "$.payload", "payload must be an Object"
        )
    entries = payload.as_object()
    if not entries:
        raise protocol_error(
            ProtocolErrorKind.MISSING_FIELD, "$.payload.schema", "payload schema is absent"
        )
    if entries[0][0] != "schema":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, "$.payload", "schema must be the first field"
        )
    from consema.protocol.schema import string_of

    observed = string_of(entries[0][1], "$.payload.schema")
    if observed != contract.schema():
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH,
            "$.payload.schema",
            f"expected {contract.schema()}",
        )
