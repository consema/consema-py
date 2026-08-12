"""Suite ``consema.toml.conformance@1`` (toml-v1.json, 18 cases): TOML 1.0
document formation, native items, query, projection, edit, resource limits,
and real-world corpora. Dispatch is by case id, mirroring
go/conformance/toml_v1.go.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.document.limits import ParseLimits
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.toml import document as toml_document
from consema.toml import edits as toml_edits
from consema.toml import errors as toml_errors
from consema.toml import materialization as toml_materialization
from consema.toml import parser as toml_parser
from consema.toml import projection as toml_projection
from consema.toml import query as toml_query

PROFILE = toml_document.TomlProfile.TOML10_V1


def _parse_toml(source: str) -> toml_document.Document:
    return toml_parser.parse(source.encode("utf-8"), PROFILE, ParseLimits())


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "toml.parse.exact-roundtrip": _parse_exact_roundtrip,
        "toml.parse.lossless-byte-coverage": _lossless_coverage,
        "toml.native.dotted-segments": _dotted_segments,
        "toml.native.table-flavors": _table_flavors,
        "toml.native.array-aot-distinct": _array_aot_distinct,
        "toml.native.float-signed-zero": _float_signed_zero,
        "toml.query.nested-entry-order": _nested_entry_order,
        "toml.query.aot-element-order": _aot_element_order,
        "toml.projection.all-core-kinds": _projection_all_core_kinds,
        "toml.projection.provenance": _projection_provenance,
        "toml.projection.reject-leap-second": _projection_reject_leap_second,
        "toml.edit.literal-minimal": _edit_literal_minimal,
        "toml.edit.reject-unrepresentable": _edit_reject_unrepresentable,
        "toml.parse.reject-invalid": _parse_reject_invalid,
        "toml.resource.token-limit": _resource_token_limit,
        "toml.resource.node-depth-limits": _resource_node_depth,
        "toml.corpus.cargo-manifest": _corpus_cargo,
        "toml.corpus.pyproject": _corpus_pyproject,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published TOML case")
            )
            continue
        message = handler(conformance_runner, vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


def _toml_fixture(conformance_runner: runner.Runner, name: str) -> str:
    return conformance_runner.fixture_bytes("toml", name).decode("utf-8")


def _cargo_manifest(conformance_runner: runner.Runner) -> str:
    return conformance_runner.repo_root_bytes("Cargo.toml").decode("utf-8")


def _parse_exact_roundtrip(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "all-values.toml")
    document = _parse_toml(source)
    if document.formation_status().value != "Complete":
        return "formation must be Complete"
    if document.format_family().id != "toml":
        return "format family must be toml"
    if document.profile().id != "toml.1.0":
        return "profile must be toml.1.0"
    if document.render() != source.encode("utf-8"):
        return "render must equal source"
    if document.diagnostics():
        return "diagnostics must be empty"
    return None


def _lossless_coverage(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "trivia-and-strings.toml")
    document = _parse_toml(source)
    index = document.lossless_structural_index()
    pieces = index.pieces
    if not pieces:
        return "no structural pieces"
    if pieces[0].span.start_byte != 0:
        return "first piece must start at 0"
    if pieces[-1].span.end_byte != len(source.encode("utf-8")):
        return "last piece must end at the source length"
    previous_end = 0
    for piece in pieces:
        if piece.span.start_byte != previous_end:
            return "pieces must be contiguous without gaps or overlaps"
        previous_end = piece.span.end_byte
    return None


def _direct_item(document: toml_document.Document, name: str):
    root = document.root()
    entries = root.table_entries() if root.table_entries() is not None else []
    for entry in entries:
        if entry.name() == name:
            return entry.item()
    return None


def _dotted_segments(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    alpha = _direct_item(document, "alpha")
    beta = None
    for entry in alpha.table_entries() or []:
        if entry.name() == "beta":
            beta = entry.item()
    if alpha is None or beta is None:
        return "alpha/beta items missing"
    if alpha.kind() is not toml_document.TomlItemKind.DOTTED_TABLE:
        return f"alpha kind: expected DottedTable, got {alpha.kind().value}"
    if beta.kind() is not toml_document.TomlItemKind.DOTTED_TABLE:
        return f"beta kind: expected DottedTable, got {beta.kind().value}"
    gamma = None
    for entry in beta.table_entries() or []:
        if entry.name() == "gamma":
            gamma = entry.item()
    if gamma is None or gamma.as_integer() != 1:
        return "gamma must be an integer 1"
    return None


def _table_flavors(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "application.toml")
    document = _parse_toml(source)
    service = _direct_item(document, "service")
    database = _direct_item(document, "database")
    observability = _direct_item(document, "observability")
    if service is None or database is None or observability is None:
        return "service/database/observability items missing"
    if service.kind() is not toml_document.TomlItemKind.DOTTED_TABLE:
        return f"service kind: expected DottedTable, got {service.kind().value}"
    if database.kind() is not toml_document.TomlItemKind.STANDARD_TABLE:
        return f"database kind: expected StandardTable, got {database.kind().value}"
    if observability.kind() is not toml_document.TomlItemKind.IMPLICIT_TABLE:
        return f"observability kind: expected ImplicitTable, got {observability.kind().value}"
    return None


def _array_aot_distinct(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "application.toml")
    document = _parse_toml(source)
    database = _direct_item(document, "database")
    timeouts = None
    for entry in database.table_entries() or []:
        if entry.name() == "timeouts":
            timeouts = entry.item()
    upstreams = _direct_item(document, "upstreams")
    if timeouts is None or upstreams is None:
        return "timeouts/upstreams items missing"
    if timeouts.kind() is not toml_document.TomlItemKind.ARRAY:
        return f"timeouts kind: expected Array, got {timeouts.kind().value}"
    if upstreams.kind() is not toml_document.TomlItemKind.ARRAY_OF_TABLES:
        return f"upstreams kind: expected ArrayOfTables, got {upstreams.kind().value}"
    elements = upstreams.array_elements()
    if len(elements) != 2:
        return f"upstreams element count: expected 2, got {len(elements)}"
    return None


def _float_signed_zero(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    positive = _direct_item(document, "positive")
    negative = _direct_item(document, "negative")
    if positive is None or negative is None:
        return "positive/negative items missing"
    if positive.as_float_bits() != 0:
        return "positive bits must be 0"
    if negative.as_float_bits() != 1 << 63:
        return "negative bits must be 1<<63"
    return None


def _ordered_results() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _toml_named_root(name: str):
    return (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("toml.try-table-entries", 1))
        .then(
            protocol_query.OperatorCall("toml.entry-name-equals", 1).with_argument(
                "name", _string_value(name)
            )
        )
        .then(protocol_query.OperatorCall("toml.entry-item", 1))
    )


def _string_value(text: str):
    from consema.core.value import PortableValue

    return PortableValue.string(text)


def _nested_entry_order(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "application.toml")
    document = _parse_toml(source)
    path = compare.sequence_field(vector.input, "path")
    if path is None or not path:
        return "missing input.path"
    expression = _toml_named_root(path[0].as_string()).then(
        protocol_query.OperatorCall("toml.try-table-entries", 1)
    )
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_toml_native_v1())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results())
    )
    matches = toml_query.execute_toml_query(definition, document)
    expected_names = compare.string_sequence(vector.expected, "names")
    if expected_names is None:
        return "missing expected.names"
    names = [match.name for match in matches if hasattr(match, "name")]
    if names != expected_names:
        return f"names: expected {expected_names!r}, got {names!r}"
    return None


def _aot_element_order(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "application.toml")
    document = _parse_toml(source)
    path = compare.sequence_field(vector.input, "path")
    if path is None or not path:
        return "missing input.path"
    expression = _toml_named_root(path[0].as_string()).then(
        protocol_query.OperatorCall("toml.try-array-elements", 1)
    )
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_toml_native_v1())
        .with_expression(expression)
        .validate()
        .bind(_ordered_results())
    )
    matches = toml_query.execute_toml_query(definition, document)
    expected_ordinals = compare.integer_sequence(vector.expected, "ordinals")
    if expected_ordinals is None:
        return "missing expected.ordinals"
    ordinals = []
    for match in matches:
        if match.kind.value != "ArrayElement":
            return f"match kind: expected ArrayElement, got {match.kind.value}"
        ordinals.append(match.ordinal)
    return compare.require_ordered(ordinals, expected_ordinals, "ordinals")


def _projection_all_core_kinds(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "all-values.toml")
    document = _parse_toml(source)
    request = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = toml_projection.project_document(document, request)
    if isinstance(result, toml_projection.FailedProjectionAttempt):
        return "projection must complete"
    projection = result
    if projection.fidelity.value != "Exact":
        return f"fidelity: expected Exact, got {projection.fidelity.value}"
    if projection.value.kind.value != "Object":
        return f"root kind: expected Object, got {projection.value.kind.value}"
    member_kinds = {compare.kind_spelling(member[1].kind) for member in projection.value.as_object()}
    required = {
        "String", "Boolean", "Integer", "BinaryFloat64", "Date", "Time",
        "LocalDateTime", "OffsetDateTime", "Array", "Object",
    }
    missing = required - member_kinds
    if missing:
        return f"missing core kinds: {sorted(missing)!r}"
    return None


def _projection_provenance(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    request = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = toml_projection.project_document(document, request)
    if isinstance(result, toml_projection.FailedProjectionAttempt):
        return "projection must complete"
    projection = result
    snapshot = document.snapshot_identity()
    for entry in projection.provenance.entries:
        for origin in entry.origins:
            if origin.snapshot != snapshot:
                return "provenance origin must be snapshot-bound"
    object_entry_associations = 0
    for entry in projection.provenance.entries:
        if entry.projected.kind.value == "Association":
            object_entry_associations += 1
    if object_entry_associations < 1:
        return "expected at least one ObjectEntry association"
    return None


def _projection_reject_leap_second(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document = _parse_toml(source)
    request = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = toml_projection.project_document(document, request)
    if not isinstance(result, toml_projection.FailedProjectionAttempt):
        return "projection must fail"
    diagnostics = result.diagnostics
    if len(diagnostics) != 1:
        return f"diagnostic count: expected 1, got {len(diagnostics)}"
    if diagnostics[0].code != "toml.projection.unrepresentable-datetime@1":
        return f"code: expected toml.projection.unrepresentable-datetime@1, got {diagnostics[0].code}"
    return None


def _edit_literal_minimal(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    literal = compare.string_field(vector.input, "literal")
    if source is None or literal is None:
        return "missing input.source/literal"
    document = _parse_toml(source)
    hex_item = _direct_item(document, "hex")
    if hex_item is None:
        return "hex item missing"
    builder = toml_edits.EditTransactionBuilder(document).literal_scalar(
        hex_item.node_ref(), literal.encode("utf-8")
    )
    commit = toml_edits.commit_document(document, builder.build())
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source mismatch: expected {expected_source!r}, got {commit.document.render()!r}"
    expected_edits = compare.integer_field(vector.expected, "source_edit_count")
    if expected_edits is not None and len(commit.change_set.source_edits) != expected_edits:
        return f"source_edit_count: expected {expected_edits}, got {len(commit.change_set.source_edits)}"
    return None


def _edit_reject_unrepresentable(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    bits = compare.string_field(vector.input, "binary64_bits")
    if source is None or bits is None:
        return "missing input.source/binary64_bits"
    document = _parse_toml(source)
    target = _direct_item(document, "float")
    if target is None:
        return "float item missing"
    from consema.core.value import PortableValue

    builder = toml_edits.EditTransactionBuilder(document).semantic_scalar(
        target.node_ref(),
        PortableValue.binary_float64(int(bits, 16)),
        toml_edits.RepresentationPolicy.CANONICAL_FOR_PROFILE,
    )
    try:
        toml_edits.commit_document(document, builder.build())
    except toml_errors.TomlEditFailure as failure:
        if failure.kind.value != "UnsupportedSemanticValue":
            return f"failure: expected UnsupportedSemanticValue, got {failure.kind.value}"
    else:
        return "commit must fail"
    if document.render() != source.encode("utf-8"):
        return "base source must stay unchanged"
    return None


def _parse_reject_invalid(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "invalid-duplicate.toml")
    try:
        toml_parser.parse(source.encode("utf-8"), PROFILE, ParseLimits())
    except toml_errors.TomlFormationFailure as failure:
        if len(failure.diagnostics) != 1:
            return f"diagnostic count: expected 1, got {len(failure.diagnostics)}"
        if failure.diagnostics[0].code != "toml.parse.syntax@1":
            return f"code: expected toml.parse.syntax@1, got {failure.diagnostics[0].code}"
        return None
    return "parse must fail"


def _resource_token_limit(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    max_tokens = compare.integer_field(vector.input, "max_token_count")
    if source is None or max_tokens is None:
        return "missing input.source/max_token_count"
    try:
        toml_parser.parse(source.encode("utf-8"), PROFILE, ParseLimits(max_token_count=max_tokens))
    except toml_errors.TomlFormationFailure:
        return None
    return "parse must fail"


def _resource_node_depth(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    max_nodes = compare.integer_field(vector.input, "max_node_count")
    max_depth = compare.integer_field(vector.input, "max_nesting_depth")
    if max_nodes is None or max_depth is None:
        return "missing input.max_node_count/max_nesting_depth"
    for limits in (
        ParseLimits(max_node_count=max_nodes),
        ParseLimits(max_nesting_depth=max_depth),
    ):
        try:
            toml_parser.parse(source.encode("utf-8"), PROFILE, limits)
        except toml_errors.TomlFormationFailure as failure:
            if failure.diagnostics and failure.diagnostics[0].code != "core.parse.resource-limit@1":
                return f"code: expected core.parse.resource-limit@1, got {failure.diagnostics[0].code}"
        else:
            return "parse must fail under the limit"
    return None


def _corpus_cargo(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _cargo_manifest(conformance_runner)
    return _corpus_roundtrip(source)


def _corpus_pyproject(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    source = _toml_fixture(conformance_runner, "pyproject.toml")
    return _corpus_roundtrip(source)


def _corpus_roundtrip(source: str) -> str | None:
    document = _parse_toml(source)
    if document.formation_status().value != "Complete":
        return "formation must be Complete"
    if document.render() != source.encode("utf-8"):
        return "render must equal source"
    request = toml_projection.ProjectionRequest.new(
        toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
    )
    result = toml_projection.project_document(document, request)
    if isinstance(result, toml_projection.FailedProjectionAttempt):
        return "projection must complete"
    return None


runner.register_suite("toml-v1.json", "consema.toml.conformance@1", "", 18, run)
