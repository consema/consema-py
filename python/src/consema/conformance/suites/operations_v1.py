"""Suite ``consema.operations.conformance@1`` (operations-v1.json, 35
cases): registry manifests, protocol-v3 dual transport, JSON/TOML
materialization and structural edits with dry-run proof/patch facts,
conflict/security matrices, the operation registries, and the four convert
cases. Dispatch is by case id, mirroring go/conformance/operations_v1.go
and its JSON (v1_json_face.go), TOML (v1_toml_face.go), and convert
(convert_face.go) faces exactly.
"""

from __future__ import annotations

import hashlib

from consema.conformance import compare
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.document.edit_plan import EditPlanSourceId
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import AssociationPlacement
from consema.document.untouched_proof import UntouchedByteProofError
from consema.convert import ConversionFailure, convert_json, convert_toml
from consema.json import edit as json_edit
from consema.json import errors as json_errors
from consema.json import kinds as json_kinds
from consema.json import materialization as json_materialization
from consema.json import parser as json_parser
from consema.json import projection as json_projection
from consema.json.operation_registry import (
    format_operation_registry as json_format_operation_registry,
)
from consema.protocol.contract import ContractId, ContractRegistry, ProtocolMessage
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import RegistryManifest
from consema.registry import operation_registry
from consema.toml import edits as toml_edits
from consema.toml import errors as toml_errors
from consema.toml import materialization as toml_materialization
from consema.toml import parser as toml_parser
from consema.toml import projection as toml_projection
from consema.toml.document import TomlProfile


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "operations.v1.registry-v3": _registry_v3,
        "operations.v1.protocol-v3-dual-transport": _protocol_v3_dual_transport,
        "operations.v1.operation-registry": _operation_registry,
        "operations.v1.materialize-json-compact": _materialize_json_success,
        "operations.v1.materialize-json-pretty-crlf": _materialize_json_success,
        "operations.v1.materialize-json-entry-mapping-duplicates": _materialize_json_success,
        "operations.v1.materialize-json-nonstring-key-rejected": _materialize_json_failure,
        "operations.v1.materialize-json-float-rejected": _materialize_json_failure,
        "operations.v1.materialize-json-output-limit": _materialize_json_failure,
        "operations.v1.materialization-depth-limit": _materialize_json_failure,
        "operations.v1.materialize-toml-native": _materialize_toml_native,
        "operations.v1.materialize-toml-explicit-mapping": _materialize_toml_mapping,
        "operations.v1.materialize-toml-implicit-mapping-rejected": _materialize_toml_mapping,
        "operations.v1.materialize-toml-null-rejected": _materialize_toml_failure,
        "operations.v1.materialize-toml-output-limit": _materialize_toml_limit,
        "operations.v1.convert-json-to-toml-exact": _convert_json_to_toml,
        "operations.v1.convert-toml-to-json-exact": _convert_toml_to_json,
        "operations.v1.convert-duplicate-json-to-toml-fails": _convert_duplicate_failure,
        "operations.v1.convert-transformed-report": _convert_transformed_report,
        "operations.v1.json-object-insert": _json_object_insert,
        "operations.v1.json-object-remove-duplicate": _json_object_remove,
        "operations.v1.json-array-remove": _json_array_remove,
        "operations.v1.json-conflict-atomic": _json_conflict_atomic,
        "operations.v1.json-dry-run-proof-patch": _json_dry_run,
        "operations.v1.json-structural-matrix": _json_structural_matrix,
        "operations.v1.json-conflict-matrix": _json_conflict_matrix,
        "operations.v1.materialization-security-matrix": _materialization_security_matrix,
        "operations.v1.untouched-proof-tamper": _untouched_proof_tamper,
        "operations.v1.toml-root-insert": _toml_root_insert,
        "operations.v1.toml-inline-rename": _toml_inline_rename,
        "operations.v1.toml-array-remove": _toml_array_remove,
        "operations.v1.toml-conflict-atomic": _toml_conflict_atomic,
        "operations.v1.toml-dry-run-proof-patch": _toml_dry_run,
        "operations.v1.toml-structural-matrix": _toml_structural_matrix,
        "operations.v1.toml-conflict-matrix": _toml_conflict_matrix,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(
                    id=vector.id, message="runner does not recognize published operations v1 case"
                )
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# registry and protocol-v3 data cases
# ---------------------------------------------------------------------------


def _registry_v3(vector: runner.Case) -> str | None:
    v1 = RegistryManifest.build(1, ContractRegistry(1), ErrorCodeRegistry(1))
    v2 = RegistryManifest.build(2, ContractRegistry(2), ErrorCodeRegistry(2))
    v3 = RegistryManifest.build(3, ContractRegistry(3), ErrorCodeRegistry(3))
    contract_count = compare.integer_field(vector.expected, "contract_count")
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    v1_contract_count = compare.integer_field(vector.expected, "v1_contract_count")
    v1_error_code_count = compare.integer_field(vector.expected, "v1_error_code_count")
    v2_contract_count = compare.integer_field(vector.expected, "v2_contract_count")
    v2_error_code_count = compare.integer_field(vector.expected, "v2_error_code_count")
    if None in (contract_count, error_code_count, v1_contract_count,
                v1_error_code_count, v2_contract_count, v2_error_code_count):
        return "missing expected registry facts"
    value = v3.to_value()
    roundtripped = RegistryManifest.from_value(value)
    if (
        v3.semantic_model.version != 3
        or len(v3.contracts) != contract_count
        or len(v3.error_codes) != error_code_count
        or len(v1.contracts) != v1_contract_count
        or len(v1.error_codes) != v1_error_code_count
        or len(v2.contracts) != v2_contract_count
        or len(v2.error_codes) != v2_error_code_count
        or not core_equal(roundtripped.to_value(), value)
    ):
        return "registry manifest facts did not match"
    return None


def _profile_reference_value(id: str, version: int) -> PortableValue:
    return PortableValue.object(
        [
            ("id", PortableValue.string(id)),
            ("version", PortableValue.integer(version)),
        ]
    )


def _digest_of(data: bytes) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string("sha256")),
            ("hex", PortableValue.string(hashlib.sha256(data).hexdigest())),
        ]
    )


def _empty_report_value(schema: str) -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string(schema)),
            ("events", PortableValue.sequence(())),
        ]
    )


def _conversion_report_payload() -> PortableValue:
    projection_report = _empty_report_value("core.projection-report@1")
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.conversion-report@1")),
            ("source_profile", _profile_reference_value("toml.1.0", 1)),
            ("target_profile", _profile_reference_value("json.strict", 1)),
            ("projection_fidelity", PortableValue.string("Exact")),
            ("projection_report", projection_report),
            ("materialization_fidelity", PortableValue.string("Exact")),
            ("materialization_report", _empty_report_value("core.materialization-report@1")),
            ("overall_fidelity", PortableValue.string("Exact")),
        ]
    )


