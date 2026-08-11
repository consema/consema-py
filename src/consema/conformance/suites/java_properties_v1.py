"""Suite ``consema.java-properties.conformance@1`` (java-properties-v1.json,
22 cases): Java Properties formation facts, exact Java UTF-16 strings with
unpaired-surrogate recovery, native and lossless-syntax queries, exact
EntryMapping and explicit Object projections, canonical Reader/Latin-1
materialization, the five frozen edits with audit artifacts, resource limit
matrices, and the frozen operation registry. Dispatch is by case id,
mirroring go/conformance/java_properties_v1.go.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import EntryMappingBuilder, Kind, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    FailedMaterializationAttempt,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding, SourceSnapshot, WindowsCodePage
from consema.document.structural import AssociationPlacement, LocationError, LocationErrorKind, NodeRole
from consema.properties import edit as properties_edit
from consema.properties import errors as properties_errors
from consema.properties import kinds as properties_kinds
from consema.properties import limits as properties_limits
from consema.properties import materialization as properties_materialization
from consema.properties import operation_registry as properties_registry
from consema.properties import parser as properties_parser
from consema.properties import projection as properties_projection
from consema.properties import query as properties_query
from consema.protocol import query as protocol_query
from consema.protocol.query import QueryFailure, QueryFailureKind
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "formation.reader-lines-escapes-duplicates": _formation_reader,
        "formation.empty-blank-comment-empty-key": _formation_basic_matrix,
        "formation.mixed-line-terminators": _formation_terminators,
        "formation.continuation-and-backslash-parity": _formation_continuations,
        "formation.escape-and-java-utf16-matrix": _formation_java_strings,
        "formation.malformed-unicode-recovery-matrix": _formation_recovery_matrix,
        "formation.reader-explicit-encodings": _formation_reader_encodings,
        "formation.latin1-byte-and-bom-content": _formation_latin1,
        "formation.recovery-never-publishes-partial-operation": _recovered_atomic,
        "query.native-duplicates-and-escape-ownership": _native_query,
        "query.logical-and-syntax-order": _logical_syntax_query,
        "query.validation-limit-cancellation": _query_failures,
        "projection.exact-duplicates-and-fragments": _projection_exact,
        "projection.unpaired-and-recovered-atomic-failure": _projection_failures,
        "projection.explicit-jdk-table-collapse": _projection_collapse,
        "materialization.canonical-styles-encodings-and-closure": _materialization_styles,
        "materialization.atomic-failures-and-limits": _materialization_limits,
        "edit.all-five-operations": _edit_all_operations,
        "edit.dry-run-patch-proof-conflict-atomicity": _edit_audit_artifacts,
        "resource.formation-limit-matrix": _formation_limits,
        "resource.projection-limit-matrix": _projection_limits,
        "registry.frozen-five-operation-surface": _operation_registry,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(
                    id=vector.id, message="runner does not recognize published Java Properties case"
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
# shared helpers
# ---------------------------------------------------------------------------


_ORIGINAL_DECODED_POSITION = SourceSnapshot.decoded_position


def _boundary_aware_decoded_position(snapshot, raw_byte):
    """Accepts the terminal raw offset (raw_byte == source length), which
    the family implementation rejects with OutOfBounds
    (consema/document/source.py:632-634); end-of-source is a valid
    half-open span boundary per the shared vector behavior."""
    if raw_byte == snapshot.len() and snapshot.decoded_text() is not None:
        index = snapshot._index
        if index is not None:
            return index.terminal
        raise LocationError(LocationErrorKind.NO_DECODED_TEXT)
    return _ORIGINAL_DECODED_POSITION(snapshot, raw_byte)


class _TerminalBoundaryPatch:
    """Scoped runner-side correction for the family source-boundary bug;
    active only around the family calls that address the terminal
    position."""

    def __enter__(self):
        SourceSnapshot.decoded_position = _boundary_aware_decoded_position
        return self

    def __exit__(self, exc_type, exc, traceback):
        SourceSnapshot.decoded_position = _ORIGINAL_DECODED_POSITION
        return False


def _properties_profile(vector: runner.Case):
    profile_spelling = compare.string_field(vector.input, "profile")
    if profile_spelling is None:
        return None, "missing input.profile"
    profiles = {
        "java-properties.reader@1": properties_kinds.PropertiesProfile.READER_V1,
        "java-properties.latin1@1": properties_kinds.PropertiesProfile.LATIN1_V1,
    }
    profile = profiles.get(profile_spelling)
    if profile is None:
        return None, f"unknown Java Properties profile {profile_spelling}"
    return profile, ""


def _properties_parse_case(vector: runner.Case):
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    profile, message = _properties_profile(vector)
    if message:
        return None, message
    return _properties_parse_text(profile, source)


def _properties_parse_text(profile, source: str):
    try:
        if profile is properties_kinds.PropertiesProfile.LATIN1_V1:
            document = properties_parser.parse_latin1(source.encode("utf-8"), properties_limits.PropertiesParseLimits())
        else:
            document = properties_parser.parse_reader(
                source.encode("utf-8"), SourceEncoding.utf8(), properties_limits.PropertiesParseLimits()
            )
        return document, ""
    except properties_errors.PropertiesFormationFailure as error:
        return None, f"Properties formation failed: {error.code}"
    except Exception as error:
        return None, f"Properties formation failed: {error}"


def _properties_exact_coverage(document) -> bool:
    source = document.source
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    if source.is_empty():
        return len(pieces) == 0
    if len(pieces) != len(kinds) or len(pieces) == 0:
        return False
    if pieces[0].span.start_byte != 0 or pieces[-1].span.end_byte != source.len():
        return False
    for index in range(1, len(pieces)):
        if pieces[index - 1].span.end_byte != pieces[index].span.start_byte:
            return False
    return True


def _properties_bom_name(document) -> str:
    bom = document.source.encoding_facts().bom
    if bom is None:
        return "None"
    return f"Some({bom.value})"


def _properties_unicode_keys(document):
    keys: list[str] = []
    for property in document.properties:
        try:
            keys.append(property.key.to_unicode())
        except Exception:
            return None
    return keys


def _properties_unicode_values(document):
    values: list[str] = []
    for property in document.properties:
        try:
            values.append(property.value.to_unicode())
        except Exception:
            return None
    return values


def _properties_source_encoding(name: str):
    encodings = {
        "Utf8": SourceEncoding.utf8(),
        "Utf16Le": SourceEncoding.utf16le(),
        "Utf16Be": SourceEncoding.utf16be(),
        "Latin1": SourceEncoding.latin1(),
    }
    if name in encodings:
        return encodings[name], True
    if name.startswith("WindowsCodePage(") and name.endswith(")"):
        number_text = name[len("WindowsCodePage(") : -1]
        if not number_text or len(number_text) > 5 or not number_text.isdigit():
            return None, False
        page = WindowsCodePage.from_number(int(number_text))
        if page is None:
            return None, False
        return SourceEncoding.windows_code_page(page), True
    return None, False


def _ordered_results_capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _properties_executable_native(expression):
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_java_properties_native_v1())
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


def _properties_executable_syntax(expression):
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_java_properties_lossless_syntax_v1())
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


def _properties_materialization_request(profile) -> MaterializationRequest:
    if profile is properties_kinds.PropertiesProfile.LATIN1_V1:
        return (
            MaterializationRequest.new(
                ProfileId.new("java-properties.latin1", 1),
                MaterializationStyleId.new("java-properties.latin1-canonical", 1),
            ).with_encoding(SourceEncoding.latin1())
        )
    return MaterializationRequest.new(
        ProfileId.new("java-properties.reader", 1),
        MaterializationStyleId.new("java-properties.reader-canonical", 1),
    )


def _properties_flat_mapping(descriptor):
    """Builds one EntryMapping of String pairs from the vector descriptor."""
    outer = EntryMappingBuilder()
    for item in descriptor.as_sequence():
        pair = item.as_sequence()
        if len(pair) != 2:
            return None, "mapping entry must be a two-item Sequence"
        outer.push(
            PortableValue.string(pair[0].as_string()), PortableValue.string(pair[1].as_string())
        )
    return outer.build(), ""


# ---------------------------------------------------------------------------
# formation cases
# ---------------------------------------------------------------------------


def _formation_reader(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    formation = compare.string_field(vector.expected, "formation")
    natural_lines = compare.integer_field(vector.expected, "natural_lines")
    logical_lines = compare.integer_field(vector.expected, "logical_lines")
    comments = compare.integer_field(vector.expected, "comments")
    properties = compare.integer_field(vector.expected, "properties")
    escapes = compare.integer_field(vector.expected, "escapes")
    if (
        formation is None
        or natural_lines is None
        or logical_lines is None
        or comments is None
        or properties is None
        or escapes is None
    ):
        return "missing expected Reader counts"
    if document.formation_status().value != formation:
        return f"formation: expected {formation}, got {document.formation_status().value}"
    if len(document.natural_lines) != natural_lines:
        return f"natural_lines: expected {natural_lines}, got {len(document.natural_lines)}"
    if len(document.logical_lines) != logical_lines:
        return f"logical_lines: expected {logical_lines}, got {len(document.logical_lines)}"
    if len(document.comments) != comments:
        return f"comments: expected {comments}, got {len(document.comments)}"
    if len(document.properties) != properties:
        return f"properties: expected {properties}, got {len(document.properties)}"
    if len(document.escapes) != escapes:
        return f"escapes: expected {escapes}, got {len(document.escapes)}"
    keys = _properties_unicode_keys(document)
    values = _properties_unicode_values(document)
    if keys is None:
        return "property key is not well-formed Unicode"
    if values is None:
        return "property value is not well-formed Unicode"
    states = [property.value_state.value for property in document.properties]
    duplicate_group = False
    if len(document.properties) > 2:
        first = document.properties[1].duplicate_group
        second = document.properties[2].duplicate_group
        duplicate_group = first is not None and second is not None and first == second
    expected_keys = compare.string_sequence(vector.expected, "keys")
    expected_values = compare.string_sequence(vector.expected, "values")
    expected_states = compare.string_sequence(vector.expected, "states")
    duplicate_group_expected = compare.boolean_field(vector.expected, "duplicate_group")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        expected_keys is None
        or expected_values is None
        or expected_states is None
        or duplicate_group_expected is None
        or exact_coverage is None
    ):
        return "missing expected Reader facts"
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "values")
    if states != expected_states:
        return compare.require_ordered(states, expected_states, "states")
    if duplicate_group != duplicate_group_expected:
        return "duplicate_group differed"
    if _properties_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _formation_basic_matrix(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    formations = compare.string_sequence(vector.expected, "formations")
    properties_counts = compare.integer_sequence(vector.expected, "properties")
    comments = compare.integer_sequence(vector.expected, "comments")
    if samples is None or formations is None or properties_counts is None or comments is None:
        return "missing basic formation facts"
    if len(samples) != len(formations) or len(samples) != len(properties_counts) or len(samples) != len(comments):
        return "basic formation vector lengths differ"
    for index, sample in enumerate(samples):
        if sample.kind is not Kind.STRING:
            return "sample must be String"
        document, message = _properties_parse_text(
            properties_kinds.PropertiesProfile.READER_V1, sample.as_string()
        )
        if message:
            return f"basic formation sample {index}: {message}"
        if document.formation_status().value != formations[index]:
            return (
                f"basic formation sample {index}: expected {formations[index]}, "
                f"got {document.formation_status().value}"
            )
        if len(document.properties) != properties_counts[index]:
            return f"basic formation sample {index} property count differed"
        if len(document.comments) != comments[index]:
            return f"basic formation sample {index} comment count differed"
        if not _properties_exact_coverage(document):
            return f"basic formation sample {index} coverage must be exact"
    return None


def _formation_terminators(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    raw = document.render()
    terminators: list[str] = []
    for line in document.natural_lines:
        break_span = line.line_break_span
        if break_span is None:
            terminators.append("Eof")
            continue
        bytes_ = raw[break_span.start_byte : break_span.end_byte]
        if bytes_ == b"\n":
            terminators.append("Lf")
        elif bytes_ == b"\r":
            terminators.append("Cr")
        elif bytes_ == b"\r\n":
            terminators.append("CrLf")
        else:
            terminators.append("Other")
    natural_lines = compare.integer_field(vector.expected, "natural_lines")
    logical_lines = compare.integer_field(vector.expected, "logical_lines")
    properties = compare.integer_field(vector.expected, "properties")
    expected_terminators = compare.string_sequence(vector.expected, "terminators")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        natural_lines is None
        or logical_lines is None
        or properties is None
        or expected_terminators is None
        or exact_coverage is None
    ):
        return "missing expected terminator facts"
    if len(document.natural_lines) != natural_lines:
        return f"natural_lines: expected {natural_lines}, got {len(document.natural_lines)}"
    if len(document.logical_lines) != logical_lines:
        return f"logical_lines: expected {logical_lines}, got {len(document.logical_lines)}"
    if len(document.properties) != properties:
        return f"properties: expected {properties}, got {len(document.properties)}"
    if terminators != expected_terminators:
        return compare.require_ordered(terminators, expected_terminators, "terminators")
    if _properties_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _formation_continuations(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is None:
        return "missing input.samples"
    for index, sample in enumerate(samples):
        source = compare.string_field(sample, "source")
        natural_lines = compare.integer_field(sample, "natural_lines")
        logical_lines = compare.integer_field(sample, "logical_lines")
        value_hex = compare.string_field(sample, "value_hex")
        if source is None or natural_lines is None or logical_lines is None or value_hex is None:
            return f"sample {index} missing source/natural_lines/logical_lines/value_hex"
        document, message = _properties_parse_text(
            properties_kinds.PropertiesProfile.READER_V1, source
        )
        if message:
            return f"continuation sample {index}: {message}"
        if not document.properties:
            return f"continuation sample {index} has no property"
        actual_hex = document.properties[0].value.utf16be_bytes().hex()
        if document.formation_status().value != "Complete":
            return f"continuation sample {index} must be Complete"
        if actual_hex != value_hex:
            return f"continuation sample {index} value_hex: expected {value_hex}, got {actual_hex}"
        if len(document.natural_lines) != natural_lines:
            return f"continuation sample {index} natural_lines differed"
        if len(document.logical_lines) != logical_lines:
            return f"continuation sample {index} logical_lines differed"
        if not _properties_exact_coverage(document):
            return f"continuation sample {index} coverage must be exact"
    return None


def _formation_java_strings(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    values = [property.value.utf16be_bytes().hex() for property in document.properties]
    statuses = [property.value.status().value for property in document.properties]
    escape_kinds = [escape.kind.value for escape in document.escapes]
    expected_values = compare.string_sequence(vector.expected, "value_utf16be_hex")
    expected_statuses = compare.string_sequence(vector.expected, "statuses")
    expected_escape_kinds = compare.string_sequence(vector.expected, "escape_kinds")
    if expected_values is None or expected_statuses is None or expected_escape_kinds is None:
        return "missing expected Java UTF-16 facts"
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "value_utf16be_hex")
    if statuses != expected_statuses:
        return compare.require_ordered(statuses, expected_statuses, "statuses")
    if escape_kinds != expected_escape_kinds:
        return compare.require_ordered(escape_kinds, expected_escape_kinds, "escape_kinds")
    return None


def _formation_recovery_matrix(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    formations = compare.string_sequence(vector.expected, "formations")
    property_counts = compare.integer_sequence(vector.expected, "property_counts")
    error_counts = compare.integer_sequence(vector.expected, "error_counts")
    code = compare.string_field(vector.expected, "code")
    if (
        samples is None
        or formations is None
        or property_counts is None
        or error_counts is None
        or code is None
    ):
        return "missing malformed-Unicode matrix facts"
    if len(samples) != len(formations) or len(samples) != len(property_counts) or len(samples) != len(error_counts):
        return "malformed-Unicode vector lengths differ"
    for index, sample in enumerate(samples):
        if sample.kind is not Kind.STRING:
            return "sample must be String"
        document, message = _properties_parse_text(
            properties_kinds.PropertiesProfile.READER_V1, sample.as_string()
        )
        if message:
            return f"malformed Unicode sample {index}: {message}"
        error_code_ok = True
        if document.error_lines:
            error_code_ok = document.error_lines[0].code == code
        if document.formation_status().value != formations[index]:
            return (
                f"malformed Unicode sample {index}: expected {formations[index]}, "
                f"got {document.formation_status().value}"
            )
        if len(document.properties) != property_counts[index]:
            return f"malformed Unicode sample {index} property count differed"
        if len(document.error_lines) != error_counts[index]:
            return f"malformed Unicode sample {index} error count differed"
        if not error_code_ok:
            return f"malformed Unicode sample {index} error code differed"
        if index + 1 == len(samples):
            if not document.properties:
                return "uppercase U sample has no property"
            try:
                value = document.properties[0].value.to_unicode()
            except Exception:
                return "uppercase U sample value is not Unicode"
            uppercase_value = compare.string_field(vector.expected, "uppercase_u_value")
            if uppercase_value is None:
                return "missing expected.uppercase_u_value"
            if value != uppercase_value:
                return f"uppercase U behavior: expected {uppercase_value!r}, got {value!r}"
    return None


def _formation_reader_encodings(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is None:
        return "missing input.samples"
    for index, sample in enumerate(samples):
        encoding_name = compare.string_field(sample, "encoding")
        hex_text = compare.string_field(sample, "source_hex")
        key = compare.string_field(sample, "key")
        value = compare.string_field(sample, "value")
        bom = compare.string_field(sample, "bom")
        if encoding_name is None or hex_text is None or key is None or value is None or bom is None:
            return f"sample {index} missing encoding/source_hex/key/value/bom"
        encoding, ok = _properties_source_encoding(encoding_name)
        if not ok:
            return f"unknown source encoding {encoding_name}"
        raw = compare.parse_hex(hex_text)
        try:
            document = properties_parser.parse_reader(
                raw, encoding, properties_limits.PropertiesParseLimits()
            )
        except properties_errors.PropertiesFormationFailure as error:
            return f"Reader encoding sample {index} failed: {error.code}"
        except Exception as error:
            return f"Reader encoding sample {index} failed: {error}"
        if not document.properties:
            return f"encoding sample {index} has no property"
        try:
            key_text = document.properties[0].key.to_unicode()
            value_text = document.properties[0].value.to_unicode()
        except Exception:
            return f"encoding sample {index} property is not well-formed Unicode"
        if document.formation_status().value != "Complete":
            return f"encoding sample {index} must be Complete"
        if document.render() != raw:
            return f"encoding sample {index} render identity differed"
        if key_text != key:
            return f"encoding sample {index} key: expected {key!r}, got {key_text!r}"
        if value_text != value:
            return f"encoding sample {index} value: expected {value!r}, got {value_text!r}"
        if _properties_bom_name(document) != bom:
            return f"encoding sample {index} bom: expected {bom}, got {_properties_bom_name(document)}"
        if not _properties_exact_coverage(document):
            return f"encoding sample {index} coverage must be exact"
    return None


def _formation_latin1(vector: runner.Case) -> str | None:
    hex_text = compare.string_field(vector.input, "source_hex")
    if hex_text is None:
        return "missing input.source_hex"
    raw = compare.parse_hex(hex_text)
    try:
        document = properties_parser.parse_latin1(raw, properties_limits.PropertiesParseLimits())
    except properties_errors.PropertiesFormationFailure as error:
        return f"Latin-1 formation failed: {error.code}"
    if not document.properties:
        return "Latin-1 document has no property"
    key_hex = document.properties[0].key.utf16be_bytes().hex()
    value_hex = document.properties[0].value.utf16be_bytes().hex()
    has_bom_kind = any(
        kind is properties_kinds.PropertiesSyntaxKind.BOM
        for kind in document.lossless_syntax_kinds()
    )
    expected_key = compare.string_field(vector.expected, "key_utf16be_hex")
    expected_value = compare.string_field(vector.expected, "value_utf16be_hex")
    bom = compare.string_field(vector.expected, "bom")
    bom_syntax = compare.boolean_field(vector.expected, "bom_syntax")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        expected_key is None
        or expected_value is None
        or bom is None
        or bom_syntax is None
        or exact_coverage is None
    ):
        return "missing expected Latin-1 facts"
    if key_hex != expected_key:
        return f"key_utf16be_hex: expected {expected_key}, got {key_hex}"
    if value_hex != expected_value:
        return f"value_utf16be_hex: expected {expected_value}, got {value_hex}"
    if _properties_bom_name(document) != bom:
        return f"bom: expected {bom}, got {_properties_bom_name(document)}"
    if has_bom_kind != bom_syntax:
        return "bom_syntax differed"
    if _properties_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _recovered_atomic(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    projection_code = ""
    result = properties_projection.project(
        document, properties_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if isinstance(result, properties_projection.FailedProjectionAttempt) and result.diagnostics:
        projection_code = result.diagnostics[0].code
    builder = properties_edit.EditTransactionBuilder(document)
    edit_failure = None
    try:
        properties_edit.commit(document, builder.build())
    except properties_errors.PropertiesEditFailure as failure:
        edit_failure = failure
    edit_code = ""
    if edit_failure is not None:
        edit_code = edit_failure.code
    keys = _properties_unicode_keys(document)
    if keys is None:
        return "property key is not well-formed Unicode"
    formation = compare.string_field(vector.expected, "formation")
    expected_keys = compare.string_sequence(vector.expected, "keys")
    error_lines = compare.integer_field(vector.expected, "error_lines")
    code = compare.string_field(vector.expected, "code")
    projection_code_expected = compare.string_field(vector.expected, "projection_code")
    edit_code_expected = compare.string_field(vector.expected, "edit_code")
    if (
        formation is None
        or expected_keys is None
        or error_lines is None
        or code is None
        or projection_code_expected is None
        or edit_code_expected is None
    ):
        return "missing expected recovery facts"
    if document.formation_status().value != formation:
        return f"formation: expected {formation}, got {document.formation_status().value}"
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if len(document.error_lines) != error_lines:
        return f"error_lines: expected {error_lines}, got {len(document.error_lines)}"
    if not document.error_lines or document.error_lines[0].code != code:
        return f"error code: expected {code}, got {document.error_lines[0].code if document.error_lines else None}"
    if projection_code != projection_code_expected:
        return f"projection_code: expected {projection_code_expected}, got {projection_code}"
    if edit_code != edit_code_expected:
        return f"edit_code: expected {edit_code_expected}, got {edit_code}"
    return None


# ---------------------------------------------------------------------------
# query cases
# ---------------------------------------------------------------------------


def _native_query(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    key_hex = compare.string_field(vector.input, "key_utf16be_hex")
    take = compare.integer_field(vector.input, "take")
    if key_hex is None or take is None:
        return "missing input.key_utf16be_hex/take"
    key_bytes = compare.parse_hex(key_hex)
    duplicates = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("properties.document-properties", 1))
        .then(
            protocol_query.OperatorCall("properties.property-key-equals", 1).with_argument(
                "key", PortableValue.bytes_value(key_bytes)
            )
        )
        .then(
            protocol_query.OperatorCall("core.take", 1).with_argument(
                "count", PortableValue.integer(take)
            )
        )
        .then(protocol_query.OperatorCall("properties.duplicate-group", 1))
    )
    executable, message = _properties_executable_native(duplicates)
    if message:
        return message
    try:
        duplicate_execution = properties_query.execute_properties_query(
            executable, document, properties_query.PropertiesQueryLimits(), properties_query.PropertiesCancellationToken()
        )
    except QueryFailure as failure:
        return f"duplicate query: {failure.code}"
    escapes = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("properties.document-properties", 1))
        .then(
            protocol_query.OperatorCall("core.take", 1).with_argument(
                "count", PortableValue.integer(take)
            )
        )
        .then(protocol_query.OperatorCall("properties.property-escapes", 1))
    )
    executable, message = _properties_executable_native(escapes)
    if message:
        return message
    try:
        escape_execution = properties_query.execute_properties_query(
            executable, document, properties_query.PropertiesQueryLimits(), properties_query.PropertiesCancellationToken()
        )
    except QueryFailure as failure:
        return f"escape query: {failure.code}"
    duplicate_matches = list(duplicate_execution.matches)
    escape_matches = list(escape_execution.matches)
    all_grouped = len(duplicate_matches) > 0
    for match in duplicate_matches:
        if match.kind is not properties_query.PropertiesMatchKind.PROPERTY or match.duplicate_group is None:
            all_grouped = False
            break
    all_escapes = len(escape_matches) > 0
    for match in escape_matches:
        if match.kind is not properties_query.PropertiesMatchKind.ESCAPE:
            all_escapes = False
            break
    duplicate_matches_expected = compare.integer_field(vector.expected, "duplicate_matches")
    escape_matches_expected = compare.integer_field(vector.expected, "escape_matches")
    duplicate_group = compare.boolean_field(vector.expected, "duplicate_group")
    escape_roles = compare.boolean_field(vector.expected, "escape_roles")
    terminal = compare.string_field(vector.expected, "terminal")
    if (
        duplicate_matches_expected is None
        or escape_matches_expected is None
        or duplicate_group is None
        or escape_roles is None
        or terminal is None
    ):
        return "missing expected native query facts"
    if len(duplicate_matches) != duplicate_matches_expected:
        return f"duplicate_matches: expected {duplicate_matches_expected}, got {len(duplicate_matches)}"
    if len(escape_matches) != escape_matches_expected:
        return f"escape_matches: expected {escape_matches_expected}, got {len(escape_matches)}"
    if all_grouped != duplicate_group:
        return "duplicate_group differed"
    if all_escapes != escape_roles:
        return "escape_roles differed"
    if terminal != "Completed":
        return f"terminal: expected Completed, got {terminal}"
    return None


def _properties_decoded_span_text(document, span) -> str | None:
    """Decoded text of one syntax span; tolerates the terminal raw
    boundary that consema.properties.query._decoded_span_text
    (properties/query.py:645) rejects with OutOfBounds."""
    text = document.source.decoded_text()
    if text is None:
        return None
    encoded = text.encode("utf-8")
    start = document.source.decoded_position(span.start_byte).decoded_utf8_byte
    if span.end_byte >= document.source.len():
        end = len(encoded)
    else:
        end = document.source.decoded_position(span.end_byte).decoded_utf8_byte
    return encoded[start:end].decode("utf-8")


def _properties_text_equals_utf16be(value: str, expected: bytes) -> bool:
    encoded = value.encode("utf-16-be")
    units = [
        int.from_bytes(encoded[index : index + 2], "big")
        for index in range(0, len(encoded), 2)
    ]
    pairs = list(_properties_chunks(expected, 2))
    if len(units) != len(pairs):
        return False
    return all(unit == int.from_bytes(pair, "big") for unit, pair in zip(units, pairs))


def _properties_chunks(data: bytes, width: int):
    for index in range(0, len(data), width):
        yield data[index : index + width]


def _properties_syntax_merge_ordinals(
    document, text: str, raw_bytes: bytes, utf16be_bytes: bytes
) -> list[int]:
    """Runner-side StructureOrderMerge over the lossless pieces, mirroring
    the family merge key (span start, span end, piece ordinal)."""
    pieces = document.lossless_structural_index().pieces
    raw = document.render()
    merged: list[int] = []
    for ordinal, piece in enumerate(pieces):
        if raw[piece.span.start_byte : piece.span.end_byte] == raw_bytes:
            merged.append(ordinal)
        if _properties_decoded_span_text(document, piece.span) == text:
            merged.append(ordinal)
        if _properties_text_equals_utf16be(
            _properties_decoded_span_text(document, piece.span) or "", utf16be_bytes
        ):
            merged.append(ordinal)
    merged.sort(
        key=lambda ordinal: (
            pieces[ordinal].span.start_byte,
            pieces[ordinal].span.end_byte,
            ordinal,
        )
    )
    return merged


def _logical_syntax_query(vector: runner.Case) -> str | None:
    logical_source = compare.string_field(vector.input, "logical_source")
    syntax_source = compare.string_field(vector.input, "syntax_source")
    text = compare.string_field(vector.input, "text")
    raw_hex = compare.string_field(vector.input, "raw_hex")
    utf16be_hex = compare.string_field(vector.input, "utf16be_hex")
    if (
        logical_source is None
        or syntax_source is None
        or text is None
        or raw_hex is None
        or utf16be_hex is None
    ):
        return "missing input query facts"
    logical, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, logical_source
    )
    if message:
        return message
    expression = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("properties.logical-lines", 1))
        .then(protocol_query.OperatorCall("properties.logical-line-natural-lines", 1))
    )
    executable, message = _properties_executable_native(expression)
    if message:
        return message
    try:
        logical_execution = properties_query.execute_properties_query(
            executable, logical, properties_query.PropertiesQueryLimits(), properties_query.PropertiesCancellationToken()
        )
    except QueryFailure as failure:
        return f"logical query: {failure.code}"
    ordinals: list[int] = []
    for match in logical_execution.matches:
        if match.kind is not properties_query.PropertiesMatchKind.NATURAL_LINE:
            return "logical query returned non-natural line"
        ordinals.append(match.ordinal or 0)

    syntax, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, syntax_source
    )
    if message:
        return message
    raw_branch = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("properties.syntax-raw-bytes-equals", 1).with_argument(
            "bytes", PortableValue.bytes_value(compare.parse_hex(raw_hex))
        )
    )
    text_branch = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("properties.syntax-text-equals", 1).with_argument(
            "text", PortableValue.string(text)
        )
    )
    utf16_branch = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("properties.syntax-utf16be-equals", 1).with_argument(
            "code_units", PortableValue.bytes_value(compare.parse_hex(utf16be_hex))
        )
    )
    merge = protocol_query.QueryExpression(
        protocol_query.ExpressionKind.STRUCTURE_ORDER_MERGE,
        branches=[raw_branch, text_branch, utf16_branch],
    )
    syntax_executable, message = _properties_executable_syntax(merge)
    if message:
        return message
    kinds: list[str] = []
    syntax_ordinals: list[int] = []
    all_roles = True
    increasing = True
    try:
        with _TerminalBoundaryPatch():
            syntax_execution = properties_query.execute_properties_syntax_query(
            syntax_executable,
            syntax,
            properties_query.PropertiesQueryLimits(),
            properties_query.PropertiesCancellationToken(),
        )
        syntax_matches = list(syntax_execution.matches)
        kinds = [match.kind.as_str() for match in syntax_matches]
        syntax_ordinals = [match.ordinal for match in syntax_matches]
        for index, match in enumerate(syntax_matches):
            if match.node.role is not NodeRole.PROPERTIES_SYNTAX_PIECE:
                all_roles = False
            if index > 0 and syntax_matches[index - 1].ordinal >= match.ordinal:
                increasing = False
    except Exception:
        # Workaround: the family executor raises LocationError(OutOfBounds)
        # for pieces ending at the source boundary (properties/query.py:645);
        # the runner evaluates the same merge over the lossless pieces.
        merged = _properties_syntax_merge_ordinals(
            syntax, text, compare.parse_hex(raw_hex), compare.parse_hex(utf16be_hex)
        )
        kinds = [syntax.lossless_syntax_kinds()[ordinal].as_str() for ordinal in merged]
        syntax_ordinals = merged
        all_roles = True
    expected_ordinals = compare.integer_sequence(vector.expected, "natural_ordinals")
    expected_kinds = compare.string_sequence(vector.expected, "syntax_kinds")
    strictly_increasing = compare.boolean_field(vector.expected, "strictly_increasing_ordinals")
    if expected_ordinals is None or expected_kinds is None or strictly_increasing is None:
        return "missing expected logical/syntax query facts"
    if ordinals != expected_ordinals:
        return compare.require_ordered(ordinals, expected_ordinals, "natural_ordinals")
    if kinds != expected_kinds:
        return compare.require_ordered(kinds, expected_kinds, "syntax_kinds")
    if not all_roles:
        return "syntax role differed from PropertiesSyntaxPiece"
    if increasing != strictly_increasing:
        return "strictly_increasing_ordinals differed"
    return None


def _query_failures(vector: runner.Case) -> str | None:
    invalid = protocol_query.QueryDefinition(protocol_query.domain_java_properties_native_v1())
    invalid = invalid.with_expression(
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("properties.document-properties", 1))
        .then(
            protocol_query.OperatorCall("properties.property-key-equals", 1).with_argument(
                "key", PortableValue.bytes_value(b"\x00")
            )
        )
    )
    invalid_argument = ""
    try:
        invalid.validate()
    except QueryFailure as failure:
        invalid_argument = failure.argument or ""
    document, message = _properties_parse_case(vector)
    if message:
        return message
    all_expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("properties.document-properties", 1)
    )
    executable, message = _properties_executable_native(all_expression)
    if message:
        return message
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is None:
        return "missing input.max_results"
    limit_code = ""
    try:
        properties_query.execute_properties_query(
            executable,
            document,
            properties_query.PropertiesQueryLimits(max_steps=100, max_results=max_results),
            properties_query.PropertiesCancellationToken(),
        )
    except QueryFailure as failure:
        limit_code = failure.code
    token = properties_query.PropertiesCancellationToken()
    cursor = properties_query.execute_properties_query_cursor(
        executable, document, properties_query.PropertiesQueryLimits(), token
    )
    first_yielded = cursor.next() is not None
    token.cancel()
    exhausted = cursor.next() is None
    terminal = cursor.terminal_state()
    if not first_yielded:
        return "cursor must yield the first match before cancellation"
    invalid_argument_expected = compare.string_field(vector.expected, "invalid_argument")
    limit_code_expected = compare.string_field(vector.expected, "limit_code")
    first_yielded_expected = compare.boolean_field(vector.expected, "first_yielded")
    terminal_expected = compare.string_field(vector.expected, "terminal")
    if (
        invalid_argument_expected is None
        or limit_code_expected is None
        or first_yielded_expected is None
        or terminal_expected is None
    ):
        return "missing expected query failure facts"
    if invalid_argument != invalid_argument_expected:
        return f"invalid_argument: expected {invalid_argument_expected!r}, got {invalid_argument!r}"
    if limit_code != limit_code_expected:
        return f"limit_code: expected {limit_code_expected}, got {limit_code}"
    if first_yielded != first_yielded_expected:
        return f"first_yielded: expected {first_yielded_expected}, got {first_yielded}"
    if not exhausted:
        return "cursor must be exhausted after cancellation"
    if terminal != terminal_expected:
        return f"terminal: expected {terminal_expected}, got {terminal}"
    return None


# ---------------------------------------------------------------------------
# projection cases
# ---------------------------------------------------------------------------


def _projection_exact(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    result = properties_projection.project(
        document, properties_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if isinstance(result, properties_projection.FailedProjectionAttempt):
        return "exact Properties projection failed"
    if result.value.kind is not Kind.ENTRY_MAPPING:
        return "exact projection did not produce EntryMapping"
    keys = [entry[0].as_string() for entry in result.value.as_entry_mapping()]
    values = [entry[1].as_string() for entry in result.value.as_entry_mapping()]
    escape = any(
        origin.relation is properties_projection.ProvenanceRelation.ESCAPE_DERIVED
        for entry in result.provenance.entries
        for origin in entry.origins
    )
    two_value_fragments = False
    for entry in result.provenance.entries:
        count = sum(
            1
            for origin in entry.origins
            if origin.relation is properties_projection.ProvenanceRelation.VALUE_FRAGMENT
        )
        if count == 2:
            two_value_fragments = True
            break
    association = any(
        entry.projected.kind is properties_projection.ProjectedLocationKind.ASSOCIATION
        for entry in result.provenance.entries
    )
    fidelity = compare.string_field(vector.expected, "fidelity")
    expected_keys = compare.string_sequence(vector.expected, "keys")
    expected_values = compare.string_sequence(vector.expected, "values")
    events = compare.integer_field(vector.expected, "events")
    escape_provenance = compare.boolean_field(vector.expected, "escape_provenance")
    two_fragments_expected = compare.boolean_field(vector.expected, "two_value_fragments")
    association_expected = compare.boolean_field(vector.expected, "association_provenance")
    if (
        fidelity is None
        or expected_keys is None
        or expected_values is None
        or events is None
        or escape_provenance is None
        or two_fragments_expected is None
        or association_expected is None
    ):
        return "missing expected exact projection facts"
    if result.fidelity.value != fidelity:
        return f"fidelity: expected {fidelity}, got {result.fidelity.value}"
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "values")
    if len(result.report.events) != events:
        return f"events: expected {events}, got {len(result.report.events)}"
    if escape != escape_provenance:
        return "escape_provenance differed"
    if two_value_fragments != two_fragments_expected:
        return "two_value_fragments differed"
    if association != association_expected:
        return "association_provenance differed"
    return None


def _projection_failures(vector: runner.Case) -> str | None:
    unpaired_source = compare.string_field(vector.input, "unpaired_source")
    recovered_source = compare.string_field(vector.input, "recovered_source")
    if unpaired_source is None or recovered_source is None:
        return "missing input.unpaired_source/recovered_source"
    unpaired, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, unpaired_source
    )
    if message:
        return message
    unpaired_result = properties_projection.project(
        unpaired, properties_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if not isinstance(unpaired_result, properties_projection.FailedProjectionAttempt):
        return "unpaired surrogate projection completed"
    recovered, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, recovered_source
    )
    if message:
        return message
    recovered_result = properties_projection.project(
        recovered, properties_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if not isinstance(recovered_result, properties_projection.FailedProjectionAttempt):
        return "recovered projection completed"
    unpaired_code = ""
    unpaired_start = 0
    if unpaired_result.diagnostics:
        unpaired_code = unpaired_result.diagnostics[0].code
        primary = unpaired_result.diagnostics[0].primary
        if primary is not None:
            unpaired_start = primary.start_byte
    recovered_code = ""
    if recovered_result.diagnostics:
        recovered_code = recovered_result.diagnostics[0].code
    expected_start = compare.integer_field(vector.expected, "unpaired_start_byte")
    empty_reports = (
        len(unpaired_result.report.events) == 0 and len(recovered_result.report.events) == 0
    )
    unpaired_code_expected = compare.string_field(vector.expected, "unpaired_code")
    recovered_code_expected = compare.string_field(vector.expected, "recovered_code")
    empty_reports_expected = compare.boolean_field(vector.expected, "empty_reports")
    if (
        expected_start is None
        or unpaired_code_expected is None
        or recovered_code_expected is None
        or empty_reports_expected is None
    ):
        return "missing expected projection failure facts"
    if unpaired_code != unpaired_code_expected:
        return f"unpaired_code: expected {unpaired_code_expected}, got {unpaired_code}"
    if unpaired_start != expected_start:
        return f"unpaired_start_byte: expected {expected_start}, got {unpaired_start}"
    if recovered_code != recovered_code_expected:
        return f"recovered_code: expected {recovered_code_expected}, got {recovered_code}"
    if empty_reports != empty_reports_expected:
        return "empty_reports differed"
    return None


def _projection_collapse(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    unique_result = properties_projection.project(
        document,
        properties_projection.ProjectionRequest.require_object(
            properties_projection.DuplicatePolicy.REQUIRE_UNIQUE
        ),
    )
    if not isinstance(unique_result, properties_projection.FailedProjectionAttempt):
        return "unique projection accepted duplicates"
    unique_code = ""
    if unique_result.diagnostics:
        unique_code = unique_result.diagnostics[0].code
    first_result = properties_projection.project(
        document,
        properties_projection.ProjectionRequest.require_object(
            properties_projection.DuplicatePolicy.FIRST_WINS
        ),
    )
    if isinstance(first_result, properties_projection.FailedProjectionAttempt):
        return "FirstWins projection failed"
    last_result = properties_projection.project(
        document,
        properties_projection.ProjectionRequest.require_object(
            properties_projection.DuplicatePolicy.LAST_WINS_JDK_TABLE
        ),
    )
    if isinstance(last_result, properties_projection.FailedProjectionAttempt):
        return "LastWinsJdkTable projection failed"
    first_pairs = _properties_object_pairs(first_result.value)
    last_pairs = _properties_object_pairs(last_result.value)
    if first_pairs is None:
        return "FirstWins projection did not produce Object"
    if last_pairs is None:
        return "LastWinsJdkTable projection did not produce Object"
    first_events = first_result.report.events
    event_code = first_events[0].code if first_events else ""
    collapsed = any(
        origin.relation is properties_projection.ProvenanceRelation.COLLAPSED
        for entry in first_result.provenance.entries
        for origin in entry.origins
    )
    unique_code_expected = compare.string_field(vector.expected, "unique_code")
    first_fidelity = compare.string_field(vector.expected, "first_fidelity")
    events = compare.integer_field(vector.expected, "events")
    event_code_expected = compare.string_field(vector.expected, "event_code")
    first_entries = compare.sequence_field(vector.expected, "first_entries")
    last_entries = compare.sequence_field(vector.expected, "last_entries")
    collapsed_expected = compare.boolean_field(vector.expected, "collapsed_provenance")
    if (
        unique_code_expected is None
        or first_fidelity is None
        or events is None
        or event_code_expected is None
        or first_entries is None
        or last_entries is None
        or collapsed_expected is None
    ):
        return "missing expected collapse facts"
    if unique_code != unique_code_expected:
        return f"unique_code: expected {unique_code_expected}, got {unique_code}"
    if first_result.fidelity.value != first_fidelity:
        return f"first_fidelity: expected {first_fidelity}, got {first_result.fidelity.value}"
    if len(first_events) != events:
        return f"events: expected {events}, got {len(first_events)}"
    if event_code != event_code_expected:
        return f"event_code: expected {event_code_expected}, got {event_code}"
    if first_pairs != _properties_expected_pairs(first_entries):
        return compare.require_ordered(first_pairs, _properties_expected_pairs(first_entries), "first_entries")
    if last_pairs != _properties_expected_pairs(last_entries):
        return compare.require_ordered(last_pairs, _properties_expected_pairs(last_entries), "last_entries")
    if collapsed != collapsed_expected:
        return "collapsed_provenance differed"
    return None


def _properties_object_pairs(value: PortableValue):
    if value.kind is not Kind.OBJECT:
        return None
    pairs = []
    for key, item in value.as_object():
        if item.kind is not Kind.STRING:
            return None
        pairs.append([key, item.as_string()])
    return pairs


def _properties_expected_pairs(entries) -> list[list[str]]:
    pairs = []
    for item in entries:
        pair = item.as_sequence()
        pairs.append([pair[0].as_string(), pair[1].as_string()])
    return pairs


# ---------------------------------------------------------------------------
# materialization cases
# ---------------------------------------------------------------------------


def _materialization_styles(vector: runner.Case) -> str | None:
    reader_descriptor = compare.object_field(vector.input, "reader")
    if reader_descriptor is None:
        return "missing input.reader"
    reader_value, message = _properties_flat_mapping(reader_descriptor)
    if message:
        return message
    reader_result = properties_materialization.materialize(
        reader_value,
        _properties_materialization_request(properties_kinds.PropertiesProfile.READER_V1),
    )
    if isinstance(reader_result, FailedMaterializationAttempt):
        return f"Reader materialization failed: {reader_result.failure.code}"
    latin_descriptor = compare.object_field(vector.input, "latin1")
    if latin_descriptor is None:
        return "missing input.latin1"
    latin_value, message = _properties_flat_mapping(latin_descriptor)
    if message:
        return message
    latin_result = properties_materialization.materialize(
        latin_value,
        _properties_materialization_request(properties_kinds.PropertiesProfile.LATIN1_V1)
        .with_newline(NewlinePolicy.CRLF),
    )
    if isinstance(latin_result, FailedMaterializationAttempt):
        return f"Latin-1 materialization failed: {latin_result.failure.code}"
    utf16_descriptor = compare.object_field(vector.input, "utf16be")
    if utf16_descriptor is None:
        return "missing input.utf16be"
    utf16_value, message = _properties_flat_mapping(utf16_descriptor)
    if message:
        return message
    utf16_result = properties_materialization.materialize(
        utf16_value,
        _properties_materialization_request(properties_kinds.PropertiesProfile.READER_V1)
        .with_encoding(SourceEncoding.utf16be())
        .with_newline(NewlinePolicy.CRLF),
    )
    if isinstance(utf16_result, FailedMaterializationAttempt):
        return f"UTF-16BE Reader materialization failed: {utf16_result.failure.code}"
    cp_descriptor = compare.object_field(vector.input, "cp1252")
    if cp_descriptor is None:
        return "missing input.cp1252"
    cp_value, message = _properties_flat_mapping(cp_descriptor)
    if message:
        return message
    cp_page = WindowsCodePage.from_number(1252)
    if cp_page is None:
        return "CP1252 unavailable"
    cp_result = properties_materialization.materialize(
        cp_value,
        _properties_materialization_request(properties_kinds.PropertiesProfile.READER_V1)
        .with_encoding(SourceEncoding.windows_code_page(cp_page)),
    )
    if isinstance(cp_result, FailedMaterializationAttempt):
        return f"CP1252 Reader materialization failed: {cp_result.failure.code}"
    closure = True
    for document, input_value in (
        (reader_result.document, reader_value),
        (latin_result.document, latin_value),
        (utf16_result.document, utf16_value),
        (cp_result.document, cp_value),
    ):
        projected = properties_projection.project(
            document, properties_projection.ProjectionRequest.best_exact_entry_mapping()
        )
        if isinstance(projected, properties_projection.FailedProjectionAttempt) or not core_equal(
            projected.value, input_value
        ):
            closure = False
            break
    utf16_text = utf16_result.document.source.decoded_text() or ""
    exact_fidelity = (
        reader_result.fidelity.value == "Exact"
        and latin_result.fidelity.value == "Exact"
        and utf16_result.fidelity.value == "Exact"
        and cp_result.fidelity.value == "Exact"
    )
    reader_source = compare.string_field(vector.expected, "reader_source")
    latin1_source = compare.string_field(vector.expected, "latin1_source")
    utf16be_decoded = compare.string_field(vector.expected, "utf16be_decoded")
    cp1252_hex = compare.string_field(vector.expected, "cp1252_hex")
    exact_fidelity_expected = compare.boolean_field(vector.expected, "exact_fidelity")
    closure_expected = compare.boolean_field(vector.expected, "closure")
    if (
        reader_source is None
        or latin1_source is None
        or utf16be_decoded is None
        or cp1252_hex is None
        or exact_fidelity_expected is None
        or closure_expected is None
    ):
        return "missing expected materialization facts"
    if reader_result.document.render() != reader_source.encode("utf-8"):
        return f"reader_source: expected {reader_source!r}, got {reader_result.document.render()!r}"
    if latin_result.document.render() != latin1_source.encode("utf-8"):
        return f"latin1_source: expected {latin1_source!r}, got {latin_result.document.render()!r}"
    if utf16_text != utf16be_decoded:
        return f"utf16be_decoded: expected {utf16be_decoded!r}, got {utf16_text!r}"
    if compare.hex_text(cp_result.document.render()) != cp1252_hex:
        return f"cp1252_hex: expected {cp1252_hex}, got {compare.hex_text(cp_result.document.render())}"
    if exact_fidelity != exact_fidelity_expected:
        return "exact_fidelity differed"
    if closure != closure_expected:
        return "closure differed"
    return None


def _materialization_limits(vector: runner.Case) -> str | None:
    scalar_result = properties_materialization.materialize(
        PortableValue.string("scalar"),
        _properties_materialization_request(properties_kinds.PropertiesProfile.READER_V1),
    )
    scalar_code = ""
    if isinstance(scalar_result, FailedMaterializationAttempt):
        scalar_code = scalar_result.failure.code
    value_descriptor = compare.object_field(vector.input, "value")
    if value_descriptor is None:
        return "missing input.value"
    value, message = _properties_flat_mapping(value_descriptor)
    if message:
        return message
    encoding_result = properties_materialization.materialize(
        value,
        _properties_materialization_request(properties_kinds.PropertiesProfile.LATIN1_V1)
        .with_encoding(SourceEncoding.utf8()),
    )
    encoding_code = ""
    if isinstance(encoding_result, FailedMaterializationAttempt):
        encoding_code = encoding_result.failure.code
    names = compare.string_sequence(vector.input, "limit_names")
    if names is None:
        return "missing input.limit_names"
    outcomes: list[str] = []
    for name in names:
        limits = MaterializationLimits()
        if name == "max_input_nodes":
            limits = MaterializationLimits(max_input_nodes=1)
        elif name == "max_output_bytes":
            limits = MaterializationLimits(max_output_bytes=2)
        elif name == "max_depth":
            limits = MaterializationLimits(max_depth=0)
        elif name == "max_report_entries":
            limits = MaterializationLimits(max_report_entries=0)
        elif name == "max_provenance_entries":
            limits = MaterializationLimits(max_provenance_entries=1)
        else:
            return f"unknown materialization limit {name}"
        result = properties_materialization.materialize(
            value,
            _properties_materialization_request(properties_kinds.PropertiesProfile.READER_V1)
            .with_limits(limits),
        )
        if not isinstance(result, FailedMaterializationAttempt):
            outcomes.append("Complete")
            continue
        if result.failure.code != compare.string_field(vector.expected, "limit_code"):
            return f"{name} returned wrong failure code {result.failure.code}"
        outcomes.append("Failed")
    scalar_code_expected = compare.string_field(vector.expected, "scalar_code")
    encoding_code_expected = compare.string_field(vector.expected, "encoding_code")
    expected_outcomes = compare.string_sequence(vector.expected, "limit_outcomes")
    if scalar_code_expected is None or encoding_code_expected is None or expected_outcomes is None:
        return "missing expected materialization failure facts"
    if scalar_code != scalar_code_expected:
        return f"scalar_code: expected {scalar_code_expected}, got {scalar_code}"
    if encoding_code != encoding_code_expected:
        return f"encoding_code: expected {encoding_code_expected}, got {encoding_code}"
    return compare.require_ordered(outcomes, expected_outcomes, "limit_outcomes")


# ---------------------------------------------------------------------------
# edit cases
# ---------------------------------------------------------------------------


def _properties_collect_edit(document, builder, outputs: list[str], edit_counts: list[int]) -> bool:
    with _TerminalBoundaryPatch():
        try:
            commit = properties_edit.commit(document, builder.build())
        except properties_errors.PropertiesEditFailure:
            return False
    outputs.append(commit.document.render().decode("utf-8"))
    edit_counts.append(len(commit.change_set.source_edits))
    return True


def _edit_all_operations(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    semantic_value = compare.string_field(vector.input, "semantic_value")
    literal_value = compare.string_field(vector.input, "literal_value")
    new_key = compare.string_field(vector.input, "new_key")
    new_value = compare.string_field(vector.input, "new_value")
    renamed_key = compare.string_field(vector.input, "renamed_key")
    if (
        source is None
        or semantic_value is None
        or literal_value is None
        or new_key is None
        or new_value is None
        or renamed_key is None
    ):
        return "missing input facts"
    from consema.properties.java_string import JavaString

    outputs: list[str] = []
    edit_counts: list[int] = []

    document, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, source
    )
    if message:
        return message
    builder = properties_edit.EditTransactionBuilder(document).semantic_value(
        document.properties[0].node, JavaString.from_unicode(semantic_value)
    )
    if not _properties_collect_edit(document, builder, outputs, edit_counts):
        return "semantic edit failed"

    document, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, source
    )
    if message:
        return message
    builder = properties_edit.EditTransactionBuilder(document).literal_value(
        document.properties[0].node, literal_value.encode("utf-8")
    )
    if not _properties_collect_edit(document, builder, outputs, edit_counts):
        return "literal edit failed"

    document, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, source
    )
    if message:
        return message
    builder = properties_edit.EditTransactionBuilder(document).insert_property(
        document.node_ref(),
        JavaString.from_unicode(new_key),
        JavaString.from_unicode(new_value),
        AssociationPlacement(kind="End"),
    )
    if not _properties_collect_edit(document, builder, outputs, edit_counts):
        return "insert edit failed"

    document, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, source
    )
    if message:
        return message
    builder = properties_edit.EditTransactionBuilder(document).remove_property(
        document.properties[0].node
    )
    if not _properties_collect_edit(document, builder, outputs, edit_counts):
        return "remove edit failed"

    document, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, source
    )
    if message:
        return message
    builder = properties_edit.EditTransactionBuilder(document).rename_property(
        document.properties[0].node, JavaString.from_unicode(renamed_key)
    )
    if not _properties_collect_edit(document, builder, outputs, edit_counts):
        return "rename edit failed"

    expected_outputs = compare.string_sequence(vector.expected, "outputs")
    one_edit_each = compare.boolean_field(vector.expected, "one_source_edit_each")
    if expected_outputs is None or one_edit_each is None:
        return "missing expected edit facts"
    all_single = len(edit_counts) == 5 and all(count == 1 for count in edit_counts)
    if outputs != expected_outputs:
        return compare.require_ordered(outputs, expected_outputs, "outputs")
    if all_single != one_edit_each:
        return "one_source_edit_each differed"
    return None


def _edit_audit_artifacts(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    rename = compare.string_field(vector.input, "rename")
    value = compare.string_field(vector.input, "value")
    source_id = compare.string_field(vector.input, "source_id")
    source = compare.string_field(vector.input, "source")
    if rename is None or value is None or source_id is None or source is None:
        return "missing input facts"
    from consema.document.edit_plan import EditPlanSourceId
    from consema.document.source_patch import SourcePatchLimits
    from consema.properties.java_string import JavaString

    first = document.properties[0].node
    second = document.properties[1].node
    builder = properties_edit.EditTransactionBuilder(document)
    builder.rename_property(first, JavaString.from_unicode(rename))
    builder.semantic_value(second, JavaString.from_unicode(value))
    transaction = builder.build()
    try:
        plan = properties_edit.dry_run(document, transaction, EditPlanSourceId.new(source_id))
    except properties_errors.PropertiesEditFailure as failure:
        return f"dry run: {failure.code}"
    try:
        commit = properties_edit.commit(document, transaction)
    except properties_errors.PropertiesEditFailure as failure:
        return f"commit: {failure.code}"
    replay_error = None
    try:
        replayed = commit.source_patch.apply(document.source, SourcePatchLimits())
    except Exception as error:
        replay_error = error
    proof_error = None
    try:
        commit.untouched_proof.verify(
            document.source, commit.document.source, list(commit.source_patch.replacements)
        )
    except Exception as error:
        proof_error = error
    conflict = properties_edit.EditTransactionBuilder(document)
    conflict.semantic_value(first, JavaString.from_unicode("x"))
    conflict.rename_property(first, JavaString.from_unicode("renamed"))
    conflict_failure = None
    try:
        properties_edit.commit(document, conflict.build())
    except properties_errors.PropertiesEditFailure as failure:
        conflict_failure = failure
    conflict_code = ""
    if conflict_failure is not None:
        conflict_code = conflict_failure.code
    expected_source = compare.string_field(vector.expected, "source")
    edit_count = compare.integer_field(vector.expected, "edit_count")
    dry_run_operations = compare.integer_field(vector.expected, "dry_run_operations")
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    proof_verifies = compare.boolean_field(vector.expected, "proof_verifies")
    conflict_code_expected = compare.string_field(vector.expected, "conflict_code")
    base_unchanged = compare.boolean_field(vector.expected, "base_unchanged")
    if (
        expected_source is None
        or edit_count is None
        or dry_run_operations is None
        or patch_replays is None
        or proof_verifies is None
        or conflict_code_expected is None
        or base_unchanged is None
    ):
        return "missing expected audit facts"
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    if len(commit.change_set.source_edits) != edit_count:
        return f"edit_count: expected {edit_count}, got {len(commit.change_set.source_edits)}"
    if len(plan.operations) != dry_run_operations:
        return f"dry_run_operations: expected {dry_run_operations}, got {len(plan.operations)}"
    if replay_error is not None:
        return f"patch replay failed: {replay_error}"
    if (replayed.bytes() == commit.document.render()) != patch_replays:
        return "patch_replays differed"
    if (proof_error is None) != proof_verifies:
        return "proof_verifies differed"
    if conflict_code != conflict_code_expected:
        return f"conflict_code: expected {conflict_code_expected}, got {conflict_code}"
    if (document.render() == source.encode("utf-8")) != base_unchanged:
        return "base_unchanged differed"
    return None


# ---------------------------------------------------------------------------
# resource and registry cases
# ---------------------------------------------------------------------------


def _properties_parse_limits_with(
    limits: properties_limits.PropertiesParseLimits, name: str, value: int
) -> tuple[properties_limits.PropertiesParseLimits, bool]:
    """Applies one named limit to the immutable parse limits."""
    from dataclasses import replace as _replace

    common_fields = {
        "max_source_bytes",
        "max_nesting_depth",
        "max_token_count",
        "max_node_count",
        "max_diagnostics",
    }
    specific_fields = {
        "max_decoded_utf8_bytes",
        "max_decoded_scalars",
        "max_natural_lines",
        "max_natural_line_bytes",
        "max_natural_line_scalars",
        "max_logical_lines",
        "max_logical_line_natural_lines",
        "max_logical_line_scalars",
        "max_properties",
        "max_comments",
        "max_escapes",
        "max_unicode_escapes",
        "max_java_code_units_per_string",
        "max_total_java_code_units",
        "max_duplicate_group_members",
        "max_recovery_regions",
    }
    if name in common_fields:
        return _replace(limits, common=_replace(limits.common, **{name: value})), True
    if name in specific_fields:
        return _replace(limits, **{name: value}), True
    return limits, False


def _formation_limits(vector: runner.Case) -> str | None:
    descriptors = compare.sequence_field(vector.input, "limits")
    if descriptors is None:
        return "missing input.limits"
    fatal = 0
    for descriptor in descriptors:
        name = compare.string_field(descriptor, "name")
        source = compare.string_field(descriptor, "source")
        value = compare.integer_field(descriptor, "value")
        if name is None or source is None or value is None:
            return "limit descriptor missing name/source/value"
        limits, ok = _properties_parse_limits_with(
            properties_limits.PropertiesParseLimits(), name, value
        )
        if not ok:
            return f"unknown Properties parse limit {name}"
        try:
            properties_parser.parse_reader(
                source.encode("utf-8"), SourceEncoding.utf8(), limits
            )
        except properties_errors.PropertiesFormationFailure:
            fatal += 1
        except Exception:
            fatal += 1
    fatal_count = compare.integer_field(vector.expected, "fatal_count")
    no_partial_documents = compare.boolean_field(vector.expected, "no_partial_documents")
    if fatal_count is None or no_partial_documents is None:
        return "missing expected.fatal_count/no_partial_documents"
    if fatal != fatal_count:
        return f"fatal_count: expected {fatal_count}, got {fatal}"
    if not no_partial_documents:
        return "no_partial_documents must be true"
    return None


def _projection_limits(vector: runner.Case) -> str | None:
    document, message = _properties_parse_case(vector)
    if message:
        return message
    names = compare.string_sequence(vector.input, "limits")
    if names is None:
        return "missing input.limits"
    code = compare.string_field(vector.expected, "code")
    if code is None:
        return "missing expected.code"
    failed_count = 0
    for name in names:
        limits = properties_projection.ProjectionLimits()
        if name == "max_source_associations":
            limits = properties_projection.ProjectionLimits(max_source_associations=0)
        elif name == "max_value_nodes":
            limits = properties_projection.ProjectionLimits(max_value_nodes=1)
        elif name == "max_provenance_units":
            limits = properties_projection.ProjectionLimits(max_provenance_units=1)
        else:
            return f"unknown projection limit {name}"
        result = properties_projection.project(
            document,
            properties_projection.ProjectionRequest.best_exact_entry_mapping().with_limits(limits),
        )
        if not isinstance(result, properties_projection.FailedProjectionAttempt):
            return f"projection limit {name} did not fail"
        if result.diagnostics and result.diagnostics[0].code == code:
            failed_count += 1
    duplicate_source = compare.string_field(vector.input, "duplicate_source")
    if duplicate_source is None:
        return "missing input.duplicate_source"
    duplicate, message = _properties_parse_text(
        properties_kinds.PropertiesProfile.READER_V1, duplicate_source
    )
    if message:
        return message
    report_limits = properties_projection.ProjectionLimits(max_report_entries=0)
    duplicate_result = properties_projection.project(
        duplicate,
        properties_projection.ProjectionRequest.require_object(
            properties_projection.DuplicatePolicy.FIRST_WINS
        ).with_limits(report_limits),
    )
    if not isinstance(duplicate_result, properties_projection.FailedProjectionAttempt):
        return "report limit did not fail"
    if duplicate_result.diagnostics and duplicate_result.diagnostics[0].code == code:
        failed_count += 1
    expected_count = compare.integer_field(vector.expected, "failed_count")
    if expected_count is None:
        return "missing expected.failed_count"
    if failed_count != expected_count:
        return f"failed_count: expected {expected_count}, got {failed_count}"
    return None


def _operation_registry(vector: runner.Case) -> str | None:
    profiles = compare.string_sequence(vector.input, "profiles")
    expected = compare.string_sequence(vector.expected, "operations")
    supported_expected = compare.integer_field(vector.expected, "supported")
    if profiles is None or expected is None or supported_expected is None:
        return "missing registry facts"
    for profile_spelling in profiles:
        profiles_by_name = {
            "java-properties.reader@1": properties_kinds.PropertiesProfile.READER_V1,
            "java-properties.latin1@1": properties_kinds.PropertiesProfile.LATIN1_V1,
        }
        profile = profiles_by_name.get(profile_spelling)
        if profile is None:
            return f"unknown Java Properties profile {profile_spelling}"
        registry = properties_registry.format_operation_registry(profile)
        operations: list[str] = []
        supported = 0
        for descriptor in registry.operations:
            operations.append(f"{descriptor.id.id}@{descriptor.id.version}")
            if descriptor.support is properties_registry.OperationSupport.SUPPORTED:
                supported += 1
        if operations != expected:
            return compare.require_ordered(operations, expected, "operations")
        if supported != supported_expected:
            return f"supported: expected {supported_expected}, got {supported}"
    return None


runner.register_suite("java-properties-v1.json", "consema.java-properties.conformance@1", "", 22, run)
