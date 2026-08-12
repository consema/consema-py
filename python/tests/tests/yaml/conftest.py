"""Shared YAML test fixtures: parse helpers and query builders.

The tests are intent documents: every golden value is transcribed from
conformance/vectors/yaml-v1.json with the case id cited in the test
docstring. The toolchain runs pytest after the L0/L1 skeleton gates
(docs/multi-language-implementation-plan.md s3/s7).
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryDomain,
    QueryExpression,
)
from consema.yaml import (
    YamlCancellationToken,
    YamlProfile,
    YamlQueryLimits,
    parse,
)

DEFAULT_LIMITS = ParseLimits()
QUERY_LIMITS = YamlQueryLimits()


def parse_source(source: str, profile: YamlProfile, limits: ParseLimits = DEFAULT_LIMITS):
    """Forms one UTF-8 YAML source under the vector profile."""
    return parse(source.encode("utf-8"), profile, limits)


def capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def executable_from_pipeline(domain_id: str, pipeline: list[str]):
    """Builds an executable from a vector pipeline of ``op@version`` ids
    (conformance/vectors/yaml-v1.json ``pipeline`` fields)."""
    operators = []
    for entry in pipeline:
        name, version = entry.rsplit("@", 1)
        operators.append(OperatorCall(name, int(version)))
    definition = QueryDefinition(QueryDomain(domain_id, 1))
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    return definition.with_expression(expression).validate().bind(capabilities())


def cancellation() -> YamlCancellationToken:
    return YamlCancellationToken()


@pytest.fixture
def limits() -> ParseLimits:
    return DEFAULT_LIMITS