def _edit_plan_payload() -> PortableValue:
    digest = _digest_of(b"unchanged")
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.edit-plan@1")),
            ("source_id", PortableValue.string("source:one")),
            ("base_digest", digest),
            ("profile", _profile_reference_value("json.strict", 1)),
            ("operations", PortableValue.sequence(())),
            ("replacements", PortableValue.sequence(())),
            ("target_digest", digest),
            ("report", PortableValue.sequence(())),
        ]
    )


def _format_operation_registry_payload() -> PortableValue:
    registry = json_format_operation_registry(json_kinds.JsonProfile.STRICT_V1)
    profile = registry.profile
    operations = []
    for descriptor in registry.operations:
        operation_id, operation_version = _split_versioned_id(descriptor.to_string())
        target_role_id, target_role_version = _split_versioned_id(descriptor.target_role)
        arguments = []
        for argument in descriptor.arguments:
            arguments.append(
                PortableValue.object(
                    [
                        ("name", PortableValue.string(argument.name)),
                        ("kind", PortableValue.string(argument.kind.value)),
                        ("required", PortableValue.boolean(argument.required)),
                    ]
                )
            )
        operations.append(
            PortableValue.object(
                [
                    ("operation", _profile_reference_value(operation_id, operation_version)),
                    ("target_role", _profile_reference_value(target_role_id, target_role_version)),
                    ("arguments", PortableValue.sequence(tuple(arguments))),
                    ("support", PortableValue.string(descriptor.support.value)),
                ]
            )
        )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.format-operation-registry@1")),
            ("profile", _profile_reference_value(profile.id, profile.version)),
            ("operations", PortableValue.sequence(tuple(operations))),
        ]
    )


def _split_versioned_id(text: str) -> tuple[str, int]:
    index = text.rfind("@")
    if index < 0:
        return text, 1
    try:
        return text[:index], int(text[index + 1:])
    except ValueError:
        return text, 1


def _materialization_provenance_map_payload() -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-provenance-map@1")),
            ("entries", PortableValue.sequence(())),
        ]
    )


def _materialization_report_payload() -> PortableValue:
    return _empty_report_value("core.materialization-report@1")


def _materialization_request_payload() -> PortableValue:
    request = MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("json.canonical-compact", 1),
    ).with_newline(NewlinePolicy.NONE)
    limits = request.limits
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-request@1")),
            ("target_profile", _profile_reference_value("json.strict", 1)),
            ("style", _profile_reference_value("json.canonical-compact", 1)),
            ("encoding", PortableValue.string("utf-8")),
            ("newline", PortableValue.string("None")),
            ("mapping_policy", PortableValue.string("RequireObject")),
            ("representability", PortableValue.string("ExactOnly")),
            (
                "limits",
                PortableValue.object(
                    [
                        ("max_input_nodes", PortableValue.integer(limits.max_input_nodes)),
                        ("max_output_bytes", PortableValue.integer(limits.max_output_bytes)),
                        ("max_depth", PortableValue.integer(limits.max_depth)),
                        ("max_report_entries", PortableValue.integer(limits.max_report_entries)),
                        ("max_provenance_entries", PortableValue.integer(limits.max_provenance_entries)),
                    ]
                ),
            ),
        ]
    )


def _materialization_result_payload() -> PortableValue:
    failure = PortableValue.object(
        [
            ("kind", PortableValue.string("UnsupportedStyle")),
            ("code", PortableValue.string("core.materialization.unsupported-style@1")),
        ]
    )
    outcome = PortableValue.object(
        [
            ("kind", PortableValue.string("Failed")),
            ("failure", failure),
            ("report", _empty_report_value("core.materialization-report@1")),
            ("analyzed_input_paths", PortableValue.sequence(())),
        ]
    )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-result@1")),
            ("target_profile", _profile_reference_value("json.strict", 1)),
            ("outcome", outcome),
        ]
    )


def _protocol_v3_dual_transport(vector: runner.Case) -> str | None:
    payloads = [
        ("core.conversion-report", _conversion_report_payload()),
        ("core.edit-plan", _edit_plan_payload()),
        ("core.format-operation-registry", _format_operation_registry_payload()),
        ("core.materialization-provenance-map", _materialization_provenance_map_payload()),
        ("core.materialization-report", _materialization_report_payload()),
        ("core.materialization-result", _materialization_result_payload()),
        ("core.materialization-request", _materialization_request_payload()),
    ]
    new_payload_count = compare.integer_field(vector.expected, "new_payload_count")
    json_equal = compare.boolean_field(vector.expected, "json_equal")
    pvce_equal = compare.boolean_field(vector.expected, "pvce_equal")
    if new_payload_count is None or json_equal is None or pvce_equal is None:
        return "missing expected facts"
    registry = ContractRegistry(3)
    limits = ProtocolLimits()
    json_closed = True
    pvce_closed = True
    for payload_id, payload in payloads:
        contract = ContractId(payload_id, 1)
        try:
            message = ProtocolMessage(contract, payload, registry)
            json_bytes = message.to_json(limits)
            decoded_json = ProtocolMessage.from_json(json_bytes, limits, registry)
            json_closed = json_closed and core_equal(decoded_json.payload, message.payload)
            pvce_bytes = message.to_pvce(limits)
            decoded_pvce = ProtocolMessage.from_pvce(pvce_bytes, limits, registry)
            pvce_closed = pvce_closed and core_equal(decoded_pvce.payload, message.payload)
        except Exception as error:  # noqa: BLE001 — vector boundary
            return f"{payload_id} transport: {error}"
    if len(payloads) != new_payload_count or json_closed != json_equal or pvce_closed != pvce_equal:
        return "protocol dual transport did not close"
    return None


def _operation_registry(vector: runner.Case) -> str | None:
    json_count = compare.integer_field(vector.expected, "json_operation_count")
    toml_count = compare.integer_field(vector.expected, "toml_operation_count")
    required_json = compare.string_field(vector.expected, "required_json")
    required_toml = compare.string_field(vector.expected, "required_toml")
    if json_count is None or toml_count is None or required_json is None or required_toml is None:
        return "missing expected facts"
    json_registry = operation_registry(ProfileId.new("json.strict", 1))
    toml_registry = operation_registry(ProfileId.new("toml.1.0", 1))
    if json_registry is None or toml_registry is None:
        return "operation registry unavailable"
    json_ids = [operation.id for operation in json_registry.operations()]
    toml_ids = [operation.id for operation in toml_registry.operations()]
    if (
        len(json_ids) != json_count
        or len(toml_ids) != toml_count
        or required_json not in json_ids
        or required_toml not in toml_ids
    ):
        return "operation registry facts did not match"
    return None


