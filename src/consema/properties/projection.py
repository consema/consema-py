"""Java Properties projection: exact EntryMapping and explicit Object
targets with duplicate policy and provenance.

Authority (Rust arbitration for exact semantics):

- Targets, policies, and request building: crates/consema-properties/src/
  projection.rs:9-82 — BestExactEntryMappingV1 (the default,
  ``java-properties.projection.best-exact-entry-mapping@1``, RFC 0010
  section 11, docs/rfcs/0010-...:312-323) and RequireObjectV1
  (``java-properties.projection.require-object@1``, published by the CLI
  wire mapping crates/consema/src/bin/consema/project_cmd.rs:158) under
  the explicit DuplicatePolicy RequireUnique | FirstWins |
  LastWinsJdkTable (RFC 0010 section 11, docs/rfcs/0010-...:324-339).
- Limits: projection.rs:84-106 (max_source_associations 2_000_000,
  max_value_nodes 4_000_001, max_report_entries 100_000,
  max_provenance_units 8_000_000).
- Gates and failure algebra: projection.rs:264-306 — Recovered documents
  fail with java-properties.projection.incomplete-document@1
  (projection.rs:743); unpaired surrogates fail atomically with
  java-properties.projection.unpaired-surrogate@1 (:745) and no partial
  mapping (RFC 0010 section 11, lines 316-323); the failure code mapping
  projection.rs:741-752.
- Duplicate collapse: projection.rs:499-611 — RequireUnique rejects with
  core.projection.target-not-applicable@1 (:748); First/Last collapse is
  explicitly authorized Lossy, emits one java-properties.projection.
  duplicate-collapsed@1 event per discarded association
  (projection.rs:548-555), and records both retained and discarded
  origins (ProvenanceRelation::Collapsed, projection.rs:556-562); the
  authorizing rules are exactly
  java-properties.duplicate-key.first-wins@1 /
  java-properties.duplicate-key.last-wins-jdk-table@1 (RFC 0010 section
  11, docs/rfcs/0010-...:341-344).
- Provenance: projection.rs:308-428 — Direct association origins,
  KeyFragment/ValueFragment raw spans, EscapeDerived escape spellings,
  Derived root origin, and the unit accounting (2 units for a new
  location, 1 for an existing one; projection.rs:318-362).
- Fidelity: projection.rs:108-117 (Exact/Transformed/Lossy); collapse
  lifts fidelity to Lossy (projection.rs:410).

The value-path and association-location records are the semantic model's
portable input locations (RFC 0004 section 8). Golden transcription
targets: conformance/vectors/java-properties-v1.json cases
projection.* (lines 76-89) and resource.projection-limit-matrix
(lines 142-145).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace

from consema.core.value import EntryMappingBuilder, ObjectBuilder, PortableValue
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    SnapshotIdentity,
    Span,
)
from consema.properties.document import PropertiesDocument
from consema.properties.errors import (
    PropertiesDiagnostic,
    PropertiesProjectionFailure,
    PropertiesProjectionFailureKind,
    PropertiesSeverity,
)
from consema.properties.java_string import JavaStringStatus
from consema.protocol.error_registry import DiagnosticCategory


class ProjectionTarget(enum.Enum):
    """Versioned Java Properties projection target (projection.rs:9-16).

    The default target is the source-ordered EntryMapping preserving every
    association (RFC 0010 section 11); RequireObjectV1 is the unique-key
    Object under one explicit duplicate policy.
    """

    BEST_EXACT_ENTRY_MAPPING_V1 = "java-properties.projection.best-exact-entry-mapping@1"
    REQUIRE_OBJECT_V1 = "java-properties.projection.require-object@1"


class DuplicatePolicy(enum.Enum):
    """Explicit duplicate behavior for ``RequireObjectV1``
    (projection.rs:18-27; RFC 0010 section 11)."""

    REQUIRE_UNIQUE = "RequireUnique"
    FIRST_WINS = "FirstWins"
    LAST_WINS_JDK_TABLE = "LastWinsJdkTable"

    @property
    def authorizing_rule(self) -> str | None:
        """The authorizing rule id externalized in conversion reports
        (RFC 0010 section 11, docs/rfcs/0010-...:341-344)."""
        return {
            DuplicatePolicy.FIRST_WINS: "java-properties.duplicate-key.first-wins@1",
            DuplicatePolicy.LAST_WINS_JDK_TABLE: (
                "java-properties.duplicate-key.last-wins-jdk-table@1"
            ),
        }.get(self)


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """Java Properties projection limits (projection.rs:84-106)."""

    max_source_associations: int = 2_000_000
    max_value_nodes: int = 4_000_001
    max_report_entries: int = 100_000
    max_provenance_units: int = 8_000_000


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Immutable explicit Properties projection request (projection.rs:29-82)."""

    target: ProjectionTarget
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.REQUIRE_UNIQUE
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)

    @classmethod
    def best_exact_entry_mapping(cls) -> ProjectionRequest:
        """Exact default that preserves every property occurrence
        (projection.rs:40-46)."""
        return cls(target=ProjectionTarget.BEST_EXACT_ENTRY_MAPPING_V1)

    @classmethod
    def require_object(cls, duplicate_policy: DuplicatePolicy) -> ProjectionRequest:
        """Explicit unique Object request (projection.rs:49-56)."""
        return cls(
            target=ProjectionTarget.REQUIRE_OBJECT_V1,
            duplicate_policy=duplicate_policy,
        )

    def with_limits(self, limits: ProjectionLimits) -> ProjectionRequest:
        """Replaces immutable resource limits (projection.rs:59-62)."""
        return replace(self, limits=limits)


