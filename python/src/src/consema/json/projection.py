"""JSON/JSONC/JSON5 projection: exact-first core selection.

Authority (Rust arbitration for exact semantics):

- Targets, policies, and request building: crates/consema-json/src/
  projection.rs:13-144 — ProjectAsObjectV1 / ProjectAsEntryMappingV1 /
  BestExactCoreV1 / Json5BestExactCoreV1 (projection.rs:15-24),
  DuplicateKeyPolicy Reject/FirstWins/LastWins (projection.rs:28-35),
  the builder's global-Reject default (projection.rs:84-94) and the
  equal-precedence conflict rule (projection.rs:130-137).
- Limits: projection.rs:146-168 (max_value_nodes 1_000_000,
  max_report_entries 100_000, max_provenance_entries 2_000_000,
  max_depth 256).
- Gates and failure algebra: projection.rs:357-429 — Recovered documents
  fail with json.projection.incomplete-document@1 (projection.rs:361-366,
  756), the JSON5/profile target binding (projection.rs:367-376),
  ProjectAs* root-object requirement (projection.rs:400-410); failure code
  mapping projection.rs:754-765; failed attempts never contain a partial
  value (RFC 0004 §7, docs/rfcs/0004-...:170-191).
- Value mapping: projection.rs:443-499 (null/bool/integer/decimal/
  BinaryFloat64/string/array; unavailable regions fail semantic-
  unavailable).
- Object mapping and duplicate handling: projection.rs:501-639 —
  EntryMapping under ProjectAsEntryMapping or duplicate names under
  BestExact (projection.rs:524-534) with the StructureReencoded event
  (projection.rs:536-548), key/value association origins
  (projection.rs:550-571), select_members policy retention
  (projection.rs:691-728) with DuplicateCollapsed events (projection.rs:
  585-609).
- Provenance: projection.rs:183-241 and add_origin projection.rs:659-678
  (every projected value and supported association maps Direct to its
  source node/span; limit projection.rs:665-667).
- Fidelity: projection.rs:170-181 (Exact/Transformed/Lossy); whole-project
  fidelity lifted on re-encoding (projection.rs:537) or collapse
  (projection.rs:576-578).
- Target contract ids: json.projection.best-exact-core@1 and
  json5.projection.best-exact-core@1 are the frozen target spellings
  (RFC 0005 §8, docs/rfcs/0005-...:174-193); the v1 vectors reference the
  target by the alias "BestExactCore@1" (conformance/vectors/v1.json:91).

Design: the value-path and association-location records are the semantic
model's portable input locations (RFC 0004 §8); the protocol layer
(consema.protocol, owned by another agent) externalizes them. This module
defines typed in-SDK records with the semantic-model field shapes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.value import ObjectBuilder, PortableValue
from consema.document.structural import (
    FormationStatus,
    NodeRef,
    SnapshotIdentity,
    Span,
)
from consema.json.document import JsonDocument, JsonObjectMember, JsonValue
from consema.json.errors import (
    JsonDiagnostic,
    JsonProjectionFailure,
    JsonProjectionFailureKind,
    JsonSeverity,
)
from consema.json.parser import InternalKind
from consema.json.kinds import JsonProfile, JsonValueKind
from consema.protocol.error_registry import DiagnosticCategory


class ProjectionTarget(enum.Enum):
    """Versioned projection target contract (projection.rs:15-24)."""

    PROJECT_AS_OBJECT_V1 = "json.projection.project-as-object@1"
    PROJECT_AS_ENTRY_MAPPING_V1 = "json.projection.project-as-entry-mapping@1"
    BEST_EXACT_CORE_V1 = "json.projection.best-exact-core@1"
    JSON5_BEST_EXACT_CORE_V1 = "json5.projection.best-exact-core@1"


class DuplicateKeyPolicy(enum.Enum):
    """Explicit duplicate member policy (projection.rs:28-35)."""

    REJECT = "Reject"
    FIRST_WINS = "FirstWins"
    LAST_WINS = "LastWins"


class ProjectionPolicyScope:
    """Scope supported by v1 projection policy rules (projection.rs:38-44)."""

    def __init__(self, node: NodeRef | None) -> None:
        self.node = node

    @classmethod
    def global_scope(cls) -> ProjectionPolicyScope:
        return cls(None)

    @classmethod
    def exact_node(cls, node: NodeRef) -> ProjectionPolicyScope:
        return cls(node)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProjectionPolicyScope):
            return NotImplemented
        return self.node == other.node

    def __hash__(self) -> int:
        return hash(self.node)


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """Projection resource limits (projection.rs:146-168)."""

    max_value_nodes: int = 1_000_000
    max_report_entries: int = 100_000
    max_provenance_entries: int = 2_000_000
    max_depth: int = 256


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Immutable versioned projection request (projection.rs:52-72)."""

    target: ProjectionTarget
    duplicate_rules: tuple[tuple[ProjectionPolicyScope, DuplicateKeyPolicy], ...] = ()
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)


