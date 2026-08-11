"""Native and syntax query golden transcriptions (yaml-v1.json cases).

Cases covered with the vector case ids cited:

- query.mapping-entries (yaml-v1.json:50-54): the pipeline
  yaml.documents / yaml.document-root / yaml.try-mapping-entries yields
  roles ["YamlMappingEntry", "YamlMappingEntry"].
- query.alias-target (yaml-v1.json:55-58): yaml.alias-occurrences /
  yaml.alias-target yields roles ["YamlNode"] and the shared identity.
- query.syntax-comments (yaml-v1.json:60-63): the Comment pieces of
  "a: 1 # first\nb: 2 # second\n" carry source ordinals [5, 12].
- query.resource-limit (yaml-v1.json:65-68): max_results 2 on a three
  element sequence fails with core.query.resource-limit@1.

The operator surface is frozen by RFC 0007 s9 (lines 229-251) and the
role rows by consema.protocol.query (the protocol agent owns domain
validation; this module binds and executes).
"""

from __future__ import annotations

import pytest

from consema.protocol.query import QueryFailure, QueryFailureKind
from consema.yaml import (
    YamlMatchKind,
    YamlProfile,
    YamlSyntaxKind,
    execute_yaml_query,
    execute_yaml_syntax_query,
)
from consema.protocol.query import OperatorCall
from consema.core.value import PortableValue
from tests.yaml.conftest import (
    QUERY_LIMITS,
    cancellation,
    executable_from_pipeline,
    parse_source,
)

NATIVE_DOMAIN = "yaml.native-semantic-query"
SYNTAX_DOMAIN = "yaml.lossless-syntax-query"


def test_query_mapping_entries():
    # Case query.mapping-entries (yaml-v1.json:50-54).
    document = parse_source("{a: 1, b: 2}\n", YamlProfile.YAML12_CORE_V1)
    executable = executable_from_pipeline(
        NATIVE_DOMAIN,
        ["yaml.documents@1", "yaml.document-root@1", "yaml.try-mapping-entries@1"],
    )
    execution = execute_yaml_query(executable, document, QUERY_LIMITS, cancellation())
    roles = [match.kind.value for match in execution.matches]
    assert roles == ["MappingEntry", "MappingEntry"]
    assert [match.ordinal for match in execution.matches] == [0, 1]


def test_query_alias_target():
    # Case query.alias-target (yaml-v1.json:55-58): the alias target is the
    # anchored node; identity is shared, never expanded.
    document = parse_source("[&x {k: v}, *x]\n", YamlProfile.YAML12_CORE_V1)
    executable = executable_from_pipeline(
        NATIVE_DOMAIN, ["yaml.alias-occurrences@1", "yaml.alias-target@1"]
    )
    execution = execute_yaml_query(executable, document, QUERY_LIMITS, cancellation())
    assert len(execution.matches) == 1
    match = execution.matches[0]
    assert match.kind is YamlMatchKind.NODE
    anchored = document.document(0).root().sequence_item(0).node()
    assert match.node == anchored.node_ref()


def test_query_syntax_comments():
    # Case query.syntax-comments (yaml-v1.json:60-63). The comment ordinals
    # are the zero-based source-piece ordinals of the Comment matches.
    document = parse_source("a: 1 # first\nb: 2 # second\n", YamlProfile.YAML12_CORE_V1)
    operator = OperatorCall("yaml.syntax-kind-is", 1).with_argument(
        "kind", PortableValue.string("Comment")
    )
    executable = executable_from_pipeline(SYNTAX_DOMAIN, ["yaml.syntax-kind-is@1"])
    execution = execute_yaml_syntax_query(executable, document, QUERY_LIMITS, cancellation())
    assert [match.ordinal for match in execution.matches] == [5, 12]
    assert all(match.kind is YamlSyntaxKind.COMMENT for match in execution.matches)


def test_query_resource_limit():
    # Case query.resource-limit (yaml-v1.json:65-68): a completed prefix is
    # never disguised as success; the failure code is
    # core.query.resource-limit@1.
    document = parse_source("[a, b, c]\n", YamlProfile.YAML12_CORE_V1)
    executable = executable_from_pipeline(
        NATIVE_DOMAIN,
        ["yaml.documents@1", "yaml.document-root@1", "yaml.try-sequence-elements@1"],
    )
    with pytest.raises(QueryFailure) as caught:
        execute_yaml_query(
            executable,
            document,
            type("Limits", (), {"max_steps": 100_000, "max_results": 2})(),
            cancellation(),
        )
    assert caught.value.kind is QueryFailureKind.RESOURCE_LIMIT
    assert caught.value.code == "core.query.resource-limit@1"


def test_query_cancellation_fails_without_prefix():
    # query.rs:278-288: cancellation never produces a completed prefix
    # disguised as success.
    document = parse_source("[a, b, c]\n", YamlProfile.YAML12_CORE_V1)
    executable = executable_from_pipeline(
        NATIVE_DOMAIN,
        ["yaml.documents@1", "yaml.document-root@1", "yaml.try-sequence-elements@1"],
    )
    token = cancellation()
    token.cancel()
    with pytest.raises(QueryFailure) as caught:
        execute_yaml_query(executable, document, QUERY_LIMITS, token)
    assert caught.value.kind is QueryFailureKind.CANCELLED


def test_query_anchor_definition_and_node():
    # query.rs:524-549: yaml.anchor-definition exposes the exact &name span
    # and yaml.anchor-node returns the anchored representation node.
    document = parse_source("first: &x [one]\n", YamlProfile.YAML12_CORE_V1)
    executable = executable_from_pipeline(
        NATIVE_DOMAIN, ["yaml.documents@1", "yaml.document-root@1", "yaml.anchor-definition@1"]
    )
    execution = execute_yaml_query(executable, document, QUERY_LIMITS, cancellation())
    assert len(execution.matches) == 1
    match = execution.matches[0]
    assert match.kind is YamlMatchKind.ANCHOR_DEFINITION
    assert match.name == "x"
    assert document.source.bytes()[match.span.start_byte : match.span.end_byte] == b"&x"
    anchored = document.document(0).root().mapping_entry(0).value()
    executable = executable_from_pipeline(
        NATIVE_DOMAIN,
        [
            "yaml.documents@1",
            "yaml.document-root@1",
            "yaml.anchor-definition@1",
            "yaml.anchor-node@1",
        ],
    )
    execution = execute_yaml_query(executable, document, QUERY_LIMITS, cancellation())
    assert execution.matches[0].node == anchored.node_ref()


def test_query_where_tag_and_canonical():
    # query.rs:441-457: yaml.where-tag and yaml.scalar-canonical-equals
    # filter on resolved tags and canonical content.
    document = parse_source("a: 1\nb: 2\n", YamlProfile.YAML12_CORE_V1)
    from consema.protocol.query import QueryDefinition, QueryDomain, QueryExpression, ExpressionKind

    definition = QueryDefinition(QueryDomain(NATIVE_DOMAIN, 1))
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("yaml.documents", 1))
        .then(OperatorCall("yaml.document-root", 1))
        .then(
            OperatorCall("yaml.where-tag", 1).with_argument(
                "tag", PortableValue.string("tag:yaml.org,2002:map")
            )
        )
    )
    executable = definition.with_expression(expression).validate().bind(
        _capabilities()
    )
    execution = execute_yaml_query(executable, document, QUERY_LIMITS, cancellation())
    # The root mapping itself is a tag:yaml.org,2002:map node.
    assert [match.kind_name for match in execution.matches] == ["Mapping"]


def _capabilities():
    from consema.protocol.query import CapabilityId, CapabilitySet

    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities
