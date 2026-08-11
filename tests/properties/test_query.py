"""Query golden transcriptions (java-properties-v1.json cases).

Cases covered:

- java-properties-v1.json: query.native-duplicates-and-escape-ownership
  (lines 61-64), query.logical-and-syntax-order (66-69),
  query.validation-limit-cancellation (71-74).
- The escaped-key duplicate query exercises exact UTF-16 ``UTF16BE/1``
  key matching without Unicode normalization or case folding (RFC 0010
  section 10).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.source import SourceEncoding
from consema.properties import (
    PropertiesCancellationToken,
    PropertiesMatchKind,
    PropertiesParseLimits,
    PropertiesQueryLimits,
    PropertiesSyntaxMatch,
    execute_properties_query,
    execute_properties_query_cursor,
    execute_properties_syntax_query,
    parse_reader,
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

DEFAULT_LIMITS = PropertiesParseLimits()
QUERY_LIMITS = PropertiesQueryLimits()
NATIVE_DOMAIN = "java-properties.native-semantic-query"
SYNTAX_DOMAIN = "java-properties.lossless-syntax-query"


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


def test_native_duplicates_and_escape_ownership():
    # Case query.native-duplicates-and-escape-ownership
    # (java-properties-v1.json:61-64).
    source = b"a\\ key=one\\u0021\na\\ key=two\nempty\n"
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    key_utf16be = bytes.fromhex("00610020006b00650079")  # "a key" as UTF16BE/1
    take = 1
    duplicates = executable(
        NATIVE_DOMAIN,
        1,
        [
            OperatorCall("properties.document-properties", 1),
            OperatorCall("properties.property-key-equals", 1).with_argument(
                "key", PortableValue.bytes_value(key_utf16be)
            ),
            OperatorCall("core.take", 1).with_argument(
                "count", PortableValue.integer(take)
            ),
            OperatorCall("properties.duplicate-group", 1),
        ],
    )
    duplicate_result = execute_properties_query(
        duplicates, document, QUERY_LIMITS, PropertiesCancellationToken()
    )
    assert len(duplicate_result.matches) == 2
    assert all(
        match.kind is PropertiesMatchKind.PROPERTY
        and match.duplicate_group is not None
        for match in duplicate_result.matches
    )

    escapes = executable(
        NATIVE_DOMAIN,
        1,
        [
            OperatorCall("properties.document-properties", 1),
            OperatorCall("core.take", 1).with_argument(
                "count", PortableValue.integer(take)
            ),
            OperatorCall("properties.property-escapes", 1),
        ],
    )
    escape_result = execute_properties_query(
        escapes, document, QUERY_LIMITS, PropertiesCancellationToken()
    )
    assert len(escape_result.matches) == 2
    assert all(
        match.kind is PropertiesMatchKind.ESCAPE for match in escape_result.matches
    )
    # The first property owns one key escape and one value escape, so
    # escape ownership (in_key) is exact.
    in_keys = [match.in_key for match in escape_result.matches]
    assert in_keys == [True, False]


def test_logical_and_syntax_order():
    # Case query.logical-and-syntax-order (java-properties-v1.json:66-69).
    logical = parse_reader(b"k=one\\\r\n two\n", SourceEncoding.utf8(), DEFAULT_LIMITS)
    logical_query = executable(
        NATIVE_DOMAIN,
        1,
        [
            OperatorCall("properties.logical-lines", 1),
            OperatorCall("properties.logical-line-natural-lines", 1),
        ],
    )
    logical_result = execute_properties_query(
        logical_query, logical, QUERY_LIMITS, PropertiesCancellationToken()
    )
    assert [match.ordinal for match in logical_result.matches] == [0, 1]
    assert all(
        match.kind is PropertiesMatchKind.NATURAL_LINE
        for match in logical_result.matches
    )

    syntax = parse_reader("键=值\n".encode("utf-8"), SourceEncoding.utf8(), DEFAULT_LIMITS)
    raw_branch = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("properties.syntax-raw-bytes-equals", 1).with_argument(
            "bytes", PortableValue.bytes_value(bytes.fromhex("e994ae"))
        )
    )
    text_branch = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("properties.syntax-text-equals", 1).with_argument(
            "text", PortableValue.string("值")
        )
    )
    utf16_branch = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("properties.syntax-utf16be-equals", 1).with_argument(
            "code_units", PortableValue.bytes_value(bytes.fromhex("503c"))
        )
    )
    merge = QueryExpression(
        ExpressionKind.STRUCTURE_ORDER_MERGE,
        branches=[raw_branch, text_branch, utf16_branch],
    )
    syntax_executable = (
        QueryDefinition(QueryDomain(SYNTAX_DOMAIN, 1))
        .with_expression(merge)
        .validate()
        .bind(capabilities())
    )
    syntax_result = execute_properties_syntax_query(
        syntax_executable,
        syntax,
        QUERY_LIMITS,
        PropertiesCancellationToken(),
    )
    assert [match.kind.value for match in syntax_result.matches] == [
        "Key",
        "Value",
        "Value",
    ]
    assert all(
        isinstance(match, PropertiesSyntaxMatch) for match in syntax_result.matches
    )
    # The two Value matches share the same span, so the ordinal sequence is
    # not strictly increasing (strictly_increasing_ordinals: false).
    ordinals = [match.ordinal for match in syntax_result.matches]
    assert not all(
        ordinals[i] < ordinals[i + 1] for i in range(len(ordinals) - 1)
    )
    assert len({match.span for match in syntax_result.matches}) == 2


def test_validation_limit_cancellation():
    # Case query.validation-limit-cancellation
    # (java-properties-v1.json:71-74).
    invalid = QueryDefinition(QueryDomain(NATIVE_DOMAIN, 1)).with_expression(
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("properties.document-properties", 1))
        .then(
            OperatorCall("properties.property-key-equals", 1).with_argument(
                "key", PortableValue.bytes_value(b"\x00")
            )
        )
    )
    with pytest.raises(QueryFailure) as caught:
        invalid.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT
    assert caught.value.argument == "key"

    document = parse_reader(b"a=1\nb=2\n", SourceEncoding.utf8(), DEFAULT_LIMITS)
    all_properties = executable(
        NATIVE_DOMAIN, 1, [OperatorCall("properties.document-properties", 1)]
    )
    with pytest.raises(QueryFailure) as caught:
        execute_properties_query(
            all_properties,
            document,
            PropertiesQueryLimits(max_steps=100, max_results=1),
            PropertiesCancellationToken(),
        )
    assert caught.value.code == "core.query.resource-limit@1"

    cancellation = PropertiesCancellationToken()
    cursor = execute_properties_query_cursor(
        all_properties, document, QUERY_LIMITS, cancellation
    )
    assert cursor.next() is not None
    cancellation.cancel()
    assert cursor.next() is None
    assert cursor.terminal_state() == "Cancelled"
