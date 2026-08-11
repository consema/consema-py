"""Plist three-domain query execution (RFC 0013 §8).

Authority (Rust arbitration for the executor semantics):

- Domain binding and versioning: crates/consema-plist/src/query.rs:269-459
  — ``plist.native-semantic-query@1`` serves both representations
  (query.rs:269-283), ``plist.lossless-syntax-query@1`` exists only for
  ``plist.xml@1`` (query.rs:322-335), and ``plist.binary-structure-query@1``
  exists only for ``plist.binary@1`` (query.rs:388-402); a domain applied
  to a document of the wrong representation is a DomainMismatch (hard
  gate 1).
- Native operators: query.rs:817-1160 — plist.document-root@1,
  plist.dict-entries@1, plist.dict-entry-key@1, plist.dict-entry-value@1,
  plist.dict-key-equals@1, plist.duplicate-key-group@1, plist.array-
  elements@1, plist.value-type-is@1, plist.value-as-integer@1,
  plist.value-as-real@1, plist.value-as-string@1, plist.value-as-data@1,
  plist.value-as-date@1, plist.value-as-uid@1, plist.value-as-boolean-is@1
  (the exact RFC 0013 §8.1 surface, docs/rfcs/0013-...:543-558); the
  closed kind names of ``plist.value-type-is@1`` (query.rs:1188-1200).
- Syntax operators: query.rs:1260-1280 (plist.syntax-kind-is@1,
  plist.syntax-text-equals@1); binary structure operators query.rs:1335-
  1358 (plist.object-table@1, plist.top-object@1, plist.object-offset@1,
  plist.offset-table@1, plist.object-refs@1, plist.trailer-facts@1).
- Matches: query.rs:55-126 (PlistMatch), 128-162 (PlistSyntaxMatch),
  164-264 (PlistBinaryMatch).
- Selection algebra and limits: consema-core/src/query.rs:2967-2981
  (QueryLimits defaults max_steps=100_000, max_results=100_000);
  typed-accessor type mismatch is the runner-mapped
  ``plist.query.type-mismatch@1`` (crates/consema-conformance/src/
  plist_v1.rs:1149).
- Expression evaluation and StructureOrderMerge: the common query model of
  consema.protocol.query (RFC 0016 §5.4), as the other families use.

The transferable query model (QueryDomain, QueryExpression, OperatorCall,
QuerySelection, QueryDefinition, ValidatedQuery, ExecutableQuery,
QueryFailure) is implemented in consema.protocol.query; this module binds
an ExecutableQuery to one immutable snapshot and produces deterministic
ordered matches.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import NodeRef, NodeRole, Span
from consema.plist.document import PlistDocument, PlistRepresentation
from consema.plist.kinds import PlistSyntaxKind
from consema.plist.native import PlistKey, PlistValueKind, PlistValueRef
from consema.protocol.query import (
    ExecutableQuery,
    ExpressionKind,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

NATIVE_DOMAIN_ID = "plist.native-semantic-query"
SYNTAX_DOMAIN_ID = "plist.lossless-syntax-query"
BINARY_DOMAIN_ID = "plist.binary-structure-query"


class PlistMatchKind(enum.Enum):
    """Match role of one native query result (query.rs:60-114)."""

    DOCUMENT = "Document"
    VALUE = "Value"
    DICT_ENTRY = "DictEntry"
    KEY = "Key"
    ARRAY_ELEMENT = "ArrayElement"


@dataclass(frozen=True, slots=True)
class PlistMatch:
    """One snapshot-bound native semantic query match (query.rs:60-114).

    Value matches reference the arena of the queried document, so shared
    identity from the binary object table survives querying: one native
    node referenced by several containers is one match identity.
    """

    kind: PlistMatchKind
    node: NodeRef
    value: PlistValueRef | None = None
    dict: PlistValueRef | None = None
    position: int | None = None
    key: PlistKey | None = None
    value_kind: PlistValueKind | None = None
    array: PlistValueRef | None = None

    @property
    def identity(self) -> NodeRef:
        """Stable match identity (query.rs:116-126)."""
        return self.node


@dataclass(frozen=True, slots=True)
class PlistSyntaxMatch:
    """One snapshot-bound lossless syntax query match (query.rs:128-162)."""

    node: NodeRef
    span: Span
    kind: PlistSyntaxKind
    ordinal: int


class PlistBinaryMatchKind(enum.Enum):
    """Match role of one binary structure query result (query.rs:164-251)."""

    STRUCTURE = "Structure"
    OBJECT = "Object"
    OFFSET = "Offset"
    REF = "Ref"
    TRAILER = "Trailer"
    TOP_OBJECT = "TopObject"


@dataclass(frozen=True, slots=True)
class PlistBinaryMatch:
    """One snapshot-bound binary structure query match (query.rs:164-251).

    The binary structure facts are document-level: ``Structure`` is the
    domain root, and every structure operator projects its fact set once
    from any binary-structure match.
    """

    kind: PlistBinaryMatchKind
    node: NodeRef
    index: int | None = None
    offset: int | None = None
    marker: int | None = None
    span: Span | None = None
    owner: int | None = None
    position: int | None = None
    target: int | None = None
    sort_version: int | None = None
    offset_int_size: int | None = None
    object_ref_size: int | None = None
    num_objects: int | None = None
    top_object: int | None = None
    offset_table_offset: int | None = None
    refs: tuple[tuple[int, int, Span], ...] = ()

    @property
    def identity(self) -> NodeRef:
        return self.node


@dataclass(frozen=True, slots=True)
class PlistQueryExecution:
    """Complete ordered query result."""

    matches: tuple[object, ...]


class PlistCancellationToken:
    """Cooperative cancellation signal."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class PlistQueryLimits:
    """Query resource limits (consema-core/src/query.rs:2967-2981)."""

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results


