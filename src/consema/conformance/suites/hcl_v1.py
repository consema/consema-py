"""Suite ``consema.hcl.conformance@1`` (hcl-v1.json, 57 cases): HCL native
and tfvars formation with recovery and fatal limits, native and lossless
syntax query, body projection with the ProjectExpression policy, canonical
materialization with the reparse closure, and the six structural edits.
Dispatch is by the ``capability`` field, mirroring
go/conformance/hcl_v1.go.
"""

from __future__ import annotations

import struct
from dataclasses import replace

from consema.conformance import compare
from consema.conformance import runner
from consema.core.value import Kind, PortableValue
from consema.document.ids import MaterializationStyleId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationRequest,
)
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import FormationStatus
from consema.hcl import edit as hcl_edit
from consema.hcl import expression as hcl_expression
from consema.hcl import materialization as hcl_materialization
from consema.hcl import projection as hcl_projection
from consema.hcl import query as hcl_query
from consema.hcl.document import HclDocument, HclEncodingSelection, parse
from consema.hcl.edit import BodyPath, BodyPlacement, EditKey, EditValue
from consema.hcl.errors import HclEditFailure, HclFormationFailure
from consema.hcl.kinds import HclProfile
from consema.hcl.limits import HclParseLimits
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    for vector in data.cases:
        capability = vector.capability
        if capability in ("hcl.native-formation@1", "hcl.tfvars-formation@1", "hcl.limit@1"):
            message = _formation_case(vector)
        elif capability == "hcl.query@1":
            message = _query_case(vector)
        elif capability == "hcl.projection@1":
            message = _projection_case(vector)
        elif capability == "hcl.materialization@1":
            message = _materialization_case(vector)
        elif capability == "hcl.edit@1":
            message = _edit_case(vector)
        else:
            message = "unknown capability " + capability
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# Shared formation
# ---------------------------------------------------------------------------


def _profile(name: str) -> HclProfile | None:
    if name == "hcl.native@1":
        return HclProfile.NATIVE_V1
    if name == "hcl.tfvars@1":
        return HclProfile.TFVARS_V1
    return None


def _parse_limits(vector: runner.Case) -> HclParseLimits:
    limits = HclParseLimits()
    overrides = compare.object_field(vector.input, "limits")
    if overrides is None:
        return limits
    common = compare.object_field(overrides, "common")
    if common is not None:
        common_fields = {}
        for name in (
            "max_source_bytes",
            "max_nesting_depth",
            "max_token_count",
            "max_node_count",
            "max_diagnostics",
        ):
            value = compare.integer_field(common, name)
            if value is not None:
                common_fields[name] = value
        if common_fields:
            limits = replace(limits, common=replace(ParseLimits(), **common_fields))
    fields = {}
    for name in (
        "max_body_depth",
        "max_expression_depth",
        "max_template_depth",
        "max_attribute_count",
        "max_block_count",
        "max_label_count",
        "max_body_item_count",
        "max_identifier_len",
        "max_string_len",
        "max_number_digits",
        "max_template_len",
        "max_heredoc_lines",
        "max_heredoc_bytes",
        "max_tuple_elements",
        "max_object_entries",
        "max_for_extent",
        "max_recovery_regions",
        "max_error_regions",
        "max_syntax_pieces",
    ):
        value = compare.integer_field(overrides, name)
        if value is not None:
            fields[name] = value
    if fields:
        limits = replace(limits, **fields)
    return limits


class _Formed:
    """One formation outcome: a formed document or a fatal failure."""

    __slots__ = ("document", "failure")

    def __init__(self, document=None, failure=None):
        self.document = document
        self.failure = failure

    def status_name(self) -> str:
        if self.failure is not None:
            return "FatalFormationFailure"
        return self.document.formation_status().value

    def has_code(self, code: str) -> bool:
        if self.failure is not None:
            return self.failure.code == code
        return any(item.code == code for item in self.document.diagnostic_records())

    def document_or_fail(self):
        if self.failure is not None:
            return None, "formation failed"
        return self.document, ""


def _form_value(vector: runner.Case):
    profile_name = compare.string_field(vector.input, "profile")
    profile = _profile(profile_name or "")
    if profile is None:
        return None, "missing or unknown profile"
    hex_text = compare.string_field(vector.input, "hex")
    if hex_text is not None:
        try:
            raw = bytes.fromhex(hex_text)
        except ValueError:
            return None, "invalid hex"
    else:
        source = compare.string_field(vector.input, "source")
        if source is None:
            return None, "missing input.source"
        raw = source.encode("utf-8")
    try:
        document = parse(raw, profile, HclEncodingSelection.PROFILE_DEFAULT, _parse_limits(vector))
    except HclFormationFailure as failure:
        return _Formed(failure=failure), ""
    return _Formed(document=document), ""


def _form_sample(vector: runner.Case, sample):
    profile_value = compare.string_field(sample, "profile")
    if profile_value is not None:
        profile = _profile(profile_value)
        if profile is None:
            return None, "unknown sample profile"
    else:
        profile_name = compare.string_field(vector.input, "profile")
        profile = _profile(profile_name or "")
        if profile is None:
            return None, "missing profile"
    hex_text = compare.string_field(sample, "hex")
    if hex_text is not None:
        try:
            raw = bytes.fromhex(hex_text)
        except ValueError:
            return None, "invalid sample hex"
    else:
        source = compare.string_field(sample, "source")
        if source is None:
            source = compare.string_field(vector.input, "source")
            if source is None:
                return None, "missing sample source"
        raw = source.encode("utf-8")
    try:
        document = parse(raw, profile, HclEncodingSelection.PROFILE_DEFAULT, _parse_limits(vector))
    except HclFormationFailure as failure:
        return _Formed(failure=failure), ""
    return _Formed(document=document), ""


def _decimal_to_f64(text: str) -> float | None:
    """Converts one exact canonical decimal spelling to its double value."""
    negative = False
    magnitude = text
    if magnitude.startswith("-"):
        negative = True
        magnitude = magnitude[1:]
    exponent = 0
    digits = magnitude
    if "." in magnitude:
        integer_part, fraction_part = magnitude.split(".", 1)
        digits = integer_part + fraction_part
        exponent = -len(fraction_part)
    try:
        coefficient = int(digits)
    except ValueError:
        return None
    value = float(coefficient) * (10.0 ** exponent)
    return -value if negative else value


