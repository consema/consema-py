"""consema.graph — immutable PortableGraph and the PGCE/1 byte codec.

Exports GraphNodeId, GraphNode, GraphNodeKind, GraphMappingEntry,
GraphLimits, GraphBuilder, PortableGraph, PgceLimits, the PGCE/1
encode/decode functions, and the typed `core.graph.*@1` / `core.pgce.*@1`
errors.

Authority: RFC 0006; crates/consema-graph/src/lib.rs and pgce.rs (byte
arbitration); go/graph as a cross-reference only.
"""

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
    GraphNodeId,
    GraphNodeKind,
    PortableGraph,
)
from consema.graph.pgce import (
    PGCE_MAGIC,
    PGCE_VERSION,
    PgceLimits,
    decode_pgce,
    encode_pgce,
    encode_pgce_bounded,
)

__all__ = [
    "GraphBuildError",
    "GraphBuildErrorKind",
    "GraphBuilder",
    "GraphLimits",
    "GraphMappingEntry",
    "GraphNode",
    "GraphNodeId",
    "GraphNodeKind",
    "PGCE_MAGIC",
    "PGCE_VERSION",
    "PgceDecodeError",
    "PgceEncodeError",
    "PgceErrorKind",
    "PgceLimits",
    "PortableGraph",
    "decode_pgce",
    "encode_pgce",
    "encode_pgce_bounded",
]