# ---------------------------------------------------------------------------
# JSON materialization cases
# ---------------------------------------------------------------------------


def _parse_json(source: str, profile):
    return json_parser.parse(source.encode("utf-8"), profile, ParseLimits())


def _json_newline(name: str | None) -> NewlinePolicy:
    if name == "Lf":
        return NewlinePolicy.LF
    if name == "CrLf":
        return NewlinePolicy.CRLF
    return NewlinePolicy.NONE


def _json_materialization_request(style: str, newline: NewlinePolicy) -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new(style, 1),
    ).with_newline(newline)


def _project_best_exact(document):
    request = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    ).build()
    result = json_projection.project(document, request)
    if isinstance(result, json_projection.FailedProjectionAttempt):
        return None, "projection failed"
    return result, None


def _materialize_json_success(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
    projection = compare.string_field(vector.input, "projection")
    if projection not in (None, "BestExactCore"):
        return f"unknown projection {projection}"
    projected, message = _project_best_exact(document)
    if message:
        return message
    style = compare.string_field(vector.input, "style") or "json.canonical-compact"
    newline = _json_newline(compare.string_field(vector.input, "newline"))
    materialization = json_materialization.materialize(
        projected.value, _json_materialization_request(style, newline)
    )
    if isinstance(materialization, FailedMaterializationAttempt):
        return f"unexpected materialization failure: {materialization.failure.code}"
    expected_output = compare.string_field(vector.expected, "output")
    if expected_output is None:
        return "missing expected.output"
    if materialization.document.render() != expected_output.encode("utf-8"):
        return "output differs"
    expected_fidelity = compare.string_field(vector.expected, "fidelity")
    if expected_fidelity is None:
        return "missing expected.fidelity"
    if materialization.fidelity.value != expected_fidelity:
        return "fidelity differs"
    minimum = compare.integer_field(vector.expected, "minimum_provenance_entries")
    if minimum is not None and len(materialization.provenance.entries) < minimum:
        return "provenance entries below minimum"
    return None


def _materialize_json_failure(vector: runner.Case) -> str | None:
    limits = MaterializationLimits()
    if vector.id == "operations.v1.materialize-json-nonstring-key-rejected":
        key_text = compare.string_field(vector.input, "key_integer")
        if key_text is None:
            return "missing input.key_integer"
        value = PortableValue.entry_mapping(
            ((PortableValue.integer(int(key_text)), PortableValue.boolean(True)),)
        )
    elif vector.id == "operations.v1.materialize-json-float-rejected":
        bits = compare.string_field(vector.input, "binary64_bits")
        if bits is None:
            return "missing input.binary64_bits"
        value = PortableValue.binary_float64(int(bits, 16))
    else:
        source = compare.string_field(vector.input, "source")
        if source is None:
            return "missing input.source"
        document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
        projected, message = _project_best_exact(document)
        if message:
            return message
        value = projected.value
        if vector.id == "operations.v1.materialize-json-output-limit":
            max_output = compare.integer_field(vector.input, "max_output_bytes")
            if max_output is None:
                return "missing input.max_output_bytes"
            limits = MaterializationLimits(max_output_bytes=max_output)
        else:
            max_depth = compare.integer_field(vector.input, "max_depth")
            if max_depth is None:
                return "missing input.max_depth"
            limits = MaterializationLimits(max_depth=max_depth)
    request = MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("json.canonical-compact", 1),
    ).with_newline(NewlinePolicy.NONE).with_limits(limits)
    materialization = json_materialization.materialize(value, request)
    if not isinstance(materialization, FailedMaterializationAttempt):
        return "materialization must fail"
    expected_code = compare.string_field(vector.expected, "code")
    has_document = compare.boolean_field(vector.expected, "has_document")
    if expected_code is None or has_document is None:
        return "missing expected facts"
    if materialization.failure.code != expected_code or has_document:
        return "failure facts differ"
    return None


# ---------------------------------------------------------------------------
# JSON edit cases
# ---------------------------------------------------------------------------


def _jsonc_document(vector: runner.Case):
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    try:
        return _parse_json(source, json_kinds.JsonProfile.JSONC_BOUNDED_V1), None
    except json_errors.JsonFormationFailure as error:
        return None, f"formation failed: {error.code}"


def _strict_document(vector: runner.Case):
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    try:
        return _parse_json(source, json_kinds.JsonProfile.STRICT_V1), None
    except json_errors.JsonFormationFailure as error:
        return None, f"formation failed: {error.code}"


def _snapshot_of(document) -> object:
    """The SourceSnapshot of one document (the JSON family keeps it as an
    attribute; the TOML family exposes it as a method)."""
    source = getattr(document, "source", None)
    if callable(source):
        return source()
    return source


def _json_object_insert(vector: runner.Case) -> str | None:
    document, message = _jsonc_document(vector)
    if message:
        return message
    members = document.root().object_members()
    if not members.is_available:
        return "root is not an object"
    before_ordinal = compare.integer_field(vector.input, "before_ordinal")
    name = compare.string_field(vector.input, "name")
    if before_ordinal is None or name is None:
        return "missing input.before_ordinal/name"
    all_members = members.value
    if before_ordinal >= len(all_members):
        return "before_ordinal out of range"
    builder = json_edit.EditTransactionBuilder(document)
    builder.insert_member(
        document.root().node_ref(),
        name,
        PortableValue.sequence((PortableValue.boolean(True),)),
        AssociationPlacement(kind="Before", anchor=all_members[before_ordinal].node_ref()),
    )
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as error:
        return f"edit failed: {error.code}"
    expected = compare.string_field(vector.expected, "output")
    if expected is None:
        return "missing expected.output"
    if commit.document.render() != expected.encode("utf-8"):
        return "output differs"
    return None


def _json_object_remove(vector: runner.Case) -> str | None:
    document, message = _jsonc_document(vector)
    if message:
        return message
    members = document.root().object_members()
    if not members.is_available:
        return "root is not an object"
    target_ordinal = compare.integer_field(vector.input, "target_ordinal")
    if target_ordinal is None:
        return "missing input.target_ordinal"
    all_members = members.value
    if target_ordinal >= len(all_members):
        return "target_ordinal out of range"
    builder = json_edit.EditTransactionBuilder(document)
    builder.remove_member(all_members[target_ordinal].node_ref())
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as error:
        return f"edit failed: {error.code}"
    return _verify_json_commit(vector, document, commit)


