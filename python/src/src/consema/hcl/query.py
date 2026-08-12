"""HCL native-semantic and lossless-syntax query execution (RFC 0014 §7).

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-hcl/src/query.rs:189-222
  (native, hcl.native-semantic-query@1) and query.rs:244-285 (syntax,
  hcl.lossless-syntax-query@1); both profiles share the one syntax system,
  so only the domain identity is guarded.
- Match model: query.rs:51-161 (HclMatch roles Body/Attribute/Block/
  BlockLabel/Expression/TemplatePart/ErrorRegion; HclSyntaxMatch with
  kind/ordinal).
- Operator semantics: query.rs:594-1148 (the full operator table of RFC
  0014 §7.1), 1185-1215 (hcl.syntax-kind-is, hcl.syntax-text-equals),
  1150-1190 (core.take, core.distinct-by-identity).
- Selection algebra: query.rs:302-330 (All/First/Last/ZeroOrOne/RequireOne
  with CardinalityViolation).
- The typed literal accessor family: query.rs:795-860 —
  `hcl.attribute-literal-value@1` with accessors as-string, as-integer,
  as-real, as-boolean-is, as-null-is; a non-literal expression is reported
  as TargetUnavailable (the conformance layer maps it to
  hcl.query.non-literal@1), a type mismatch as RequiredTypeMismatch
  (hcl.query.type-mismatch@1) — crates/consema-conformance/src/hcl_v1.rs:
  658-668.
- Limits and cancellation: the common QueryLimits defaults
  max_steps=100_000, max_results=100_000.

The transferable query model (QueryDomain, QueryExpression, OperatorCall,
QuerySelection, QueryDefinition, ValidatedQuery, ExecutableQuery,
QueryFailure) is the language-neutral one implemented in
consema.protocol.query (RFC 0016 §5.4). This module binds an ExecutableQuery
to one immutable snapshot and produces deterministic ordered matches.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import NodeRef, Span
from consema.hcl.document import HclDocument
from consema.hcl.expression import (
    HclExpression,
    HclExpressionKindName,
    HclTemplatePart,
    is_literal_complete,
    literal_value,
)
from consema.hcl.kinds import HclSyntaxKind
from consema.hcl.native import HclBody, HclErrorRegion
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

NATIVE_DOMAIN_ID = "hcl.native-semantic-query"
SYNTAX_DOMAIN_ID = "hcl.lossless-syntax-query"


class HclMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:51-112)."""

    BODY = "Body"
    ATTRIBUTE = "Attribute"
    BLOCK = "Block"
    BLOCK_LABEL = "BlockLabel"
    EXPRESSION = "Expression"
    TEMPLATE_PART = "TemplatePart"
    ERROR_REGION = "ErrorRegion"


@dataclass(frozen=True, slots=True)
class HclMatch:
    """One snapshot-bound HCL native semantic query match (query.rs:51-112).

    Every match carries a snapshot-bound handle and a reference into the
    immutable native tree of the queried document: the same tree node
    reached through different operators is one match identity.
    """

    kind: HclMatchKind
    node: NodeRef
    body: HclBody | None = None
    attribute: object | None = None
    block: object | None = None
    label: object | None = None
    expression: HclExpression | None = None
    part: HclTemplatePart | None = None
    region: HclErrorRegion | None = None
    position: int | None = None
    name: str | None = None
    text: str | None = None

    @property
    def identity(self) -> NodeRef:
        return self.node


@dataclass(frozen=True, slots=True)
class HclSyntaxMatch:
    """One snapshot-bound lossless syntax query match (query.rs:128-161)."""

    node: NodeRef
    span: Span
    kind: HclSyntaxKind
    ordinal: int


@dataclass(frozen=True, slots=True)
class HclQueryExecution:
    """Complete ordered query result."""

    matches: tuple[object, ...]


class HclCancellationToken:
    """Cooperative cancellation signal."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class HclQueryLimits:
    """Query resource limits (consema-core query.rs defaults
    max_steps=100_000, max_results=100_000)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _NativeContext:
    def __init__(
        self,
        document: HclDocument,
        limits: HclQueryLimits,
        cancellation: HclCancellationToken,
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

    def node_ref(self, node) -> NodeRef:
        return self.document.node_ref(node)


def execute_hcl_native_query(
    executable: ExecutableQuery,
    document: HclDocument,
    limits: HclQueryLimits,
    cancellation: HclCancellationToken,
) -> HclQueryExecution:
    """Executes a validated HCL native semantic query (query.rs:189-222).

    The domain serves both profiles: the two profiles own the one native
    model, so only the domain identity is guarded here.
    """
    definition = executable.definition
    if definition.domain.id != NATIVE_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _NativeContext(document, limits, cancellation)
    root = document.root_body()
    context.step(1)
    input_matches = [
        HclMatch(
            kind=HclMatchKind.BODY,
            node=document.node_ref(root),
            body=root,
        )
    ]
    matches = _execute_native_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return HclQueryExecution(matches=tuple(matches))


def execute_hcl_syntax_query(
    executable: ExecutableQuery,
    document: HclDocument,
    limits: HclQueryLimits,
    cancellation: HclCancellationToken,
) -> HclQueryExecution:
    """Executes a validated HCL lossless syntax query in raw source order
    (query.rs:244-285).

    The lossless index is always present under both profiles because both
    share the one syntax system.
    """
    definition = executable.definition
    if definition.domain.id != SYNTAX_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _NativeContext(document, limits, cancellation)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    context.step(len(pieces))
    input_matches = [
        HclSyntaxMatch(
            node=document.authority.node_ref(ordinal, _syntax_piece_role()),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return HclQueryExecution(matches=tuple(matches))


def _syntax_piece_role():
    from consema.document.structural import NodeRole

    return NodeRole.HCL_SYNTAX_PIECE


def _execute_native_expression(
    expression: QueryExpression,
    input_matches: list[HclMatch],
    context: _NativeContext,
) -> list[HclMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_native_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[HclMatch] = []
        for branch in expression.branches:
            output.extend(_execute_native_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    output = []
    for branch in expression.branches:
        output.extend(_execute_native_expression(branch, input_matches, context))
    output.sort(key=lambda item: item.identity.index)
    context.step(len(output))
    return output


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[HclSyntaxMatch],
    context: _NativeContext,
) -> list[HclSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[HclSyntaxMatch] = []
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
    operator, input_matches: list[HclMatch], context: _NativeContext
) -> list[HclMatch]:
    output: list[HclMatch] = []
    operator_id = operator.id
    if operator_id == "hcl.document-body":
        if input_matches:
            root = context.document.root_body()
            output.append(
                HclMatch(
                    kind=HclMatchKind.BODY,
                    node=context.document.node_ref(root),
                    body=root,
                )
            )
    elif operator_id == "hcl.body-items":
        for item in input_matches:
            if item.kind is not HclMatchKind.BODY:
                continue
            for body_item in item.body.items:
                attribute = body_item.as_attribute()
                if attribute is not None:
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.ATTRIBUTE,
                            node=context.document.node_ref(attribute),
                            attribute=attribute,
                            name=attribute.name,
                        )
                    )
                else:
                    block = body_item.as_block()
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.BLOCK,
                            node=context.document.node_ref(block),
                            block=block,
                            name=block.block_type,
                        )
                    )
    elif operator_id == "hcl.body-attributes":
        for item in input_matches:
            if item.kind is not HclMatchKind.BODY:
                continue
            for body_item in item.body.items:
                attribute = body_item.as_attribute()
                if attribute is not None:
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.ATTRIBUTE,
                            node=context.document.node_ref(attribute),
                            attribute=attribute,
                            name=attribute.name,
                        )
                    )
    elif operator_id == "hcl.body-blocks":
        for item in input_matches:
            if item.kind is not HclMatchKind.BODY:
                continue
            for body_item in item.body.items:
                block = body_item.as_block()
                if block is not None:
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.BLOCK,
                            node=context.document.node_ref(block),
                            block=block,
                            name=block.block_type,
                        )
                    )
    elif operator_id == "hcl.body-block-type-equals":
        expected = _string_argument(operator, "type")
        for item in input_matches:
            if item.kind is not HclMatchKind.BODY:
                continue
            for body_item in item.body.items:
                block = body_item.as_block()
                if block is not None and block.block_type == expected:
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.BLOCK,
                            node=context.document.node_ref(block),
                            block=block,
                            name=block.block_type,
                        )
                    )
    elif operator_id == "hcl.attribute-name":
        output.extend(item for item in input_matches if item.kind is HclMatchKind.ATTRIBUTE)
    elif operator_id == "hcl.attribute-name-equals":
        expected = _string_argument(operator, "name")
        output.extend(
            item
            for item in input_matches
            if item.kind is HclMatchKind.ATTRIBUTE and item.name == expected
        )
    elif operator_id == "hcl.attribute-expression":
        for item in input_matches:
            if item.kind is HclMatchKind.ATTRIBUTE and item.attribute is not None:
                expression = item.attribute.expression
                output.append(
                    HclMatch(
                        kind=HclMatchKind.EXPRESSION,
                        node=context.document.node_ref(expression),
                        expression=expression,
                    )
                )
    elif operator_id == "hcl.attribute-literal-value":
        accessor = _string_argument(operator, "accessor")
        expected = _EXPECTED_LITERAL_KIND[accessor]
        for item in input_matches:
            expression = _expression_payload(item)
            if expression is None:
                continue
            try:
                value = literal_value(expression)
            except Exception:
                raise QueryFailure(QueryFailureKind.TARGET_UNAVAILABLE) from None
            actual = _literal_kind(value)
            if actual != expected:
                raise QueryFailure(
                    QueryFailureKind.REQUIRED_TYPE_MISMATCH,
                    operator=operator_id,
                    argument="accessor",
                    expected_kind=expected,
                )
            output.append(item)
    elif operator_id == "hcl.block-type":
        output.extend(item for item in input_matches if item.kind is HclMatchKind.BLOCK)
    elif operator_id == "hcl.block-type-equals":
        expected = _string_argument(operator, "type")
        output.extend(
            item
            for item in input_matches
            if item.kind is HclMatchKind.BLOCK and item.name == expected
        )
    elif operator_id == "hcl.block-labels":
        for item in input_matches:
            if item.kind is HclMatchKind.BLOCK and item.block is not None:
                for label in item.block.labels:
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.BLOCK_LABEL,
                            node=context.document.node_ref(label),
                            label=label,
                            text=label.text,
                        )
                    )
    elif operator_id == "hcl.block-label-equals":
        expected = _string_argument(operator, "label")
        output.extend(
            item
            for item in input_matches
            if item.kind is HclMatchKind.BLOCK_LABEL and item.text == expected
        )
    elif operator_id == "hcl.block-nested-body":
        for item in input_matches:
            if item.kind is HclMatchKind.BLOCK and item.block is not None:
                body = item.block.body
                output.append(
                    HclMatch(
                        kind=HclMatchKind.BODY,
                        node=context.document.node_ref(body),
                        body=body,
                    )
                )
    elif operator_id == "hcl.expression-kind-is":
        expected = HclExpressionKindName.from_name(_string_argument(operator, "kind"))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator=operator_id,
                argument="kind",
            )
        output.extend(
            item
            for item in input_matches
            if item.kind is HclMatchKind.EXPRESSION
            and item.expression is not None
            and item.expression.kind.name is expected
        )
    elif operator_id == "hcl.expression-is-literal":
        output.extend(
            item
            for item in input_matches
            if item.kind is HclMatchKind.EXPRESSION
            and item.expression is not None
            and is_literal_complete(item.expression)
        )
    elif operator_id == "hcl.expression-text":
        output.extend(item for item in input_matches if item.kind is HclMatchKind.EXPRESSION)
    elif operator_id == "hcl.expression-children":
        for item in input_matches:
            if item.kind is HclMatchKind.EXPRESSION and item.expression is not None:
                for child in item.expression.children():
                    output.append(
                        HclMatch(
                            kind=HclMatchKind.EXPRESSION,
                            node=context.document.node_ref(child),
                            expression=child,
                        )
                    )
    elif operator_id == "hcl.template-parts":
        for item in input_matches:
            if item.kind is not HclMatchKind.EXPRESSION or item.expression is None:
                continue
            if item.expression.kind.name is not HclExpressionKindName.TEMPLATE:
                continue
            parts, _ = item.expression.kind.payload
            for part in parts:
                output.append(
                    HclMatch(
                        kind=HclMatchKind.TEMPLATE_PART,
                        node=context.document.node_ref(part),
                        part=part,
                    )
                )
    elif operator_id == "hcl.tuple-elements":
        for item in input_matches:
            if item.kind is not HclMatchKind.EXPRESSION or item.expression is None:
                continue
            if item.expression.kind.name is not HclExpressionKindName.TUPLE:
                continue
            for element in item.expression.kind.payload:
                output.append(
                    HclMatch(
                        kind=HclMatchKind.EXPRESSION,
                        node=context.document.node_ref(element),
                        expression=element,
                    )
                )
    elif operator_id == "hcl.object-entries":
        for item in input_matches:
            if item.kind is not HclMatchKind.EXPRESSION or item.expression is None:
                continue
            if item.expression.kind.name is not HclExpressionKindName.OBJECT:
                continue
            for entry in item.expression.kind.payload:
                value = entry.value
                output.append(
                    HclMatch(
                        kind=HclMatchKind.EXPRESSION,
                        node=context.document.node_ref(value),
                        expression=value,
                    )
                )
    elif operator_id == "hcl.error-regions":
        if input_matches:
            for position, region in enumerate(context.document.error_regions):
                index = context.document.tree_node_count() + position
                output.append(
                    HclMatch(
                        kind=HclMatchKind.ERROR_REGION,
                        node=context.document.authority.node_ref(
                            index, _error_region_role()
                        ),
                        region=region,
                        position=position,
                        text=region.code,
                    )
                )
    elif operator_id == "core.take":
        count = _integer_argument(operator)
        output.extend(input_matches[:count])
    elif operator_id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.identity not in seen:
                seen.add(item.identity)
                output.append(item)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator_id,
            version=operator.version,
        )
    context.step(len(output))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[HclSyntaxMatch], context: _NativeContext
) -> list[HclSyntaxMatch]:
    output: list[HclSyntaxMatch] = []
    operator_id = operator.id
    if operator_id == "hcl.syntax-kind-is":
        expected = HclSyntaxKind.from_name(_string_argument(operator, "kind"))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator=operator_id,
                argument="kind",
            )
        output.extend(item for item in input_matches if item.kind is expected)
    elif operator_id == "hcl.syntax-text-equals":
        expected = _string_argument(operator, "text").encode("utf-8")
        raw = context.document.source.bytes()
        output.extend(
            item
            for item in input_matches
            if raw[item.span.start_byte : item.span.end_byte] == expected
        )
    elif operator_id == "core.take":
        count = _integer_argument(operator)
        output.extend(input_matches[:count])
    elif operator_id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.node not in seen:
                seen.add(item.node)
                output.append(item)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator_id,
            version=operator.version,
        )
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


def _expression_payload(item: HclMatch) -> HclExpression | None:
    if item.kind is HclMatchKind.EXPRESSION:
        return item.expression
    if item.kind is HclMatchKind.ATTRIBUTE and item.attribute is not None:
        return item.attribute.expression
    return None


def _literal_kind(value) -> str:
    kind = value.kind
    if kind == "integer":
        return "Integer"
    if kind == "real":
        return "Decimal"
    if kind == "string":
        return "String"
    if kind == "boolean":
        return "Boolean"
    if kind == "null":
        return "Null"
    if kind == "tuple":
        return "Sequence"
    return "Object"


_EXPECTED_LITERAL_KIND = {
    "as-string": "String",
    "as-integer": "Integer",
    "as-real": "Decimal",
    "as-boolean-is": "Boolean",
    "as-null-is": "Null",
}


def _error_region_role():
    from consema.document.structural import NodeRole

    return NodeRole.HCL_ERROR_REGION


def _string_argument(operator, name: str) -> str:
    value = operator.arguments.get(name)
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT,
            operator=operator.id,
            argument=name,
        )
    return value.as_string()


def _integer_argument(operator) -> int:
    value = operator.arguments.get("count")
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT,
            operator=operator.id,
            argument="count",
        )
    return value.as_integer()
