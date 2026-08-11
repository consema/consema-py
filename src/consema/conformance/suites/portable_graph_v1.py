"""Suite ``consema.portable-graph.conformance@1`` (portable-graph-v1.json,
10 cases): PGCE/1 golden vectors, strict graph equality and hashing, PGCE
round-trip stability, strict decode rejections, portable-graph query
execution, and the bounded-encode stream limit. Dispatch is by case id,
mirroring go/conformance/portable_graph_v1.go.

The portable-graph query executor (``core.portable-graph-query@1``) is a
runner-side capability implementation mirroring the Rust executor
(crates/consema-graph/src/query.rs): the Input expression yields one Node
match per root in root order, ``graph.reachable-nodes`` performs the
canonical first-discovery traversal with one shared visited set,
``core.distinct-by-identity`` deduplicates by match identity, and the
association operators preserve order and shared identity.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import core_query
from consema.conformance import runner
from consema.core.value import Kind
from consema.graph import graph as graph_module
from consema.graph import pgce as graph_pgce
from consema.graph.errors import PgceDecodeError, PgceEncodeError
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet

_GRAPH_DOMAIN = protocol_query.QueryDomain("core.portable-graph-query", 1)


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "pgce.empty-vector": _pgce_vector,
        "pgce.scalar-vector": _pgce_vector,
        "graph.isomorphic-builder-numbering": _graph_equality,
        "graph.sharing-is-not-duplication": _graph_equality,
        "pgce.cycle-roundtrip": _pgce_roundtrip,
        "pgce.reject-nonminimal-varint": _pgce_rejection,
        "pgce.reject-noncanonical-node-order": _pgce_rejection,
        "query.reachable-canonical-order": _graph_query,
        "query.distinct-shared-identity": _graph_query,
        "resource.pgce-stream-limit": _pgce_stream_limit,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(
                    id=vector.id, message="runner does not recognize published graph case"
                )
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# graph building (portable_graph_v1.go:51-169)
# ---------------------------------------------------------------------------


def _graph_from_vector(value) -> graph_module.PortableGraph:
    """Builds one graph from the vector descriptor
    {nodes: [{kind, tag, content|items|entries}], roots: [...]}, mirroring
    graphFromVector including the builder numbering (reserve in descriptor
    order)."""
    nodes = compare.sequence_field(value, "nodes")
    roots = compare.sequence_field(value, "roots")
    if nodes is None:
        raise ValueError("graph.nodes missing")
    if roots is None:
        raise ValueError("graph.roots missing")
    builder = graph_module.GraphBuilder()
    ids = [builder.reserve_node() for _ in nodes]
    for index, node_value in enumerate(nodes):
        kind = compare.string_field(node_value, "kind")
        tag = compare.string_field(node_value, "tag")
        if kind is None:
            raise ValueError(f"graph node {index} kind missing")
        if tag is None:
            raise ValueError(f"graph node {index} tag missing")
        if kind == "Scalar":
            content = compare.string_field(node_value, "content")
            if content is None:
                raise ValueError(f"scalar {index} content missing")
            builder.define_scalar(ids[index], tag, content)
        elif kind == "Sequence":
            items = compare.sequence_field(node_value, "items")
            if items is None:
                raise ValueError(f"sequence {index} items missing")
            builder.define_sequence(
                ids[index], tag, [_reference(item, ids) for item in items]
            )
        elif kind == "Mapping":
            entries = compare.sequence_field(node_value, "entries")
            if entries is None:
                raise ValueError(f"mapping {index} entries missing")
            mapping_entries = []
            for entry_value in entries:
                key = _object_reference(entry_value, "key", ids)
                value = _object_reference(entry_value, "value", ids)
                mapping_entries.append(graph_module.GraphMappingEntry(key, value))
            builder.define_mapping(ids[index], tag, mapping_entries)
        else:
            raise ValueError(f"unknown graph node kind {kind!r}")
    for root in roots:
        builder.push_root(_reference(root, ids))
    return builder.build()


def _reference(item, ids) -> graph_module.GraphNodeId:
    """One integer graph descriptor resolved into its reserved node id."""
    if item.kind is not Kind.INTEGER:
        raise ValueError("graph reference must be Integer")
    number = item.as_integer()
    if number < 0 or number >= len(ids):
        raise ValueError("graph reference out of range")
    return ids[number]


def _object_reference(value, name: str, ids) -> graph_module.GraphNodeId:
    field = compare.object_field(value, name)
    if field is None:
        raise ValueError(f"graph mapping entry {name} missing")
    return _reference(field, ids)


def _build_graph(vector: runner.Case, field_name: str):
    """Builds the named input graph; returns (graph, error_message)."""
    value = compare.object_field(vector.input, field_name)
    if value is None:
        return None, f"missing input.{field_name}"
    try:
        return _graph_from_vector(value), None
    except Exception as error:  # malformed descriptor
        return None, str(error)


# ---------------------------------------------------------------------------
# PGCE golden vectors
# ---------------------------------------------------------------------------


def _pgce_vector(vector: runner.Case) -> str | None:
    built, message = _build_graph(vector, "graph")
    if message:
        return message
    encoded = graph_pgce.encode_pgce(built)
    expected_hex = compare.string_field(vector.expected, "hex")
    if expected_hex is None:
        return "missing expected.hex"
    if encoded.hex() != expected_hex:
        return f"pgce hex {encoded.hex()} != {expected_hex}"
    return None


def _graph_equality(vector: runner.Case) -> str | None:
    left, message = _build_graph(vector, "left")
    if message:
        return message
    right, message = _build_graph(vector, "right")
    if message:
        return message
    strict_equal = compare.boolean_field(vector.expected, "strict_equal")
    if strict_equal is None:
        return "missing expected.strict_equal"
    if (left == right) != strict_equal:
        return "strict equality differs"
    left_bytes = graph_pgce.encode_pgce(left)
    right_bytes = graph_pgce.encode_pgce(right)
    pgce_equal = compare.boolean_field(vector.expected, "pgce_equal")
    if pgce_equal is None:
        return "missing expected.pgce_equal"
    if (left_bytes == right_bytes) != pgce_equal:
        return "pgce equality differs"
    hash_equal = compare.boolean_field(vector.expected, "strict_hash_equal")
    if hash_equal is not None and (hash(left) == hash(right)) != hash_equal:
        return "hash equality differs"
    return None


def _pgce_roundtrip(vector: runner.Case) -> str | None:
    built, message = _build_graph(vector, "graph")
    if message:
        return message
    if compare.boolean_field(vector.expected, "strict_equal") is not True:
        return "expected.strict_equal must be true"
    if compare.boolean_field(vector.expected, "byte_stable") is not True:
        return "expected.byte_stable must be true"
    encoded = graph_pgce.encode_pgce(built)
    decoded = graph_pgce.decode_pgce(encoded)
    if decoded != built:
        return "decoded graph is not strictly equal"
    reencoded = graph_pgce.encode_pgce(decoded)
    if reencoded != encoded:
        return "re-encode is not byte-stable"
    return None


def _pgce_kind_name(kind) -> str:
    """The CamelCase vector spelling of one PGCE failure kind (the Python
    kind values are kebab-case; the vectors freeze the Go enum names)."""
    return "".join(part.capitalize() for part in kind.value.split("-"))


def _pgce_rejection(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "hex")
    if text is None:
        return "missing input.hex"
    try:
        graph_pgce.decode_pgce(bytes.fromhex(text))
    except PgceDecodeError as error:
        expected_failure = compare.string_field(vector.expected, "failure")
        if expected_failure is None:
            return "missing expected.failure"
        actual = _pgce_kind_name(error.kind)
        if actual != expected_failure:
            return f"failure: expected {expected_failure}, got {actual}"
        return None
    return "decode must fail"


def _pgce_stream_limit(vector: runner.Case) -> str | None:
    built, message = _build_graph(vector, "graph")
    if message:
        return message
    max_stream_bytes = compare.integer_field(vector.input, "max_stream_bytes")
    if max_stream_bytes is None:
        return "missing input.max_stream_bytes"
    try:
        graph_pgce.encode_pgce_bounded(
            built, graph_pgce.PgceLimits(max_stream_bytes=max_stream_bytes)
        )
    except PgceEncodeError as error:
        if compare.string_field(vector.expected, "failure") != "ResourceLimit":
            return "unexpected expectation facts"
        expected_limit = compare.string_field(vector.expected, "limit")
        if error.name != expected_limit:
            return f"limit {error.name} != {expected_limit}"
        return None
    return "encode must fail"


# ---------------------------------------------------------------------------
# portable-graph query execution (crates/consema-graph/src/query.rs)
# ---------------------------------------------------------------------------


class GraphMatch:
    """One portable-graph query match (query.rs:13-39)."""

    __slots__ = ("kind", "node_id", "parent", "ordinal", "key", "value")

    def __init__(
        self,
        kind: str,
        node_id=None,
        parent=None,
        ordinal: int | None = None,
        key=None,
        value=None,
    ):
        self.kind = kind  # "Node" | "SequenceElement" | "MappingEntry"
        self.node_id = node_id
        self.parent = parent
        self.ordinal = ordinal
        self.key = key
        self.value = value

    def identity(self):
        """Match identity for distinct-by-identity (query.rs:65-83)."""
        if self.kind == "Node":
            return ("node", self.node_id)
        if self.kind == "SequenceElement":
            return ("element", self.parent, self.ordinal)
        return ("entry", self.parent, self.ordinal)


def _outgoing_reverse(graph: graph_module.PortableGraph, node_id) -> list:
    """Children in reverse order so the DFS pop visits them forward
    (query.rs outgoing_reverse; graph.rs:_outgoing_reverse)."""
    node = graph.node(node_id)
    items = node.sequence_items()
    if items is not None:
        return list(reversed(items))
    entries = node.mapping_entries()
    if entries is not None:
        outgoing = []
        for entry in reversed(entries):
            outgoing.append(entry.value)
            outgoing.append(entry.key)
        return outgoing
    return []


def _apply_graph_selection(selection: protocol_query.QuerySelection, matches):
    if selection is protocol_query.QuerySelection.ALL:
        return matches
    if selection is protocol_query.QuerySelection.FIRST:
        return matches[:1]
    if selection is protocol_query.QuerySelection.LAST:
        return matches[-1:]
    if selection is protocol_query.QuerySelection.ZERO_OR_ONE:
        if len(matches) <= 1:
            return matches
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.CARDINALITY_VIOLATION
        )
    if selection is protocol_query.QuerySelection.REQUIRE_ONE:
        if len(matches) == 1:
            return matches
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.CARDINALITY_VIOLATION
        )
    return matches


def _string_argument(operator: protocol_query.OperatorCall, name: str) -> str:
    value = operator.arguments.get(name)
    if value is None or value.kind is not Kind.STRING:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.WRONG_ARGUMENT_TYPE,
            operator=operator.id,
            argument=name,
        )
    return value.as_string()


def _integer_argument(operator: protocol_query.OperatorCall, name: str) -> int:
    value = operator.arguments.get(name)
    if value is None or value.kind is not Kind.INTEGER:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.WRONG_ARGUMENT_TYPE,
            operator=operator.id,
            argument=name,
        )
    return value.as_integer()


def _require_node(match: GraphMatch, operator_id: str) -> None:
    if match.kind != "Node":
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
            operator=operator_id,
            expected_role=protocol_query.MatchRole.GRAPH_NODE,
            actual_role=_match_role(match),
        )


def _match_role(match: GraphMatch) -> protocol_query.MatchRole:
    if match.kind == "Node":
        return protocol_query.MatchRole.GRAPH_NODE
    if match.kind == "SequenceElement":
        return protocol_query.MatchRole.GRAPH_SEQUENCE_ELEMENT
    return protocol_query.MatchRole.GRAPH_MAPPING_ENTRY


def _evaluate_graph(expression, inputs, graph: graph_module.PortableGraph):
    if expression.kind is protocol_query.ExpressionKind.INPUT:
        return list(inputs)
    if expression.kind is protocol_query.ExpressionKind.APPLY:
        inner = _evaluate_graph(expression.input, inputs, graph)
        return _apply_graph_operator(expression.operator, inner, graph)
    if expression.kind is protocol_query.ExpressionKind.CONCAT:
        output = []
        for branch in expression.branches:
            output.extend(_evaluate_graph(branch, inputs, graph))
        return output
    raise protocol_query.QueryFailure(
        protocol_query.QueryFailureKind.INVALID_ARGUMENT,
        operator="expression",
        argument="kind",
    )


def _apply_graph_operator(operator, inputs, graph: graph_module.PortableGraph):
    output: list[GraphMatch] = []
    if operator.id == "core.take":
        output.extend(inputs[: _integer_argument(operator, "count")])
    elif operator.id == "core.distinct-by-identity":
        seen = set()
        for match in inputs:
            if match.identity() not in seen:
                seen.add(match.identity())
                output.append(match)
    elif operator.id == "graph.reachable-nodes":
        seen = set()
        for match in inputs:
            _require_node(match, operator.id)
            stack = [match.node_id]
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                output.append(GraphMatch("Node", node_id=node))
                stack.extend(_outgoing_reverse(graph, node))
    elif operator.id == "graph.where-kind":
        expected = _string_argument(operator, "kind")
        for match in inputs:
            _require_node(match, operator.id)
            if graph.node(match.node_id).kind.value == expected:
                output.append(match)
    elif operator.id == "graph.where-tag":
        expected = _string_argument(operator, "tag")
        for match in inputs:
            _require_node(match, operator.id)
            if graph.node(match.node_id).tag == expected:
                output.append(match)
    elif operator.id == "graph.try-sequence-elements":
        for match in inputs:
            _require_node(match, operator.id)
            items = graph.node(match.node_id).sequence_items()
            if items is not None:
                for ordinal, item in enumerate(items):
                    output.append(
                        GraphMatch(
                            "SequenceElement",
                            parent=match.node_id,
                            ordinal=ordinal,
                            node_id=item,
                        )
                    )
    elif operator.id == "graph.sequence-element-node":
        for match in inputs:
            if match.kind != "SequenceElement":
                raise protocol_query.QueryFailure(
                    protocol_query.QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                    operator=operator.id,
                    expected_role=protocol_query.MatchRole.GRAPH_SEQUENCE_ELEMENT,
                    actual_role=_match_role(match),
                )
            output.append(GraphMatch("Node", node_id=match.node_id))
    elif operator.id == "graph.try-mapping-entries":
        for match in inputs:
            _require_node(match, operator.id)
            entries = graph.node(match.node_id).mapping_entries()
            if entries is not None:
                for ordinal, entry in enumerate(entries):
                    output.append(
                        GraphMatch(
                            "MappingEntry",
                            parent=match.node_id,
                            ordinal=ordinal,
                            key=entry.key,
                            value=entry.value,
                        )
                    )
    elif operator.id in ("graph.mapping-entry-key", "graph.mapping-entry-value"):
        for match in inputs:
            if match.kind != "MappingEntry":
                raise protocol_query.QueryFailure(
                    protocol_query.QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                    operator=operator.id,
                    expected_role=protocol_query.MatchRole.GRAPH_MAPPING_ENTRY,
                    actual_role=_match_role(match),
                )
            output.append(
                GraphMatch(
                    "Node",
                    node_id=match.key if operator.id == "graph.mapping-entry-key" else match.value,
                )
            )
    else:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator.id,
            version=operator.version,
        )
    return output


def _execute_graph_query(
    graph: graph_module.PortableGraph, executable: protocol_query.ExecutableQuery
) -> list[GraphMatch]:
    """Executes a validated graph-domain query (query.rs:142-174)."""
    definition = executable.definition
    if definition.domain != _GRAPH_DOMAIN:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.DOMAIN_MISMATCH, domain=definition.domain
        )
    roots = [GraphMatch("Node", node_id=root) for root in graph.roots()]
    matches = _evaluate_graph(definition.expression, roots, graph)
    return _apply_graph_selection(definition.selection, matches)


def _graph_query(vector: runner.Case) -> str | None:
    built, message = _build_graph(vector, "graph")
    if message:
        return message
    pipeline = compare.string_sequence(vector.input, "pipeline")
    if pipeline is None:
        return "missing input.pipeline"
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for spelling in pipeline:
        operator_id, version_text = spelling.rsplit("@", 1)
        expression = expression.then(protocol_query.OperatorCall(operator_id, int(version_text)))
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    definition = (
        protocol_query.QueryDefinition(_GRAPH_DOMAIN)
        .with_expression(expression)
        .validate()
        .bind(capabilities)
    )
    try:
        matches = _execute_graph_query(built, definition)
    except protocol_query.QueryFailure as failure:
        return f"query execution failed: {core_query.query_failure_name(failure)}"
    expected_ids = compare.integer_sequence(vector.expected, "builder_node_ids")
    if expected_ids is None:
        return "missing expected.builder_node_ids"
    expected_count = compare.integer_field(vector.expected, "count")
    if expected_count is not None and len(matches) != expected_count:
        return f"match count {len(matches)} != {expected_count}"
    if len(matches) != len(expected_ids):
        return f"match count {len(matches)} != {len(expected_ids)}"
    actual = []
    for match in matches:
        if match.kind != "Node":
            return "query result was not a node"
        actual.append(match.node_id.as_u64())
    if actual != expected_ids:
        return f"builder_node_ids {actual!r} != {expected_ids!r}"
    return None


runner.register_suite("portable-graph-v1.json", "consema.portable-graph.conformance@1", "", 10, run)
