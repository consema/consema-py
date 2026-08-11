"""Intent documents for PortableGraph and the PGCE/1 byte codec.

Golden byte vectors are frozen by the Rust tests
(crates/consema-graph/src/pgce.rs:664-686): a scalar graph with tag
`tag:yaml.org,2002:str` and content "x", and the empty graph.
"""

import pytest

from consema.graph import (
    GraphBuildError,
    GraphBuildErrorKind,
    GraphBuilder,
    GraphLimits,
    GraphMappingEntry,
    GraphNodeId,
    GraphNodeKind,
    PgceDecodeError,
    PgceEncodeError,
    PgceErrorKind,
    PgceLimits,
    PortableGraph,
    decode_pgce,
    encode_pgce,
    encode_pgce_bounded,
)

STR_TAG = "tag:yaml.org,2002:str"
SEQ_TAG = "tag:yaml.org,2002:seq"
MAP_TAG = "tag:yaml.org,2002:map"


def test_scalar_byte_vector_is_frozen():
    # pgce.rs:664-678.
    builder = GraphBuilder(GraphLimits())
    root = builder.reserve_node()
    builder.define_scalar(root, STR_TAG, "x").push_root(root)
    graph = builder.build()
    assert encode_pgce(graph).hex() == (
        "504743450101010020157461673a79616d6c2e6f72672c323030323a7374720178"
    )
    assert graph.node_count() == 1
    assert graph.edge_count() == 0
    node = graph.node(root)
    assert node is not None
    assert node.kind is GraphNodeKind.SCALAR
    assert node.tag == STR_TAG
    assert node.scalar_content() == "x"


def test_empty_graph_byte_vector_is_frozen():
    # pgce.rs:681-686.
    graph = GraphBuilder(GraphLimits()).build()
    assert encode_pgce(graph).hex() == "50474345010000"
    assert decode_pgce(encode_pgce(graph)) == graph


def test_sharing_cycles_and_duplicate_arbitrary_keys_are_values():
    # lib.rs:717-746.
    builder = GraphBuilder(GraphLimits())
    mapping = builder.reserve_node()
    key = builder.reserve_node()
    sequence = builder.reserve_node()
    builder.define_scalar(key, STR_TAG, "self")
    builder.define_sequence(sequence, SEQ_TAG, [mapping, key, key])
    builder.define_mapping(
        mapping,
        MAP_TAG,
        [GraphMappingEntry(key, sequence), GraphMappingEntry(key, mapping)],
    ).push_root(mapping)
    graph = builder.build()
    assert graph.node_count() == 3
    assert graph.edge_count() == 7
    stream = encode_pgce(graph)
    decoded = decode_pgce(stream)
    assert decoded == graph
    assert encode_pgce(decoded) == stream


def test_strict_equality_ignores_builder_ids_but_preserves_topology():
    # lib.rs:748-788.
    def build(shared_first: bool) -> PortableGraph:
        builder = GraphBuilder(GraphLimits())
        if shared_first:
            shared = builder.reserve_node()
            root = builder.reserve_node()
        else:
            root = builder.reserve_node()
            shared = builder.reserve_node()
        builder.define_scalar(shared, STR_TAG, "x")
        builder.define_sequence(root, SEQ_TAG, [shared, shared]).push_root(root)
        return builder.build()

    first = build(False)
    second = build(True)
    assert first == second
    assert hash(first) == hash(second)
    assert encode_pgce(first) == encode_pgce(second)

    duplicated = GraphBuilder(GraphLimits())
    duplicated_root = duplicated.reserve_node()
    left = duplicated.reserve_node()
    right = duplicated.reserve_node()
    duplicated.define_scalar(left, STR_TAG, "x")
    duplicated.define_scalar(right, STR_TAG, "x")
    duplicated.define_sequence(duplicated_root, SEQ_TAG, [left, right]).push_root(duplicated_root)
    assert first != duplicated.build()


