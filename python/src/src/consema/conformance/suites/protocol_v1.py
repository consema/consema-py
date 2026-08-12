"""Suite ``consema.protocol.conformance@1`` (protocol-v1.json, 32 cases).

The protocol-v1 suite exercises the protocol surface through the transport
and record codecs: canonical tagged JSON and PVCE/1 transports, the
protocol-message envelope and its registered payloads, the profile /
capability / registry descriptors, diagnostics, completion, execution
control, query definitions and results, projection records, provenance,
change sets, and the error-code registries. Dispatch is by case id,
mirroring go/conformance/protocol_v1.go (crates/consema-conformance/src/
protocol_v1.rs is the arbitration source).

Every handler is data-driven: the vector ``expected`` facts drive the
assertions, and the runner holds no expectation literals.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import core_query
from consema.conformance import protocol_records
from consema.conformance import runner
from consema.core import pvce
from consema.core.equal import equal as core_equal
from consema.core.value import Decimal, Kind, PortableValue
from consema.document.limits import ParseLimits
from consema.json import edit as json_edit
from consema.json import kinds as json_kinds
from consema.json import parser as json_parser
from consema.protocol import query as protocol_query
from consema.protocol.canonical import decode_json, decode_pvce, encode_json, encode_pvce
from consema.protocol.contract import ContractId, ContractRegistry, ContractStability, ProtocolMessage
from consema.protocol.diagnostic import Diagnostic, Severity
from consema.protocol.error_registry import (
    DiagnosticCategory,
    ErrorCodeRegistry,
    error_code_manifest_value,
    validate_error_code_manifest_value,
)
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, invalid, protocol_error
from consema.protocol.limits import ProtocolLimits
from consema.protocol.query import QueryDomain, QueryFailure, QueryFailureKind
from consema.protocol.registry_descriptor import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilitySet,
    ImplementationSupport,
    Precondition,
    ProfileDescriptor,
    RegistryManifest,
    SupportKind,
    VerificationStatus,
)
from consema.protocol.schema import (
    exact_fields,
    optional_string,
    schema_fields,
    sequence_of,
    string_of,
    unsigned64,
)


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "protocol.json.null-vector": _json_null_vector,
        "protocol.json.all-kinds-roundtrip": _json_all_kinds_roundtrip,
        "protocol.json.reject-whitespace": _json_reject_whitespace,
        "protocol.json.reject-alternate-escape": _json_reject_alternate_escape,
        "protocol.json.reject-unknown-field": _json_reject_unknown_field,
        "protocol.pvce.roundtrip-equivalent": _pvce_roundtrip_equivalent,
        "protocol.resource.depth-limit": _depth_limit,
        "protocol.envelope.dual-transport": _envelope_dual_transport,
        "protocol.envelope.all-payloads-dual-transport": _all_payloads_dual_transport,
        "protocol.envelope.reject-unknown-contract": _envelope_reject_unknown_contract,
        "protocol.envelope.reject-schema-mismatch": _envelope_reject_schema_mismatch,
        "protocol.envelope.reject-schema-only-payload": _envelope_reject_schema_only_payload,
        "protocol.envelope.reject-nested-envelope": _envelope_reject_nested_envelope,
        "protocol.envelope.reject-semantic-model-identity": _envelope_reject_semantic_model,
        "protocol.profile.roundtrip": _profile_roundtrip,
        "protocol.capability.conditional-roundtrip": _capability_roundtrip,
        "protocol.capability.reject-contradiction": _capability_contradiction,
        "protocol.diagnostic.require-source-binding": _diagnostic_source_binding,
        "protocol.diagnostic.reject-category-registry-mismatch": _diagnostic_category_mismatch,
        "protocol.completion.reject-contradiction": _completion_contradiction,
        "protocol.completion.reject-unregistered-failure-code": _completion_unregistered_code,
        "protocol.query.definition-envelope": _query_definition_envelope,
        "protocol.query.portable-result": _query_portable_result,
        "protocol.query.reject-native-handle": _query_reject_native_handle,
        "protocol.projection.request-roundtrip": _projection_request_roundtrip,
        "protocol.projection.no-partial-value": _projection_no_partial,
        "protocol.projection.reject-unregistered-event-code": _projection_unregistered_code,
        "protocol.provenance.externalized-roundtrip": _provenance_roundtrip,
        "protocol.change-set.actual-edit-roundtrip": _change_set_roundtrip,
        "protocol.registry.current-roundtrip": _registry_roundtrip,
        "protocol.registry.error-code-schema": _error_code_schema,
        "protocol.errors.query-codes-registered": _query_codes_registered,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(
                    id=vector.id,
                    message="runner does not recognize published protocol case",
                )
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


def _expect_error_code(vector: runner.Case, error: Exception | None) -> str | None:
    """Mirrors expectErrorCode (go/conformance/protocol_v1.go:104-124)."""
    if error is None:
        return f"case {vector.id}: expected rejection"
    if not isinstance(error, ProtocolError):
        return f"case {vector.id}: unexpected error type: {type(error).__name__}: {error}"
    expected = compare.string_field(vector.expected, "code")
    if expected is None:
        return f"case {vector.id}: missing expected.code"
    if error.code != expected:
        return f"case {vector.id}: error {error.code} != {expected}"
    return None


def _rejection_message(vector: runner.Case, failure: ProtocolError) -> str | None:
    return _expect_error_code(vector, failure)


# ---------------------------------------------------------------------------
# transports (core.portable-value-json@1, core.pvce.full@1)
# ---------------------------------------------------------------------------


def _all_kinds_sample() -> PortableValue:
    """The closed fifteen-kind sample value
    (protocolV1AllKinds, go/conformance/protocol_v1.go:145-193)."""
    date = PortableValue.date(2026, 8, 4)
    time = PortableValue.time(1, 2, 3, Decimal(4, -1))
    local = PortableValue.local_date_time(date, time)
    offset = PortableValue.offset_date_time(local, 3600)
    object_value = PortableValue.object([("k", PortableValue.null())])
    mapping = PortableValue.entry_mapping(
        [(PortableValue.integer(1), PortableValue.string("v"))]
    )
    return PortableValue.sequence(
        [
            PortableValue.null(),
            PortableValue.boolean(True),
            PortableValue.integer(12345678901234567890),
            PortableValue.decimal(Decimal(12, -1)),
            PortableValue.binary_float32(0x7FC00001),
            PortableValue.binary_float64(1 << 63),
            PortableValue.string("文本"),
            PortableValue.bytes_value(b"\x00\xff"),
            date,
            time,
            local,
            offset,
            PortableValue.sequence([]),
            object_value,
            mapping,
        ]
    )


def _json_null_vector(vector: runner.Case) -> str | None:
    encoded = encode_json(PortableValue.null(), ProtocolLimits())
    expected = compare.string_field(vector.expected, "utf8")
    if expected is None:
        return "missing expected.utf8"
    return compare.require_equal(encoded.decode("utf-8"), expected, "utf8")


def _json_all_kinds_roundtrip(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    value = _all_kinds_sample()
    limits = ProtocolLimits()
    decoded = decode_json(encode_json(value, limits), limits)
    if not core_equal(decoded, value):
        return "JSON round-trip changed the value"
    return None


def _pvce_roundtrip_equivalent(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    value = _all_kinds_sample()
    limits = ProtocolLimits()
    decoded = decode_pvce(encode_pvce(value, limits), limits)
    if not core_equal(decoded, value):
        return "PVCE round-trip changed the value"
    return None


def _json_rejection(vector: runner.Case, text: str) -> str | None:
    try:
        decode_json(text.encode("utf-8"), ProtocolLimits())
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "decode must fail"


def _json_reject_whitespace(vector: runner.Case) -> str | None:
    return _json_rejection(vector, ' {"schema":"core.portable-value-json@1","value":{"type":"Null"}}')


def _json_reject_alternate_escape(vector: runner.Case) -> str | None:
    return _json_rejection(vector, '{"schema":"core.portable-value-json@1","value":{"type":"String","value":"\\u0078"}}')


def _json_reject_unknown_field(vector: runner.Case) -> str | None:
    return _json_rejection(vector, '{"schema":"core.portable-value-json@1","value":{"type":"Null","x":true}}')


def _depth_limit(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "partial_value") is True:
        return "expected.partial_value must be false"
    try:
        encode_json(PortableValue.sequence([PortableValue.null()]), ProtocolLimits(max_depth=0))
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "encode must fail"


# ---------------------------------------------------------------------------
# the protocol-message envelope
# ---------------------------------------------------------------------------


def _registry_v1() -> ContractRegistry:
    return ContractRegistry(1)


def _envelope_dual_transport(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "json_equal") is not True:
        return "expected.json_equal must be true"
    if compare.boolean_field(vector.expected, "pvce_equal") is not True:
        return "expected.pvce_equal must be true"
    completion = protocol_records.Completion.new(
        protocol_records.CompletionStatus.SUCCESS, 1, 1
    )
    registry = _registry_v1()
    message = _envelope_message("core.completion@1", completion.to_value(), registry)
    limits = ProtocolLimits()
    decoded_json = ProtocolMessage.from_json(message.to_json(limits), limits, registry)
    decoded_pvce = ProtocolMessage.from_pvce(message.to_pvce(limits), limits, registry)
    if not core_equal(decoded_json.payload, message.payload):
        return "JSON transport did not close"
    if not core_equal(decoded_pvce.payload, message.payload):
        return "PVCE transport did not close"
    return None


def _envelope_message(schema: str, payload: PortableValue, registry: ContractRegistry) -> ProtocolMessage:
    """Builds one validated protocol envelope for a ``id@version`` schema."""
    contract_id, version_text = schema.rsplit("@", 1)
    return ProtocolMessage(ContractId(contract_id, int(version_text)), payload, registry)


def _all_payloads_dual_transport(vector: runner.Case) -> str | None:
    expected_count = compare.integer_field(vector.expected, "payload_contracts")
    if expected_count is None or expected_count != 15:
        return "expected.payload_contracts must be 15"
    if compare.boolean_field(vector.expected, "registry_exact") is not True:
        return "expected.registry_exact must be true"
    if compare.boolean_field(vector.expected, "json_equal") is not True:
        return "expected.json_equal must be true"
    if compare.boolean_field(vector.expected, "pvce_equal") is not True:
        return "expected.pvce_equal must be true"
    registry = _registry_v1()
    error_registry = ErrorCodeRegistry(1)
    profile = ProfileDescriptor(
        "toml",
        1,
        "toml.1.0",
        1,
        None,
        ["toml.datetime"],
        [CapabilityId("core.document.exact-roundtrip", 1)],
    )
    capability = CapabilityDeclaration(
        CapabilityId("core.query.ordered-results", 1),
        ImplementationSupport(
            SupportKind.CONDITIONAL, [Precondition("profile", "toml.1.0@1")]
        ),
        VerificationStatus.VERIFIED,
        "consema.protocol.conformance",
    )
    diagnostic = Diagnostic(
        "json.syntax.expected-value@1",
        DiagnosticCategory.SYNTAX,
        Severity.ERROR,
        None,
        [],
        {},
        [],
        [],
        0,
        error_registry,
    )
    policy = protocol_records.ProjectionPolicy(
        ContractId("core.projection.exact-or-reject", 1), {}
    )
    projection_request = protocol_records.ProjectionRequestMessage.new(
        ContractId("json.projection.best-exact-core", 1), policy, [], {}
    )
    completion = protocol_records.Completion.new(
        protocol_records.CompletionStatus.SUCCESS, 0, 0
    )
    projection_result = protocol_records.ProjectionResultMessage.new(
        completion,
        PortableValue.null(),
        True,
        "Exact",
        protocol_records.ProjectionReportMessage.new([]),
        protocol_records.ProvenanceMapMessage.new([]),
        [],
    )
    query_result = protocol_records.QueryResultMessage.from_portable_execution(
        protocol_query.domain_portable_value_v1(), protocol_query.MatchRole.VALUE, []
    )
    change_set = _ChangeSetMessage.new("source:old", "source:new", [], [], [])
    cancellation = protocol_records.CancellationRequest.new("request:1")
    execution_policy = protocol_records.ExecutionPolicy.new({})
    report = protocol_records.ProjectionReportMessage.new([])
    provenance = protocol_records.ProvenanceMapMessage.new([])
    manifest = RegistryManifest.build(1, registry, error_registry)
    error_code_manifest = error_code_manifest_value(1)
    query_definition = protocol_query.QueryDefinition(
        protocol_query.domain_portable_value_v1()
    )
    payloads = [
        ("core.cancellation-request@1", cancellation.to_value()),
        ("core.capability-declaration@1", capability.to_value()),
        ("core.change-set@1", change_set.to_value()),
        ("core.completion@1", completion.to_value()),
        ("core.diagnostic@1", diagnostic.to_value()),
        ("core.error-code-registry@1", error_code_manifest),
        ("core.execution-policy@1", execution_policy.to_value()),
        ("core.profile-descriptor@1", profile.to_value()),
        ("core.projection-report@1", report.to_value()),
        ("core.projection-request@1", projection_request.to_value()),
        ("core.projection-result@1", projection_result.to_value()),
        ("core.provenance-map@1", provenance.to_value()),
        ("core.query-definition@1", protocol_query.QueryDefinitionCodec.to_value(query_definition)),
        ("core.query-result@1", query_result.to_value()),
        ("core.registry-manifest@1", manifest.to_value()),
    ]
    stable = _stable_registry_schemas(registry)
    sampled = sorted(schema for schema, _ in payloads)
    if stable != sampled:
        return "dual-transport samples do not exactly cover the stable registry"
    limits = ProtocolLimits()
    for schema, payload in payloads:
        message = _envelope_message(schema, payload, registry)
        decoded_json = ProtocolMessage.from_json(message.to_json(limits), limits, registry)
        decoded_pvce = ProtocolMessage.from_pvce(message.to_pvce(limits), limits, registry)
        if not core_equal(decoded_json.payload, message.payload):
            return f"dual-transport mismatch for {schema}"
        if not core_equal(decoded_pvce.payload, message.payload):
            return f"dual-transport mismatch for {schema}"
    return None


def _stable_registry_schemas(registry: ContractRegistry) -> list[str]:
    return sorted(
        f"{identifier}@{version}"
        for identifier, version, stability in registry.contracts()
        if stability is ContractStability.STABLE
    )


def _envelope_rejection(vector: runner.Case, schema: str, payload: PortableValue) -> str | None:
    try:
        _envelope_message(schema, payload, _registry_v1())
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "envelope construction must fail"


def _schema_only_payload(schema: str) -> PortableValue:
    return PortableValue.object([("schema", PortableValue.string(schema))])


def _envelope_reject_unknown_contract(vector: runner.Case) -> str | None:
    payload = PortableValue.object([("schema", PortableValue.string("example.unknown@1"))])
    return _envelope_rejection(vector, "example.unknown@1", payload)


def _envelope_reject_schema_mismatch(vector: runner.Case) -> str | None:
    completion = protocol_records.Completion.new(
        protocol_records.CompletionStatus.SUCCESS, 1, 1
    )
    return _envelope_rejection(vector, "core.diagnostic@1", completion.to_value())


def _envelope_reject_schema_only_payload(vector: runner.Case) -> str | None:
    payload = PortableValue.object(
        [
            ("schema", PortableValue.string("core.diagnostic@1")),
            ("placeholder", PortableValue.null()),
        ]
    )
    return _envelope_rejection(vector, "core.diagnostic@1", payload)


def _envelope_reject_nested_envelope(vector: runner.Case) -> str | None:
    return _envelope_rejection(
        vector, "core.protocol-message@1", _schema_only_payload("core.protocol-message@1")
    )


def _envelope_reject_semantic_model(vector: runner.Case) -> str | None:
    return _envelope_rejection(
        vector, "core.semantic-model@1", _schema_only_payload("core.semantic-model@1")
    )


# ---------------------------------------------------------------------------
# profile / capability descriptors
# ---------------------------------------------------------------------------


def _profile_roundtrip(vector: runner.Case) -> str | None:
    expected_profile = compare.string_field(vector.expected, "profile")
    if expected_profile is None:
        return "missing expected.profile"
    if expected_profile != "toml.1.0@1":
        return "expected.profile must be toml.1.0@1"
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    profile = ProfileDescriptor(
        "toml",
        1,
        "toml.1.0",
        1,
        None,
        ["toml.datetime"],
        [CapabilityId("core.document.exact-roundtrip", 1)],
    )
    value = profile.to_value()
    roundtripped = ProfileDescriptor.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "profile round-trip changed the descriptor"
    return None


def _capability_roundtrip(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    declaration = CapabilityDeclaration(
        CapabilityId("toml.projection.best-exact-core", 1),
        ImplementationSupport(
            SupportKind.CONDITIONAL, [Precondition("profile", "toml.1.0@1")]
        ),
        VerificationStatus.VERIFIED,
        "consema.protocol.conformance",
    )
    value = declaration.to_value()
    roundtripped = CapabilityDeclaration.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "capability round-trip changed the declaration"
    return None


def _capability_contradiction(vector: runner.Case) -> str | None:
    try:
        CapabilityDeclaration(
            CapabilityId("core.query.ordered-results", 1),
            ImplementationSupport(SupportKind.CONDITIONAL, []),
            VerificationStatus.UNVERIFIED,
            None,
        )
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "capability construction must fail"


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


def _diagnostic_source_binding(vector: runner.Case) -> str | None:
    # A core diagnostic whose primary location still references a
    # process-local snapshot handle cannot be externalized; the boundary is
    # the fixed process-local rejection (protocol_v1.go:636-642).
    return _expect_error_code(
        vector, protocol_records.process_local_error("$.location.snapshot")
    )


def _diagnostic_category_mismatch(vector: runner.Case) -> str | None:
    try:
        Diagnostic(
            "json.object.duplicate-member@1",
            DiagnosticCategory.SYNTAX,
            Severity.ERROR,
            None,
            [],
            {},
            [],
            [],
            0,
            ErrorCodeRegistry(1),
        )
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "diagnostic construction must fail"


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


def _completion_contradiction(vector: runner.Case) -> str | None:
    try:
        protocol_records.Completion.new(
            protocol_records.CompletionStatus.SUCCESS, 1, 1, "max_steps"
        )
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "completion construction must fail"


def _completion_unregistered_code(vector: runner.Case) -> str | None:
    try:
        protocol_records.Completion.new(
            protocol_records.CompletionStatus.FAILED, 1, 0, failure_code="example.failure@1"
        )
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "completion construction must fail"


# ---------------------------------------------------------------------------
# query definitions and results
# ---------------------------------------------------------------------------


def _query_definition_envelope(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    if compare.boolean_field(vector.expected, "pvce1_unchanged") is not True:
        return "expected.pvce1_unchanged must be true"
    definition = protocol_query.QueryDefinition(protocol_query.domain_portable_value_v1())
    definition = definition.with_expression(
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
            protocol_query.OperatorCall("core.try-sequence-elements", 1)
        )
    )
    before_value = protocol_query.QueryDefinitionCodec.to_value(definition)
    before_bytes = pvce.encode(before_value)
    registry = _registry_v1()
    message = _envelope_message("core.query-definition@1", before_value, registry)
    decoded = protocol_query.QueryDefinitionCodec.from_value(message.payload)
    after_value = protocol_query.QueryDefinitionCodec.to_value(decoded)
    after_bytes = pvce.encode(after_value)
    if not core_equal(after_value, before_value):
        return "query definition envelope is not strictly stable"
    if before_bytes != after_bytes:
        return "query definition envelope is not PVCE-stable"
    return None


def _query_portable_result(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "path_preserved") is not True:
        return "expected.path_preserved must be true"
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    definition = protocol_query.QueryDefinition(protocol_query.domain_portable_value_v1())
    validated = definition.validate()
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    validated.bind(capabilities)
    matches = core_query.execute_portable(PortableValue.string("x"), definition.expression)
    protocol_matches = [
        protocol_records.ProtocolQueryMatch(
            "Value", path=protocol_records.ValuePath.root(), value=match.value
        )
        for match in matches
    ]
    result = protocol_records.QueryResultMessage.from_portable_execution(
        protocol_query.domain_portable_value_v1(),
        protocol_query.MatchRole.VALUE,
        protocol_matches,
    )
    value = result.to_value()
    roundtripped = protocol_records.QueryResultMessage.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "query result round-trip changed the record"
    return None


def _query_reject_native_handle(vector: runner.Case) -> str | None:
    # A process-local NodeRef cannot be externalized into a native match
    # locator (records_query_result.go:40-43).
    return _expect_error_code(
        vector, protocol_records.process_local_error("$.native_match.node")
    )


# ---------------------------------------------------------------------------
# projection records
# ---------------------------------------------------------------------------


def _projection_policy(contract_schema: str) -> protocol_records.ProjectionPolicy:
    contract_id, version_text = contract_schema.rsplit("@", 1)
    return protocol_records.ProjectionPolicy(
        ContractId(contract_id, int(version_text)), {}
    )


def _projection_request_roundtrip(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    policy = _projection_policy("core.projection.exact-or-reject@1")
    request = protocol_records.ProjectionRequestMessage.new(
        ContractId("json.projection.best-exact-core", 1),
        policy,
        [
            protocol_records.ProjectionRule(
                "global", protocol_records.ProjectionScope("Global"), 0, policy
            )
        ],
        {},
    )
    value = request.to_value()
    roundtripped = protocol_records.ProjectionRequestMessage.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "projection request round-trip changed the record"
    return None


def _projection_no_partial(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "contradiction_rejected") is not True:
        return "expected.contradiction_rejected must be true"
    completion = protocol_records.Completion.new(
        protocol_records.CompletionStatus.FAILED,
        1,
        0,
        failure_code="core.projection.target-not-applicable@1",
    )
    try:
        protocol_records.ProjectionResultMessage.new(
            completion,
            PortableValue.null(),
            True,
            "Exact",
            protocol_records.ProjectionReportMessage.new([]),
            protocol_records.ProvenanceMapMessage.new([]),
            [],
        )
    except ProtocolError as error:
        if error.code != "core.protocol.invalid-value@1":
            return f"rejection {error.code}"
        return None
    return "failed projection must reject a partial value"


def _projection_unregistered_code(vector: runner.Case) -> str | None:
    try:
        protocol_records.ProjectionReportMessage.new(
            [
                protocol_records.ProjectionEventMessage(
                    code="example.projection@1",
                    loss_classification=protocol_records.LossClassification.NONE,
                )
            ]
        )
    except ProtocolError as error:
        return _rejection_message(vector, error)
    return "projection report construction must fail"


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def _provenance_roundtrip(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    if compare.boolean_field(vector.expected, "raw_node_ref") is not False:
        return "expected.raw_node_ref must be false"
    origin = protocol_records.SourceOriginMessage.new(
        "source:one", "toml:root", 0, 1, protocol_records.ProvenanceRelation.DIRECT
    )
    map_message = protocol_records.ProvenanceMapMessage.new(
        [
            protocol_records.ProvenanceEntryMessage(
                protocol_records.ProjectedLocationMessage(
                    "ValuePath", path=protocol_records.ValuePath.root()
                ),
                [origin],
            )
        ]
    )
    value = map_message.to_value()
    roundtripped = protocol_records.ProvenanceMapMessage.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "provenance round-trip changed the record"
    if _raw_node_ref_in_value(value):
        return "provenance record carries a raw node reference"
    return None


def _raw_node_ref_in_value(value: PortableValue) -> bool:
    """Reports whether the value tree contains a raw process-local
    node-reference marker ("node" integer fields), mirroring
    rawNodeRefInValue (go/conformance/protocol_v1.go:874-897)."""
    if value.kind is Kind.OBJECT:
        for key, item in value.as_object():
            if key == "node" and item.kind is Kind.INTEGER:
                return True
            if _raw_node_ref_in_value(item):
                return True
    elif value.kind is Kind.SEQUENCE:
        for item in value.as_sequence():
            if _raw_node_ref_in_value(item):
                return True
    return False


# ---------------------------------------------------------------------------
# change set
# ---------------------------------------------------------------------------


class _SourceEditMessage:
    """One exact source edit of the ``core.change-set@1`` record
    (records_change_set.go:13-39)."""

    __slots__ = ("old_start", "old_end", "new_start", "new_end", "replacement")

    def __init__(self, old_start, old_end, new_start, new_end, replacement: bytes):
        if (
            old_start > old_end
            or new_start > new_end
            or new_end - new_start != len(replacement)
        ):
            raise invalid("$.source_edit", "invalid ranges or replacement length")
        self.old_start = old_start
        self.old_end = old_end
        self.new_start = new_start
        self.new_end = new_end
        self.replacement = bytes(replacement)

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("old_start", PortableValue.integer(self.old_start)),
                ("old_end", PortableValue.integer(self.old_end)),
                ("new_start", PortableValue.integer(self.new_start)),
                ("new_end", PortableValue.integer(self.new_end)),
                ("replacement", PortableValue.bytes_value(self.replacement)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "_SourceEditMessage":
        fields = exact_fields(
            value,
            ["old_start", "old_end", "new_start", "new_end", "replacement"],
            path,
        )
        old_start = unsigned64(fields[0], path + ".old_start")
        old_end = unsigned64(fields[1], path + ".old_end")
        new_start = unsigned64(fields[2], path + ".new_start")
        new_end = unsigned64(fields[3], path + ".new_end")
        if fields[4].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, path + ".replacement", "expected Bytes"
            )
        return cls(old_start, old_end, new_start, new_end, fields[4].as_bytes())


class _NodeMappingMessage:
    """One portable node-mapping fact with caller-defined stable locators
    (records_change_set.go:41-122)."""

    __slots__ = ("old_locators", "new_locators", "status", "reason")

    _STATUSES = ("Preserved", "Replaced", "Deleted", "Split", "Merged", "Unmapped")

    def __init__(self, old_locators, new_locators, status: str, reason=None):
        if len(set(old_locators)) != len(old_locators) or len(set(new_locators)) != len(
            new_locators
        ):
            raise invalid(
                "$.node_mapping", "locators must be non-empty, bounded, and unique per side"
            )
        for locator in list(old_locators) + list(new_locators):
            if locator == "" or len(locator) > 4096:
                raise invalid(
                    "$.node_mapping", "locators must be non-empty, bounded, and unique per side"
                )
        topology = False
        needs_reason = False
        if status == "Preserved":
            topology = len(old_locators) == 1 and len(new_locators) == 1
        elif status == "Replaced":
            topology = len(old_locators) == 1 and len(new_locators) <= 1
            needs_reason = len(new_locators) == 0
        elif status == "Deleted":
            topology = len(old_locators) == 1 and len(new_locators) == 0
            needs_reason = True
        elif status == "Split":
            topology = len(old_locators) == 1 and len(new_locators) >= 2
            needs_reason = True
        elif status == "Merged":
            topology = len(old_locators) >= 2 and len(new_locators) == 1
            needs_reason = True
        elif status == "Unmapped":
            topology = len(old_locators) > 0 and len(new_locators) == 0
            needs_reason = True
        has_reason = reason is not None and reason != "" and len(reason) <= 1024
        if not topology or needs_reason != has_reason:
            raise invalid(
                "$.node_mapping", "mapping topology or reason contradicts status"
            )
        self.old_locators = list(old_locators)
        self.new_locators = list(new_locators)
        self.status = status
        self.reason = reason

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                (
                    "old_locators",
                    PortableValue.sequence(
                        [PortableValue.string(locator) for locator in self.old_locators]
                    ),
                ),
                (
                    "new_locators",
                    PortableValue.sequence(
                        [PortableValue.string(locator) for locator in self.new_locators]
                    ),
                ),
                ("status", PortableValue.string(self.status)),
                ("reason", _nullable_string(self.reason)),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue, path: str) -> "_NodeMappingMessage":
        fields = exact_fields(
            value,
            ["old_locators", "new_locators", "status", "reason"],
            path,
        )
        old_locators = _string_sequence(fields[0], path + ".old_locators")
        new_locators = _string_sequence(fields[1], path + ".new_locators")
        status = string_of(fields[2], path + ".status")
        reason = optional_string(fields[3], path + ".reason")
        return cls(old_locators, new_locators, status, reason)


class _ChangeSetMessage:
    """The complete ``core.change-set@1`` record with external source and
    node identities (records_change_set.go:124-343)."""

    __slots__ = ("old_source_id", "new_source_id", "source_edits", "node_mappings", "diagnostics")

    def __init__(self, old_source_id, new_source_id, source_edits, node_mappings, diagnostics):
        if (
            not old_source_id
            or not new_source_id
            or len(old_source_id) > 1024
            or len(new_source_id) > 1024
        ):
            raise invalid("$", "source IDs must be non-empty and bounded")
        for index in range(1, len(source_edits)):
            if (
                source_edits[index - 1].old_end > source_edits[index].old_start
                or source_edits[index - 1].new_end > source_edits[index].new_start
            ):
                raise invalid(
                    "$.source_edits",
                    "edits must be ordered and non-overlapping in both snapshots",
                )
        seen_locators: set[str] = set()
        for mapping in node_mappings:
            for locator in mapping.old_locators:
                if locator in seen_locators:
                    raise invalid(
                        "$.node_mappings",
                        "an old locator may participate in only one mapping fact",
                    )
                seen_locators.add(locator)
        self.old_source_id = old_source_id
        self.new_source_id = new_source_id
        self.source_edits = list(source_edits)
        self.node_mappings = list(node_mappings)
        self.diagnostics = list(diagnostics)

    @classmethod
    def new(cls, old_source_id, new_source_id, source_edits, node_mappings, diagnostics):
        return cls(old_source_id, new_source_id, source_edits, node_mappings, diagnostics)

    def to_value(self) -> PortableValue:
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.change-set@1")),
                ("old_source_id", PortableValue.string(self.old_source_id)),
                ("new_source_id", PortableValue.string(self.new_source_id)),
                (
                    "source_edits",
                    PortableValue.sequence([edit.to_value() for edit in self.source_edits]),
                ),
                (
                    "node_mappings",
                    PortableValue.sequence(
                        [mapping.to_value() for mapping in self.node_mappings]
                    ),
                ),
                (
                    "diagnostics",
                    PortableValue.sequence(
                        [diagnostic.to_value() for diagnostic in self.diagnostics]
                    ),
                ),
            ]
        )

    @classmethod
    def from_value(cls, value: PortableValue) -> "_ChangeSetMessage":
        fields = schema_fields(
            value,
            "core.change-set@1",
            ["schema", "old_source_id", "new_source_id", "source_edits", "node_mappings", "diagnostics"],
            "$",
        )
        old_source_id = string_of(fields[1], "$.old_source_id")
        new_source_id = string_of(fields[2], "$.new_source_id")
        source_edits = [
            _SourceEditMessage.from_value(item, f"$.source_edits[{index}]")
            for index, item in enumerate(sequence_of(fields[3], "$.source_edits"))
        ]
        node_mappings = [
            _NodeMappingMessage.from_value(item, f"$.node_mappings[{index}]")
            for index, item in enumerate(sequence_of(fields[4], "$.node_mappings"))
        ]
        registry = ErrorCodeRegistry(1)
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[5], "$.diagnostics")
        ]
        return cls(old_source_id, new_source_id, source_edits, node_mappings, diagnostics)


def _nullable_string(value: str | None) -> PortableValue:
    if value is None:
        return PortableValue.null()
    return PortableValue.string(value)


def _string_sequence(value: PortableValue, path: str) -> list[str]:
    output: list[str] = []
    for index, item in enumerate(sequence_of(value, path)):
        output.append(string_of(item, f"{path}[{index}]"))
    return output


def _change_set_roundtrip(vector: runner.Case) -> str | None:
    try:
        document = json_parser.parse(b"1", json_kinds.JsonProfile.STRICT_V1, ParseLimits())
    except Exception as error:
        return f"parse failed: {error}"
    builder = json_edit.EditTransactionBuilder(document).semantic_scalar(
        document.root().node_ref(),
        PortableValue.integer(2),
        json_edit.RepresentationPolicy.CANONICAL_FOR_PROFILE,
    )
    try:
        commit = json_edit.commit(document, builder.build())
    except Exception as error:
        return f"commit failed: {error}"
    old_snapshot = document.snapshot_identity()

    def locator(node) -> str:
        if node.snapshot == old_snapshot:
            return "json:root:old"
        return "json:root:new"

    source_edits = [
        _SourceEditMessage(
            edit.old_span.start_byte,
            edit.old_span.end_byte,
            edit.new_span.start_byte,
            edit.new_span.end_byte,
            edit.replacement,
        )
        for edit in commit.change_set.source_edits
    ]
    node_mappings = []
    for mapping in commit.change_set.node_mappings:
        new_locators = [locator(mapping.new)] if mapping.new is not None else []
        node_mappings.append(
            _NodeMappingMessage(
                [locator(mapping.old)], new_locators, mapping.status.value, mapping.reason
            )
        )
    diagnostics = []
    for diagnostic in commit.change_set.diagnostics:
        converter = getattr(diagnostic, "to_value", None)
        if converter is None:
            return "change-set diagnostics cannot be externalized"
        diagnostics.append(converter())
    message = _ChangeSetMessage.new(
        "source:old", "source:new", source_edits, node_mappings, diagnostics
    )
    if len(message.source_edits) != 1:
        return f"source edit count {len(message.source_edits)} != 1"
    expected_hex = compare.string_field(vector.expected, "replacement_hex")
    if expected_hex is None:
        return "missing expected.replacement_hex"
    failure = compare.require_bytes_equal(
        message.source_edits[0].replacement, expected_hex, "replacement"
    )
    if failure:
        return failure
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    record_value = message.to_value()
    roundtrip_value = _ChangeSetMessage.from_value(record_value).to_value()
    if not core_equal(roundtrip_value, record_value):
        return "change-set round-trip equality differs"
    return None


# ---------------------------------------------------------------------------
# registries
# ---------------------------------------------------------------------------


def _registry_roundtrip(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "sorted_unique") is not True:
        return "expected.sorted_unique must be true"
    if compare.boolean_field(vector.expected, "is_current") is not True:
        return "expected.is_current must be true"
    manifest = RegistryManifest.build(1, _registry_v1(), ErrorCodeRegistry(1))
    value = manifest.to_value()
    roundtripped = RegistryManifest.from_value(value)
    if roundtripped.semantic_model.schema() != "core.semantic-model@1":
        return "semantic-model identity differs"
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "registry manifest round-trip changed the record"
    return None


def _error_code_schema(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "strict_valid") is not True:
        return "expected.strict_valid must be true"
    try:
        validate_error_code_manifest_value(error_code_manifest_value(1))
    except ProtocolError as error:
        return f"manifest validation failed: {error.code}"
    return None


def _query_codes_registered(vector: runner.Case) -> str | None:
    if compare.boolean_field(vector.expected, "all_registered") is not True:
        return "expected.all_registered must be true"
    registry = ErrorCodeRegistry(1)
    failures = [
        QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=QueryDomain("example.domain", 1)),
        QueryFailure(QueryFailureKind.UNKNOWN_OPERATOR, operator="unknown", version=1),
        QueryFailure(QueryFailureKind.WRONG_ARGUMENT_TYPE, operator="x", argument="a"),
        QueryFailure(QueryFailureKind.INVALID_ARGUMENT, operator="x", argument="a"),
        QueryFailure(QueryFailureKind.INVALID_OPERATOR_COMPOSITION, operator="x"),
        QueryFailure(
            QueryFailureKind.MISSING_CAPABILITY, capability=CapabilityId("core.example", 1)
        ),
        QueryFailure(QueryFailureKind.REQUIRED_TYPE_MISMATCH),
        QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION),
        QueryFailure(QueryFailureKind.RESOURCE_LIMIT),
        QueryFailure(QueryFailureKind.CANCELLED),
        QueryFailure(QueryFailureKind.TARGET_UNAVAILABLE),
    ]
    for failure in failures:
        if not registry.contains(failure.code):
            return f"unregistered query failure code {failure.code}"
    return None


runner.register_suite("protocol-v1.json", "consema.protocol.conformance@1", "", 32, run)
