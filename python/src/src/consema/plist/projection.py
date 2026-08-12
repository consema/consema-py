"""Plist projection: the exact ``plist.value-tree@1`` record or an explicit
unique-key Object (RFC 0013 §9).

Authority (Rust arbitration for exact semantics):

- Targets, policies, and limits: crates/consema-plist/src/projection.rs:49-
  184 — ProjectionTarget::ValueTreeV1 / RequireObjectV1 (projection.rs:53-
  61), UidPolicy Exclude / Include (projection.rs:63-71), CollisionPolicy
  Reject / First / Last (projection.rs:73-82), the request builders
  (projection.rs:93-157), and ProjectionLimits (projection.rs:159-184:
  max_source_nodes 2_000_000, max_value_nodes 2_000_000,
  max_report_entries 100_000, max_provenance_units 4_000_000).
- Completion algebra and failures: projection.rs:322-403 — CompleteProjection
  (value, fidelity, report, provenance), FailedProjectionAttempt (stable
  ordered diagnostics, empty report), and the StableFailure code mapping
  (projection.rs:393-402: plist.projection.incomplete-document@1,
  unpaired-surrogate@1, collision@1, unrepresentable@1, resource-limit@1,
  core-invariant@1).
- Value-tree emission: projection.rs:412-1155 — the versioned
  ``plist.value-tree@1`` record (one root value, ordered dictionary
  associations, ordered array elements, typed leaves; RFC 0013 §9, lines
  598-613); UIDs project only under IncludeUid into a typed UID member and
  are never disguised as integers; unpaired-surrogate strings fail
  ordinary projection atomically.
- Require-object collapse: projection.rs:1157-1810 — only when every key
  is a string, every value is a string/integer/real/boolean, and the
  chosen collision policy has no collision or supplies a versioned
  Reject | First | Last loss policy; date, data, and UID leaves fail with a
  diagnostic rather than being rendered as strings (hard gate 3); every
  discarded association emits one AssociationDiscarded event (projection.rs:
  272-320) and lifts fidelity to Transformed.
- Provenance: projection.rs:197-270 — ProjectedLocation (Value /
  Association), ProvenanceRelation (Direct / Derived / Collapsed /
  ReferenceDerived), SourceOrigin (snapshot, node, span, relation), and
  the ordered ProvenanceMap. No projection sorts keys, formats dates, or
  invents JSON conventions (RFC 0013 §9).

The value-path and association-location records are the semantic model's
portable input locations (RFC 0004 §8); the protocol layer externalizes
them. This module defines typed in-SDK records with the semantic-model
field shapes, as the other format families do.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import EntryMappingBuilder, ObjectBuilder, PortableValue
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    NodeRole as NodeRoleOf,
    SnapshotIdentity,
    Span,
)
from consema.plist.document import PlistDocument
from consema.plist.errors import (
    PlistDiagnostic,
    PlistProjectionFailure,
    PlistProjectionFailureKind,
    PlistSeverity,
)
from consema.plist.native import PlistValueKind, PlistValueRef
from consema.protocol.error_registry import DiagnosticCategory

_PLIST_EPOCH_SPELLING = "2001-01-01T00:00:00Z"


class ProjectionTarget(enum.Enum):
    """Versioned plist projection target (projection.rs:53-61)."""

    VALUE_TREE_V1 = "plist.projection.value-tree@1"
    REQUIRE_OBJECT_V1 = "plist.projection.require-object@1"


class UidPolicy(enum.Enum):
    """UID handling for the value-tree target (projection.rs:63-71)."""

    EXCLUDE = "Exclude"
    INCLUDE = "Include"


class CollisionPolicy(enum.Enum):
    """Duplicate-key handling for the require-object target
    (projection.rs:73-82)."""

    REJECT = "Reject"
    FIRST = "First"
    LAST = "Last"


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """Plist projection resource limits (projection.rs:159-184)."""

    max_source_nodes: int = 2_000_000
    max_value_nodes: int = 2_000_000
    max_report_entries: int = 100_000
    max_provenance_units: int = 4_000_000


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Explicit plist projection request; every policy is mandatory
    (projection.rs:84-92)."""

    target: ProjectionTarget
    uid_policy: UidPolicy
    collision: CollisionPolicy
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)

    @classmethod
    def value_tree(cls) -> ProjectionRequest:
        """Exact ``plist.value-tree@1`` record request for the complete
        document (projection.rs:94-103)."""
        return cls(
            target=ProjectionTarget.VALUE_TREE_V1,
            uid_policy=UidPolicy.EXCLUDE,
            collision=CollisionPolicy.REJECT,
        )

    @classmethod
    def value_tree_with_uid(cls, policy: UidPolicy) -> ProjectionRequest:
        """Exact value-tree request with an explicit UID policy
        (projection.rs:105-114)."""
        return cls(
            target=ProjectionTarget.VALUE_TREE_V1,
            uid_policy=policy,
            collision=CollisionPolicy.REJECT,
        )

    @classmethod
    def require_object(cls, collision: CollisionPolicy) -> ProjectionRequest:
        """Explicit require-object request with one duplicate-key loss
        policy (projection.rs:117-126)."""
        return cls(
            target=ProjectionTarget.REQUIRE_OBJECT_V1,
            uid_policy=UidPolicy.EXCLUDE,
            collision=collision,
        )

    def with_limits(self, limits: ProjectionLimits) -> ProjectionRequest:
        """Applies explicit resource limits to this request
        (projection.rs:128-132)."""
        return ProjectionRequest(
            target=self.target,
            uid_policy=self.uid_policy,
            collision=self.collision,
            limits=limits,
        )


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:186-195)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


