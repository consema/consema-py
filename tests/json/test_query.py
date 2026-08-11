"""Query golden transcriptions (json-family-v2.json cases).

Cases covered: json5.query.syntax-v2-identifier (json-family-v2.json:114-
118) and json5.query.native-v2-binary (120-124); both pin ``v1_rejected``
— a v1-domain query on a JSON5 document is rejected with a domain
mismatch (RFC 0005 §7: JSON5 requires the v2 domains).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.limits import ParseLimits
from consema.json import (
    JsonCancellationToken,
    JsonProfile,
    JsonQueryLimits,
    JsonSyntaxKind,
    JsonValueKind,
    execute_json_query,
    execute_json_syntax_query,
    parse,
)
from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDomain,
    QueryDefinition,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
)

DEFAULT_LIMITS = ParseLimits()
QUERY_LIMITS = JsonQueryLimits()


def capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def executable(domain_id: str, version: int, operators: list[OperatorCall]):
    definition = QueryDefinition(QueryDomain(domain_id, version))
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    return definition.with_expression(expression).validate().bind(capabilities())


def test_json5_query_syntax_v2_identifier():
    # Case json5.query.syntax-v2-identifier (json-family-v2.json:114-118).
    document = parse(b"{key:1,true:2}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    operator = OperatorCall("json.syntax-kind-is", 1).with_argument(
        "kind", PortableValue.string("Identifier")
    )
    execution = execute_json_syntax_query(
        executable("json.lossless-syntax-query", 2, [operator]),
        document,
        QUERY_LIMITS,
        JsonCancellationToken(),
    )
    texts = [
        document.source.bytes()[match.span.start_byte : match.span.end_byte].decode("utf-8")
        for match in execution.matches
    ]
    assert texts == ["key", "true"]
    assert all(match.kind is JsonSyntaxKind.IDENTIFIER for match in execution.matches)


def test_json5_query_syntax_v1_rejected():
    # v1_rejected fact of case json5.query.syntax-v2-identifier
    # (json-family-v2.json:117). With a v1-valid kind ("Number") the
    # executor itself raises the domain mismatch for a JSON5 document
    # (RFC 0005 §7: JSON5 requires the v2 domains).
    document = parse(b"{key:1,true:2}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    operator = OperatorCall("json.syntax-kind-is", 1).with_argument(
        "kind", PortableValue.string("Number")
    )
    with pytest.raises(QueryFailure) as caught:
        execute_json_syntax_query(
            executable("json.lossless-syntax-query", 1, [operator]),
            document,
            QUERY_LIMITS,
            JsonCancellationToken(),
        )
    assert caught.value.kind is QueryFailureKind.DOMAIN_MISMATCH
    # The v2-only kind "Identifier" is rejected at validation for v1.
    operator = OperatorCall("json.syntax-kind-is", 1).with_argument(
        "kind", PortableValue.string("Identifier")
    )
    with pytest.raises(QueryFailure) as caught:
        executable("json.lossless-syntax-query", 1, [operator])
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT


def test_json5_query_native_v2_binary():
    # Case json5.query.native-v2-binary (json-family-v2.json:120-124).
    document = parse(b"-Infinity", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    execution = execute_json_query(
        executable("json.native-semantic-query", 2, []),
        document,
        QUERY_LIMITS,
        JsonCancellationToken(),
    )
    assert len(execution.matches) == 1
    match = execution.matches[0]
    kind = document.root().kind()
    assert kind.is_available and kind.value is JsonValueKind.BINARY_FLOAT64


def test_json5_query_native_v1_rejected():
    # v1_rejected fact of case json5.query.native-v2-binary
    # (json-family-v2.json:123).
    document = parse(b"-Infinity", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    with pytest.raises(QueryFailure) as caught:
        execute_json_query(
            executable("json.native-semantic-query", 1, []),
            document,
            QUERY_LIMITS,
            JsonCancellationToken(),
        )
    assert caught.value.kind is QueryFailureKind.DOMAIN_MISMATCH


def test_native_v2_query_is_valid_for_strict_documents():
    # v2 domains are valid for strict/JSONC documents (RFC 0005 §7).
    document = parse(b'{"a":1}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    operators = [
        OperatorCall("json.try-object-members", 1),
        OperatorCall("json.member-name-equals", 1).with_argument(
            "name", PortableValue.string("a")
        ),
    ]
    execution = execute_json_query(
        executable("json.native-semantic-query", 2, operators),
        document,
        QUERY_LIMITS,
        JsonCancellationToken(),
    )
    assert len(execution.matches) == 1
