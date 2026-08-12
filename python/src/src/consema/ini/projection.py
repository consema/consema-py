"""INI projection: exact nested EntryMapping or explicit unique Object.

Authority (Rust arbitration for exact semantics):

- Targets, comparison, and collision policy: crates/consema-ini/src/
  projection.rs:9-36 — ProjectionTarget::BestExactEntryMappingV1 /
  RequireObjectV1 (projection.rs:10-16), NameComparison OriginalExact /
  ProfileEquivalent (projection.rs:19-25), CollisionPolicy Reject / First /
  Last (projection.rs:27-36); the request builders projection.rs:47-68;
  the default policy is exact and never collapses (RFC 0009 §10,
  docs/rfcs/0009-ini-family-profiles-v1.md:347-381).
- Limits: projection.rs:102-124 (max_source_associations 2_000_000,
  max_value_nodes 2_000_000, max_report_entries 100_000,
  max_provenance_units 4_000_000).
- Gates and failure algebra: projection.rs:288-314 — Recovered documents
  fail with ini.projection.incomplete-document@1; failure code mapping
  projection.rs:886-893 and the failed-attempt arguments projection.rs:
  852-884 (reason, limit, profile); failed attempts never contain a
  partial value (RFC 0004 §7, docs/rfcs/0004-...:170-191).
- Exact mapping: projection.rs:428-537 — every section occurrence maps to
  an outer EntryMapping entry, every entry occurrence to an inner
  EntryMapping entry, in source order; duplicate spellings remain
  duplicate associations.
- Object collapse: projection.rs:546-785 — explicit comparison and
  collision policy; every discarded association emits one
  SectionCollisionCollapsed / EntryCollisionCollapsed event
  (projection.rs:197-223), lifts fidelity to Transformed
  (projection.rs:372-379), and keeps a Collapsed provenance origin
  (projection.rs:148-159); First and Last retain source occurrence
  spelling and retained-source order (RFC 0009 §10, docs/rfcs/0009-...:
  377-381).
- Provenance: add_origin projection.rs:325-370 (new locations count two
  units, existing one; Direct origins insert at position 0, others
  append); entry value origins projection.rs:381-425 (Direct or
  QuoteDerived on the value span, ContinuationFragment for every
  EntryValue piece of a continuation physical line); the root value maps
  Derived over the whole source.
- The Python default section is an ordinary association whose provenance
  carries the DefaultSection role; it is not expanded into every section
  (RFC 0009 §10, docs/rfcs/0009-...:355-358).

The value-path and association-location records are the semantic model's
portable input locations (RFC 0004 §8); the protocol layer externalizes
them. This module defines typed in-SDK records with the semantic-model
field shapes, as the other format families do.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import EntryMappingBuilder, ObjectBuilder, PortableValue
from consema.document.structural import FormationStatus, NodeRef, SnapshotIdentity, Span
from consema.ini.document import IniDocument
from consema.ini.errors import (
    IniDiagnostic,
    IniProjectionFailure,
    IniProjectionFailureKind,
    IniSeverity,
)
from consema.ini.kinds import IniProfile, IniQuoteStyle, IniSyntaxKind
from consema.ini.python_case import optionxform
from consema.protocol.error_registry import DiagnosticCategory


class ProjectionTarget(enum.Enum):
    """Versioned INI projection target contract (projection.rs:10-16).

    The request target names are exactly ``BestExactEntryMappingV1`` and
    ``RequireObjectV1`` (RFC 0009 §10, docs/rfcs/0009-...:375-376).
    """

    BEST_EXACT_ENTRY_MAPPING_V1 = "ini.projection.best-exact-entry-mapping@1"
    REQUIRE_OBJECT_V1 = "ini.projection.require-object@1"


class NameComparison(enum.Enum):
    """Name comparison used only by RequireObjectV1 (projection.rs:19-25)."""

    ORIGINAL_EXACT = "OriginalExact"
    PROFILE_EQUIVALENT = "ProfileEquivalent"


class CollisionPolicy(enum.Enum):
    """Explicit collision behavior for Object projection (projection.rs:27-36)."""

    REJECT = "Reject"
    FIRST = "First"
    LAST = "Last"


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """INI projection limits (projection.rs:102-124)."""

    max_source_associations: int = 2_000_000
    max_value_nodes: int = 2_000_000
    max_report_entries: int = 100_000
    max_provenance_units: int = 4_000_000


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Immutable explicit projection request (projection.rs:38-45)."""

    target: ProjectionTarget
    comparison: NameComparison
    collision_policy: CollisionPolicy
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)

    @classmethod
    def best_exact_entry_mapping(cls) -> ProjectionRequest:
        """Exact default that preserves duplicate associations
        (projection.rs:49-57)."""
        return cls(
            target=ProjectionTarget.BEST_EXACT_ENTRY_MAPPING_V1,
            comparison=NameComparison.ORIGINAL_EXACT,
            collision_policy=CollisionPolicy.REJECT,
        )

    @classmethod
    def require_object(
        cls, comparison: NameComparison, collision_policy: CollisionPolicy
    ) -> ProjectionRequest:
        """Explicit unique Object request (projection.rs:60-68)."""
        return cls(
            target=ProjectionTarget.REQUIRE_OBJECT_V1,
            comparison=comparison,
            collision_policy=collision_policy,
        )

    def with_limits(self, limits: ProjectionLimits) -> ProjectionRequest:
        """Replaces immutable resource limits (projection.rs:71-75)."""
        return ProjectionRequest(
            target=self.target,
            comparison=self.comparison,
            collision_policy=self.collision_policy,
            limits=limits,
        )


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:126-135)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"

    def lifted(self, other: Fidelity) -> Fidelity:
        """Worst-of fidelity (projection.rs:377-379)."""
        order = [Fidelity.EXACT, Fidelity.TRANSFORMED, Fidelity.LOSSY]
        return order[max(order.index(self), order.index(other))]


