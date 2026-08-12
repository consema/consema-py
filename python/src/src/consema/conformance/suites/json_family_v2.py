"""Suite ``consema.json-family.conformance@2`` (json-family-v2.json, 33
cases): the JSON5/JSONC formation surface, lossless-syntax and native-
semantic v2 queries, JSON5 best-exact projection, canonical materialization,
dialect conversion, structural move-member and scalar-preserving edits, the
semantic-model v4 registry facts, and the parse depth limit. Dispatch is by
``input.action``, mirroring go/conformance/json_family_v2.go.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import core_query
from consema.conformance import runner
from consema.core.value import Kind, PortableValue
from consema.core.value import decimal as core_decimal
from consema.document.edit_plan import EditPlanSourceId
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import MaterializationRequest, NewlinePolicy
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import AssociationPlacement
from consema.json import document as json_document
from consema.json import edit as json_edit
from consema.json import errors as json_errors
from consema.json import kinds as json_kinds
from consema.json import materialization as json_materialization
from consema.json import parser as json_parser
from consema.json import projection as json_projection
from consema.json import query as json_query
from consema.protocol import query as protocol_query
from consema.protocol.contract import ContractRegistry
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet

_PROFILES = {
    "json.strict@1": json_kinds.JsonProfile.STRICT_V1,
    "jsonc.bounded@1": json_kinds.JsonProfile.JSONC_BOUNDED_V1,
    "json5.standard@1": json_kinds.JsonProfile.JSON5_STANDARD_V1,
}

_PROJECTION_TARGETS = {
    "json5-best-exact": json_projection.ProjectionTarget.JSON5_BEST_EXACT_CORE_V1,
    "json-best-exact": json_projection.ProjectionTarget.BEST_EXACT_CORE_V1,
}


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "parse": _parse_case,
        "syntax-query": _syntax_query_case,
        "native-query": _native_query_case,
        "project": _project_case,
        "materialize": _materialize_case,
        "convert": _convert_case,
        "move-member": _move_member_case,
        "edit-scalars": _edit_scalars_case,
        "registry-v4": _registry_v4_case,
        "parse-limit": _parse_limit_case,
    }
    for vector in data.cases:
        action = compare.string_field(vector.input, "action")
        if action is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="missing input.action")
            )
            continue
        handler = handlers.get(action)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="unknown input.action " + action)
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _parse_document(source: str, profile: json_kinds.JsonProfile) -> json_document.JsonDocument:
    return json_parser.parse(source.encode("utf-8"), profile, ParseLimits())


def _ordered_results() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _profile_id_from_name(name: str) -> ProfileId | None:
    """Resolves one versioned profile spelling ("json5.standard@1")."""
    id_part, separator, version_text = name.rpartition("@")
    if not separator or not version_text:
        return None
    return ProfileId.new(id_part, int(version_text))


def _materialization_request(
    profile: json_kinds.JsonProfile, style: str
) -> MaterializationRequest:
    """Strict request with the frozen style id and no trailing newline
    (materialization.rs:122-132; json_family_v2.go:470)."""
    return MaterializationRequest.new(
        profile.id(), MaterializationStyleId.new(style, 1)
    ).with_newline(NewlinePolicy.NONE)


def _source_patch_limits(document: json_document.JsonDocument) -> SourcePatchLimits:
    limits = document.parse_limits
    return SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=limits.max_source_bytes,
            max_decoded_utf8_bytes=limits.max_source_bytes,
            max_decoded_scalars=limits.max_source_bytes,
        ),
        max_replacements=len(document.entities) + 1,
        max_patch_bytes=limits.max_source_bytes * 2,
    )


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def _parse_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    profile_name = compare.string_field(vector.input, "profile")
    profile = _PROFILES.get(profile_name or "")
    if profile is None:
        return "unknown profile " + (profile_name or "")
    try:
        document = _parse_document(source, profile)
    except json_errors.JsonFormationFailure as error:
        return f"formation failed: {error.code}"
    if document.render() != source.encode("utf-8"):
        return "render differs from source"
    expected_formation = compare.string_field(vector.expected, "formation")
    if expected_formation is not None and document.formation_status().value != expected_formation:
        return f"formation {document.formation_status().value} != {expected_formation}"
    diagnostic_codes = [diagnostic.code for diagnostic in document.diagnostic_records()]
    for code in compare.string_sequence(vector.expected, "diagnostic_contains") or []:
        if code not in diagnostic_codes:
            return f"missing diagnostic {code}"
    syntax_kinds = document.lossless_syntax_kinds()
    for kind_name in compare.string_sequence(vector.expected, "syntax_contains") or []:
        if not any(kind.as_str() == kind_name for kind in syntax_kinds):
            return f"missing syntax kind {kind_name}"
    root = document.root()
    expected_kind = compare.string_field(vector.expected, "root_kind")
    if expected_kind is not None:
        availability = root.kind()
        actual = availability.value.value if availability.is_available else None
        if actual != expected_kind:
            return f"root kind {actual} != {expected_kind}"
    expected_bits = compare.string_field(vector.expected, "root_bits")
    if expected_bits is not None:
        binary = root.as_binary_float64()
        if not binary.is_available:
            return "root is not BinaryFloat64"
        got = f"{binary.value:016x}"
        if got != expected_bits:
            return f"root bits {got} != {expected_bits}"
    expected_integer = compare.string_field(vector.expected, "root_integer")
    if expected_integer is not None:
        integer = root.as_integer()
        if not integer.is_available or str(integer.value) != expected_integer:
            return "root integer mismatch"
    expected_names = compare.string_sequence(vector.expected, "member_names")
    expected_member_kinds = compare.string_sequence(vector.expected, "member_kinds")
    if expected_names is not None or expected_member_kinds is not None:
        members_availability = root.object_members()
        if not members_availability.is_available:
            return "root is not an object"
        members = members_availability.value
        if expected_names is not None:
            names: list[str] = []
            for member in members:
                name = member.name()
                if not name.is_available:
                    return "member name unavailable"
                names.append(name.value)
            if names != expected_names:
                return f"member names {names!r} != {expected_names!r}"
        if expected_member_kinds is not None:
            kinds: list[str] = []
            for member in members:
                kind = member.value().kind()
                if not kind.is_available:
                    return "member kind unavailable"
                kinds.append(kind.value.value)
            if kinds != expected_member_kinds:
                return f"member kinds {kinds!r} != {expected_member_kinds!r}"
    expected_element_kinds = compare.string_sequence(vector.expected, "element_kinds")
    expected_element_strings = compare.string_sequence(vector.expected, "element_strings")
    expected_decimals = compare.sequence_field(vector.expected, "element_decimals")
    if (
        expected_element_kinds is not None
        or expected_element_strings is not None
        or expected_decimals is not None
    ):
        elements_availability = root.array_elements()
        if not elements_availability.is_available:
            return "root is not an array"
        values = [element.value() for element in elements_availability.value]
        if expected_element_kinds is not None:
            kinds: list[str] = []
            for value in values:
                kind = value.kind()
                if not kind.is_available:
                    return "element kind unavailable"
                kinds.append(kind.value.value)
            if kinds != expected_element_kinds:
                return f"element kinds {kinds!r} != {expected_element_kinds!r}"
        if expected_element_strings is not None:
            strings: list[str] = []
            for value in values:
                text = value.as_string()
                if not text.is_available:
                    return "element is not a string"
                strings.append(text.value)
            if strings != expected_element_strings:
                return f"element strings {strings!r} != {expected_element_strings!r}"
        if expected_decimals is not None:
            pairs: list[str] = []
            for value in values:
                decimal = value.as_decimal()
                if not decimal.is_available:
                    return "element is not a decimal"
                pairs.append(f"{decimal.value.coefficient}\x00{decimal.value.exponent}")
            expected_pairs: list[str] = []
            for pair in expected_decimals:
                if pair.kind is not Kind.SEQUENCE or len(pair.as_sequence()) != 2:
                    return "invalid element_decimal pair"
                coefficient, exponent = pair.as_sequence()
                expected_pairs.append(f"{coefficient.as_string()}\x00{exponent.as_string()}")
            if pairs != expected_pairs:
                return f"element decimals {pairs!r} != {expected_pairs!r}"
    return None


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def _syntax_query_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    kind_name = compare.string_field(vector.input, "kind")
    if source is None or kind_name is None:
        return "missing input.source/kind"
    document = _parse_document(source, json_kinds.JsonProfile.JSON5_STANDARD_V1)
    expression = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(
            protocol_query.OperatorCall("json.syntax-kind-is", 1).with_argument(
                "kind", PortableValue.string(kind_name)
            )
        )
    )
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_json_lossless_syntax_v2())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results())
    )
    execution = json_query.execute_json_syntax_query(
        definition, document, json_query.JsonQueryLimits(), json_query.JsonCancellationToken()
    )
    raw = source.encode("utf-8")
    actual = [
        raw[match.span.start_byte : match.span.end_byte].decode("utf-8")
        for match in execution.matches
    ]
    expected = compare.string_sequence(vector.expected, "texts")
    if expected is None:
        return "missing expected.texts"
    if actual != expected:
        return f"texts {actual!r} != {expected!r}"
    if compare.boolean_field(vector.expected, "v1_rejected") is True:
        v1_definition = (
            protocol_query.QueryDefinition(protocol_query.domain_json_lossless_syntax_v1())
            .with_expression(protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT))
            .validate()
            .bind(_ordered_results())
        )
        try:
            json_query.execute_json_syntax_query(
                v1_definition, document, json_query.JsonQueryLimits(),
                json_query.JsonCancellationToken(),
            )
        except protocol_query.QueryFailure as failure:
            if core_query.query_failure_name(failure) != "DomainMismatch":
                return f"v1 query must be domain-rejected, got {core_query.query_failure_name(failure)}"
        else:
            return "v1 query must be domain-rejected on JSON5"
    return None


def _native_query_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_document(source, json_kinds.JsonProfile.JSON5_STANDARD_V1)
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_json_native_v2())
        .with_expression(protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT))
        .validate()
        .bind(_ordered_results())
    )
    execution = json_query.execute_json_query(
        definition, document, json_query.JsonQueryLimits(), json_query.JsonCancellationToken()
    )
    matches = list(execution.matches)
    if len(matches) != 1 or matches[0].kind is not json_query.JsonMatchKind.VALUE:
        return "native v2 root result is not one available value"
    availability = document.root().kind()
    if not availability.is_available:
        return "root kind unavailable"
    expected = compare.string_field(vector.expected, "kind")
    if expected is None:
        return "missing expected.kind"
    if availability.value.value != expected:
        return f"kind {availability.value.value} != {expected}"
    if compare.boolean_field(vector.expected, "v1_rejected") is True:
        v1_definition = (
            protocol_query.QueryDefinition(protocol_query.domain_json_native_v1())
            .with_expression(protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT))
            .validate()
            .bind(_ordered_results())
        )
        try:
            json_query.execute_json_query(
                v1_definition, document, json_query.JsonQueryLimits(),
                json_query.JsonCancellationToken(),
            )
        except protocol_query.QueryFailure as failure:
            if core_query.query_failure_name(failure) != "DomainMismatch":
                return f"v1 query must be domain-rejected, got {core_query.query_failure_name(failure)}"
        else:
            return "v1 query must be domain-rejected on JSON5"
    return None


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------


def _project_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    target_name = compare.string_field(vector.input, "target")
    if source is None or target_name is None:
        return "missing input.source/target"
    target = _PROJECTION_TARGETS.get(target_name)
    if target is None:
        return "unknown projection target " + target_name
    document = _parse_document(source, json_kinds.JsonProfile.JSON5_STANDARD_V1)
    request = json_projection.ProjectionRequestBuilder(target).build()
    result = json_projection.project(document, request)
    complete_expected = compare.boolean_field(vector.expected, "complete")
    if isinstance(result, json_projection.CompleteProjection):
        if complete_expected is not True:
            return "projection unexpectedly completed"
        expected_kind = compare.string_field(vector.expected, "kind")
        if expected_kind is None:
            return "missing expected.kind"
        if result.value.kind.value != expected_kind:
            return f"kind {result.value.kind.value} != {expected_kind}"
        expected_bits = compare.string_sequence(vector.expected, "binary_bits")
        if expected_bits is not None:
            if result.value.kind is not Kind.ENTRY_MAPPING:
                return "projection is not EntryMapping"
            actual = []
            for _key, entry_value in result.value.as_entry_mapping():
                if entry_value.kind is not Kind.BINARY_FLOAT64:
                    return "entry is not BinaryFloat64"
                actual.append(f"{entry_value.as_binary_float64():016x}")
            if actual != expected_bits:
                return f"binary bits {actual!r} != {expected_bits!r}"
    else:
        if complete_expected is True:
            return "projection unexpectedly failed"
        expected_code = compare.string_field(vector.expected, "code")
        if expected_code is None:
            return "missing expected.code"
        codes = [diagnostic.code for diagnostic in result.diagnostics]
        if expected_code not in codes:
            return f"missing failure code {expected_code} in {codes!r}"
    return None


# ---------------------------------------------------------------------------
# materialization
# ---------------------------------------------------------------------------


def _materialization_value(value: PortableValue) -> PortableValue | None:
    """One vector value descriptor {bits|string|null}
    (json_family_v2.go:833-848)."""
    bits = compare.string_field(value, "bits")
    if bits is not None:
        return PortableValue.binary_float64(int(bits, 16))
    text = compare.string_field(value, "string")
    if text is not None:
        return PortableValue.string(text)
    if compare.boolean_field(value, "null") is True:
        return PortableValue.null()
    return None


def _materialize_case(vector: runner.Case) -> str | None:
    profile_name = compare.string_field(vector.input, "profile")
    style = compare.string_field(vector.input, "style")
    values = compare.sequence_field(vector.input, "values")
    if profile_name is None or style is None or values is None:
        return "missing input.profile/style/values"
    profile = _PROFILES.get(profile_name)
    if profile is None:
        return "unknown profile " + profile_name
    items = []
    for value in values:
        converted = _materialization_value(value)
        if converted is None:
            return "unrepresentable materialization value"
        items.append(converted)
    request = _materialization_request(profile, style)
    result = json_materialization.materialize(PortableValue.sequence(items), request)
    if isinstance(result, json_materialization.CompleteMaterialization):
        expected_output = compare.string_field(vector.expected, "output")
        if expected_output is None:
            return "missing expected.output"
        actual = result.document.render().decode("utf-8")
        if actual != expected_output:
            return f"output mismatch: got {actual!r}"
    else:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return "missing expected.failure"
        actual = json_materialization.materialization_failure_name(result.failure)
        if actual != expected_failure:
            return f"failure {actual} != {expected_failure}"
    return None


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------


def _convert_case(vector: runner.Case) -> str | None:
    from consema import convert as consema_convert

    source_profile_name = compare.string_field(vector.input, "source_profile")
    source = compare.string_field(vector.input, "source")
    target_profile_name = compare.string_field(vector.input, "target_profile")
    style = compare.string_field(vector.input, "style")
    if source_profile_name is None or source is None or target_profile_name is None or style is None:
        return "missing input.source_profile/source/target_profile/style"
    profile = _PROFILES.get(source_profile_name)
    if profile is None:
        return "unknown source profile " + source_profile_name
    target_profile = _profile_id_from_name(target_profile_name)
    if target_profile is None:
        return "malformed target profile " + target_profile_name
    document = _parse_document(source, profile)
    target = (
        json_projection.ProjectionTarget.JSON5_BEST_EXACT_CORE_V1
        if profile is json_kinds.JsonProfile.JSON5_STANDARD_V1
        else json_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    projection_request = json_projection.ProjectionRequestBuilder(target).build()
    materialization_request = (
        MaterializationRequest.new(
            target_profile, MaterializationStyleId.new(style, 1)
        ).with_newline(NewlinePolicy.NONE)
    )
    result = consema_convert.convert_json(document, projection_request, materialization_request)
    if isinstance(result, consema_convert.CompleteConversion):
        expected_output = compare.string_field(vector.expected, "output")
        if expected_output is None:
            return "missing expected.output"
        actual = result.document.render().decode("utf-8")
        if actual != expected_output:
            return f"output mismatch: got {actual!r}"
        expected_fidelity = compare.string_field(vector.expected, "fidelity")
        if (
            expected_fidelity is not None
            and result.report.overall_fidelity.value != expected_fidelity
        ):
            return f"fidelity {result.report.overall_fidelity.value} != {expected_fidelity}"
    else:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return "missing expected.failure"
        failure = result.materialization_failure
        if failure is None:
            return f"conversion failed without a materialization failure: {result.kind.value}"
        actual = json_materialization.materialization_failure_name(failure)
        if actual != expected_failure:
            return f"failure {actual} != {expected_failure}"
    return None


# ---------------------------------------------------------------------------
# edits
# ---------------------------------------------------------------------------


def _ordinal_path(value, name: str) -> list[int] | None:
    items = compare.sequence_field(value, name)
    if items is None:
        return None
    path: list[int] = []
    for item in items:
        if item.kind is not Kind.INTEGER:
            return None
        number = item.as_integer()
        if number < 0:
            return None
        path.append(number)
    return path


def _resolve_member(
    document: json_document.JsonDocument, path: list[int]
) -> json_document.JsonObjectMember | None:
    """Ordinal member-path resolution (json_family_v2.go:906-926)."""
    if not path:
        return None
    value = document.root()
    for depth, ordinal in enumerate(path):
        members_availability = value.object_members()
        if not members_availability.is_available:
            return None
        members = members_availability.value
        if ordinal >= len(members):
            return None
        if depth + 1 == len(path):
            return members[ordinal]
        value = members[ordinal].value()
    return None


def _move_member_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    profile_name = compare.string_field(vector.input, "profile")
    if source is None or profile_name is None:
        return "missing input.source/profile"
    profile = _PROFILES.get(profile_name)
    if profile is None:
        return "unknown profile " + profile_name
    document = _parse_document(source, profile)
    target_path = _ordinal_path(vector.input, "target_path")
    if target_path is None:
        return "missing input.target_path"
    target = _resolve_member(document, target_path)
    if target is None:
        return "target path does not resolve"
    placement_name = compare.string_field(vector.input, "placement")
    if placement_name is None:
        return "missing input.placement"
    if placement_name in ("start", "end"):
        placement = AssociationPlacement(
            "Start" if placement_name == "start" else "End"
        )
    elif placement_name in ("before", "after"):
        anchor_path = _ordinal_path(vector.input, "anchor_path")
        if anchor_path is None:
            return "missing input.anchor_path"
        anchor = _resolve_member(document, anchor_path)
        if anchor is None:
            return "anchor path does not resolve"
        placement = AssociationPlacement(
            "Before" if placement_name == "before" else "After",
            anchor=anchor.node_ref(),
        )
    else:
        return "unknown placement " + placement_name
    builder = json_edit.EditTransactionBuilder(document).move_member(
        target.node_ref(), placement
    )
    transaction = builder.build()
    try:
        commit = json_edit.commit(document, transaction)
    except json_errors.JsonEditFailure as failure:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return f"edit failed: {failure.name}"
        if failure.name != expected_failure:
            return f"failure {failure.name} != {expected_failure}"
        return None
    expected_output = compare.string_field(vector.expected, "output")
    if expected_output is None:
        return "missing expected.output"
    actual = commit.document.render().decode("utf-8")
    if actual != expected_output:
        return f"output mismatch: got {actual!r}"
    plan = json_edit.dry_run(document, transaction, EditPlanSourceId.new("conformance.json5"))
    artifacts_equal = (
        list(plan.replacements()) == list(commit.source_patch.replacements)
        and plan.target_digest() == commit.source_patch.target_digest
    )
    patch_equal = compare.boolean_field(vector.expected, "patch_equal")
    if patch_equal is not None and artifacts_equal != patch_equal:
        return "plan and commit artifacts differ"
    reapplied = commit.source_patch.apply(document.source, _source_patch_limits(document))
    if reapplied.bytes() != commit.document.source.bytes():
        return "patch does not reapply to the output"
    proof_ok = True
    try:
        commit.untouched_proof.verify(
            document.source, commit.document.source, list(commit.source_patch.replacements)
        )
    except Exception:
        proof_ok = False
    proof_valid = compare.boolean_field(vector.expected, "proof_valid")
    if proof_valid is not None and proof_ok != proof_valid:
        return "untouched proof verdict differs"
    return None


def _scalar_replacement(value: PortableValue) -> PortableValue | None:
    """One vector scalar replacement descriptor
    (json_family_v2.go:850-884)."""
    integer_text = compare.string_field(value, "integer")
    if integer_text is not None:
        return PortableValue.integer(int(integer_text))
    coefficient_text = compare.string_field(value, "decimal_coefficient")
    if coefficient_text is not None:
        exponent_text = compare.string_field(value, "decimal_exponent")
        if exponent_text is None:
            return None
        return PortableValue.decimal(
            core_decimal(int(coefficient_text), int(exponent_text))
        )
    string_text = compare.string_field(value, "string")
    if string_text is not None:
        return PortableValue.string(string_text)
    bits = compare.string_field(value, "bits")
    if bits is not None:
        return PortableValue.binary_float64(int(bits, 16))
    return None


def _edit_scalars_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_document(source, json_kinds.JsonProfile.JSON5_STANDARD_V1)
    members_availability = document.root().object_members()
    if not members_availability.is_available:
        return "root is not an object"
    members = members_availability.value
    replacements = compare.sequence_field(vector.input, "replacements")
    if replacements is None:
        return "missing input.replacements"
    builder = json_edit.EditTransactionBuilder(document)
    for item in replacements:
        ordinal = compare.integer_field(item, "ordinal")
        if ordinal is None:
            return "replacement ordinal is invalid"
        value = _scalar_replacement(item)
        if value is None:
            return "replacement value is invalid"
        if ordinal >= len(members):
            return "replacement ordinal out of range"
        builder.semantic_scalar(
            members[ordinal].value_node_ref(),
            value,
            json_edit.RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
    try:
        commit = json_edit.commit(document, builder.build())
    except json_errors.JsonEditFailure as failure:
        return f"edit failed: {failure.name}"
    expected_output = compare.string_field(vector.expected, "output")
    if expected_output is None:
        return "missing expected.output"
    actual = commit.document.render().decode("utf-8")
    if actual != expected_output:
        return f"output mismatch: got {actual!r}"
    return None


# ---------------------------------------------------------------------------
# registry and limits
# ---------------------------------------------------------------------------


def _registry_v4_case(vector: runner.Case) -> str | None:
    contract_count = compare.integer_field(vector.expected, "contract_count")
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    v3_error_code_count = compare.integer_field(vector.expected, "v3_error_code_count")
    new_code = compare.string_field(vector.expected, "new_code")
    if (
        contract_count is None
        or error_code_count is None
        or v3_error_code_count is None
        or new_code is None
    ):
        return "missing expected registry facts"
    contracts = ContractRegistry(4)
    v4 = ErrorCodeRegistry(4)
    v3 = ErrorCodeRegistry(3)
    if len(contracts.contracts()) != contract_count:
        return f"contract_count: expected {contract_count}, got {len(contracts.contracts())}"
    if len(v4.codes()) != error_code_count:
        return f"error_code_count: expected {error_code_count}, got {len(v4.codes())}"
    if len(v3.codes()) != v3_error_code_count:
        return f"v3_error_code_count: expected {v3_error_code_count}, got {len(v3.codes())}"
    if not v4.contains(new_code) or v3.contains(new_code):
        return "registry facts differ"
    return None


def _parse_limit_case(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    max_depth = compare.integer_field(vector.input, "max_depth")
    if source is None or max_depth is None:
        return "missing input.source/max_depth"
    failed = False
    try:
        json_parser.parse(
            source.encode("utf-8"),
            json_kinds.JsonProfile.JSON5_STANDARD_V1,
            ParseLimits(max_nesting_depth=max_depth),
        )
    except json_errors.JsonFormationFailure:
        failed = True
    fatal = compare.boolean_field(vector.expected, "fatal")
    if fatal is not None and failed != fatal:
        return "fatal verdict differs"
    return None


runner.register_suite("json-family-v2.json", "consema.json-family.conformance@2", "", 33, run)
