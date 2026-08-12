"""INI native-semantic and lossless-syntax query execution.

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-ini/src/query.rs:117-143
  (native domain ini.native-semantic-query@1) and 160-204 (syntax domain
  ini.lossless-syntax-query@1); the ten native operators and the two
  syntax operators are the exact RFC 0009 §9 surface (docs/rfcs/0009-ini-
  family-profiles-v1.md:287-345).
- Operators: query.rs:428-625 (native: ini.document-sections,
  ini.section-entries, ini.all-entries, ini.entry-section,
  ini.section-name-equals, ini.entry-key-equals, ini.entry-value-state-is,
  ini.duplicate-group, ini.physical-lines, ini.logical-lines, core.take,
  core.distinct-by-identity) and query.rs:370-419 (syntax:
  ini.syntax-kind-is, ini.syntax-text-equals, core.take,
  core.distinct-by-identity).
- Expression evaluation and StructureOrderMerge: query.rs:298-368; source
  order for native matches query.rs:627-659; selection algebra
  query.rs:693-710 (All/First/Last/ZeroOrOne/RequireOne with
  CardinalityViolation).
- Limits and cancellation: consema-core/src/query.rs:2967-2981 (QueryLimits
  defaults max_steps=100_000, max_results=100_000); the step accounting
  query.rs:228-240; cursor cancellation query.rs:146-157.
- Syntax text comparison uses the decoded Unicode scalar text of the exact
  piece span, not its raw encoding bytes (RFC 0009 §9,
  docs/rfcs/0009-...:337-341; query.rs:661-676) — UTF-8, UTF-16LE, and
  explicit Windows-code-page queries are semantically identical.
- Name filters require OriginalExact | ProfileEquivalent explicitly; a
  query never silently uses case folding (RFC 0009 §9,
  docs/rfcs/0009-...:301-304; query.rs:470-525).
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
from consema.ini.document import IniDocument
from consema.ini.kinds import IniLogicalLineKind, IniProfile, IniSyntaxKind, IniValueState
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

NATIVE_DOMAIN_ID = "ini.native-semantic-query"
SYNTAX_DOMAIN_ID = "ini.lossless-syntax-query"


class IniMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:10-67)."""

    DOCUMENT = "Document"
    SECTION = "Section"
    ENTRY = "Entry"
    PHYSICAL_LINE = "PhysicalLine"
    LOGICAL_LINE = "LogicalLine"


@dataclass(frozen=True, slots=True)
class IniMatch:
    """Owned snapshot-bound INI native semantic query match (query.rs:10-67)."""

    kind: IniMatchKind
    node: NodeRef
    ordinal: int | None = None
    name: str | None = None
    comparison_name: str | None = None
    is_default: bool | None = None
    section: NodeRef | None = None
    key: str | None = None
    comparison_key: str | None = None
    value_state: IniValueState | None = None
    duplicate_group: int | None = None
    span: Span | None = None
    logical_kind: IniLogicalLineKind | None = None

    @property
    def identity(self) -> NodeRef:
        """Stable match identity (query.rs:69-79)."""
        return self.node


@dataclass(frozen=True, slots=True)
class IniSyntaxMatch:
    """Owned snapshot-bound INI lossless syntax query match (query.rs:82-88)."""

    node: NodeRef
    span: Span
    kind: IniSyntaxKind
    ordinal: int


@dataclass(frozen=True, slots=True)
class IniQueryExecution:
    """Complete ordered query result (query.rs:117-143, 160-204)."""

    matches: tuple[object, ...]


class IniCancellationToken:
    """Cooperative cancellation signal (query.rs:228-240; consema-core)."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class IniQueryLimits:
    """Query resource limits (consema-core/src/query.rs:2967-2981)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _Context:
    def __init__(
        self,
        document: IniDocument,
        limits: IniQueryLimits,
        cancellation: IniCancellationToken,
    ) -> None:
        self.document = document
        self.limits = limits
        self.cancellation = cancellation
        self.steps = 0

    def step(self, results: int) -> None:
        """One step and result-budget accounting (query.rs:228-240)."""
        if self.cancellation.is_cancelled():
            raise QueryFailure(QueryFailureKind.CANCELLED)
        self.steps += 1
        if self.steps > self.limits.max_steps or results > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)

    def push(self, output: list, value: object) -> None:
        if len(output) + 1 > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        output.append(value)

    def append(self, output: list, values: list) -> None:
        if len(output) + len(values) > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        output.extend(values)

    def section_match(self, ordinal: int) -> IniMatch:
        section = self.document.sections[ordinal]
        return IniMatch(
            kind=IniMatchKind.SECTION,
            node=section.node,
            ordinal=ordinal,
            name=section.name,
            comparison_name=section.comparison_name,
            is_default=section.is_default,
            duplicate_group=section.duplicate_group,
        )

    def entry_match(self, ordinal: int) -> IniMatch:
        entry = self.document.entries[ordinal]
        return IniMatch(
            kind=IniMatchKind.ENTRY,
            node=entry.node,
            ordinal=ordinal,
            section=entry.section,
            key=entry.key,
            comparison_key=entry.comparison_key,
            value_state=entry.state,
            duplicate_group=entry.duplicate_group,
        )