# -- value paths and association locations ----------------------------------


class ValuePathSegmentKind(enum.Enum):
    """Value-path segment kinds of the semantic model (RFC 0004 §8)."""

    SEQUENCE_ELEMENT = "SequenceElement"
    OBJECT_VALUE = "ObjectValue"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


@dataclass(frozen=True, slots=True)
class ValuePathSegment:
    """One value-path segment (ValuePathSegment, semantic model)."""

    kind: ValuePathSegmentKind
    key: object  # int ordinal or str name


@dataclass(frozen=True, slots=True)
class ValuePath:
    """Portable input value path (ValuePath, semantic model; RFC 0004 §8)."""

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
    """Association roles of the semantic model (AssociationRole)."""

    OBJECT_ENTRY = "ObjectEntry"
    OBJECT_KEY = "ObjectKey"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"


@dataclass(frozen=True, slots=True)
class AssociationLocation:
    """Portable association location (AssociationLocation, RFC 0004 §8)."""

    path: ValuePath
    ordinal: int
    role: AssociationRole


# -- projected locations, provenance, events ---------------------------------


class ProjectedLocationKind(enum.Enum):
    """Projected location kind (projection.rs:137-144)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """One portable projected location (projection.rs:137-144)."""

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
    """Source-to-projection relation (projection.rs:146-159)."""

    DIRECT = "Direct"
    DERIVED = "Derived"
    CONTINUATION_FRAGMENT = "ContinuationFragment"
    QUOTE_DERIVED = "QuoteDerived"
    COLLAPSED = "Collapsed"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs:161-172)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance entry (projection.rs:174-181)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable many-valued provenance mapping (projection.rs:183-195)."""

    entries: tuple[ProvenanceEntry, ...] = ()


