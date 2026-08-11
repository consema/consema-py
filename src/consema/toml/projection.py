"""Explicit projection from native TOML semantics to PortableValue.

Authority:

- RFC 0001 §5 (docs/rfcs/0001-toml-1.0-profile.md:78-100): the frozen
  target ``toml.best-exact-core@1``; the kind mapping table (Boolean ->
  Boolean, Integer -> Integer, Float -> BinaryFloat64, String -> String,
  LocalDate -> Date, LocalTime -> Time, LocalDateTime -> LocalDateTime,
  OffsetDateTime -> OffsetDateTime, Array -> Sequence, all table flavors
  and inline tables -> Object, ArrayOfTables -> Sequence<Object>); TOML
  documents contain no duplicate logical keys so tables project losslessly
  to unique-key Objects; table flavor/quoting/radix/underscore/comment/
  layout are native facts, not PortableValue facts; every produced value
  and object association maps back to a source NodeRef/span; temporal
  fields outside the PortableValue v1 closure (e.g. leap seconds) fail
  the whole projection with ``toml.projection.unrepresentable-datetime@1``
  — no truncation, normalization, or silent substitution.
- The completion algebra and failure mapping transcribe
  crates/consema-toml/src/projection.rs:9-227 and 410-435:
  CompleteProjection{value, fidelity, report, provenance} or
  FailedProjectionAttempt{diagnostics, report, partial_analysis};
  fidelity Exact/Transformed/Lossy; failure codes
  toml.projection.unrepresentable-datetime@1,
  core.projection.resource-limit@1 (with the ``limit`` argument),
  toml.projection.core-invariant@1; provenance relations Direct/Derived
  and the per-value/association origin roles (TomlItem, TomlArrayElement,
  TomlEntry, TomlKey) at projection.rs:237-365.
- ProjectionLimits defaults: max_value_nodes 1_000_000,
  max_report_entries 100_000, max_provenance_entries 2_000_000,
  max_depth 256 (projection.rs:53-75).
- RFC 0016 §5.2: the conservative default policy is
  core.projection.exact-or-reject@1 — never invented values.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.core.errors import PVCEError
from consema.core.value import Decimal, PortableValue
from consema.document.structural import NodeRef, NodeRole, SnapshotIdentity, Span
from consema.protocol.diagnostic import Severity
from consema.protocol.error_registry import DiagnosticCategory

from consema.toml.document import Document, TomlDateTime
from consema.toml.errors import (
    TomlDiagnostic,
    TomlProjectionFailure,
    TomlProjectionFailureKind,
)
from consema.toml.paths import (
    AssociationLocation,
    AssociationRole,
    ValuePath,
    ValuePathSegment,
)


class ProjectionTarget(enum.Enum):
    """Versioned TOML projection target contract (projection.rs:9-14)."""

    BEST_EXACT_CORE_V1 = "toml.best-exact-core@1"


@dataclass(frozen=True, slots=True)
class ProjectionLimits:
    """Projection resource limits (projection.rs:53-75)."""

    max_value_nodes: int = 1_000_000
    max_report_entries: int = 100_000
    max_provenance_entries: int = 2_000_000
    max_depth: int = 256

    @classmethod
    def default(cls) -> ProjectionLimits:
        return cls()


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Immutable explicit projection request (projection.rs:15-51)."""

    target: ProjectionTarget
    limits: ProjectionLimits = field(default_factory=ProjectionLimits.default)

    @classmethod
    def new(cls, target: ProjectionTarget) -> ProjectionRequest:
        return cls(target=target)

    def with_limits(self, limits: ProjectionLimits) -> ProjectionRequest:
        return ProjectionRequest(target=self.target, limits=limits)


