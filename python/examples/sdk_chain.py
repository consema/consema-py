"""Consema SDK chain example (Python): one JSON document through the full SDK
surface — parse, native semantic query, best-exact projection, structural
edit, canonical materialization, and cross-format conversion to TOML.

Scenario: read `{"a":1,"b":{"c":2}}` under `json.strict`, query `b.c`
(`json.native-semantic-query@1`), project
`json.projection.best-exact-core@1`, edit `a` to `42` (semantic scalar
replacement, `CanonicalForProfile` representation), materialize the edited
value as canonical compact JSON, and convert the edited document to TOML
(`toml.canonical-document`).

Run: `cd python && PYTHONPATH=src python examples/sdk_chain.py`

Language-neutral contract reference (consema spec repository):
  - https://github.com/consema/consema/blob/main/docs/cookbook.md — the CLI recipes for the same operations
  - https://github.com/consema/consema/blob/main/docs/multi-language-implementation-plan.md — the five-language SDK design
  https://github.com/consema/consema/blob/main/docs/cookbook.md
"""

from consema import ConversionFailure, convert_json
from consema.core import PortableValue
from consema.document import (
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationRequest,
    MaterializationStyleId,
    NewlinePolicy,
    ProfileId,
)
from consema.json import (
    CompleteProjection,
    EditTransactionBuilder,
    FailedProjectionAttempt,
    JsonCancellationToken,
    JsonQueryLimits,
    ProjectionRequestBuilder,
    ProjectionTarget,
    RepresentationPolicy,
    commit,
    execute_json_query,
    materialize,
    project,
)
from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryExpression,
    QuerySelection,
    domain_json_native_v1,
)
from consema.registry import parse_document


def member_value_ref(value, name: str):
    """Returns the value of one object member by decoded name, walking
    ``object_members()`` with an explicit SemanticAvailability pattern match."""
    members = value.object_members()
    if not members.is_available:
        raise RuntimeError(f"semantics unavailable: {members.reason}")
    if members.value is None:
        raise RuntimeError("value is not an object")
    for member in members.value:
        member_name = member.name()
        if member_name.is_available and member_name.value == name:
            return member.value()
    raise RuntimeError(f"member '{name}' not found")


def project_to_json(json_document, projection_request, compact_request) -> bytes:
    """Projects one JSON document and renders its value as canonical compact
    JSON bytes."""
    result = project(json_document, projection_request)
    if isinstance(result, FailedProjectionAttempt):
        raise RuntimeError(f"projection failed: {result.diagnostics}")
    materialized = materialize(result.value, compact_request)
    if isinstance(materialized, FailedMaterializationAttempt):
        raise RuntimeError(f"materialization failed: {materialized.failure.code}")
    return materialized.document.render()


def main() -> None:
    source = b'{"a":1,"b":{"c":2}}'
    profile = ProfileId.new("json.strict", 1)

    # 1. Parse under the exact profile through the single facade parse entry.
    document = parse_document(source, profile)
    status = document.formation_status()
    if status.value != "Complete":
        raise RuntimeError(f"expected a Complete document, got {status.value}")
    print(
        f"parse: profile={document.profile().id} status={status.value} "
        f"render={document.render().decode('utf-8')}"
    )
    json_document = document.as_json()
    if json_document is None:
        raise RuntimeError("source is not a JSON document")

    # 2. Query `b.c` through the JSON native semantic domain.
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("json.try-object-members", 1))
        .then(OperatorCall("json.member-name-equals", 1).with_argument("name", PortableValue.string("b")))
        .then(OperatorCall("json.member-value", 1))
        .then(OperatorCall("json.try-object-members", 1))
        .then(OperatorCall("json.member-name-equals", 1).with_argument("name", PortableValue.string("c")))
        .then(OperatorCall("json.member-value", 1))
    )
    definition = (
        QueryDefinition(domain_json_native_v1())
        .with_expression(expression)
        .with_selection(QuerySelection.REQUIRE_ONE)
    )
    validated = definition.validate()
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    executable = validated.bind(capabilities)
    execution = execute_json_query(
        executable, json_document, JsonQueryLimits(), JsonCancellationToken()
    )
    # Render the matched value through the semantic tree API (the same walk
    # the edit target below uses).
    c_value = member_value_ref(member_value_ref(json_document.root(), "b"), "c")
    kind = "?"
    value = "?"
    kind_availability = c_value.kind()
    if kind_availability.is_available and kind_availability.value is not None:
        kind = kind_availability.value.value
    integer_availability = c_value.as_integer()
    if integer_availability.is_available and integer_availability.value is not None:
        value = str(integer_availability.value)
    print(f"query b.c: matches={len(execution.matches)} value={value} kind={kind}")

    # 3. Project the document with the conservative best-exact core target.
    projection_request = ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build()
    compact_request = (
        MaterializationRequest.new(
            ProfileId.new("json.strict", 1),
            MaterializationStyleId.new("json.canonical-compact", 1),
        ).with_newline(NewlinePolicy.NONE)
    )
    print(
        f"project json.projection.best-exact-core@1: fidelity=Exact "
        f"value={project_to_json(json_document, projection_request, compact_request).decode('utf-8')}"
    )

    # 4. Edit `a` to 42 with a semantic scalar replacement under the
    #    profile-canonical representation policy.
    a_value = member_value_ref(json_document.root(), "a")
    builder = EditTransactionBuilder(json_document)
    builder.semantic_scalar(
        a_value.node_ref(),
        PortableValue.integer(42),
        RepresentationPolicy.CANONICAL_FOR_PROFILE,
    )
    commit_result = commit(json_document, builder.build())
    edited = commit_result.document
    print(
        f"edit a->42 semantic_scalar CanonicalForProfile: "
        f"render={edited.render().decode('utf-8')}"
    )

    # 5. Materialize the edited value as canonical compact JSON.
    print(
        f"materialize json.canonical-compact: "
        f"{project_to_json(edited, projection_request, compact_request).decode('utf-8')}"
    )

    # 6. Convert the edited JSON document to TOML (two-stage composition).
    toml_request = (
        MaterializationRequest.new(
            ProfileId.new("toml.1.0", 1),
            MaterializationStyleId.new("toml.canonical-document", 1),
        ).with_mapping_policy(MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT)
    )
    conversion = convert_json(edited, projection_request, toml_request)
    if isinstance(conversion, ConversionFailure):
        raise RuntimeError(f"conversion failed: {conversion.kind}")
    print("convert to toml.canonical-document:")
    print(conversion.document.render().decode("utf-8"), end="")


if __name__ == "__main__":
    main()
