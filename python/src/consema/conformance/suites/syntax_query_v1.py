"""Suite ``consema.syntax-query.conformance@1`` (syntax-query-v1.json, 19
cases): JSON and TOML lossless-syntax queries plus the ordered-cursor
terminal semantics. Dispatch is by case id, mirroring
go/conformance/syntax_query_v1.go, the JSON face v1_json_face.go
(runSyntaxQueryJSONCase / syntaxQueryDefinition), the TOML face
v1_toml_face.go (RunSyntaxQueryTomlFace), and the cursor face g43_faces.go
(RunSyntaxCursorFace).
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.core.value import PortableValue
from consema.document.limits import ParseLimits
from consema.json import errors as json_errors
from consema.json import kinds as json_kinds
from consema.json import parser as json_parser
from consema.json import query as json_query
from consema.protocol import query as protocol_query
from consema.protocol.query import (
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.toml import document as toml_document
from consema.toml import errors as toml_errors
from consema.toml import parser as toml_parser
from consema.toml import query as toml_query


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    for vector in data.cases:
        if vector.id.startswith("syntax.json."):
            message = _syntax_json_case(vector)
        elif vector.id.startswith("syntax.toml."):
            message = _syntax_toml_case(vector)
        elif vector.id.startswith("syntax.cursor."):
            message = _syntax_cursor_case(vector)
        else:
            message = "runner does not recognize published syntax-query case"
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# shared definition building
# ---------------------------------------------------------------------------


def _syntax_expression(vector: runner.Case, format: str) -> QueryExpression:
    """Builds the branch expression from the vector filters
    (go/conformance/v1_json_face.go syntaxQueryDefinition)."""
    filter_values = compare.sequence_field(vector.input, "filters")
    if filter_values is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator="vector", argument="filters"
        )
    branches = []
    for filter_value in filter_values:
        operator = compare.string_field(filter_value, "operator")
        if operator is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT, operator="vector", argument="operator"
            )
        argument = compare.string_field(filter_value, "argument")
        if operator == "kind-is":
            if argument is None:
                raise QueryFailure(
                    QueryFailureKind.INVALID_ARGUMENT,
                    operator=operator,
                    argument="argument",
                )
            call = OperatorCall(f"{format}.syntax-kind-is", 1).with_argument(
                "kind", PortableValue.string(argument)
            )
        elif operator == "text-equals":
            if argument is None:
                raise QueryFailure(
                    QueryFailureKind.INVALID_ARGUMENT,
                    operator=operator,
                    argument="argument",
                )
            call = OperatorCall(f"{format}.syntax-text-equals", 1).with_argument(
                "text", PortableValue.string(argument)
            )
        elif operator == "take":
            if argument is None:
                raise QueryFailure(
                    QueryFailureKind.INVALID_ARGUMENT,
                    operator=operator,
                    argument="argument",
                )
            call = OperatorCall("core.take", 1).with_argument(
                "count", PortableValue.integer(int(argument))
            )
        elif operator == "distinct-by-identity":
            call = OperatorCall("core.distinct-by-identity", 1)
        else:
            call = OperatorCall(operator, 1)
        branches.append(QueryExpression(ExpressionKind.INPUT).then(call))
    combine = compare.string_field(vector.input, "combine") or ""
    if combine in ("Single", ""):
        if not branches:
            expression = QueryExpression(ExpressionKind.INPUT)
        elif len(branches) == 1:
            expression = branches[0]
        else:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT, operator="vector", argument="combine"
            )
    elif combine == "StructureOrderMerge":
        expression = QueryExpression(ExpressionKind.STRUCTURE_ORDER_MERGE, branches=branches)
    elif combine == "Concat":
        expression = QueryExpression(ExpressionKind.CONCAT, branches=branches)
    else:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator="vector", argument="combine"
        )
    return expression


def _syntax_selection(vector: runner.Case) -> QuerySelection:
    selection = compare.string_field(vector.input, "selection") or ""
    choices = {
        "": QuerySelection.ALL,
        "All": QuerySelection.ALL,
        "First": QuerySelection.FIRST,
        "Last": QuerySelection.LAST,
    }
    if selection not in choices:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator="vector", argument="selection"
        )
    return choices[selection]


def _ordered_results_capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _syntax_executable(vector: runner.Case, domain, format: str):
    """Builds the validated, capability-bound executable from the vector."""
    expression = _syntax_expression(vector, format)
    selection = _syntax_selection(vector)
    return (
        QueryDefinition(domain)
        .with_expression(expression)
        .with_selection(selection)
        .validate()
        .bind(_ordered_results_capabilities())
    )


def _expect_query_code(vector: runner.Case, actual_code: str) -> str | None:
    expected = compare.string_field(vector.expected, "code")
    if expected is None:
        return "missing expected.code"
    return compare.require_equal(actual_code, expected, "failure code")


# ---------------------------------------------------------------------------
# JSON face (syntax.json.*)
# ---------------------------------------------------------------------------


def _syntax_json_case(vector: runner.Case) -> str | None:
    profile_name = compare.string_field(vector.input, "profile")
    if profile_name is None:
        return "missing input.profile"
    profile = {
        "json.strict@1": json_kinds.JsonProfile.STRICT_V1,
        "jsonc.bounded@1": json_kinds.JsonProfile.JSONC_BOUNDED_V1,
    }.get(profile_name)
    if profile is None:
        return f"unknown JSON profile {profile_name}"
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    try:
        document = json_parser.parse(source.encode("utf-8"), profile, ParseLimits())
    except json_errors.JsonFormationFailure as error:
        return f"formation failed: {error.code}"
    try:
        executable = _syntax_executable(
            vector, protocol_query.domain_json_lossless_syntax_v1(), "json"
        )
    except QueryFailure as failure:
        return _expect_query_code(vector, failure.code)
    token = json_query.JsonCancellationToken()
    if compare.boolean_field(vector.input, "cancelled") is True:
        token.cancel()
    limits = json_query.JsonQueryLimits()
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is not None:
        limits.max_results = max_results
    try:
        execution = json_query.execute_json_syntax_query(executable, document, limits, token)
    except QueryFailure as failure:
        return _expect_query_code(vector, failure.code)
    return _compare_json_matches(vector, document, list(execution.matches))


def _compare_json_matches(vector: runner.Case, document, matches) -> str | None:
    expected_values = compare.sequence_field(vector.expected, "matches")
    if expected_values is None:
        return "missing expected.matches"
    if len(matches) != len(expected_values):
        return f"match count differs: actual {len(matches)}, expected {len(expected_values)}"
    raw = document.source.bytes()
    for index, (match, expected_value) in enumerate(zip(matches, expected_values)):
        message = _compare_one_match(match, expected_value, raw, index, "JsonSyntaxPiece")
        if message:
            return message
    return None


# ---------------------------------------------------------------------------
# TOML face (syntax.toml.*)
# ---------------------------------------------------------------------------


def _syntax_toml_case(vector: runner.Case) -> str | None:
    if compare.string_field(vector.input, "profile") != "toml.1.0@1":
        return "unknown TOML profile"
    source = compare.string_field(vector.input, "source")
    if source is None:
        return "missing input.source"
    try:
        document = toml_parser.parse(
            source.encode("utf-8"), toml_document.TomlProfile.TOML10_V1, ParseLimits()
        )
    except toml_errors.TomlFormationFailure as error:
        return f"formation failed: {error.code}"
    try:
        executable = _syntax_executable(
            vector, protocol_query.domain_toml_lossless_syntax_v1(), "toml"
        )
    except QueryFailure as failure:
        return _expect_query_code(vector, failure.code)
    token = toml_query.CancellationToken()
    if compare.boolean_field(vector.input, "cancelled") is True:
        token.cancel()
    limits = toml_query.QueryLimits()
    max_steps = compare.integer_field(vector.input, "max_steps")
    if max_steps is not None:
        limits.max_steps = max_steps
    max_results = compare.integer_field(vector.input, "max_results")
    if max_results is not None:
        limits.max_results = max_results
    try:
        matches = toml_query.execute_toml_syntax_query(executable, document, limits, token)
    except QueryFailure as failure:
        return _expect_query_code(vector, failure.code)
    if compare.string_field(vector.expected, "terminal") != "Completed":
        return "expected terminal differs"
    return _compare_toml_matches(vector, document, matches)


def _compare_toml_matches(vector: runner.Case, document, matches) -> str | None:
    expected_values = compare.sequence_field(vector.expected, "matches")
    if expected_values is None:
        return "missing expected.matches"
    if len(matches) != len(expected_values):
        return f"match count differs: actual {len(matches)}, expected {len(expected_values)}"
    raw = document.render()
    for index, (match, expected_value) in enumerate(zip(matches, expected_values)):
        message = _compare_one_match(match, expected_value, raw, index, "TomlSyntaxPiece")
        if message:
            return message
    return None


def _compare_one_match(match, expected_value, raw: bytes, index: int, role: str) -> str | None:
    """One expected syntax match fact: kind, text, ordinal, role."""
    kind = compare.string_field(expected_value, "kind")
    text = compare.string_field(expected_value, "text")
    ordinal = compare.integer_field(expected_value, "ordinal")
    role_value = compare.string_field(expected_value, "role")
    if kind is None or text is None or ordinal is None or role_value is None:
        return f"expected match {index} must carry kind/text/ordinal/role"
    if role_value != role:
        return f"expected match {index} role must be {role}"
    actual_text = raw[match.span.start_byte : match.span.end_byte].decode("utf-8")
    message = compare.require_equal(match.kind.as_str(), kind, f"match[{index}].kind")
    if message:
        return message
    message = compare.require_equal(actual_text, text, f"match[{index}].text")
    if message:
        return message
    return compare.require_equal(match.ordinal, ordinal, f"match[{index}].ordinal")


# ---------------------------------------------------------------------------
# cursor terminal face (syntax.cursor.*)
# ---------------------------------------------------------------------------


def _syntax_cursor_case(vector: runner.Case) -> str | None:
    """The ordered cursor terminal semantics over the vector values
    (go/conformance/g43_faces.go RunSyntaxCursorFace): Completed after
    exhaustion, Cancelled when cancellation pre-empts the stream, Failed for
    a declared failing stream."""
    values = compare.integer_sequence(vector.input, "values")
    if values is None:
        return "missing input.values"
    mode = compare.string_field(vector.input, "mode")
    if mode is None:
        return "missing input.mode"
    position = 0
    yielded = 0
    terminal = None
    cancelled = False

    def advance():
        nonlocal position, terminal, cancelled
        if terminal is not None or cancelled:
            return None
        if position < len(values):
            value = values[position]
            position += 1
            return value
        return None

    if mode == "Completed":
        while advance() is not None:
            if terminal is not None:
                return "terminal state set before exhaustion"
            yielded += 1
        terminal = "Completed"
    elif mode == "Cancelled":
        if advance() is not None:
            if terminal is not None:
                return "terminal state set before cancellation"
            yielded += 1
        cancelled = True
        if advance() is not None:
            return "cancelled cursor must stop yielding"
        terminal = "Cancelled"
    elif mode == "Failed":
        while advance() is not None:
            if terminal is not None:
                return "terminal state set before exhaustion"
            yielded += 1
        terminal = "Failed"
    else:
        return f"unknown cursor mode {mode}"
    expected_yielded = compare.integer_field(vector.expected, "yielded")
    expected_terminal = compare.string_field(vector.expected, "terminal")
    if expected_yielded is None or expected_terminal is None:
        return "missing expected facts"
    message = compare.require_equal(yielded, expected_yielded, "yielded")
    if message:
        return message
    return compare.require_equal(terminal, expected_terminal, "terminal")


runner.register_suite("syntax-query-v1.json", "consema.syntax-query.conformance@1", "", 19, run)