class ProjectionEventKind(enum.Enum):
    """Collision report category (projection.rs:197-204)."""

    SECTION_COLLISION_COLLAPSED = "SectionCollisionCollapsed"
    ENTRY_COLLISION_COLLAPSED = "EntryCollisionCollapsed"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One explicit Object collision event (projection.rs:206-223)."""

    kind: ProjectionEventKind
    policy: CollisionPolicy
    comparison: NameComparison
    discarded: NodeRef
    retained: NodeRef
    projected: AssociationLocation
    impact: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs:225-237)."""

    events: tuple[ProjectionEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs:239-250)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed attempt with no value or provenance map (projection.rs:252-259)."""

    diagnostics: tuple[IniDiagnostic, ...]
    report: ProjectionReport = ProjectionReport()


ProjectionResult = CompleteProjection | FailedProjectionAttempt


class _Context:
    def __init__(
        self,
        document: IniDocument,
        request: ProjectionRequest,
    ) -> None:
        self.document = document
        self.request = request
        self.provenance_entries: list[ProvenanceEntry] = []
        self.provenance_units = 0
        self.report: list[ProjectionEvent] = []
        self.fidelity = Fidelity.EXACT

    def add_origin(
        self,
        projected: ProjectedLocation,
        node: NodeRef,
        span: Span,
        relation: ProvenanceRelation,
    ) -> None:
        """One provenance origin with deterministic unit accounting
        (projection.rs:325-370)."""
        new_location = all(entry.projected != projected for entry in self.provenance_entries)
        increment = 2 if new_location else 1
        self.provenance_units += increment
        if self.provenance_units > self.request.limits.max_provenance_units:
            raise IniProjectionFailure(
                IniProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_provenance_units",
            )
        origin = SourceOrigin(
            snapshot=self.document.snapshot_identity(),
            node=node,
            span=span,
            relation=relation,
        )
        for index, entry in enumerate(self.provenance_entries):
            if entry.projected == projected:
                origins = list(entry.origins)
                if relation is ProvenanceRelation.DIRECT:
                    origins.insert(0, origin)
                else:
                    origins.append(origin)
                self.provenance_entries[index] = ProvenanceEntry(
                    projected=projected, origins=tuple(origins)
                )
                return
        self.provenance_entries.append(
            ProvenanceEntry(projected=projected, origins=(origin,))
        )

    def push_event(self, event: ProjectionEvent) -> None:
        """One report event; fidelity lifts to Transformed
        (projection.rs:372-379)."""
        if len(self.report) >= self.request.limits.max_report_entries:
            raise IniProjectionFailure(
                IniProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_report_entries",
            )
        self.report.append(event)
        self.fidelity = self.fidelity.lifted(Fidelity.TRANSFORMED)

    def add_entry_value_origins(
        self, projected: ProjectedLocation, entry_index: int
    ) -> None:
        """Value span plus every continuation fragment origin
        (projection.rs:381-425; RFC 0009 §10, docs/rfcs/0009-...:383-385)."""
        entry = self.document.entries[entry_index]
        self.add_origin(
            projected,
            entry.node,
            entry.value_span,
            (
                ProvenanceRelation.DIRECT
                if entry.quote_style is IniQuoteStyle.NONE
                else ProvenanceRelation.QUOTE_DERIVED
            ),
        )
        logical = self.document.resolve_logical_line(entry.logical_line)
        pieces = self.document.structural_index.pieces
        kinds = self.document.syntax_kinds
        for physical_node in logical.physical_nodes[1:]:
            physical = self.document.resolve_physical_line(physical_node)
            for piece, kind in zip(pieces, kinds):
                if piece.span.start_byte >= physical.content_span.end_byte:
                    break
                if (
                    piece.span.end_byte > physical.content_span.start_byte
                    and kind is IniSyntaxKind.ENTRY_VALUE
                ):
                    self.add_origin(
                        projected,
                        entry.node,
                        piece.span,
                        ProvenanceRelation.CONTINUATION_FRAGMENT,
                    )


def project(document: IniDocument, request: ProjectionRequest) -> ProjectionResult:
    """Projects this snapshot under one explicit target and collision
    contract (projection.rs:288-314)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        return _failed(document, IniProjectionFailureKind.RECOVERED_DOCUMENT)
    source_associations = len(document.sections) + len(document.entries)
    if source_associations > request.limits.max_source_associations:
        return _failed(
            document,
            IniProjectionFailureKind.RESOURCE_LIMIT,
            resource_name="max_source_associations",
        )
    try:
        if request.target is ProjectionTarget.BEST_EXACT_ENTRY_MAPPING_V1:
            complete = _project_exact(document, request)
        else:
            complete = _project_object(document, request)
    except IniProjectionFailure as failure:
        return _failed(
            document,
            failure.kind,
            resource_name=failure.resource_name,
            container=failure.container,
            name_value=failure.name_value,
        )
    return complete


