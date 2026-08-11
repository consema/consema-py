"""Execution of the TOML native and lossless-syntax query domains.

Authority:

- RFC 0001 §4 (docs/rfcs/0001-toml-1.0-profile.md:64-76): the frozen
  domains ``toml.native-semantic-query@1`` and
  ``toml.lossless-syntax-query@1`` with the standard operator registry
  (toml.try-table-entries, toml.entry-name-equals, toml.entry-item,
  toml.try-array-elements, toml.array-element-item; toml.syntax-kind-is,
  toml.syntax-text-equals) plus the generic core.take and
  core.distinct-by-identity; validation happens before execution.
- The match shapes transcribe crates/consema-toml/src/query.rs:9-86
  (TomlMatch Item/Entry/ArrayElement; TomlSyntaxMatch node/span/kind/
  ordinal).
- The execution semantics transcribe query.rs:88-488: domain check, step
  counting against QueryLimits (max_steps/max_results default 100_000,
  crates/consema-core/src/query.rs:2967-2978), expression evaluation
  (Input/Apply/Concat/StructureOrderMerge sorted by source span),
  operator application, and selection (All/First/Last/ZeroOrOne/
  RequireOne with cardinality enforcement).
- The query definition/validation model lives in consema.protocol
  (query.py:523-529 and 597-602 freeze the toml operator rows;
  query.py:1075-1079 the syntax-kind vocabulary). The executor below
  consumes a validated ``ExecutableQuery``.
- QueryLimits and CancellationToken are owned here until the protocol
  agent publishes the shared records (RFC 0016 §5.4); the frozen fields
  and defaults are the core query.rs ones.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import NodeRef, NodeRole, Span
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryDomain,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

from consema.toml.document import Document, TomlItem, TomlItemKind
from consema.toml.syntax import TomlSyntaxKind


class QueryLimits:
    """Execution resource limits (crates/consema-core/src/query.rs:2967-2978)."""

    __slots__ = ("max_steps", "max_results")

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results

    @classmethod
    def default(cls) -> QueryLimits:
        return cls()


class CancellationToken:
    """Process-local cancellation signal (consema-core CancellationToken)."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class TomlMatchKind(enum.Enum):
    ITEM = "Item"
    ENTRY = "Entry"
    ARRAY_ELEMENT = "ArrayElement"


@dataclass(frozen=True, slots=True)
class TomlMatch:
    """One snapshot-bound TOML native semantic query match (query.rs:9-41)."""

    kind: TomlMatchKind
    node: NodeRef | None = None
    kind_name: TomlItemKind | None = None
    ordinal: int | None = None
    name: str | None = None
    key: NodeRef | None = None
    item: NodeRef | None = None
    entry: NodeRef | None = None
    element: NodeRef | None = None

    @classmethod
    def of_item(cls, node: NodeRef, kind_name: TomlItemKind) -> TomlMatch:
        return cls(kind=TomlMatchKind.ITEM, node=node, kind_name=kind_name)

    @classmethod
    def of_entry(
        cls,
        ordinal: int,
        name: str,
        key: NodeRef,
        item: NodeRef,
        entry: NodeRef,
    ) -> TomlMatch:
        return cls(
            kind=TomlMatchKind.ENTRY,
            ordinal=ordinal,
            name=name,
            key=key,
            item=item,
            entry=entry,
        )

    @classmethod
    def of_array_element(cls, ordinal: int, element: NodeRef, item: NodeRef) -> TomlMatch:
        return cls(
            kind=TomlMatchKind.ARRAY_ELEMENT,
            ordinal=ordinal,
            element=element,
            item=item,
        )

    def identity(self) -> NodeRef:
        """The identity used by core.distinct-by-identity and
        structure-order-merge (query.rs:43-51)."""
        if self.kind is TomlMatchKind.ITEM:
            assert self.node is not None
            return self.node
        if self.kind is TomlMatchKind.ENTRY:
            assert self.entry is not None
            return self.entry
        assert self.element is not None
        return self.element


@dataclass(frozen=True, slots=True)
class TomlSyntaxMatch:
    """One snapshot-bound TOML lossless syntax query match (query.rs:53-86).

    The Rust accessors (node_ref/span/kind/ordinal) map to the dataclass
    fields of the same names.
    """

    node: NodeRef
    span: Span
    kind: TomlSyntaxKind
    ordinal: int


