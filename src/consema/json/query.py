"""JSON native-semantic and lossless-syntax query execution.

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-json/src/query.rs:91-125
  (native) and 142-183 (syntax) — domains json.native-semantic-query@1/2
  and json.lossless-syntax-query@1/2; JSON5 documents require version 2
  (query.rs:100-104, 151-155; RFC 0005 §7, docs/rfcs/0005-...:152-172).
- Operators: query.rs:307-356 (syntax: json.syntax-kind-is,
  json.syntax-text-equals, core.take, core.distinct-by-identity) and
  query.rs:358-477 (native: json.try-object-members, json.member-name-
  equals, json.member-value, json.try-array-elements, json.array-element-
  value, core.take, core.distinct-by-identity).
- Expression evaluation and StructureOrderMerge: query.rs:230-305.
- Selection algebra: query.rs:479-496 (All/First/Last/ZeroOrOne/RequireOne
  with CardinalityViolation).
- Limits and cancellation: consema-core/src/query.rs:2967-2981 (QueryLimits
  defaults max_steps=100_000, max_results=100_000); the step accounting
  query.rs:204-213.
- Failure codes: core.query.*@1 (crates/consema-protocol/src/
  error_registry.rs:108-118) via consema.protocol.query.QueryFailure.

The transferable query model (QueryDomain, QueryExpression, OperatorCall,
QuerySelection, QueryDefinition, ValidatedQuery, ExecutableQuery,
QueryFailure) is the language-neutral one implemented in
consema.protocol.query (RFC 0016 §5.4; RFC 0011 §8 domain/role pattern).
This module binds an ExecutableQuery to one immutable snapshot and produces
deterministic ordered matches (RFC 0016 §5.4: match count/identity/order).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import NodeRef, NodeRole, Span
from consema.json.document import (
    JsonArrayElement,
    JsonDocument,
    JsonObjectMember,
    JsonValue,
)
from consema.json.kinds import JsonProfile, JsonSyntaxKind
from consema.json.parser import InternalKind
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

NATIVE_DOMAIN_ID = "json.native-semantic-query"
SYNTAX_DOMAIN_ID = "json.lossless-syntax-query"


class JsonMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:11-43)."""

    VALUE = "Value"
    OBJECT_MEMBER = "ObjectMember"
    ARRAY_ELEMENT = "ArrayElement"


@dataclass(frozen=True, slots=True)
class JsonMatch:
    """One snapshot-bound native semantic query match (query.rs:11-43)."""

    kind: JsonMatchKind
    node: NodeRef
    ordinal: int | None = None
    name: str | None = None
    key: NodeRef | None = None
    value: NodeRef | None = None

    @property
    def identity(self) -> NodeRef:
        return self.node


@dataclass(frozen=True, slots=True)
class JsonSyntaxMatch:
    """One snapshot-bound lossless syntax query match (query.rs:55-88)."""

    node: NodeRef
    span: Span
    kind: JsonSyntaxKind
    ordinal: int


@dataclass(frozen=True, slots=True)
class JsonQueryExecution:
    """Complete ordered query result (query.rs:90-125, 142-183)."""

    matches: tuple[object, ...]


class JsonCancellationToken:
    """Cooperative cancellation signal (query.rs:204-213; consema-core)."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class JsonQueryLimits:
    """Query resource limits (consema-core/src/query.rs:2967-2981)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _NativeContext:
    def __init__(
        self,
        document: JsonDocument,
        limits: JsonQueryLimits,
        cancellation: JsonCancellationToken,
    ) -> None:
        self.document = document
        self.limits = limits
        self.cancellation = cancellation
        self.steps = 0

    def step(self, results: int) -> None:
        if self.cancellation.is_cancelled():
            raise QueryFailure(QueryFailureKind.CANCELLED)
        self.steps += 1
        if self.steps > self.limits.max_steps or results > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)

    def value_match(self, index: int) -> JsonMatch:
        value = JsonValue(self.document, index)
        return JsonMatch(
            kind=JsonMatchKind.VALUE,
            node=value.node_ref(),
            value=value.node_ref(),
        )


def execute_json_query(
    executable: ExecutableQuery,
    document: JsonDocument,
    limits: JsonQueryLimits,
    cancellation: JsonCancellationToken,
) -> JsonQueryExecution:
    """Executes a validated JSON native semantic query (query.rs:91-125)."""
    definition = executable.definition
    version = definition.domain.version
    if (
        definition.domain.id != NATIVE_DOMAIN_ID
        or version not in (1, 2)
        or (document.profile is JsonProfile.JSON5_STANDARD_V1 and version != 2)
    ):
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _NativeContext(document, limits, cancellation)
    root = document.root()
    context.step(1)
    availability = root.kind()
    kind = availability.value if availability.is_available else None
    input_matches = [
        JsonMatch(kind=JsonMatchKind.VALUE, node=root.node_ref(), value=root.node_ref())
    ]
    matches = _execute_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return JsonQueryExecution(matches=tuple(matches))