# -- value paths and association locations ----------------------------------


class ValuePathSegmentKind(enum.Enum):
    """Value-path segment kinds of the semantic model (RFC 0004 §8)."""

    SEQUENCE_ELEMENT = "SequenceElement"
    OBJECT_VALUE = "ObjectValue"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


@dataclass(frozen=True, slots=True)
class ValuePathSegment:
    """One value-path segment."""

    kind: ValuePathSegmentKind
    key: object  # int ordinal or str name


@dataclass(frozen=True, slots=True)
class ValuePath:
    """Portable input value path (RFC 0004 §8)."""

    segments: tuple[ValuePathSegment, ...] = ()

    @classmethod
    def root(cls) -> ValuePath:
        return cls()

    def child(self, segment: ValuePathSegment) -> ValuePath:
        return ValuePath(self.segments + (segment,))

    def __repr__(self) -> str:
        return "Root" if not self.segments else "Root/" + "/".join(
            f"{segment.kind.value}({segment.key})" for segment in self.segments
        )


class AssociationRole(enum.Enum):
    """Association roles of the semantic model."""

    OBJECT_ENTRY = "ObjectEntry"
    OBJECT_KEY = "ObjectKey"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"


@dataclass(frozen=True, slots=True)
class AssociationLocation:
    """Portable association location (RFC 0004 §8)."""

    path: ValuePath
    ordinal: int
    role: AssociationRole


# -- projected locations, provenance, events ---------------------------------


class ProjectedLocationKind(enum.Enum):
    """Projected location kind (projection.rs:197-204)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """One portable projected location (projection.rs:197-204)."""

    kind: ProjectedLocationKind
    path: ValuePath | None = None
    association: AssociationLocation | None = None

    @classmethod
    def value_location(cls, path: ValuePath) -> ProjectedLocation:
        return cls(ProjectedLocationKind.VALUE, path=path)

    @classmethod
    def association_location(cls, association: AssociationLocation) -> ProjectedLocation:
        return cls(ProjectedLocationKind.ASSOCIATION, association=association)


class ProvenanceRelation(enum.Enum):
    """Source-to-projection relation (projection.rs:206-217)."""

    DIRECT = "Direct"
    DERIVED = "Derived"
    COLLAPSED = "Collapsed"
    REFERENCE_DERIVED = "ReferenceDerived"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs:219-230)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance entry (projection.rs:232-239)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable many-valued provenance mapping (projection.rs:241-270)."""

    entries: tuple[ProvenanceEntry, ...] = ()