class Fidelity(enum.Enum):
    """Projection fidelity classification (projection.rs:77-86)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"
    LOSSY = "Lossy"


class ProjectedLocationKind(enum.Enum):
    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class ProjectedLocation:
    """Projected value or association location (projection.rs:88-95)."""

    kind: ProjectedLocationKind
    path: ValuePath | None = None
    association: AssociationLocation | None = None

    @classmethod
    def of_value(cls, path: ValuePath) -> ProjectedLocation:
        return cls(kind=ProjectedLocationKind.VALUE, path=path)

    @classmethod
    def of_association(cls, location: AssociationLocation) -> ProjectedLocation:
        return cls(kind=ProjectedLocationKind.ASSOCIATION, association=location)


class ProvenanceRelation(enum.Enum):
    """Source-to-projection relation (projection.rs:97-104)."""

    DIRECT = "Direct"
    DERIVED = "Derived"


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    """One exact source origin (projection.rs:106-117)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: ProvenanceRelation


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One many-valued provenance mapping entry (projection.rs:119-126)."""

    projected: ProjectedLocation
    origins: tuple[SourceOrigin, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """Immutable multi-map from projected locations to source origins
    (projection.rs:128-140)."""

    entries: tuple[ProvenanceEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Complete ordered projection report; exact TOML projections emit no
    transformation or loss events (projection.rs:142-156)."""

    events: tuple[TomlDiagnostic, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CompleteProjection:
    """Complete successful projection; its value is never partial
    (projection.rs:158-169)."""

    value: PortableValue
    fidelity: Fidelity
    report: ProjectionReport = field(default_factory=ProjectionReport)
    provenance: ProvenanceMap = field(default_factory=ProvenanceMap)


@dataclass(frozen=True, slots=True)
class FailedProjectionAttempt:
    """Failed attempt without a partial PortableValue (projection.rs:171-180)."""

    diagnostics: tuple[TomlDiagnostic, ...]
    report: ProjectionReport = field(default_factory=ProjectionReport)
    partial_analysis: tuple[str, ...] = field(default_factory=tuple)


# The closed projection completion algebra (projection.rs:182-189).
ProjectionResult = CompleteProjection | FailedProjectionAttempt


class _ProjectionContext:
    __slots__ = ("document", "limits", "value_nodes", "provenance_units", "provenance")

    def __init__(self, document: Document, limits: ProjectionLimits) -> None:
        self.document = document
        self.limits = limits
        self.value_nodes = 0
        self.provenance_units = 0
        self.provenance: dict[ProjectedLocation, list[SourceOrigin]] = {}

    def project_item(self, index: int, path: ValuePath, depth: int) -> PortableValue:
        if depth > self.limits.max_depth:
            raise TomlProjectionFailure(
                TomlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_depth"
            )
        self.value_nodes += 1
        if self.value_nodes > self.limits.max_value_nodes:
            raise TomlProjectionFailure(
                TomlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_value_nodes"
            )
        kind = self.document._item_entity(index).kind
        value: PortableValue
        if kind.name == "String":
            value = PortableValue.string(kind.value)
        elif kind.name == "Integer":
            value = PortableValue.integer(kind.value)
        elif kind.name == "Float":
            value = PortableValue.binary_float64(kind.value)
        elif kind.name == "Boolean":
            value = PortableValue.boolean(kind.value)
        elif kind.name == "DateTime":
            value = self._project_datetime(kind.value, self.document._entity(index).span)
        elif kind.name in ("Array", "ArrayOfTables"):
            items = []
            for ordinal, element_index in enumerate(kind.children):
                element = self.document._entity(element_index).kind
                child_path = path.child(ValuePathSegment.sequence_element(ordinal))
                items.append(self.project_item(element.item, child_path, depth + 1))
                self._add_origin(
                    ProjectedLocation.of_value(child_path),
                    element_index,
                    NodeRole.TOML_ARRAY_ELEMENT,
                    ProvenanceRelation.DIRECT,
                )
            value = PortableValue.sequence(items)
        else:  # Table / InlineTable
            pairs = []
            for entry_index in kind.children:
                entry = self.document._entity(entry_index).kind
                key_entity = self.document._entity(entry.key).kind
                child_path = path.child(ValuePathSegment.object_value(key_entity.name))
                child = self.project_item(entry.item, child_path, depth + 1)
                if any(existing_key == key_entity.name for existing_key, _ in pairs):
                    raise TomlProjectionFailure(TomlProjectionFailureKind.CORE_INVARIANT)
                pairs.append((key_entity.name, child))
                association = AssociationLocation.new(
                    path, entry.ordinal, AssociationRole.OBJECT_ENTRY
                )
                self._add_origin(
                    ProjectedLocation.of_association(association),
                    entry_index,
                    NodeRole.TOML_ENTRY,
                    ProvenanceRelation.DIRECT,
                )
                key_association = AssociationLocation.new(
                    path, entry.ordinal, AssociationRole.OBJECT_KEY
                )
                self._add_origin(
                    ProjectedLocation.of_association(key_association),
                    entry.key,
                    NodeRole.TOML_KEY,
                    ProvenanceRelation.DIRECT,
                )
            value = PortableValue.object(pairs)
        self._add_origin(
            ProjectedLocation.of_value(path),
            index,
            NodeRole.TOML_ITEM,
            ProvenanceRelation.DIRECT,
        )
        return value

    def _project_datetime(self, value: TomlDateTime, span: Span) -> PortableValue:
        """projection.rs:367-408. Temporal fields outside the PortableValue
        closure (leap seconds, invalid core fields) fail the whole
        projection with toml.projection.unrepresentable-datetime@1."""
        date = value.date
        time = value.time
        offset = value.offset_minutes
        try:
            if date is not None and time is None and offset is None:
                return PortableValue.date(date.year, date.month, date.day)
            if date is None and time is not None and offset is None:
                fraction = Decimal(time.nanosecond, -9)
                return PortableValue.time(time.hour, time.minute, time.second, fraction)
            if date is not None and time is not None and offset is None:
                local = PortableValue.local_date_time(
                    PortableValue.date(date.year, date.month, date.day),
                    PortableValue.time(
                        time.hour,
                        time.minute,
                        time.second,
                        Decimal(time.nanosecond, -9),
                    ),
                )
                return local
            if date is not None and time is not None and offset is not None:
                local = PortableValue.local_date_time(
                    PortableValue.date(date.year, date.month, date.day),
                    PortableValue.time(
                        time.hour,
                        time.minute,
                        time.second,
                        Decimal(time.nanosecond, -9),
                    ),
                )
                return PortableValue.offset_date_time(local, offset * 60)
        except (ValueError, PVCEError):
            raise TomlProjectionFailure(
                TomlProjectionFailureKind.UNREPRESENTABLE_DATETIME, span=span
            ) from None
        raise TomlProjectionFailure(
            TomlProjectionFailureKind.UNREPRESENTABLE_DATETIME, span=span
        )

    def _add_origin(
        self,
        projected: ProjectedLocation,
        index: int,
        role: NodeRole,
        relation: ProvenanceRelation,
    ) -> None:
        self.provenance_units += 1
        if self.provenance_units > self.limits.max_provenance_entries:
            raise TomlProjectionFailure(
                TomlProjectionFailureKind.RESOURCE_LIMIT, limit_name="max_provenance_entries"
            )
        origin = SourceOrigin(
            snapshot=self.document.snapshot_identity(),
            node=self.document.node_ref(index, role),
            span=self.document._entity(index).span,
            relation=relation,
        )
        self.provenance.setdefault(projected, []).append(origin)


def project_document(document: Document, request: ProjectionRequest) -> ProjectionResult:
    """Applies an immutable explicit projection request (projection.rs:202-227)."""
    if request.target is not ProjectionTarget.BEST_EXACT_CORE_V1:
        return FailedProjectionAttempt(
            diagnostics=(
                TomlDiagnostic(
                    code="core.projection.resource-limit@1",
                    category=DiagnosticCategory.RESOURCE,
                    severity=Severity.ERROR,
                    primary=None,
                    arguments={"limit": "target"},
                    occurrence=0,
                ),
            )
        )
    context = _ProjectionContext(document, request.limits)
    try:
        value = context.project_item(document.root().index, ValuePath.root(), 0)
    except TomlProjectionFailure as failure:
        return FailedProjectionAttempt(
            diagnostics=(failure.to_diagnostic(),),
            partial_analysis=(),
        )
    entries = tuple(
        ProvenanceEntry(projected=projected, origins=tuple(origins))
        for projected, origins in context.provenance.items()
    )
    return CompleteProjection(
        value=value,
        fidelity=Fidelity.EXACT,
        report=ProjectionReport(),
        provenance=ProvenanceMap(entries=entries),
    )
