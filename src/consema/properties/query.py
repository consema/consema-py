"""Java Properties native-semantic and lossless-syntax query execution.

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-properties/src/query.rs:
  124-150 (native) and 167-211 (syntax) — domains
  java-properties.native-semantic-query@1 and
  java-properties.lossless-syntax-query@1 (RFC 0010 section 10,
  docs/rfcs/0010-java-properties-profiles-v1.md:269-308).
- Operators: query.rs:398-532 (native: properties.document-properties,
  properties.natural-lines, properties.logical-lines,
  properties.logical-line-natural-lines, properties.property-key-equals,
  properties.property-value-state-is, properties.property-escapes,
  properties.duplicate-group, core.take, core.distinct-by-identity) and
  query.rs:534-607 (syntax: properties.syntax-kind-is,
  properties.syntax-text-equals, properties.syntax-raw-bytes-equals,
  properties.syntax-utf16be-equals, core.take,
  core.distinct-by-identity).
- Expression evaluation and StructureOrderMerge: query.rs:326-396.
- Selection algebra: query.rs:675-692 (All/First/Last/ZeroOrOne/RequireOne
  with CardinalityViolation).
- Limits and cancellation: consema-core/src/query.rs:2967-2981 (QueryLimits
  defaults max_steps=100_000, max_results=100_000); step accounting
  query.rs:234-277.
- Failure codes: core.query.*@1 (crates/consema-protocol/src/
  error_registry.rs:108-118) via consema.protocol.query.QueryFailure;
  the argument vocabulary for the Properties operators is validated by
  the transferable query model (consema.protocol.query.
  _check_operator_arguments, query.py:964-1053) before binding.
- Key matching takes exact UTF-16 code units encoded as ``UTF16BE/1``; it
  does not normalize Unicode or case (RFC 0010 section 10,
  docs/rfcs/0010-...:284-286). Decoded-text matching is available only
  when a piece is well-formed Unicode; exact raw-byte and exact
  UTF-16-code-unit filters cover all other pieces (lines 304-308).

The transferable query model (QueryDomain, QueryExpression, OperatorCall,
QuerySelection, QueryDefinition, ValidatedQuery, ExecutableQuery,
QueryFailure) is the language-neutral one implemented in
consema.protocol.query (RFC 0016 section 5.4). This module binds an
ExecutableQuery to one immutable snapshot and produces deterministic
ordered matches (RFC 0016 section 5.4: match count/identity/order).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import NodeRef, NodeRole, Span
from consema.properties.document import PropertiesDocument
from consema.properties.java_string import JavaString
from consema.properties.kinds import (
    NATIVE_QUERY_DOMAIN_ID,
    SYNTAX_QUERY_DOMAIN_ID,
    PropertiesEscapeKind,
    PropertiesLogicalLineKind,
    PropertiesSyntaxKind,
    PropertiesValueState,
)
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)


class PropertiesMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:12-74)."""

    DOCUMENT = "Document"
    PROPERTY = "Property"
    NATURAL_LINE = "NaturalLine"
    LOGICAL_LINE = "LogicalLine"
    ESCAPE = "Escape"


@dataclass(frozen=True, slots=True)
class PropertiesMatch:
    """One snapshot-bound native semantic query match (query.rs:12-74).

    Only the fields relevant to the match kind are populated; ``node`` is
    the match identity for every kind.
    """

    kind: PropertiesMatchKind
    node: NodeRef
    ordinal: int | None = None
    logical_line: NodeRef | None = None
    key: JavaString | None = None
    value: JavaString | None = None
    value_state: PropertiesValueState | None = None
    duplicate_group: int | None = None
    span: Span | None = None
    record_kind: PropertiesLogicalLineKind | None = None
    property: NodeRef | None = None
    in_key: bool | None = None
    escape_kind: PropertiesEscapeKind | None = None
    output_start: int | None = None
    output_end: int | None = None