class _Context:
    """Execution state shared by the three domains: budget accounting,
    cancellation, and fresh snapshot-bound node identities."""

    def __init__(
        self,
        document: PlistDocument,
        limits: PlistQueryLimits,
        cancellation: PlistCancellationToken,
    ) -> None:
        self.document = document
        self.limits = limits
        self.cancellation = cancellation
        self.steps = 0
        self.next_node = 1  # 0 is the document root node

    def step(self, results: int) -> None:
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

    def issue(self, role: NodeRole) -> NodeRef:
        node = self.document.authority.node_ref(self.next_node, role)
        self.next_node += 1
        return node

    def value_match(self, value: PlistValueRef) -> PlistMatch:
        native = self.document.document()
        assert native is not None
        node = native.get(value)
        kind = node.kind if node is not None else PlistValueKind.STRING
        return PlistMatch(
            kind=PlistMatchKind.VALUE,
            node=self.issue(NodeRole.PLIST_VALUE),
            value=value,
            value_kind=kind,
        )


# ---------------------------------------------------------------------------
# Native domain
# ---------------------------------------------------------------------------


def execute_plist_native_query(
    executable: ExecutableQuery,
    document: PlistDocument,
    limits: PlistQueryLimits,
    cancellation: PlistCancellationToken,
) -> PlistQueryExecution:
    """Executes a validated plist native semantic query (query.rs:269-283).

    The native domain serves both representations; only the domain identity
    is guarded here."""
    definition = executable.definition
    if (
        definition.domain.id != NATIVE_DOMAIN_ID
        or definition.domain.version != 1
    ):
        raise QueryFailure(
            QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    context = _Context(document, limits, cancellation)
    context.step(1)
    input_matches: list[PlistMatch] = [
        PlistMatch(
            kind=PlistMatchKind.DOCUMENT,
            node=document.node_ref(),
        )
    ]
    matches = _execute_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return PlistQueryExecution(matches=tuple(matches))


def execute_plist_syntax_query(
    executable: ExecutableQuery,
    document: PlistDocument,
    limits: PlistQueryLimits,
    cancellation: PlistCancellationToken,
) -> PlistQueryExecution:
    """Executes a validated plist lossless syntax query (query.rs:322-335).

    The domain exists only for the ``plist.xml@1`` representation; a binary
    document is a DomainMismatch (hard gate 1)."""
    definition = executable.definition
    if (
        definition.domain.id != SYNTAX_DOMAIN_ID
        or definition.domain.version != 1
    ):
        raise QueryFailure(
            QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    index = document.lossless_structural_index()
    kinds = document.lossless_syntax_kinds()
    if document.representation() is not PlistRepresentation.XML or index is None or kinds is None:
        raise QueryFailure(
            QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    context = _Context(document, limits, cancellation)
    context.step(len(index.pieces))
    input_matches = [
        PlistSyntaxMatch(
            node=document.authority.node_ref(ordinal, NodeRole.PLIST_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(index.pieces)
    ]
    matches = _execute_syntax_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return PlistQueryExecution(matches=tuple(matches))


def execute_plist_binary_query(
    executable: ExecutableQuery,
    document: PlistDocument,
    limits: PlistQueryLimits,
    cancellation: PlistCancellationToken,
) -> PlistQueryExecution:
    """Executes a validated plist binary structure query (query.rs:388-402).

    The domain exists only for the ``plist.binary@1`` representation; an
    XML document is a DomainMismatch (hard gate 1)."""
    definition = executable.definition
    if (
        definition.domain.id != BINARY_DOMAIN_ID
        or definition.domain.version != 1
    ):
        raise QueryFailure(
            QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    facts = document.binary_facts()
    if document.representation() is not PlistRepresentation.BINARY or facts is None:
        raise QueryFailure(
            QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    context = _Context(document, limits, cancellation)
    context.step(1)
    input_matches: list[PlistBinaryMatch] = [
        PlistBinaryMatch(
            kind=PlistBinaryMatchKind.STRUCTURE,
            node=document.node_ref(),
        )
    ]
    matches = _execute_binary_expression(definition.expression, input_matches, context)
    matches = _apply_selection(matches, definition.selection)
    return PlistQueryExecution(matches=tuple(matches))


# ---------------------------------------------------------------------------
# Expression evaluation (the common model of consema.protocol.query)
# ---------------------------------------------------------------------------


def _execute_expression(
    expression: QueryExpression,
    input_matches: list[PlistMatch],
    context: _Context,
) -> list[PlistMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[PlistMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    # StructureOrderMerge: source-order by (start, end, entity index).
    output = []
    for branch in expression.branches:
        output.extend(_execute_expression(branch, input_matches, context))
    output.sort(
        key=lambda item: (
            _start_byte(context.document, item),
            _end_byte(context.document, item),
            item.identity.index,
        )
    )
    return output


def _start_byte(document: PlistDocument, match: object) -> int:
    if isinstance(match, PlistSyntaxMatch):
        return match.span.start_byte
    return 0


def _end_byte(document: PlistDocument, match: object) -> int:
    if isinstance(match, PlistSyntaxMatch):
        return match.span.end_byte
    return 0


def _apply_operator(operator, input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """One native-domain operator (query.rs:814-1160)."""
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    id_string = operator.id
    if id_string == "plist.document-root":
        return _document_root(input_matches, context)
    if id_string == "plist.dict-entries":
        return _dict_entries(input_matches, context)
    if id_string == "plist.dict-entry-key":
        return _dict_entry_key(input_matches, context)
    if id_string == "plist.dict-entry-value":
        return _dict_entry_value(input_matches, context)
    if id_string == "plist.dict-key-equals":
        return _dict_key_equals(operator, input_matches, context)
    if id_string == "plist.duplicate-key-group":
        return _duplicate_key_group(input_matches, context)
    if id_string == "plist.array-elements":
        return _array_elements(input_matches, context)
    if id_string == "plist.value-type-is":
        return _value_type_is(operator, input_matches, context)
    if id_string == "plist.value-as-integer":
        return _value_as_typed("integer", input_matches, context)
    if id_string == "plist.value-as-real":
        return _value_as_typed("real", input_matches, context)
    if id_string == "plist.value-as-string":
        return _value_as_typed("string", input_matches, context)
    if id_string == "plist.value-as-data":
        return _value_as_typed("data", input_matches, context)
    if id_string == "plist.value-as-date":
        return _value_as_typed("date", input_matches, context)
    if id_string == "plist.value-as-uid":
        return _value_as_typed("uid", input_matches, context)
    if id_string == "plist.value-as-boolean-is":
        return _value_as_boolean_is(operator, input_matches, context)
    if id_string == "core.take":
        return input_matches
    if id_string == "core.distinct-by-identity":
        seen = set()
        output = []
        for match in input_matches:
            if match.identity not in seen:
                seen.add(match.identity)
                output.append(match)
        return output
    raise QueryFailure(
        QueryFailureKind.UNKNOWN_OPERATOR, operator=id_string, version=operator.version
    )


def _document_root(input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """``plist.document-root``: the root value, when formation proved it
    (query.rs:852-876)."""
    native = context.document.document()
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.DOCUMENT:
            continue
        if native is None:
            continue
        context.push(
            output,
            PlistMatch(
                kind=PlistMatchKind.VALUE,
                node=context.issue(NodeRole.PLIST_VALUE),
                value=native.root(),
                value_kind=native.root_value().kind,
            ),
        )
        context.step(len(output))
    return output


def _dict_entries(input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """``plist.dict-entries``: the ordered associations of every dictionary
    value match (query.rs:878-899)."""
    native = context.document.document()
    assert native is not None
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.VALUE:
            continue
        value = native.get(match.value)
        if value is None or value.kind is not PlistValueKind.DICT:
            continue
        dict_value = value.payload
        for position, entry in enumerate(dict_value.entries):
            entry_value = native.get(entry.value)
            entry_kind = entry_value.kind if entry_value is not None else PlistValueKind.STRING
            context.push(
                output,
                PlistMatch(
                    kind=PlistMatchKind.DICT_ENTRY,
                    node=context.issue(NodeRole.PLIST_DICT_ENTRY),
                    dict=match.value,
                    position=position,
                    key=entry.key,
                    value=entry.value,
                    value_kind=entry_kind,
                ),
            )
        context.step(len(output))
    return output


def _dict_entry_key(input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """``plist.dict-entry-key``: the string key identity of every entry
    match (query.rs:901-928)."""
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.DICT_ENTRY:
            continue
        context.push(
            output,
            PlistMatch(
                kind=PlistMatchKind.KEY,
                node=context.issue(NodeRole.PLIST_KEY),
                dict=match.dict,
                position=match.position,
                key=match.key,
            ),
        )
        context.step(len(output))
    return output


def _dict_entry_value(input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """``plist.dict-entry-value``: the associated value of every entry
    match (query.rs:929-953)."""
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.DICT_ENTRY:
            continue
        context.push(output, context.value_match(match.value))
        context.step(len(output))
    return output


def _dict_key_equals(
    operator, input_matches: list[PlistMatch], context: _Context
) -> list[PlistMatch]:
    """``plist.dict-key-equals``: exact Unicode key equality; case is never
    folded (query.rs:954-974; RFC 0013 §8.1)."""
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    argument = operator.arguments["key"].as_string()
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.DICT_ENTRY:
            continue
        if match.key == PlistKey.from_unicode(argument):
            context.push(output, match)
        context.step(len(output))
    return output


def _duplicate_key_group(
    input_matches: list[PlistMatch], context: _Context
) -> list[PlistMatch]:
    """``plist.duplicate-key-group``: expands one entry match to every
    same-key association of the same dictionary in source order
    (query.rs:975-999)."""
    native = context.document.document()
    assert native is not None
    output: list[PlistMatch] = []
    seen: set[tuple[int, int]] = set()
    for match in input_matches:
        if match.kind is not PlistMatchKind.DICT_ENTRY:
            continue
        key = match.key
        dict_index = match.dict.index
        if (dict_index, match.position) in seen:
            continue
        seen.add((dict_index, match.position))
        value = native.get(match.dict)
        if value is None or value.kind is not PlistValueKind.DICT:
            continue
        for position, entry in enumerate(value.payload.entries):
            if entry.key == key:
                entry_value = native.get(entry.value)
                entry_kind = entry_value.kind if entry_value is not None else PlistValueKind.STRING
                context.push(
                    output,
                    PlistMatch(
                        kind=PlistMatchKind.DICT_ENTRY,
                        node=context.issue(NodeRole.PLIST_DICT_ENTRY),
                        dict=match.dict,
                        position=position,
                        key=entry.key,
                        value=entry.value,
                        value_kind=entry_kind,
                    ),
                )
        context.step(len(output))
    return output


def _array_elements(input_matches: list[PlistMatch], context: _Context) -> list[PlistMatch]:
    """``plist.array-elements``: the ordered element associations of every
    array value match (query.rs:1000-1035)."""
    native = context.document.document()
    assert native is not None
    output: list[PlistMatch] = []
    for match in input_matches:
        if match.kind is not PlistMatchKind.VALUE:
            continue
        value = native.get(match.value)
        if value is None or value.kind is not PlistValueKind.ARRAY:
            continue
        for position, element in enumerate(value.payload.elements):
            element_value = native.get(element)
            element_kind = element_value.kind if element_value is not None else PlistValueKind.STRING
            context.push(
                output,
                PlistMatch(
                    kind=PlistMatchKind.ARRAY_ELEMENT,
                    node=context.issue(NodeRole.PLIST_ARRAY_ELEMENT),
                    array=match.value,
                    position=position,
                    value=element,
                    value_kind=element_kind,
                ),
            )
        context.step(len(output))
    return output


def _value_type_is(
    operator, input_matches: list[PlistMatch], context: _Context
) -> list[PlistMatch]:
    """``plist.value-type-is``: keeps value-bearing matches of exactly the
    closed kind name (query.rs:1036-1083)."""
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    wanted = operator.arguments["kind"].as_string()
    output: list[PlistMatch] = []
    for match in input_matches:
        kind = _value_kind_of(match)
        if kind is not None and kind == wanted:
            context.push(output, match)
        context.step(len(output))
    return output


def _value_kind_of(match: PlistMatch) -> str | None:
    if match.kind is PlistMatchKind.VALUE:
        return match.value_kind.value if match.value_kind is not None else None
    if match.kind in (PlistMatchKind.DICT_ENTRY, PlistMatchKind.ARRAY_ELEMENT):
        return match.value_kind.value if match.value_kind is not None else None
    return None


def _value_as_typed(
    wanted: str, input_matches: list[PlistMatch], context: _Context
) -> list[PlistMatch]:
    """The ``plist.value-as-*@1`` typed accessors: validate the value type
    before returning; a type mismatch is a query failure (RFC 0013 §8.1;
    query.rs:1084-1158)."""
    output: list[PlistMatch] = []
    for match in input_matches:
        kind = _value_kind_of(match)
        if kind is None:
            context.push(output, match)
            continue
        if kind != wanted:
            raise QueryFailure(
                QueryFailureKind.REQUIRED_TYPE_MISMATCH,
                operator=f"plist.value-as-{wanted}",
                version=1,
                expected_kind=wanted,
            )
        context.push(output, match)
        context.step(len(output))
    return output


def _value_as_boolean_is(
    operator, input_matches: list[PlistMatch], context: _Context
) -> list[PlistMatch]:
    """``plist.value-as-boolean-is``: validates that every value-bearing
    match is a boolean of the argument value (query.rs:1084-1158)."""
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    wanted = operator.arguments["value"].as_string()
    native = context.document.document()
    assert native is not None
    output: list[PlistMatch] = []
    for match in input_matches:
        value = _match_value(native, match)
        if value is None:
            context.push(output, match)
            continue
        if value.kind is not PlistValueKind.BOOLEAN:
            raise QueryFailure(
                QueryFailureKind.REQUIRED_TYPE_MISMATCH,
                operator="plist.value-as-boolean-is",
                version=1,
                expected_kind="boolean",
            )
        if str(value.payload.value).lower() != wanted.lower():
            context.step(len(output))
            continue
        context.push(output, match)
        context.step(len(output))
    return output


def _match_value(native, match: PlistMatch):
    if match.kind is PlistMatchKind.VALUE:
        return native.get(match.value)
    if match.kind in (PlistMatchKind.DICT_ENTRY, PlistMatchKind.ARRAY_ELEMENT):
        return native.get(match.value)
    return None


# ---------------------------------------------------------------------------
# Syntax domain
# ---------------------------------------------------------------------------


def _execute_syntax_expression(
    expression: QueryExpression,
    input_matches: list[PlistSyntaxMatch],
    context: _Context,
) -> list[PlistSyntaxMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[PlistSyntaxMatch] = []
        for branch in expression.branches:
            output.extend(_execute_syntax_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    output = []
    for branch in expression.branches:
        output.extend(_execute_syntax_expression(branch, input_matches, context))
    output.sort(key=lambda item: (item.span.start_byte, item.span.end_byte, item.node.index))
    return output


def _apply_syntax_operator(
    operator, input_matches: list[PlistSyntaxMatch], context: _Context
) -> list[PlistSyntaxMatch]:
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    id_string = operator.id
    if id_string == "plist.syntax-kind-is":
        wanted = operator.arguments["kind"].as_string()
        output = [
            match for match in input_matches if match.kind.as_str() == wanted
        ]
        context.step(len(output))
        return output
    if id_string == "plist.syntax-text-equals":
        wanted = operator.arguments["text"].as_string()
        output = []
        for match in input_matches:
            raw = context.document.source.bytes()
            text = raw[match.span.start_byte : match.span.end_byte].decode("utf-8")
            if text == wanted:
                output.append(match)
            context.step(len(output))
        return output
    if id_string == "core.take":
        return input_matches
    if id_string == "core.distinct-by-identity":
        seen = set()
        output = []
        for match in input_matches:
            if match.node not in seen:
                seen.add(match.node)
                output.append(match)
        return output
    raise QueryFailure(
        QueryFailureKind.UNKNOWN_OPERATOR, operator=id_string, version=operator.version
    )


# ---------------------------------------------------------------------------
# Binary structure domain
# ---------------------------------------------------------------------------


def _execute_binary_expression(
    expression: QueryExpression,
    input_matches: list[PlistBinaryMatch],
    context: _Context,
) -> list[PlistBinaryMatch]:
    if expression.kind is ExpressionKind.INPUT:
        return list(input_matches)
    if expression.kind is ExpressionKind.APPLY:
        inner = _execute_binary_expression(expression.input, input_matches, context)
        return _apply_binary_operator(expression.operator, inner, context)
    if expression.kind is ExpressionKind.CONCAT:
        output: list[PlistBinaryMatch] = []
        for branch in expression.branches:
            output.extend(_execute_binary_expression(branch, input_matches, context))
            context.step(len(output))
        return output
    output = []
    for branch in expression.branches:
        output.extend(_execute_binary_expression(branch, input_matches, context))
    output.sort(key=lambda item: (item.span.start_byte if item.span else 0, item.node.index))
    return output


def _apply_binary_operator(
    operator, input_matches: list[PlistBinaryMatch], context: _Context
) -> list[PlistBinaryMatch]:
    from consema.protocol.query import OperatorCall

    assert isinstance(operator, OperatorCall)
    id_string = operator.id
    facts = context.document.binary_facts()
    assert facts is not None
    output: list[PlistBinaryMatch] = []
    if id_string in ("plist.object-table", "plist.top-object"):
        output = _object_facts(facts, context, top_only=id_string == "plist.top-object")
    elif id_string in ("plist.object-offset", "plist.offset-table"):
        for fact in facts.offsets:
            context.push(
                output,
                PlistBinaryMatch(
                    kind=PlistBinaryMatchKind.OFFSET,
                    node=context.issue(NodeRole.PLIST_DOCUMENT),
                    index=fact.index,
                    offset=fact.offset,
                    span=fact.span,
                ),
            )
        context.step(len(output))
    elif id_string == "plist.object-refs":
        for index, fact in enumerate(facts.refs):
            context.push(
                output,
                PlistBinaryMatch(
                    kind=PlistBinaryMatchKind.REF,
                    node=context.issue(NodeRole.PLIST_DOCUMENT),
                    index=index,
                    owner=fact.owner,
                    position=fact.position,
                    target=fact.target,
                    span=fact.span,
                ),
            )
        context.step(len(output))
    elif id_string == "plist.trailer-facts":
        trailer = facts.trailer
        context.push(
            output,
            PlistBinaryMatch(
                kind=PlistBinaryMatchKind.TRAILER,
                node=context.issue(NodeRole.PLIST_DOCUMENT),
                sort_version=trailer.sort_version,
                offset_int_size=trailer.offset_int_size,
                object_ref_size=trailer.object_ref_size,
                num_objects=trailer.num_objects,
                top_object=trailer.top_object,
                offset_table_offset=trailer.offset_table_offset,
                span=trailer.span,
            ),
        )
        context.step(len(output))
    elif id_string == "core.take":
        return input_matches
    elif id_string == "core.distinct-by-identity":
        seen = set()
        for match in input_matches:
            if match.node not in seen:
                seen.add(match.node)
                output.append(match)
        return output
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=id_string, version=operator.version
        )
    return output


def _object_facts(
    facts, context: _Context, top_only: bool
) -> list[PlistBinaryMatch]:
    """``plist.object-table`` / ``plist.top-object``: the object facts, or
    only the trailer's top object with its ordered reference facts
    (query.rs:1351-1412)."""
    output: list[PlistBinaryMatch] = []
    trailer = facts.trailer
    if top_only:
        target = trailer.top_object
        facts_by_index = {fact.index: fact for fact in facts.objects}
        fact = facts_by_index.get(target)
        if fact is None:
            return output
        refs = tuple(
            (ref.position, ref.target, ref.span)
            for ref in facts.refs
            if ref.owner == target
        )
        context.push(
            output,
            PlistBinaryMatch(
                kind=PlistBinaryMatchKind.TOP_OBJECT,
                node=context.issue(NodeRole.PLIST_DOCUMENT),
                index=fact.index,
                offset=fact.offset,
                marker=fact.marker,
                span=fact.span,
                refs=refs,
            ),
        )
        context.step(len(output))
        return output
    for fact in facts.objects:
        context.push(
            output,
            PlistBinaryMatch(
                kind=PlistBinaryMatchKind.OBJECT,
                node=context.issue(NodeRole.PLIST_DOCUMENT),
                index=fact.index,
                offset=fact.offset,
                marker=fact.marker,
                span=fact.span,
            ),
        )
        context.step(len(output))
    return output


# ---------------------------------------------------------------------------
# Selection algebra (query.rs:693-710)
# ---------------------------------------------------------------------------


def _apply_selection(matches: list, selection: QuerySelection) -> list:
    if selection is QuerySelection.ALL:
        return matches
    if selection is QuerySelection.FIRST:
        return matches[:1]
    if selection is QuerySelection.LAST:
        return matches[-1:]
    if selection is QuerySelection.ZERO_OR_ONE:
        if len(matches) > 1:
            raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
        return matches
    # RequireOne
    if len(matches) != 1:
        raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
    return matches
