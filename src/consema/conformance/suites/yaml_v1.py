"""Suite ``consema.yaml.conformance@1`` (yaml-v1.json, 27 cases): YAML
profile scalar resolution, source encoding, stream facts, lossless syntax,
native mapping facts, formation rejection, graph projection/PGCE, native and
syntax queries, value/graph projection policies, materialization, structural
edits, resource limits, and the plain-property regression. Dispatch is by
case id, mirroring go/conformance/yaml_v1.go.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    FailedMaterializationAttempt,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source import SourceEncodingKind
from consema.document.structural import AssociationPlacement
from consema.graph import encode_pgce
from consema.protocol import query as protocol_query
from consema.protocol.query import QueryFailure
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.yaml import edit as yaml_edit
from consema.yaml import errors as yaml_errors
from consema.yaml import kinds as yaml_kinds
from consema.yaml import materialization as yaml_materialization
from consema.yaml import parser as yaml_parser
from consema.yaml import projection as yaml_projection
from consema.yaml import query as yaml_query


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "profile.yaml12-scalars": _scalar_profile,
        "profile.yaml11-scalars": _scalar_profile,
        "source.utf16le-bom": _source_encoding,
        "stream.empty": _stream_facts,
        "stream.multi-document": _stream_facts,
        "syntax.styles-and-trivia": _syntax_facts,
        "native.arbitrary-duplicate-mapping": _mapping_facts,
        "formation.undefined-alias": _formation_rejection,
        "graph.shared-cycle": _graph_facts,
        "query.mapping-entries": _native_query,
        "query.alias-target": _native_query,
        "query.syntax-comments": _syntax_query,
        "query.resource-limit": _query_limit,
        "projection.sharing-policy": _projection_sharing,
        "projection.cycle": _projection_failure,
        "projection.tag-policy": _projection_tag,
        "projection.mapping-policy": _projection_mapping,
        "projection.graph-provenance": _graph_provenance,
        "materialization.graph-cycle-flow": _graph_materialization,
        "materialization.value-flow": _value_materialization,
        "edit.scalar-atomic": _edit_scalar,
        "edit.anchor-rename": _edit_anchor,
        "edit.structural-insert": _edit_structural,
        "edit.anchor-dependency": _edit_anchor_dependency,
        "resource.parse-source-bytes": _parse_limit,
        "resource.graph-provenance": _graph_provenance_limit,
        "regression.plain-property-characters": _plain_property_regression,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published YAML case")
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


def _yaml_profile(spelling: str):
    profiles = {
        "yaml.1.2-core@1": yaml_kinds.YamlProfile.YAML12_CORE_V1,
        "yaml.1.1-compat@1": yaml_kinds.YamlProfile.YAML11_COMPAT_V1,
    }
    return profiles.get(spelling)


def _parse_yaml_case(vector: runner.Case):
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    profile_spelling = compare.string_field(vector.input, "profile")
    profile = _yaml_profile(profile_spelling or "yaml.1.2-core@1")
    if profile is None:
        return None, f"unknown YAML profile {profile_spelling}"
    try:
        document = yaml_parser.parse(source.encode("utf-8"), profile, ParseLimits())
        return document, None
    except yaml_errors.YamlFormationFailure as error:
        return None, f"YAML formation failed: {error.code}"


def _yaml_document_zero(vector: runner.Case, document):
    yaml_doc = document.document(0)
    if yaml_doc is None:
        return None, "document 0 missing"
    return yaml_doc, None


def _yaml_encoding_name(encoding) -> str:
    """Mirrors the Go yamlEncodingName spellings."""
    kind = encoding.kind
    if kind is SourceEncodingKind.UTF8:
        return "Utf8"
    if kind is SourceEncodingKind.UTF16LE:
        return "Utf16Le"
    if kind is SourceEncodingKind.UTF16BE:
        return "Utf16Be"
    if kind is SourceEncodingKind.LATIN1:
        return "Latin1"
    if kind is SourceEncodingKind.BINARY:
        return "Binary"
    if encoding.code_page is not None:
        return f"WindowsCodePage({encoding.code_page.number})"
    return "Unknown"


_YAML_ROLE_BY_MATCH_KIND = {
    yaml_query.YamlMatchKind.STREAM: "YamlStream",
    yaml_query.YamlMatchKind.DOCUMENT: "YamlDocument",
    yaml_query.YamlMatchKind.NODE: "YamlNode",
    yaml_query.YamlMatchKind.MAPPING_ENTRY: "YamlMappingEntry",
    yaml_query.YamlMatchKind.SEQUENCE_ELEMENT: "YamlSequenceElement",
    yaml_query.YamlMatchKind.ANCHOR_DEFINITION: "YamlAnchorDefinition",
    yaml_query.YamlMatchKind.ALIAS_OCCURRENCE: "YamlAliasOccurrence",
}


def _yaml_match_role(match) -> str:
    return _YAML_ROLE_BY_MATCH_KIND.get(match.kind, "")


def _ordered_results_capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _yaml_executable_from_pipeline(vector: runner.Case):
    pipeline = compare.string_sequence(vector.input, "pipeline")
    if pipeline is None:
        return None, "missing input.pipeline"
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for spelling in pipeline:
        operator_id, _version = spelling.rsplit("@", 1)
        expression = expression.then(protocol_query.OperatorCall(operator_id, 1))
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_yaml_native_v1())
        .with_expression(expression)
    )
    try:
        validated = definition.validate()
    except QueryFailure as failure:
        return None, f"validation: {failure.code}"
    try:
        return validated.bind(_ordered_results_capabilities()), ""
    except QueryFailure as failure:
        return None, f"binding: {failure.code}"


def _materialization_request(style: str) -> MaterializationRequest:
    return (
        MaterializationRequest.new(
            ProfileId.new("yaml.1.2-core", 1), MaterializationStyleId.new(style, 1)
        ).with_newline(NewlinePolicy.LF)
    )


# ---------------------------------------------------------------------------
# profile / source / stream / syntax / native cases
# ---------------------------------------------------------------------------


def _scalar_profile(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    root = yaml_doc.root()
    count = root.sequence_len()
    if count is None:
        return "root must be Sequence"
    kinds: list[str] = []
    canonical: list[str] = []
    for ordinal in range(count):
        item = root.sequence_item(ordinal)
        if item is None:
            return "sequence item missing"
        scalar = item.node().scalar()
        if scalar is None:
            return "sequence item must be Scalar"
        kinds.append(scalar.kind().value)
        canonical.append(scalar.canonical())
    expected_kinds = compare.string_sequence(vector.expected, "kinds")
    expected_canonical = compare.string_sequence(vector.expected, "canonical")
    if expected_kinds is None or expected_canonical is None:
        return "missing expected.kinds/canonical"
    if kinds != expected_kinds:
        return compare.require_ordered(kinds, expected_kinds, "kinds")
    return compare.require_ordered(canonical, expected_canonical, "canonical")


def _source_encoding(vector: runner.Case) -> str | None:
    hex_text = compare.string_field(vector.input, "source_hex")
    if hex_text is None:
        return "missing input.source_hex"
    raw = compare.parse_hex(hex_text)
    profile_spelling = compare.string_field(vector.input, "profile")
    profile = _yaml_profile(profile_spelling or "yaml.1.2-core@1")
    if profile is None:
        return f"unknown YAML profile {profile_spelling}"
    try:
        document = yaml_parser.parse(raw, profile, ParseLimits())
    except yaml_errors.YamlFormationFailure as error:
        return f"YAML formation failed: {error.code}"
    expected_encoding = compare.string_field(vector.expected, "encoding")
    expected_count = compare.integer_field(vector.expected, "document_count")
    if expected_encoding is None or expected_count is None:
        return "missing expected.encoding/document_count"
    if document.render() != raw:
        return "render does not equal the raw source"
    actual_encoding = _yaml_encoding_name(document.source.encoding_facts().selected)
    if actual_encoding != expected_encoding:
        return f"encoding: expected {expected_encoding}, got {actual_encoding}"
    if document.document_count() != expected_count:
        return f"document_count: expected {expected_count}, got {document.document_count()}"
    return None


def _stream_facts(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    expected_count = compare.integer_field(vector.expected, "document_count")
    expected_aliases = compare.integer_field(vector.expected, "alias_count")
    if expected_count is None or expected_aliases is None:
        return "missing expected.document_count/alias_count"
    if document.formation_status().value != "Complete":
        return "formation must be Complete"
    if document.document_count() != expected_count:
        return f"document_count: expected {expected_count}, got {document.document_count()}"
    if document.alias_count() != expected_aliases:
        return f"alias_count: expected {expected_aliases}, got {document.alias_count()}"
    if document.render() != source.encode("utf-8"):
        return "render does not equal source"
    return None


def _syntax_facts(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    kinds = document.lossless_syntax_kinds()
    expected_count = compare.integer_field(vector.expected, "piece_count")
    required = compare.string_sequence(vector.expected, "required_kinds")
    if expected_count is None or required is None:
        return "missing expected.piece_count/required_kinds"
    if len(kinds) != expected_count:
        return f"piece_count: expected {expected_count}, got {len(kinds)}"
    present = {kind.value for kind in kinds}
    for kind_name in required:
        if kind_name not in present:
            return f"missing required syntax kind {kind_name}"
    coverage = 0
    for piece in document.lossless_structural_index().pieces:
        coverage += piece.span.len()
    if coverage != len(document.render()):
        return f"syntax coverage: expected {len(document.render())}, got {coverage}"
    return None


def _mapping_facts(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    root = yaml_doc.root()
    count = root.mapping_len()
    if count is None:
        return "root must be Mapping"
    expected_count = compare.integer_field(vector.expected, "entry_count")
    expected_kinds = compare.string_sequence(vector.expected, "key_kinds")
    expected_values = compare.string_sequence(vector.expected, "values")
    if expected_count is None or expected_kinds is None or expected_values is None:
        return "missing expected.entry_count/key_kinds/values"
    if count != expected_count:
        return f"entry_count: expected {expected_count}, got {count}"
    for ordinal in range(count):
        entry = root.mapping_entry(ordinal)
        if entry is None:
            return "mapping entry missing"
        actual_kind = entry.key().kind().value
        if actual_kind != expected_kinds[ordinal]:
            return f"key kind at {ordinal}: expected {expected_kinds[ordinal]!r}, got {actual_kind!r}"
        scalar = entry.value().scalar()
        if scalar is None:
            return "value must be Scalar"
        if scalar.canonical() != expected_values[ordinal]:
            return f"value canonical at {ordinal}: expected {expected_values[ordinal]!r}, got {scalar.canonical()!r}"
    return None


def _formation_rejection(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    profile_spelling = compare.string_field(vector.input, "profile")
    profile = _yaml_profile(profile_spelling or "yaml.1.2-core@1")
    if profile is None:
        return f"unknown YAML profile {profile_spelling}"
    expected_code = compare.string_field(vector.expected, "code")
    if expected_code is None:
        return "missing expected.code"
    try:
        yaml_parser.parse(source.encode("utf-8"), profile, ParseLimits())
    except yaml_errors.YamlFormationFailure as error:
        if error.code != expected_code:
            return f"formation code: expected {expected_code}, got {error.code}"
        return None
    return "formation unexpectedly succeeded"


# ---------------------------------------------------------------------------
# graph cases
# ---------------------------------------------------------------------------


def _graph_facts(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    graph = yaml_projection.project_graph(document)
    encoded = encode_pgce(graph)
    expected_nodes = compare.integer_field(vector.expected, "node_count")
    expected_roots = compare.integer_field(vector.expected, "root_count")
    expected_hex = compare.string_field(vector.expected, "pgce_hex")
    if expected_nodes is None or expected_roots is None or expected_hex is None:
        return "missing expected.node_count/root_count/pgce_hex"
    if graph.node_count() != expected_nodes:
        return f"node_count: expected {expected_nodes}, got {graph.node_count()}"
    if len(graph.roots()) != expected_roots:
        return f"root_count: expected {expected_roots}, got {len(graph.roots())}"
    return compare.require_bytes_equal(encoded, expected_hex, "pgce_hex")


def _graph_provenance(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    try:
        projected = yaml_projection.project_graph_with_provenance(
            document, yaml_projection.GraphProjectionRequest.best_exact_v1()
        )
    except yaml_errors.YamlGraphProjectionError as error:
        return f"graph provenance failed: {error.code}"
    expected_references = compare.integer_field(vector.expected, "reference_origins")
    expected_associations = compare.integer_field(vector.expected, "association_entries")
    if expected_references is None or expected_associations is None:
        return "missing expected.reference_origins/association_entries"
    if projected.provenance.reference_origin_count() != expected_references:
        return (
            f"reference_origins: expected {expected_references}, "
            f"got {projected.provenance.reference_origin_count()}"
        )
    if projected.provenance.association_entry_count() != expected_associations:
        return (
            f"association_entries: expected {expected_associations}, "
            f"got {projected.provenance.association_entry_count()}"
        )
    return None


def _graph_provenance_limit(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    max_entries = compare.integer_field(vector.input, "max_provenance_entries")
    if max_entries is None:
        return "missing input.max_provenance_entries"
    limits = yaml_projection.GraphProjectionLimits(max_provenance_entries=max_entries)
    request = yaml_projection.GraphProjectionRequest.best_exact_v1().with_limits(limits)
    expected_code = compare.string_field(vector.expected, "code")
    if expected_code is None:
        return "missing expected.code"
    try:
        yaml_projection.project_graph_with_provenance(document, request)
    except yaml_errors.YamlGraphProjectionError as error:
        if error.code != expected_code:
            return f"graph provenance limit code: expected {expected_code}, got {error.code}"
        return None
    return "graph provenance limit unexpectedly succeeded"


# ---------------------------------------------------------------------------
# query cases
# ---------------------------------------------------------------------------


def _native_query(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    executable, message = _yaml_executable_from_pipeline(vector)
    if message:
        return message
    try:
        execution = yaml_query.execute_yaml_query(
            executable, document, yaml_query.YamlQueryLimits(), yaml_query.YamlCancellationToken()
        )
    except QueryFailure as failure:
        return f"query: {failure.code}"
    roles = [_yaml_match_role(match) for match in execution.matches]
    expected = compare.string_sequence(vector.expected, "roles")
    if expected is None:
        return "missing expected.roles"
    return compare.require_ordered(roles, expected, "roles")


def _syntax_query(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    kind = compare.string_field(vector.input, "kind")
    if kind is None:
        return "missing input.kind"
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("yaml.syntax-kind-is", 1).with_argument(
            "kind", PortableValue.string(kind)
        )
    )
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_yaml_lossless_syntax_v1())
        .with_expression(expression)
    )
    try:
        validated = definition.validate()
        executable = validated.bind(_ordered_results_capabilities())
    except QueryFailure as failure:
        return f"validation: {failure.code}"
    try:
        execution = yaml_query.execute_yaml_syntax_query(
            executable, document, yaml_query.YamlQueryLimits(), yaml_query.YamlCancellationToken()
        )
    except QueryFailure as failure:
        return f"query: {failure.code}"
    expected = compare.integer_sequence(vector.expected, "ordinals")
    if expected is None:
        return "missing expected.ordinals"
    ordinals = [match.ordinal for match in execution.matches]
    return compare.require_ordered(ordinals, expected, "ordinals")


def _query_limit(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    executable, message = _yaml_executable_from_pipeline(vector)
    if message:
        return message
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is None:
        return "missing input.max_results"
    try:
        yaml_query.execute_yaml_query(
            executable,
            document,
            yaml_query.YamlQueryLimits(max_results=max_results),
            yaml_query.YamlCancellationToken(),
        )
    except QueryFailure as failure:
        if failure.code != "core.query.resource-limit@1":
            return f"query limit code: expected core.query.resource-limit@1, got {failure.code}"
        return None
    return "query limit did not fail"


# ---------------------------------------------------------------------------
# projection cases
# ---------------------------------------------------------------------------


def _projection_sharing(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    default_result = yaml_projection.project_value(
        document, yaml_projection.ValueProjectionRequest.best_exact_v1()
    )
    if not isinstance(default_result, yaml_projection.FailedValueProjection):
        return "default sharing policy unexpectedly completed"
    expected_code = compare.string_field(vector.expected, "default_code")
    if expected_code is None:
        return "missing expected.default_code"
    if default_result.code != expected_code:
        return f"default sharing code: expected {expected_code}, got {default_result.code}"
    duplicated = yaml_projection.project_value(
        document,
        yaml_projection.ValueProjectionRequest.best_exact_v1().with_sharing(
            yaml_projection.SharingPolicy.DUPLICATE_ACYCLIC
        ),
    )
    if isinstance(duplicated, yaml_projection.FailedValueProjection):
        return f"explicit acyclic duplication failed: {duplicated.code}"
    expected_events = compare.integer_field(vector.expected, "event_count")
    if expected_events is None:
        return "missing expected.event_count"
    events = duplicated.report.events
    if duplicated.fidelity.value != "Transformed":
        return f"fidelity: expected Transformed, got {duplicated.fidelity.value}"
    if len(events) != expected_events:
        return f"event_count: expected {expected_events}, got {len(events)}"
    for event in events:
        if event.kind is not yaml_projection.ProjectionEventKind.SHARING_DUPLICATED:
            return f"unexpected sharing event kind {event.kind.value}"
    return None


def _projection_failure(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    result = yaml_projection.project_value(
        document,
        yaml_projection.ValueProjectionRequest.best_exact_v1().with_sharing(
            yaml_projection.SharingPolicy.DUPLICATE_ACYCLIC
        ),
    )
    if not isinstance(result, yaml_projection.FailedValueProjection):
        return "projection unexpectedly completed"
    expected_code = compare.string_field(vector.expected, "code")
    if expected_code is None:
        return "missing expected.code"
    if result.code != expected_code:
        return f"projection failure code: expected {expected_code}, got {result.code}"
    return None


def _projection_tag(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    default_result = yaml_projection.project_value(
        document, yaml_projection.ValueProjectionRequest.best_exact_v1()
    )
    if not isinstance(default_result, yaml_projection.FailedValueProjection):
        return "unknown tag unexpectedly projected exactly"
    expected_code = compare.string_field(vector.expected, "default_code")
    if expected_code is None:
        return "missing expected.default_code"
    if default_result.code != expected_code:
        return f"tag policy default code: expected {expected_code}, got {default_result.code}"
    stripped = yaml_projection.project_value(
        document,
        yaml_projection.ValueProjectionRequest.best_exact_v1().with_tags(
            yaml_projection.TagPolicy.STRIP_TO_NODE_KIND
        ),
    )
    if isinstance(stripped, yaml_projection.FailedValueProjection):
        return f"explicit tag stripping failed: {stripped.code}"
    expected_value = compare.string_field(vector.expected, "value")
    if expected_value is None:
        return "missing expected.value"
    if stripped.fidelity.value != "Lossy":
        return f"fidelity: expected Lossy, got {stripped.fidelity.value}"
    if stripped.value.kind is not Kind.STRING or stripped.value.as_string() != expected_value:
        return "tag policy value differed"
    if len(stripped.report.events) != 1:
        return f"event count: expected 1, got {len(stripped.report.events)}"
    return None


def _projection_mapping(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    object_result = yaml_projection.project_value(
        document,
        yaml_projection.ValueProjectionRequest.best_exact_v1().with_mapping(
            yaml_projection.MappingPolicy.REQUIRE_OBJECT
        ),
    )
    if not isinstance(object_result, yaml_projection.FailedValueProjection):
        return "duplicate mapping unexpectedly became Object"
    expected_code = compare.string_field(vector.expected, "object_code")
    if expected_code is None:
        return "missing expected.object_code"
    if object_result.code != expected_code:
        return f"object policy code: expected {expected_code}, got {object_result.code}"
    entries_result = yaml_projection.project_value(
        document,
        yaml_projection.ValueProjectionRequest.best_exact_v1().with_mapping(
            yaml_projection.MappingPolicy.REQUIRE_ENTRY_MAPPING
        ),
    )
    if isinstance(entries_result, yaml_projection.FailedValueProjection):
        return f"explicit EntryMapping projection failed: {entries_result.code}"
    expected_count = compare.integer_field(vector.expected, "entry_count")
    if expected_count is None:
        return "missing expected.entry_count"
    if entries_result.value.kind is not Kind.ENTRY_MAPPING:
        return "mapping policy did not produce EntryMapping"
    if len(entries_result.value.as_entry_mapping()) != expected_count:
        return (
            f"entry_count: expected {expected_count}, "
            f"got {len(entries_result.value.as_entry_mapping())}"
        )
    return None


# ---------------------------------------------------------------------------
# materialization cases
# ---------------------------------------------------------------------------


def _graph_materialization(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    projected = yaml_projection.project_graph(document)
    result = yaml_materialization.materialize_graph(
        projected, _materialization_request("yaml.canonical-flow")
    )
    if isinstance(result, yaml_materialization.FailedGraphMaterializationAttempt):
        return f"graph materialization failed: {result.failure.code}"
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    reparsed = yaml_projection.project_graph(result.document)
    if reparsed != projected:
        return "graph materialization did not round-trip"
    if result.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {result.document.render()!r}"
    if result.fidelity.value != "Exact":
        return f"fidelity: expected Exact, got {result.fidelity.value}"
    return None


def _value_materialization(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    projected = yaml_projection.project_value(
        document, yaml_projection.ValueProjectionRequest.best_exact_v1()
    )
    if isinstance(projected, yaml_projection.FailedValueProjection):
        return f"input value projection failed: {projected.code}"
    result = yaml_materialization.materialize_value(
        projected.value, _materialization_request("yaml.canonical-flow")
    )
    if isinstance(result, FailedMaterializationAttempt):
        return f"value materialization failed: {result.failure.code}"
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    reprojected = yaml_projection.project_value(
        result.document, yaml_projection.ValueProjectionRequest.best_exact_v1()
    )
    if isinstance(reprojected, yaml_projection.FailedValueProjection):
        return "materialized value did not reproject"
    if result.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {result.document.render()!r}"
    if not core_equal(reprojected.value, projected.value):
        return "materialized value is not strictly equal to the input projection"
    if result.fidelity.value != "Exact":
        return f"fidelity: expected Exact, got {result.fidelity.value}"
    return None


# ---------------------------------------------------------------------------
# edit cases
# ---------------------------------------------------------------------------


def _edit_scalar(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    entry_ordinal = compare.integer_field(vector.input, "entry")
    integer_text = compare.string_field(vector.input, "integer")
    if entry_ordinal is None or integer_text is None:
        return "missing input.entry/integer"
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    entry = yaml_doc.root().mapping_entry(entry_ordinal)
    if entry is None:
        return "scalar edit target missing"
    builder = yaml_edit.EditTransactionBuilder(document).semantic_scalar(
        entry.value().node_ref(),
        PortableValue.integer(int(integer_text)),
        yaml_edit.RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    try:
        commit = yaml_edit.commit(document, builder.build())
    except yaml_errors.YamlEditFailure as failure:
        return f"edit: {failure.code}"
    expected_source = compare.string_field(vector.expected, "source")
    expected_count = compare.integer_field(vector.expected, "edit_count")
    if expected_source is None or expected_count is None:
        return "missing expected.source/edit_count"
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    if len(commit.change_set.source_edits) != expected_count:
        return f"edit_count: expected {expected_count}, got {len(commit.change_set.source_edits)}"
    return None


def _edit_anchor(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    entry_ordinal = compare.integer_field(vector.input, "entry")
    name = compare.string_field(vector.input, "name")
    if entry_ordinal is None or name is None:
        return "missing input.entry/name"
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    entry = yaml_doc.root().mapping_entry(entry_ordinal)
    if entry is None:
        return "anchor target missing"
    anchor_ref = entry.value().anchor_node_ref()
    if anchor_ref is None:
        return "anchor target missing"
    builder = yaml_edit.EditTransactionBuilder(document).rename_anchor(anchor_ref, name)
    try:
        commit = yaml_edit.commit(document, builder.build())
    except yaml_errors.YamlEditFailure as failure:
        return f"edit: {failure.code}"
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    alias = commit.document.alias(0)
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    if alias is None or alias.name() != name:
        return "anchor rename did not update the alias occurrence"
    return None


def _edit_structural(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    root = yaml_doc.root()
    sequence_entry = root.mapping_entry(0)
    mapping_entry = root.mapping_entry(1)
    if sequence_entry is None or mapping_entry is None:
        return "sequence/mapping entries missing"
    sequence = sequence_entry.value()
    mapping = mapping_entry.value()
    second = sequence.sequence_item(1)
    if second is None:
        return "second sequence item missing"
    builder = yaml_edit.EditTransactionBuilder(document)
    builder.insert_sequence_element(
        sequence.node_ref(),
        PortableValue.boolean(True),
        AssociationPlacement(kind="Before", anchor=second.node_ref()),
    )
    builder.insert_mapping_entry(
        mapping.node_ref(),
        PortableValue.string("b"),
        PortableValue.integer(2),
        AssociationPlacement(kind="End"),
    )
    try:
        commit = yaml_edit.commit(document, builder.build())
    except yaml_errors.YamlEditFailure as failure:
        return f"edit: {failure.code}"
    expected_source = compare.string_field(vector.expected, "source")
    if expected_source is None:
        return "missing expected.source"
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    return None


def _edit_anchor_dependency(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    entry = yaml_doc.root().mapping_entry(0)
    if entry is None:
        return "mapping entry missing"
    target = entry.value().sequence_item(0)
    if target is None:
        return "anchored sequence item missing"
    builder = yaml_edit.EditTransactionBuilder(document).remove_sequence_element(target.node_ref())
    expected_code = compare.string_field(vector.expected, "code")
    if expected_code is None:
        return "missing expected.code"
    try:
        yaml_edit.commit(document, builder.build())
    except yaml_errors.YamlEditFailure as failure:
        if failure.code != expected_code:
            return f"anchor dependency code: expected {expected_code}, got {failure.code}"
        if document.render() != source.encode("utf-8"):
            return "base document must stay unchanged"
        return None
    return "anchor dependency removal unexpectedly succeeded"


# ---------------------------------------------------------------------------
# resource and regression cases
# ---------------------------------------------------------------------------


def _parse_limit(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    max_source_bytes = compare.integer_field(vector.input, "max_source_bytes")
    if source is None or max_source_bytes is None:
        return "missing input.source/max_source_bytes"
    profile_spelling = compare.string_field(vector.input, "profile")
    profile = _yaml_profile(profile_spelling or "yaml.1.2-core@1")
    if profile is None:
        return f"unknown YAML profile {profile_spelling}"
    expected_code = compare.string_field(vector.expected, "code")
    if expected_code is None:
        return "missing expected.code"
    try:
        yaml_parser.parse(source.encode("utf-8"), profile, ParseLimits(max_source_bytes=max_source_bytes))
    except yaml_errors.YamlFormationFailure as error:
        if error.code != expected_code:
            return f"parse limit code: expected {expected_code}, got {error.code}"
        return None
    return "parse limit unexpectedly succeeded"


def _plain_property_regression(vector: runner.Case) -> str | None:
    document, message = _parse_yaml_case(vector)
    if message:
        return message
    yaml_doc, message = _yaml_document_zero(vector, document)
    if message:
        return message
    scalar = yaml_doc.root().scalar()
    if scalar is None:
        return "root must be Scalar"
    expected_canonical = compare.string_field(vector.expected, "canonical")
    if expected_canonical is None:
        return "missing expected.canonical"
    if scalar.canonical() != expected_canonical:
        return f"canonical: expected {expected_canonical!r}, got {scalar.canonical()!r}"
    if document.alias_count() != 0:
        return "alias_count must be 0"
    for kind in document.lossless_syntax_kinds():
        if kind is yaml_kinds.YamlSyntaxKind.ANCHOR or kind is yaml_kinds.YamlSyntaxKind.TAG:
            return "plain scalar fabricated YAML node properties"
    return None


runner.register_suite("yaml-v1.json", "consema.yaml.conformance@1", "", 27, run)