class ProjectionRequestBuilder:
    """Builder that rejects conflicting equal-precedence rules
    (projection.rs:74-144)."""

    def __init__(self, target: ProjectionTarget) -> None:
        self._target = target
        self._rules: list[tuple[ProjectionPolicyScope, DuplicateKeyPolicy]] = [
            (ProjectionPolicyScope.global_scope(), DuplicateKeyPolicy.REJECT)
        ]
        self._limits = ProjectionLimits()

    def global_duplicate_policy(self, policy: DuplicateKeyPolicy) -> ProjectionRequestBuilder:
        self._rules = [
            (scope, existing)
            for scope, existing in self._rules
            if scope.node is not None
        ]
        self._rules.append((ProjectionPolicyScope.global_scope(), policy))
        return self

    def exact_node_duplicate_policy(
        self, node: NodeRef, policy: DuplicateKeyPolicy
    ) -> ProjectionRequestBuilder:
        self._rules.append((ProjectionPolicyScope.exact_node(node), policy))
        return self

    def limits(self, limits: ProjectionLimits) -> ProjectionRequestBuilder:
        self._limits = limits
        return self

    def build(self) -> ProjectionRequest:
        """Validates rule precedence and completes the request
        (projection.rs:130-137)."""
        for index, (scope, policy) in enumerate(self._rules):
            for other_scope, other_policy in self._rules[index + 1 :]:
                if scope == other_scope and policy is not other_policy:
                    raise JsonProjectionFailure(
                        JsonProjectionFailureKind.CONFLICTING_POLICY_RULES
                    )
        return ProjectionRequest(
            target=self._target,
            duplicate_rules=tuple(self._rules),
            limits=self._limits,
        )


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


# -- fidelity, events, provenance --------------------------------------------


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:170-181)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProjectedLocationKind(enum.Enum):
    """Projected location kind (projection.rs:183-190)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """One portable projected location (projection.rs:183-190)."""

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
    """Relationship from portable fact to source fact (projection.rs:192-205)."""

    DIRECT = "Direct"
    REENCODED = "Reencoded"
    GENERATED = "Generated"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One source origin (projection.rs:207-217)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One projected location mapped to its source origins (projection.rs:220-227)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Complete projection provenance map (projection.rs:229-241)."""

    entries: tuple[ProvenanceEntry, ...] = ()


class ProjectionEventKind(enum.Enum):
    """Stable projection event kinds (projection.rs:243-258)."""

    STRUCTURE_REENCODED = "StructureReencoded"
    DUPLICATE_COLLAPSED = "DuplicateCollapsed"


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    """One reportable projection event (projection.rs:260-276)."""

    kind: ProjectionEventKind
    policy: DuplicateKeyPolicy | None
    source: NodeRef
    projected: ProjectedLocation | None
    old_category: str
    new_category: str
    reversible: bool
    loss: Fidelity


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report (projection.rs:281-289)."""

    events: tuple[ProjectionEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection (projection.rs:295-307)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport
    provenance: ProvenanceMap


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed attempt with no value (projection.rs:308-318)."""

    diagnostics: tuple[JsonDiagnostic, ...]
    report: ProjectionReport
    partial_analysis: tuple[str, ...] = ()


ProjectionResult = CompleteProjection | FailedProjectionAttempt