def execute_json_syntax_query(
    executable: ExecutableQuery,
    document: JsonDocument,
    limits: JsonQueryLimits,
    cancellation: JsonCancellationToken,
) -> JsonQueryExecution:
    """Executes a validated JSON lossless syntax query (query.rs:142-183)."""
    definition = executable.definition
    version = definition.domain.version
    if (
        definition.domain.id != SYNTAX_DOMAIN_ID
        or version not in (1, 2)
        or (document.profile is JsonProfile.JSON5_STANDARD_V1 and version != 2)
    ):
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _NativeContext(document, limits, cancellation)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    context.step(len(pieces))
    input_matches = [
        JsonSyntaxMatch(
            node=document.authority.node_ref(ordinal, NodeRole.JSON_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return JsonQueryExecution(matches=tuple(matches))


def _execute_expression(
    expression: QueryExpression,
    input_matches: list[JsonMatch],
    context: _NativeContext,
) -> list[JsonMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[JsonMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: source-order by (start, end, entity index)
    # (query.rs:252-269).
    output = []
    for branch in expression.branches:
        output.extend(_execute_expression(branch, input_matches, context))
    output.sort(
        key=lambda item: (
            context.document.span(item.identity.index).start_byte,
            context.document.span(item.identity.index).end_byte,
            item.identity.index,
        )
    )
    context.step(len(output))
    return output


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[JsonSyntaxMatch],
    context: _NativeContext,
) -> list[JsonSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[JsonSyntaxMatch] = []
        for branch in expression.branches:
            output.extend(_execute_syntax_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    output = []
    for branch in expression.branches:
        output.extend(_execute_syntax_expression(branch, input_matches, context))
    output.sort(key=lambda item: (item.span.start_byte, item.span.end_byte, item.ordinal))
    context.step(len(output))
    return output


def _apply_operator(
    operator, input_matches: list[JsonMatch], context: _NativeContext
) -> list[JsonMatch]:
    output: list[JsonMatch] = []
    if operator.id == "json.try-object-members":
        for item in input_matches:
            if item.kind is not JsonMatchKind.VALUE:
                continue
            index = item.identity.index
            internal = context.document.value_entity(index).internal
            if internal.kind is not InternalKind.OBJECT:
                continue
            for member_index in internal.payload:
                member = JsonObjectMember(context.document, member_index)
                availability = member.name()
                output.append(
                    JsonMatch(
                        kind=JsonMatchKind.OBJECT_MEMBER,
                        node=member.node_ref(),
                        ordinal=member.ordinal(),
                        name=availability.value if availability.is_available else None,
                        key=member.key_node_ref(),
                        value=member.value_node_ref(),
                    )
                )
    elif operator.id == "json.member-name-equals":
        expected = _string_argument(operator)
        output.extend(
            item
            for item in input_matches
            if item.kind is JsonMatchKind.OBJECT_MEMBER
            and item.name is not None
            and item.name == expected
        )
    elif operator.id == "json.member-value":
        for item in input_matches:
            if item.kind is JsonMatchKind.OBJECT_MEMBER:
                output.append(context.value_match(item.value.index))
    elif operator.id == "json.try-array-elements":
        for item in input_matches:
            if item.kind is not JsonMatchKind.VALUE:
                continue
            index = item.identity.index
            internal = context.document.value_entity(index).internal
            if internal.kind is not InternalKind.ARRAY:
                continue
            for element_index in internal.payload:
                element = JsonArrayElement(context.document, element_index)
                output.append(
                    JsonMatch(
                        kind=JsonMatchKind.ARRAY_ELEMENT,
                        node=element.node_ref(),
                        ordinal=element.ordinal(),
                        value=element.value_node_ref(),
                    )
                )
    elif operator.id == "json.array-element-value":
        for item in input_matches:
            if item.kind is JsonMatchKind.ARRAY_ELEMENT:
                output.append(context.value_match(item.value.index))
    elif operator.id == "core.take":
        count = _integer_argument(operator)
        output.extend(input_matches[:count])
    elif operator.id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.identity not in seen:
                seen.add(item.identity)
                output.append(item)
    else:
        raise QueryFailure(QueryFailureKind.UNKNOWN_OPERATOR, operator=operator.id, version=operator.version)
    context.step(len(output))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[JsonSyntaxMatch], context: _NativeContext
) -> list[JsonSyntaxMatch]:
    output: list[JsonSyntaxMatch] = []
    if operator.id == "json.syntax-kind-is":
        expected = JsonSyntaxKind.from_name(_string_argument(operator))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator=operator.id,
                argument="kind",
            )
        output.extend(item for item in input_matches if item.kind is expected)
    elif operator.id == "json.syntax-text-equals":
        expected = _string_argument(operator).encode("utf-8")
        raw = context.document.source.bytes()
        output.extend(
            item
            for item in input_matches
            if raw[item.span.start_byte : item.span.end_byte] == expected
        )
    elif operator.id == "core.take":
        count = _integer_argument(operator)
        output.extend(input_matches[:count])
    elif operator.id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.node not in seen:
                seen.add(item.node)
                output.append(item)
    else:
        raise QueryFailure(QueryFailureKind.UNKNOWN_OPERATOR, operator=operator.id, version=operator.version)
    context.step(len(output))
    return output


def _apply_selection(values: list[object], selection: QuerySelection) -> list[object]:
    if selection is QuerySelection.ALL:
        return values
    if selection is QuerySelection.FIRST:
        return values[:1]
    if selection is QuerySelection.LAST:
        return values[-1:]
    if selection is QuerySelection.ZERO_OR_ONE:
        if len(values) <= 1:
            return values
        raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
    if selection is QuerySelection.REQUIRE_ONE:
        if len(values) == 1:
            return values
        raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
    return values


def _string_argument(operator) -> str:
    for name in ("name", "kind", "text"):
        value = operator.arguments.get(name)
        if value is not None:
            return value.as_string()
    raise QueryFailure(
        QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="value"
    )


def _integer_argument(operator) -> int:
    value = operator.arguments.get("count")
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="count"
        )
    return value.as_integer()