@dataclass(frozen=True, slots=True)
class PropertiesSyntaxMatch:
    """One snapshot-bound lossless syntax query match (query.rs:88-121)."""

    node: NodeRef
    span: Span
    kind: PropertiesSyntaxKind
    ordinal: int


@dataclass(frozen=True, slots=True)
class PropertiesQueryExecution:
    """Complete ordered query result (query.rs:124-150, 167-211)."""

    matches: tuple[object, ...]


class PropertiesCancellationToken:
    """Cooperative cancellation signal (query.rs:234-247; consema-core)."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class PropertiesQueryLimits:
    """Query resource limits (consema-core/src/query.rs:2967-2981)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _NativeContext:
    """One execution context bound to an immutable snapshot
    (query.rs:227-324)."""

    def __init__(
        self,
        document: PropertiesDocument,
        limits: PropertiesQueryLimits,
        cancellation: PropertiesCancellationToken,
    ) -> None:
        self.document = document
        self.limits = limits
        self.cancellation = cancellation
        self.steps = 0

    def step(self, results: int) -> None:
        """Step accounting with cancellation and limit enforcement
        (query.rs:234-247)."""
        if self.cancellation.is_cancelled():
            raise QueryFailure(QueryFailureKind.CANCELLED)
        self.steps += 1
        if self.steps > self.limits.max_steps or results > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)

    def push(self, output: list[PropertiesMatch], value: PropertiesMatch) -> None:
        if len(output) + 1 > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        output.append(value)

    def append(
        self, output: list[PropertiesMatch], values: list[PropertiesMatch]
    ) -> None:
        if len(output) + len(values) > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        output.extend(values)

    # -- match construction -------------------------------------------------

    def property_match(self, ordinal: int) -> PropertiesMatch:
        property = self.document.properties[ordinal]
        return PropertiesMatch(
            kind=PropertiesMatchKind.PROPERTY,
            node=property.node,
            ordinal=ordinal,
            logical_line=property.logical_line,
            key=property.key,
            value=property.value,
            value_state=property.value_state,
            duplicate_group=property.duplicate_group,
        )

    def natural_line_match(self, ordinal: int) -> PropertiesMatch:
        line = self.document.natural_lines[ordinal]
        return PropertiesMatch(
            kind=PropertiesMatchKind.NATURAL_LINE,
            node=line.node,
            ordinal=ordinal,
            span=line.span,
        )

    def logical_line_match(self, ordinal: int) -> PropertiesMatch:
        line = self.document.logical_lines[ordinal]
        return PropertiesMatch(
            kind=PropertiesMatchKind.LOGICAL_LINE,
            node=line.node,
            ordinal=ordinal,
            record_kind=line.kind,
        )

    def escape_match(self, ordinal: int) -> PropertiesMatch:
        escape = self.document.escapes[ordinal]
        return PropertiesMatch(
            kind=PropertiesMatchKind.ESCAPE,
            node=escape.node,
            ordinal=ordinal,
            property=escape.property,
            in_key=escape.in_key,
            escape_kind=escape.kind,
            span=escape.span,
            output_start=escape.output_start,
            output_end=escape.output_end,
        )


def execute_properties_query(
    executable: ExecutableQuery,
    document: PropertiesDocument,
    limits: PropertiesQueryLimits,
    cancellation: PropertiesCancellationToken,
) -> PropertiesQueryExecution:
    """Executes a validated Properties native semantic query
    (query.rs:124-150)."""
    definition = executable.definition
    domain = definition.domain
    if domain.id != NATIVE_QUERY_DOMAIN_ID or domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=domain)
    context = _NativeContext(document, limits, cancellation)
    context.step(1)
    input_matches = [
        PropertiesMatch(kind=PropertiesMatchKind.DOCUMENT, node=document.node_ref())
    ]
    matches = _execute_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return PropertiesQueryExecution(matches=tuple(matches))