def _expected_f64(value) -> float | None:
    kind = value.kind
    if kind is Kind.BINARY_FLOAT64:
        return struct.unpack(">d", struct.pack(">Q", value.as_binary_float64()))[0]
    if kind is Kind.BINARY_FLOAT32:
        return float(struct.unpack(">f", struct.pack(">I", value.as_binary_float32()))[0])
    if kind is Kind.DECIMAL:
        decimal = value.as_decimal()
        return float(decimal.coefficient) * (10.0 ** decimal.exponent)
    if kind is Kind.INTEGER:
        return float(value.as_integer())
    return None


def _bits_equal(left: float, right: float) -> bool:
    return struct.pack(">d", left).hex() == struct.pack(">d", right).hex()


def _formation_case(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _formation_samples(vector, samples)
    formed, message = _form_value(vector)
    if message:
        return message
    message = _assert_expected_status(vector, formed)
    if message:
        return message
    if formed.status_name() == "Complete":
        document, _ = formed.document_or_fail()
        render = compare.string_field(vector.expected, "render")
        if render is not None and document.render() != render.encode("utf-8"):
            return f"render {document.render()!r} != {render!r}"
    return None


def _assert_expected_status(vector: runner.Case, formed) -> str | None:
    status = compare.string_field(vector.expected, "status")
    if status is not None and formed.status_name() != status:
        return f"status {formed.status_name()} != {status}"
    diagnostic = compare.string_field(vector.expected, "diagnostic")
    if diagnostic is not None and not formed.has_code(diagnostic):
        return f"diagnostic {diagnostic} not found"
    return None


def _assert_canonical_value(document: HclDocument, expected) -> str | None:
    items = document.root_body().items
    if not items:
        return "no attribute to canonicalize"
    attribute = items[0].as_attribute()
    if attribute is None:
        return "no attribute to canonicalize"
    try:
        literal = hcl_expression.literal_value(attribute.expression)
    except hcl_expression.NonLiteralExpression:
        return "expression is not literal-complete"
    if literal.kind == "integer":
        try:
            actual = int(literal.text)
        except ValueError:
            return "integer canonical value is not numeric"
        if expected.kind is not Kind.INTEGER:
            return "expected an integer canonical value"
        if actual != expected.as_integer():
            return "integer canonical value mismatch"
        return None
    if literal.kind == "real":
        actual = _decimal_to_f64(literal.text)
        if actual is None:
            return "canonical is not numeric"
        expected_value = _expected_f64(expected)
        if expected_value is None:
            return "expected a real canonical value"
        if not _bits_equal(actual, expected_value):
            return "real canonical value mismatch"
        return None
    return "unexpected literal kind"


def _formation_samples(vector: runner.Case, samples) -> str | None:
    statuses = compare.sequence_field(vector.expected, "statuses")
    diagnostics = compare.sequence_field(vector.expected, "diagnostics")
    if statuses is None or diagnostics is None:
        return "missing expected.statuses/diagnostics"
    if len(samples) != len(statuses) or len(samples) != len(diagnostics):
        return "status/diagnostic count mismatch"
    canonical_values = compare.sequence_field(vector.expected, "canonical_values")
    proven_names = compare.sequence_field(vector.expected, "proven_attribute_names")
    for index, sample in enumerate(samples):
        formed, message = _form_sample(vector, sample)
        if message:
            return f"sample {index}: {message}"
        status_value = statuses[index]
        if status_value.kind is not Kind.STRING:
            return "status must be a string"
        status = status_value.as_string()
        if formed.status_name() != status:
            return f"sample {index} status {formed.status_name()} != {status}"
        code_value = diagnostics[index]
        if code_value.kind is Kind.STRING:
            if not formed.has_code(code_value.as_string()):
                return f"sample {index} diagnostic {code_value.as_string()} not found"
        if status == "Complete" and canonical_values is not None:
            if canonical_values[index].kind is not Kind.NULL:
                document, _ = formed.document_or_fail()
                message = _assert_canonical_value(document, canonical_values[index])
                if message:
                    return f"sample {index}: {message}"
        if proven_names is not None:
            names_value = proven_names[index]
            if names_value.kind is Kind.SEQUENCE:
                document, _ = formed.document_or_fail()
                actual = [
                    item.as_attribute().name
                    for item in document.root_body().items
                    if item.as_attribute() is not None
                ]
                expected = [
                    item.as_string() for item in names_value.as_sequence() if item.kind is Kind.STRING
                ]
                if actual != expected:
                    return f"sample {index} attribute names {actual!r} != {expected!r}"
    return None


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

_QUERY_FAILURE_CODES = {
    "domain-mismatch": "hcl.query.domain-mismatch@1",
    "unknown-operator": "hcl.query.unknown-operator@1",
    "wrong-argument-type": "hcl.query.wrong-argument-type@1",
    "invalid-argument": "hcl.query.invalid-argument@1",
    "invalid-composition": "hcl.query.invalid-composition@1",
    "missing-capability": "hcl.query.missing-capability@1",
    "required-type-mismatch": "hcl.query.type-mismatch@1",
    "cardinality-violation": "hcl.query.cardinality-violation@1",
    "resource-limit": "hcl.query.resource-limit@1",
    "cancelled": "hcl.query.cancelled@1",
    "target-unavailable": "hcl.query.non-literal@1",
}


def _query_failure_code(failure) -> str:
    return _QUERY_FAILURE_CODES.get(failure.kind.value, "hcl.query.invalid-argument@1")


def _argument_name(operator: str) -> str:
    if operator == "hcl.attribute-name-equals":
        return "name"
    if operator == "hcl.attribute-literal-value":
        return "accessor"
    if operator in ("hcl.body-block-type-equals", "hcl.block-type-equals"):
        return "type"
    if operator == "hcl.block-label-equals":
        return "label"
    if operator in ("hcl.expression-kind-is", "hcl.syntax-kind-is"):
        return "kind"
    if operator == "hcl.syntax-text-equals":
        return "text"
    return "argument"


def _build_filters(filters):
    calls = []
    for filter_value in filters:
        operator = compare.string_field(filter_value, "operator")
        if operator is None:
            return None, "missing filter.operator"
        operator_id, version_text = operator.rsplit("@", 1)
        call = protocol_query.OperatorCall(operator_id, int(version_text))
        argument = compare.string_field(filter_value, "argument")
        if argument is not None:
            call = call.with_argument(
                _argument_name(operator_id), PortableValue.string(argument)
            )
        calls.append(call)
    return calls, None


def _bind_query(domain, calls):
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for call in calls:
        expression = expression.then(call)
    definition = (
        protocol_query.QueryDefinition(domain).with_expression(expression).validate()
    )
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return definition.bind(capabilities)


def _expression_facts(document: HclDocument, match: hcl_query.HclMatch):
    expression = match.expression
    kind = expression.kind.as_str()
    text = expression.text(document.source)
    literal = hcl_expression.is_literal_complete(expression)
    return kind, text, literal


def _assert_expression_match(document: HclDocument, match, expected) -> str | None:
    kind, text, literal = _expression_facts(document, match)
    expected_kind = compare.string_field(expected, "kind")
    if expected_kind is not None and kind != expected_kind:
        return f"kind {kind} != {expected_kind}"
    expected_text = compare.string_field(expected, "text")
    if expected_text is not None and text != expected_text:
        return f"text {text!r} != {expected_text!r}"
    expected_literal = compare.boolean_field(expected, "literal")
    if expected_literal is not None and literal != expected_literal:
        return f"literal {literal} != {expected_literal}"
    return None


def _query_case(vector: runner.Case) -> str | None:
    domain = compare.string_field(vector.input, "domain")
    if domain is None:
        return "missing input.domain"
    if domain == "hcl.native-semantic-query@1":
        return _native_query_case(vector)
    if domain == "hcl.lossless-syntax-query@1":
        return _syntax_query_case(vector)
    return "unknown query domain " + domain


def _native_query_case(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _native_query_samples(vector, samples)
    formed, message = _form_value(vector)
    if message:
        return message
    document, message = formed.document_or_fail()
    if message:
        return message
    expects_error_regions = compare.sequence_field(vector.expected, "error_regions") is not None
    if document.formation_status().value != "Complete" and not expects_error_regions:
        return "native-query input must form completely"
    filters = compare.sequence_field(vector.input, "filters")
    if filters is None:
        return "missing input.filters"
    calls, message = _build_filters(filters)
    if message:
        return message
    executable = _bind_query(protocol_query.domain_hcl_native_v1(), calls)
    try:
        execution = hcl_query.execute_hcl_native_query(
            executable, document, hcl_query.HclQueryLimits(), hcl_query.HclCancellationToken()
        )
    except protocol_query.QueryFailure as failure:
        return "execute: " + str(failure)
    matches = list(execution.matches)
    terminal = compare.string_field(vector.expected, "terminal")
    if terminal is None:
        return "missing expected.terminal"
    if terminal != "Completed":
        return f"terminal Completed != {terminal}"
    expected_matches = compare.sequence_field(vector.expected, "matches")
    if expected_matches is not None:
        if len(matches) != len(expected_matches):
            return f"match count {len(matches)} != {len(expected_matches)}"
        for index, expected_match in enumerate(expected_matches):
            message = _assert_expression_match(document, matches[index], expected_match)
            if message:
                return message
    expected_regions = compare.sequence_field(vector.expected, "error_regions")
    if expected_regions is not None:
        regions = [
            (match.region.code, match.position)
            for match in matches
            if match.kind is hcl_query.HclMatchKind.ERROR_REGION
        ]
        if len(regions) != len(expected_regions):
            return f"error region count {len(regions)} != {len(expected_regions)}"
        for index, expected_region in enumerate(expected_regions):
            expected_code = compare.string_field(expected_region, "code")
            if expected_code is not None and regions[index][0] != expected_code:
                return f"error region code {regions[index][0]} != {expected_code}"
            expected_position = compare.integer_field(expected_region, "position")
            if expected_position is not None and regions[index][1] != expected_position:
                return f"error region position {regions[index][1]} != {expected_position}"
    return None


def _sample_accessor(sample) -> str:
    filters = compare.sequence_field(sample, "filters")
    if not filters:
        return ""
    return compare.string_field(filters[-1], "argument") or ""


def _assert_integer_matches(document: HclDocument, matches, expected_matches) -> str | None:
    if len(matches) != len(expected_matches):
        return f"integer match count {len(matches)} != {len(expected_matches)}"
    for index, expected_match in enumerate(expected_matches):
        expected_kind = compare.string_field(expected_match, "kind")
        if expected_kind != "integer":
            return "missing expected match kind"
        try:
            literal = hcl_expression.literal_value(matches[index].expression)
        except hcl_expression.NonLiteralExpression:
            return "match is not an integer literal"
        if literal.kind != "integer":
            return "match is not an integer literal"
        try:
            actual = int(literal.text)
        except ValueError:
            return "integer canonical value is not numeric"
        expected_value = compare.object_field(expected_match, "value")
        if expected_value is None or expected_value.kind is not Kind.INTEGER:
            return "missing expected integer value"
        if actual != expected_value.as_integer():
            return "integer literal value mismatch"
    return None


def _assert_boolean_matches(document: HclDocument, matches, expected_matches) -> str | None:
    if len(matches) != len(expected_matches):
        return f"boolean match count {len(matches)} != {len(expected_matches)}"
    for index, expected_match in enumerate(expected_matches):
        expected_kind = compare.string_field(expected_match, "kind")
        if expected_kind != "boolean":
            return "missing expected match kind"
        try:
            literal = hcl_expression.literal_value(matches[index].expression)
        except hcl_expression.NonLiteralExpression:
            return "match is not a boolean literal"
        if literal.kind != "boolean":
            return "match is not a boolean literal"
        expected_value = compare.boolean_field(expected_match, "value")
        if expected_value is None:
            return "missing expected boolean value"
        if literal.flag != expected_value:
            return "boolean literal value mismatch"
    return None


def _assert_label_matches(matches, expected_matches) -> str | None:
    if len(matches) != len(expected_matches):
        return f"label match count {len(matches)} != {len(expected_matches)}"
    for index, expected_match in enumerate(expected_matches):
        match = matches[index]
        if match.kind is not hcl_query.HclMatchKind.BLOCK_LABEL:
            return "match is not a block label"
        expected_text = compare.string_field(expected_match, "text")
        if expected_text is not None and match.label.text != expected_text:
            return f"label text {match.label.text!r} != {expected_text!r}"
        expected_quoted = compare.boolean_field(expected_match, "quoted")
        if expected_quoted is not None and match.label.quoted != expected_quoted:
            return f"label quoted {match.label.quoted} != {expected_quoted}"
    return None


def _assert_nested_matches(document: HclDocument, matches, expected_matches) -> str | None:
    if len(matches) != len(expected_matches):
        return f"nested match count {len(matches)} != {len(expected_matches)}"
    for index, expected_match in enumerate(expected_matches):
        kind, text, _ = _expression_facts(document, matches[index])
        expected_kind = compare.string_field(expected_match, "kind")
        if expected_kind is not None and kind != expected_kind:
            return f"kind {kind} != {expected_kind}"
        expected_text = compare.string_field(expected_match, "text")
        if expected_text is not None and text != expected_text:
            return f"text {text!r} != {expected_text!r}"
    return None


def _native_query_samples(vector: runner.Case, samples) -> str | None:
    terminals = compare.sequence_field(vector.expected, "terminals")
    if terminals is None:
        return "missing expected.terminals"
    if len(samples) != len(terminals):
        return "terminal count mismatch"
    codes = compare.sequence_field(vector.expected, "codes")
    integer_matches = compare.sequence_field(vector.expected, "integer_matches")
    boolean_matches = compare.sequence_field(vector.expected, "boolean_matches")
    label_matches = compare.sequence_field(vector.expected, "label_matches")
    nested_matches = compare.sequence_field(vector.expected, "nested_matches")
    for index, sample in enumerate(samples):
        formed, message = _form_sample(vector, sample)
        if message:
            return message
        document, message = formed.document_or_fail()
        if message:
            return message
        if document.formation_status().value != "Complete":
            return "native-query input must form completely"
        filters = compare.sequence_field(sample, "filters")
        if filters is None:
            return "missing sample filters"
        last_operator = ""
        if len(filters) > 0:
            last_operator = compare.string_field(filters[-1], "operator") or ""
        calls, message = _build_filters(filters)
        if message:
            return message
        terminal_value = terminals[index]
        if terminal_value.kind is not Kind.STRING:
            return "terminal must be a string"
        terminal = terminal_value.as_string()
        if terminal == "Completed":
            executable = _bind_query(protocol_query.domain_hcl_native_v1(), calls)
            try:
                execution = hcl_query.execute_hcl_native_query(
                    executable, document, hcl_query.HclQueryLimits(), hcl_query.HclCancellationToken()
                )
            except protocol_query.QueryFailure as failure:
                return "execute: " + str(failure)
            matches = list(execution.matches)
            if last_operator == "hcl.attribute-literal-value" and _sample_accessor(sample) == "as-integer" and integer_matches is not None:
                message = _assert_integer_matches(document, matches, integer_matches)
                if message:
                    return message
            elif last_operator == "hcl.attribute-literal-value" and _sample_accessor(sample) == "as-boolean-is" and boolean_matches is not None:
                message = _assert_boolean_matches(document, matches, boolean_matches)
                if message:
                    return message
            elif last_operator == "hcl.block-label-equals" and label_matches is not None:
                message = _assert_label_matches(matches, label_matches)
                if message:
                    return message
            elif last_operator == "hcl.expression-text" and nested_matches is not None:
                message = _assert_nested_matches(document, matches, nested_matches)
                if message:
                    return message
        elif terminal == "Failed":
            executable = _bind_query(protocol_query.domain_hcl_native_v1(), calls)
            try:
                hcl_query.execute_hcl_native_query(
                    executable, document, hcl_query.HclQueryLimits(), hcl_query.HclCancellationToken()
                )
            except protocol_query.QueryFailure as failure:
                if codes is None:
                    return "missing expected.codes"
                expected_code = codes[index]
                if expected_code.kind is not Kind.STRING:
                    return "expected code must be a string"
                if _query_failure_code(failure) != expected_code.as_string():
                    return f"query failure {_query_failure_code(failure)} != {expected_code.as_string()}"
            else:
                return "execution must fail"
        else:
            return "unknown terminal " + terminal
    return None


def _syntax_query_case(vector: runner.Case) -> str | None:
    formed, message = _form_value(vector)
    if message:
        return message
    document, message = formed.document_or_fail()
    if message:
        return message
    if document.formation_status().value != "Complete":
        return "syntax-query input must form completely"
    samples = compare.sequence_field(vector.input, "samples")
    if samples is None:
        return "missing input.samples"
    terminals = compare.sequence_field(vector.expected, "terminals")
    if terminals is None:
        return "missing expected.terminals"
    if len(samples) != len(terminals):
        return "terminal count mismatch"
    matches_sets = compare.sequence_field(vector.expected, "matches")
    if matches_sets is None:
        return "missing expected.matches"
    if len(samples) != len(matches_sets):
        return "match count mismatch"
    decoded = document.source.decoded_text()
    for index, sample in enumerate(samples):
        filters = compare.sequence_field(sample, "filters")
        if filters is None:
            return "missing sample filters"
        calls, message = _build_filters(filters)
        if message:
            return message
        executable = _bind_query(protocol_query.domain_hcl_lossless_syntax_v1(), calls)
        try:
            execution = hcl_query.execute_hcl_syntax_query(
                executable, document, hcl_query.HclQueryLimits(), hcl_query.HclCancellationToken()
            )
        except protocol_query.QueryFailure as failure:
            return "execute: " + str(failure)
        matches = list(execution.matches)
        terminal_value = terminals[index]
        if terminal_value.kind is not Kind.STRING or terminal_value.as_string() != "Completed":
            return "unexpected terminal"
        expected_matches = matches_sets[index].as_sequence()
        if len(matches) != len(expected_matches):
            return f"syntax match count {len(matches)} != {len(expected_matches)}"
        for match_index, expected_match in enumerate(expected_matches):
            expected_kind = compare.string_field(expected_match, "kind")
            if expected_kind is None:
                return "missing expected match kind"
            if matches[match_index].kind.value != expected_kind:
                return f"kind {matches[match_index].kind.value} != {expected_kind}"
            expected_text = compare.string_field(expected_match, "text")
            if expected_text is not None:
                span = matches[match_index].span
                actual = decoded[span.start_byte : span.end_byte]
                if actual != expected_text:
                    return f"text {actual!r} != {expected_text!r}"
            expected_ordinal = compare.integer_field(expected_match, "ordinal")
            if expected_ordinal is not None and matches[match_index].ordinal != expected_ordinal:
                return f"ordinal {matches[match_index].ordinal} != {expected_ordinal}"
    return None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _projection_request(vector: runner.Case):
    target = compare.string_field(vector.input, "target") or "hcl.projection.body@1"
    if target != "hcl.projection.body@1":
        return None, "unknown projection target " + target
    policy = compare.string_field(vector.input, "policy")
    if policy is not None:
        if policy != "ProjectExpression":
            return None, "unknown projection policy " + policy
        return hcl_projection.ProjectionRequest.body_with_expression_policy(
            hcl_projection.ExpressionPolicy.PROJECT_EXPRESSION
        ), ""
    return hcl_projection.ProjectionRequest.body(), ""


def _projection_case(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _projection_samples(vector, samples)
    formed, message = _form_value(vector)
    if message:
        return message
    document, message = formed.document_or_fail()
    if message:
        return message
    request, message = _projection_request(vector)
    if message:
        return message
    result = hcl_projection.project(document, request)
    expected_failure = compare.string_field(vector.expected, "failure")
    if expected_failure is not None:
        if not isinstance(result, hcl_projection.FailedProjectionAttempt):
            return "projection must fail"
        code = result.diagnostics[0].code if result.diagnostics else ""
        if code != expected_failure:
            return f"failure code {code} != {expected_failure}"
        return None
    if not isinstance(result, hcl_projection.CompleteProjection):
        return "projection must complete"
    record = compare.string_field(vector.expected, "record")
    if record is None:
        return "missing expected.record"
    actual_record = compare.string_field(result.value, "record")
    if actual_record != record:
        return f"record {actual_record} != {record}"
    expected_attributes = compare.sequence_field(vector.expected, "attributes")
    if expected_attributes is not None:
        message = _assert_projected_attributes(result.value, expected_attributes)
        if message:
            return message
    expected_blocks = compare.sequence_field(vector.expected, "blocks")
    if expected_blocks is not None:
        message = _assert_projected_blocks(result.value, expected_blocks)
        if message:
            return message
    transformed = compare.integer_field(vector.expected, "transformed_events")
    if transformed is not None:
        events = 0
        for event in result.report.events:
            if event.kind is hcl_projection.ProjectionEventKind.EXPRESSION_SUBSTITUTED:
                events += 1
        if events != transformed:
            return f"transformed events {events} != {transformed}"
    provenance = compare.boolean_field(vector.expected, "event_provenance")
    if provenance is not None:
        non_empty = len(result.provenance.entries) > 0
        if provenance != non_empty:
            return "event provenance mismatch"
    for name in ("attribute_order_preserved", "duplicate_keys_preserved", "canonical_decimal"):
        declared = compare.boolean_field(vector.expected, name)
        if declared is not None and not declared:
            return f"declared projection flag {name} is false"
    return None


def _projected_items(projected) -> tuple | None:
    return compare.sequence_field(projected, "items")


def _item_kind(item) -> str | None:
    return compare.string_field(item, "kind")


def _assert_projected_attributes(projected, expected_attributes) -> str | None:
    items = _projected_items(projected)
    if items is None:
        return "missing projected items"
    attributes = [item for item in items if _item_kind(item) == "attribute"]
    if len(attributes) != len(expected_attributes):
        return f"attribute count {len(attributes)} != {len(expected_attributes)}"
    for index, expected in enumerate(expected_attributes):
        expected_name = compare.string_field(expected, "name")
        if expected_name is None:
            return "missing expected attribute name"
        actual_name = compare.string_field(attributes[index], "name")
        if actual_name != expected_name:
            return f"attribute name {actual_name} != {expected_name}"
        value = compare.object_field(attributes[index], "value")
        if value is None:
            return "missing projected value"
        message = _assert_projected_value(value, expected)
        if message:
            return message
    return None


def _assert_projected_blocks(projected, expected_blocks) -> str | None:
    items = _projected_items(projected)
    if items is None:
        return "missing projected items"
    blocks = [item for item in items if _item_kind(item) == "block"]
    if len(blocks) != len(expected_blocks):
        return f"block count {len(blocks)} != {len(expected_blocks)}"
    for index, expected in enumerate(expected_blocks):
        expected_type = compare.string_field(expected, "type")
        if expected_type is not None:
            actual_type = compare.string_field(blocks[index], "type")
            if actual_type != expected_type:
                return f"block type {actual_type} != {expected_type}"
        expected_labels = compare.sequence_field(expected, "labels")
        if expected_labels is not None:
            actual_labels = compare.sequence_field(blocks[index], "labels")
            if actual_labels is None:
                return "missing projected block labels"
            if len(actual_labels) != len(expected_labels):
                return f"label count {len(actual_labels)} != {len(expected_labels)}"
            for label_index, expected_label in enumerate(expected_labels):
                if expected_label.kind is not Kind.STRING:
                    return "expected label must be a string"
                actual = actual_labels[label_index]
                if actual.kind is not Kind.STRING or actual.as_string() != expected_label.as_string():
                    return "label mismatch"
    return None


def _assert_projected_value(actual, expected) -> str | None:
    kind = compare.string_field(expected, "kind")
    if kind is None:
        return "missing expected value kind"
    if kind == "string":
        text = compare.string_field(expected, "text")
        if text is None:
            return "missing expected text"
        if actual.kind is not Kind.STRING or actual.as_string() != text:
            return "projected string mismatch"
    elif kind == "integer":
        expected_value = compare.object_field(expected, "value")
        if expected_value is None or expected_value.kind is not Kind.INTEGER:
            return "missing expected integer"
        if actual.kind is not Kind.INTEGER or actual.as_integer() != expected_value.as_integer():
            return "projected integer mismatch"
    elif kind == "real":
        expected_value = compare.object_field(expected, "value")
        if expected_value is None:
            return "missing expected real"
        expected_f64 = _expected_f64(expected_value)
        actual_f64 = _expected_f64(actual)
        if actual_f64 is None or expected_f64 is None or not _bits_equal(actual_f64, expected_f64):
            return "projected real mismatch"
    elif kind == "boolean":
        expected_value = compare.boolean_field(expected, "value")
        if expected_value is None:
            return "missing expected boolean"
        if actual.kind is not Kind.BOOLEAN or actual.as_boolean() != expected_value:
            return "projected boolean mismatch"
    elif kind == "null":
        if actual.kind is not Kind.NULL:
            return "projected value is not null"
    elif kind == "tuple":
        elements = compare.sequence_field(expected, "elements")
        if elements is None:
            return "missing expected elements"
        if actual.kind is not Kind.SEQUENCE:
            return "projected value is not a tuple"
        if len(actual.as_sequence()) != len(elements):
            return "tuple count mismatch"
        for index, expected_element in enumerate(elements):
            message = _assert_projected_element(actual.as_sequence()[index], expected_element)
            if message:
                return message
    elif kind == "object":
        entries = compare.sequence_field(expected, "entries")
        if entries is None:
            return "missing expected entries"
        if actual.kind is not Kind.ENTRY_MAPPING and actual.kind is not Kind.OBJECT:
            return "projected value is not an object"
        actual_entries = (
            actual.as_entry_mapping() if actual.kind is Kind.ENTRY_MAPPING else actual.as_object()
        )
        if len(actual_entries) != len(entries):
            return "object count mismatch"
        for index, expected_entry in enumerate(entries):
            pair = expected_entry.as_sequence()
            if len(pair) != 2:
                return "expected object entry must be a pair"
            expected_key = pair[0]
            if expected_key.kind is not Kind.STRING:
                return "expected object key must be a string"
            actual_key, actual_value = actual_entries[index]
            if actual_key.kind is Kind.STRING:
                actual_key = actual_key.as_string()
            if actual_key != expected_key.as_string():
                return "object key mismatch"
            message = _assert_projected_element(actual_value, pair[1])
            if message:
                return message
    elif kind == "expression":
        expected_expression = compare.object_field(expected, "expression")
        if expected_expression is None:
            return "missing expected expression record"
        # The family emits the value as a {kind, expression} wrapper
        # (hcl/projection.py:403-407) while the Go runner projects the
        # expression record directly; both carry the same record facts.
        expression_record = compare.object_field(actual, "expression")
        if expression_record is None:
            expression_record = actual
        actual_record = compare.string_field(expression_record, "record")
        expected_record = compare.string_field(expected_expression, "record")
        if actual_record != expected_record:
            return "expression record mismatch"
        actual_kind = compare.string_field(expression_record, "kind")
        expected_kind = compare.string_field(expected_expression, "kind")
        if actual_kind != expected_kind:
            return "expression kind mismatch"
        actual_text = compare.string_field(expression_record, "text")
        expected_text = compare.string_field(expected_expression, "text")
        if actual_text != expected_text:
            return "expression text mismatch"
    else:
        return "unknown projected value kind " + kind
    return None


def _assert_projected_element(actual, expected) -> str | None:
    if expected.kind is Kind.STRING:
        if actual.kind is not Kind.STRING or actual.as_string() != expected.as_string():
            return "projected element string mismatch"
        return None
    if expected.kind is Kind.INTEGER:
        if actual.kind is not Kind.INTEGER or actual.as_integer() != expected.as_integer():
            return "projected element integer mismatch"
        return None
    if expected.kind is Kind.BOOLEAN:
        if actual.kind is not Kind.BOOLEAN or actual.as_boolean() != expected.as_boolean():
            return "projected element boolean mismatch"
        return None
    expected_f64 = _expected_f64(expected)
    if expected_f64 is not None:
        actual_f64 = _expected_f64(actual)
        if actual_f64 is None or not _bits_equal(actual_f64, expected_f64):
            return "projected element real mismatch"
        return None
    if compare.object_field(expected, "kind") is not None:
        return _assert_projected_value(actual, expected)
    return "unsupported expected element"


def _projection_samples(vector: runner.Case, samples) -> str | None:
    codes = compare.sequence_field(vector.expected, "codes")
    literals = compare.sequence_field(vector.expected, "literals")
    for index, sample in enumerate(samples):
        formed, message = _form_sample(vector, sample)
        if message:
            return message
        document, message = formed.document_or_fail()
        if message:
            return message
        request, message = _projection_request(vector)
        if message:
            return message
        result = hcl_projection.project(document, request)
        if codes is not None:
            code_value = codes[index]
            if code_value.kind is Kind.STRING:
                if not isinstance(result, hcl_projection.FailedProjectionAttempt):
                    return "projection must fail"
                code = result.diagnostics[0].code if result.diagnostics else ""
                if code != code_value.as_string():
                    return f"projection code {code} != {code_value.as_string()}"
        if literals is not None:
            expected_literal = literals[index]
            if expected_literal.kind is not Kind.BOOLEAN:
                return "expected literal must be a boolean"
            completed = isinstance(result, hcl_projection.CompleteProjection)
            if completed != expected_literal.as_boolean():
                return f"sample {index} projection completion {completed} != {expected_literal.as_boolean()}"
    return None


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialization_request(style: str, profile_name: str):
    profile = _profile(profile_name)
    if profile is None:
        return None, "unknown profile " + profile_name
    if style != "hcl.canonical-document@1":
        return None, "unknown materialization style " + style
    return MaterializationRequest.new(
        profile.id(), MaterializationStyleId.new("hcl.canonical-document", 1)
    ), ""


def _complete_materialization(record, request):
    result = hcl_materialization.materialize(record, request)
    if isinstance(result, FailedMaterializationAttempt):
        return None, "materialization failed: " + hcl_materialization.materialization_failure_code(result.failure)
    return result, ""


def _materialization_case(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _materialization_samples(vector, samples)
    style = compare.string_field(vector.input, "style")
    profile_name = compare.string_field(vector.input, "profile")
    if style is None or profile_name is None:
        return "missing input.style/profile"
    record = compare.object_field(vector.input, "record")
    if record is None:
        return "missing input.record"
    request, message = _materialization_request(style, profile_name)
    if message:
        return message
    expected_failure = compare.string_field(vector.expected, "failure")
    if expected_failure is not None:
        result = hcl_materialization.materialize(record, request)
        if not isinstance(result, FailedMaterializationAttempt):
            return "materialization must fail"
        actual = hcl_materialization.materialization_failure_code(result.failure)
        if actual != expected_failure:
            return f"failure {actual} != {expected_failure}"
        return None
    complete, message = _complete_materialization(record, request)
    if message:
        return message
    render = compare.string_field(vector.expected, "render")
    if render is not None and complete.document.render() != render.encode("utf-8"):
        return f"render {complete.document.render()!r} != {render!r}"
    closure = compare.boolean_field(vector.expected, "closure")
    if closure is not None and closure:
        if complete.document.formation_status().value != "Complete":
            return "materialized document must be complete"
    fingerprint = compare.boolean_field(vector.expected, "fingerprint_match")
    if fingerprint is not None and fingerprint:
        message = _assert_fingerprint_match(complete, record)
        if message:
            return message
    return None


def _assert_fingerprint_match(complete, record) -> str | None:
    request = hcl_projection.ProjectionRequest.body_with_expression_policy(
        hcl_projection.ExpressionPolicy.PROJECT_EXPRESSION
    )
    result = hcl_projection.project(complete.document, request)
    if not isinstance(result, hcl_projection.CompleteProjection):
        return "materialized document must re-project"
    projection = result
    items = compare.sequence_field(record, "items")
    if items is None:
        return "missing record items"
    projected_items = _projected_items(projection.value)
    if projected_items is None:
        return "missing projected items"
    projected_attributes = [
        item for item in projected_items if _item_kind(item) == "attribute"
    ]
    for item in items:
        if _item_kind(item) != "attribute":
            continue
        value = compare.object_field(item, "value")
        if value is None:
            continue
        if compare.string_field(value, "kind") != "expression":
            continue
        name = compare.string_field(item, "name")
        if name is None:
            return "missing attribute name"
        expected_expression = compare.object_field(value, "expression")
        if expected_expression is None:
            return "missing expression record"
        projected = None
        for candidate in projected_attributes:
            candidate_name = compare.string_field(candidate, "name")
            if candidate_name == name:
                projected = candidate
                break
        if projected is None:
            return "projected attribute " + name + " not found"
        projected_value = compare.object_field(projected, "value")
        if projected_value is None:
            return "missing projected value"
        expression_record = compare.object_field(projected_value, "expression")
        if expression_record is None:
            expression_record = projected_value
        for member in ("kind", "text", "record"):
            actual = compare.string_field(expression_record, member)
            expected = compare.string_field(expected_expression, member)
            if actual != expected:
                return f"expression {member} {actual} != {expected}"
    return None


def _materialization_samples(vector: runner.Case, samples) -> str | None:
    renders = compare.sequence_field(vector.expected, "renders")
    codes = compare.sequence_field(vector.expected, "codes")
    closure = compare.boolean_field(vector.expected, "closure")
    if renders is None and codes is None:
        return "missing expected.codes"
    expected_length = len(codes) if codes is not None else len(renders)
    if len(samples) != expected_length:
        return "render/code count mismatch"
    for index, sample in enumerate(samples):
        style = compare.string_field(sample, "style")
        if style is None:
            style = compare.string_field(vector.input, "style")
            if style is None:
                return "missing sample style"
        profile_value = compare.string_field(sample, "profile")
        if profile_value is None:
            profile_value = compare.string_field(vector.input, "profile")
            if profile_value is None:
                return "missing sample profile"
        request, message = _materialization_request(style, profile_value)
        if message:
            return message
        record = compare.object_field(sample, "record")
        if record is None:
            return "missing sample record"
        result = hcl_materialization.materialize(record, request)
        if isinstance(result, CompleteMaterialization):
            if renders is not None:
                expected_render = renders[index]
                if expected_render.kind is not Kind.STRING:
                    return "expected render must be a string"
                if result.document.render() != expected_render.as_string().encode("utf-8"):
                    return f"render {result.document.render()!r} != {expected_render.as_string()!r}"
            elif codes is not None:
                if codes[index].kind is Kind.STRING:
                    return "materialization must fail"
            if closure is not None and closure:
                if result.document.formation_status().value != "Complete":
                    return "materialized document must be complete"
        else:
            if codes is None:
                return "materialization must complete"
            expected_code = codes[index]
            if expected_code.kind is not Kind.STRING:
                return "expected code must be a string"
            actual = hcl_materialization.materialization_failure_code(result.failure)
            if actual != expected_code.as_string():
                return f"materialization failure {actual} != {expected_code.as_string()}"
    return None


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def _edit_value(value) -> EditValue | None:
    kind = compare.string_field(value, "kind")
    if kind is None:
        return None
    if kind == "string":
        text = compare.string_field(value, "text")
        if text is None:
            return None
        return EditValue.string(text)
    if kind == "integer":
        payload = compare.object_field(value, "value")
        if payload is None or payload.kind is not Kind.INTEGER:
            return None
        return EditValue.integer(payload.as_integer())
    if kind == "real":
        payload = compare.object_field(value, "value")
        if payload is None:
            return None
        real = _expected_f64(payload)
        if real is None:
            return None
        return EditValue.real(real)
    if kind == "boolean":
        payload = compare.object_field(value, "value")
        if payload is None or payload.kind is not Kind.BOOLEAN:
            return None
        return EditValue.boolean(payload.as_boolean())
    if kind == "null":
        return EditValue.null()
    if kind == "tuple":
        elements_value = compare.object_field(value, "elements")
        if elements_value is None or elements_value.kind is not Kind.SEQUENCE:
            return None
        elements = []
        for element in elements_value.as_sequence():
            converted = _edit_value(element)
            if converted is None:
                return None
            elements.append(converted)
        return EditValue.tuple(tuple(elements))
    if kind == "object":
        entries_value = compare.object_field(value, "entries")
        if entries_value is None or entries_value.kind is not Kind.SEQUENCE:
            return None
        entries = []
        for entry in entries_value.as_sequence():
            pair = entry.as_sequence()
            if len(pair) != 2:
                return None
            key_value = pair[0]
            if key_value.kind is Kind.STRING:
                key = EditKey.identifier(key_value.as_string())
            elif key_value.kind is Kind.INTEGER:
                key = EditKey.number(key_value.as_integer())
            else:
                return None
            converted = _edit_value(pair[1])
            if converted is None:
                return None
            entries.append((key, converted))
        return EditValue.object(tuple(entries))
    if kind == "expression":
        expression = compare.object_field(value, "expression")
        if expression is None:
            return None
        expression_kind = compare.string_field(expression, "kind")
        text = compare.string_field(expression, "text")
        if expression_kind is None or text is None:
            return None
        return EditValue.expression(expression_kind, text)
    return None


def _edit_placement(operation) -> BodyPlacement | None:
    placement = compare.string_field(operation, "placement") or "Last"
    if placement == "Last":
        return BodyPlacement.last()
    if placement == "First":
        return BodyPlacement.first()
    return None


def _build_transaction(document, operations):
    builder = hcl_edit.EditTransactionBuilder(document)
    for operation in operations:
        op = compare.string_field(operation, "op")
        if op is None:
            return None, "missing op"
        if op == "hcl.edit.set-attribute-value@1":
            body = _body_path(operation)
            attribute = compare.string_field(operation, "attribute")
            value_field = compare.object_field(operation, "value")
            value = _edit_value(value_field)
            if attribute is None or value is None:
                return None, "missing attribute/value"
            builder.set_attribute_value(body, attribute, value)
        elif op == "hcl.edit.insert-attribute@1":
            body = _body_path(operation)
            name = compare.string_field(operation, "name")
            value_field = compare.object_field(operation, "value")
            value = _edit_value(value_field)
            if name is None or value is None:
                return None, "missing name/value"
            placement = _edit_placement(operation)
            if placement is None:
                return None, "unknown placement"
            builder.insert_attribute(body, name, value, placement)
        elif op == "hcl.edit.remove-attribute@1":
            body = _body_path(operation)
            attribute = compare.string_field(operation, "attribute")
            if attribute is None:
                return None, "missing attribute"
            builder.remove_attribute(body, attribute)
        elif op == "hcl.edit.rename-attribute@1":
            body = _body_path(operation)
            attribute = compare.string_field(operation, "attribute")
            name = compare.string_field(operation, "name")
            if attribute is None or name is None:
                return None, "missing attribute/name"
            builder.rename_attribute(body, attribute, name)
        elif op == "hcl.edit.insert-block@1":
            body = _body_path(operation)
            block_type = compare.string_field(operation, "type")
            labels_value = compare.sequence_field(operation, "labels")
            attributes_value = compare.sequence_field(operation, "attributes")
            if block_type is None or labels_value is None or attributes_value is None:
                return None, "missing block facts"
            labels = [item.as_string() for item in labels_value if item.kind is Kind.STRING]
            attributes = []
            for attribute in attributes_value:
                name = compare.string_field(attribute, "name")
                value_field = compare.object_field(attribute, "value")
                value = _edit_value(value_field)
                if name is None or value is None:
                    return None, "missing block attribute"
                attributes.append((name, value))
            placement = _edit_placement(operation)
            if placement is None:
                return None, "unknown placement"
            builder.insert_block(body, block_type, labels, attributes, placement)
        elif op == "hcl.edit.remove-block@1":
            node_ref = compare.object_field(operation, "node_ref")
            if node_ref is None:
                return None, "missing node_ref"
            block_type = compare.string_field(node_ref, "type")
            labels_value = compare.sequence_field(node_ref, "labels")
            if block_type is None or labels_value is None:
                return None, "missing node_ref type/labels"
            labels = [item.as_string() for item in labels_value if item.kind is Kind.STRING]
            builder.remove_block(BodyPath.root(), block_type, labels, 0)
        else:
            return None, "unknown edit op " + op
    return builder.build(), None


def _body_path(operation) -> BodyPath:
    body = compare.string_field(operation, "body")
    if body is None or body == "root":
        return BodyPath.root()
    return BodyPath.of_steps([])


def _reparse(document: HclDocument):
    profile = HclProfile.TFVARS_V1 if document.profile_id().id == "hcl.tfvars" else HclProfile.NATIVE_V1
    try:
        formed = parse(document.render(), profile, HclEncodingSelection.PROFILE_DEFAULT, HclParseLimits())
    except HclFormationFailure as failure:
        return None, "reparse: " + failure.code
    return formed, ""


def _all_labels_quoted(body) -> bool:
    for item in body.items:
        if item.as_attribute() is not None:
            continue
        block = item.as_block()
        for label in block.labels:
            if not label.quoted:
                return False
        if not _all_labels_quoted(block.body):
            return False
    return True


def _replacement_sets_equal(left, right) -> bool:
    if len(left) != len(right):
        return False
    for left_item, right_item in zip(left, right):
        if (
            left_item.old_start != right_item.old_start
            or left_item.old_end != right_item.old_end
            or left_item.original != right_item.original
            or left_item.replacement != right_item.replacement
        ):
            return False
    return True


def _edit_case(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        return _edit_conflicts(vector, samples)
    formed, message = _form_value(vector)
    if message:
        return message
    document, message = formed.document_or_fail()
    if message:
        return message
    if document.formation_status().value != "Complete":
        return "edit input must form completely"
    operations = compare.sequence_field(vector.input, "operations")
    if operations is None:
        return "missing input.operations"
    builder, message = _build_transaction(document, operations)
    if message:
        return message
    transaction = builder
    try:
        commit = hcl_edit.commit(document, transaction)
    except HclEditFailure as failure:
        return failure.code
    message = _assert_edit_facts(document, transaction, commit, vector)
    if message:
        return message
    return None


def _assert_edit_facts(base, transaction, commit, vector) -> str | None:
    committed = commit.document
    if committed.formation_status().value != "Complete":
        return "committed document must be complete"
    render = compare.string_field(vector.expected, "render")
    if render is not None and committed.render() != render.encode("utf-8"):
        return f"render {committed.render()!r} != {render!r}"
    reparse_closure = compare.boolean_field(vector.expected, "reparse_closure")
    if reparse_closure is not None and reparse_closure:
        reparsed, message = _reparse(committed)
        if message:
            return message
        if reparsed.formation_status().value != "Complete":
            return "committed document must reparse completely"
    untouched = compare.boolean_field(vector.expected, "untouched_byte_proof")
    if untouched is not None and untouched:
        try:
            commit.untouched_proof.verify(
                base.source, committed.source, list(commit.source_patch.replacements)
            )
        except Exception as error:
            return "untouched proof: " + str(error)
    patch_replays = compare.boolean_field(vector.expected, "patch_replays")
    if patch_replays is not None and patch_replays:
        try:
            replay = commit.source_patch.apply(base.source, SourcePatchLimits())
        except Exception as error:
            return "patch apply: " + str(error)
        if replay.bytes() != committed.render():
            return "patch does not replay"
    labels_quoted = compare.boolean_field(vector.expected, "labels_always_quoted")
    if labels_quoted is not None and labels_quoted:
        if not _all_labels_quoted(committed.root_body()):
            return "a block label is not quoted"
    dry_run = compare.boolean_field(vector.expected, "dry_run_equivalent")
    if dry_run is not None and dry_run:
        from consema.document.edit_plan import EditPlanSourceId

        plan = hcl_edit.dry_run(base, transaction, EditPlanSourceId.new("hcl-conformance"))
        if not _replacement_sets_equal(
            list(plan.replacements()), list(commit.source_patch.replacements)
        ):
            return "dry-run replacement set differs from the committed replacement set"
    return None


def _edit_conflicts(vector: runner.Case, samples) -> str | None:
    codes = compare.sequence_field(vector.expected, "codes")
    if codes is None:
        return "missing expected.codes"
    base_unchanged = compare.boolean_field(vector.expected, "base_unchanged")
    if len(samples) != len(codes):
        return "code count mismatch"
    for index, sample in enumerate(samples):
        formed, message = _form_sample(vector, sample)
        if message:
            return message
        document, message = formed.document_or_fail()
        if message:
            return message
        operations = compare.sequence_field(sample, "operations")
        if operations is None:
            return "missing operations"
        wrong_source = compare.object_field(sample, "wrong_source")
        if wrong_source is not None:
            wrong_vector = runner.Case(
                id=vector.id,
                capability=vector.capability,
                contract=vector.contract,
                input=wrong_source,
                expected=vector.expected,
                index=vector.index,
            )
            wrong_formed, message = _form_value(wrong_vector)
            if message:
                return message
            other, message = wrong_formed.document_or_fail()
            if message:
                return message
            builder, message = _build_transaction(other, operations)
            if message:
                return message
            transaction = builder
        else:
            builder, message = _build_transaction(document, operations)
            if message:
                return message
            transaction = builder
        try:
            hcl_edit.commit(document, transaction)
        except HclEditFailure as failure:
            expected_code = codes[index]
            if expected_code.kind is not Kind.STRING:
                return "expected code must be a string"
            if failure.code != expected_code.as_string():
                return f"edit failure {failure.code} != {expected_code.as_string()}"
        else:
            return "edit must fail"
        if base_unchanged is not None and base_unchanged:
            if document.render() != document.source.bytes():
                return "base document changed"
    return None


runner.register_suite("hcl-v1.json", "consema.hcl.conformance@1", "", 57, run)