def execute_ini_query(
    executable: ExecutableQuery,
    document: IniDocument,
    limits: IniQueryLimits,
    cancellation: IniCancellationToken,
) -> IniQueryExecution:
    """Executes a validated INI native semantic query (query.rs:117-143)."""
    definition = executable.definition
    if definition.domain.id != NATIVE_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _Context(document, limits, cancellation)
    context.step(1)
    input_matches = [
        IniMatch(kind=IniMatchKind.DOCUMENT, node=document.node_ref())
    ]
    matches = _execute_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return IniQueryExecution(matches=tuple(matches))


def execute_ini_syntax_query(
    executable: ExecutableQuery,
    document: IniDocument,
    limits: IniQueryLimits,
    cancellation: IniCancellationToken,
) -> IniQueryExecution:
    """Executes a validated INI lossless syntax query (query.rs:160-204)."""
    definition = executable.definition
    if definition.domain.id != SYNTAX_DOMAIN_ID or definition.domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain)
    context = _Context(document, limits, cancellation)
    pieces = document.structural_index.pieces
    kinds = document.syntax_kinds
    context.step(len(pieces))
    input_matches = [
        IniSyntaxMatch(
            node=document.authority.node_ref(ordinal, NodeRole.INI_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return IniQueryExecution(matches=tuple(matches))


def _execute_expression(
    expression: QueryExpression,
    input_matches: list[IniMatch],
    context: _Context,
) -> list[IniMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[IniMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: source order by (start byte, ordinal)
    # (query.rs:321-331, 627-659).
    output = []
    for branch in expression.branches:
        output.extend(_execute_expression(branch, input_matches, context))
    output.sort(key=lambda item: (_source_start(context.document, item), _source_ordinal(item)))
    context.step(len(output))
    return output


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[IniSyntaxMatch],
    context: _Context,
) -> list[IniSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[IniSyntaxMatch] = []
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
    operator, input_matches: list[IniMatch], context: _Context
) -> list[IniMatch]:
    output: list[IniMatch] = []
    if operator.id == "ini.document-sections":
        for item in input_matches:
            if item.kind is IniMatchKind.DOCUMENT:
                for ordinal in range(len(context.document.sections)):
                    context.push(output, context.section_match(ordinal))
    elif operator.id == "ini.section-entries":
        for item in input_matches:
            if item.kind is IniMatchKind.SECTION:
                for ordinal, entry in enumerate(context.document.entries):
                    if entry.section == item.node:
                        context.push(output, context.entry_match(ordinal))
    elif operator.id == "ini.all-entries":
        for item in input_matches:
            if item.kind is IniMatchKind.DOCUMENT:
                for ordinal in range(len(context.document.entries)):
                    context.push(output, context.entry_match(ordinal))
    elif operator.id == "ini.entry-section":
        for item in input_matches:
            if item.kind is IniMatchKind.ENTRY and item.section is not None:
                for ordinal, section in enumerate(context.document.sections):
                    if section.node == item.section:
                        context.push(output, context.section_match(ordinal))
                        break
    elif operator.id == "ini.section-name-equals":
        expected = _string_argument(operator, "name")
        comparison = _string_argument(operator, "comparison")
        equivalent = _section_comparison(context.document.profile, expected)
        for item in input_matches:
            if item.kind is not IniMatchKind.SECTION:
                continue
            if comparison == "OriginalExact":
                matches = item.name == expected
            else:
                matches = item.comparison_name == equivalent
            if matches:
                context.push(output, item)
    elif operator.id == "ini.entry-key-equals":
        expected = _string_argument(operator, "key")
        comparison = _string_argument(operator, "comparison")
        equivalent = _key_comparison(context.document.profile, expected)
        for item in input_matches:
            if item.kind is not IniMatchKind.ENTRY:
                continue
            if comparison == "OriginalExact":
                matches = item.key == expected
            else:
                matches = item.comparison_key == equivalent
            if matches:
                context.push(output, item)
    elif operator.id == "ini.entry-value-state-is":
        expected = IniValueState(_string_argument(operator, "state"))
        for item in input_matches:
            if item.kind is IniMatchKind.ENTRY and item.value_state is expected:
                context.push(output, item)
    elif operator.id == "ini.duplicate-group":
        for item in input_matches:
            if item.kind is IniMatchKind.SECTION and item.duplicate_group is not None:
                for ordinal in range(len(context.document.sections)):
                    if context.document.sections[ordinal].duplicate_group == item.duplicate_group:
                        context.push(output, context.section_match(ordinal))
            elif item.kind is IniMatchKind.ENTRY and item.duplicate_group is not None:
                for ordinal in range(len(context.document.entries)):
                    if context.document.entries[ordinal].duplicate_group == item.duplicate_group:
                        context.push(output, context.entry_match(ordinal))
    elif operator.id == "ini.physical-lines":
        for item in input_matches:
            if item.kind is IniMatchKind.DOCUMENT:
                for ordinal, line in enumerate(context.document.physical_lines):
                    context.push(
                        output,
                        IniMatch(
                            kind=IniMatchKind.PHYSICAL_LINE,
                            node=line.node,
                            ordinal=ordinal,
                            span=line.span,
                        ),
                    )
    elif operator.id == "ini.logical-lines":
        for item in input_matches:
            if item.kind is IniMatchKind.DOCUMENT:
                for ordinal, line in enumerate(context.document.logical_lines):
                    context.push(
                        output,
                        IniMatch(
                            kind=IniMatchKind.LOGICAL_LINE,
                            node=line.node,
                            ordinal=ordinal,
                            logical_kind=line.kind,
                        ),
                    )
    elif operator.id == "core.take":
        count = _integer_argument(operator)
        for item in input_matches[:count]:
            context.push(output, item)
    elif operator.id == "core.distinct-by-identity":
        seen = set()
        for item in input_matches:
            if item.identity not in seen:
                seen.add(item.identity)
                context.push(output, item)
    else:
        raise QueryFailure(QueryFailureKind.UNKNOWN_OPERATOR, operator=operator.id, version=operator.version)
    context.step(len(output))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[IniSyntaxMatch], context: _Context
) -> list[IniSyntaxMatch]:
    output: list[IniSyntaxMatch] = []
    if operator.id == "ini.syntax-kind-is":
        expected = IniSyntaxKind.from_name(_string_argument(operator, "kind"))
        if expected is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator=operator.id,
                argument="kind",
            )
        for item in input_matches:
            if item.kind is expected:
                context.push(output, item)
    elif operator.id == "ini.syntax-text-equals":
        expected = _string_argument(operator, "text")
        for item in input_matches:
            if _decoded_span_text(context.document, item.span) == expected:
                context.push(output, item)
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


def _source_start(document: IniDocument, item: IniMatch) -> int:
    """Native match source-order start byte (query.rs:627-659)."""
    if item.kind is IniMatchKind.DOCUMENT:
        return 0
    if item.kind is IniMatchKind.SECTION:
        return document.resolve_section(item.node).span.start_byte
    if item.kind is IniMatchKind.ENTRY:
        return document.resolve_entry(item.node).span.start_byte
    if item.kind is IniMatchKind.PHYSICAL_LINE:
        assert item.span is not None
        return item.span.start_byte
    logical = document.resolve_logical_line(item.node)
    if logical.physical_nodes:
        return document.resolve_physical_line(logical.physical_nodes[0]).span.start_byte
    return 0


def _source_ordinal(item: IniMatch) -> int:
    return item.ordinal if item.ordinal is not None else 0


def _decoded_span_text(document: IniDocument, span: Span) -> str:
    """Decoded Unicode scalar text of one exact raw piece span
    (query.rs:661-676; RFC 0009 §9, docs/rfcs/0009-...:337-341).

    The decoded coordinates are UTF-8 byte offsets into the decoded text
    (Rust &str byte slicing), so the slice happens on the UTF-8 encoding,
    never on Python scalar indices — this keeps UTF-16LE and code-page
    sources identical to UTF-8.
    """
    start = document.source.decoded_position(span.start_byte).decoded_utf8_byte
    end = document.source.decoded_position(span.end_byte).decoded_utf8_byte
    text = document.source.decoded_text()
    assert text is not None
    return text.encode("utf-8")[start:end].decode("utf-8")


def _section_comparison(profile: IniProfile, name: str) -> str:
    """Profile-specific section comparison (query.rs:678-683)."""
    if profile is IniProfile.WINDOWS_V1:
        return name.lower()
    return name


def _key_comparison(profile: IniProfile, key: str) -> str:
    """Profile-specific key comparison (query.rs:685-691)."""
    if profile is IniProfile.WINDOWS_V1:
        return key.lower()
    if profile is IniProfile.PYTHON_CONFIGPARSER_V1:
        from consema.ini.python_case import optionxform

        return optionxform(key)
    return key


def _string_argument(operator, name: str) -> str:
    value = operator.arguments.get(name)
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument=name
        )
    return value.as_string()


def _integer_argument(operator) -> int:
    value = operator.arguments.get("count")
    if value is None:
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument="count"
        )
    return value.as_integer()