# -- value paths and association locations ----------------------------------


class ValuePathSegmentKind(enum.Enum):
    """Value-path segment kinds of the semantic model (RFC 0004 section 8)."""

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
    """Portable input value path (ValuePath, semantic model; RFC 0004
    section 8)."""

    segments: tuple[ValuePathSegment, ...] = ()

    @classmethod
    def root(cls) -> ValuePath:
        return cls()

    def child(self, segment: ValuePathSegment) -> ValuePath:
        return ValuePath(self.segments + (segment,))

    def __repr__(self) -> str:
        if not self.segments:
            return "Root"
        return "Root/" + "/".join(
            f"{segment.kind.value}({segment.key})" for segment in self.segments
        )


class AssociationRole(enum.Enum):
    """Association roles of the semantic model (AssociationRole)."""

    OBJECT_ENTRY = "ObjectEntry"
    OBJECT_KEY = "ObjectKey"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"


@dataclass(frozen=True, slots=True)
class AssociationLocation:
    """Portable association location (AssociationLocation, RFC 0004
    section 8)."""

    path: ValuePath
    ordinal: int
    role: AssociationRole


# -- fidelity, events, provenance --------------------------------------------


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:108-117)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProjectedLocationKind(enum.Enum):
    """Projected location kind (projection.rs:119-126)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """One portable projected location (projection.rs:119-126)."""

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
    """Source-to-projection relation (projection.rs:128-143)."""

    DIRECT = "Direct"
    DERIVED = "Derived"
    KEY_FRAGMENT = "KeyFragment"
    VALUE_FRAGMENT = "ValueFragment"
    ESCAPE_DERIVED = "EscapeDerived"
    COLLAPSED = "Collapsed"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs:145-156)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance entry (projection.rs:158-165)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable many-valued provenance mapping (projection.rs:167-179)."""

    entries: tuple[ProvenanceEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One explicit duplicate-collapse event (projection.rs:181-196)."""

    code: str
    policy: DuplicatePolicy
    discarded: NodeRef
    retained: NodeRef
    projected: AssociationLocation
    impact: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs:198-210)."""

    events: tuple[ProjectionEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs:212-223)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed projection attempt without a partial value (projection.rs:225-232)."""

    diagnostics: tuple[PropertiesDiagnostic, ...]
    report: ProjectionReport


ProjectionResult = CompleteProjection | FailedProjectionAttempt


class _StringComponent(enum.Enum):
    KEY = "key"
    VALUE = "value"


class _ProjectionContext:
    """One projection execution (projection.rs:308-428)."""

    def __init__(
        self,
        document: PropertiesDocument,
        request: ProjectionRequest,
    ) -> None:
        self.document = document
        self.request = request
        self.provenance: list[ProvenanceEntry] = []
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
        """One origin under the provenance-unit budget (projection.rs:318-362)."""
        new_location = all(entry.projected != projected for entry in self.provenance)
        increment = 2 if new_location else 1
        self.provenance_units += increment
        if self.provenance_units > self.request.limits.max_provenance_units:
            raise PropertiesProjectionFailure(
                PropertiesProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_provenance_units",
            )
        origin = SourceOrigin(
            snapshot=self.document.snapshot_identity(),
            node=node,
            span=span,
            relation=relation,
        )
        for index, entry in enumerate(self.provenance):
            if entry.projected == projected:
                if relation is ProvenanceRelation.DIRECT:
                    origins = (origin,) + entry.origins
                else:
                    origins = entry.origins + (origin,)
                self.provenance[index] = ProvenanceEntry(
                    projected=projected, origins=origins
                )
                return
        self.provenance.append(
            ProvenanceEntry(projected=projected, origins=(origin,))
        )

    def add_string_origins(
        self,
        projected: ProjectedLocation,
        property_index: int,
        component: _StringComponent,
    ) -> None:
        """Fragmented key/value origins plus escape spellings
        (projection.rs:364-404)."""
        property = self.document.properties[property_index]
        if component is _StringComponent.KEY:
            fragments, relation = property.key_fragments, ProvenanceRelation.KEY_FRAGMENT
        else:
            fragments, relation = property.value_fragments, ProvenanceRelation.VALUE_FRAGMENT
        if not fragments:
            anchor = (
                property.key_anchor
                if component is _StringComponent.KEY
                else property.value_anchor
            )
            self.add_origin(projected, property.node, anchor, relation)
        else:
            for span in fragments:
                self.add_origin(projected, property.node, span, relation)
        for escape_node in property.escapes:
            escape = self.document.escape(escape_node)
            if escape.in_key == (component is _StringComponent.KEY):
                self.add_origin(
                    projected,
                    escape.node,
                    escape.span,
                    ProvenanceRelation.ESCAPE_DERIVED,
                )

    def push_event(self, event: ProjectionEvent) -> None:
        """One collapse event under the report budget (projection.rs:406-413)."""
        if len(self.report) >= self.request.limits.max_report_entries:
            raise PropertiesProjectionFailure(
                PropertiesProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="max_report_entries",
            )
        self.fidelity = _max_fidelity(self.fidelity, event.impact)
        self.report.append(event)

    def add_root_origin(self) -> None:
        """Derived root value origin over the complete document
        (projection.rs:415-428)."""
        root_span = self.document.authority.span(0, len(self.document.render()))
        self.add_origin(
            ProjectedLocation.value_location(ValuePath.root()),
            self.document.node_ref(),
            root_span,
            ProvenanceRelation.DERIVED,
        )


def project(document: PropertiesDocument, request: ProjectionRequest) -> ProjectionResult:
    """Projects one immutable snapshot under one explicit target and
    duplicate contract (projection.rs:264-306).

    A failure returns no value, no partial mapping, and no provenance that
    can be mistaken for a result (RFC 0010 section 11).
    """
    if document.formation_status() is not FormationStatus.COMPLETE:
        return _failed(document, PropertiesProjectionFailureKind.RECOVERED_DOCUMENT)
    if len(document.properties) > request.limits.max_source_associations:
        return _failed(
            document,
            PropertiesProjectionFailureKind.RESOURCE_LIMIT,
            resource_name="max_source_associations",
        )
    for property in document.properties:
        if property.key.status() is JavaStringStatus.UNPAIRED_SURROGATE:
            return _failed(
                document,
                PropertiesProjectionFailureKind.UNPAIRED_SURROGATE,
                property_node=property.node,
                component=_StringComponent.KEY.value,
            )
        if property.value.status() is JavaStringStatus.UNPAIRED_SURROGATE:
            return _failed(
                document,
                PropertiesProjectionFailureKind.UNPAIRED_SURROGATE,
                property_node=property.node,
                component=_StringComponent.VALUE.value,
            )
    try:
        if request.target is ProjectionTarget.BEST_EXACT_ENTRY_MAPPING_V1:
            return _project_exact(document, request)
        return _project_object(document, request)
    except PropertiesProjectionFailure as failure:
        return _failed_from_exception(document, failure)


def _project_exact(
    document: PropertiesDocument, request: ProjectionRequest
) -> CompleteProjection:
    """Source-ordered EntryMapping preserving every association
    (projection.rs:430-497)."""
    required_nodes = len(document.properties) * 2 + 1
    if required_nodes > request.limits.max_value_nodes:
        raise PropertiesProjectionFailure(
            PropertiesProjectionFailureKind.RESOURCE_LIMIT,
            resource_name="max_value_nodes",
        )
    context = _ProjectionContext(document, request)
    root = ValuePath.root()
    mapping = EntryMappingBuilder()
    for ordinal, property in enumerate(document.properties):
        association = AssociationLocation(
            root, ordinal, AssociationRole.ENTRY_MAPPING_ENTRY
        )
        context.add_origin(
            ProjectedLocation.association_location(association),
            property.node,
            property.span,
            ProvenanceRelation.DIRECT,
        )
        context.add_string_origins(
            ProjectedLocation.value_location(
                root.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, ordinal))
            ),
            ordinal,
            _StringComponent.KEY,
        )
        context.add_string_origins(
            ProjectedLocation.value_location(
                root.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, ordinal))
            ),
            ordinal,
            _StringComponent.VALUE,
        )
        mapping.push(
            PortableValue.string(property.key.to_unicode()),
            PortableValue.string(property.value.to_unicode()),
        )
    context.add_root_origin()
    return CompleteProjection(
        value=mapping.build(),
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


def _project_object(
    document: PropertiesDocument, request: ProjectionRequest
) -> CompleteProjection:
    """Unique-key Object under one explicit duplicate policy
    (projection.rs:499-611)."""
    keys = [
        property.key.to_unicode()
        for property in document.properties
    ]
    retained = _select_indices(document, keys, request.duplicate_policy)
    required_nodes = len(retained) + 1
    if required_nodes > request.limits.max_value_nodes:
        raise PropertiesProjectionFailure(
            PropertiesProjectionFailureKind.RESOURCE_LIMIT,
            resource_name="max_value_nodes",
        )
    context = _ProjectionContext(document, request)
    root = ValuePath.root()
    retained_set = set(retained)
    retained_by_key = {keys[index]: index for index in retained}
    projected_ordinal = {
        source: ordinal for ordinal, source in enumerate(retained)
    }
    for source_index, property in enumerate(document.properties):
        if source_index in retained_set:
            continue
        retained_index = retained_by_key[keys[source_index]]
        ordinal = projected_ordinal[retained_index]
        location = AssociationLocation(root, ordinal, AssociationRole.OBJECT_ENTRY)
        context.push_event(
            ProjectionEvent(
                code="java-properties.projection.duplicate-collapsed@1",
                policy=request.duplicate_policy,
                discarded=property.node,
                retained=document.properties[retained_index].node,
                projected=location,
                impact=Fidelity.LOSSY,
            )
        )
        context.add_origin(
            ProjectedLocation.association_location(location),
            property.node,
            property.span,
            ProvenanceRelation.COLLAPSED,
        )

    object_builder = ObjectBuilder()
    for ordinal, property_index in enumerate(retained):
        property = document.properties[property_index]
        association = AssociationLocation(root, ordinal, AssociationRole.OBJECT_ENTRY)
        context.add_origin(
            ProjectedLocation.association_location(association),
            property.node,
            property.span,
            ProvenanceRelation.DIRECT,
        )
        context.add_string_origins(
            ProjectedLocation.association_location(
                AssociationLocation(root, ordinal, AssociationRole.OBJECT_KEY)
            ),
            property_index,
            _StringComponent.KEY,
        )
        context.add_string_origins(
            ProjectedLocation.value_location(
                root.child(
                    ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, keys[property_index])
                )
            ),
            property_index,
            _StringComponent.VALUE,
        )
        object_builder.insert(
            keys[property_index],
            PortableValue.string(property.value.to_unicode()),
        )
    context.add_root_origin()
    return CompleteProjection(
        value=object_builder.build(),
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


def _select_indices(
    document: PropertiesDocument, keys: list[str], policy: DuplicatePolicy
) -> list[int]:
    """Explicit duplicate retention (projection.rs:613-648)."""
    first_by_key: dict[str, int] = {}
    for index, key in enumerate(keys):
        if key in first_by_key:
            if policy is DuplicatePolicy.REQUIRE_UNIQUE:
                raise PropertiesProjectionFailure(
                    PropertiesProjectionFailureKind.DUPLICATE_KEY,
                    retained=document.properties[first_by_key[key]].node,
                    duplicate=document.properties[index].node,
                )
        else:
            first_by_key[key] = index
    if policy in (DuplicatePolicy.REQUIRE_UNIQUE, DuplicatePolicy.FIRST_WINS):
        seen = set()
        return [
            index
            for index in range(len(keys))
            if keys[index] not in seen and not seen.add(keys[index])
        ]
    seen = set()
    retained = [
        index
        for index in range(len(keys) - 1, -1, -1)
        if keys[index] not in seen and not seen.add(keys[index])
    ]
    retained.reverse()
    return retained


def _failed(
    document: PropertiesDocument,
    kind: PropertiesProjectionFailureKind,
    *,
    property_node: NodeRef | None = None,
    component: str | None = None,
    retained: NodeRef | None = None,
    duplicate: NodeRef | None = None,
    resource_name: str | None = None,
) -> FailedProjectionAttempt:
    """One stable failed attempt with a deterministic diagnostic
    (projection.rs:654-711)."""
    failure = PropertiesProjectionFailure(
        kind,
        property_node=property_node,
        component=component,
        retained=retained,
        duplicate=duplicate,
        resource_name=resource_name,
    )
    arguments: dict[str, str] = {
        "reason": {
            PropertiesProjectionFailureKind.RECOVERED_DOCUMENT: "incomplete-document",
            PropertiesProjectionFailureKind.UNPAIRED_SURROGATE: "unpaired-surrogate",
            PropertiesProjectionFailureKind.DUPLICATE_KEY: "duplicate-key",
            PropertiesProjectionFailureKind.RESOURCE_LIMIT: "resource-limit",
            PropertiesProjectionFailureKind.CORE_INVARIANT: "target-not-applicable",
        }[kind]
    }
    if kind is PropertiesProjectionFailureKind.UNPAIRED_SURROGATE:
        arguments["component"] = component or "value"
        _insert_property_ordinal(document, arguments, "property_ordinal", property_node)
    elif kind is PropertiesProjectionFailureKind.DUPLICATE_KEY:
        _insert_property_ordinal(document, arguments, "retained_ordinal", retained)
        _insert_property_ordinal(document, arguments, "duplicate_ordinal", duplicate)
    elif kind is PropertiesProjectionFailureKind.RESOURCE_LIMIT:
        arguments["limit"] = resource_name or ""
    profile = document.profile_id()
    arguments["profile"] = f"{profile.id}@{profile.version}"
    primary = _failure_span(document, kind, property_node, duplicate)
    return FailedProjectionAttempt(
        diagnostics=(
            PropertiesDiagnostic(
                code=failure.code,
                category=DiagnosticCategory.PROJECTION,
                severity=PropertiesSeverity.ERROR,
                primary=primary,
                arguments=arguments,
            ),
        ),
        report=ProjectionReport(),
    )


def _failed_from_exception(
    document: PropertiesDocument, failure: PropertiesProjectionFailure
) -> FailedProjectionAttempt:
    return _failed(
        document,
        failure.kind,
        property_node=failure.property_node,
        component=failure.component,
        retained=failure.retained,
        duplicate=failure.duplicate,
        resource_name=failure.resource_name,
    )


def _insert_property_ordinal(
    document: PropertiesDocument,
    arguments: dict[str, str],
    argument: str,
    node: NodeRef | None,
) -> None:
    if node is None:
        return
    for ordinal, property in enumerate(document.properties):
        if property.node == node:
            arguments[argument] = str(ordinal)
            return


def _failure_span(
    document: PropertiesDocument,
    kind: PropertiesProjectionFailureKind,
    property_node: NodeRef | None,
    duplicate: NodeRef | None,
) -> Span | None:
    """Primary failure span (projection.rs:730-739): the unpaired or
    duplicated property's complete source range."""
    target = property_node if kind is PropertiesProjectionFailureKind.UNPAIRED_SURROGATE else duplicate
    if target is None:
        return None
    for property in document.properties:
        if property.node == target:
            return property.span
    return None


def _max_fidelity(left: Fidelity, right: Fidelity) -> Fidelity:
    order = [Fidelity.EXACT, Fidelity.TRANSFORMED, Fidelity.LOSSY]
    return order[max(order.index(left), order.index(right))]