class _ProjectionContext:
    def __init__(
        self,
        document: JsonDocument,
        request: ProjectionRequest,
    ) -> None:
        self.document = document
        self.request = request
        self.report: list[ProjectionEvent] = []
        self.provenance: list[ProvenanceEntry] = []
        self.fidelity = Fidelity.EXACT
        self.value_nodes = 0
        self.partial_analysis: list[str] = []

    def project_value(
        self, value: JsonValue, path: ValuePath, depth: int
    ) -> PortableValue:
        if depth > self.request.limits.max_depth:
            raise JsonProjectionFailure(
                JsonProjectionFailureKind.RESOURCE_LIMIT, resource_name="projection-depth"
            )
        self.value_nodes += 1
        if self.value_nodes > self.request.limits.max_value_nodes:
            raise JsonProjectionFailure(
                JsonProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="projected-value-nodes",
            )
        self.partial_analysis.append(f"{path!r}:Projectable")
        self.add_origin(ProjectedLocation.value_location(path), value.node_ref(), value.span())
        internal = self.document.value_entity(value.index).internal
        if internal.kind is InternalKind.NULL:
            return PortableValue.null()
        if internal.kind is InternalKind.BOOLEAN:
            return PortableValue.boolean(internal.payload)
        if internal.kind is InternalKind.INTEGER:
            return PortableValue.integer(internal.payload)
        if internal.kind is InternalKind.DECIMAL:
            return PortableValue.decimal(internal.payload)
        if internal.kind is InternalKind.BINARY_FLOAT64:
            return PortableValue.binary_float64(internal.payload)
        if internal.kind is InternalKind.STRING:
            return PortableValue.string(internal.payload)
        if internal.kind is InternalKind.ARRAY:
            items = []
            for entity_index in internal.payload:
                element = _element_value(self.document, entity_index)
                items.append(
                    self.project_value(
                        element,
                        path.child(
                            ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, len(items))
                        ),
                        depth + 1,
                    )
                )
            return PortableValue.sequence(items)
        if internal.kind is InternalKind.OBJECT:
            members = [
                JsonObjectMember(self.document, entity_index)
                for entity_index in internal.payload
            ]
            return self.project_object(value, members, path, depth)
        raise JsonProjectionFailure(
            JsonProjectionFailureKind.SEMANTIC_UNAVAILABLE,
            node=value.node_ref(),
            reason=internal.payload,
        )

    def project_object(
        self,
        object_value: JsonValue,
        members: list[JsonObjectMember],
        path: ValuePath,
        depth: int,
    ) -> PortableValue:
        names: list[str] = []
        for member in members:
            availability = member.name()
            if not availability.is_available:
                raise JsonProjectionFailure(
                    JsonProjectionFailureKind.SEMANTIC_UNAVAILABLE,
                    node=member.key_node_ref(),
                    reason=availability.reason,
                )
            names.append(availability.value)

        seen = set()
        has_duplicates = any(
            name in seen or seen.add(name) for name in names
        )
        use_mapping = False
        if self.request.target is ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1:
            use_mapping = True
        elif (
            self.request.target
            in (ProjectionTarget.BEST_EXACT_CORE_V1, ProjectionTarget.JSON5_BEST_EXACT_CORE_V1)
            and has_duplicates
        ):
            use_mapping = True

        if use_mapping:
            if self.request.target is not ProjectionTarget.PROJECT_AS_OBJECT_V1:
                self.fidelity = _max_fidelity(self.fidelity, Fidelity.TRANSFORMED)
                self.push_event(
                    ProjectionEvent(
                        kind=ProjectionEventKind.STRUCTURE_REENCODED,
                        policy=None,
                        source=object_value.node_ref(),
                        projected=ProjectedLocation.value_location(path),
                        old_category="JsonObject",
                        new_category="EntryMapping",
                        reversible=True,
                        loss=Fidelity.TRANSFORMED,
                    )
                )
            entries = []
            for ordinal, (member, name) in enumerate(zip(members, names)):
                key_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, ordinal)
                )
                value_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, ordinal)
                )
                association = AssociationLocation(
                    path, ordinal, AssociationRole.ENTRY_MAPPING_ENTRY
                )
                self.add_origin(
                    ProjectedLocation.association_location(association),
                    member.node_ref(),
                    member.span(),
                )
                self.add_origin(
                    ProjectedLocation.value_location(key_path),
                    member.key_node_ref(),
                    self.document.span(member.entity().key),
                )
                projected = self.project_value(member.value(), value_path, depth + 1)
                entries.append((PortableValue.string(name), projected))
            return PortableValue.entry_mapping(entries)

        policy = self.duplicate_policy(object_value.node_ref())
        retained = select_members(members, names, policy, object_value.node_ref())
        if len(retained) != len(members):
            self.fidelity = Fidelity.LOSSY
        retained_set = set(retained)
        projected_ordinals = {
            source: ordinal for ordinal, source in enumerate(retained)
        }
        for source_ordinal, member in enumerate(members):
            if source_ordinal not in retained_set:
                name = names[source_ordinal]
                retained_source = next(
                    index for index in retained if names[index] == name
                )
                projected_ordinal = projected_ordinals[retained_source]
                self.push_event(
                    ProjectionEvent(
                        kind=ProjectionEventKind.DUPLICATE_COLLAPSED,
                        policy=policy,
                        source=member.node_ref(),
                        projected=ProjectedLocation.association_location(
                            AssociationLocation(
                                path, projected_ordinal, AssociationRole.OBJECT_ENTRY
                            )
                        ),
                        old_category="JsonObjectMember",
                        new_category="Collapsed",
                        reversible=False,
                        loss=Fidelity.LOSSY,
                    )
                )
        builder = ObjectBuilder()
        for projected_ordinal, source_ordinal in enumerate(retained):
            member = members[source_ordinal]
            name = names[source_ordinal]
            value_path = path.child(
                ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, name)
            )
            self.add_origin(
                ProjectedLocation.association_location(
                    AssociationLocation(
                        path, projected_ordinal, AssociationRole.OBJECT_ENTRY
                    )
                ),
                member.node_ref(),
                member.span(),
            )
            self.add_origin(
                ProjectedLocation.association_location(
                    AssociationLocation(
                        path, projected_ordinal, AssociationRole.OBJECT_KEY
                    )
                ),
                member.key_node_ref(),
                self.document.span(member.entity().key),
            )
            projected = self.project_value(member.value(), value_path, depth + 1)
            builder.insert(name, projected)
        return builder.build()

    def duplicate_policy(self, node: NodeRef) -> DuplicateKeyPolicy:
        """Exact-node rules first, then the global rule, else Reject
        (projection.rs:641-657)."""
        for scope, policy in self.request.duplicate_rules:
            if scope.node == node:
                return policy
        for scope, policy in self.request.duplicate_rules:
            if scope.node is None:
                return policy
        return DuplicateKeyPolicy.REJECT

    def add_origin(
        self, projected: ProjectedLocation, node: NodeRef, span: Span
    ) -> None:
        if len(self.provenance) >= self.request.limits.max_provenance_entries:
            raise JsonProjectionFailure(
                JsonProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="provenance-entries",
            )
        self.provenance.append(
            ProvenanceEntry(
                projected=projected,
                origins=(
                    SourceOrigin(
                        snapshot=self.document.snapshot_identity(),
                        node=node,
                        span=span,
                        relation=ProvenanceRelation.DIRECT,
                    ),
                ),
            )
        )

    def push_event(self, event: ProjectionEvent) -> None:
        if len(self.report) >= self.request.limits.max_report_entries:
            raise JsonProjectionFailure(
                JsonProjectionFailureKind.RESOURCE_LIMIT,
                resource_name="projection-report-entries",
            )
        self.report.append(event)


