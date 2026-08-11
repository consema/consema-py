"""YAML graph and value projection: exact-first selection with provenance.

Authority (language-neutral first; Rust only for arbitration):

- RFC 0007 s10 (docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md:260-301):
  yaml.projection.best-exact-graph@1 preserves all standard resolved tags,
  arbitrary keys, association order, sharing, and cycles; provenance relates
  graph nodes and edges to every relevant source node/alias occurrence
  without collapsing duplicate origins. The value target defaults
  (RequireExactlyOneDocument, Reject sharing, Reject cycles,
  RequireKnownPortableTag, BestExactObjectOrEntryMapping, alias expansion
  disabled) freeze SharingPolicy/TagPolicy/MappingPolicy.
- Policies and limits: crates/consema-yaml/src/projection.rs:18-62 (graph
  request/limits), 204-258 (SharingPolicy, TagPolicy, MappingPolicy,
  ValueProjectionLimits), 260-332 (ValueProjectionRequest), 334-420
  (Fidelity, events, report), 436-529 (failures and codes).
- Graph projection with provenance: projection.rs:531-754 — the root/node/
  edge origin order, the Reference relation for alias edges, and the
  provenance-unit limit.
- Value projection: projection.rs:771-1147 — cycle detection on the active
  stack, sharing events, tag stripping, object/entry-mapping selection
  (object_names projection.rs:1149-1170), timestamp/binary lowering, and
  provenance.
- Vector surface: conformance/vectors/yaml-v1.json cases graph.shared-cycle
  (node_count/root_count/pgce_hex), projection.sharing-policy (default
  code, event_count 3), projection.cycle, projection.tag-policy,
  projection.mapping-policy (object_code, entry_count),
  projection.graph-provenance (reference_origins, association_entries),
  resource.graph-provenance (yaml.projection.provenance-limit@1).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import Decimal, PortableValue
from consema.document.structural import NodeRef, NodeRole, SnapshotIdentity, Span
from consema.graph import (
    GraphBuildError,
    GraphBuilder,
    GraphLimits,
    GraphMappingEntry,
    GraphNodeId,
)
from consema.yaml.document import Document
from consema.yaml.errors import (
    YamlGraphProjectionError,
    YamlGraphProjectionErrorKind,
    YamlProjectionFailure,
    YamlProjectionFailureKind,
)
from consema.yaml.kinds import YamlNodeKind, YamlScalarKind
from consema.yaml.parser import (
    NativeMappingEntry,
    NativeScalar,
    NativeSequenceItem,
    TAG_BINARY,
    TAG_BOOL,
    TAG_FLOAT,
    TAG_INT,
    TAG_MAP,
    TAG_MERGE,
    TAG_NULL,
    TAG_OMAP,
    TAG_PAIRS,
    TAG_SEQ,
    TAG_SET,
    TAG_STR,
    TAG_TIMESTAMP,
    TAG_VALUE,
    TAG_YAML,
    node_ref as _node_ref,
)

# Frozen non-finite binary64 bit patterns (RFC 0007 s5; JSON5 parity).
BITS_POSITIVE_INFINITY = 0x7FF0000000000000
BITS_NEGATIVE_INFINITY = 0xFFF0000000000000
BITS_NAN = 0x7FF8000000000000


class SharingPolicy(enum.Enum):
    """Explicit YAML graph-sharing policy for PortableValue projection
    (projection.rs:204-211)."""

    REJECT = "Reject"
    DUPLICATE_ACYCLIC = "DuplicateAcyclic"


class TagPolicy(enum.Enum):
    """Explicit YAML tag policy for PortableValue projection
    (projection.rs:213-220)."""

    REQUIRE_KNOWN_PORTABLE_TAG = "RequireKnownPortableTag"
    STRIP_TO_NODE_KIND = "StripToNodeKind"


class MappingPolicy(enum.Enum):
    """YAML mapping-to-tree selection policy (projection.rs:222-231)."""

    BEST_EXACT_OBJECT_OR_ENTRY_MAPPING = "BestExactObjectOrEntryMapping"
    REQUIRE_OBJECT = "RequireObject"
    REQUIRE_ENTRY_MAPPING = "RequireEntryMapping"


@dataclass(frozen=True, slots=True)
class GraphProjectionLimits:
    """Graph projection resource contract (projection.rs:19-33)."""

    graph: GraphLimits = field(default_factory=GraphLimits)
    max_provenance_entries: int = 2_000_000


@dataclass(frozen=True, slots=True)
class GraphProjectionRequest:
    """Immutable ``yaml.projection.best-exact-graph@1`` request
    (projection.rs:35-62)."""

    limits: GraphProjectionLimits = field(default_factory=GraphProjectionLimits)

    @classmethod
    def best_exact_v1(cls) -> GraphProjectionRequest:
        return cls()

    def with_limits(self, limits: GraphProjectionLimits) -> GraphProjectionRequest:
        return GraphProjectionRequest(limits=limits)


class ProjectedLocationKind(enum.Enum):
    """Graph-projected location kinds (projection.rs:64-92)."""

    ROOT = "Root"
    NODE = "Node"
    SEQUENCE_ELEMENT = "SequenceElement"
    MAPPING_KEY = "MappingKey"
    MAPPING_VALUE = "MappingValue"


@dataclass(frozen=True, slots=True)
class GraphProjectedLocation:
    """One exact projected graph location (projection.rs:64-92)."""

    kind: ProjectedLocationKind
    parent: GraphNodeId | None = None
    ordinal: int | None = None
    node: GraphNodeId | None = None


class ProvenanceRelation(enum.Enum):
    """Source relation shared by graph and tree projection provenance
    (projection.rs:94-105)."""

    DIRECT = "Direct"
    REFERENCE = "Reference"
    EXPANDED = "Expanded"
    TAG_STRIPPED = "TagStripped"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact YAML source origin (projection.rs:107-119)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class GraphProvenanceEntry:
    """One graph provenance multimap entry (projection.rs:121-127)."""

    projected: GraphProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class GraphProvenanceMap:
    """Complete deterministic graph provenance multimap (projection.rs:129-141)."""

    entries: tuple[GraphProvenanceEntry, ...] = ()

    def reference_origin_count(self) -> int:
        """Number of Reference origins (vector projection.graph-provenance
        field ``reference_origins``)."""
        return sum(
            1
            for entry in self.entries
            for origin in entry.origins
            if origin.relation is ProvenanceRelation.REFERENCE
        )

    def association_entry_count(self) -> int:
        """Number of sequence/mapping association entries (vector
        projection.graph-provenance field ``association_entries``)."""
        return sum(
            1
            for entry in self.entries
            if entry.projected.kind
            in (
                ProjectedLocationKind.SEQUENCE_ELEMENT,
                ProjectedLocationKind.MAPPING_KEY,
                ProjectedLocationKind.MAPPING_VALUE,
            )
        )


@dataclass(frozen=True, slots=True)
class CompleteGraphProjection:
    """Complete exact graph projection (projection.rs:143-152)."""

    graph: object
    provenance: GraphProvenanceMap


# -- value projection --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValueProjectionLimits:
    """PortableValue projection resource contract (projection.rs:233-258)."""

    max_value_nodes: int = 1_000_000
    max_depth: int = 256
    max_report_entries: int = 100_000
    max_provenance_entries: int = 2_000_000
    max_amplification_ratio: int = 16


@dataclass(frozen=True, slots=True)
class ValueProjectionRequest:
    """Immutable ``yaml.projection.best-exact-value@1`` request
    (projection.rs:260-332)."""

    sharing: SharingPolicy = SharingPolicy.REJECT
    tags: TagPolicy = TagPolicy.REQUIRE_KNOWN_PORTABLE_TAG
    mapping: MappingPolicy = MappingPolicy.BEST_EXACT_OBJECT_OR_ENTRY_MAPPING
    limits: ValueProjectionLimits = field(default_factory=ValueProjectionLimits)

    @classmethod
    def best_exact_v1(cls) -> ValueProjectionRequest:
        return cls()

    def with_sharing(self, sharing: SharingPolicy) -> ValueProjectionRequest:
        return ValueProjectionRequest(sharing=sharing, tags=self.tags, mapping=self.mapping, limits=self.limits)

    def with_tags(self, tags: TagPolicy) -> ValueProjectionRequest:
        return ValueProjectionRequest(sharing=self.sharing, tags=tags, mapping=self.mapping, limits=self.limits)

    def with_mapping(self, mapping: MappingPolicy) -> ValueProjectionRequest:
        return ValueProjectionRequest(sharing=self.sharing, tags=self.tags, mapping=mapping, limits=self.limits)

    def with_limits(self, limits: ValueProjectionLimits) -> ValueProjectionRequest:
        return ValueProjectionRequest(sharing=self.sharing, tags=self.tags, mapping=self.mapping, limits=limits)


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:334-343)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProjectionEventKind(enum.Enum):
    """Structured YAML value projection event category (projection.rs:377-384)."""

    SHARING_DUPLICATED = "SharingDuplicated"
    TAG_STRIPPED = "TagStripped"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One machine-readable projection transformation/loss event
    (projection.rs:386-405)."""

    kind: ProjectionEventKind
    policy: str
    source: NodeRef
    path: object
    old_category: str
    new_category: str
    reversible: bool
    loss: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered value projection report (projection.rs:407-419)."""

    events: tuple[ProjectionEvent, ...] = ()


class ValuePathSegmentKind(enum.Enum):
    """Value-path segment kinds of the semantic model (RFC 0004 s8)."""

    SEQUENCE_ELEMENT = "SequenceElement"
    OBJECT_VALUE = "ObjectValue"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


@dataclass(frozen=True, slots=True)
class ValuePathSegment:
    """One value-path segment."""

    kind: ValuePathSegmentKind
    key: object


@dataclass(frozen=True, slots=True)
class ValuePath:
    """Portable input value path (RFC 0004 s8)."""

    segments: tuple[ValuePathSegment, ...] = ()

    @classmethod
    def root(cls) -> ValuePath:
        return cls()

    def child(self, segment: ValuePathSegment) -> ValuePath:
        return ValuePath(self.segments + (segment,))


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """One PortableValue or association location (projection.rs:345-352)."""

    path: ValuePath
    association: bool = False


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One PortableValue provenance entry (projection.rs:354-362)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Complete deterministic PortableValue provenance multimap
    (projection.rs:364-375)."""

    entries: tuple[ProvenanceEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteValueProjection:
    """Complete successful PortableValue projection (projection.rs:422-432)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedValueProjection:
    """Value projection failure; no partial value or provenance is returned
    (projection.rs:436-476, 522-529)."""

    kind: YamlProjectionFailureKind
    code: str
    resource_name: str | None = None
    tag: str | None = None


# -- graph projection --------------------------------------------------------


def project_graph_with_provenance(
    document: Document,
    request: GraphProjectionRequest,
) -> CompleteGraphProjection:
    """Applies exact graph projection with complete node/edge/alias
    provenance (projection.rs:531-554)."""
    limits = request.limits
    builder = GraphBuilder(limits.graph)
    ids: list[GraphNodeId] = []
    for _ in range(len(document.native.nodes)):
        ids.append(builder.reserve_node())
    for index, node in enumerate(document.native.nodes):
        if not _is_standard_graph_tag(node.tag):
            raise YamlGraphProjectionError(
                YamlGraphProjectionErrorKind.UNSUPPORTED_TAG, tag=node.tag
            )
        content = node.content
        try:
            if isinstance(content, NativeScalar):
                builder.define_scalar(ids[index], node.tag, content.canonical)
            elif isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
                builder.define_sequence(
                    ids[index], node.tag, [ids[item.node] for item in content]
                )
            else:
                builder.define_mapping(
                    ids[index],
                    node.tag,
                    [
                        GraphMappingEntry(ids[entry.key], ids[entry.value])
                        for entry in content
                    ],
                )
        except GraphBuildError as error:
            raise YamlGraphProjectionError(
                YamlGraphProjectionErrorKind.GRAPH, graph_message=str(error)
            ) from None
    for record in document.native.documents:
        builder.push_root(ids[record.root])
    try:
        graph = builder.build()
    except GraphBuildError as error:
        raise YamlGraphProjectionError(
            YamlGraphProjectionErrorKind.GRAPH, graph_message=str(error)
        ) from None
    provenance = _graph_provenance(document, ids, limits.max_provenance_entries)
    return CompleteGraphProjection(graph=graph, provenance=provenance)


def project_graph(document: Document, graph_limits: GraphLimits | None = None):
    """Projects all document roots to one exact PortableGraph (lib.rs:433-448)."""
    request = GraphProjectionRequest(
        limits=GraphProjectionLimits(graph=graph_limits or GraphLimits())
    )
    return project_graph_with_provenance(document, request).graph


def _graph_provenance(document: Document, ids: list[GraphNodeId], max_entries: int) -> GraphProvenanceMap:
    """Root/node/edge provenance in construction order (projection.rs:605-754)."""
    entries: list[GraphProvenanceEntry] = []
    index: dict[tuple, int] = {}
    units = 0

    def add(projected: GraphProjectedLocation, origin: SourceOrigin) -> None:
        nonlocal units
        existing = index.get(_location_key(projected))
        observed = units + (1 if existing is not None else 2)
        if observed > max_entries:
            raise YamlGraphProjectionError(YamlGraphProjectionErrorKind.PROVENANCE_LIMIT)
        units = observed
        if existing is not None:
            prior = entries[existing]
            entries[existing] = GraphProvenanceEntry(
                projected=prior.projected, origins=prior.origins + (origin,)
            )
        else:
            index[_location_key(projected)] = len(entries)
            entries.append(GraphProvenanceEntry(projected=projected, origins=(origin,)))

    for ordinal, record in enumerate(document.native.documents):
        add(
            GraphProjectedLocation(kind=ProjectedLocationKind.ROOT, ordinal=ordinal),
            SourceOrigin(
                snapshot=document.snapshot_identity(),
                node=document.authority.node_ref(ordinal, NodeRole.YAML_DOCUMENT),
                span=record.span,
                relation=ProvenanceRelation.DIRECT,
            ),
        )
    def add_alias(location: GraphProjectedLocation, ordinal: int) -> None:
        alias = document.native.aliases[ordinal]
        add(
            location,
            SourceOrigin(
                snapshot=document.snapshot_identity(),
                node=document.authority.node_ref(alias.identity, NodeRole.YAML_ALIAS),
                span=alias.span,
                relation=ProvenanceRelation.REFERENCE,
            ),
        )

    for index_number, node in enumerate(document.native.nodes):
        add(
            GraphProjectedLocation(kind=ProjectedLocationKind.NODE, node=ids[index_number]),
            SourceOrigin(
                snapshot=document.snapshot_identity(),
                node=_node_ref(document.authority, index_number),
                span=node.span,
                relation=ProvenanceRelation.DIRECT,
            ),
        )
        content = node.content
        if isinstance(content, NativeScalar):
            continue
        if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            for ordinal, item in enumerate(content):
                location = GraphProjectedLocation(
                    kind=ProjectedLocationKind.SEQUENCE_ELEMENT,
                    parent=ids[index_number],
                    ordinal=ordinal,
                )
                add(
                    location,
                    SourceOrigin(
                        snapshot=document.snapshot_identity(),
                        node=document.authority.node_ref(
                            item.identity, NodeRole.YAML_SEQUENCE_ELEMENT
                        ),
                        span=item.span,
                        relation=ProvenanceRelation.DIRECT,
                    ),
                )
                if item.alias is not None:
                    add_alias(location, item.alias)
        else:
            for ordinal, entry in enumerate(content):
                for role, alias in (
                    (ProjectedLocationKind.MAPPING_KEY, entry.key_alias),
                    (ProjectedLocationKind.MAPPING_VALUE, entry.value_alias),
                ):
                    location = GraphProjectedLocation(
                        kind=role, parent=ids[index_number], ordinal=ordinal
                    )
                    add(
                        location,
                        SourceOrigin(
                            snapshot=document.snapshot_identity(),
                            node=document.authority.node_ref(
                                entry.identity, NodeRole.YAML_MAPPING_ENTRY
                            ),
                            span=entry.span,
                            relation=ProvenanceRelation.DIRECT,
                        ),
                    )
                    if alias is not None:
                        add_alias(location, alias)
    return GraphProvenanceMap(entries=tuple(entries))


def _location_key(location: GraphProjectedLocation) -> tuple:
    return (
        location.kind.value,
        location.parent.as_u64() if location.parent is not None else None,
        location.ordinal,
        location.node.as_u64() if location.node is not None else None,
    )


def _is_standard_graph_tag(tag: str) -> bool:
    return tag in (
        TAG_NULL, TAG_BOOL, TAG_INT, TAG_FLOAT, TAG_STR, TAG_SEQ, TAG_MAP,
        TAG_TIMESTAMP, TAG_BINARY, TAG_MERGE, TAG_OMAP, TAG_PAIRS, TAG_SET,
        TAG_VALUE, TAG_YAML,
    )


# -- value projection --------------------------------------------------------


def project_value(document: Document, request: ValueProjectionRequest):
    """Applies explicit YAML-to-PortableValue tree projection
    (projection.rs:556-603)."""
    if document.document_count() != 1:
        return FailedValueProjection(
            kind=YamlProjectionFailureKind.DOCUMENT_CARDINALITY,
            code="yaml.projection.document-cardinality@1",
        )
    if request.limits.max_amplification_ratio == 0:
        return FailedValueProjection(
            kind=YamlProjectionFailureKind.RESOURCE_LIMIT,
            code="yaml.projection.resource-limit@1",
            resource_name="max_amplification_ratio",
        )
    context = _ValueContext(document, request)
    root = document.native.documents[0].root
    value = context.project_node(root, ValuePath.root(), 0, None)
    if isinstance(value, FailedValueProjection):
        return value
    maximum = len(context.seen) * request.limits.max_amplification_ratio
    if context.visits > maximum:
        return FailedValueProjection(
            kind=YamlProjectionFailureKind.RESOURCE_LIMIT,
            code="yaml.projection.resource-limit@1",
            resource_name="max_amplification_ratio",
        )
    return CompleteValueProjection(
        value=value,
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report_events)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


class _ValueContext:
    def __init__(self, document: Document, request: ValueProjectionRequest) -> None:
        self.document = document
        self.request = request
        self.seen: set[int] = set()
        self.stack: set[int] = set()
        self.visits = 0
        self.report_events: list[ProjectionEvent] = []
        self.provenance: list[ProvenanceEntry] = []
        self.provenance_index: dict[tuple, int] = {}
        self.provenance_units = 0
        self.fidelity = Fidelity.EXACT

    def fail(self, kind: YamlProjectionFailureKind, **kwargs):
        return FailedValueProjection(
            kind=kind, code=_CODE_BY_PROJECTION_KIND[kind], **kwargs
        )

    def project_node(self, index: int, path: ValuePath, depth: int, incoming_alias: int | None):
        limits = self.request.limits
        if depth > limits.max_depth:
            return self.fail(YamlProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_depth")
        self.visits += 1
        if self.visits > limits.max_value_nodes:
            return self.fail(
                YamlProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_value_nodes"
            )
        node_ref = _node_ref(self.document.authority, index)
        if index in self.stack:
            return self.fail(YamlProjectionFailureKind.CYCLE)
        if index in self.seen:
            if self.request.sharing is SharingPolicy.REJECT:
                return self.fail(YamlProjectionFailureKind.SHARING)
            event = self.event(
                ProjectionEventKind.SHARING_DUPLICATED,
                "DuplicateAcyclicSharing@1",
                self.alias_ref(incoming_alias) if incoming_alias is not None else node_ref,
                path,
                "SharedGraphNode",
                "DuplicatedTreeValue",
                False,
                Fidelity.TRANSFORMED,
            )
            if isinstance(event, FailedValueProjection):
                return event
        self.seen.add(index)
        self.stack.add(index)
        node = self.document.native.nodes[index]
        supported = _is_portable_tag(node.tag, node.content)
        if not supported:
            if self.request.tags is TagPolicy.REQUIRE_KNOWN_PORTABLE_TAG:
                return self.fail(
                    YamlProjectionFailureKind.UNSUPPORTED_TAG, tag=node.tag
                )
            event = self.event(
                ProjectionEventKind.TAG_STRIPPED,
                "StripToNodeKind@1",
                node_ref,
                path,
                node.tag,
                _node_kind_name(node.content),
                False,
                Fidelity.LOSSY,
            )
            if isinstance(event, FailedValueProjection):
                return event
        content = node.content
        if isinstance(content, NativeScalar):
            value = self.project_scalar(index, content, supported)
            if isinstance(value, FailedValueProjection):
                return value
        elif isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
            items = []
            for ordinal, item in enumerate(content):
                child_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, ordinal)
                )
                child = self.project_node(item.node, child_path, depth + 1, item.alias)
                if isinstance(child, FailedValueProjection):
                    return child
                items.append(child)
                origin = self.add_origin(
                    ProjectedLocation(child_path),
                    self.document.authority.node_ref(item.identity, NodeRole.YAML_SEQUENCE_ELEMENT),
                    item.span,
                    ProvenanceRelation.DIRECT,
                )
                if isinstance(origin, FailedValueProjection):
                    return origin
            value = PortableValue.sequence(items)
        else:
            value = self.project_mapping(index, content, path, depth)
            if isinstance(value, FailedValueProjection):
                return value
        self.stack.remove(index)
        relation = ProvenanceRelation.DIRECT if supported else ProvenanceRelation.TAG_STRIPPED
        origin = self.add_origin(ProjectedLocation(path), node_ref, node.span, relation)
        if isinstance(origin, FailedValueProjection):
            return origin
        if incoming_alias is not None:
            alias = self.document.native.aliases[incoming_alias]
            origin = self.add_origin(
                ProjectedLocation(path),
                self.document.authority.node_ref(alias.identity, NodeRole.YAML_ALIAS),
                alias.span,
                ProvenanceRelation.EXPANDED,
            )
            if isinstance(origin, FailedValueProjection):
                return origin
        return value

    def project_mapping(self, index: int, entries, path: ValuePath, depth: int):
        object_names = _object_names(self.document, entries)
        use_object = False
        if self.request.mapping is MappingPolicy.BEST_EXACT_OBJECT_OR_ENTRY_MAPPING:
            use_object = object_names is not None
        elif self.request.mapping is MappingPolicy.REQUIRE_OBJECT:
            if object_names is None:
                return self.fail(YamlProjectionFailureKind.MAPPING_NOT_OBJECT)
            use_object = True
        if use_object:
            names = object_names
            pairs = []
            for ordinal, (entry, name) in enumerate(zip(entries, names)):
                key_result = self.visit_object_key(entry.key, entry.key_alias, path)
                if isinstance(key_result, FailedValueProjection):
                    return key_result
                child_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, name)
                )
                value = self.project_node(entry.value, child_path, depth + 1, entry.value_alias)
                if isinstance(value, FailedValueProjection):
                    return value
                pairs.append((name, value))
                origin = self.add_mapping_origins(path, ordinal, entry, True)
                if isinstance(origin, FailedValueProjection):
                    return origin
            return PortableValue.object(pairs)
        builder_entries = []
        for ordinal, entry in enumerate(entries):
            key_path = path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, ordinal))
            value_path = path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, ordinal))
            key = self.project_node(entry.key, key_path, depth + 1, entry.key_alias)
            if isinstance(key, FailedValueProjection):
                return key
            value = self.project_node(entry.value, value_path, depth + 1, entry.value_alias)
            if isinstance(value, FailedValueProjection):
                return value
            builder_entries.append((key, value))
            origin = self.add_mapping_origins(path, ordinal, entry, False)
            if isinstance(origin, FailedValueProjection):
                return origin
        return PortableValue.entry_mapping(builder_entries)

    def visit_object_key(self, index: int, alias: int | None, path: ValuePath):
        node_ref = _node_ref(self.document.authority, index)
        if index in self.stack:
            return self.fail(YamlProjectionFailureKind.CYCLE)
        if index in self.seen:
            if self.request.sharing is SharingPolicy.REJECT:
                return self.fail(YamlProjectionFailureKind.SHARING)
            event = self.event(
                ProjectionEventKind.SHARING_DUPLICATED,
                "DuplicateAcyclicSharing@1",
                self.alias_ref(alias) if alias is not None else node_ref,
                path,
                "SharedGraphNode",
                "DuplicatedObjectKey",
                False,
                Fidelity.TRANSFORMED,
            )
            if isinstance(event, FailedValueProjection):
                return event
        self.seen.add(index)
        self.visits += 1
        if self.visits > self.request.limits.max_value_nodes:
            return self.fail(
                YamlProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_value_nodes"
            )
        return None

    def project_scalar(self, index: int, scalar: NativeScalar, supported: bool):
        invalid = lambda: self.fail(YamlProjectionFailureKind.INVALID_CANONICAL_SCALAR)
        if not supported:
            return PortableValue.string(scalar.decoded)
        if scalar.kind is YamlScalarKind.NULL:
            return PortableValue.null()
        if scalar.kind is YamlScalarKind.BOOLEAN:
            if scalar.canonical == "true":
                return PortableValue.boolean(True)
            if scalar.canonical == "false":
                return PortableValue.boolean(False)
            return invalid()
        if scalar.kind is YamlScalarKind.INTEGER:
            try:
                return PortableValue.integer(int(scalar.canonical))
            except ValueError:
                return invalid()
        if scalar.kind is YamlScalarKind.FLOAT:
            if scalar.canonical == ".inf":
                return PortableValue.binary_float64(BITS_POSITIVE_INFINITY)
            if scalar.canonical == "-.inf":
                return PortableValue.binary_float64(BITS_NEGATIVE_INFINITY)
            if scalar.canonical == ".nan":
                return PortableValue.binary_float64(BITS_NAN)
            decimal = _parse_exact_decimal(scalar.canonical)
            if decimal is None:
                return invalid()
            return PortableValue.decimal(decimal)
        if scalar.kind is YamlScalarKind.STRING:
            return PortableValue.string(scalar.canonical)
        if scalar.kind is YamlScalarKind.BINARY:
            decoded = _decode_base64(scalar.canonical)
            if decoded is None:
                return invalid()
            return PortableValue.bytes_value(decoded)
        if scalar.kind is YamlScalarKind.TIMESTAMP:
            value = _project_timestamp(scalar.canonical)
            if value is None:
                return self.fail(YamlProjectionFailureKind.UNREPRESENTABLE_TIMESTAMP)
            return value
        # Custom/Tagged scalars lower exactly to their decoded string only
        # under an explicit tag policy (projection.rs:1017-1019).
        return PortableValue.string(scalar.decoded)

    def add_mapping_origins(self, path: ValuePath, ordinal: int, entry, object_: bool):
        origin = self.add_origin(
            ProjectedLocation(path, association=True),
            self.document.authority.node_ref(entry.identity, NodeRole.YAML_MAPPING_ENTRY),
            entry.span,
            ProvenanceRelation.DIRECT,
        )
        if isinstance(origin, FailedValueProjection):
            return origin
        if object_:
            key_location = ProjectedLocation(path, association=True)
            key = self.document.native.nodes[entry.key]
            origin = self.add_origin(
                key_location,
                _node_ref(self.document.authority, entry.key),
                key.span,
                ProvenanceRelation.DIRECT,
            )
            if isinstance(origin, FailedValueProjection):
                return origin
            if entry.key_alias is not None:
                alias = self.document.native.aliases[entry.key_alias]
                origin = self.add_origin(
                    key_location,
                    self.document.authority.node_ref(alias.identity, NodeRole.YAML_ALIAS),
                    alias.span,
                    ProvenanceRelation.EXPANDED,
                )
                if isinstance(origin, FailedValueProjection):
                    return origin
        return None

    def event(self, kind, policy, source, path, old_category, new_category, reversible, loss):
        observed = len(self.report_events) + 1
        if observed > self.request.limits.max_report_entries:
            return self.fail(
                YamlProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_report_entries"
            )
        self.report_events.append(
            ProjectionEvent(
                kind=kind,
                policy=policy,
                source=source,
                path=path,
                old_category=old_category,
                new_category=new_category,
                reversible=reversible,
                loss=loss,
            )
        )
        if loss is Fidelity.TRANSFORMED and self.fidelity is Fidelity.EXACT:
            self.fidelity = Fidelity.TRANSFORMED
        elif loss is Fidelity.LOSSY:
            self.fidelity = Fidelity.LOSSY
        return None

    def add_origin(self, projected, node, span, relation):
        existing = self.provenance_index.get((projected.path, projected.association))
        observed = self.provenance_units + (1 if existing is not None else 2)
        if observed > self.request.limits.max_provenance_entries:
            return self.fail(
                YamlProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_provenance_entries"
            )
        self.provenance_units = observed
        origin = SourceOrigin(
            snapshot=self.document.snapshot_identity(), node=node, span=span, relation=relation
        )
        if existing is not None:
            prior = self.provenance[existing]
            self.provenance[existing] = ProvenanceEntry(
                projected=prior.projected, origins=prior.origins + (origin,)
            )
        else:
            self.provenance_index[(projected.path, projected.association)] = len(self.provenance)
            self.provenance.append(ProvenanceEntry(projected=projected, origins=(origin,)))
        return None

    def alias_ref(self, ordinal: int) -> NodeRef:
        alias = self.document.native.aliases[ordinal]
        return self.document.authority.node_ref(alias.identity, NodeRole.YAML_ALIAS)


_CODE_BY_PROJECTION_KIND = {
    YamlProjectionFailureKind.DOCUMENT_CARDINALITY: "yaml.projection.document-cardinality@1",
    YamlProjectionFailureKind.CYCLE: "yaml.projection.cycle@1",
    YamlProjectionFailureKind.SHARING: "yaml.projection.sharing@1",
    YamlProjectionFailureKind.UNSUPPORTED_TAG: "yaml.projection.unsupported-tag@1",
    YamlProjectionFailureKind.MAPPING_NOT_OBJECT: "yaml.projection.mapping-not-object@1",
    YamlProjectionFailureKind.INVALID_CANONICAL_SCALAR: "yaml.projection.invalid-canonical-scalar@1",
    YamlProjectionFailureKind.UNREPRESENTABLE_TIMESTAMP: "yaml.projection.unrepresentable-timestamp@1",
    YamlProjectionFailureKind.RESOURCE_LIMIT: "yaml.projection.resource-limit@1",
}


def _object_names(document: Document, entries) -> list[str] | None:
    """Object eligibility: every key is a unique str tag scalar
    (projection.rs:1149-1170)."""
    seen: set[str] = set()
    names: list[str] = []
    for entry in entries:
        key = document.native.nodes[entry.key]
        content = key.content
        if not isinstance(content, NativeScalar):
            return None
        if key.tag != TAG_STR:
            return None
        name = content.canonical
        if name in seen:
            return None
        seen.add(name)
        names.append(name)
    return names


def _is_portable_tag(tag: str, content) -> bool:
    if isinstance(content, NativeScalar):
        return tag in (TAG_NULL, TAG_BOOL, TAG_INT, TAG_FLOAT, TAG_STR, TAG_TIMESTAMP, TAG_BINARY)
    if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
        return tag == TAG_SEQ
    return tag == TAG_MAP


def _node_kind_name(content) -> str:
    if isinstance(content, NativeScalar):
        return "Scalar"
    if isinstance(content, tuple) and content and isinstance(content[0], NativeSequenceItem):
        return "Sequence"
    return "Mapping"


def _parse_exact_decimal(text: str) -> Decimal | None:
    sign = -1 if text.startswith("-") else 1
    unsigned = text[1:] if text[:1] in ("+", "-") else text
    mantissa = unsigned
    exponent_text = ""
    for marker in ("e", "E"):
        index = unsigned.find(marker)
        if index != -1:
            mantissa, exponent_text = unsigned.split(marker, 1)
            break
    if not mantissa:
        return None
    scale = 0
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
        scale = len(fraction)
        digits = whole + fraction
    else:
        digits = mantissa
    try:
        coefficient = sign * int(digits) if digits else 0
        exponent = int(exponent_text) - scale if exponent_text else -scale
    except ValueError:
        return None
    return Decimal(coefficient, exponent)


def _decode_base64(value: str) -> bytes | None:
    table = {}
    for code in range(26):
        table[chr(ord("A") + code)] = code
        table[chr(ord("a") + code)] = 26 + code
    for code in range(10):
        table[chr(ord("0") + code)] = 52 + code
    table["+"] = 62
    table["/"] = 63
    if len(value) % 4 != 0:
        return None
    output = bytearray()
    for chunk_start in range(0, len(value), 4):
        chunk = value[chunk_start : chunk_start + 4]
        values = []
        for character in chunk:
            if character == "=":
                values.append(None)
            elif character in table:
                values.append(table[character])
            else:
                return None
        if values.count(None) > 2:
            return None
        a, b, c, d = values
        if a is None or b is None:
            return None
        combined = (a << 18) | (b << 12)
        if c is not None:
            combined |= c << 6
        if d is not None:
            combined |= d
        output.append((combined >> 16) & 0xFF)
        if c is not None:
            output.append((combined >> 8) & 0xFF)
        if d is not None:
            output.append(combined & 0xFF)
    return bytes(output)


def _project_timestamp(value: str) -> PortableValue | None:
    """Timestamp lowering to Date/OffsetDateTime (projection.rs:1230-1269)."""
    try:
        year = int(value[0:4])
        month = int(value[5:7])
        day = int(value[8:10])
    except (ValueError, IndexError):
        return None
    try:
        date = PortableValue.date(year, month, day)
    except Exception:
        return None
    if len(value) == 10:
        return date
    if len(value) < 19 or value[10] != "T":
        return None
    try:
        hour = int(value[11:13])
        minute = int(value[14:16])
        second = int(value[17:19])
    except (ValueError, IndexError):
        return None
    tail = value[19:]
    zone_start = None
    for marker in ("Z", "+", "-"):
        index = tail.find(marker)
        if index != -1 and (zone_start is None or index < zone_start):
            zone_start = index
    if zone_start is None:
        return None
    fraction = Decimal(0, 0)
    if zone_start > 0:
        fraction_text = tail[:zone_start]
        if not fraction_text.startswith("."):
            return None
        digits = fraction_text[1:]
        if not digits.isdigit():
            return None
        fraction = Decimal(int(digits), -len(digits))
    try:
        time = PortableValue.time(hour, minute, second, fraction)
        local = PortableValue.local_date_time(date, time)
    except Exception:
        return None
    zone = tail[zone_start:]
    if zone == "Z":
        offset = 0
    else:
        try:
            sign = -1 if zone.startswith("-") else 1
            hours = int(zone[1:3])
            minutes = int(zone[4:6])
        except (ValueError, IndexError):
            return None
        offset = sign * (hours * 3600 + minutes * 60)
    try:
        return PortableValue.offset_date_time(local, offset)
    except Exception:
        return None
