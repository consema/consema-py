"""Immutable PortableGraph values and the reservation/definition lifecycle.

Authority: RFC 0006; crates/consema-graph/src/lib.rs (the construction
invariants: reserved-then-defined nodes, reachability from the ordered
roots, first-visit traversal depth bound, canonical first-discovery order
for equality/encoding). Go (go/graph/graph.go, equal.go) is a cross-reference.

PortableGraph is independent from PortableValue: it preserves graph-local
identity, sharing, cycles, arbitrary mapping keys, duplicate associations,
and association order.

Design: ``GraphNodeId`` carries the index assigned by one ``GraphBuilder``
plus the builder's identity token, so IDs from other builders are rejected
(the WrongGraph / UnknownNode checks). ``GraphNode`` is immutable with the
three closed kinds Scalar / Sequence / Mapping. Strict graph equality and
hashing operate on the canonical first-discovery layout, ignoring builder
ID numbers.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass

from consema.graph.errors import GraphBuildError, GraphBuildErrorKind


class GraphNodeKind(enum.Enum):
    """The three stable node kinds of PortableGraph@1 (lib.rs:50-58)."""

    SCALAR = "Scalar"
    SEQUENCE = "Sequence"
    MAPPING = "Mapping"


class GraphNodeId:
    """A graph-local identity assigned by one ``GraphBuilder``.

    IDs are valid only for the completed graph built by that builder; their
    numeric values are not part of strict graph equality or canonical
    encoding (lib.rs:28-35). ``as_u64()`` returns the builder-local numeric
    representation.
    """

    __slots__ = ("_builder_token", "index")

    def __init__(self, builder_token: object, index: int):
        self._builder_token = builder_token
        self.index = index

    def as_u64(self) -> int:
        return self.index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphNodeId):
            return NotImplemented
        return self._builder_token is other._builder_token and self.index == other.index

    def __hash__(self) -> int:
        return hash((id(self._builder_token), self.index))

    def __repr__(self) -> str:
        return f"GraphNodeId({self.index})"


class GraphMappingEntry:
    """One ordered mapping association with arbitrary node key and value."""

    __slots__ = ("key", "value")

    def __init__(self, key: GraphNodeId, value: GraphNodeId):
        self.key = key
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphMappingEntry):
            return NotImplemented
        return self.key == other.key and self.value == other.value

    def __hash__(self) -> int:
        return hash((self.key, self.value))

    def __repr__(self) -> str:
        return f"GraphMappingEntry(key={self.key}, value={self.value})"


class GraphNode:
    """One immutable tagged graph node (lib.rs:94-157)."""

    __slots__ = ("tag", "content")

    def __init__(self, tag: str, content: tuple):
        self.tag = tag
        # content is one of:
        #   ("scalar", canonical_content)
        #   ("sequence", (item_ids...))
        #   ("mapping", (GraphMappingEntry...))
        self.content = content

    @property
    def kind(self) -> GraphNodeKind:
        return {
            "scalar": GraphNodeKind.SCALAR,
            "sequence": GraphNodeKind.SEQUENCE,
            "mapping": GraphNodeKind.MAPPING,
        }[self.content[0]]

    def scalar_content(self) -> str | None:
        return self.content[1] if self.content[0] == "scalar" else None

    def sequence_items(self) -> tuple[GraphNodeId, ...] | None:
        return self.content[1] if self.content[0] == "sequence" else None

    def mapping_entries(self) -> tuple[GraphMappingEntry, ...] | None:
        return self.content[1] if self.content[0] == "mapping" else None

    def _outgoing_reverse(self) -> list[GraphNodeId]:
        if self.content[0] == "sequence":
            return list(reversed(self.content[1]))
        if self.content[0] == "mapping":
            outgoing: list[GraphNodeId] = []
            for entry in reversed(self.content[1]):
                outgoing.append(entry.value)
                outgoing.append(entry.key)
            return outgoing
        return []

    def __repr__(self) -> str:
        return f"GraphNode(tag={self.tag!r}, kind={self.kind.value})"


@dataclass(frozen=True)
class GraphLimits:
    """Resource bounds for graph construction and traversal (lib.rs:160-190)."""

    max_roots: int = 1_000_000
    max_nodes: int = 1_000_000
    max_edges: int = 2_000_000
    max_container_entries: int = 1_000_000
    max_tag_bytes: int = 1024 * 1024
    max_scalar_bytes: int = 64 * 1024 * 1024
    max_traversal_depth: int = 256


class GraphBuilder:
    """Mutable reservation/definition lifecycle for one immutable graph.

    Usage: ``reserve_node()`` for each node, ``define_scalar`` /
    ``define_sequence`` / ``define_mapping`` exactly once per reserved node,
    ``push_root`` for each ordered root, then ``build()``.
    """

    def __init__(self, limits: GraphLimits | None = None):
        self._limits = limits or GraphLimits()
        # A per-instance identity token distinguishes builders without
        # global state (no hidden shared state; plan §1).
        self._token = object()
        self._nodes: list[GraphNode | None] = []
        self._roots: list[GraphNodeId] = []
        self._edge_count = 0

    def reserve_node(self) -> GraphNodeId:
        observed = len(self._nodes) + 1
        self._check_limit("graph-nodes", observed, self._limits.max_nodes)
        node_id = GraphNodeId(self._token, len(self._nodes))
        self._nodes.append(None)
        return node_id

    def push_root(self, root: GraphNodeId) -> "GraphBuilder":
        self._require_reserved(root)
        observed = len(self._roots) + 1
        self._check_limit("graph-roots", observed, self._limits.max_roots)
        self._roots.append(root)
        return self

    def define_scalar(
        self, node_id: GraphNodeId, tag: str, canonical_content: str
    ) -> "GraphBuilder":
        self._validate_tag(tag)
        self._check_limit(
            "scalar-bytes", len(canonical_content.encode("utf-8")), self._limits.max_scalar_bytes
        )
        return self._define(node_id, GraphNode(tag, ("scalar", canonical_content)), 0)

    def define_sequence(
        self, node_id: GraphNodeId, tag: str, items: list[GraphNodeId]
    ) -> "GraphBuilder":
        self._validate_tag(tag)
        self._check_limit(
            "container-entries", len(items), self._limits.max_container_entries
        )
        for item in items:
            self._require_reserved(item)
        return self._define(node_id, GraphNode(tag, ("sequence", tuple(items))), len(items))

    def define_mapping(
        self, node_id: GraphNodeId, tag: str, entries: list[GraphMappingEntry]
    ) -> "GraphBuilder":
        self._validate_tag(tag)
        self._check_limit(
            "container-entries", len(entries), self._limits.max_container_entries
        )
        for entry in entries:
            self._require_reserved(entry.key)
            self._require_reserved(entry.value)
        return self._define(
            node_id, GraphNode(tag, ("mapping", tuple(entries))), len(entries) * 2
        )

    def build(self) -> "PortableGraph":
        nodes: list[GraphNode] = []
        for index, node in enumerate(self._nodes):
            if node is None:
                raise GraphBuildError(
                    GraphBuildErrorKind.UNDEFINED_NODE,
                    node_id=GraphNodeId(self._token, index),
                )
            nodes.append(node)
        order = _canonical_order(nodes, self._roots, self._limits.max_traversal_depth)
        if len(order) != len(nodes):
            reachable = set(order)
            for index in range(len(nodes)):
                if index not in reachable:
                    raise GraphBuildError(
                        GraphBuildErrorKind.UNREACHABLE_NODE,
                        node_id=GraphNodeId(self._token, index),
                    )
            raise AssertionError("unreachable node search must find one")
        return PortableGraph(self._token, tuple(self._roots), tuple(nodes), self._edge_count)

    # -- internal ----------------------------------------------------------

    def _require_reserved(self, node_id: GraphNodeId) -> int:
        if node_id._builder_token is not self._token:
            raise GraphBuildError(GraphBuildErrorKind.WRONG_GRAPH, node_id=node_id)
        if not 0 <= node_id.index < len(self._nodes):
            raise GraphBuildError(GraphBuildErrorKind.UNKNOWN_NODE, node_id=node_id)
        return node_id.index

    def _define(self, node_id: GraphNodeId, node: GraphNode, new_edges: int) -> "GraphBuilder":
        index = self._require_reserved(node_id)
        if self._nodes[index] is not None:
            raise GraphBuildError(
                GraphBuildErrorKind.DUPLICATE_DEFINITION, node_id=node_id
            )
        edge_count = self._edge_count + new_edges
        self._check_limit("graph-edges", edge_count, self._limits.max_edges)
        self._nodes[index] = node
        self._edge_count = edge_count
        return self

    def _validate_tag(self, tag: str) -> None:
        # lib.rs:447-456: empty tags, ASCII control characters, and ASCII
        # whitespace are invalid. (ASCII whitespace is ' ' plus the control
        # range 0x09-0x0D, so "code <= 0x20 or code == 0x7f" is the whole
        # set; non-ASCII whitespace is permitted.)
        if not tag:
            raise GraphBuildError(GraphBuildErrorKind.INVALID_TAG)
        for character in tag:
            code = ord(character)
            if code <= 0x20 or code == 0x7F:
                raise GraphBuildError(GraphBuildErrorKind.INVALID_TAG)
        self._check_limit("tag-bytes", len(tag.encode("utf-8")), self._limits.max_tag_bytes)

    def _check_limit(self, name: str, observed: int, limit: int) -> None:
        if observed > limit:
            raise GraphBuildError(
                GraphBuildErrorKind.RESOURCE_LIMIT,
                name=name,
                observed=observed,
                limit=limit,
            )


class PortableGraph:
    """An immutable rooted, directed, ordered, tagged graph value."""

    def __init__(self, token: object, roots: tuple, nodes: tuple, edge_count: int):
        self._token = token
        self._roots = roots
        self._nodes = nodes
        self._edge_count = edge_count

    def roots(self) -> tuple[GraphNodeId, ...]:
        """Ordered roots; an empty tuple represents an empty root stream."""
        return self._roots

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return self._edge_count

    def node(self, node_id: GraphNodeId) -> GraphNode | None:
        if node_id._builder_token is not self._token:
            return None
        if not 0 <= node_id.index < len(self._nodes):
            return None
        return self._nodes[node_id.index]

    def nodes(self) -> list[tuple[GraphNodeId, GraphNode]]:
        """Builder-local IDs and nodes; numeric ID order is not value semantics."""
        return [
            (GraphNodeId(self._token, index), node)
            for index, node in enumerate(self._nodes)
        ]

    def _canonical_layout(self) -> tuple[list[int], list[int]]:
        """Canonical first-discovery order and the id→canonical-id map."""
        order = _canonical_order(self._nodes, self._roots, None)
        canonical_ids = [0] * len(self._nodes)
        for canonical, original in enumerate(order):
            canonical_ids[original] = canonical
        return order, canonical_ids

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PortableGraph):
            return NotImplemented
        if (
            len(self._roots) != len(other._roots)
            or len(self._nodes) != len(other._nodes)
            or self._edge_count != other._edge_count
        ):
            return False
        left_order, left_ids = self._canonical_layout()
        right_order, right_ids = other._canonical_layout()
        for left_root, right_root in zip(self._roots, other._roots):
            if left_ids[left_root.index] != right_ids[right_root.index]:
                return False
        for left_index, right_index in zip(left_order, right_order):
            if not _canonical_node_eq(
                self._nodes[left_index], left_ids,
                other._nodes[right_index], right_ids,
            ):
                return False
        return True

    def __hash__(self) -> int:
        # Deterministic and consistent with __eq__: a tuple of the canonical
        # facts (root count, canonical root ids, node count, then per-node
        # tag/kind/content in canonical order).
        order, canonical_ids = self._canonical_layout()
        facts: list[object] = [len(self._roots)]
        for root in self._roots:
            facts.append(canonical_ids[root.index])
        facts.append(len(self._nodes))
        for index in order:
            facts.append(_canonical_node_hash_facts(self._nodes[index], canonical_ids))
        return hash(tuple(facts))

    def __repr__(self) -> str:
        return (
            f"PortableGraph(roots={len(self._roots)}, nodes={len(self._nodes)}, "
            f"edges={self._edge_count})"
        )


# --------------------------------------------------------------------------
# canonical first-discovery traversal (lib.rs:542-578)
# --------------------------------------------------------------------------

def _canonical_order(
    nodes: list[GraphNode], roots: tuple, max_depth: int | None
) -> list[int]:
    order: list[int] = []
    visited = [False] * len(nodes)
    stack: list[tuple[int, int]] = []
    for root in reversed(roots):
        stack.append((root.index, 0))
    while stack:
        index, depth = stack.pop()
        if visited[index]:
            continue
        if max_depth is not None and depth > max_depth:
            raise GraphBuildError(
                GraphBuildErrorKind.RESOURCE_LIMIT,
                name="traversal-depth",
                observed=depth,
                limit=max_depth,
            )
        visited[index] = True
        order.append(index)
        for child_id in nodes[index]._outgoing_reverse():
            stack.append((child_id.index, depth + 1))
    return order


def _canonical_id(canonical_ids: list[int], node_id: GraphNodeId) -> int:
    return canonical_ids[node_id.index]


def _canonical_node_eq(
    left: GraphNode, left_ids: list[int], right: GraphNode, right_ids: list[int]
) -> bool:
    if left.tag != right.tag:
        return False
    if left.content[0] != right.content[0]:
        return False
    if left.content[0] == "scalar":
        return left.content[1] == right.content[1]
    if left.content[0] == "sequence":
        left_items, right_items = left.content[1], right.content[1]
        if len(left_items) != len(right_items):
            return False
        return all(
            _canonical_id(left_ids, item_left) == _canonical_id(right_ids, item_right)
            for item_left, item_right in zip(left_items, right_items)
        )
    left_entries, right_entries = left.content[1], right.content[1]
    if len(left_entries) != len(right_entries):
        return False
    for entry_left, entry_right in zip(left_entries, right_entries):
        if _canonical_id(left_ids, entry_left.key) != _canonical_id(
            right_ids, entry_right.key
        ) or _canonical_id(left_ids, entry_left.value) != _canonical_id(
            right_ids, entry_right.value
        ):
            return False
    return True


def _canonical_node_hash_facts(node: GraphNode, canonical_ids: list[int]) -> tuple:
    if node.content[0] == "scalar":
        return (node.tag, node.kind.value, node.content[1])
    if node.content[0] == "sequence":
        return (
            node.tag,
            node.kind.value,
            len(node.content[1]),
            tuple(_canonical_id(canonical_ids, item) for item in node.content[1]),
        )
    entries = node.content[1]
    return (
        node.tag,
        node.kind.value,
        len(entries),
        tuple(
            itertools.chain.from_iterable(
                (_canonical_id(canonical_ids, entry.key), _canonical_id(canonical_ids, entry.value))
                for entry in entries
            )
        ),
    )
