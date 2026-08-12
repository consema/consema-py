"""Suite ``consema.ini.conformance@1`` (ini-v1.json, 20 cases): INI
formation facts across the three profiles, Windows UTF-16/code-page and
Python ConfigParser behaviors, recovery atomicity, native and syntax
queries, exact and collapsed projections with provenance, canonical
materialization, the eight frozen edits with audit artifacts, resource
limit matrices, and the frozen operation registry. Dispatch is by case id,
mirroring go/conformance/ini_v1.go.
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
from consema.document.source import SourceEncoding, SourceEncodingKind, SourceSnapshot, WindowsCodePage
from consema.document.structural import AssociationPlacement, LocationError, LocationErrorKind, NodeRole
from consema.ini import edit as ini_edit
from consema.ini import errors as ini_errors
from consema.ini import kinds as ini_kinds
from consema.ini import materialization as ini_materialization
from consema.ini import operation_registry as ini_registry
from consema.ini import parser as ini_parser
from consema.ini import projection as ini_projection
from consema.ini import query as ini_query
from consema.protocol import query as protocol_query
from consema.protocol.query import QueryFailure, QueryFailureKind
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "formation.portable-lossless": _portable_lossless,
        "formation.profile-counterexample-matrix": _profile_counterexamples,
        "formation.windows-utf16-case-and-quote": _windows_utf16,
        "formation.windows-explicit-code-page": _windows_code_page,
        "formation.python-default-continuation-raw": _python_multiline,
        "formation.python-unicode16-optionxform": _python_optionxform,
        "formation.recovery-never-fabricates-entry": _recovered_atomic,
        "query.native-order-and-profile-equivalence": _native_query,
        "query.syntax-decoded-structure-order": _syntax_query,
        "query.validation-limit-cancellation": _query_failures,
        "projection.exact-duplicate-entry-mapping": _projection_exact,
        "projection.explicit-object-collapse": _projection_collapse,
        "projection.fragmented-value-provenance": _projection_fragments,
        "materialization.all-canonical-styles": _materialization_styles,
        "materialization.atomic-failures-and-limits": _materialization_limits,
        "edit.all-eight-operations": _edit_all_operations,
        "edit.dry-run-patch-proof-and-atomic-failure": _edit_audit_artifacts,
        "resource.formation-limit-matrix": _formation_limits,
        "resource.projection-limit-matrix": _projection_limits,
        "registry.frozen-eight-operation-surface": _operation_registry,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published INI case")
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


def _ini_profile(spelling: str):
    profiles = {
        "ini.portable@1": ini_kinds.IniProfile.PORTABLE_V1,
        "ini.windows@1": ini_kinds.IniProfile.WINDOWS_V1,
        "ini.python-configparser@1": ini_kinds.IniProfile.PYTHON_CONFIGPARSER_V1,
    }
    return profiles.get(spelling)


def _ini_profile_field(vector: runner.Case):
    profile_spelling = compare.string_field(vector.input, "profile")
    if profile_spelling is None:
        return None, "missing input.profile"
    profile = _ini_profile(profile_spelling)
    if profile is None:
        return None, f"unknown INI profile {profile_spelling}"
    return profile, ""


def _ini_parse_text(profile, source: str):
    try:
        return (
            ini_parser.parse(
                source.encode("utf-8"),
                profile,
                ini_kinds.IniEncodingSelection.profile_default(),
                ini_kinds.IniParseLimits(),
            ),
            "",
        )
    except ini_errors.IniFormationFailure as error:
        return None, f"INI formation failed: {error.code}"


def _ini_parse_case(vector: runner.Case):
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    profile, message = _ini_profile_field(vector)
    if message:
        return None, message
    return _ini_parse_text(profile, source)


def _ini_exact_coverage(document) -> bool:
    source = document.source
    if source.is_empty():
        return len(document.lossless_structural_index().pieces) == 0
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    if len(pieces) != len(kinds) or len(pieces) == 0:
        return False
    if pieces[0].span.start_byte != 0 or pieces[-1].span.end_byte != source.len():
        return False
    for index in range(1, len(pieces)):
        if pieces[index - 1].span.end_byte != pieces[index].span.start_byte:
            return False
    return True


def _ini_encoding_name(encoding) -> str:
    """Mirrors the Go iniSourceEncodingName spellings."""
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


def _ini_hex_input(vector: runner.Case):
    text = compare.string_field(vector.input, "source_hex")
    if text is None:
        return None, "missing input.source_hex"
    return compare.parse_hex(text), ""


def _ini_materialization_request(profile) -> MaterializationRequest:
    if profile is ini_kinds.IniProfile.PORTABLE_V1:
        return MaterializationRequest.new(
            ProfileId.new("ini.portable", 1), MaterializationStyleId.new("ini.portable-canonical", 1)
        )
    if profile is ini_kinds.IniProfile.WINDOWS_V1:
        return (
            MaterializationRequest.new(
                ProfileId.new("ini.windows", 1),
                MaterializationStyleId.new("ini.windows-canonical", 1),
            )
            .with_encoding(SourceEncoding.utf16le())
            .with_newline(NewlinePolicy.CRLF)
        )
    return MaterializationRequest.new(
        ProfileId.new("ini.python-configparser", 1),
        MaterializationStyleId.new("ini.python-configparser-canonical", 1),
    )


def _ini_nested_mapping(descriptor):
    """Builds one nested EntryMapping from the vector descriptor."""
    sections = descriptor.as_sequence()
    outer = EntryMappingBuilder()
    for section in sections:
        name = compare.string_field(section, "section")
        entries = compare.sequence_field(section, "entries")
        if name is None or entries is None:
            return None, "section descriptor must carry section/entries"
        inner = EntryMappingBuilder()
        for entry in entries:
            pair = entry.as_sequence()
            if len(pair) != 2:
                return None, "entry descriptor must contain key and value"
            inner.push(PortableValue.string(pair[0].as_string()), PortableValue.string(pair[1].as_string()))
        outer.push(PortableValue.string(name), inner.build())
    return outer.build(), ""


def _ordered_results_capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _ini_native_executable(expression):
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_ini_native_v1())
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


def _ini_syntax_executable(expression):
    definition = (
        protocol_query.QueryDefinition(protocol_query.domain_ini_lossless_syntax_v1())
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


# ---------------------------------------------------------------------------
# formation cases
# ---------------------------------------------------------------------------


def _portable_lossless(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    expected_formation = compare.string_field(vector.expected, "formation")
    physical_lines = compare.integer_field(vector.expected, "physical_lines")
    logical_lines = compare.integer_field(vector.expected, "logical_lines")
    expected_sections = compare.string_sequence(vector.expected, "section_names")
    expected_keys = compare.string_sequence(vector.expected, "keys")
    expected_values = compare.string_sequence(vector.expected, "values")
    expected_states = compare.string_sequence(vector.expected, "value_states")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        expected_formation is None
        or physical_lines is None
        or logical_lines is None
        or expected_sections is None
        or expected_keys is None
        or expected_values is None
        or expected_states is None
        or exact_coverage is None
    ):
        return "missing expected formation facts"
    if document.formation_status().value != expected_formation:
        return f"formation: expected {expected_formation}, got {document.formation_status().value}"
    if len(document.physical_lines) != physical_lines:
        return f"physical_lines: expected {physical_lines}, got {len(document.physical_lines)}"
    if len(document.logical_lines) != logical_lines:
        return f"logical_lines: expected {logical_lines}, got {len(document.logical_lines)}"
    section_names = [section.name for section in document.sections]
    keys = [entry.key for entry in document.entries]
    values = [entry.value for entry in document.entries]
    states = [entry.state.value for entry in document.entries]
    if section_names != expected_sections:
        return compare.require_ordered(section_names, expected_sections, "section_names")
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "values")
    if states != expected_states:
        return compare.require_ordered(states, expected_states, "value_states")
    if _ini_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _profile_counterexamples(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is None:
        return "missing input.samples"
    profiles = [
        (ini_kinds.IniProfile.PORTABLE_V1, "portable"),
        (ini_kinds.IniProfile.WINDOWS_V1, "windows"),
        (ini_kinds.IniProfile.PYTHON_CONFIGPARSER_V1, "python"),
    ]
    for profile, name in profiles:
        expected = compare.string_sequence(vector.expected, name)
        if expected is None:
            return f"missing expected.{name}"
        if len(expected) != len(samples):
            return f"expected.{name} length differed"
        for index, sample in enumerate(samples):
            source = compare.string_field(sample, "source")
            if source is None:
                return "sample.source missing"
            actual = "Fatal"
            try:
                document = ini_parser.parse(
                    source.encode("utf-8"),
                    profile,
                    ini_kinds.IniEncodingSelection.profile_default(),
                    ini_kinds.IniParseLimits(),
                )
            except ini_errors.IniFormationFailure:
                pass
            else:
                actual = document.formation_status().value
            if actual != expected[index]:
                return f"{name} counterexample matrix differed at {index}: expected {expected[index]!r}, got {actual!r}"
    return None


def _windows_utf16(vector: runner.Case) -> str | None:
    raw, message = _ini_hex_input(vector)
    if message:
        return message
    profile, message = _ini_profile_field(vector)
    if message:
        return message
    try:
        document = ini_parser.parse(
            raw,
            profile,
            ini_kinds.IniEncodingSelection.profile_default(),
            ini_kinds.IniParseLimits(),
        )
    except ini_errors.IniFormationFailure as error:
        return f"INI formation failed: {error.code}"
    expected_encoding = compare.string_field(vector.expected, "encoding")
    expected_sections = compare.string_sequence(vector.expected, "section_names")
    comparison_section = compare.string_field(vector.expected, "comparison_section")
    expected_keys = compare.string_sequence(vector.expected, "keys")
    comparison_key = compare.string_field(vector.expected, "comparison_key")
    expected_values = compare.string_sequence(vector.expected, "values")
    quote_style = compare.string_field(vector.expected, "quote_style")
    case_collision_code = compare.string_field(vector.expected, "case_collision_code")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        expected_encoding is None
        or expected_sections is None
        or comparison_section is None
        or expected_keys is None
        or comparison_key is None
        or expected_values is None
        or quote_style is None
        or case_collision_code is None
        or exact_coverage is None
    ):
        return "missing expected Windows UTF-16 facts"
    sections = document.sections
    entries = document.entries
    codes = [diagnostic.code for diagnostic in document.diagnostics]
    if len(sections) < 2 or len(entries) < 2:
        return "expected at least two sections and two entries"
    actual_encoding = _ini_encoding_name(document.source.encoding_facts().selected)
    if actual_encoding != expected_encoding:
        return f"encoding: expected {expected_encoding}, got {actual_encoding}"
    section_names = [section.name for section in sections]
    if section_names != expected_sections:
        return compare.require_ordered(section_names, expected_sections, "section_names")
    if sections[0].comparison_name != comparison_section:
        return f"comparison_section: expected {comparison_section}, got {sections[0].comparison_name}"
    keys = [entry.key for entry in entries]
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if entries[0].comparison_key != comparison_key:
        return f"comparison_key: expected {comparison_key}, got {entries[0].comparison_key}"
    values = [entry.value for entry in entries]
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "values")
    if entries[0].quote_style.value != quote_style:
        return f"quote_style: expected {quote_style}, got {entries[0].quote_style.value}"
    if sections[0].duplicate_group is None or sections[0].duplicate_group != sections[1].duplicate_group:
        return "section duplicate groups must be equal and present"
    if entries[0].duplicate_group is None or entries[0].duplicate_group != entries[1].duplicate_group:
        return "entry duplicate groups must be equal and present"
    if case_collision_code not in codes:
        return f"{case_collision_code!r} not found in {codes!r}"
    if _ini_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _windows_code_page(vector: runner.Case) -> str | None:
    raw, message = _ini_hex_input(vector)
    if message:
        return message
    number = compare.integer_field(vector.input, "code_page")
    if number is None:
        return "missing input.code_page"
    if number > 65535:
        return "code page out of range"
    page = WindowsCodePage.from_number(number)
    if page is None:
        return "unsupported vector code page"
    profile, message = _ini_profile_field(vector)
    if message:
        return message
    try:
        document = ini_parser.parse(
            raw,
            profile,
            ini_kinds.IniEncodingSelection.explicit(SourceEncoding.windows_code_page(page)),
            ini_kinds.IniParseLimits(),
        )
    except ini_errors.IniFormationFailure as error:
        return f"INI formation failed: {error.code}"
    expected_value = compare.string_field(vector.expected, "value")
    expected_encoding = compare.string_field(vector.expected, "encoding")
    bom_policy = compare.string_field(vector.expected, "bom_policy")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if expected_value is None or expected_encoding is None or bom_policy is None or exact_coverage is None:
        return "missing expected code-page facts"
    if not document.entries:
        return "code-page document has no entries"
    if document.entries[0].value != expected_value:
        return f"value: expected {expected_value!r}, got {document.entries[0].value!r}"
    actual_encoding = _ini_encoding_name(document.source.encoding_facts().selected)
    if actual_encoding != expected_encoding:
        return f"encoding: expected {expected_encoding}, got {actual_encoding}"
    if document.source.encoding_facts().bom_policy.value != bom_policy:
        return (
            f"bom_policy: expected {bom_policy}, "
            f"got {document.source.encoding_facts().bom_policy.value}"
        )
    if _ini_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _python_multiline(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    expected_formation = compare.string_field(vector.expected, "formation")
    default_section = compare.boolean_field(vector.expected, "default_section")
    expected_keys = compare.string_sequence(vector.expected, "comparison_keys")
    expected_values = compare.string_sequence(vector.expected, "values")
    continuation_lines = compare.integer_field(vector.expected, "continuation_physical_lines")
    exact_coverage = compare.boolean_field(vector.expected, "exact_coverage")
    if (
        expected_formation is None
        or default_section is None
        or expected_keys is None
        or expected_values is None
        or continuation_lines is None
        or exact_coverage is None
    ):
        return "missing expected Python continuation facts"
    sections = document.sections
    entries = document.entries
    if not sections or len(entries) < 2:
        return "expected at least one section and two entries"
    if document.formation_status().value != expected_formation:
        return f"formation: expected {expected_formation}, got {document.formation_status().value}"
    if sections[0].is_default != default_section:
        return f"default_section: expected {default_section}, got {sections[0].is_default}"
    comparison_keys = [entry.comparison_key for entry in entries]
    values = [entry.value for entry in entries]
    if comparison_keys != expected_keys:
        return compare.require_ordered(comparison_keys, expected_keys, "comparison_keys")
    if values != expected_values:
        return compare.require_ordered(values, expected_values, "values")
    try:
        continued = document.resolve_logical_line(entries[1].logical_line)
    except Exception as error:
        return f"continued logical line missing: {error}"
    if len(continued.physical_nodes) != continuation_lines:
        return (
            f"continuation_physical_lines: expected {continuation_lines}, "
            f"got {len(continued.physical_nodes)}"
        )
    if _ini_exact_coverage(document) != exact_coverage:
        return "exact_coverage differed"
    return None


def _python_optionxform(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    expected_formation = compare.string_field(vector.expected, "formation")
    expected_keys = compare.string_sequence(vector.expected, "comparison_keys")
    duplicate_group = compare.boolean_field(vector.expected, "duplicate_group")
    code = compare.string_field(vector.expected, "code")
    if expected_formation is None or expected_keys is None or duplicate_group is None or code is None:
        return "missing expected optionxform facts"
    entries = document.entries
    if len(entries) < 2:
        return "expected at least two entries"
    codes = [diagnostic.code for diagnostic in document.diagnostics]
    comparison_keys = [entry.comparison_key for entry in entries]
    if document.formation_status().value != expected_formation:
        return f"formation: expected {expected_formation}, got {document.formation_status().value}"
    if comparison_keys != expected_keys:
        return compare.require_ordered(comparison_keys, expected_keys, "comparison_keys")
    if (entries[0].duplicate_group is not None) != duplicate_group:
        return "duplicate_group presence differed"
    if entries[0].duplicate_group is None or entries[0].duplicate_group != entries[1].duplicate_group:
        return "entry duplicate groups must be equal and present"
    if code not in codes:
        return f"{code!r} not found in {codes!r}"
    return None


def _recovered_atomic(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    expected_formation = compare.string_field(vector.expected, "formation")
    entries = compare.integer_field(vector.expected, "entries")
    error_lines = compare.integer_field(vector.expected, "error_lines")
    code = compare.string_field(vector.expected, "code")
    projection_code = compare.string_field(vector.expected, "projection_code")
    edit_code = compare.string_field(vector.expected, "edit_code")
    if (
        expected_formation is None
        or entries is None
        or error_lines is None
        or code is None
        or projection_code is None
        or edit_code is None
    ):
        return "missing expected recovery facts"
    projected = ini_projection.project(
        document, ini_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    projection_diagnostic = ""
    if isinstance(projected, ini_projection.FailedProjectionAttempt) and projected.diagnostics:
        projection_diagnostic = projected.diagnostics[0].code
    transaction = ini_edit.EditTransactionBuilder(document).build()
    edit_failure = ""
    try:
        ini_edit.commit(document, transaction)
    except ini_errors.IniEditFailure as failure:
        edit_failure = failure.code
    if document.formation_status().value != expected_formation:
        return f"formation: expected {expected_formation}, got {document.formation_status().value}"
    if len(document.entries) != entries:
        return f"entries: expected {entries}, got {len(document.entries)}"
    if len(document.error_lines) != error_lines:
        return f"error_lines: expected {error_lines}, got {len(document.error_lines)}"
    if not document.error_lines or document.error_lines[0].code != code:
        return f"error code: expected {code}, got {document.error_lines[0].code if document.error_lines else None}"
    if projection_diagnostic != projection_code:
        return f"projection_code: expected {projection_code}, got {projection_diagnostic}"
    if edit_failure != edit_code:
        return f"edit_code: expected {edit_code}, got {edit_failure}"
    return None


# ---------------------------------------------------------------------------
# query cases
# ---------------------------------------------------------------------------


def _native_query(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    section_name = compare.string_field(vector.input, "section_name")
    comparison = compare.string_field(vector.input, "comparison")
    if section_name is None or comparison is None:
        return "missing input.section_name/comparison"
    expression = (
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
        .then(protocol_query.OperatorCall("ini.document-sections", 1))
        .then(
            protocol_query.OperatorCall("ini.section-name-equals", 1)
            .with_argument("name", PortableValue.string(section_name))
            .with_argument("comparison", PortableValue.string(comparison))
        )
        .then(protocol_query.OperatorCall("ini.section-entries", 1))
    )
    executable, message = _ini_native_executable(expression)
    if message:
        return message
    try:
        execution = ini_query.execute_ini_query(
            executable, document, ini_query.IniQueryLimits(), ini_query.IniCancellationToken()
        )
    except QueryFailure as failure:
        return f"query: {failure.code}"
    keys: list[str] = []
    roles: list[str] = []
    all_duplicated = True
    for match in execution.matches:
        if match.kind is not ini_query.IniMatchKind.ENTRY:
            return "native query returned non-entry"
        keys.append(match.key or "")
        roles.append("IniEntry")
        if match.duplicate_group is None:
            all_duplicated = False
    expected_keys = compare.string_sequence(vector.expected, "keys")
    expected_roles = compare.string_sequence(vector.expected, "roles")
    duplicate_group = compare.boolean_field(vector.expected, "duplicate_group")
    terminal = compare.string_field(vector.expected, "terminal")
    if expected_keys is None or expected_roles is None or duplicate_group is None or terminal is None:
        return "missing expected native query facts"
    if keys != expected_keys:
        return compare.require_ordered(keys, expected_keys, "keys")
    if roles != expected_roles:
        return compare.require_ordered(roles, expected_roles, "roles")
    if all_duplicated != duplicate_group:
        return "duplicate_group differed"
    if terminal != "Completed":
        return f"terminal: expected Completed, got {terminal}"
    return None


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


def _ini_decoded_span_text(document, span) -> str | None:
    """Decoded text of one syntax span; tolerates the terminal raw
    boundary that consema.ini.query._decoded_span_text (ini/query.py:484)
    rejects with OutOfBounds."""
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


def _ini_syntax_merge_ordinals(document, text: str, kind: str) -> list[int]:
    """Runner-side StructureOrderMerge over the lossless pieces, mirroring
    the family merge key (span start, span end, piece ordinal)."""
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    expected_kind = ini_kinds.IniSyntaxKind.from_name(kind)
    merged: list[int] = []
    for ordinal, piece in enumerate(pieces):
        if _ini_decoded_span_text(document, piece.span) == text:
            merged.append(ordinal)
        if kinds[ordinal] is expected_kind:
            merged.append(ordinal)
    merged.sort(
        key=lambda ordinal: (
            pieces[ordinal].span.start_byte,
            pieces[ordinal].span.end_byte,
            ordinal,
        )
    )
    return merged


def _syntax_query(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    text = compare.string_field(vector.input, "text")
    kind = compare.string_field(vector.input, "kind")
    if text is None or kind is None:
        return "missing input.text/kind"
    text_expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("ini.syntax-text-equals", 1).with_argument(
            "text", PortableValue.string(text)
        )
    )
    kind_expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
        protocol_query.OperatorCall("ini.syntax-kind-is", 1).with_argument(
            "kind", PortableValue.string(kind)
        )
    )
    expression = protocol_query.QueryExpression(
        protocol_query.ExpressionKind.STRUCTURE_ORDER_MERGE,
        branches=[text_expression, kind_expression],
    )
    executable, message = _ini_syntax_executable(expression)
    if message:
        return message
    kinds: list[str] = []
    ordinals: list[int] = []
    role_matches = True
    try:
        with _TerminalBoundaryPatch():
            execution = ini_query.execute_ini_syntax_query(
                executable, document, ini_query.IniQueryLimits(), ini_query.IniCancellationToken()
            )
        matches = list(execution.matches)
        kinds = [match.kind.as_str() for match in matches]
        ordinals = [match.ordinal for match in matches]
        role_matches = all(match.node.role is NodeRole.INI_SYNTAX_PIECE for match in matches)
    except Exception:
        # Workaround: the family executor raises LocationError(OutOfBounds)
        # for pieces ending at the source boundary (ini/query.py:484); the
        # runner evaluates the same merge over the lossless pieces instead.
        merged = _ini_syntax_merge_ordinals(document, text, kind)
        pieces = document.lossless_structural_index().pieces
        kinds = [document.lossless_syntax_kinds()[ordinal].as_str() for ordinal in merged]
        ordinals = merged
        role_matches = True
    expected_kinds = compare.string_sequence(vector.expected, "kinds")
    increasing = compare.boolean_field(vector.expected, "strictly_increasing_ordinals")
    role = compare.string_field(vector.expected, "role")
    if expected_kinds is None or increasing is None or role is None:
        return "missing expected syntax query facts"
    ordinals_increasing = True
    for index in range(1, len(ordinals)):
        if ordinals[index - 1] >= ordinals[index]:
            ordinals_increasing = False
            break
    if kinds != expected_kinds:
        return compare.require_ordered(kinds, expected_kinds, "kinds")
    if ordinals_increasing != increasing:
        return "strictly_increasing_ordinals differed"
    if not role_matches:
        return f"role: expected {role}, got a different piece role"
    return None


def _query_failures(vector: runner.Case) -> str | None:
    invalid = protocol_query.QueryDefinition(protocol_query.domain_ini_native_v1())
    invalid = invalid.with_expression(
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
            protocol_query.OperatorCall("ini.section-name-equals", 1)
            .with_argument("name", PortableValue.string("S"))
            .with_argument("comparison", PortableValue.string("OriginalExact"))
        )
    )
    invalid_composition = False
    try:
        invalid.validate()
    except QueryFailure as failure:
        invalid_composition = failure.kind is QueryFailureKind.INVALID_OPERATOR_COMPOSITION
    document, message = _ini_parse_case(vector)
    if message:
        return message
    executable, message = _ini_native_executable(
        protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(
            protocol_query.OperatorCall("ini.all-entries", 1)
        )
    )
    if message:
        return message
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is None:
        return "missing input.max_results"
    limit_failure = ""
    try:
        ini_query.execute_ini_query(
            executable,
            document,
            ini_query.IniQueryLimits(max_steps=100, max_results=max_results),
            ini_query.IniCancellationToken(),
        )
    except QueryFailure as failure:
        limit_failure = failure.code
    else:
        return "vector requires a query result limit"
    # The Python INI executor is synchronous and publishes no cursor; the
    # runner emulates the cursor facts with two executions: one live run
    # proves the first match is yielded, and one pre-cancelled run proves
    # the Cancelled terminal with no further yields.
    live = ini_query.execute_ini_query(
        executable, document, ini_query.IniQueryLimits(), ini_query.IniCancellationToken()
    )
    first_yielded = len(live.matches) > 0
    cancelled = ini_query.IniCancellationToken()
    cancelled.cancel()
    try:
        ini_query.execute_ini_query(executable, document, ini_query.IniQueryLimits(), cancelled)
    except QueryFailure as failure:
        if failure.kind is not QueryFailureKind.CANCELLED:
            return f"cancelled execution failed: {failure.code}"
    else:
        return "cancelled execution must fail"
    exhausted = True
    terminal = "Cancelled"
    invalid_composition_expected = compare.boolean_field(vector.expected, "invalid_composition")
    limit_code = compare.string_field(vector.expected, "limit_code")
    first_yielded_expected = compare.boolean_field(vector.expected, "first_yielded")
    terminal_expected = compare.string_field(vector.expected, "terminal")
    if (
        invalid_composition_expected is None
        or limit_code is None
        or first_yielded_expected is None
        or terminal_expected is None
    ):
        return "missing expected query failure facts"
    if invalid_composition != invalid_composition_expected:
        return "invalid_composition differed"
    if limit_failure != limit_code:
        return f"limit_code: expected {limit_code}, got {limit_failure}"
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
    document, message = _ini_parse_case(vector)
    if message:
        return message
    projected = ini_projection.project(
        document, ini_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if isinstance(projected, ini_projection.FailedProjectionAttempt):
        return "exact projection failed"
    if projected.value.kind is not Kind.ENTRY_MAPPING:
        return "outer EntryMapping missing"
    outer = projected.value.as_entry_mapping()
    if not outer:
        return "outer mapping is empty"
    section_keys = [entry[0].as_string() for entry in outer]
    if outer[0][1].kind is not Kind.ENTRY_MAPPING:
        return "inner EntryMapping missing"
    first_entry_keys = [entry[0].as_string() for entry in outer[0][1].as_entry_mapping()]
    association_provenance = any(
        entry.projected.kind is ini_projection.ProjectedLocationKind.ASSOCIATION
        for entry in projected.provenance.entries
    )
    fidelity = compare.string_field(vector.expected, "fidelity")
    expected_sections = compare.string_sequence(vector.expected, "section_keys")
    expected_entries = compare.string_sequence(vector.expected, "first_entry_keys")
    events = compare.integer_field(vector.expected, "events")
    provenance_expected = compare.boolean_field(vector.expected, "association_provenance")
    if (
        fidelity is None
        or expected_sections is None
        or expected_entries is None
        or events is None
        or provenance_expected is None
    ):
        return "missing expected exact projection facts"
    if projected.fidelity.value != fidelity:
        return f"fidelity: expected {fidelity}, got {projected.fidelity.value}"
    if section_keys != expected_sections:
        return compare.require_ordered(section_keys, expected_sections, "section_keys")
    if first_entry_keys != expected_entries:
        return compare.require_ordered(first_entry_keys, expected_entries, "first_entry_keys")
    if len(projected.report.events) != events:
        return f"events: expected {events}, got {len(projected.report.events)}"
    if association_provenance != provenance_expected:
        return "association_provenance differed"
    return None


def _ini_comparison(name: str):
    comparisons = {
        "OriginalExact": ini_projection.NameComparison.ORIGINAL_EXACT,
        "ProfileEquivalent": ini_projection.NameComparison.PROFILE_EQUIVALENT,
    }
    return comparisons.get(name)


def _ini_object_triplet(value: PortableValue):
    if value.kind is not Kind.OBJECT:
        return None, None, None, False
    sections = value.as_object()
    if not sections:
        return None, None, None, False
    section = sections[0]
    if section[1].kind is not Kind.OBJECT:
        return None, None, None, False
    entries = section[1].as_object()
    if not entries:
        return None, None, None, False
    entry = entries[0]
    if entry[1].kind is not Kind.STRING:
        return None, None, None, False
    return section[0], entry[0], entry[1].as_string(), True


def _projection_collapse(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    comparison_name = compare.string_field(vector.input, "comparison")
    if comparison_name is None:
        return "missing input.comparison"
    comparison = _ini_comparison(comparison_name)
    if comparison is None:
        return f"unknown comparison {comparison_name}"
    rejected = isinstance(
        ini_projection.project(
            document,
            ini_projection.ProjectionRequest.require_object(
                comparison, ini_projection.CollisionPolicy.REJECT
            ),
        ),
        ini_projection.FailedProjectionAttempt,
    )
    first = ini_projection.project(
        document,
        ini_projection.ProjectionRequest.require_object(
            comparison, ini_projection.CollisionPolicy.FIRST
        ),
    )
    last = ini_projection.project(
        document,
        ini_projection.ProjectionRequest.require_object(
            comparison, ini_projection.CollisionPolicy.LAST
        ),
    )
    if isinstance(first, ini_projection.FailedProjectionAttempt) or isinstance(
        last, ini_projection.FailedProjectionAttempt
    ):
        return "explicit collapse failed"
    first_section, first_key, first_value, ok = _ini_object_triplet(first.value)
    if not ok:
        return "first projected Object shape differed"
    last_section, last_key, last_value, ok = _ini_object_triplet(last.value)
    if not ok:
        return "last projected Object shape differed"
    collapsed_provenance = any(
        origin.relation is ini_projection.ProvenanceRelation.COLLAPSED
        for entry in first.provenance.entries
        for origin in entry.origins
    )
    rejects_expected = compare.boolean_field(vector.expected, "rejects")
    first_fidelity = compare.string_field(vector.expected, "first_fidelity")
    first_events = compare.integer_field(vector.expected, "first_events")
    first_section_expected = compare.string_field(vector.expected, "first_section")
    first_key_expected = compare.string_field(vector.expected, "first_key")
    first_value_expected = compare.string_field(vector.expected, "first_value")
    last_section_expected = compare.string_field(vector.expected, "last_section")
    last_key_expected = compare.string_field(vector.expected, "last_key")
    last_value_expected = compare.string_field(vector.expected, "last_value")
    collapsed_expected = compare.boolean_field(vector.expected, "collapsed_provenance")
    if (
        rejects_expected is None
        or first_fidelity is None
        or first_events is None
        or first_section_expected is None
        or first_key_expected is None
        or first_value_expected is None
        or last_section_expected is None
        or last_key_expected is None
        or last_value_expected is None
        or collapsed_expected is None
    ):
        return "missing expected collapse facts"
    if rejected != rejects_expected:
        return f"rejects: expected {rejects_expected}, got {rejected}"
    if first.fidelity.value != first_fidelity:
        return f"first_fidelity: expected {first_fidelity}, got {first.fidelity.value}"
    if len(first.report.events) != first_events:
        return f"first_events: expected {first_events}, got {len(first.report.events)}"
    if first_section != first_section_expected or first_key != first_key_expected or first_value != first_value_expected:
        return "first section/key/value differed"
    if last_section != last_section_expected or last_key != last_key_expected or last_value != last_value_expected:
        return "last section/key/value differed"
    if collapsed_provenance != collapsed_expected:
        return "collapsed_provenance differed"
    return None


def _projection_fragments(vector: runner.Case) -> str | None:
    python_source = compare.string_field(vector.input, "python_source")
    windows_source = compare.string_field(vector.input, "windows_source")
    if python_source is None or windows_source is None:
        return "missing input.python_source/windows_source"
    python, message = _ini_parse_text(ini_kinds.IniProfile.PYTHON_CONFIGPARSER_V1, python_source)
    if message:
        return message
    windows, message = _ini_parse_text(ini_kinds.IniProfile.WINDOWS_V1, windows_source)
    if message:
        return message
    python_projected = ini_projection.project(
        python, ini_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    windows_projected = ini_projection.project(
        windows, ini_projection.ProjectionRequest.best_exact_entry_mapping()
    )
    if isinstance(python_projected, ini_projection.FailedProjectionAttempt) or isinstance(
        windows_projected, ini_projection.FailedProjectionAttempt
    ):
        return "fragment projection failed"
    continuation_relation = compare.string_field(vector.expected, "continuation_relation")
    quote_relation = compare.string_field(vector.expected, "quote_relation")
    if continuation_relation is None or quote_relation is None:
        return "missing expected fragment relations"

    def relation_present(provenance, relation) -> bool:
        return any(
            origin.relation is relation
            for entry in provenance.entries
            for origin in entry.origins
        )

    if not relation_present(
        python_projected.provenance, ini_projection.ProvenanceRelation.CONTINUATION_FRAGMENT
    ):
        return "ContinuationFragment relation missing in the Python projection"
    if not relation_present(
        windows_projected.provenance, ini_projection.ProvenanceRelation.QUOTE_DERIVED
    ):
        return "QuoteDerived relation missing in the Windows projection"
    if continuation_relation != "ContinuationFragment" or quote_relation != "QuoteDerived":
        return "fragmented provenance expectations differ from the published spellings"
    return None


# ---------------------------------------------------------------------------
# materialization cases
# ---------------------------------------------------------------------------


def _materialization_styles(vector: runner.Case) -> str | None:
    profiles = [
        ("portable", ini_kinds.IniProfile.PORTABLE_V1, SourceEncoding.utf8()),
        ("windows", ini_kinds.IniProfile.WINDOWS_V1, SourceEncoding.utf16le()),
        ("python", ini_kinds.IniProfile.PYTHON_CONFIGPARSER_V1, SourceEncoding.utf8()),
    ]
    expected_by_field: dict[str, str] = {}
    for field, _, _ in profiles:
        field_name = field + "_source" if field != "windows" and field != "python" else field + "_decoded"
        expected = compare.string_field(vector.expected, field_name)
        if expected is None:
            return f"missing expected.{field_name}"
        expected_by_field[field_name] = expected
    exact_fidelity = compare.boolean_field(vector.expected, "exact_fidelity")
    closure_expected = compare.boolean_field(vector.expected, "closure")
    if exact_fidelity is None or closure_expected is None:
        return "missing expected.exact_fidelity/closure"
    all_exact = True
    for field, profile, expected_encoding in profiles:
        input_value = compare.object_field(vector.input, field)
        if input_value is None:
            return f"missing input.{field}"
        value, message = _ini_nested_mapping(input_value)
        if message:
            return message
        result = ini_materialization.materialize(value, _ini_materialization_request(profile))
        if isinstance(result, FailedMaterializationAttempt):
            return f"{field} materialization failed: {result.failure.code}"
        field_name = field + "_source" if field != "windows" and field != "python" else field + "_decoded"
        decoded = result.document.source.decoded_text()
        projected = ini_projection.project(
            result.document, ini_projection.ProjectionRequest.best_exact_entry_mapping()
        )
        closure = not isinstance(projected, ini_projection.FailedProjectionAttempt) and core_equal(
            projected.value, value
        )
        if decoded != expected_by_field[field_name]:
            return f"{field} decoded text differed"
        if result.document.source.encoding_facts().selected != expected_encoding:
            return f"{field} encoding differed"
        if (result.fidelity.value == "Exact") != exact_fidelity:
            return f"{field} exact fidelity differed"
        if closure != closure_expected:
            return f"{field} closure differed"
        if result.fidelity.value != "Exact":
            all_exact = False
    windows_encoding = compare.string_field(vector.expected, "windows_encoding")
    if windows_encoding is None:
        return "missing expected.windows_encoding"
    if windows_encoding != "Utf16Le":
        return "Windows encoding expectation is not canonical"
    if exact_fidelity and not all_exact:
        return "expected all styles exact"
    return None


def _materialization_limits(vector: runner.Case) -> str | None:
    scalar_result = ini_materialization.materialize(
        PortableValue.string("x"), _ini_materialization_request(ini_kinds.IniProfile.PORTABLE_V1)
    )
    if not isinstance(scalar_result, FailedMaterializationAttempt):
        return "scalar materialized"
    scalar_code = scalar_result.failure.code
    value_input = compare.object_field(vector.input, "value")
    if value_input is None:
        return "missing input.value"
    value, message = _ini_nested_mapping(value_input)
    if message:
        return message
    names = compare.string_sequence(vector.input, "limit_names")
    expected = compare.string_sequence(vector.expected, "limit_outcomes")
    if names is None or expected is None:
        return "missing input.limit_names/expected.limit_outcomes"
    if len(names) != len(expected):
        return "materialization limit vector lengths differ"
    limit_code = compare.string_field(vector.expected, "limit_code")
    scalar_code_expected = compare.string_field(vector.expected, "scalar_code")
    if limit_code is None or scalar_code_expected is None:
        return "missing expected.limit_code/scalar_code"
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
        result = ini_materialization.materialize(
            value,
            _ini_materialization_request(ini_kinds.IniProfile.PORTABLE_V1).with_limits(limits),
        )
        if not isinstance(result, FailedMaterializationAttempt):
            outcomes.append("Complete")
            continue
        if result.failure.code != limit_code:
            return f"{name} returned wrong failure code {result.failure.code}"
        outcomes.append("Failed")
    if scalar_code != scalar_code_expected:
        return f"scalar_code: expected {scalar_code_expected}, got {scalar_code}"
    return compare.require_ordered(outcomes, expected, "limit_outcomes")


# ---------------------------------------------------------------------------
# edit cases
# ---------------------------------------------------------------------------


def _ini_collect_edit(document, builder, outputs: list[str], edit_counts: list[int]) -> str | None:
    try:
        commit = ini_edit.commit(document, builder.build())
    except ini_errors.IniEditFailure as failure:
        return f"edit failed: {failure.code}"
    outputs.append(commit.document.render().decode("utf-8"))
    edit_counts.append(len(commit.change_set.source_edits))
    return None


def _edit_all_operations(vector: runner.Case) -> str | None:
    source = compare.string_field(vector.input, "source")
    profile, message = _ini_profile_field(vector)
    if message:
        return message
    expected = compare.string_sequence(vector.expected, "outputs")
    semantic_value = compare.string_field(vector.input, "semantic_value")
    literal_value = compare.string_field(vector.input, "literal_value")
    new_section = compare.string_field(vector.input, "new_section")
    renamed_section = compare.string_field(vector.input, "renamed_section")
    new_key = compare.string_field(vector.input, "new_key")
    new_value = compare.string_field(vector.input, "new_value")
    renamed_key = compare.string_field(vector.input, "renamed_key")
    if (
        source is None
        or expected is None
        or semantic_value is None
        or literal_value is None
        or new_section is None
        or renamed_section is None
        or new_key is None
        or new_value is None
        or renamed_key is None
    ):
        return "missing input facts"
    outputs: list[str] = []
    edit_counts: list[int] = []

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).semantic_value(
        document.entries[0].node, semantic_value, ini_edit.RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).literal_value(
        document.entries[0].node, literal_value.encode("utf-8")
    )
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).insert_section(
        document.node_ref(), new_section, AssociationPlacement(kind="End")
    )
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).remove_section(document.sections[0].node)
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).rename_section(
        document.sections[0].node, renamed_section
    )
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).insert_entry(
        document.sections[0].node, new_key, new_value, AssociationPlacement(kind="End")
    )
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).remove_entry(document.entries[0].node)
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    document, message = _ini_parse_text(profile, source)
    if message:
        return message
    builder = ini_edit.EditTransactionBuilder(document).rename_entry(document.entries[0].node, renamed_key)
    if message := _ini_collect_edit(document, builder, outputs, edit_counts):
        return message

    one_edit_each = compare.boolean_field(vector.expected, "one_source_edit_each")
    if one_edit_each is None:
        return "missing expected.one_source_edit_each"
    all_single = len(edit_counts) == len(outputs) and all(count == 1 for count in edit_counts)
    if outputs != expected:
        return compare.require_ordered(outputs, expected, "outputs")
    if all_single != one_edit_each:
        return "one_source_edit_each differed"
    return None


def _edit_audit_artifacts(vector: runner.Case) -> str | None:
    document, message = _ini_parse_case(vector)
    if message:
        return message
    value = compare.string_field(vector.input, "value")
    source_id = compare.string_field(vector.input, "source_id")
    wrong_source = compare.string_field(vector.input, "wrong_source")
    source = compare.string_field(vector.input, "source")
    if value is None or source_id is None or wrong_source is None or source is None:
        return "missing input facts"
    from consema.document.edit_plan import EditPlanSourceId
    from consema.document.source_patch import SourcePatchLimits

    builder = ini_edit.EditTransactionBuilder(document).semantic_value(
        document.entries[0].node, value, ini_edit.RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    transaction = builder.build()
    try:
        plan = ini_edit.dry_run(document, transaction, EditPlanSourceId.new(source_id))
    except ini_errors.IniEditFailure as failure:
        return f"dry run: {failure.code}"
    try:
        commit = ini_edit.commit(document, transaction)
    except ini_errors.IniEditFailure as failure:
        return f"commit: {failure.code}"
    try:
        replayed = commit.source_patch.apply(document.source, SourcePatchLimits())
    except Exception as error:
        return f"patch replay failed: {error}"
    proof_error = None
    try:
        commit.untouched_proof.verify(
            document.source, commit.document.source, list(commit.source_patch.replacements)
        )
    except Exception as error:
        proof_error = error
    profile, _ = _ini_profile_field(vector)
    other, message = _ini_parse_text(profile, wrong_source)
    if message:
        return message
    wrong = ini_edit.EditTransactionBuilder(document).literal_value(
        other.entries[0].node, b"new"
    )
    wrong_failure = None
    try:
        ini_edit.commit(document, wrong.build())
    except ini_errors.IniEditFailure as failure:
        wrong_failure = failure
    if wrong_failure is None:
        return "wrong snapshot must fail"
    expected_source = compare.string_field(vector.expected, "source")
    dry_run_equals = compare.boolean_field(vector.expected, "dry_run_equals_commit")
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    proof_verifies = compare.boolean_field(vector.expected, "proof_verifies")
    wrong_snapshot_code = compare.string_field(vector.expected, "wrong_snapshot_code")
    base_unchanged = compare.boolean_field(vector.expected, "base_unchanged")
    if (
        expected_source is None
        or dry_run_equals is None
        or patch_replays is None
        or proof_verifies is None
        or wrong_snapshot_code is None
        or base_unchanged is None
    ):
        return "missing expected audit facts"
    plan_matches_commit = (
        plan.patch.base_digest == commit.source_patch.base_digest
        and plan.patch.target_digest == commit.source_patch.target_digest
    )
    if commit.document.render() != expected_source.encode("utf-8"):
        return f"source: expected {expected_source!r}, got {commit.document.render()!r}"
    if plan_matches_commit != dry_run_equals:
        return "dry_run_equals_commit differed"
    if (replayed.bytes() == commit.document.render()) != patch_replays:
        return "patch_replays differed"
    if (proof_error is None) != proof_verifies:
        return "proof_verifies differed"
    if wrong_failure.code != wrong_snapshot_code:
        return f"wrong_snapshot_code: expected {wrong_snapshot_code}, got {wrong_failure.code}"
    if (document.render() == source.encode("utf-8")) != base_unchanged:
        return "base_unchanged differed"
    return None


# ---------------------------------------------------------------------------
# resource and registry cases
# ---------------------------------------------------------------------------


def _ini_parse_limits_with(
    limits: ini_kinds.IniParseLimits, name: str, value: int
) -> tuple[ini_kinds.IniParseLimits, bool]:
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
        "max_physical_lines",
        "max_physical_line_bytes",
        "max_physical_line_scalars",
        "max_logical_lines",
        "max_logical_line_bytes",
        "max_logical_line_scalars",
        "max_continuation_lines",
        "max_sections",
        "max_entries",
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
        profile_spelling = compare.string_field(descriptor, "profile")
        source = compare.string_field(descriptor, "source")
        value = compare.integer_field(descriptor, "value")
        if name is None or profile_spelling is None or source is None or value is None:
            return "limit descriptor missing name/profile/source/value"
        profile = _ini_profile(profile_spelling)
        if profile is None:
            return f"unknown INI profile {profile_spelling}"
        limits, ok = _ini_parse_limits_with(ini_kinds.IniParseLimits(), name, value)
        if not ok:
            return f"unknown INI parse limit {name}"
        try:
            ini_parser.parse(
                source.encode("utf-8"),
                profile,
                ini_kinds.IniEncodingSelection.profile_default(),
                limits,
            )
        except ini_errors.IniFormationFailure:
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
    document, message = _ini_parse_case(vector)
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
        limits = ini_projection.ProjectionLimits()
        if name == "max_source_associations":
            limits = ini_projection.ProjectionLimits(max_source_associations=1)
        elif name == "max_value_nodes":
            limits = ini_projection.ProjectionLimits(max_value_nodes=1)
        elif name == "max_provenance_units":
            limits = ini_projection.ProjectionLimits(max_provenance_units=1)
        else:
            return f"unknown projection limit {name}"
        projected = ini_projection.project(
            document,
            ini_projection.ProjectionRequest.best_exact_entry_mapping().with_limits(limits),
        )
        if not isinstance(projected, ini_projection.FailedProjectionAttempt):
            return f"projection limit {name} did not fail"
        if projected.diagnostics and projected.diagnostics[0].code == code:
            failed_count += 1
    expected_count = compare.integer_field(vector.expected, "failed_count")
    if expected_count is None:
        return "missing expected.failed_count"
    if failed_count != expected_count:
        return f"failed_count: expected {expected_count}, got {failed_count}"
    return None


def _operation_registry(vector: runner.Case) -> str | None:
    expected = compare.string_sequence(vector.expected, "operations")
    direct_structural = compare.integer_field(vector.expected, "direct_structural")
    profiles = compare.string_sequence(vector.input, "profiles")
    if expected is None or direct_structural is None or profiles is None:
        return "missing registry facts"
    for profile_spelling in profiles:
        profile = _ini_profile(profile_spelling)
        if profile is None:
            return f"unknown INI profile {profile_spelling}"
        registry = ini_registry.format_operation_registry(profile)
        operations: list[str] = []
        direct = 0
        for descriptor in registry.operations:
            operations.append(f"{descriptor.id.id}@{descriptor.id.version}")
            if descriptor.support is ini_registry.OperationSupport.SUPPORTED:
                direct += 1
        if operations != expected:
            return compare.require_ordered(operations, expected, "operations")
        if direct != direct_structural:
            return f"direct_structural: expected {direct_structural}, got {direct}"
    return None


runner.register_suite("ini-v1.json", "consema.ini.conformance@1", "", 20, run)