def test_builder_rejects_incomplete_unreachable_duplicate_and_invalid_tag():
    # lib.rs:814-857.
    incomplete = GraphBuilder(GraphLimits())
    missing = incomplete.reserve_node()
    incomplete.push_root(missing)
    with pytest.raises(GraphBuildError) as caught:
        incomplete.build()
    assert caught.value.kind is GraphBuildErrorKind.UNDEFINED_NODE

    unreachable = GraphBuilder(GraphLimits())
    root = unreachable.reserve_node()
    hidden = unreachable.reserve_node()
    unreachable.define_scalar(root, STR_TAG, "root")
    unreachable.define_scalar(hidden, STR_TAG, "hidden")
    unreachable.push_root(root)
    with pytest.raises(GraphBuildError) as caught:
        unreachable.build()
    assert caught.value.kind is GraphBuildErrorKind.UNREACHABLE_NODE

    duplicate = GraphBuilder(GraphLimits())
    node = duplicate.reserve_node()
    duplicate.define_scalar(node, STR_TAG, "x")
    with pytest.raises(GraphBuildError) as caught:
        duplicate.define_scalar(node, STR_TAG, "y")
    assert caught.value.kind is GraphBuildErrorKind.DUPLICATE_DEFINITION

    invalid = GraphBuilder(GraphLimits())
    bad = invalid.reserve_node()
    with pytest.raises(GraphBuildError) as caught:
        invalid.define_scalar(bad, "bad tag", "x")
    assert caught.value.kind is GraphBuildErrorKind.INVALID_TAG

    first = GraphBuilder(GraphLimits())
    foreign = first.reserve_node()
    second = GraphBuilder(GraphLimits())
    with pytest.raises(GraphBuildError) as caught:
        second.push_root(foreign)
    assert caught.value.kind is GraphBuildErrorKind.WRONG_GRAPH


def test_graph_build_failures_have_stable_codes():
    # lib.rs:893-911.
    assert GraphBuildError(GraphBuildErrorKind.INVALID_TAG).code == "core.graph.invalid@1"
    assert (
        GraphBuildError(
            GraphBuildErrorKind.RESOURCE_LIMIT, name="graph-nodes", observed=2, limit=1
        ).code
        == "core.graph.resource-limit@1"
    )
    assert GraphBuildError(GraphBuildErrorKind.SIZE_OVERFLOW).code == "core.graph.resource-limit@1"


def test_decode_rejects_nonminimal_varint_trailing_and_invalid_reference():
    # pgce.rs:749-771.
    scalar = bytes.fromhex("504743450101010020157461673a79616d6c2e6f72672c323030323a7374720178")
    nonminimal = scalar[:4] + bytes([0x81, 0x00]) + scalar[5:]
    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(nonminimal)
    assert caught.value.kind is PgceErrorKind.NON_MINIMAL_VARINT
    assert caught.value.code == "core.pgce.non-canonical@1"

    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(scalar + b"\x00")
    assert caught.value.kind is PgceErrorKind.TRAILING_BYTES

    invalid_reference = bytearray(scalar)
    invalid_reference[7] = 1
    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(bytes(invalid_reference))
    assert caught.value.kind is PgceErrorKind.REFERENCE_OUT_OF_RANGE


def test_decode_rejects_noncanonical_node_numbering():
    # pgce.rs:774-792: a root referencing node 1 violates canonical discovery.
    stream = (
        b"PGCE"
        + bytes([1, 1, 2, 1, 0x20, 21])
        + STR_TAG.encode("ascii")
        + bytes([1, ord("x"), 0x40, 21])
        + SEQ_TAG.encode("ascii")
        + bytes([1, 0])
    )
    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(stream)
    assert caught.value.kind is PgceErrorKind.NON_CANONICAL_NODE_ORDER


def test_decode_rejects_unknown_node_kind_and_bad_magic():
    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(b"XXXX\x01\x00\x00")
    assert caught.value.kind is PgceErrorKind.INVALID_MAGIC
    assert caught.value.code == "core.pgce.invalid@1"

    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(b"PGCE\x02\x00\x00")
    assert caught.value.kind is PgceErrorKind.UNSUPPORTED_VERSION
    assert caught.value.code == "core.pgce.unsupported-version@1"


def test_encode_and_decode_limits_fail_atomically():
    # pgce.rs:795-817.
    scalar = bytes.fromhex("504743450101010020157461673a79616d6c2e6f72672c323030323a7374720178")
    limits = PgceLimits(max_stream_bytes=len(scalar) - 1)
    with pytest.raises(PgceDecodeError) as caught:
        decode_pgce(scalar, limits)
    assert caught.value.kind is PgceErrorKind.RESOURCE_LIMIT
    assert caught.value.name == "stream-bytes"

    graph = decode_pgce(scalar)
    with pytest.raises(PgceEncodeError):
        encode_pgce_bounded(graph, limits)


def test_node_ids_are_graph_local():
    builder = GraphBuilder(GraphLimits())
    first = builder.reserve_node()
    second = builder.reserve_node()
    assert isinstance(first, GraphNodeId)
    assert first.as_u64() == 0
    assert second.as_u64() == 1
    assert first != second
