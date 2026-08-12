"""Suite ``consema.conformance@1`` (v1.json, 30 cases): core value strict
equality, PVCE/1 golden bytes, JSON parse/query/projection/edit baseline,
and portable-value query execution. Dispatch is by case id, mirroring
go/conformance/v1.go.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import core_query
from consema.conformance import loader
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.equal import hash_value as core_hash
from consema.core import pvce
from consema.core.value import Decimal, Kind, PortableValue
from consema.document.ids import ProfileId
from consema.document.limits import ParseLimits
from consema.json import kinds as json_kinds
from consema.json import parser as json_parser
from consema.json import query as json_query
from consema.json import projection as json_projection
from consema.json import edit as json_edit
from consema.json import document as json_document
from consema.json import errors as json_errors
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "value.integer-arbitrary-precision": _integer_precision,
        "value.decimal-normalization": _decimal_normalization,
        "value.float-signed-zero": _float_signed_zero,
        "pvce.null-vector": _pvce_vector,
        "pvce.negative-integer-vector": _pvce_vector,
        "pvce.object-vector": _pvce_vector,
        "pvce.reject-nonminimal-varint": _pvce_rejection,
        "pvce.encode-blob-limit": _pvce_blob_limit,
        "parse.strict-exact-roundtrip": _parse_roundtrip,
        "parse.jsonc-comments-trailing-comma": _parse_roundtrip,
        "parse.recovery-missing-close": _parse_roundtrip,
        "parse.duplicate-members": _parse_roundtrip,
        "parse.lossless-byte-coverage": _lossless_coverage,
        "query.reject-role-mismatch": _query_role_mismatch,
        "query.json-duplicate-order": _query_json_duplicate_order,
        "query.protocol-roundtrip": _query_protocol_roundtrip,
        "query.root-result-limit": _query_root_result_limit,
        "query.cursor-failure-terminal": _query_cursor_failure_terminal,
        "projection.best-exact-duplicate-mapping": _projection_best_exact,
        "projection.object-reject-duplicates": _projection_object,
        "projection.object-last-wins": _projection_object,
        "projection.object-key-provenance": _projection_object,
        "edit.scalar-minimal": _edit_scalar,
        "edit.preserve-decimal-scale": _edit_scalar,
        "edit.preserve-exponent-style": _edit_scalar,
        "edit.canonical-for-profile": _edit_scalar,
        "edit.preserve-else-canonical": _edit_scalar,
        "edit.preserve-incompatible-rejected": _edit_scalar,
        "edit.wrong-snapshot": _edit_wrong_snapshot,
        "resource.parse-token-limit": _parse_token_limit,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published v1 case")
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# core value cases
# ---------------------------------------------------------------------------


def _integer_precision(vector: runner.Case) -> str | None:
    spelling = compare.string_field(vector.input, "decimal")
    if spelling is None:
        return "missing input.decimal"
    number = int(spelling)
    return compare.require_equal(str(number), spelling, "decimal")


def _decimal_from_spelling(spelling: str) -> Decimal:
    return loader._decimal_from_text(spelling)


def _decimal_normalization(vector: runner.Case) -> str | None:
    left = compare.string_field(vector.input, "left")
    right = compare.string_field(vector.input, "right")
    if left is None or right is None:
        return "missing input.left/right"
    left_value = PortableValue.decimal(_decimal_from_spelling(left))
    right_value = PortableValue.decimal(_decimal_from_spelling(right))
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    if not core_equal(left_value, right_value):
        return "left and right must be strictly equal"
    if compare.boolean_field(vector.expected, "strict_hash_equal") is not True:
        return "expected.strict_hash_equal must be true"
    if core_hash(left_value) != core_hash(right_value):
        return "left and right hashes must be equal"
    return None


def _float_signed_zero(vector: runner.Case) -> str | None:
    positive = compare.string_field(vector.input, "positive_bits")
    negative = compare.string_field(vector.input, "negative_bits")
    if positive is None or negative is None:
        return "missing input.positive_bits/negative_bits"
    expected = compare.boolean_field(vector.expected, "strict_equal")
    if expected is None:
        return "missing expected.strict_equal"
    left = PortableValue.binary_float64(int(positive, 16))
    right = PortableValue.binary_float64(int(negative, 16))
    actual = core_equal(left, right)
    if actual != expected:
        return f"strict_equal: expected {expected}, got {actual}"
    return None


def _pvce_vector(vector: runner.Case) -> str | None:
    value = _pvce_value_from_input(vector.input)
    if value is None:
        return "missing or invalid input value"
    expected_hex = compare.string_field(vector.expected, "hex")
    if expected_hex is None:
        return "missing expected.hex"
    return compare.require_bytes_equal(pvce.encode(value), expected_hex, "hex")


def _pvce_value_from_input(value) -> PortableValue | None:
    if value.kind is Kind.STRING:
        text = value.as_string()
        if text == "Null":
            return PortableValue.null()
        return None
    if value.kind is Kind.BOOLEAN:
        return PortableValue.boolean(value.as_boolean())
    if value.kind is Kind.INTEGER:
        return PortableValue.integer(value.as_integer())
    if value.kind is not Kind.OBJECT:
        return None
    for key, item in value.as_object():
        if key == "value":
            return _pvce_value_from_input(item)
        if key == "integer":
            return PortableValue.integer(int(item.as_string()))
        if key == "decimal":
            return PortableValue.decimal(_decimal_from_spelling(item.as_string()))
        if key == "string":
            return PortableValue.string(item.as_string())
        if key == "sequence":
            elements = [_pvce_value_from_input(element) for element in item.as_sequence()]
            if any(element is None for element in elements):
                return None
            return PortableValue.sequence(tuple(elements))
        if key == "object":
            entries = []
            for member_key, member_value in item.as_object():
                converted = _pvce_value_from_input(member_value)
                if converted is None:
                    return None
                entries.append((member_key, converted))
            return PortableValue.object(entries)
    return None


def _pvce_rejection(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "hex")
    if text is None:
        return "missing input.hex"
    try:
        pvce.decode(bytes.fromhex(text))
    except pvce.PVCEError as error:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return "missing expected.failure"
        actual = _pvce_kind_name(error.kind)
        if actual != expected_failure:
            return f"failure: expected {expected_failure}, got {actual}"
        return None
    return "decode must fail"


def _pvce_kind_name(kind) -> str:
    """The CamelCase vector spelling of one PVCE failure kind (the Python
    kind values are kebab-case; the vectors freeze the Go enum names)."""
    return "".join(part.capitalize() for part in kind.value.split("-"))


def _pvce_blob_limit(vector: runner.Case) -> str | None:
    value = _pvce_value_from_input(vector.input)
    if value is None:
        return "missing or invalid input value"
    max_blob = compare.integer_field(vector.input, "max_blob_bytes")
    if max_blob is None:
        return "missing input.max_blob_bytes"
    try:
        pvce.encode_bounded(value, pvce.EncodeLimits(max_blob_bytes=max_blob))
    except pvce.PVCEError as error:
        if error.kind is pvce.PVCEErrorKind.RESOURCE_LIMIT:
            return None
        return f"failure kind: expected ResourceLimit, got {error.kind.value}"
    return "encode must fail"


# ---------------------------------------------------------------------------
# JSON parse cases
# ---------------------------------------------------------------------------


def _json_profile(spelling: str):
    profiles = {
        "json.strict@1": json_kinds.JsonProfile.STRICT_V1,
        "jsonc.bounded@1": json_kinds.JsonProfile.JSONC_BOUNDED_V1,
        "json5.standard@1": json_kinds.JsonProfile.JSON5_STANDARD_V1,
    }
    return profiles.get(spelling)


def _parse_json(vector: runner.Case):
    profile_spelling = compare.string_field(vector.input, "profile")
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    profile = _json_profile(profile_spelling or "json.strict@1")
    try:
        document = json_parser.parse(source.encode("utf-8"), profile, ParseLimits())
        return document, None
    except json_errors.JsonFormationFailure as error:
        return None, f"formation failed: {error.code}"


def _parse_roundtrip(vector: runner.Case) -> str | None:
    document, message = _parse_json(vector)
    if message:
        return message
    source = compare.string_field(vector.input, "source")
    expected_formation = compare.string_field(vector.expected, "formation")
    if expected_formation is not None:
        actual = document.formation_status().value
        if actual != expected_formation:
            return f"formation: expected {expected_formation}, got {actual}"
    expected_diagnostic = compare.string_field(vector.expected, "diagnostic")
    if expected_diagnostic is not None:
        codes = [diagnostic.code for diagnostic in document.diagnostic_records()]
        if expected_diagnostic not in codes:
            return f"diagnostic {expected_diagnostic!r} not found in {codes!r}"
    if compare.boolean_field(vector.expected, "render_equals_source") is True:
        if document.render() != source.encode("utf-8"):
            return "render does not equal source"
    if vector.id == "parse.duplicate-members":
        expected_names = compare.string_sequence(vector.expected, "member_names")
        if expected_names is None:
            return "missing expected.member_names"
        root = document.root()
        availability = root.object_members()
        if not availability.is_available:
            return "root members unavailable"
        names = [member.name().value if member.name().is_available else None for member in availability.value]
        if names != expected_names:
            return f"member_names: expected {expected_names!r}, got {names!r}"
        if compare.boolean_field(vector.expected, "distinct_member_identity") is not True:
            return "expected.distinct_member_identity must be true"
        identities = [member.ordinal() for member in availability.value]
        if len(set(identities)) != len(identities):
            return "member identities are not distinct"
    return None


def _lossless_coverage(vector: runner.Case) -> str | None:
    document, message = _parse_json(vector)
    if message:
        return message
    index = document.lossless_structural_index()
    pieces = index.pieces
    gap = 0
    overlap = 0
    previous_end = 0
    for piece in pieces:
        start = piece.span.start_byte
        end = piece.span.end_byte
        if start > previous_end:
            gap += start - previous_end
        if start < previous_end:
            overlap += previous_end - start
        previous_end = max(previous_end, end)
    expected_gap = compare.integer_field(vector.expected, "gap_count")
    expected_overlap = compare.integer_field(vector.expected, "overlap_count")
    expected_covered = compare.integer_field(vector.expected, "covered_bytes")
    if expected_gap is None or expected_overlap is None or expected_covered is None:
        return "missing expected coverage facts"
    if gap != expected_gap:
        return f"gap_count: expected {expected_gap}, got {gap}"
    if overlap != expected_overlap:
        return f"overlap_count: expected {expected_overlap}, got {overlap}"
    if previous_end != expected_covered:
        return f"covered_bytes: expected {expected_covered}, got {previous_end}"
    return None


def _parse_token_limit(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    max_tokens = compare.integer_field(vector.input, "max_token_count")
    if max_tokens is None:
        return "missing input.max_token_count"
    profile = json_kinds.JsonProfile.STRICT_V1
    limits = ParseLimits(max_token_count=max_tokens)
    failed = False
    try:
        json_parser.parse(source.encode("utf-8"), profile, limits)
    except json_errors.JsonFormationFailure:
        failed = True
    expected_status = compare.string_field(vector.expected, "status")
    if expected_status == "FatalFormationFailure" and not failed:
        return "parse must fail fatally"
    if expected_status != "FatalFormationFailure" and failed:
        return "parse must succeed"
    if compare.boolean_field(vector.expected, "truncated_success") is True:
        return "truncated_success must be false"
    return None


# ---------------------------------------------------------------------------
# query cases
# ---------------------------------------------------------------------------


def _pipeline_expression(pipeline: tuple, arguments: dict[str, str] | None = None):
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    operators = []
    for spelling in pipeline:
        operator_id, version_text = spelling.rsplit("@", 1)
        operators.append(protocol_query.OperatorCall(operator_id, int(version_text)))
    for index, operator in enumerate(operators):
        if arguments is not None and index == len(operators) - 1:
            for name, argument in arguments.items():
                operator.with_argument(name, PortableValue.string(argument))
        expression = expression.then(operator)
    return expression


def _ordered_results_capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _query_role_mismatch(vector: runner.Case) -> str | None:
    domain_spelling = compare.string_field(vector.input, "domain")
    if domain_spelling != "core.portable-value-query@1":
        return "input.domain must be core.portable-value-query@1"
    pipeline = compare.string_sequence(vector.input, "pipeline")
    if pipeline is None:
        return "missing input.pipeline"
    expression = _pipeline_expression(tuple(pipeline))
    definition = protocol_query.QueryDefinition(protocol_query.domain_portable_value_v1())
    definition = definition.with_expression(expression)
    try:
        definition.validate()
    except protocol_query.QueryFailure as failure:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure != core_query.query_failure_name(failure):
            return f"failure: expected {expected_failure}, got {core_query.query_failure_name(failure)}"
        return None
    return "validation must fail"


def _query_json_duplicate_order(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    member_name = compare.string_field(vector.input, "member_name")
    if source is None or member_name is None:
        return "missing input.source/member_name"
    try:
        document = json_parser.parse(
            source.encode("utf-8"), json_kinds.JsonProfile.STRICT_V1, ParseLimits()
        )
    except json_errors.JsonFormationFailure as error:
        return f"formation failed: {error.code}"
    expression = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("json.try-object-members", 1))
        .then(
            protocol_query.OperatorCall("json.member-name-equals", 1).with_argument(
                "name", PortableValue.string(member_name)
            )
        )
    )
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_json_native_v1())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results_capabilities())
    )
    execution = json_query.execute_json_query(
        definition, document, json_query.JsonQueryLimits(), json_query.JsonCancellationToken()
    )
    expected_ordinals = compare.integer_sequence(vector.expected, "ordinals")
    expected_count = compare.integer_field(vector.expected, "count")
    if expected_ordinals is None or expected_count is None:
        return "missing expected.ordinals/count"
    matches = list(execution.matches)
    if len(matches) != expected_count:
        return f"count: expected {expected_count}, got {len(matches)}"
    ordinals = []
    for match in matches:
        if match.kind is not json_query.JsonMatchKind.OBJECT_MEMBER:
            return f"match kind: expected ObjectMember, got {match.kind.value}"
        ordinals.append(match.ordinal)
    return compare.require_ordered(ordinals, expected_ordinals, "ordinals")


def _query_protocol_roundtrip(vector: runner.Case) -> str | None:
    domain_spelling = compare.string_field(vector.input, "domain")
    operator_spelling = compare.string_field(vector.input, "operator")
    selection_spelling = compare.string_field(vector.input, "selection")
    if domain_spelling != "core.portable-value-query@1":
        return "input.domain must be core.portable-value-query@1"
    if operator_spelling != "core.try-sequence-elements@1":
        return "input.operator must be core.try-sequence-elements@1"
    if selection_spelling not in ("All", "First", "Last", "ZeroOrOne", "RequireOne"):
        return f"unknown selection {selection_spelling}"
    if compare.boolean_field(vector.expected, "roundtrip_equal") is not True:
        return "expected.roundtrip_equal must be true"
    if compare.string_field(vector.expected, "unknown_field") != "Reject":
        return "expected.unknown_field must be Reject"
    operator_id, version_text = operator_spelling.rsplit("@", 1)
    definition = protocol_query.QueryDefinition(protocol_query.domain_portable_value_v1())
    definition = definition.with_expression(
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
            protocol_query.OperatorCall(operator_id, int(version_text))
        )
    )
    definition = definition.with_selection(protocol_query.QuerySelection(selection_spelling))
    value = protocol_query.QueryDefinitionCodec.to_value(definition)
    decoded = protocol_query.QueryDefinitionCodec.from_value(value)
    if not core_equal(value, protocol_query.QueryDefinitionCodec.to_value(decoded)):
        return "definition round trip must be strictly equal"
    return None


def _query_root_result_limit(vector: runner.Case) -> str | None:
    domain_spelling = compare.string_field(vector.input, "domain")
    if domain_spelling != "core.portable-value-query@1":
        return "input.domain must be core.portable-value-query@1"
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is None:
        return "missing input.max_results"
    matches = core_query.execute_portable(PortableValue.null(), protocol_query.QueryExpression(
        protocol_query.ExpressionKind.INPUT), max_results=max_results)
    if len(matches) > 0:
        return "execution must fail"
    if compare.string_field(vector.expected, "status") != "Failed":
        return "expected.status must be Failed"
    if compare.string_field(vector.expected, "failure") != "ResourceLimitExceeded":
        return "expected.failure must be ResourceLimitExceeded"
    return None


def _query_cursor_failure_terminal(vector: runner.Case) -> str | None:
    elements = compare.sequence_field(vector.input, "elements")
    max_results = compare.integer_field(vector.input, "max_results")
    if elements is None or max_results is None:
        return "missing input.elements/max_results"
    values = []
    for element in elements:
        if element.kind is Kind.OBJECT:
            spelling = compare.string_field(element, "integer")
            if spelling is None:
                return "invalid element descriptor"
            values.append(PortableValue.integer(int(spelling)))
        else:
            values.append(element)
    pipeline = compare.string_sequence(vector.input, "pipeline")
    if pipeline is None:
        return "missing input.pipeline"
    expression = core_query.build_pipeline("core.portable-value-query", 1, pipeline)
    root = PortableValue.sequence(tuple(values))
    cursor = core_query.CoreCursor(
        core_query.execute_portable(root, expression), max_results=max_results
    )
    while cursor.next() is not None:
        pass
    yielded = cursor.yielded()
    expected_yielded = compare.integer_field(vector.expected, "yielded_before_failure")
    expected_terminal = compare.string_field(vector.expected, "terminal")
    if expected_yielded is None or expected_terminal is None:
        return "missing expected facts"
    if yielded != expected_yielded:
        return f"yielded_before_failure: expected {expected_yielded}, got {yielded}"
    if cursor.terminal_state() != expected_terminal:
        return f"terminal: expected {expected_terminal}, got {cursor.terminal_state()}"
    return None


# ---------------------------------------------------------------------------
# projection cases
# ---------------------------------------------------------------------------


def _projection_best_exact(vector: runner.Case) -> str | None:
    document, message = _parse_json(vector)
    if message:
        return message
    request = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    ).build()
    result = json_projection.project(document, request)
    if isinstance(result, json_projection.FailedProjectionAttempt):
        return "projection must complete"
    projection = result
    expected_kind = compare.string_field(vector.expected, "kind")
    expected_fidelity = compare.string_field(vector.expected, "fidelity")
    if expected_kind is None or expected_fidelity is None:
        return "missing expected.kind/fidelity"
    actual_kind = projection.value.kind.value
    if actual_kind != expected_kind:
        return f"kind: expected {expected_kind}, got {actual_kind}"
    if projection.fidelity.value != expected_fidelity:
        return f"fidelity: expected {expected_fidelity}, got {projection.fidelity.value}"
    association_origins = 0
    for entry in projection.provenance.entries:
        if entry.projected.kind is json_projection.ProjectedLocationKind.ASSOCIATION:
            association_origins += len(entry.origins)
    expected_origins = compare.integer_field(vector.expected, "association_origins")
    if expected_origins is not None and association_origins != expected_origins:
        return f"association_origins: expected {expected_origins}, got {association_origins}"
    return None


def _projection_object(vector: runner.Case) -> str | None:
    document, message = _parse_json(vector)
    if message:
        return message
    builder = json_projection.ProjectionRequestBuilder(
        json_projection.ProjectionTarget.PROJECT_AS_OBJECT_V1
    )
    duplicates = compare.string_field(vector.input, "duplicates")
    if duplicates == "Reject":
        builder.global_duplicate_policy(json_projection.DuplicateKeyPolicy.REJECT)
    elif duplicates == "LastWins":
        builder.global_duplicate_policy(json_projection.DuplicateKeyPolicy.LAST_WINS)
    result = json_projection.project(document, builder.build())
    if vector.id == "projection.object-reject-duplicates":
        if compare.string_field(vector.expected, "status") != "Failed":
            return "expected.status must be Failed"
        if not isinstance(result, json_projection.FailedProjectionAttempt):
            return "projection must fail"
        if compare.boolean_field(vector.expected, "partial_value") is True:
            return "partial_value must be false"
        return None
    if vector.id == "projection.object-last-wins":
        if compare.string_field(vector.expected, "status") != "Success":
            return "expected.status must be Success"
        if isinstance(result, json_projection.FailedProjectionAttempt):
            return "projection must complete"
        projection = result
        if projection.fidelity.value != "Lossy":
            return f"fidelity: expected Lossy, got {projection.fidelity.value}"
        expected_events = compare.string_sequence(vector.expected, "events")
        if expected_events != ["DuplicateCollapsed"]:
            return "expected.events must be [DuplicateCollapsed]"
        kinds = [event.kind.value for event in projection.report.events]
        if "DuplicateCollapsed" not in kinds:
            return f"DuplicateCollapsed event missing in {kinds!r}"
        return None
    if vector.id == "projection.object-key-provenance":
        if isinstance(result, json_projection.FailedProjectionAttempt):
            return "projection must complete"
        projection = result
        key_origins = 0
        entry_origins = 0
        for entry in projection.provenance.entries:
            if entry.projected.kind is not json_projection.ProjectedLocationKind.ASSOCIATION:
                continue
            role = entry.projected.association.role
            if role is json_projection.AssociationRole.OBJECT_KEY:
                key_origins += len(entry.origins)
            if role is json_projection.AssociationRole.OBJECT_ENTRY:
                entry_origins += len(entry.origins)
        expected_key = compare.integer_field(vector.expected, "key_association_origins")
        expected_entry = compare.integer_field(vector.expected, "entry_association_origins")
        if expected_key is None or expected_entry is None:
            return "missing expected association facts"
        if key_origins != expected_key:
            return f"key_association_origins: expected {expected_key}, got {key_origins}"
        if entry_origins != expected_entry:
            return f"entry_association_origins: expected {expected_entry}, got {entry_origins}"
        return None
    return "unrecognized projection case"


# ---------------------------------------------------------------------------
# edit cases
# ---------------------------------------------------------------------------


def _new_value(value) -> PortableValue | None:
    if value.kind is not Kind.OBJECT:
        return None
    for key, item in value.as_object():
        if key == "integer":
            return PortableValue.integer(int(item.as_string()))
        if key == "decimal":
            return PortableValue.decimal(_decimal_from_spelling(item.as_string()))
        if key == "string":
            return PortableValue.string(item.as_string())
    return None


def _representation_policy(spelling: str):
    policies = {
        "PreserveCompatible": json_edit.RepresentationPolicy.PRESERVE_COMPATIBLE,
        "CanonicalForProfile": json_edit.RepresentationPolicy.CANONICAL_FOR_PROFILE,
        "PreserveElseCanonical": json_edit.RepresentationPolicy.PRESERVE_ELSE_CANONICAL,
        "ExactLiteral": json_edit.RepresentationPolicy.EXACT_LITERAL,
    }
    return policies.get(spelling)


def _edit_scalar(vector: runner.Case) -> str | None:
    document, message = _parse_json(vector)
    if message:
        return message
    new_value = _new_value(compare.object_field(vector.input, "new_value"))
    if new_value is None:
        return "missing input.new_value"
    policy = _representation_policy(compare.string_field(vector.input, "policy"))
    if policy is None:
        return "missing input.policy"
    root = document.root()
    members = root.object_members()
    if not members.is_available or not members.value:
        return "root object members unavailable"
    target = members.value[0].value().node_ref()
    builder = json_edit.EditTransactionBuilder(document).semantic_scalar(target, new_value, policy)
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as failure:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return f"edit failed: {failure.name}"
        if failure.name != expected_failure:
            return f"failure: expected {expected_failure}, got {failure.name}"
        if compare.string_field(vector.expected, "status") != "Failed":
            return "expected.status must be Failed"
        return None
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    expected_edits = compare.integer_field(vector.expected, "source_edit_count")
    if expected_edits is not None and len(commit.change_set.source_edits) != expected_edits:
        return f"source_edit_count: expected {expected_edits}, got {len(commit.change_set.source_edits)}"
    expected_fallback = compare.integer_field(vector.expected, "fallback_diagnostics")
    if expected_fallback is not None:
        codes = [diagnostic.code for diagnostic in commit.change_set.diagnostics]
        fallback = sum(1 for code in codes if code == "json.edit.representation-fallback@1")
        if fallback != expected_fallback:
            return f"fallback_diagnostics: expected {expected_fallback}, got {fallback}"
    return None


def _edit_wrong_snapshot(vector: runner.Case) -> str | None:
    first = compare.string_field(vector.input, "first")
    second = compare.string_field(vector.input, "second")
    literal = compare.string_field(vector.input, "literal")
    if first is None or second is None or literal is None:
        return "missing input.first/second/literal"
    try:
        first_doc = json_parser.parse(first.encode("utf-8"), json_kinds.JsonProfile.STRICT_V1, ParseLimits())
        second_doc = json_parser.parse(second.encode("utf-8"), json_kinds.JsonProfile.STRICT_V1, ParseLimits())
    except json_errors.JsonFormationFailure as error:
        return f"formation failed: {error.code}"
    builder = json_edit.EditTransactionBuilder(first_doc).literal_scalar(
        first_doc.root().node_ref(), literal.encode("utf-8")
    )
    expected_failure = compare.string_field(vector.expected, "failure")
    try:
        json_edit.commit(second_doc, builder.build())
    except json_errors.JsonEditFailure as failure:
        if expected_failure is not None and failure.name != expected_failure:
            return f"failure: expected {expected_failure}, got {failure.name}"
    else:
        return "commit must fail on a wrong-snapshot transaction"
    if compare.boolean_field(vector.expected, "second_unchanged") is not True:
        return "expected.second_unchanged must be true"
    if second_doc.render() != second.encode("utf-8"):
        return "second document must stay unchanged"
    return None


runner.register_suite("v1.json", "consema.conformance@1", "", 30, run)
