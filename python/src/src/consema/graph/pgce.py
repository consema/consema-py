"""PGCE/1 — Portable Graph Canonical Encoding / 1.

Wire format frozen by the Rust reference codec
(crates/consema-graph/src/pgce.rs), the byte arbitration source:

- stream magic is the ASCII octets ``PGCE`` (pgce.rs:12);
- wire version is minimal unsigned LEB128 ``1`` (pgce.rs:14);
- header: root count varint, node count varint, then the root references as
  canonical (first-discovery) id varints (pgce.rs:233-239);
- then one node record per canonical first-discovery position: a node-kind
  octet (0x20 scalar, 0x40 sequence, 0x41 mapping — pgce.rs:16-18), the tag
  as a length-prefixed UTF-8 blob, then per kind: scalar content blob;
  sequence item-count varint + item id varints; mapping entry-count varint +
  (key id, value id) varint pairs (pgce.rs:240-272);
- all varints are minimal unsigned LEB128 (pgce.rs:398-410).

Golden byte vectors are frozen by the Rust tests: a scalar graph with tag
`tag:yaml.org,2002:str` and content "x" encodes to
``504743450101010020157461673a79616d6c2e6f72672c323030323a7374720178``
(pgce.rs:664-678) and the empty graph to ``50474345010000`` (pgce.rs:681-686).
The decoder is strict: it validates canonical node numbering (ids assigned
in first-discovery order), rejects non-minimal varints, out-of-range
references, trailing bytes, and finally re-encodes the decoded graph and
requires byte equality (NonCanonicalEncoding; pgce.rs:494-506).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.graph.errors import (
    GraphBuildError,
    GraphBuildErrorKind,
    PgceDecodeError,
    PgceEncodeError,
    PgceErrorKind,
)
from consema.graph.graph import (
    GraphBuilder,
    GraphLimits,
    GraphMappingEntry,
    GraphNode,
    PortableGraph,
)

PGCE_MAGIC = b"PGCE"
PGCE_VERSION = 1

NODE_SCALAR = 0x20
NODE_SEQUENCE = 0x40
NODE_MAPPING = 0x41


@dataclass(frozen=True)
class PgceLimits:
    """Bounded PGCE encode/decode limits (pgce.rs:22-54)."""

    max_stream_bytes: int = 64 * 1024 * 1024
    max_roots: int = 1_000_000
    max_nodes: int = 1_000_000
    max_edges: int = 2_000_000
    max_container_entries: int = 1_000_000
    max_tag_bytes: int = 1024 * 1024
    max_scalar_bytes: int = 64 * 1024 * 1024
    max_traversal_depth: int = 256

    def graph_limits(self) -> GraphLimits:
        return GraphLimits(
            max_roots=self.max_roots,
            max_nodes=self.max_nodes,
            max_edges=self.max_edges,
            max_container_entries=self.max_container_entries,
            max_tag_bytes=self.max_tag_bytes,
            max_scalar_bytes=self.max_scalar_bytes,
            max_traversal_depth=self.max_traversal_depth,
        )


def _varint_size(value: int) -> int:
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


def _append_varint(output: bytearray, value: int) -> None:
    while True:
        octet = value & 0x7F
        value >>= 7
        if value != 0:
            octet |= 0x80
        output.append(octet)
        if value == 0:
            return


# --------------------------------------------------------------------------
# encoder
# --------------------------------------------------------------------------

def encode_pgce(graph: PortableGraph) -> bytes:
    """Encodes one graph with the default bounded policy (pgce.rs:219-221)."""
    return encode_pgce_bounded(graph, PgceLimits())


def encode_pgce_bounded(graph: PortableGraph, limits: PgceLimits) -> bytes:
    """Encodes one complete canonical PGCE/1 stream after exact measurement.

    Exceeding any limit raises :class:`PgceEncodeError`; no partial output
    is ever returned (pgce.rs:224-275).
    """
    _validate_graph_limits(graph, limits)
    order, canonical_ids = graph._canonical_layout()
    size = _measure(graph, canonical_ids, order, limits)
    if size > limits.max_stream_bytes:
        raise PgceEncodeError(name="stream-bytes", observed=size, limit=limits.max_stream_bytes)
    output = bytearray()
    output.extend(PGCE_MAGIC)
    _append_varint(output, PGCE_VERSION)
    _append_varint(output, len(graph.roots()))
    _append_varint(output, graph.node_count())
    for root in graph.roots():
        _append_varint(output, canonical_ids[root.index])
    for index in order:
        node = graph._nodes[index]
        if node.content[0] == "scalar":
            output.append(NODE_SCALAR)
            _write_blob(node.tag.encode("utf-8"), output)
            _write_blob(node.content[1].encode("utf-8"), output)
        elif node.content[0] == "sequence":
            output.append(NODE_SEQUENCE)
            _write_blob(node.tag.encode("utf-8"), output)
            _append_varint(output, len(node.content[1]))
            for item in node.content[1]:
                _append_varint(output, canonical_ids[item.index])
        else:
            output.append(NODE_MAPPING)
            _write_blob(node.tag.encode("utf-8"), output)
            _append_varint(output, len(node.content[1]))
            for entry in node.content[1]:
                _append_varint(output, canonical_ids[entry.key.index])
                _append_varint(output, canonical_ids[entry.value.index])
    return bytes(output)


def _write_blob(blob: bytes, output: bytearray) -> None:
    _append_varint(output, len(blob))
    output.extend(blob)


def _validate_graph_limits(graph: PortableGraph, limits: PgceLimits) -> None:
    _check_encode_limit("graph-roots", len(graph.roots()), limits.max_roots)
    _check_encode_limit("graph-nodes", graph.node_count(), limits.max_nodes)
    _check_encode_limit("graph-edges", graph.edge_count(), limits.max_edges)
    _canonical_order_for_encode(graph, limits)


def _canonical_order_for_encode(graph: PortableGraph, limits: PgceLimits) -> None:
    # Reuses the graph module traversal; resource-limit failures map to the
    # PGCE encode surface (pgce.rs map_build_to_encode, 376-390).
    from consema.graph.graph import _canonical_order

    try:
        _canonical_order(graph._nodes, graph.roots(), limits.max_traversal_depth)
    except GraphBuildError as error:
        if error.kind is GraphBuildErrorKind.RESOURCE_LIMIT:
            raise PgceEncodeError(
                name=error.name, observed=error.observed, limit=error.limit
            ) from None
        raise PgceEncodeError() from error


def _measure(graph: PortableGraph, canonical_ids: list[int], order: list[int], limits: PgceLimits) -> int:
    size = len(PGCE_MAGIC)
    size += _varint_size(PGCE_VERSION)
    size += _varint_size(len(graph.roots()))
    size += _varint_size(graph.node_count())
    for root in graph.roots():
        size += _varint_size(canonical_ids[root.index])
    for index in order:
        node = graph._nodes[index]
        _check_encode_limit("tag-bytes", len(node.tag.encode("utf-8")), limits.max_tag_bytes)
        size += 1
        size += _varint_size(len(node.tag.encode("utf-8"))) + len(node.tag.encode("utf-8"))
        if node.content[0] == "scalar":
            content = node.content[1].encode("utf-8")
            _check_encode_limit("scalar-bytes", len(content), limits.max_scalar_bytes)
            size += _varint_size(len(content)) + len(content)
        elif node.content[0] == "sequence":
            items = node.content[1]
            _check_encode_limit("container-entries", len(items), limits.max_container_entries)
            size += _varint_size(len(items))
            for item in items:
                size += _varint_size(canonical_ids[item.index])
        else:
            entries = node.content[1]
            _check_encode_limit("container-entries", len(entries), limits.max_container_entries)
            size += _varint_size(len(entries))
            for entry in entries:
                size += _varint_size(canonical_ids[entry.key.index])
                size += _varint_size(canonical_ids[entry.value.index])
    return size


def _check_encode_limit(name: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise PgceEncodeError(name=name, observed=observed, limit=limit)


# --------------------------------------------------------------------------
# decoder
# --------------------------------------------------------------------------

def decode_pgce(stream: bytes, limits: PgceLimits | None = None) -> PortableGraph:
    """Strictly decodes one canonical PGCE/1 stream (pgce.rs:422-507)."""
    limits = limits or PgceLimits()
    if len(stream) > limits.max_stream_bytes:
        raise PgceDecodeError(
            PgceErrorKind.RESOURCE_LIMIT,
            name="stream-bytes",
            observed=len(stream),
            limit=limits.max_stream_bytes,
        )
    decoder = _Decoder(stream, limits)
    if decoder.take(len(PGCE_MAGIC)) != PGCE_MAGIC:
        raise PgceDecodeError(PgceErrorKind.INVALID_MAGIC)
    version = decoder.varint()
    if version != PGCE_VERSION:
        raise PgceDecodeError(PgceErrorKind.UNSUPPORTED_VERSION, value=version)
    root_count = decoder.count("graph-roots", limits.max_roots)
    node_count = decoder.count("graph-nodes", limits.max_nodes)

    builder = GraphBuilder(limits.graph_limits())
    ids = [builder.reserve_node() for _ in range(node_count)]

    root_indices = [decoder.reference(node_count) for _ in range(root_count)]
    for index in root_indices:
        builder.push_root(ids[index])

    for index in range(node_count):
        kind = decoder.byte()
        tag = decoder.string("tag-bytes", limits.max_tag_bytes)
        if kind == NODE_SCALAR:
            content = decoder.string("scalar-bytes", limits.max_scalar_bytes)
            _define_scalar(builder, ids[index], tag, content)
        elif kind == NODE_SEQUENCE:
            count = decoder.count("container-entries", limits.max_container_entries)
            decoder.add_edges(count)
            items = [ids[decoder.reference(node_count)] for _ in range(count)]
            _define_sequence(builder, ids[index], tag, items)
        elif kind == NODE_MAPPING:
            count = decoder.count("container-entries", limits.max_container_entries)
            decoder.add_edges(count * 2)
            entries = [
                GraphMappingEntry(ids[decoder.reference(node_count)], ids[decoder.reference(node_count)])
                for _ in range(count)
            ]
            _define_mapping(builder, ids[index], tag, entries)
        else:
            raise PgceDecodeError(PgceErrorKind.UNKNOWN_NODE_KIND, value=kind)
    if decoder.offset != len(stream):
        raise PgceDecodeError(PgceErrorKind.TRAILING_BYTES)
    graph = _build_graph(builder)
    order, _canonical_ids = graph._canonical_layout()
    if order != list(range(node_count)):
        raise PgceDecodeError(PgceErrorKind.NON_CANONICAL_NODE_ORDER)
    encoded = encode_pgce_bounded(graph, limits)
    if encoded != stream:
        raise PgceDecodeError(PgceErrorKind.NON_CANONICAL_ENCODING)
    return graph


def _define_scalar(builder: GraphBuilder, node_id, tag: str, content: str) -> None:
    try:
        builder.define_scalar(node_id, tag, content)
    except GraphBuildError as error:
        raise _map_build_to_decode(error) from None


def _define_sequence(builder: GraphBuilder, node_id, tag: str, items) -> None:
    try:
        builder.define_sequence(node_id, tag, items)
    except GraphBuildError as error:
        raise _map_build_to_decode(error) from None


def _define_mapping(builder: GraphBuilder, node_id, tag: str, entries) -> None:
    try:
        builder.define_mapping(node_id, tag, entries)
    except GraphBuildError as error:
        raise _map_build_to_decode(error) from None


def _build_graph(builder: GraphBuilder) -> PortableGraph:
    try:
        return builder.build()
    except GraphBuildError as error:
        raise _map_build_to_decode(error) from None


def _map_build_to_decode(error: GraphBuildError) -> PgceDecodeError:
    # pgce.rs map_build_to_decode, 613-627.
    if error.kind is GraphBuildErrorKind.RESOURCE_LIMIT:
        return PgceDecodeError(
            PgceErrorKind.RESOURCE_LIMIT,
            name=error.name,
            observed=error.observed,
            limit=error.limit,
        )
    if error.kind is GraphBuildErrorKind.INVALID_TAG:
        return PgceDecodeError(PgceErrorKind.INVALID_TAG)
    return PgceDecodeError(PgceErrorKind.INVALID_GRAPH, cause=error)


class _Decoder:
    __slots__ = ("data", "offset", "limits", "edges")

    def __init__(self, data: bytes, limits: PgceLimits):
        self.data = data
        self.offset = 0
        self.limits = limits
        self.edges = 0

    def byte(self) -> int:
        if self.offset >= len(self.data):
            raise PgceDecodeError(PgceErrorKind.UNEXPECTED_EOF)
        value = self.data[self.offset]
        self.offset += 1
        return value

    def take(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self.data):
            raise PgceDecodeError(PgceErrorKind.UNEXPECTED_EOF)
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def varint(self) -> int:
        start = self.offset
        value = 0
        for shift in range(0, 64, 7):
            octet = self.byte()
            payload = octet & 0x7F
            if shift == 63 and payload > 1:
                raise PgceDecodeError(PgceErrorKind.VARINT_OVERFLOW)
            value |= payload << shift
            if octet & 0x80 == 0:
                if self.offset - start != _varint_size(value):
                    raise PgceDecodeError(PgceErrorKind.NON_MINIMAL_VARINT)
                return value
        raise PgceDecodeError(PgceErrorKind.VARINT_OVERFLOW)

    def count(self, name: str, limit: int) -> int:
        value = self.varint()
        if value > limit:
            raise PgceDecodeError(
                PgceErrorKind.RESOURCE_LIMIT, name=name, observed=value, limit=limit
            )
        return value

    def reference(self, node_count: int) -> int:
        value = self.varint()
        if value >= node_count:
            raise PgceDecodeError(PgceErrorKind.REFERENCE_OUT_OF_RANGE, value=value)
        return value

    def string(self, limit_name: str, limit: int) -> str:
        length = self.count(limit_name, limit)
        blob = self.take(length)
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            raise PgceDecodeError(PgceErrorKind.INVALID_UTF8) from None

    def add_edges(self, count: int) -> None:
        self.edges += count
        if self.edges > self.limits.max_edges:
            raise PgceDecodeError(
                PgceErrorKind.RESOURCE_LIMIT,
                name="graph-edges",
                observed=self.edges,
                limit=self.limits.max_edges,
            )
