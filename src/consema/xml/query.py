"""Execution of the XML native and lossless-syntax query domains.

Authority:

- RFC 0012 §8 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:285-311): the
  frozen domains ``xml.native-semantic-query@1`` and
  ``xml.lossless-syntax-query@1``; native order is document order; element
  attributes and namespace declarations preserve their respective source
  orders; child content preserves mixed-content order; descendant
  traversal is bounded pre-order; no query resolves a URI, evaluates
  XPath, loads a schema, or expands application data; the lossless domain
  supports syntax-kind/text filtering, exact source order, selection,
  core.take@1, core.distinct-by-identity@1, limits, cancellation, and
  explicit Completed/Cancelled/Failed terminal states.
- The match shapes and every operator transcribe
  crates/consema-xml/src/query.rs:22-220 (XmlMatch), 187-220
  (XmlSyntaxMatch), 223-249 (execute_xml_query), 286-335
  (execute_xml_syntax_query), 578-622 (operator dispatch), 624-1376
  (operator implementations) — byte/registry arbitration only.
- The query definition/validation model lives in consema.protocol
  (query.py:649-691 freeze the xml operator rows and the syntax-kind
  vocabulary at query.py:1109-1121); the executor below consumes a
  validated ``ExecutableQuery``.
- QueryLimits and CancellationToken follow the shared records of
  consema.protocol.query (RFC 0016 §5.4); the frozen defaults are the core
  query.rs ones (max_steps/max_results 100_000).

go/xml is a cross-reference only; no code structure is copied.
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

from consema.xml.document import (
    Document,
    ReferenceFragmentKind,
    XmlContentKind,
    XmlPrologItemKind,
    text_semantic,
)
from consema.xml.kinds import XmlSyntaxKind


class QueryLimits:
    """Execution resource limits (crates/consema-core/src/query.rs)."""

    __slots__ = ("max_steps", "max_results")

    def __init__(self, max_steps: int = 100_000, max_results: int = 100_000) -> None:
        self.max_steps = max_steps
        self.max_results = max_results

    @classmethod
    def default(cls) -> QueryLimits:
        return cls()


class CancellationToken:
    """Process-local cancellation signal."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class XmlMatchKind(enum.Enum):
    """Closed native match category (query.rs:31-165)."""

    DOCUMENT = "document"
    DECLARATION = "declaration"
    DOCTYPE = "doctype"
    PROLOG_ITEM = "prolog-item"
    ELEMENT = "element"
    ATTRIBUTE = "attribute"
    NAMESPACE_BINDING = "namespace-binding"
    TEXT = "text"
    CDATA = "cdata"
    COMMENT = "comment"
    PROCESSING_INSTRUCTION = "processing-instruction"
    REFERENCE = "reference"
    ERROR_REGION = "error-region"


class XmlReferenceKind(enum.Enum):
    """One XML reference occurrence kind (query.rs:20-29)."""

    CHARACTER = "Character"
    PREDEFINED = "Predefined"
    GENERAL = "General"


@dataclass(frozen=True, slots=True)
class XmlMatch:
    """One snapshot-bound XML native semantic query match (query.rs:31-165)."""

    kind: XmlMatchKind
    node: NodeRef
    parent: NodeRef | None = None
    prefix: str | None = None
    local: str | None = None
    namespace: str | None = None
    namespace_error: bool = False
    value: str | None = None
    semantic: str | None = None
    text: str | None = None
    target: str | None = None
    content: str | None = None
    version: str | None = None
    encoding: str | None = None
    standalone: bool | None = None
    name: str | None = None
    resolved: str | None = None
    reference_kind: XmlReferenceKind | None = None
    span: Span | None = None
    element: NodeRef | None = None

    def identity(self) -> NodeRef:
        """The identity used by core.distinct-by-identity and
        structure-order-merge (query.rs:167-185)."""
        return self.node


@dataclass(frozen=True, slots=True)
class XmlSyntaxMatch:
    """One snapshot-bound XML lossless syntax query match (query.rs:187-220)."""

    node: NodeRef
    span: Span
    kind: XmlSyntaxKind
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

    def push(self, output: list, value) -> None:
        if len(output) + 1 > self.limits.max_results:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        output.append(value)

    def element_match(self, index: int) -> XmlMatch:
        data = self.document._element_data(index)
        return XmlMatch(
            kind=XmlMatchKind.ELEMENT,
            node=self.document.node_ref(index, NodeRole.XML_ELEMENT),
            parent=self.parent_of(index),
            prefix=data.qname.prefix,
            local=data.qname.local,
            namespace=data.expanded.namespace if data.expanded is not None else None,
            namespace_error=data.namespace_error is not None,
        )

    def parent_of(self, index: int) -> NodeRef | None:
        parent = self.document._parent_of_index(index)
        if parent is None:
            return None
        return self.document.node_ref(parent, NodeRole.XML_ELEMENT)

    def attribute_match(self, data, element: NodeRef) -> XmlMatch:
        return XmlMatch(
            kind=XmlMatchKind.ATTRIBUTE,
            node=self.document.node_ref(data.ordinal, NodeRole.XML_ATTRIBUTE),
            element=element,
            prefix=data.qname.prefix,
            local=data.qname.local,
            namespace=data.expanded.namespace if data.expanded is not None else None,
            value=data.normalized_value,
        )

    def namespace_binding_match(self, binding, element: NodeRef) -> XmlMatch:
        return XmlMatch(
            kind=XmlMatchKind.NAMESPACE_BINDING,
            node=self.document.node_ref(binding.ordinal, NodeRole.XML_NAMESPACE_BINDING),
            element=element,
            prefix=binding.prefix,
            name=binding.uri,
        )


def _require_domain(executable: ExecutableQuery, expected_id: str) -> QueryDomain:
    domain = executable.definition.domain
    if domain.id != expected_id or domain.version != 1:
        raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=domain)
    return domain


def execute_xml_query(
    executable: ExecutableQuery,
    document: Document,
    limits: QueryLimits | None = None,
    cancellation: CancellationToken | None = None,
) -> list[XmlMatch]:
    """Executes a validated XML native semantic query against one immutable
    snapshot (query.rs:223-249). Raises QueryFailure."""
    _require_domain(executable, "xml.native-semantic-query")
    limits = limits or QueryLimits.default()
    cancellation = cancellation or CancellationToken()
    context = _Context(document, limits, cancellation)
    context.step(0)
    input_matches = [
        XmlMatch(
            kind=XmlMatchKind.DOCUMENT,
            node=document.node_ref(0, NodeRole.XML_DOCUMENT),
        )
    ]
    matches = _execute_expression(executable.definition.expression, input_matches, context)
    return _apply_selection(matches, executable.definition.selection)


def execute_xml_syntax_query(
    executable: ExecutableQuery,
    document: Document,
    limits: QueryLimits | None = None,
    cancellation: CancellationToken | None = None,
) -> list[XmlSyntaxMatch]:
    """Executes a validated XML lossless syntax query against every source
    piece in raw order (query.rs:286-335). Raises QueryFailure."""
    _require_domain(executable, "xml.lossless-syntax-query")
    limits = limits or QueryLimits.default()
    cancellation = cancellation or CancellationToken()
    context = _Context(document, limits, cancellation)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    context.step(len(pieces))
    input_matches = [
        XmlSyntaxMatch(
            node=document.node_ref(ordinal, NodeRole.XML_SYNTAX_PIECE),
            span=piece.span,
            kind=kinds[ordinal],
            ordinal=ordinal,
        )
        for ordinal, piece in enumerate(pieces)
    ]
    matches = _execute_syntax_expression(executable.definition.expression, input_matches, context)
    return _apply_selection(matches, executable.definition.selection)


def _execute_expression(expression, input_matches: list[XmlMatch], context: _Context) -> list[XmlMatch]:
    kind = expression.kind
    if kind is ExpressionKind.INPUT:
        return input_matches
    if kind is ExpressionKind.APPLY:
        nested = _execute_expression(expression.input, input_matches, context)
        return _apply_operator(expression.operator, nested, context)
    if kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
        output: list[XmlMatch] = []
        for branch in expression.branches:
            output.extend(_execute_expression(branch, input_matches, context))
            context.step(len(output))
        if kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
            output.sort(key=_source_order)
            context.step(len(output))
        return output
    raise QueryFailure(QueryFailureKind.INVALID_ARGUMENT, operator="expression", argument="kind")


def _execute_syntax_expression(
    expression, input_matches: list[XmlSyntaxMatch], context: _Context
) -> list[XmlSyntaxMatch]:
    kind = expression.kind
    if kind is ExpressionKind.INPUT:
        return input_matches
    if kind is ExpressionKind.APPLY:
        nested = _execute_syntax_expression(expression.input, input_matches, context)
        return _apply_syntax_operator(expression.operator, nested, context)
    if kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
        output: list[XmlSyntaxMatch] = []
        for branch in expression.branches:
            output.extend(_execute_syntax_expression(branch, input_matches, context))
            context.step(len(output))
        if kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
            output.sort(key=lambda match: match.ordinal)
            context.step(len(output))
        return output
    raise QueryFailure(QueryFailureKind.INVALID_ARGUMENT, operator="expression", argument="kind")


