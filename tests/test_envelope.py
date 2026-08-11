"""Intent documents for the core.protocol-message@1 envelope.

The envelope shape (schema / contract_id / contract_version / payload) and
the transport round trips are defined by RFC 0016 §3.2 and
crates/consema-protocol/src/contract.rs:419-521; the registered-payload
dispatch follows crates/consema-protocol/src/payload.rs.
"""

import pytest

from consema.core import PortableValue
from consema.protocol import (
    ContractId,
    ContractRegistry,
    ErrorCodeRegistry,
    ProtocolError,
    ProtocolErrorKind,
    ProtocolMessage,
    error_code_manifest_value,
    validate_registered_payload,
)
from consema.protocol.limits import ProtocolLimits

LIMITS = ProtocolLimits()


def _completion_like_payload() -> PortableValue:
    # A complete core.completion@1 record (execution.rs:40-49) that the
    # registered-payload validation decodes fully.
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.completion@1")),
            ("status", PortableValue.string("Success")),
            ("processed", PortableValue.integer(1)),
            ("produced", PortableValue.integer(1)),
            ("limit_name", PortableValue.null()),
            ("failure_code", PortableValue.null()),
        ]
    )


def test_envelope_round_trips_over_value_json_and_pvce():
    registry = ContractRegistry(1)
    message = ProtocolMessage(ContractId("core.completion", 1), _completion_like_payload(), registry)
    value = message.to_value()
    decoded = ProtocolMessage.from_value(value, registry)
    assert decoded.contract.id == "core.completion"
    assert decoded.contract.version == 1
    assert decoded.payload == message.payload
    assert ProtocolMessage.from_json(message.to_json(LIMITS), LIMITS, registry) == message
    assert ProtocolMessage.from_pvce(message.to_pvce(LIMITS), LIMITS, registry) == message


def test_incomplete_completion_payload_is_rejected():
    # A schema-first completion payload is not enough: registered payloads
    # are validated with the full record decoder (payload.rs), so the
    # partial record fails with a missing-field rejection.
    partial = PortableValue.object(
        [
            ("schema", PortableValue.string("core.completion@1")),
            ("status", PortableValue.string("Success")),
        ]
    )
    with pytest.raises(ProtocolError) as caught:
        ProtocolMessage(ContractId("core.completion", 1), partial, ContractRegistry(1))
    assert caught.value.kind is ProtocolErrorKind.MISSING_FIELD


def test_unknown_contract_and_schema_mismatch_are_distinct():
    registry = ContractRegistry(1)
    with pytest.raises(ProtocolError) as caught:
        ProtocolMessage(
            ContractId("example.unknown", 1),
            PortableValue.object([("schema", PortableValue.string("example.unknown@1"))]),
            registry,
        )
    assert caught.value.kind is ProtocolErrorKind.UNKNOWN_CONTRACT
    assert caught.value.code == "core.protocol.unknown-contract@1"

    with pytest.raises(ProtocolError) as caught:
        ProtocolMessage(
            ContractId("core.diagnostic", 1),
            PortableValue.object([("schema", PortableValue.string("core.completion@1"))]),
            registry,
        )
    assert caught.value.kind is ProtocolErrorKind.SCHEMA_MISMATCH


def test_matching_schema_does_not_bypass_full_payload_validation():
    # A core.diagnostic@1 payload with an unknown field fails the record
    # decode (contract.rs:651-663).
    payload = PortableValue.object(
        [
            ("schema", PortableValue.string("core.diagnostic@1")),
            ("placeholder", PortableValue.null()),
        ]
    )
    with pytest.raises(ProtocolError) as caught:
        ProtocolMessage(ContractId("core.diagnostic", 1), payload, ContractRegistry(1))
    assert caught.value.kind is ProtocolErrorKind.UNKNOWN_FIELD


def test_transport_envelope_is_not_a_nested_payload_contract():
    payload = PortableValue.object(
        [("schema", PortableValue.string("core.protocol-message@1"))]
    )
    with pytest.raises(ProtocolError) as caught:
        ProtocolMessage(ContractId("core.protocol-message", 1), payload, ContractRegistry(1))
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def test_error_code_registry_payload_is_fully_validated():
    # The manifest payload validates through validate_registered_payload.
    payload = error_code_manifest_value(1)
    validate_registered_payload(ContractId("core.error-code-registry", 1), payload, ContractRegistry(1))
    message = ProtocolMessage(
        ContractId("core.error-code-registry", 1), payload, ContractRegistry(1)
    )
    assert message.contract.schema() == "core.error-code-registry@1"


def test_profile_descriptor_and_capability_declaration_payloads():
    from consema.protocol import CapabilityDeclaration, ProfileDescriptor
    from consema.protocol.registry_descriptor import (
        CapabilityId,
        ImplementationSupport,
        SupportKind,
        VerificationStatus,
    )

    descriptor = ProfileDescriptor(
        format_family_id="json",
        format_family_version=1,
        profile_id="json.strict",
        profile_version=1,
        base_profile=None,
        differences=[],
        required_capabilities=[CapabilityId("core.value.strict-equality", 1)],
    )
    validate_registered_payload(
        ContractId("core.profile-descriptor", 1), descriptor.to_value(), ContractRegistry(1)
    )

    declaration = CapabilityDeclaration(
        CapabilityId("core.value.strict-equality", 1),
        ImplementationSupport(SupportKind.CONFORMANT, []),
        VerificationStatus.VERIFIED,
        suite_id="consema.conformance",
    )
    validate_registered_payload(
        ContractId("core.capability-declaration", 1),
        declaration.to_value(),
        ContractRegistry(1),
    )


def test_registry_manifest_payload():
    from consema.protocol import RegistryManifest

    manifest = RegistryManifest.build(7, ContractRegistry(7), ErrorCodeRegistry(7))
    validate_registered_payload(
        ContractId("core.registry-manifest", 1), manifest.to_value(), ContractRegistry(7)
    )