class _Context:
    __slots__ = ("document", "limits", "cancellation", "steps")

    def __init__(
        self,
        document: Document,
        limits: QueryLimits,
        cancellation: CancellationToken,
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

    def item_match(self, index: int) -> TomlMatch:
        item = TomlItem(self.document, index)
        return TomlMatch.of_item(item.node_ref(), item.kind())


def _require_domain(executable: ExecutableQuery, expected_id: str) -> QueryDomain:
    domain = executable.definition.domain
    if domain.id != expected_id or domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=domain)
    return domain


def execute_toml_query(
    executable: ExecutableQuery,
    document: Document,
    limits: QueryLimits | None = None,
    cancellation: CancellationToken | None = None,
) -> list[TomlMatch]:
    """Executes a validated TOML native semantic query against one
    immutable snapshot (query.rs:88-113). Raises QueryFailure."""
    _require_domain(executable, "toml.native-semantic-query")
    limits = limits or QueryLimits.default()
    cancellation = cancellation or CancellationToken()
    context = _Context(document, limits, cancellation)
    context.step(0)
    input_matches = [context.item_match(document.root().index)]
    matches = _execute_expression(executable.definition.expression, input_matches, context)
    return _apply_selection(matches, executable.definition.selection)


def execute_toml_syntax_query(
    executable: ExecutableQuery,
    document: Document,
    limits: QueryLimits | None = None,
    cancellation: CancellationToken | None = None,
) -> list[TomlSyntaxMatch]:
    """Executes a validated TOML lossless syntax query against every
    source piece in raw order (query.rs:129-169). Raises QueryFailure."""
    _require_domain(executable, "toml.lossless-syntax-query")
    limits = limits or QueryLimits.default()
    cancellation = cancellation or CancellationToken()
    context = _Context(document, limits, cancellation)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    context.step(len(pieces))
    input_matches = [
        TomlSyntaxMatch(
            node=document.node_ref(ordinal, NodeRole.TOML_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(executable.definition.expression, input_matches, context)
    return _apply_selection(matches, executable.definition.selection)


def _execute_expression(
    expression, input_matches: list[TomlMatch], context: _Context
) -> list[TomlMatch]:
    kind = expression.kind
    if kind is ExpressionKind.INPUT:
        return input_matches
    if kind is ExpressionKind.APPLY:
        nested = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, nested, context)
    if kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
        output: list[TomlMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        if kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
            def key(match: TomlMatch) -> tuple[int, int, int]:
                identity = match.identity()
                entity = context.document._entity(identity.index)
                return (entity.span.start_byte, entity.span.end_byte, identity.index)

            output.sort(key=key)
            context.step(len(output))
        return output
    raise QueryFailure(QueryFailureKind.INVALID_ARGUMENT, operator="expression", argument="kind")


def _execute_syntax_expression(
    expression, input_matches: list[TomlSyntaxMatch], context: _Context
) -> list[TomlSyntaxMatch]:
    kind = expression.kind
    if kind is ExpressionKind.INPUT:
        return input_matches
    if kind is ExpressionKind.APPLY:
        nested = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, nested, context)
    if kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
        output: list[TomlSyntaxMatch] = []
        for branch in expression.branches:
            output.extend(_execute_syntax_expression(branch, input_matches, context))
            context.step(len(output))
        if kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
            output.sort(key=lambda match: match.ordinal)
            context.step(len(output))
        return output
    raise QueryFailure(QueryFailureKind.INVALID_ARGUMENT, operator="expression", argument="kind")


def _apply_operator(
    operator, input_matches: list[TomlMatch], context: _Context
) -> list[TomlMatch]:
    output: list[TomlMatch] = []
    operator_id = operator.id
    if operator_id == "toml.try-table-entries":
        for match_item in input_matches:
            if match_item.kind is not TomlMatchKind.ITEM:
                continue
            assert match_item.node is not None
            entity = context.document._entity(match_item.node.index)
            item = entity.kind
            if item.kind.name not in ("Table", "InlineTable"):
                continue
            for entry_index in item.kind.children:
                entry = context.document._entity(entry_index).kind
                name_entity = context.document._entity(entry.key).kind
                output.append(
                    TomlMatch.of_entry(
                        ordinal=entry.ordinal,
                        name=name_entity.name,
                        key=context.document.node_ref(entry.key, NodeRole.TOML_KEY),
                        item=context.document.node_ref(entry.item, NodeRole.TOML_ITEM),
                        entry=context.document.node_ref(entry_index, NodeRole.TOML_ENTRY),
                    )
                )
    elif operator_id == "toml.entry-name-equals":
        expected = operator.arguments["name"].as_string()
        output.extend(
            match_item
            for match_item in input_matches
            if match_item.kind is TomlMatchKind.ENTRY and match_item.name == expected
        )
    elif operator_id == "toml.entry-item":
        for match_item in input_matches:
            if match_item.kind is not TomlMatchKind.ENTRY or match_item.item is None:
                continue
            output.append(context.item_match(match_item.item.index))
    elif operator_id == "toml.try-array-elements":
        for match_item in input_matches:
            if match_item.kind is not TomlMatchKind.ITEM:
                continue
            assert match_item.node is not None
            entity = context.document._entity(match_item.node.index)
            item = entity.kind
            if item.kind.name not in ("Array", "ArrayOfTables"):
                continue
            for element_index in item.kind.children:
                element = context.document._entity(element_index).kind
                output.append(
                    TomlMatch.of_array_element(
                        ordinal=element.ordinal,
                        element=context.document.node_ref(
                            element_index, NodeRole.TOML_ARRAY_ELEMENT
                        ),
                        item=context.document.node_ref(element.item, NodeRole.TOML_ITEM),
                    )
                )
    elif operator_id == "toml.array-element-item":
        for match_item in input_matches:
            if match_item.kind is not TomlMatchKind.ARRAY_ELEMENT or match_item.item is None:
                continue
            output.append(context.item_match(match_item.item.index))
    elif operator_id == "core.take":
        count = operator.arguments["count"].as_integer()
        output.extend(input_matches[:count])
    elif operator_id == "core.distinct-by-identity":
        seen: set[NodeRef] = set()
        for match_item in input_matches:
            identity = match_item.identity()
            if identity not in seen:
                seen.add(identity)
                output.append(match_item)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=operator_id, version=operator.version
        )
    context.step(len(output))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[TomlSyntaxMatch], context: _Context
) -> list[TomlSyntaxMatch]:
    output: list[TomlSyntaxMatch] = []
    operator_id = operator.id
    if operator_id == "toml.syntax-kind-is":
        expected = TomlSyntaxKind.from_name(operator.arguments["kind"].as_string())
        assert expected is not None, "kind was validated before binding"
        output.extend(match for match in input_matches if match.kind is expected)
    elif operator_id == "toml.syntax-text-equals":
        expected = operator.arguments["text"].as_string().encode("utf-8")
        raw = context.document.render()
        output.extend(
            match
            for match in input_matches
            if raw[match.span.start_byte : match.span.end_byte] == expected
        )
    elif operator_id == "core.take":
        count = operator.arguments["count"].as_integer()
        output.extend(input_matches[:count])
    elif operator_id == "core.distinct-by-identity":
        seen: set[NodeRef] = set()
        for match in input_matches:
            if match.node not in seen:
                seen.add(match.node)
                output.append(match)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=operator_id, version=operator.version
        )
    context.step(len(output))
    return output


def _apply_selection(matches: list, selection: QuerySelection) -> list:
    if selection is QuerySelection.ALL:
        return matches
    if selection is QuerySelection.FIRST:
        return matches[:1]
    if selection is QuerySelection.LAST:
        return matches[-1:] if matches else []
    if selection is QuerySelection.ZERO_OR_ONE:
        if len(matches) <= 1:
            return matches
        raise QueryFailure(
            QueryFailureKind.CARDINALITY_VIOLATION,
            expected_role=None,
            actual_role=None,
        )
    if selection is QuerySelection.REQUIRE_ONE:
        if len(matches) == 1:
            return matches
        raise QueryFailure(
            QueryFailureKind.CARDINALITY_VIOLATION,
            expected_role=None,
            actual_role=None,
        )
    raise AssertionError("closed selection set")