def _source_order(item: XmlMatch) -> int:
    """Document-order key (query.rs:556-576)."""
    if item.kind is XmlMatchKind.DOCUMENT:
        return 0
    if item.kind is XmlMatchKind.ERROR_REGION:
        assert item.span is not None
        return item.span.start_byte
    return item.node.index


def _apply_operator(operator, input_matches: list[XmlMatch], context: _Context) -> list[XmlMatch]:
    output: list[XmlMatch] = []
    operator_id = operator.id
    if operator_id == "xml.document-root":
        root = context.document.root()
        if root is not None:
            for item in input_matches:
                if item.kind is XmlMatchKind.DOCUMENT:
                    context.push(output, context.element_match(root.index))
    elif operator_id == "xml.document-declaration":
        declared = context.document.declaration()
        for item in input_matches:
            if item.kind is XmlMatchKind.DOCUMENT and declared is not None:
                context.push(
                    output,
                    XmlMatch(
                        kind=XmlMatchKind.DECLARATION,
                        node=context.document.node_ref(1, NodeRole.XML_DECLARATION),
                        version=declared.version,
                        encoding=declared.encoding[1] if declared.encoding is not None else None,
                        standalone=declared.standalone[1] if declared.standalone is not None else None,
                    ),
                )
    elif operator_id == "xml.document-doctype":
        doctype = context.document.doctype()
        for item in input_matches:
            if item.kind is XmlMatchKind.DOCUMENT and doctype is not None:
                context.push(
                    output,
                    XmlMatch(
                        kind=XmlMatchKind.DOCTYPE,
                        node=context.document.node_ref(2, NodeRole.XML_DOCTYPE),
                        name=doctype.name.qname().as_str,
                    ),
                )
    elif operator_id in ("xml.document-prolog", "xml.document-epilog"):
        items = (
            context.document.prolog()
            if operator_id == "xml.document-prolog"
            else context.document.epilog()
        )
        for item in input_matches:
            if item.kind is not XmlMatchKind.DOCUMENT:
                continue
            for prolog_item in items:
                if prolog_item.kind is XmlPrologItemKind.PROCESSING_INSTRUCTION:
                    pi = prolog_item.data
                    context.push(
                        output,
                        XmlMatch(
                            kind=XmlMatchKind.PROLOG_ITEM,
                            node=context.document.node_ref(
                                pi.ordinal, NodeRole.XML_PROCESSING_INSTRUCTION
                            ),
                            name="processing-instruction",
                        ),
                    )
                elif prolog_item.kind is XmlPrologItemKind.COMMENT:
                    comment = prolog_item.data
                    context.push(
                        output,
                        XmlMatch(
                            kind=XmlMatchKind.PROLOG_ITEM,
                            node=context.document.node_ref(comment.ordinal, NodeRole.XML_COMMENT),
                            name="comment",
                        ),
                    )
    elif operator_id == "xml.element-children":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                context.push(output, _content_match(context, child, item.node))
    elif operator_id == "xml.element-child-elements":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                if context.document._nodes[child].kind is XmlContentKind.ELEMENT:
                    context.push(output, context.element_match(child))
    elif operator_id == "xml.element-child-text":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                if context.document._nodes[child].kind is XmlContentKind.TEXT:
                    context.push(output, _content_match(context, child, item.node))
    elif operator_id == "xml.element-child-cdata":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                if context.document._nodes[child].kind is XmlContentKind.CDATA:
                    context.push(output, _content_match(context, child, item.node))
    elif operator_id == "xml.element-child-comments":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                if context.document._nodes[child].kind is XmlContentKind.COMMENT:
                    context.push(output, _content_match(context, child, item.node))
    elif operator_id == "xml.element-child-pi":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for child in data.children:
                if context.document._nodes[child].kind is XmlContentKind.PROCESSING_INSTRUCTION:
                    context.push(output, _content_match(context, child, item.node))
    elif operator_id == "xml.element-descendants":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            index = item.node.index
            stack = [index]
            while stack:
                current = stack.pop()
                data = context.document._element_data(current)
                for child in reversed(data.children):
                    if context.document._nodes[child].kind is XmlContentKind.ELEMENT:
                        stack.append(child)
                if current != index:
                    context.push(output, context.element_match(current))
    elif operator_id == "xml.element-attributes":
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            data = context.document._element_data(item.node.index)
            for attribute in data.attributes:
                context.push(output, context.attribute_match(attribute, item.node))
    elif operator_id in ("xml.element-namespace-bindings", "xml.element-in-scope-namespaces"):
        for item in input_matches:
            if item.kind is not XmlMatchKind.ELEMENT:
                continue
            index = item.node.index
            if operator_id == "xml.element-in-scope-namespaces":
                chain: list[int] = []
                current: int | None = index
                while current is not None:
                    chain.append(current)
                    current = context.document._parent_of_index(current)
                for at in reversed(chain):
                    element = context.document.node_ref(at, NodeRole.XML_ELEMENT)
                    for binding in context.document._element_data(at).namespaces:
                        context.push(output, context.namespace_binding_match(binding, element))
            else:
                data = context.document._element_data(index)
                for binding in data.namespaces:
                    context.push(output, context.namespace_binding_match(binding, item.node))
    elif operator_id in ("xml.content-parent", "xml.attribute-element", "xml.reference-text"):
        for item in input_matches:
            if item.kind in (XmlMatchKind.ATTRIBUTE, XmlMatchKind.NAMESPACE_BINDING):
                assert item.element is not None
                context.push(output, _element_from_node(context, item.element))
            elif item.kind in (
                XmlMatchKind.TEXT,
                XmlMatchKind.CDATA,
                XmlMatchKind.COMMENT,
                XmlMatchKind.PROCESSING_INSTRUCTION,
                XmlMatchKind.ELEMENT,
                XmlMatchKind.REFERENCE,
            ):
                if item.parent is not None:
                    context.push(output, _element_from_node(context, item.parent))
    elif operator_id == "xml.text-references":
        for item in input_matches:
            if item.kind is not XmlMatchKind.TEXT:
                continue
            content = context.document._nodes[item.node.index]
            if content.kind is not XmlContentKind.TEXT:
                continue
            data = content.data
            for ordinal, fragment in enumerate(data.fragments):
                if fragment.kind is ReferenceFragmentKind.LITERAL:
                    continue
                if fragment.kind is ReferenceFragmentKind.CHARACTER_REFERENCE:
                    reference_kind = XmlReferenceKind.CHARACTER
                    name = f"&#x{ord(fragment.resolved):X};" if fragment.resolved else ""
                    resolved = fragment.resolved or ""
                elif fragment.kind is ReferenceFragmentKind.PREDEFINED_ENTITY:
                    reference_kind = XmlReferenceKind.PREDEFINED
                    name = fragment.name or ""
                    resolved = fragment.resolved or ""
                else:
                    reference_kind = XmlReferenceKind.GENERAL
                    name = fragment.name or ""
                    resolved = fragment.resolved or ""
                context.push(
                    output,
                    XmlMatch(
                        kind=XmlMatchKind.REFERENCE,
                        node=context.document.node_ref(ordinal, NodeRole.XML_ENTITY_REFERENCE),
                        text=item.node,
                        parent=item.parent,
                        reference_kind=reference_kind,
                        name=name,
                        resolved=resolved,
                    ),
                )
    elif operator_id == "xml.name-equals":
        expected_prefix = operator.arguments["prefix"].as_string()
        expected_local = operator.arguments["local"].as_string()
        expected_namespace = operator.arguments["namespace"].as_string()
        comparison = operator.arguments["comparison"].as_string()
        for item in input_matches:
            if item.kind not in (XmlMatchKind.ELEMENT, XmlMatchKind.ATTRIBUTE):
                continue
            if comparison == "OriginalExact":
                matches = (item.prefix or "") == expected_prefix and item.local == expected_local
            elif comparison == "Expanded":
                if item.kind is XmlMatchKind.ELEMENT and item.namespace_error:
                    matches = False
                else:
                    matches = (item.namespace or "") == expected_namespace and item.local == expected_local
            else:
                matches = False
            if matches:
                context.push(output, item)
    elif operator_id == "xml.attribute-value-equals":
        expected = operator.arguments["value"].as_string()
        for item in input_matches:
            if item.kind is XmlMatchKind.ATTRIBUTE and item.value == expected:
                context.push(output, item)
    elif operator_id == "xml.pi-target-equals":
        expected = operator.arguments["target"].as_string()
        for item in input_matches:
            if item.kind is XmlMatchKind.PROCESSING_INSTRUCTION and item.target == expected:
                context.push(output, item)
    elif operator_id == "xml.reference-kind-is":
        expected = {
            "Character": XmlReferenceKind.CHARACTER,
            "Predefined": XmlReferenceKind.PREDEFINED,
            "General": XmlReferenceKind.GENERAL,
        }[operator.arguments["kind"].as_string()]
        for item in input_matches:
            if item.kind is XmlMatchKind.REFERENCE and item.reference_kind is expected:
                context.push(output, item)
    elif operator_id == "xml.reference-name-equals":
        expected = operator.arguments["name"].as_string()
        for item in input_matches:
            if item.kind is XmlMatchKind.REFERENCE and item.name == expected:
                context.push(output, item)
    elif operator_id == "xml.node-kind-is":
        expected = operator.arguments["kind"].as_string()
        for item in input_matches:
            if item.kind.value == expected:
                context.push(output, item)
    elif operator_id == "core.take":
        count = operator.arguments["count"].as_integer()
        output.extend(input_matches[:count])
    elif operator_id == "core.distinct-by-identity":
        seen: set[NodeRef] = set()
        for item in input_matches:
            if item.identity() not in seen:
                seen.add(item.identity())
                output.append(item)
    else:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR, operator=operator_id, version=operator.version
        )
    context.step(len(output))
    return output