def execute_properties_syntax_query(
    executable: ExecutableQuery,
    document: PropertiesDocument,
    limits: PropertiesQueryLimits,
    cancellation: PropertiesCancellationToken,
) -> PropertiesQueryExecution:
    """Executes a validated Properties lossless syntax query
    (query.rs:167-211)."""
    definition = executable.definition
    domain = definition.domain
    if domain.id != SYNTAX_QUERY_DOMAIN_ID or domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=domain)
    context = _NativeContext(document, limits, cancellation)
    pieces = document.structural_index.pieces
    kinds = document.syntax_kinds
    context.step(len(pieces))
    input_matches = [
        PropertiesSyntaxMatch(
            node=document.authority.node_ref(ordinal, NodeRole.PROPERTIES_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return PropertiesQueryExecution(matches=tuple(matches))


class _PropertiesCursor:
    """Ordered cursor with cooperative cancellation (query.rs:153-164,
    214-225; consema-core OrderedQueryCursor)."""

    def __init__(
        self,
        matches: list[object],
        cancellation: PropertiesCancellationToken,
    ) -> None:
        self._matches = list(matches)
        self._position = 0
        self._cancellation = cancellation
        self._terminal = None

    def next(self) -> object | None:
        if self._terminal is not None:
            return None
        if self._cancellation.is_cancelled():
            self._terminal = "Cancelled"
            return None
        if self._position >= len(self._matches):
            self._terminal = "Completed"
            return None
        match = self._matches[self._position]
        self._position += 1
        return match

    def terminal_state(self) -> str | None:
        return self._terminal


def execute_properties_query_cursor(
    executable: ExecutableQuery,
    document: PropertiesDocument,
    limits: PropertiesQueryLimits,
    cancellation: PropertiesCancellationToken,
) -> _PropertiesCursor:
    """Executes and exposes a complete Properties native result through an
    ordered cursor (query.rs:153-164)."""
    result = execute_properties_query(executable, document, limits, cancellation)
    return _PropertiesCursor(list(result.matches), cancellation)


def execute_properties_syntax_query_cursor(
    executable: ExecutableQuery,
    document: PropertiesDocument,
    limits: PropertiesQueryLimits,
    cancellation: PropertiesCancellationToken,
) -> _PropertiesCursor:
    """Executes and exposes a complete Properties syntax result through an
    ordered cursor (query.rs:214-225)."""
    result = execute_properties_syntax_query(executable, document, limits, cancellation)
    return _PropertiesCursor(list(result.matches), cancellation)


# ---------------------------------------------------------------------------
# Expression evaluation (query.rs:326-396)
# ---------------------------------------------------------------------------


def _execute_expression(
    expression: QueryExpression,
    input_matches: list[PropertiesMatch],
    context: _NativeContext,
) -> list[PropertiesMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[PropertiesMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: source order by property span start, then
    # ordinal (query.rs:349-359, 609-634).
    output = []
    for branch in expression.branches:
        output.extend(_execute_expression(branch, input_matches, context))
    output.sort(key=lambda item: _source_order(context.document, item))
    context.step(len(output))
    return output


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[PropertiesSyntaxMatch],
    context: _NativeContext,
) -> list[PropertiesSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[PropertiesSyntaxMatch] = []
        for branch in expression.branches:
            output.extend(_execute_syntax_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: raw source order with ties preserved
    # (query.rs:385-395); the sort is stable, so equal spans keep their
    # branch order (the vector pins the merged kinds and the non-strict
    # ordinal increase; java-properties-v1.json:66-69).
    output = []
    for branch in expression.branches:
        output.extend(_execute_syntax_expression(branch, input_matches, context))
    output.sort(key=lambda item: (item.span.start_byte, item.span.end_byte, item.ordinal))
    context.step(len(output))
    return output


def _source_order(document: PropertiesDocument, item: PropertiesMatch) -> tuple[int, int]:
    """Deterministic source-order key (query.rs:609-634)."""
    if item.kind is PropertiesMatchKind.DOCUMENT:
        return (0, 0)
    if item.kind is PropertiesMatchKind.PROPERTY:
        return (document.property(item.node).span.start_byte, item.ordinal or 0)
    if item.kind in (PropertiesMatchKind.NATURAL_LINE, PropertiesMatchKind.ESCAPE):
        return (item.span.start_byte if item.span is not None else 0, item.ordinal or 0)
    # LogicalLine: start of the first constituent natural line
    # (query.rs:622-632).
    logical = document.logical_line(item.node)
    start = 0
    if logical.natural_lines:
        first = logical.natural_lines[0]
        try:
            start = document.natural_line(first).span.start_byte
        except Exception:
            start = 0
    return (start, item.ordinal or 0)


# ---------------------------------------------------------------------------
# Native operators (query.rs:398-532)
# ---------------------------------------------------------------------------


def _apply_operator(
    operator, input_matches: list[PropertiesMatch], context: _NativeContext
) -> list[PropertiesMatch]:
    output: list[PropertiesMatch] = []
    if operator.id == "properties.document-properties":
        for item in input_matches:
            if item.kind is PropertiesMatchKind.DOCUMENT:
                for ordinal in range(len(context.document.properties)):
                    context.push(output, context.property_match(ordinal))
    elif operator.id == "properties.natural-lines":
        for item in input_matches:
            if item.kind is PropertiesMatchKind.DOCUMENT:
                for ordinal in range(len(context.document.natural_lines)):
                    context.push(output, context.natural_line_match(ordinal))
    elif operator.id == "properties.logical-lines":
        for item in input_matches:
            if item.kind is PropertiesMatchKind.DOCUMENT:
                for ordinal in range(len(context.document.logical_lines)):
                    context.push(output, context.logical_line_match(ordinal))
    elif operator.id == "properties.logical-line-natural-lines":
        for item in input_matches:
            if item.kind is not PropertiesMatchKind.LOGICAL_LINE:
                continue
            logical = context.document.logical_line(item.node)
            for natural in logical.natural_lines:
                ordinal = _natural_ordinal(context.document, natural)
                if ordinal is not None:
                    context.push(output, context.natural_line_match(ordinal))
    elif operator.id == "properties.property-key-equals":
        expected = _bytes_argument(operator, "key")
        for item in input_matches:
            if (
                item.kind is PropertiesMatchKind.PROPERTY
                and item.key is not None
                and _java_string_equals_utf16be(item.key, expected)
            ):
                context.push(output, item)
    elif operator.id == "properties.property-value-state-is":
        expected = _state_argument(operator)
        for item in input_matches:
            if (
                item.kind is PropertiesMatchKind.PROPERTY
                and item.value_state is expected
            ):
                context.push(output, item)
    elif operator.id == "properties.property-escapes":
        for item in input_matches:
            if item.kind is not PropertiesMatchKind.PROPERTY:
                continue
            for ordinal, escape in enumerate(context.document.escapes):
                if escape.property == item.node:
                    context.push(output, context.escape_match(ordinal))
    elif operator.id == "properties.duplicate-group":
        for item in input_matches:
            if (
                item.kind is PropertiesMatchKind.PROPERTY
                and item.duplicate_group is not None
            ):
                group = item.duplicate_group
                for ordinal in range(len(context.document.properties)):
                    if context.document.properties[ordinal].duplicate_group == group:
                        context.push(output, context.property_match(ordinal))
    elif operator.id == "core.take":
        count = _integer_argument(operator)
        for item in input_matches[:count]:
            context.push(output, item)
    elif operator.id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.node not in seen:
                seen.add(item.node)
                context.push(output, item)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator.id,
            version=operator.version,
        )
    context.step(len(output))
    return output


# ---------------------------------------------------------------------------
# Syntax operators (query.rs:534-607)
# ---------------------------------------------------------------------------


def _apply_syntax_operator(
    operator, input_matches: list[PropertiesSyntaxMatch], context: _NativeContext
) -> list[PropertiesSyntaxMatch]:
    output: list[PropertiesSyntaxMatch] = []
    if operator.id == "properties.syntax-kind-is":
        expected = PropertiesSyntaxKind.from_name(_string_argument(operator, "kind"))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator=operator.id,
                argument="kind",
            )
        output.extend(item for item in input_matches if item.kind is expected)
    elif operator.id == "properties.syntax-text-equals":
        expected = _string_argument(operator, "text")
        output.extend(
            item
            for item in input_matches
            if _decoded_span_text(context.document, item.span) == expected
        )
    elif operator.id == "properties.syntax-raw-bytes-equals":
        expected = _bytes_argument(operator, "bytes")
        raw = context.document.render()
        output.extend(
            item
            for item in input_matches
            if raw[item.span.start_byte : item.span.end_byte] == expected
        )
    elif operator.id == "properties.syntax-utf16be-equals":
        expected = _bytes_argument(operator, "code_units")
        output.extend(
            item
            for item in input_matches
            if _unicode_text_equals_utf16be(
                _decoded_span_text(context.document, item.span), expected
            )
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
            QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator.id,
            version=operator.version,
        )
    context.step(len(output))
    return output


# ---------------------------------------------------------------------------
# Selection algebra (query.rs:675-692)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Argument and text helpers
# ---------------------------------------------------------------------------


def _string_argument(operator, name: str) -> str:
    value = operator.arguments.get(name)
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument=name
        )
    return value.as_string()


def _bytes_argument(operator, name: str) -> bytes:
    value = operator.arguments.get(name)
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument=name
        )
    return value.as_bytes()


def _integer_argument(operator) -> int:
    value = operator.arguments.get("count")
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="count"
        )
    return value.as_integer()


def _state_argument(operator) -> PropertiesValueState:
    state = _string_argument(operator, "state")
    try:
        return PropertiesValueState(state)
    except ValueError:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="state"
        ) from None


