"""Typed PortableGraph construction and PGCE/1 codec failures.

Stable diagnostic codes (semantic-model v5 registry arbitration,
crates/consema-protocol/src/error_registry.rs:692-705 and the StableFailure
mappings in crates/consema-graph/src/lib.rs:230-242 and pgce.rs:96-216):

- construction: `core.graph.resource-limit@1` (ResourceLimit, SizeOverflow)
  and `core.graph.invalid@1` (all structural failures);
- PGCE encode: `core.pgce.resource-limit@1`;
- PGCE decode: `core.pgce.resource-limit@1`, `core.pgce.unsupported-version@1`,
  `core.pgce.non-canonical@1` (NonMinimalVarint, NonCanonicalNodeOrder,
  NonCanonicalEncoding), and `core.pgce.invalid@1` (everything else).

Go (go/graph/errors.go) is a cross-reference only.
"""

from __future__ import annotations

import enum


class GraphBuildErrorKind(enum.Enum):
    RESOURCE_LIMIT = "resource-limit"
    SIZE_OVERFLOW = "size-overflow"
    UNKNOWN_NODE = "unknown-node"
    WRONG_GRAPH = "wrong-graph"
    DUPLICATE_DEFINITION = "duplicate-definition"
    UNDEFINED_NODE = "undefined-node"
    UNREACHABLE_NODE = "unreachable-node"
    INVALID_TAG = "invalid-tag"


class GraphBuildError(Exception):
    """A stable graph construction failure (crates/consema-graph/src/lib.rs:194-218).

    ``name``/``observed``/``limit`` carry the resource-limit facts;
    ``node_id`` carries the offending graph-local ID when relevant.
    """

    def __init__(
        self,
        kind: GraphBuildErrorKind,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        node_id: "object | None" = None,
    ):
        super().__init__(kind.value, name, observed, limit, node_id)
        self.kind = kind
        self.name = name
        self.observed = observed
        self.limit = limit
        self.node_id = node_id

    @property
    def code(self) -> str:
        """The frozen registered code (lib.rs:230-242)."""
        if self.kind in (GraphBuildErrorKind.RESOURCE_LIMIT, GraphBuildErrorKind.SIZE_OVERFLOW):
            return "core.graph.resource-limit@1"
        return "core.graph.invalid@1"

    def __str__(self) -> str:
        if self.kind is GraphBuildErrorKind.RESOURCE_LIMIT:
            return f"graph resource limit: {self.name} observed={self.observed} limit={self.limit}"
        return f"graph build failure: {self.kind.value}"


class PgceErrorKind(enum.Enum):
    RESOURCE_LIMIT = "resource-limit"
    INVALID_MAGIC = "invalid-magic"
    UNSUPPORTED_VERSION = "unsupported-version"
    UNEXPECTED_EOF = "unexpected-eof"
    NON_MINIMAL_VARINT = "non-minimal-varint"
    VARINT_OVERFLOW = "varint-overflow"
    UNKNOWN_NODE_KIND = "unknown-node-kind"
    INVALID_UTF8 = "invalid-utf8"
    INVALID_TAG = "invalid-tag"
    REFERENCE_OUT_OF_RANGE = "reference-out-of-range"
    NON_CANONICAL_NODE_ORDER = "non-canonical-node-order"
    TRAILING_BYTES = "trailing-bytes"
    INVALID_GRAPH = "invalid-graph"
    NON_CANONICAL_ENCODING = "non-canonical-encoding"


class PgceDecodeError(Exception):
    """A strict PGCE/1 decoding failure (pgce.rs:116-152).

    ``name``/``observed``/``limit`` carry the resource-limit facts;
    ``value`` carries the offending version, node-kind octet, or reference.
    """

    def __init__(
        self,
        kind: PgceErrorKind,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        value: int | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(kind.value, name, observed, limit, value, cause)
        self.kind = kind
        self.name = name
        self.observed = observed
        self.limit = limit
        self.value = value
        self.cause = cause

    @property
    def code(self) -> str:
        """The frozen registered code (pgce.rs:164-184)."""
        if self.kind is PgceErrorKind.UNSUPPORTED_VERSION:
            return "core.pgce.unsupported-version@1"
        if self.kind in (
            PgceErrorKind.NON_MINIMAL_VARINT,
            PgceErrorKind.NON_CANONICAL_NODE_ORDER,
            PgceErrorKind.NON_CANONICAL_ENCODING,
        ):
            return "core.pgce.non-canonical@1"
        if self.kind is PgceErrorKind.RESOURCE_LIMIT or (
            self.kind is PgceErrorKind.INVALID_GRAPH
            and isinstance(self.cause, GraphBuildError)
            and self.cause.kind
            in (GraphBuildErrorKind.RESOURCE_LIMIT, GraphBuildErrorKind.SIZE_OVERFLOW)
        ):
            return "core.pgce.resource-limit@1"
        return "core.pgce.invalid@1"

    def __str__(self) -> str:
        if self.kind is PgceErrorKind.RESOURCE_LIMIT:
            return f"PGCE resource limit: {self.name} observed={self.observed} limit={self.limit}"
        return f"PGCE decode failure: {self.kind.value}"


class PgceEncodeError(Exception):
    """A bounded PGCE/1 encoding failure (pgce.rs:72-84). No partial output."""

    def __init__(
        self,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
    ):
        super().__init__(name, observed, limit)
        self.name = name
        self.observed = observed
        self.limit = limit

    @property
    def code(self) -> str:
        return "core.pgce.resource-limit@1"

    def __str__(self) -> str:
        return f"PGCE encode resource limit: {self.name} observed={self.observed} limit={self.limit}"