def _json_array_remove(vector: runner.Case) -> str | None:
    document, message = _jsonc_document(vector)
    if message:
        return message
    elements = document.root().array_elements()
    if not elements.is_available:
        return "root is not an array"
    target_ordinal = compare.integer_field(vector.input, "target_ordinal")
    if target_ordinal is None:
        return "missing input.target_ordinal"
    all_elements = elements.value
    if target_ordinal >= len(all_elements):
        return "target_ordinal out of range"
    builder = json_edit.EditTransactionBuilder(document)
    builder.remove_array_element(all_elements[target_ordinal].node_ref())
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as error:
        return f"edit failed: {error.code}"
    expected = compare.string_field(vector.expected, "output")
    if expected is None:
        return "missing expected.output"
    if commit.document.render() != expected.encode("utf-8"):
        return "output differs"
    return None


def _json_conflict_atomic(vector: runner.Case) -> str | None:
    document, message = _strict_document(vector)
    if message:
        return message
    original = document.render()
    members = document.root().object_members()
    if not members.is_available:
        return "root is not an object"
    target_ordinal = compare.integer_field(vector.input, "target_ordinal")
    if target_ordinal is None:
        return "missing input.target_ordinal"
    all_members = members.value
    if target_ordinal >= len(all_members):
        return "target_ordinal out of range"
    target = all_members[target_ordinal].node_ref()
    builder = json_edit.EditTransactionBuilder(document)
    builder.rename_member(target, "x").remove_member(target)
    try:
        json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as error:
        expected_code = compare.string_field(vector.expected, "code")
        base_unchanged = compare.boolean_field(vector.expected, "base_unchanged")
        if expected_code is None or base_unchanged is None:
            return "missing expected facts"
        if error.code != expected_code or (document.render() == original) != base_unchanged:
            return "conflict facts differ"
        return None
    return "commit must fail"


def _json_dry_run(vector: runner.Case) -> str | None:
    document, message = _strict_document(vector)
    if message:
        return message
    name = compare.string_field(vector.input, "name")
    value = compare.string_field(vector.input, "value")
    source_id = compare.string_field(vector.input, "source_id")
    if name is None or value is None or source_id is None:
        return "missing input.name/value/source_id"
    builder = json_edit.EditTransactionBuilder(document)
    builder.insert_member(
        document.root().node_ref(),
        name,
        PortableValue.string(value),
        AssociationPlacement(kind="End"),
    )
    transaction = builder.build()
    try:
        plan = json_edit.dry_run(document, transaction, EditPlanSourceId.new(source_id))
        commit = json_edit.commit(document, transaction)
    except json_errors.JsonEditFailure as error:
        return f"edit failed: {error.code}"
    expected = compare.string_field(vector.expected, "output")
    if expected is None:
        return "missing expected.output"
    if commit.document.render() != expected.encode("utf-8"):
        return "output differs"
    same_replacements = compare.boolean_field(vector.expected, "same_replacements")
    if (plan.replacements() == commit.source_patch.replacements) != same_replacements:
        return "plan and commit replacements differ"
    same_digest = compare.boolean_field(vector.expected, "same_target_digest")
    if (plan.target_digest() == commit.source_patch.target_digest) != same_digest:
        return "plan and commit target digests differ"
    safe = compare.boolean_field(vector.expected, "safe_summary")
    if _plan_summary_is_safe(plan) != safe:
        return "summary safety differs"
    redacted_debug = compare.boolean_field(vector.expected, "redacted_debug")
    if redacted_debug is None:
        return "missing expected.redacted_debug"
    redacted = plan.with_all_replacements_redacted(True, True)
    if ("secret" in repr(redacted.source_patch())) == redacted_debug:
        return "redacted debug presentation differs"
    return _verify_json_commit(vector, document, commit)


def _plan_summary_is_safe(plan) -> bool:
    for operation in plan.operations:
        for value in operation.arguments.values():
            if "secret" in value:
                return False
    return True


def _verify_json_commit(vector: runner.Case, base_document, commit) -> str | None:
    expected = compare.string_field(vector.expected, "output")
    if expected is None:
        return "missing expected.output"
    limits = SourcePatchLimits()
    try:
        replay = commit.source_patch.apply(_snapshot_of(base_document), limits)
    except Exception as error:  # noqa: BLE001 — vector boundary
        return f"patch replay: {error}"
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    if (
        (replay.bytes() == expected.encode("utf-8")) != patch_replays
        or commit.document.render() != expected.encode("utf-8")
    ):
        return "patch replay differs"
    proof_verifies = compare.boolean_field(vector.expected, "proof_verifies")
    try:
        commit.untouched_proof.verify(
            _snapshot_of(base_document),
            _snapshot_of(commit.document),
            list(commit.source_patch.replacements),
        )
        verifies = True
    except UntouchedByteProofError:
        verifies = False
    if verifies != proof_verifies:
        return "untouched proof verdict differs"
    return None


def _json_structural_matrix(vector: runner.Case) -> str | None:
    items = compare.sequence_field(vector.input, "cases")
    if items is None:
        return "missing input.cases"
    completed = 0
    for item in items:
        operation = compare.string_field(item, "operation")
        source = compare.string_field(item, "source")
        if operation is None or source is None:
            return "matrix item lacks operation/source"
        document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
        builder = json_edit.EditTransactionBuilder(document)
        try:
            if operation == "insert-member-end":
                name = compare.string_field(item, "name")
                if name is None:
                    return "matrix item lacks name"
                builder.insert_member(
                    document.root().node_ref(),
                    name,
                    PortableValue.boolean(True),
                    AssociationPlacement(kind="End"),
                )
            elif operation == "remove-member":
                target = _matrix_target_member(document, item)
                if target is None:
                    return "matrix item target invalid"
                builder.remove_member(target)
            elif operation == "rename-member":
                target = _matrix_target_member(document, item)
                if target is None:
                    return "matrix item target invalid"
                name = compare.string_field(item, "name")
                if name is None:
                    return "matrix item lacks name"
                builder.rename_member(target, name)
            elif operation == "insert-array-start":
                builder.insert_array_element(
                    document.root().node_ref(),
                    PortableValue.integer(1),
                    AssociationPlacement(kind="Start"),
                )
            elif operation == "insert-array-after":
                elements = document.root().array_elements()
                if not elements.is_available:
                    return "root is not an array"
                anchor_ordinal = compare.integer_field(item, "anchor_ordinal")
                if anchor_ordinal is None:
                    return "matrix item lacks anchor_ordinal"
                all_elements = elements.value
                if anchor_ordinal >= len(all_elements):
                    return "anchor_ordinal out of range"
                builder.insert_array_element(
                    document.root().node_ref(),
                    PortableValue.string("x"),
                    AssociationPlacement(kind="After", anchor=all_elements[anchor_ordinal].node_ref()),
                )
            else:
                return f"unknown JSON matrix operation {operation}"
            commit = json_edit.commit(document, builder.build())
        except json_errors.JsonEditFailure as error:
            return f"matrix edit failed: {error.code}"
        expected = compare.string_field(item, "expected")
        if expected is None:
            return "matrix item lacks expected"
        if commit.document.render() != expected.encode("utf-8"):
            return f"matrix output mismatch for {operation}"
        completed += 1
    expected_completed = compare.integer_field(vector.expected, "completed")
    if expected_completed is None or completed != expected_completed:
        return "completed count differs"
    return None


