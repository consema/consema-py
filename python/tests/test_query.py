"""Intent documents for query definition validation/binding.

The domain/operator table is the complete language-neutral table of
crates/consema-core/src/query.rs:899-1897. The role-mismatch rejection is
pinned by conformance/vectors/v1.json `query.reject-role-mismatch`.
"""

import pytest

from consema.core import PortableValue
from consema.protocol import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    MatchRole,
    OperatorCall,
    QueryDefinition,
    QueryDefinitionCodec,
    QueryDomain,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
    domain_portable_value_v1,
)

ROLE_VALUE = MatchRole.VALUE
ROLE_OBJECT_ENTRY = MatchRole.OBJECT_ENTRY


def _operator(id: str, **arguments) -> OperatorCall:
    call = OperatorCall(id, 1)
    for name, value in arguments.items():
        call.with_argument(name, value)
    return call


def test_valid_definition_round_trips_through_protocol_value():
    # conformance/vectors/v1.json query.protocol-roundtrip.
    definition = (
        QueryDefinition(domain_portable_value_v1())
        .with_expression(
            QueryExpression(ExpressionKind.INPUT).then(
                _operator("core.try-sequence-elements")
            )
        )
        .with_selection(QuerySelection.FIRST)
    )
    value = QueryDefinitionCodec.to_value(definition)
    decoded = QueryDefinitionCodec.from_value(value)
    assert decoded.domain == definition.domain
    assert decoded.selection is QuerySelection.FIRST
    validated = decoded.validate()
    assert validated.output_role is ROLE_VALUE
    assert validated.required_capabilities == [CapabilityId("core.query.ordered-results", 1)]


def test_role_mismatch_vector():
    # conformance/vectors/v1.json query.reject-role-mismatch: the
    # core.object-entry-value operator requires ObjectEntry input, but the
    # domain root input role is Value.
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(_operator("core.object-entry-value"))
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_OPERATOR_COMPOSITION
    assert caught.value.code == "core.query.invalid-composition@1"


def test_domain_mismatch():
    definition = QueryDefinition(QueryDomain("example.unknown", 1))
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.DOMAIN_MISMATCH
    assert caught.value.code == "core.query.domain-mismatch@1"


def test_unknown_operator_and_wrong_argument_type():
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(_operator("core.unknown-op"))
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.UNKNOWN_OPERATOR

    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(
            _operator("core.where-type", kind=PortableValue.integer(3))
        )
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.WRONG_ARGUMENT_TYPE
    assert caught.value.argument == "kind"
    assert caught.value.expected_kind == "String"


def test_where_type_accepts_the_frozen_fifteen_kind_names():
    for kind_name in (
        "Null", "Boolean", "Integer", "Decimal", "BinaryFloat32", "BinaryFloat64",
        "String", "Bytes", "Date", "Time", "LocalDateTime", "OffsetDateTime",
        "Sequence", "Object", "EntryMapping",
    ):
        definition = QueryDefinition(domain_portable_value_v1()).with_expression(
            QueryExpression(ExpressionKind.INPUT).then(
                _operator("core.where-type", kind=PortableValue.string(kind_name))
            )
        )
        assert definition.validate().output_role is ROLE_VALUE
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(
            _operator("core.where-type", kind=PortableValue.string("Mystery"))
        )
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT


def test_operator_version_must_be_1():
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(OperatorCall("core.take", 2))
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.UNKNOWN_OPERATOR
    assert caught.value.version == 2


def test_take_requires_a_nonnegative_count():
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(
            _operator("core.take", count=PortableValue.integer(-1))
        )
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT
    assert caught.value.argument == "count"


def test_concat_branches_must_agree_on_output_role():
    input_expression = QueryExpression(ExpressionKind.INPUT)
    definition = QueryDefinition(domain_portable_value_v1()).with_expression(
        QueryExpression(
            ExpressionKind.CONCAT,
            branches=[
                input_expression.then(_operator("core.try-sequence-elements")),
                input_expression.then(_operator("core.try-object-entries")),
            ],
        )
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_OPERATOR_COMPOSITION


def test_bind_requires_ordered_results_capability():
    definition = QueryDefinition(domain_portable_value_v1())
    validated = definition.validate()
    empty = CapabilitySet()
    with pytest.raises(QueryFailure) as caught:
        validated.bind(empty)
    assert caught.value.kind is QueryFailureKind.MISSING_CAPABILITY
    assert caught.value.capability == CapabilityId("core.query.ordered-results", 1)
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    assert validated.bind(capabilities).output_role is ROLE_VALUE


def test_syntax_kind_vocabularies_are_closed():
    # json.lossless-syntax-query@2 accepts the Identifier kind; v1 does not
    # (query_validate.go isJSONSyntaxKind).
    from consema.protocol import domain_json_lossless_syntax_v2, domain_json_lossless_syntax_v1

    for version, domain in ((1, domain_json_lossless_syntax_v1()), (2, domain_json_lossless_syntax_v2())):
        definition = QueryDefinition(domain).with_expression(
            QueryExpression(ExpressionKind.INPUT).then(
                _operator("json.syntax-kind-is", kind=PortableValue.string("Identifier"))
            )
        )
        if version == 2:
            assert definition.validate().output_role is MatchRole.JSON_SYNTAX_PIECE
        else:
            with pytest.raises(QueryFailure) as caught:
                definition.validate()
            assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT


def test_graph_and_yaml_where_rows():
    from consema.protocol import domain_portable_graph_v1, domain_yaml_native_v1

    definition = QueryDefinition(domain_portable_graph_v1()).with_expression(
        QueryExpression(ExpressionKind.INPUT).then(
            _operator("graph.where-kind", kind=PortableValue.string("Mapping"))
        )
    )
    assert definition.validate().output_role is MatchRole.GRAPH_NODE

    # yaml.where-tag expects a YamlNode input: first descend documents →
    # document-root, then apply the row; an empty tag is an invalid argument.
    input_expression = QueryExpression(ExpressionKind.INPUT)
    definition = QueryDefinition(domain_yaml_native_v1()).with_expression(
        input_expression.then(_operator("yaml.documents"))
        .then(_operator("yaml.document-root"))
        .then(_operator("yaml.where-tag", tag=PortableValue.string("")))
    )
    with pytest.raises(QueryFailure) as caught:
        definition.validate()
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT
    assert caught.value.argument == "tag"