class ProjectionEventKind(enum.Enum):
    """Projection report category (projection.rs:272-278)."""

    ASSOCIATION_DISCARDED = "AssociationDiscarded"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One explicit transformation event (projection.rs:280-289)."""

    kind: ProjectionEventKind
    discarded: NodeRef
    impact: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs:291-320)."""

    events: tuple[ProjectionEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs:322-333)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed projection attempt without a partial value (projection.rs:335-
    342)."""

    diagnostics: tuple[PlistDiagnostic, ...]
    report: ProjectionReport = ProjectionReport()


class ProjectionResult:
    """Projection completion algebra (projection.rs:344-351)."""

    def __init__(self, complete: CompleteProjection | None = None, failed: FailedProjectionAttempt | None = None):
        self.complete = complete
        self.failed = failed

    @classmethod
    def completed(cls, projection: CompleteProjection) -> ProjectionResult:
        return cls(complete=projection)

    @classmethod
    def failed(cls, attempt: FailedProjectionAttempt) -> ProjectionResult:
        return cls(failed=attempt)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class _Projector:
    """One projection pass over one immutable snapshot."""

    def __init__(
        self,
        document: PlistDocument,
        request: ProjectionRequest,
        native,
    ) -> None:
        self.document = document
        self.request = request
        self.native = native
        self.value_nodes = 0
        self.source_nodes = 0
        self.provenance: list[ProvenanceEntry] = []
        self.events: list[ProjectionEvent] = []
        self.diagnostics: list[PlistDiagnostic] = []
        self.fidelity = Fidelity.EXACT
        self.source_spans: dict[int, Span] = _source_spans(document, native)
        self.authority = document.authority
        self.next_node = 0

    # -- resource accounting -------------------------------------------------

    def check_value_nodes(self, count: int = 1) -> None:
        self.value_nodes += count
        if self.value_nodes > self.request.limits.max_value_nodes:
            raise PlistProjectionFailure(
                PlistProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_value_nodes",
            )

    def check_source_nodes(self) -> None:
        self.source_nodes += 1
        if self.source_nodes > self.request.limits.max_source_nodes:
            raise PlistProjectionFailure(
                PlistProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_source_nodes",
            )

    def check_report(self) -> None:
        if len(self.events) > self.request.limits.max_report_entries:
            raise PlistProjectionFailure(
                PlistProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_report_entries",
            )

    def check_provenance(self, units: int = 2) -> None:
        observed = sum(
            1 + len(entry.origins) for entry in self.provenance
        ) + units
        if observed > self.request.limits.max_provenance_units:
            raise PlistProjectionFailure(
                PlistProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_provenance_units",
            )

    # -- provenance helpers --------------------------------------------------

    def add_origin(self, projected: ProjectedLocation, node: NodeRef, span: Span, relation: ProvenanceRelation) -> None:
        self.check_provenance()
        entry = ProvenanceEntry(
            projected=projected,
            origins=((SourceOrigin(self.document.snapshot_identity(), node, span, relation),)),
        )
        if relation is ProvenanceRelation.DIRECT:
            self.provenance.insert(0, entry)
        else:
            self.provenance.append(entry)

    def add_existing_origin(self, projected: ProjectedLocation, origin: SourceOrigin) -> None:
        for entry in self.provenance:
            if entry.projected == projected:
                self.provenance.remove(entry)
                self.provenance.append(
                    ProvenanceEntry(projected=projected, origins=entry.origins + (origin,))
                )
                return
        self.add_origin(projected, origin.node, origin.span, origin.relation)

    # -- value-tree emission --------------------------------------------------

    def project_value(self, value_ref: PlistValueRef, path: ValuePath, depth: int) -> PortableValue:
        """Projects one native value node (projection.rs:588-1155)."""
        self.check_source_nodes()
        value = self.native.get(value_ref)
        if value is None:
            raise PlistProjectionFailure(PlistProjectionFailureKind.CORE_INVARIANT)
        kind = value.kind
        if kind is PlistValueKind.DICT:
            builder = EntryMappingBuilder()
            dict_value = value.payload
            for position, entry in enumerate(dict_value.entries):
                key = entry.key
                if key.status().value == "UnpairedSurrogate":
                    raise PlistProjectionFailure(
                        PlistProjectionFailureKind.UNPAIRED_SURROGATE
                    )
                entry_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, key.to_unicode())
                )
                association = AssociationLocation(
                    entry_path, position, AssociationRole.ENTRY_MAPPING_ENTRY
                )
                value_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, key.to_unicode())
                )
                self.add_origin(
                    ProjectedLocation.association_location(association),
                    self.entry_node(position, value_ref),
                    self.entry_span(value_ref, position),
                    ProvenanceRelation.DIRECT,
                )
                child = self.project_value(entry.value, value_path, depth + 1)
                builder.push(PortableValue.string(key.to_unicode()), child)
                self.add_origin(
                    ProjectedLocation.value_location(value_path),
                    self.value_node(entry.value),
                    self.value_span(entry.value),
                    ProvenanceRelation.DIRECT,
                )
                self.check_report()
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DERIVED,
            )
            return builder.build()
        if kind is PlistValueKind.ARRAY:
            builder_items = []
            array_value = value.payload
            for position, element in enumerate(array_value.elements):
                element_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, position)
                )
                child = self.project_value(element, element_path, depth + 1)
                builder_items.append(child)
                self.add_origin(
                    ProjectedLocation.value_location(element_path),
                    self.value_node(element),
                    self.value_span(element),
                    ProvenanceRelation.DIRECT,
                )
                self.check_report()
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DERIVED,
            )
            return PortableValue.sequence(builder_items)
        if kind is PlistValueKind.STRING:
            string = value.payload
            if string.status().value == "UnpairedSurrogate":
                raise PlistProjectionFailure(
                    PlistProjectionFailureKind.UNPAIRED_SURROGATE
                )
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.string(string.to_unicode())
        if kind is PlistValueKind.INTEGER:
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.integer(value.payload.value)
        if kind is PlistValueKind.REAL:
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.binary_float64(_f64_bits(value.payload.as_f64()))
        if kind is PlistValueKind.BOOLEAN:
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.boolean(value.payload.value)
        if kind is PlistValueKind.DATE:
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            seconds = value.payload.seconds
            return PortableValue.object(
                (
                    ("epoch", PortableValue.string(_PLIST_EPOCH_SPELLING)),
                    ("seconds", PortableValue.binary_float64(_f64_bits(seconds))),
                )
            )
        if kind is PlistValueKind.DATA:
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.bytes_value(value.payload.bytes)
        if kind is PlistValueKind.UID:
            if self.request.uid_policy is UidPolicy.EXCLUDE:
                raise PlistProjectionFailure(
                    PlistProjectionFailureKind.UNREPRESENTABLE,
                    detail="uid",
                )
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DIRECT,
            )
            return PortableValue.object(
                (("uid", PortableValue.integer(value.payload.value)),)
            )
        raise PlistProjectionFailure(PlistProjectionFailureKind.CORE_INVARIANT)

    # -- identity and span helpers --------------------------------------------

    def value_node(self, value_ref: PlistValueRef) -> NodeRef:
        node = self.authority.node_ref(self.next_node, NodeRoleOf.PLIST_VALUE)
        self.next_node += 1
        return node

    def entry_node(self, position: int, dict_ref: PlistValueRef) -> NodeRef:
        node = self.authority.node_ref(self.next_node, NodeRoleOf.PLIST_DICT_ENTRY)
        self.next_node += 1
        return node

    def value_span(self, value_ref: PlistValueRef) -> Span:
        span = self.source_spans.get(value_ref.index)
        if span is None:
            span = self.authority.span(0, 0)
        return span

    def entry_span(self, dict_ref: PlistValueRef, position: int) -> Span:
        return self.value_span(dict_ref)

    # -- require-object collapse ----------------------------------------------

    def project_require_object(self, value_ref: PlistValueRef, path: ValuePath, depth: int) -> PortableValue:
        """Require-object projection with the explicit collision policy
        (projection.rs:1157-1810)."""
        self.check_source_nodes()
        value = self.native.get(value_ref)
        if value is None:
            raise PlistProjectionFailure(PlistProjectionFailureKind.CORE_INVARIANT)
        kind = value.kind
        if kind is PlistValueKind.DICT:
            dict_value = value.payload
            entries = dict_value.entries
            # Collision resolution over the ordered associations
            # (projection.rs:1157-1810): Reject fails, First keeps the first
            # occurrence, Last keeps the last; every discarded association
            # emits one AssociationDiscarded event and lifts fidelity to
            # Transformed.
            kept_positions: set[int] = set()
            discarded: list[int] = []
            seen: dict[str, int] = {}
            for position, entry in enumerate(entries):
                if entry.key.status().value == "UnpairedSurrogate":
                    raise PlistProjectionFailure(
                        PlistProjectionFailureKind.UNPAIRED_SURROGATE
                    )
                key_text = entry.key.to_unicode()
                if key_text in seen:
                    if self.request.collision is CollisionPolicy.REJECT:
                        raise PlistProjectionFailure(
                            PlistProjectionFailureKind.COLLISION, key=key_text
                        )
                    if self.request.collision is CollisionPolicy.FIRST:
                        discarded.append(position)
                        self._discard_event(position, value_ref)
                    else:  # Last
                        discarded.append(seen[key_text])
                        self._discard_event(seen[key_text], value_ref)
                        seen[key_text] = position
                else:
                    seen[key_text] = position
            builder = ObjectBuilder()
            for position, entry in enumerate(entries):
                if position in discarded:
                    continue
                key = entry.key
                key_text = key.to_unicode()
                entry_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, key_text)
                )
                entry_node = self.entry_node(position, value_ref)
                association = AssociationLocation(
                    entry_path, position, AssociationRole.OBJECT_ENTRY
                )
                self.add_origin(
                    ProjectedLocation.association_location(association),
                    entry_node,
                    self.entry_span(value_ref, position),
                    ProvenanceRelation.DIRECT,
                )
                child = self.project_require_object(entry.value, entry_path, depth + 1)
                builder.insert(key_text, child)
                self.check_report()
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DERIVED,
            )
            return builder.build()
        if kind is PlistValueKind.ARRAY:
            items = [
                self.project_require_object(
                    element,
                    path.child(
                        ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, position)
                    ),
                    depth + 1,
                )
                for position, element in enumerate(value.payload.elements)
            ]
            self.check_value_nodes()
            self.add_origin(
                ProjectedLocation.value_location(path),
                self.value_node(value_ref),
                self.value_span(value_ref),
                ProvenanceRelation.DERIVED,
            )
            return PortableValue.sequence(items)
        if kind in (PlistValueKind.STRING, PlistValueKind.INTEGER, PlistValueKind.REAL, PlistValueKind.BOOLEAN):
            return self.project_scalar_leaf(value_ref, depth)
        raise PlistProjectionFailure(
            PlistProjectionFailureKind.UNREPRESENTABLE,
            detail=kind.value,
        )

    def _discard_event(self, position: int, dict_ref: PlistValueRef) -> None:
        """One discarded association event (projection.rs:272-320)."""
        self.events.append(
            ProjectionEvent(
                ProjectionEventKind.ASSOCIATION_DISCARDED,
                self.entry_node(position, dict_ref),
                Fidelity.TRANSFORMED,
            )
        )
        self.fidelity = Fidelity.TRANSFORMED
        self.check_report()

    def project_scalar_leaf(self, value_ref: PlistValueRef, depth: int) -> PortableValue:
        self.check_source_nodes()
        value = self.native.get(value_ref)
        if value is None:
            raise PlistProjectionFailure(PlistProjectionFailureKind.CORE_INVARIANT)
        kind = value.kind
        if kind is PlistValueKind.STRING:
            string = value.payload
            if string.status().value == "UnpairedSurrogate":
                raise PlistProjectionFailure(
                    PlistProjectionFailureKind.UNPAIRED_SURROGATE
                )
            self.check_value_nodes()
            return PortableValue.string(string.to_unicode())
        if kind is PlistValueKind.INTEGER:
            self.check_value_nodes()
            return PortableValue.integer(value.payload.value)
        if kind is PlistValueKind.REAL:
            self.check_value_nodes()
            return PortableValue.binary_float64(_f64_bits(value.payload.as_f64()))
        if kind is PlistValueKind.BOOLEAN:
            self.check_value_nodes()
            return PortableValue.boolean(value.payload.value)
        raise PlistProjectionFailure(
            PlistProjectionFailureKind.UNREPRESENTABLE,
            detail=kind.value,
        )


def _f64_bits(value: float) -> int:
    import struct

    return struct.unpack(">Q", struct.pack(">d", value))[0]


def _source_spans(document: PlistDocument, native) -> dict[int, Span]:
    """Exact raw span of every native arena node: the binary object
    marker-through-payload range, or the XML value element open-through-
    close range recorded by the parser in close-tag order."""
    spans: dict[int, Span] = {}
    formed = document.formed_binary if document.formed_binary is not None else document.formed_xml
    if formed is None:
        return spans
    value_spans = getattr(formed, "value_spans", None)
    if value_spans is not None:
        return dict(value_spans)
    return spans


def project(document: PlistDocument, request: ProjectionRequest) -> ProjectionResult:
    """Projects one complete plist document under one explicit target and
    policy contract (RFC 0013 §9; projection.rs:405-412).

    The projection is atomic: a recovered source, an unpaired-surrogate
    string, an unrepresentable leaf, or a resource limit returns no partial
    value, provenance, or report (hard gate 3)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        return ProjectionResult.failed(
            FailedProjectionAttempt(
                diagnostics=(
                    PlistDiagnostic(
                        code="plist.projection.incomplete-document@1",
                        category=DiagnosticCategory.CONFORMANCE,
                        severity=PlistSeverity.ERROR,
                        primary=None,
                    ),
                )
            )
        )
    native = document.document()
    if native is None:
        return ProjectionResult.failed(
            FailedProjectionAttempt(
                diagnostics=(
                    PlistDiagnostic(
                        code="plist.projection.incomplete-document@1",
                        category=DiagnosticCategory.CONFORMANCE,
                        severity=PlistSeverity.ERROR,
                        primary=None,
                    ),
                )
            )
        )
    projector = _Projector(document, request, native)
    try:
        if request.target is ProjectionTarget.VALUE_TREE_V1:
            record = PortableValue.object(
                (
                    ("record", PortableValue.string("plist.value-tree@1")),
                    ("root", projector.project_value(native.root(), ValuePath.root(), 0)),
                )
            )
        else:
            # The require-object target carries the unique-key Object itself
            # (projection.rs:1157-1810; the Go runner reads the projection
            # value as a plain *core.Object — go/conformance/plist_v1.go:
            # 2636-2641), not a value-tree record wrapper.
            record = projector.project_require_object(native.root(), ValuePath.root(), 0)
    except PlistProjectionFailure as failure:
        return ProjectionResult.failed(
            FailedProjectionAttempt(
                diagnostics=(
                    PlistDiagnostic(
                        code=failure.code,
                        category=DiagnosticCategory.CONFORMANCE,
                        severity=PlistSeverity.ERROR,
                        primary=None,
                        arguments=(
                            {"key": failure.key}
                            if failure.key is not None
                            else ({"resource": failure.resource_name} if failure.resource_name else {})
                        ),
                    ),
                )
            )
        )
    return ProjectionResult.completed(
        CompleteProjection(
            value=record,
            fidelity=projector.fidelity,
            report=ProjectionReport(tuple(projector.events)),
            provenance=ProvenanceMap(tuple(projector.provenance)),
        )
    )