def _content_match(context: _Context, index: int, parent: NodeRef) -> XmlMatch:
    """One child content occurrence match (query.rs:696-772)."""
    content = context.document._nodes[index]
    if content.kind is XmlContentKind.ELEMENT:
        return context.element_match(index)
    if content.kind is XmlContentKind.TEXT:
        return XmlMatch(
            kind=XmlMatchKind.TEXT,
            node=context.document.node_ref(content.data.ordinal, NodeRole.XML_TEXT),
            parent=parent,
            semantic=text_semantic(content.data),
        )
    if content.kind is XmlContentKind.CDATA:
        return XmlMatch(
            kind=XmlMatchKind.CDATA,
            node=context.document.node_ref(content.data.ordinal, NodeRole.XML_CDATA),
            parent=parent,
            text=content.data.text,
        )
    if content.kind is XmlContentKind.COMMENT:
        return XmlMatch(
            kind=XmlMatchKind.COMMENT,
            node=context.document.node_ref(content.data.ordinal, NodeRole.XML_COMMENT),
            parent=parent,
            text=content.data.text,
        )
    if content.kind is XmlContentKind.PROCESSING_INSTRUCTION:
        return XmlMatch(
            kind=XmlMatchKind.PROCESSING_INSTRUCTION,
            node=context.document.node_ref(
                content.data.ordinal, NodeRole.XML_PROCESSING_INSTRUCTION
            ),
            parent=parent,
            target=content.data.target,
            content=content.data.content[1] if content.data.content is not None else None,
        )
    return XmlMatch(
        kind=XmlMatchKind.ERROR_REGION,
        node=context.document.node_ref(content.data.ordinal, NodeRole.XML_ERROR_REGION),
        span=content.data.span,
    )


def _element_from_node(context: _Context, node: NodeRef) -> XmlMatch:
    """One step back to the owning element (query.rs:1262-1274)."""
    try:
        data = context.document._element_data(node.index)
        return context.element_match(data.index)
    except AssertionError:
        root = context.document.root()
        if root is not None:
            return context.element_match(root.index)
        return XmlMatch(
            kind=XmlMatchKind.DOCUMENT,
            node=context.document.node_ref(0, NodeRole.XML_DOCUMENT),
        )


def _apply_syntax_operator(
    operator, input_matches: list[XmlSyntaxMatch], context: _Context
) -> list[XmlSyntaxMatch]:
    output: list[XmlSyntaxMatch] = []
    operator_id = operator.id
    if operator_id == "xml.syntax-kind-is":
        expected = XmlSyntaxKind.from_name(operator.arguments["kind"].as_string())
        assert expected is not None, "kind was validated before binding"
        output.extend(match for match in input_matches if match.kind is expected)
    elif operator_id == "xml.syntax-text-equals":
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
        raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
    if selection is QuerySelection.REQUIRE_ONE:
        if len(matches) == 1:
            return matches
        raise QueryFailure(QueryFailureKind.CARDINALITY_VIOLATION)
    raise AssertionError("closed selection set")