def _matrix_target_member(document, item):
    target_ordinal = compare.integer_field(item, "target_ordinal")
    if target_ordinal is None:
        return None
    members = document.root().object_members()
    if not members.is_available:
        return None
    all_members = members.value
    if target_ordinal >= len(all_members):
        return None
    return all_members[target_ordinal].node_ref()


def _json_conflict_matrix(vector: runner.Case) -> str | None:
    items = compare.sequence_field(vector.input, "cases")
    if items is None:
        return "missing input.cases"
    failed_atomically = 0
    for item in items:
        mode = compare.string_field(item, "mode")
        source = compare.string_field(item, "source")
        if mode is None or source is None:
            return "matrix item lacks mode/source"
        document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
        original = document.render()
        builder = json_edit.EditTransactionBuilder(document)
        try:
            if mode == "wrong-snapshot":
                foreign_source = compare.string_field(item, "foreign")
                if foreign_source is None:
                    return "matrix item lacks foreign"
                foreign = _parse_json(foreign_source, json_kinds.JsonProfile.STRICT_V1)
                builder.literal_scalar(foreign.root().node_ref(), b"3")
                json_edit.commit(document, builder.build())
            elif mode == "same-boundary":
                builder.insert_member(
                    document.root().node_ref(), "x", PortableValue.boolean(True),
                    AssociationPlacement(kind="End"),
                ).insert_member(
                    document.root().node_ref(), "y", PortableValue.boolean(False),
                    AssociationPlacement(kind="End"),
                )
                json_edit.commit(document, builder.build())
            elif mode == "removed-anchor":
                members = document.root().object_members()
                if not members.is_available:
                    return "root is not an object"
                member = members.value[0]
                builder.remove_member(member.node_ref()).insert_member(
                    document.root().node_ref(), "x", PortableValue.boolean(True),
                    AssociationPlacement(kind="Before", anchor=member.node_ref()),
                )
                json_edit.commit(document, builder.build())
            elif mode == "ancestor-descendant":
                members = document.root().object_members()
                if not members.is_available:
                    return "root is not an object"
                member = members.value[0]
                builder.semantic_scalar(
                    member.value_node_ref(),
                    PortableValue.integer(3),
                    json_edit.RepresentationPolicy.PRESERVE_COMPATIBLE,
                ).remove_member(member.node_ref())
                json_edit.commit(document, builder.build())
            else:
                return f"unknown JSON conflict mode {mode}"
        except json_errors.JsonEditFailure as error:
            expected_code = compare.string_field(item, "code")
            if expected_code is None:
                return "matrix item lacks code"
            if error.code != expected_code or document.render() != original:
                return f"conflict mismatch for {mode}"
            failed_atomically += 1
            continue
        return f"JSON conflict mode {mode} unexpectedly completed"
    expected = compare.integer_field(vector.expected, "failed_atomically")
    if expected is None or failed_atomically != expected:
        return "failed_atomically count differs"
    return None


def _materialization_security_matrix(vector: runner.Case) -> str | None:
    items = compare.sequence_field(vector.input, "cases")
    if items is None:
        return "missing input.cases"
    completed = 0
    for item in items:
        mode = compare.string_field(item, "mode")
        source = compare.string_field(item, "source")
        if mode is None or source is None:
            return "matrix item lacks mode/source"
        document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
        projected, message = _project_best_exact(document)
        if message:
            return message
        base_request = MaterializationRequest.new(
            ProfileId.new("json.strict", 1),
            MaterializationStyleId.new("json.canonical-compact", 1),
        ).with_newline(NewlinePolicy.NONE)
        if mode in ("node-limit", "provenance-limit"):
            limit = compare.integer_field(item, "limit")
            if limit is None:
                return "matrix item lacks limit"
            limits = MaterializationLimits()
            if mode == "node-limit":
                limits = MaterializationLimits(max_input_nodes=limit)
            else:
                limits = MaterializationLimits(max_provenance_entries=limit)
            materialization = json_materialization.materialize(
                projected.value, base_request.with_limits(limits)
            )
            if not isinstance(materialization, FailedMaterializationAttempt):
                return "security case unexpectedly completed"
            expected_code = compare.string_field(item, "code")
            if expected_code is None:
                return "matrix item lacks code"
            if materialization.failure.code != expected_code:
                return f"security code mismatch for {mode}"
        elif mode == "escaping":
            materialization = json_materialization.materialize(projected.value, base_request)
            if isinstance(materialization, FailedMaterializationAttempt):
                return "escaping case unexpectedly failed"
            expected = compare.string_field(item, "expected")
            if expected is None:
                return "matrix item lacks expected"
            if materialization.document.render() != expected.encode("utf-8"):
                return "escaping output mismatch"
        else:
            return f"unknown security mode {mode}"
        completed += 1
    expected = compare.integer_field(vector.expected, "completed")
    if expected is None or completed != expected:
        return "completed count differs"
    return None


def _untouched_proof_tamper(vector: runner.Case) -> str | None:
    document, message = _strict_document(vector)
    if message:
        return message
    members = document.root().object_members()
    if not members.is_available:
        return "root is not an object"
    member = members.value[0]
    builder = json_edit.EditTransactionBuilder(document)
    builder.semantic_scalar(
        member.value_node_ref(),
        PortableValue.integer(2),
        json_edit.RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as error:
        return f"edit failed: {error.code}"
    tampered_source = compare.string_field(vector.input, "tampered_target")
    if tampered_source is None:
        return "missing input.tampered_target"
    tampered = _parse_json(tampered_source, json_kinds.JsonProfile.STRICT_V1)
    try:
        commit.untouched_proof.verify(
            _snapshot_of(document),
            _snapshot_of(tampered),
            list(commit.source_patch.replacements),
        )
        detected = False
    except UntouchedByteProofError:
        detected = True
    tamper_detected = compare.boolean_field(vector.expected, "tamper_detected")
    if tamper_detected is None or detected != tamper_detected:
        return "tamper detection differs"
    return None


# ---------------------------------------------------------------------------
# TOML materialization cases
# ---------------------------------------------------------------------------


def _parse_toml(source: str):
    return toml_parser.parse(source.encode("utf-8"), TomlProfile.TOML10_V1, ParseLimits())


def _toml_request(mapping_policy: MappingPolicy) -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("toml.1.0", 1),
        MaterializationStyleId.new("toml.canonical-document", 1),
    ).with_newline(NewlinePolicy.LF).with_mapping_policy(mapping_policy)


