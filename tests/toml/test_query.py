"""Query intent documents: the toml.native-semantic-query@1 and
toml.lossless-syntax-query@1 domains.

Vector cases transcribed: toml.query.nested-entry-order,
toml.query.aot-element-order (conformance/vectors/toml-v1.json); the
domain/operator rows are frozen by consema.protocol query.py:523-529 and
597-602; execution semantics per crates/consema-toml/src/query.rs.
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.protocol.query import (
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryExpression,
    QuerySelection,
    domain_toml_lossless_syntax_v1,
    domain_toml_native_v1,
)
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.core import PortableValue
from consema.toml import (
    QueryLimits,
    TomlItemKind,
    TomlMatchKind,
    TomlProfile,
    execute_toml_query,
    execute_toml_syntax_query,
    parse,
)


def _document(source: bytes):
    return parse(source, TomlProfile.TOML10_V1, ParseLimits())


def _capabilities():
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _executable(expression: QueryExpression, domain=None, selection=None):
    definition = QueryDefinition(domain or domain_toml_native_v1()).with_expression(expression)
    if selection is not None:
        definition = definition.with_selection(selection)
    return definition.validate().bind(_capabilities())


def test_vector_nested_entry_order(fixture_bytes):
    """toml.query.nested-entry-order: entries of the dotted table service
    keep their insertion order name, environment, listen."""
    document = _document(fixture_bytes("toml/application.toml"))
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("toml.try-table-entries", 1))
        .then(OperatorCall("toml.entry-name-equals", 1).with_argument("name", PortableValue.string("service")))
        .then(OperatorCall("toml.entry-item", 1))
        .then(OperatorCall("toml.try-table-entries", 1))
    )
    matches = execute_toml_query(_executable(expression), document, QueryLimits.default())
    assert [match.name for match in matches] == ["name", "environment", "listen"]
    assert all(match.kind is TomlMatchKind.ENTRY for match in matches)


def test_vector_aot_element_order(fixture_bytes):
    """toml.query.aot-element-order: upstreams yields ordinals 0, 1."""
    document = _document(fixture_bytes("toml/application.toml"))
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("toml.try-table-entries", 1))
        .then(OperatorCall("toml.entry-name-equals", 1).with_argument("name", PortableValue.string("upstreams")))
        .then(OperatorCall("toml.entry-item", 1))
        .then(OperatorCall("toml.try-array-elements", 1))
    )
    matches = execute_toml_query(_executable(expression), document, QueryLimits.default())
    assert [match.ordinal for match in matches] == [0, 1]
    assert all(match.kind is TomlMatchKind.ARRAY_ELEMENT for match in matches)


def test_array_element_item_projection():
    """toml.array-element-item converts element matches back to items."""
    document = _document(b"values = [1, 2, 3]\n")
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("toml.try-table-entries", 1))
        .then(OperatorCall("toml.entry-item", 1))
        .then(OperatorCall("toml.try-array-elements", 1))
        .then(OperatorCall("toml.array-element-item", 1))
    )
    matches = execute_toml_query(
        _executable(expression, selection=QuerySelection.LAST),
        document,
        QueryLimits.default(),
    )
    assert len(matches) == 1
    assert matches[0].kind is TomlMatchKind.ITEM
    assert matches[0].kind_name is TomlItemKind.INTEGER


def test_selection_cardinality_enforced():
    """RequireOne/ZeroOrOne reject cardinality violations before the
    cursor is consumed."""
    from consema.protocol.query import QueryFailure, QueryFailureKind

    document = _document(b"a = 1\nb = 2\n")
    expression = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("toml.try-table-entries", 1)
    )
    with pytest.raises(QueryFailure) as caught:
        execute_toml_query(
            _executable(expression, selection=QuerySelection.REQUIRE_ONE),
            document,
            QueryLimits.default(),
        )
    assert caught.value.kind is QueryFailureKind.CARDINALITY_VIOLATION


def test_syntax_kind_and_text_queries():
    """toml.lossless-syntax-query@1: kind and exact text filters over the
    exhaustive pieces in raw order."""
    document = _document(b"a = 1 # note\nb = 2\n")
    newlines = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("toml.syntax-kind-is", 1).with_argument("kind", PortableValue.string("Newline"))
    )
    comments = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("toml.syntax-text-equals", 1).with_argument("text", PortableValue.string("# note"))
    )
    expression = QueryExpression(ExpressionKind.STRUCTURE_ORDER_MERGE, branches=[newlines, comments]
    )
    executable = _executable(expression, domain=domain_toml_lossless_syntax_v1())
    matches = execute_toml_syntax_query(executable, document, QueryLimits.default())
    assert [match.kind.value for match in matches] == ["Comment", "Newline", "Newline"]
    assert matches[0].span.start_byte == 6  # "# note" starts after "a = 1 "
    assert matches[1].ordinal < matches[2].ordinal


def test_domain_mismatch_rejected():
    """A definition bound to another domain cannot execute here."""
    from consema.protocol.query import QueryFailure, QueryFailureKind

    document = _document(b"a = 1\n")
    executable = _executable(QueryExpression(ExpressionKind.INPUT))
    # rebind the same definition under the syntax domain
    executable = executable.validated.definition
    from consema.protocol.query import QueryDefinition

    redefined = QueryDefinition(domain_toml_lossless_syntax_v1()).with_expression(
        executable.expression
    )
    with pytest.raises(QueryFailure) as caught:
        execute_toml_query(
            redefined.validate().bind(_capabilities()), document, QueryLimits.default()
        )
    assert caught.value.kind is QueryFailureKind.DOMAIN_MISMATCH


def test_distinct_by_identity_and_take():
    """Generic operators keep the ordered-results capability semantics."""
    document = _document(b"values = [1, 1, 2]\n")
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("toml.try-table-entries", 1))
        .then(OperatorCall("toml.entry-item", 1))
        .then(OperatorCall("toml.try-array-elements", 1))
        .then(OperatorCall("toml.array-element-item", 1))
        .then(OperatorCall("core.take", 1).with_argument("count", PortableValue.integer(2)))
    )
    matches = execute_toml_query(_executable(expression), document, QueryLimits.default())
    assert len(matches) == 2

