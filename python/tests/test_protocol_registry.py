"""Intent documents for the contract and error-code registries.

Frozen facts: contract registry counts 16/18/25/25/30/38/41 across v1-v7
(crates/consema-protocol/src/contract.rs:696-702) and error-code counts
55/62/90/92/132/166/187 (error_registry.rs:1717-1723); every version is
sorted; later versions are supersets of earlier ones (contract.rs:703-716,
error_registry.rs:1726-1774).
"""

import pytest

from consema.protocol import (
    ContractId,
    ContractRegistry,
    ErrorCodeDescriptor,
    ErrorCodeRegistry,
    ProtocolError,
    ProtocolErrorKind,
    error_code_manifest_value,
    validate_error_code_manifest_value,
)

CONTRACT_COUNTS = {1: 16, 2: 18, 3: 25, 4: 25, 5: 30, 6: 38, 7: 41}
ERROR_COUNTS = {1: 55, 2: 62, 3: 90, 4: 92, 5: 132, 6: 166, 7: 187}


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7])
def test_contract_registry_counts(version):
    records = ContractRegistry(version).contracts()
    assert len(records) == CONTRACT_COUNTS[version]
    # Records are sorted by (id, version) and unique.
    for left, right in zip(records, records[1:]):
        assert (left[0], left[1]) < (right[0], right[1])


def test_contract_registry_v4_shares_v3_and_later_versions_grow():
    assert ContractRegistry(4).contracts() == ContractRegistry(3).contracts()
    assert set(ContractRegistry(5).contracts()) >= set(ContractRegistry(4).contracts())
    assert set(ContractRegistry(6).contracts()) >= set(ContractRegistry(5).contracts())
    assert set(ContractRegistry(7).contracts()) >= set(ContractRegistry(6).contracts())


def test_v7_contracts_include_the_cli_records():
    registry = ContractRegistry(7)
    assert registry.recognizes(ContractId("core.cli-output", 1))
    assert registry.recognizes(ContractId("core.batch-plan", 1))
    assert registry.recognizes(ContractId("core.batch-result", 1))
    # The v6 registry does not know the CLI records (contract.rs:734-741).
    for contract in (
        ContractId("core.cli-output", 1),
        ContractId("core.batch-plan", 1),
        ContractId("core.batch-result", 1),
    ):
        assert not ContractRegistry(6).recognizes(contract)


def test_protocol_message_is_registered_as_transport_only():
    registry = ContractRegistry(7)
    descriptor = registry.descriptor(ContractId("core.protocol-message", 1))
    assert descriptor is not None
    assert descriptor[2].value == "Transport"


def test_contract_id_validation():
    with pytest.raises(ProtocolError) as caught:
        ContractId("Core.Bad", 1)
    assert caught.value.code == "core.protocol.invalid-value@1"
    with pytest.raises(ProtocolError):
        ContractId("core.bad", 0)
    assert ContractId("core.diagnostic", 1).schema() == "core.diagnostic@1"


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7])
def test_error_code_registry_counts(version):
    codes = ErrorCodeRegistry(version).codes()
    assert len(codes) == ERROR_COUNTS[version]
    # Sorted and unique (error_registry.rs:1700-1716).
    for left, right in zip(codes, codes[1:]):
        assert left.code < right.code


def test_error_registry_growth_and_specific_codes():
    # error_registry.rs:1757-1774.
    assert not ErrorCodeRegistry(1).contains("core.source.patch-base-mismatch@1")
    assert ErrorCodeRegistry(2).contains("core.source.patch-base-mismatch@1")
    assert not ErrorCodeRegistry(2).contains("core.materialization.unrepresentable@1")
    assert ErrorCodeRegistry(3).contains("core.materialization.unrepresentable@1")
    assert not ErrorCodeRegistry(4).contains("yaml.parse.syntax@1")
    assert ErrorCodeRegistry(5).contains("yaml.parse.syntax@1")
    assert not ErrorCodeRegistry(5).contains("ini.profile.encoding@1")
    assert ErrorCodeRegistry(6).contains("ini.profile.encoding@1")
    assert not ErrorCodeRegistry(6).contains("cli.data.io@1")
    assert ErrorCodeRegistry(7).contains("cli.data.io@1")
    assert ErrorCodeRegistry(7).contains("cli.write.target-is-directory@1")
    # The 0.13.0 registration (audit finding F3) exists only in v7.
    assert not ErrorCodeRegistry(6).contains("json.projection.incomplete-document@1")
    assert ErrorCodeRegistry(7).contains("json.projection.incomplete-document@1")


def test_error_code_descriptor_fields():
    descriptor = ErrorCodeRegistry(7).descriptor("cli.data.io@1")
    assert descriptor is not None
    assert descriptor.code == "cli.data.io@1"
    assert descriptor.category.value == "Encoding"
    assert descriptor.introduced == "0.12.0"
    assert descriptor.description


def test_protocol_and_graph_codes_are_registered():
    # The v1 registry registers every core.protocol.* code
    # (error_registry.rs:75-139); the v5 registry adds the graph/PGCE codes
    # (error_registry.rs:692-725).
    registry = ErrorCodeRegistry(1)
    for code in (
        "core.protocol.invalid-json@1",
        "core.protocol.non-canonical-json@1",
        "core.protocol.unknown-contract@1",
        "core.protocol.resource-limit@1",
        "core.protocol.process-local-handle@1",
    ):
        assert registry.contains(code)
    v5 = ErrorCodeRegistry(5)
    for code in (
        "core.graph.invalid@1",
        "core.graph.resource-limit@1",
        "core.pgce.invalid@1",
        "core.pgce.non-canonical@1",
        "core.pgce.resource-limit@1",
        "core.pgce.unsupported-version@1",
    ):
        assert v5.contains(code)


def test_pvce_codes_are_codec_emitted_but_not_registry_registered():
    # The `core.pvce.*@1` codes are the codec's StableFailure diagnostic
    # codes (crates/consema-pvce/src/lib.rs:1062-1087); the error-code
    # registry does NOT register them (error_registry.rs has no core.pvce.*
    # entry), so Diagnostic construction cannot use them.
    registry = ErrorCodeRegistry(7)
    for code in (
        "core.pvce.invalid-magic@1",
        "core.pvce.non-canonical-varint@1",
        "core.pvce.expected-core@1",
        "core.pvce.nested-extended@1",
    ):
        assert not registry.contains(code)


def test_error_code_manifest_round_trips_and_validates():
    for version in (1, 5, 7):
        manifest = error_code_manifest_value(version)
        validate_error_code_manifest_value(manifest)


def test_error_code_manifest_is_strictly_validated():
    manifest = error_code_manifest_value(7)
    # A reordered error_codes list must fail the sortedness check. Build a
    # broken copy by swapping the first two records.
    from consema.core import PortableValue

    fields = manifest.as_object()
    codes = list(fields[1][1].as_sequence())
    broken = PortableValue.object(
        [
            ("schema", fields[0][1]),
            ("error_codes", PortableValue.sequence([codes[1], codes[0]] + codes[2:])),
        ]
    )
    with pytest.raises(ProtocolError) as caught:
        validate_error_code_manifest_value(broken)
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def test_error_code_descriptor_validation_rejects_unknown_codes():
    with pytest.raises(ProtocolError) as caught:
        ErrorCodeRegistry(7).validate("example.not-registered@1")
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE
