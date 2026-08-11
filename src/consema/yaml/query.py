"""YAML native-semantic and lossless-syntax query execution.

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-yaml/src/query.rs:167-197
  (native) and 213-255 (syntax) — domains yaml.native-semantic-query@1 and
  yaml.lossless-syntax-query@1 (RFC 0007 s9, lines 229-251).
- Native operators: query.rs:394-596 (yaml.documents, yaml.document-root,
  yaml.where-node-kind, yaml.where-tag, yaml.scalar-canonical-equals,
  yaml.try-sequence-elements, yaml.sequence-element-node,
  yaml.try-mapping-entries, yaml.mapping-entry-key, yaml.mapping-entry-value,
  yaml.anchor-definition, yaml.anchor-node, yaml.alias-occurrences,
  yaml.alias-target, core.take, core.distinct-by-identity).
- Syntax operators: query.rs:598-649 (yaml.syntax-kind-is,
  yaml.syntax-text-equals, core.take, core.distinct-by-identity), with the
  encoded-text comparison query.rs:651-660.
- Expression evaluation and StructureOrderMerge: query.rs:313-392; selection
  algebra query.rs:690-707; limits and cancellation query.rs:278-288.
- Failure codes: core.query.*@1 (crates/consema-protocol/src/error_registry.rs:108-118)
  via consema.protocol.query.QueryFailure.
- Vector surface: conformance/vectors/yaml-v1.json cases query.mapping-entries
  (roles), query.alias-target (roles), query.syntax-comments (ordinals),
  query.resource-limit (core.query.resource-limit@1).

The transferable query model (QueryDomain, QueryExpression, OperatorCall,
QuerySelection, QueryDefinition, ValidatedQuery, ExecutableQuery,
QueryFailure) is the language-neutral one implemented in
consema.protocol.query (RFC 0016 s5.4). This module binds an ExecutableQuery
to one immutable snapshot and produces deterministic ordered matches.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import NodeRef, NodeRole, Span
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)
from consema.yaml.document import Document
from consema.yaml.kinds import (
    YamlNodeKind,
    YamlScalarKind,
    YamlSyntaxKind,
)
from consema.yaml.parser import (
    NativeMappingEntry,
    NativeScalar,
    NativeSequenceItem,
    node_ref as _node_ref,
)

NATIVE_DOMAIN_ID = "yaml.native-semantic-query"
SYNTAX_DOMAIN_ID = "yaml.lossless-syntax-query"


class YamlMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:13-99)."""

    STREAM = "Stream"
    DOCUMENT = "Document"
    NODE = "Node"
    MAPPING_ENTRY = "MappingEntry"
    SEQUENCE_ELEMENT = "SequenceElement"
    ANCHOR_DEFINITION = "AnchorDefinition"
    ALIAS_OCCURRENCE = "AliasOccurrence"


@dataclass(frozen=True, slots=True)
class YamlMatch:
    """One snapshot-bound native semantic query match (query.rs:13-99)."""

    kind: YamlMatchKind
    node: NodeRef
    span: Span
    ordinal: int | None = None
    document_count: int | None = None
    kind_name: str | None = None
    tag: str | None = None
    scalar_kind: str | None = None
    canonical: str | None = None
    anchor: str | None = None
    name: str | None = None
    key: NodeRef | None = None
    value: NodeRef | None = None
    target: NodeRef | None = None

    @property
    def identity(self) -> NodeRef:
        """Primary process-local identity (query.rs:101-114)."""
        return self.node


@dataclass(frozen=True, slots=True)
class YamlSyntaxMatch:
    """One snapshot-bound lossless syntax query match (query.rs:131-164)."""

    node: NodeRef
    span: Span
    kind: YamlSyntaxKind
    ordinal: int

    @property
    def identity(self) -> NodeRef:
        return self.node


@dataclass(frozen=True, slots=True)
class YamlQueryExecution:
    """Complete ordered query result (query.rs:167-197, 213-255)."""

    matches: tuple[object, ...]