def _toml_project(document):
    request = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = toml_projection.project_document(document, request)
    if isinstance(result, toml_projection.FailedProjectionAttempt):
        diagnostics = result.diagnostics
        code = diagnostics[0].code if diagnostics else "unknown"
        return None, f"TOML projection failed: {code}"
    return result.value, None


def _materialize_toml_native(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    value, message = _toml_project(document)
    if message:
        return message
    result = toml_materialization.materialize(value, _toml_request(MappingPolicy.REQUIRE_OBJECT))
    if isinstance(result, FailedMaterializationAttempt):
        return f"unexpected materialization failure: {result.failure.code}"
    reparsed = _parse_toml(result.document.render().decode("utf-8"))
    reprojected, message = _toml_project(reparsed)
    if message:
        return message
    fidelity = compare.string_field(vector.expected, "fidelity")
    minimum = compare.integer_field(vector.expected, "minimum_provenance_entries")
    reprojects_equal = compare.boolean_field(vector.expected, "reprojects_equal")
    if fidelity is None or minimum is None or reprojects_equal is None:
        return "missing expected facts"
    if (
        result.fidelity.value != fidelity
        or len(result.provenance.entries) < minimum
        or core_equal(reprojected, value) != reprojects_equal
    ):
        return "materialization facts did not match"
    return None


def _entry_mapping_value(vector: runner.Case):
    """Projects the strict-JSON source as an EntryMapping
    (v1_toml_face.go strictJSONValue with asMapping=true)."""
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    projection = compare.string_field(vector.input, "projection")
    if projection != "EntryMapping":
        return None, f"unknown projection {projection}"
    document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
    request = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1
    ).build()
    result = json_projection.project(document, request)
    if isinstance(result, json_projection.FailedProjectionAttempt):
        return None, "projection failed"
    return result.value, None


def _materialize_toml_mapping(vector: runner.Case) -> str | None:
    value, message = _entry_mapping_value(vector)
    if message:
        return message
    policy_text = compare.string_field(vector.input, "mapping_policy")
    if policy_text == "RequireObject":
        policy = MappingPolicy.REQUIRE_OBJECT
    elif policy_text == "UniqueStringEntriesToObject":
        policy = MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT
    else:
        return f"unknown mapping policy {policy_text}"
    result = toml_materialization.materialize(value, _toml_request(policy))
    if not isinstance(result, FailedMaterializationAttempt):
        output = compare.string_field(vector.expected, "output")
        fidelity = compare.string_field(vector.expected, "fidelity")
        event_code = compare.string_field(vector.expected, "event_code")
        if output is None or fidelity is None:
            return "missing expected facts"
        has_event = any(event.code == event_code for event in result.report.events)
        if (
            result.document.render() != output.encode("utf-8")
            or result.fidelity.value != fidelity
            or not has_event
        ):
            return "materialization output or report did not match"
        return None
    code = compare.string_field(vector.expected, "code")
    has_document = compare.boolean_field(vector.expected, "has_document")
    if code is None or has_document is None:
        return "missing expected facts"
    if result.failure.code != code or has_document:
        return "materialization failure facts did not match"
    return None


def _materialize_toml_failure(vector: runner.Case) -> str | None:
    value = PortableValue.null()
    result = toml_materialization.materialize(value, _toml_request(MappingPolicy.REQUIRE_OBJECT))
    if not isinstance(result, FailedMaterializationAttempt):
        return "unexpected materialization success"
    code = compare.string_field(vector.expected, "code")
    has_document = compare.boolean_field(vector.expected, "has_document")
    if code is None or has_document is None:
        return "missing expected facts"
    if result.failure.code != code or has_document:
        return "materialization failure facts did not match"
    return None