def _project_exact(
    document: IniDocument, request: ProjectionRequest
) -> CompleteProjection:
    """Exact nested EntryMapping preserving every occurrence
    (projection.rs:428-537)."""
    required_nodes = len(document.sections) * 2 + len(document.entries) * 2 + 1
    if required_nodes > request.limits.max_value_nodes:
        raise IniProjectionFailure(
            IniProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_value_nodes"
        )
    context = _Context(document, request)
    root = ValuePath.root()
    entries_by_section = _group_entries(document)
    outer = EntryMappingBuilder()
    for outer_ordinal, section in enumerate(document.sections):
        section_path = root.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, outer_ordinal))
        outer_association = AssociationLocation(
            root, outer_ordinal, AssociationRole.ENTRY_MAPPING_ENTRY
        )
        context.add_origin(
            ProjectedLocation.association_location(outer_association),
            section.node,
            section.span,
            ProvenanceRelation.DIRECT,
        )
        context.add_origin(
            ProjectedLocation.value_location(
                root.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, outer_ordinal))
            ),
            section.node,
            section.name_span,
            ProvenanceRelation.DIRECT,
        )
        context.add_origin(
            ProjectedLocation.value_location(section_path),
            section.node,
            section.span,
            ProvenanceRelation.DERIVED,
        )
        inner = EntryMappingBuilder()
        for local_ordinal, entry_index in enumerate(
            entries_by_section.get(section.node, ())
        ):
            entry = document.entries[entry_index]
            association = AssociationLocation(
                section_path, local_ordinal, AssociationRole.ENTRY_MAPPING_ENTRY
            )
            context.add_origin(
                ProjectedLocation.association_location(association),
                entry.node,
                entry.span,
                ProvenanceRelation.DIRECT,
            )
            context.add_origin(
                ProjectedLocation.value_location(
                    section_path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, local_ordinal))
                ),
                entry.node,
                entry.key_span,
                ProvenanceRelation.DIRECT,
            )
            value_path = section_path.child(
                ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, local_ordinal)
            )
            context.add_entry_value_origins(ProjectedLocation.value_location(value_path), entry_index)
            inner.push(PortableValue.string(entry.key), PortableValue.string(entry.value))
        outer.push(PortableValue.string(section.name), inner.build())
    root_span = document.authority.span(0, document.source.len())
    context.add_origin(
        ProjectedLocation.value_location(root),
        document.node_ref(),
        root_span,
        ProvenanceRelation.DERIVED,
    )
    return CompleteProjection(
        value=outer.build(),
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance_entries)),
    )


@dataclass(frozen=True, slots=True)
class _SelectedSection:
    """One retained section with its retained entry ordinals."""

    source_index: int
    all_entry_indices: tuple[int, ...]
    entry_indices: tuple[int, ...]


def _project_object(
    document: IniDocument, request: ProjectionRequest
) -> CompleteProjection:
    """Explicit unique Object projection with collision policy
    (projection.rs:546-785)."""
    section_names = [
        _comparison_name(document.profile, section.name, request.comparison, False)
        for section in document.sections
    ]
    retained_sections = _select_indices(
        section_names, request.collision_policy, document.node_ref()
    )
    entries_by_section = _group_entries(document)
    selected: list[_SelectedSection] = []
    for section_index in retained_sections:
        section = document.sections[section_index]
        entry_indices = tuple(entries_by_section.get(section.node, ()))
        entry_names = [
            _comparison_name(
                document.profile,
                document.entries[index].key,
                request.comparison,
                True,
            )
            for index in entry_indices
        ]
        retained_local = _select_indices(entry_names, request.collision_policy, section.node)
        selected.append(
            _SelectedSection(
                source_index=section_index,
                all_entry_indices=entry_indices,
                entry_indices=tuple(entry_indices[index] for index in retained_local),
            )
        )
    retained_entries = sum(len(section.entry_indices) for section in selected)
    required_nodes = retained_entries + len(selected) + 1
    if required_nodes > request.limits.max_value_nodes:
        raise IniProjectionFailure(
            IniProjectionFailureKind.RESOURCE_LIMIT, resource_name="max_value_nodes"
        )
    context = _Context(document, request)
    root = ValuePath.root()
    retained_section_by_name = {
        section_names[section.source_index]: section.source_index for section in selected
    }
    projected_section_ordinal = {
        section.source_index: ordinal for ordinal, section in enumerate(selected)
    }
    for source_index, section in enumerate(document.sections):
        retained = retained_section_by_name[section_names[source_index]]
        if retained != source_index:
            projected_ordinal = projected_section_ordinal[retained]
            location = AssociationLocation(root, projected_ordinal, AssociationRole.OBJECT_ENTRY)
            context.push_event(
                ProjectionEvent(
                    kind=ProjectionEventKind.SECTION_COLLISION_COLLAPSED,
                    policy=request.collision_policy,
                    comparison=request.comparison,
                    discarded=section.node,
                    retained=document.sections[retained].node,
                    projected=location,
                    impact=Fidelity.TRANSFORMED,
                )
            )
            context.add_origin(
                ProjectedLocation.association_location(location),
                section.node,
                section.span,
                ProvenanceRelation.COLLAPSED,
            )
    outer = ObjectBuilder()
    for projected_section_ordinal_value, selected_section in enumerate(selected):
        section = document.sections[selected_section.source_index]
        section_path = root.child(
            ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, section.name)
        )
        outer_location = AssociationLocation(
            root, projected_section_ordinal_value, AssociationRole.OBJECT_ENTRY
        )
        context.add_origin(
            ProjectedLocation.association_location(outer_location),
            section.node,
            section.span,
            ProvenanceRelation.DIRECT,
        )
        context.add_origin(
            ProjectedLocation.association_location(
                AssociationLocation(root, projected_section_ordinal_value, AssociationRole.OBJECT_KEY)
            ),
            section.node,
            section.name_span,
            ProvenanceRelation.DIRECT,
        )
        context.add_origin(
            ProjectedLocation.value_location(section_path),
            section.node,
            section.span,
            ProvenanceRelation.DERIVED,
        )
        retained_entry_set = set(selected_section.entry_indices)
        retained_by_name = {
            _comparison_name(
                document.profile,
                document.entries[index].key,
                request.comparison,
                True,
            ): index
            for index in selected_section.entry_indices
        }
        projected_entry_ordinal = {
            source: ordinal
            for ordinal, source in enumerate(selected_section.entry_indices)
        }
        for entry_index in selected_section.all_entry_indices:
            if entry_index in retained_entry_set:
                continue
            entry = document.entries[entry_index]
            name = _comparison_name(
                document.profile, entry.key, request.comparison, True
            )
            retained = retained_by_name[name]
            projected_ordinal = projected_entry_ordinal[retained]
            location = AssociationLocation(
                section_path, projected_ordinal, AssociationRole.OBJECT_ENTRY
            )
            context.push_event(
                ProjectionEvent(
                    kind=ProjectionEventKind.ENTRY_COLLISION_COLLAPSED,
                    policy=request.collision_policy,
                    comparison=request.comparison,
                    discarded=entry.node,
                    retained=document.entries[retained].node,
                    projected=location,
                    impact=Fidelity.TRANSFORMED,
                )
            )
            context.add_origin(
                ProjectedLocation.association_location(location),
                entry.node,
                entry.span,
                ProvenanceRelation.COLLAPSED,
            )
        inner = ObjectBuilder()
        for projected_entry_ordinal_value, entry_index in enumerate(
            selected_section.entry_indices
        ):
            entry = document.entries[entry_index]
            context.add_origin(
                ProjectedLocation.association_location(
                    AssociationLocation(
                        section_path, projected_entry_ordinal_value, AssociationRole.OBJECT_ENTRY
                    )
                ),
                entry.node,
                entry.span,
                ProvenanceRelation.DIRECT,
            )
            context.add_origin(
                ProjectedLocation.association_location(
                    AssociationLocation(
                        section_path, projected_entry_ordinal_value, AssociationRole.OBJECT_KEY
                    )
                ),
                entry.node,
                entry.key_span,
                ProvenanceRelation.DIRECT,
            )
            context.add_entry_value_origins(
                ProjectedLocation.value_location(
                    section_path.child(
                        ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, entry.key)
                    )
                ),
                entry_index,
            )
            inner.insert(entry.key, PortableValue.string(entry.value))
        outer.insert(section.name, inner.build())
    root_span = document.authority.span(0, document.source.len())
    context.add_origin(
        ProjectedLocation.value_location(root),
        document.node_ref(),
        root_span,
        ProvenanceRelation.DERIVED,
    )
    return CompleteProjection(
        value=outer.build(),
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance_entries)),
    )