def _natural_ordinal(document: PropertiesDocument, node: NodeRef) -> int | None:
    for ordinal, line in enumerate(document.natural_lines):
        if line.node == node:
            return ordinal
    return None


def _decoded_span_text(document: PropertiesDocument, span: Span) -> str:
    """Decoded text of one syntax span (query.rs:636-651).

    ``decoded_utf8_byte`` offsets address the decoded text's UTF-8 byte
    sequence (RFC 0003 section 5); the Python ``str`` is sliced through
    its UTF-8 encoding so byte offsets never become character indices.
    """
    text = document.source.decoded_text()
    assert text is not None, "Properties source is text"
    encoded = text.encode("utf-8")
    start = document.source.decoded_position(span.start_byte).decoded_utf8_byte
    end = document.source.decoded_position(span.end_byte).decoded_utf8_byte
    return encoded[start:end].decode("utf-8")


def _java_string_equals_utf16be(value: JavaString, expected: bytes) -> bool:
    """Exact UTF16BE/1 key comparison (query.rs:653-660)."""
    units = value.code_units()
    if len(units) * 2 != len(expected):
        return False
    for unit, pair in zip(units, _chunks(expected, 2)):
        if unit.to_bytes(2, "big") != pair:
            return False
    return True


def _unicode_text_equals_utf16be(value: str, expected: bytes) -> bool:
    """Exact UTF16BE/1 comparison of decoded text (query.rs:662-673)."""
    pairs = list(_chunks(expected, 2))
    encoded = value.encode("utf-16-be")
    units = [
        int.from_bytes(encoded[index : index + 2], "big")
        for index in range(0, len(encoded), 2)
    ]
    if len(units) != len(pairs):
        return False
    return all(
        unit == int.from_bytes(pair, "big") for unit, pair in zip(units, pairs)
    )


def _chunks(data: bytes, size: int):
    for index in range(0, len(data), size):
        yield data[index : index + size]