def _materialize_toml_limit(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    output_limit = compare.integer_field(vector.input, "max_output_bytes")
    if source is None or output_limit is None:
        return "missing input.source/max_output_bytes"
    document = _parse_toml(source)
    value, message = _toml_project(document)
    if message:
        return message
    request = _toml_request(MappingPolicy.REQUIRE_OBJECT).with_limits(
        MaterializationLimits(max_output_bytes=output_limit)
    )
    result = toml_materialization.materialize(value, request)
    if not isinstance(result, FailedMaterializationAttempt):
        return "unexpected materialization success"
    code = compare.string_field(vector.expected, "code")
    has_document = compare.boolean_field(vector.expected, "has_document")
    if code is None or has_document is None:
        return "missing expected facts"
    if result.failure.code != code or has_document:
        return "materialization limit facts did not match"
    return None


# ---------------------------------------------------------------------------
# TOML edit cases
# ---------------------------------------------------------------------------


def _toml_root_entry(document, name: str):
    entries = document.root().table_entries()
    if entries is None:
        return None
    for entry in entries:
        if entry.name() == name:
            return entry
    return None


def _toml_root_insert(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    key = compare.string_field(vector.input, "key")
    if source is None or key is None:
        return "missing input.source/key"
    document = _parse_toml(source)
    builder = toml_edits.EditTransactionBuilder(document)
    builder.insert_entry(
        document.root().node_ref(),
        key,
        PortableValue.boolean(True),
        AssociationPlacement(kind="End"),
    )
    try:
        commit = toml_edits.commit_document(document, builder.build())
    except toml_errors.TomlEditFailure as error:
        return f"edit failed: {error.code}"
    output = compare.string_field(vector.expected, "output")
    if output is None:
        return "missing expected.output"
    if commit.document.render() != output.encode("utf-8"):
        return "edit output did not match"
    return None


def _toml_inline_rename(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    table_name = compare.string_field(vector.input, "table")
    target_ordinal = compare.integer_field(vector.input, "target_ordinal")
    key = compare.string_field(vector.input, "key")
    if source is None or table_name is None or target_ordinal is None or key is None:
        return "missing input facts"
    document = _parse_toml(source)
    table_entry = _toml_root_entry(document, table_name)
    if table_entry is None:
        return f"missing root entry {table_name}"
    entries = table_entry.item().table_entries()
    if entries is None:
        return "expected inline table"
    builder = toml_edits.EditTransactionBuilder(document)
    builder.rename_entry(entries[target_ordinal].node_ref(), key)
    try:
        commit = toml_edits.commit_document(document, builder.build())
    except toml_errors.TomlEditFailure as error:
        return f"edit failed: {error.code}"
    output = compare.string_field(vector.expected, "output")
    if output is None:
        return "missing expected.output"
    if commit.document.render() != output.encode("utf-8"):
        return "edit output did not match"
    return None


def _toml_array_remove(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    array_name = compare.string_field(vector.input, "array")
    target_ordinal = compare.integer_field(vector.input, "target_ordinal")
    if source is None or array_name is None or target_ordinal is None:
        return "missing input facts"
    document = _parse_toml(source)
    array_entry = _toml_root_entry(document, array_name)
    if array_entry is None:
        return f"missing root entry {array_name}"
    elements = array_entry.item().array_elements()
    if elements is None:
        return "expected array"
    builder = toml_edits.EditTransactionBuilder(document)
    builder.remove_array_element(elements[target_ordinal].node_ref())
    try:
        commit = toml_edits.commit_document(document, builder.build())
    except toml_errors.TomlEditFailure as error:
        return f"edit failed: {error.code}"
    output = compare.string_field(vector.expected, "output")
    if output is None:
        return "missing expected.output"
    if commit.document.render() != output.encode("utf-8"):
        return "edit output did not match"
    return None


def _toml_conflict_atomic(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    key = compare.string_field(vector.input, "key")
    if source is None or key is None:
        return "missing input.source/key"
    document = _parse_toml(source)
    builder = toml_edits.EditTransactionBuilder(document)
    builder.insert_entry(
        document.root().node_ref(),
        key,
        PortableValue.boolean(True),
        AssociationPlacement(kind="Start"),
    )
    try:
        toml_edits.commit_document(document, builder.build())
    except toml_errors.TomlEditFailure as error:
        code = compare.string_field(vector.expected, "code")
        base_unchanged = compare.boolean_field(vector.expected, "base_unchanged")
        if code is None or base_unchanged is None:
            return "missing expected facts"
        if error.code != code or (document.render() == source.encode("utf-8")) != base_unchanged:
            return "conflict facts did not match"
        return None
    return "duplicate key edit must fail"


def _verify_toml_commit(vector: runner.Case, base_document, commit) -> str | None:
    output = compare.string_field(vector.expected, "output")
    if output is None:
        return "missing expected.output"
    limits = SourcePatchLimits()
    try:
        replay = commit.source_patch.apply(_snapshot_of(base_document), limits)
    except Exception as error:  # noqa: BLE001 — vector boundary
        return f"patch replay: {error}"
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    proof_verifies = compare.boolean_field(vector.expected, "proof_verifies")
    if patch_replays is None or proof_verifies is None:
        return "missing expected facts"
    proof_ok = True
    try:
        commit.untouched_proof.verify(
            _snapshot_of(base_document),
            _snapshot_of(commit.document),
            list(commit.source_patch.replacements),
        )
    except UntouchedByteProofError:
        proof_ok = False
    if (
        commit.document.render() != output.encode("utf-8")
        or (replay.bytes() == output.encode("utf-8")) != patch_replays
        or proof_ok != proof_verifies
    ):
        return "commit verification facts did not match"
    return None


def _toml_dry_run(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    key = compare.string_field(vector.input, "key")
    value = compare.string_field(vector.input, "value")
    source_id = compare.string_field(vector.input, "source_id")
    if source is None or key is None or value is None or source_id is None:
        return "missing input facts"
    document = _parse_toml(source)
    builder = toml_edits.EditTransactionBuilder(document)
    builder.insert_entry(
        document.root().node_ref(),
        key,
        PortableValue.string(value),
        AssociationPlacement(kind="End"),
    )
    transaction = builder.build()
    try:
        plan = toml_edits.dry_run_document(
            document, transaction, EditPlanSourceId.new(source_id)
        )
        commit = toml_edits.commit_document(document, transaction)
    except toml_errors.TomlEditFailure as error:
        return f"edit failed: {error.code}"
    output = compare.string_field(vector.expected, "output")
    if output is None:
        return "missing expected.output"
    if commit.document.render() != output.encode("utf-8"):
        return "edit output did not match"
    safe = _plan_summary_is_safe(plan)
    redacted = plan.with_all_replacements_redacted(True, True)
    redacted_leaks = "secret" in repr(redacted.source_patch())
    same_replacements = compare.boolean_field(vector.expected, "same_replacements")
    same_target_digest = compare.boolean_field(vector.expected, "same_target_digest")
    safe_summary = compare.boolean_field(vector.expected, "safe_summary")
    redacted_debug = compare.boolean_field(vector.expected, "redacted_debug")
    if None in (same_replacements, same_target_digest, safe_summary, redacted_debug):
        return "missing expected facts"
    replacements_match = plan.replacements() == commit.source_patch.replacements
    digest_match = plan.target_digest() == commit.source_patch.target_digest
    if (
        replacements_match != same_replacements
        or digest_match != same_target_digest
        or safe != safe_summary
        or redacted_leaks != (not redacted_debug)
    ):
        return "dry-run facts did not match"
    return _verify_toml_commit(vector, document, commit)


def _toml_structural_matrix(vector: runner.Case) -> str | None:
    items = compare.sequence_field(vector.input, "cases")
    if items is None:
        return "missing input.cases"
    completed = 0
    for item in items:
        operation = compare.string_field(item, "operation")
        source = compare.string_field(item, "source")
        if operation is None or source is None:
            return "matrix item lacks operation/source"
        document = _parse_toml(source)
        builder = toml_edits.EditTransactionBuilder(document)
        try:
            if operation == "insert-standard-table":
                table_name = compare.string_field(item, "table")
                key = compare.string_field(item, "key")
                if table_name is None or key is None:
                    return "matrix item lacks table/key"
                table_entry = _toml_root_entry(document, table_name)
                if table_entry is None:
                    return f"missing root entry {table_name}"
                builder.insert_entry(
                    table_entry.item().node_ref(),
                    key,
                    PortableValue.string("localhost"),
                    AssociationPlacement(kind="End"),
                )
            elif operation == "insert-inline":
                table_name = compare.string_field(item, "table")
                key = compare.string_field(item, "key")
                before_ordinal = compare.integer_field(item, "before_ordinal")
                if table_name is None or key is None or before_ordinal is None:
                    return "matrix item lacks table/key/before_ordinal"
                table_entry = _toml_root_entry(document, table_name)
                if table_entry is None:
                    return f"missing root entry {table_name}"
                entries = table_entry.item().table_entries()
                if entries is None:
                    return "expected inline table"
                builder.insert_entry(
                    table_entry.item().node_ref(),
                    key,
                    PortableValue.sequence((PortableValue.boolean(True),)),
                    AssociationPlacement(kind="Before", anchor=entries[before_ordinal].node_ref()),
                )
            elif operation == "remove-inline":
                table_name = compare.string_field(item, "table")
                target_ordinal = compare.integer_field(item, "target_ordinal")
                if table_name is None or target_ordinal is None:
                    return "matrix item lacks table/target_ordinal"
                table_entry = _toml_root_entry(document, table_name)
                if table_entry is None:
                    return f"missing root entry {table_name}"
                entries = table_entry.item().table_entries()
                if entries is None:
                    return "expected inline table"
                builder.remove_entry(entries[target_ordinal].node_ref())
            elif operation == "insert-array-start":
                array_name = compare.string_field(item, "array")
                if array_name is None:
                    return "matrix item lacks array"
                array_entry = _toml_root_entry(document, array_name)
                if array_entry is None:
                    return f"missing root entry {array_name}"
                builder.insert_array_element(
                    array_entry.item().node_ref(),
                    PortableValue.integer(1),
                    AssociationPlacement(kind="Start"),
                )
            else:
                return f"unknown TOML matrix operation {operation}"
            commit = toml_edits.commit_document(document, builder.build())
        except toml_errors.TomlEditFailure as error:
            return f"matrix edit failed: {error.code}"
        expected = compare.string_field(item, "expected")
        if expected is None:
            return "matrix item lacks expected"
        if commit.document.render() != expected.encode("utf-8"):
            return f"TOML matrix output mismatch for {operation}"
        completed += 1
    expected_completed = compare.integer_field(vector.expected, "completed")
    if expected_completed is None or completed != expected_completed:
        return "matrix completion count did not match"
    return None


def _toml_conflict_matrix(vector: runner.Case) -> str | None:
    items = compare.sequence_field(vector.input, "cases")
    if items is None:
        return "missing input.cases"
    failed_atomically = 0
    for item in items:
        mode = compare.string_field(item, "mode")
        source = compare.string_field(item, "source")
        if mode is None or source is None:
            return "matrix item lacks mode/source"
        document = _parse_toml(source)
        builder = toml_edits.EditTransactionBuilder(document)
        try:
            if mode == "duplicate-target":
                entry = _toml_root_entry(document, "a")
                if entry is None:
                    return "missing root entry a"
                builder.rename_entry(entry.node_ref(), "x").remove_entry(entry.node_ref())
                toml_edits.commit_document(document, builder.build())
            elif mode == "removed-anchor":
                entry = _toml_root_entry(document, "a")
                if entry is None:
                    return "missing root entry a"
                builder.remove_entry(entry.node_ref()).insert_entry(
                    document.root().node_ref(),
                    "x",
                    PortableValue.boolean(True),
                    AssociationPlacement(kind="Before", anchor=entry.node_ref()),
                )
                toml_edits.commit_document(document, builder.build())
            elif mode == "ancestor-descendant":
                entry = _toml_root_entry(document, "a")
                if entry is None:
                    return "missing root entry a"
                builder.semantic_scalar(
                    entry.item().node_ref(),
                    PortableValue.integer(3),
                    toml_edits.RepresentationPolicy.PRESERVE_COMPATIBLE,
                ).remove_entry(entry.node_ref())
                toml_edits.commit_document(document, builder.build())
            elif mode == "unsupported-table-remove":
                entry = _toml_root_entry(document, "service")
                if entry is None:
                    return "missing root entry service"
                builder.remove_entry(entry.node_ref())
                toml_edits.commit_document(document, builder.build())
            else:
                return f"unknown TOML conflict mode {mode}"
        except toml_errors.TomlEditFailure as error:
            code = compare.string_field(item, "code")
            if code is None:
                return "matrix item lacks code"
            if error.code != code or document.render() != source.encode("utf-8"):
                return f"TOML conflict mismatch for {mode}"
            failed_atomically += 1
            continue
        return f"TOML conflict mode {mode} unexpectedly completed"
    expected_failed = compare.integer_field(vector.expected, "failed_atomically")
    if expected_failed is None or failed_atomically != expected_failed:
        return "matrix failure count did not match"
    return None


# ---------------------------------------------------------------------------
# convert cases (convert_face.go)
# ---------------------------------------------------------------------------


def _toml_convert_request() -> MaterializationRequest:
    return _toml_request(MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT)


def _json_compact_request() -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("json.canonical-compact", 1),
    ).with_newline(NewlinePolicy.NONE)


def _convert_json_to_toml(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
    projection = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    ).build()
    result = convert_json(document, projection, _toml_convert_request())
    if isinstance(result, ConversionFailure):
        return f"unexpected conversion failure: {result.code()}"
    output = compare.string_field(vector.expected, "output")
    fidelity = compare.string_field(vector.expected, "overall_fidelity")
    if output is None or fidelity is None:
        return "missing expected facts"
    if result.document.render() != output.encode("utf-8") or result.report.overall_fidelity.value != fidelity:
        return "conversion output or fidelity did not match"
    return None


def _convert_toml_to_json(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    projection = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = convert_toml(document, projection, _json_compact_request())
    if isinstance(result, ConversionFailure):
        return f"unexpected conversion failure: {result.code()}"
    output = compare.string_field(vector.expected, "output")
    fidelity = compare.string_field(vector.expected, "overall_fidelity")
    if output is None or fidelity is None:
        return "missing expected facts"
    if result.document.render() != output.encode("utf-8") or result.report.overall_fidelity.value != fidelity:
        return "conversion output or fidelity did not match"
    return None


def _convert_duplicate_failure(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
    projection = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    ).build()
    result = convert_json(document, projection, _toml_convert_request())
    code = compare.string_field(vector.expected, "code")
    has_document = compare.boolean_field(vector.expected, "has_document")
    if code is None or has_document is None:
        return "missing expected facts"
    if not isinstance(result, ConversionFailure):
        return "duplicate-key conversion unexpectedly completed"
    if result.code() != code or has_document:
        return "conversion failure facts did not match"
    return None


def _convert_transformed_report(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_json(source, json_kinds.JsonProfile.STRICT_V1)
    projection = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1
    ).build()
    result = convert_json(document, projection, _toml_convert_request())
    if isinstance(result, ConversionFailure):
        return f"unexpected conversion failure: {result.code()}"
    fidelity = compare.string_field(vector.expected, "overall_fidelity")
    projection_event = compare.string_field(vector.expected, "projection_event")
    materialization_event = compare.string_field(vector.expected, "materialization_event")
    if fidelity is None or projection_event is None or materialization_event is None:
        return "missing expected facts"
    projection_codes = result.report.projection_report.event_codes()
    materialization_codes = result.report.materialization_report.event_codes()
    if (
        result.report.overall_fidelity.value != fidelity
        or projection_event not in projection_codes
        or materialization_event not in materialization_codes
    ):
        return "transformed conversion report facts did not match"
    return None


runner.register_suite("operations-v1.json", "consema.operations.conformance@1", "", 35, run)