def select_members(
    members: list[JsonObjectMember],
    names: list[str],
    policy: DuplicateKeyPolicy,
    node: NodeRef,
) -> list[int]:
    """Applies the explicit duplicate policy (projection.rs:691-728)."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    if policy is DuplicateKeyPolicy.REJECT:
        for name in names:
            if counts[name] > 1:
                raise JsonProjectionFailure(
                    JsonProjectionFailureKind.DUPLICATE_KEYS,
                    node=node,
                    name_value=name,
                )
    if policy in (DuplicateKeyPolicy.REJECT, DuplicateKeyPolicy.FIRST_WINS):
        seen = set()
        return [index for index in range(len(members)) if names[index] not in seen and not seen.add(names[index])]
    retained = []
    seen = set()
    for index in range(len(members) - 1, -1, -1):
        if names[index] not in seen:
            seen.add(names[index])
            retained.append(index)
    retained.reverse()
    return retained


def project(document: JsonDocument, request: ProjectionRequest) -> ProjectionResult:
    """Applies an immutable request; a failure never contains a partial value
    (projection.rs:357-429)."""
    if document.formation_status() is not FormationStatus.COMPLETE:
        return _failed(JsonProjectionFailureKind.RECOVERED_DOCUMENT)
    if (
        request.target is ProjectionTarget.JSON5_BEST_EXACT_CORE_V1
        and document.profile is not JsonProfile.JSON5_STANDARD_V1
    ) or (
        request.target is ProjectionTarget.BEST_EXACT_CORE_V1
        and document.profile is JsonProfile.JSON5_STANDARD_V1
    ):
        return _failed(JsonProjectionFailureKind.TARGET_NOT_APPLICABLE)
    root_availability = document.root().kind()
    if (
        request.target
        in (
            ProjectionTarget.PROJECT_AS_OBJECT_V1,
            ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1,
        )
        and (
            not root_availability.is_available
            or root_availability.value is not JsonValueKind.OBJECT
        )
    ):
        return _failed(JsonProjectionFailureKind.TARGET_NOT_APPLICABLE)
    for scope, _ in request.duplicate_rules:
        if scope.node is None:
            continue
        node = scope.node
        if node.snapshot != document.snapshot_identity():
            return _failed(JsonProjectionFailureKind.WRONG_SNAPSHOT_POLICY)
        if node.index < 0 or node.index >= len(document.entities):
            return _failed(JsonProjectionFailureKind.INVALID_POLICY_TARGET)
        entity = document.entities[node.index]
        if not hasattr(entity, "internal"):
            return _failed(JsonProjectionFailureKind.INVALID_POLICY_TARGET)
        if entity.internal.kind is not InternalKind.OBJECT:
            return _failed(JsonProjectionFailureKind.INVALID_POLICY_TARGET)

    context = _ProjectionContext(document, request)
    try:
        value = context.project_value(document.root(), ValuePath.root(), 0)
    except JsonProjectionFailure as failure:
        return _failed_with_analysis(failure, context)
    return CompleteProjection(
        value=value,
        fidelity=context.fidelity,
        report=ProjectionReport(events=tuple(context.report)),
        provenance=ProvenanceMap(entries=tuple(context.provenance)),
    )


def _failed(kind: JsonProjectionFailureKind) -> FailedProjectionAttempt:
    return FailedProjectionAttempt(
        diagnostics=(
            JsonDiagnostic(
                code=JsonProjectionFailure(kind).code,
                category=DiagnosticCategory.PROJECTION,
                severity=JsonSeverity.ERROR,
                primary=None,
            ),
        ),
        report=ProjectionReport(),
    )


def _failed_with_analysis(
    failure: JsonProjectionFailure, context: _ProjectionContext
) -> FailedProjectionAttempt:
    return FailedProjectionAttempt(
        diagnostics=(
            JsonDiagnostic(
                code=failure.code,
                category=DiagnosticCategory.PROJECTION,
                severity=JsonSeverity.ERROR,
                primary=None,
            ),
        ),
        report=ProjectionReport(events=tuple(context.report)),
        partial_analysis=tuple(context.partial_analysis),
    )


def _element_value(document: JsonDocument, index: int):
    from consema.json.document import JsonArrayElement

    return JsonArrayElement(document, index).value()


def _max_fidelity(left: Fidelity, right: Fidelity) -> Fidelity:
    order = [Fidelity.EXACT, Fidelity.TRANSFORMED, Fidelity.LOSSY]
    return order[max(order.index(left), order.index(right))]