class YamlCancellationToken:
    """Cooperative cancellation signal (query.rs:278-288)."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class YamlQueryLimits:
    """Query resource limits (consema-core/src/query.rs:2967-2981)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _Context:
    def __init__(
        self,
        document: Document,
        limits: YamlQueryLimits,
        cancellation: YamlCancellationToken,
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

    def node_match(self, index: int) -> YamlMatch:
        node = self.document.native.nodes[index]
        content = node.content
        if isinstance(content, NativeScalar):
            kind = YamlNodeKind.SCALAR.value
            scalar_kind = content.kind.value
            canonical = content.canonical
        elif isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            kind = YamlNodeKind.SEQUENCE.value
            scalar_kind = None
            canonical = None
        else:
            kind = YamlNodeKind.MAPPING.value
            scalar_kind = None
            canonical = None
        return YamlMatch(
            kind=YamlMatchKind.NODE,
            node=_node_ref(self.document.authority, index),
            span=node.span,
            kind_name=kind,
            tag=node.tag,
            scalar_kind=scalar_kind,
            canonical=canonical,
            anchor=node.anchor,
        )

    def resolve_index(self, node: NodeRef) -> int:
        return node.index


def _role_order(role: NodeRole) -> int:
    return {
        NodeRole.YAML_STREAM: 0,
        NodeRole.YAML_DOCUMENT: 1,
        NodeRole.YAML_MAPPING_ENTRY: 2,
        NodeRole.YAML_SEQUENCE_ELEMENT: 2,
        NodeRole.YAML_ANCHOR_DEFINITION: 3,
        NodeRole.YAML_ALIAS: 4,
        NodeRole.YAML_NODE: 5,
    }.get(role, 6)


def execute_yaml_query(
    executable: ExecutableQuery,
    document: Document,
    limits: YamlQueryLimits,
    cancellation: YamlCancellationToken,
) -> YamlQueryExecution:
    """Executes a validated YAML native semantic query (query.rs:167-197)."""
    definition = executable.definition
    if definition.domain.id != NATIVE_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _Context(document, limits, cancellation)
    context.step(1)
    input_matches = [
        YamlMatch(
            kind=YamlMatchKind.STREAM,
            node=document.stream_node_ref(),
            span=document.stream_span(),
            document_count=document.document_count(),
        )
    ]
    matches = _execute_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return YamlQueryExecution(matches=tuple(matches))


def execute_yaml_syntax_query(
    executable: ExecutableQuery,
    document: Document,
    limits: YamlQueryLimits,
    cancellation: YamlCancellationToken,
) -> YamlQueryExecution:
    """Executes a validated YAML lossless syntax query (query.rs:213-255)."""
    definition = executable.definition
    if definition.domain.id != SYNTAX_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _Context(document, limits, cancellation)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    context.step(len(pieces))
    input_matches = [
        YamlSyntaxMatch(
            node=document.authority.node_ref(ordinal, NodeRole.YAML_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return YamlQueryExecution(matches=tuple(matches))


def _execute_expression(
    expression: QueryExpression,
    input_matches: list[YamlMatch],
    context: _Context,
) -> list[YamlMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[YamlMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: source order by (start, end, role order, index)
    # (query.rs:335-356).
    output = []
    for branch in expression.branches:
        output.extend(_execute_expression(branch, input_matches, context))
    output.sort(
        key=lambda item: (
            item.span.start_byte,
            item.span.end_byte,
            _role_order(item.node.role),
            item.node.index,
        )
    )
    context.step(len(output))
    return output


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[YamlSyntaxMatch],
    context: _Context,
) -> list[YamlSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[YamlSyntaxMatch] = []
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
    operator, input_matches: list[YamlMatch], context: _Context
) -> list[YamlMatch]:
    output: list[YamlMatch] = []
    if operator.id == "yaml.documents":
        for item in input_matches:
            if item.kind is not YamlMatchKind.STREAM:
                continue
            for ordinal, record in enumerate(context.document.native.documents):
                output.append(
                    YamlMatch(
                        kind=YamlMatchKind.DOCUMENT,
                        node=context.document.authority.node_ref(ordinal, NodeRole.YAML_DOCUMENT),
                        span=record.span,
                        ordinal=ordinal,
                    )
                )
    elif operator.id == "yaml.document-root":
        for item in input_matches:
            if item.kind is YamlMatchKind.DOCUMENT:
                index = item.node.index
                record = context.document.native.documents[index]
                output.append(context.node_match(record.root))
    elif operator.id == "yaml.where-node-kind":
        expected = _string_argument(operator)
        output.extend(
            item
            for item in input_matches
            if item.kind is YamlMatchKind.NODE and item.kind_name == expected
        )
    elif operator.id == "yaml.where-tag":
        expected = _string_argument(operator)
        output.extend(
            item
            for item in input_matches
            if item.kind is YamlMatchKind.NODE and item.tag == expected
        )
    elif operator.id == "yaml.scalar-canonical-equals":
        expected = _string_argument(operator)
        output.extend(
            item
            for item in input_matches
            if item.kind is YamlMatchKind.NODE
            and item.canonical is not None
            and item.canonical == expected
        )
    elif operator.id == "yaml.try-sequence-elements":
        for item in input_matches:
            if item.kind is not YamlMatchKind.NODE:
                continue
            content = context.document.native.nodes[item.node.index].content
            if not (isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem)):
                continue
            for ordinal, entry in enumerate(content):
                output.append(
                    YamlMatch(
                        kind=YamlMatchKind.SEQUENCE_ELEMENT,
                        node=context.document.authority.node_ref(
                            entry.identity, NodeRole.YAML_SEQUENCE_ELEMENT
                        ),
                        span=entry.span,
                        ordinal=ordinal,
                    )
                )
    elif operator.id == "yaml.sequence-element-node":
        for item in input_matches:
            if item.kind is YamlMatchKind.SEQUENCE_ELEMENT:
                content = context.document.native.nodes[item.node.index].content
                entry = content[item.ordinal]
                output.append(context.node_match(entry.node))
    elif operator.id == "yaml.try-mapping-entries":
        for item in input_matches:
            if item.kind is not YamlMatchKind.NODE:
                continue
            content = context.document.native.nodes[item.node.index].content
            if not (isinstance(content, tuple) and content and isinstance(content[0], NativeMappingEntry)):
                continue
            for ordinal, entry in enumerate(content):
                output.append(
                    YamlMatch(
                        kind=YamlMatchKind.MAPPING_ENTRY,
                        node=context.document.authority.node_ref(
                            entry.identity, NodeRole.YAML_MAPPING_ENTRY
                        ),
                        span=entry.span,
                        ordinal=ordinal,
                        key=_node_ref(context.document.authority, entry.key),
                        value=_node_ref(context.document.authority, entry.value),
                    )
                )
    elif operator.id == "yaml.mapping-entry-key":
        for item in input_matches:
            if item.kind is YamlMatchKind.MAPPING_ENTRY:
                output.append(context.node_match(item.key.index))
    elif operator.id == "yaml.mapping-entry-value":
        for item in input_matches:
            if item.kind is YamlMatchKind.MAPPING_ENTRY:
                output.append(context.node_match(item.value.index))
    elif operator.id == "yaml.anchor-definition":
        for item in input_matches:
            if item.kind is not YamlMatchKind.NODE:
                continue
            node = context.document.native.nodes[item.node.index]
            if node.anchor is not None and node.anchor_span is not None:
                output.append(
                    YamlMatch(
                        kind=YamlMatchKind.ANCHOR_DEFINITION,
                        node=context.document.authority.node_ref(
                            item.node.index, NodeRole.YAML_ANCHOR_DEFINITION
                        ),
                        span=node.anchor_span,
                        name=node.anchor,
                    )
                )
    elif operator.id == "yaml.anchor-node":
        for item in input_matches:
            if item.kind is YamlMatchKind.ANCHOR_DEFINITION:
                output.append(context.node_match(item.node.index))
    elif operator.id == "yaml.alias-occurrences":
        for item in input_matches:
            if item.kind is not YamlMatchKind.STREAM:
                continue
            for ordinal, alias in enumerate(context.document.native.aliases):
                output.append(
                    YamlMatch(
                        kind=YamlMatchKind.ALIAS_OCCURRENCE,
                        node=context.document.authority.node_ref(
                            alias.identity, NodeRole.YAML_ALIAS
                        ),
                        span=alias.span,
                        ordinal=ordinal,
                        name=alias.name,
                        target=_node_ref(context.document.authority, alias.target),
                    )
                )
    elif operator.id == "yaml.alias-target":
        for item in input_matches:
            if item.kind is YamlMatchKind.ALIAS_OCCURRENCE:
                output.append(context.node_match(item.target.index))
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
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=operator.id, version=operator.version
        )
    context.step(len(output))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[YamlSyntaxMatch], context: _Context
) -> list[YamlSyntaxMatch]:
    output: list[YamlSyntaxMatch] = []
    if operator.id == "yaml.syntax-kind-is":
        expected = YamlSyntaxKind.from_name(_string_argument(operator))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="kind"
            )
        output.extend(item for item in input_matches if item.kind is expected)
    elif operator.id == "yaml.syntax-text-equals":
        expected = _encoded_text(_string_argument(operator), context.document.source)
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
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=operator.id, version=operator.version
        )
    context.step(len(output))
    return output


def _encoded_text(value: str, source) -> bytes:
    """Encodes the comparison text in the source's selected encoding
    (query.rs:651-660)."""
    encoding = source.encoding_facts().selected
    kind = encoding.kind
    from consema.document.source import SourceEncodingKind

    if kind is SourceEncodingKind.UTF8:
        return value.encode("utf-8")
    if kind is SourceEncodingKind.UTF16LE:
        return value.encode("utf-16-le")
    if kind is SourceEncodingKind.UTF16BE:
        return value.encode("utf-16-be")
    return b""


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
    for name in ("kind", "tag", "canonical", "text"):
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