def _select_indices(
    names: list[str], policy: CollisionPolicy, container: NodeRef
) -> list[int]:
    """Explicit collision selection (projection.rs:787-821)."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    if policy is CollisionPolicy.REJECT:
        for name in names:
            if counts[name] > 1:
                raise IniProjectionFailure(
                    IniProjectionFailureKind.COLLISION,
                    container=container,
                    name_value=name,
                )
    if policy in (CollisionPolicy.REJECT, CollisionPolicy.FIRST):
        seen = set()
        return [
            index
            for index in range(len(names))
            if names[index] not in seen and not seen.add(names[index])
        ]
    seen = set()
    retained = [
        index
        for index in range(len(names) - 1, -1, -1)
        if names[index] not in seen and not seen.add(names[index])
    ]
    retained.reverse()
    return retained


def _group_entries(document: IniDocument) -> dict[NodeRef, tuple[int, ...]]:
    """Entry ordinals grouped by owning section node (projection.rs:823-829)."""
    groups: dict[NodeRef, list[int]] = {}
    for index, entry in enumerate(document.entries):
        groups.setdefault(entry.section, []).append(index)
    return {node: tuple(indices) for node, indices in groups.items()}


def _comparison_name(
    profile: IniProfile, value: str, comparison: NameComparison, is_key: bool
) -> str:
    """Profile comparison name under the request mode (projection.rs:831-846)."""
    if comparison is NameComparison.ORIGINAL_EXACT:
        return value
    if profile is IniProfile.WINDOWS_V1:
        return value.lower()
    if profile is IniProfile.PYTHON_CONFIGPARSER_V1 and is_key:
        return optionxform(value)
    return value


def _failed(
    document: IniDocument,
    kind: IniProjectionFailureKind,
    *,
    resource_name: str | None = None,
    container: NodeRef | None = None,
    name_value: str | None = None,
) -> FailedProjectionAttempt:
    """Failed attempt with one stable diagnostic (projection.rs:852-884)."""
    failure = IniProjectionFailure(
        kind,
        container=container,
        name_value=name_value,
        resource_name=resource_name,
    )
    arguments: dict[str, str] = {"reason": failure.reason}
    if failure.resource_name is not None:
        arguments["limit"] = failure.resource_name
    profile = document.profile_id()
    arguments["profile"] = f"{profile.id}@{profile.version}"
    return FailedProjectionAttempt(
        diagnostics=(
            IniDiagnostic(
                code=failure.code,
                category=DiagnosticCategory.PROJECTION,
                severity=IniSeverity.ERROR,
                primary=None,
                arguments=arguments,
            ),
        ),
        report=ProjectionReport(),
    )
