"""Shared HCL test helpers: query executable construction and diagnostics.

The executable helper mirrors the JSON family's pattern
(tests/json/test_query.py:41-52): a definition is validated against the
language-neutral operator table of consema.protocol.query and bound with
the core ordered-results capability (RFC 0016 §5.4).
"""

from __future__ import annotations

from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryDomain,
    QueryExpression,
)


def capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def executable(domain_id: str, version: int, operators: list[OperatorCall]):
    """Validates and binds one query definition over one ordered operator
    chain."""
    definition = QueryDefinition(QueryDomain(domain_id, version))
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    return definition.with_expression(expression).validate().bind(capabilities())


def diagnostic_codes(document) -> list[str]:
    """Ordered diagnostic codes of one formed document."""
    return [diagnostic.code for diagnostic in document.diagnostics]


def syntax_kind_names(document) -> list[str]:
    return [kind.value for kind in document.lossless_syntax_kinds()]
