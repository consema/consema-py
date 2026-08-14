"""Common immutable contracts for creating a new format document.

Authority (language-neutral first; Rust only for arbitration):

- RFC 0004 §3 (https://github.com/consema/consema/blob/main/docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md): the common immutable MaterializationRequest records
  (target_profile, style, encoding, newline, mapping_policy,
  representability, limits); the closed v1 MaterializationLimits fields and
  semantics; ExactOnly is intentionally the only v1 representability value;
  UniqueStringEntriesToObject is explicit and reportable.
- RFC 0004 §4 (lines 98-127): frozen style IDs (json.canonical-compact@1,
  json.canonical-pretty@1, toml.canonical-document@1) and newline rules.
- RFC 0004 §7 (lines 170-191): the completion algebra — Complete{Value,
  Fidelity, Report, Provenance} or Failed{Diagnostics, Report,
  analyzed_input_paths}; failed attempts contain no Document and no partial
  output bytes.
- RFC 0004 §8 (lines 193-217): materialization provenance points from
  portable input locations (Value/Association) to target origins
  (snapshot identity, NodeRef, raw Span, relation Direct/Reencoded/
  Generated).
- RFC 0016 §5.2 (https://github.com/consema/consema/blob/main/docs/rfcs/0016-go-api-mapping-v1.md): the
  conservative default policy is core.projection.exact-or-reject@1 (never
  invented).
- https://github.com/consema/consema-rs/blob/main/consema-document/src/materialization.rs — arbitration:
  MaterializationLimits defaults materialization.rs;
  MaterializationRequest defaults materialization.rs;
  failure code mapping materialization.rs.
- Error codes: https://github.com/consema/consema-rs/blob/main/consema-protocol/src/error_registry.rs
  (core.materialization.formation-failed@1:556, invalid-request@1:562,
  resource-limit@1:574, unrepresentable@1:580, unsupported-encoding@1:586,
  unsupported-newline@1:592, unsupported-profile@1:598,
  unsupported-style@1:604).

https://github.com/consema/consema-go/blob/main/go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace

from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.source import SourceEncoding
from consema.document.structural import NodeRef, SnapshotIdentity, Span

# Frozen defaults, https://github.com/consema/consema-rs/blob/main/consema-document/src/materialization.rs
_DEFAULT_MAX_INPUT_NODES = 1_000_000
_DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_DEPTH = 256
_DEFAULT_MAX_REPORT_ENTRIES = 100_000
_DEFAULT_MAX_PROVENANCE_ENTRIES = 2_000_000


class NewlinePolicy(enum.Enum):
    """Explicit output newline policy (materialization.rs)."""

    NONE = "None"
    LF = "Lf"
    CRLF = "CrLf"

    @property
    def bytes(self) -> bytes:
        """Exact selected newline bytes (materialization.rs)."""
        return {NewlinePolicy.NONE: b"", NewlinePolicy.LF: b"\n", NewlinePolicy.CRLF: b"\r\n"}[self]


class MappingPolicy(enum.Enum):
    """Explicit treatment of ordered mappings at object-only targets
    (materialization.rs; RFC 0004 §3)."""

    REQUIRE_OBJECT = "RequireObject"
    UNIQUE_STRING_ENTRIES_TO_OBJECT = "UniqueStringEntriesToObject"


class RepresentabilityPolicy(enum.Enum):
    """Closed v1 representability policy (materialization.rs; RFC 0004
    §3: ExactOnly is intentionally the only v1 value)."""

    EXACT_ONLY = "ExactOnly"


@dataclass(frozen=True, slots=True)
class MaterializationLimits:
    """Resource limits for one complete materialization
    (materialization.rs; RFC 0004 §3).

    All limits apply before or during allocation; a failure returns no
    Document, no partial bytes, and no provenance that can be mistaken for a
    result (RFC 0004 §3, lines 83-84).
    """

    max_input_nodes: int = _DEFAULT_MAX_INPUT_NODES
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_report_entries: int = _DEFAULT_MAX_REPORT_ENTRIES
    max_provenance_entries: int = _DEFAULT_MAX_PROVENANCE_ENTRIES


@dataclass(frozen=True, slots=True)
class MaterializationRequest:
    """Complete immutable request for creating one new target document
    (materialization.rs; RFC 0004 §3).

    ``new(target_profile, style)`` creates a strict request with UTF-8, LF,
    Object-only, and ExactOnly defaults (materialization.rs).
    Materialization consumes one complete PortableValue; it never consumes a
    format AST, process-local handle, partial projection, or arbitrary bytes
    (RFC 0004 §3, lines 58-59).
    """

    target_profile: ProfileId
    style: MaterializationStyleId
    encoding: SourceEncoding = field(default_factory=SourceEncoding.utf8)
    newline: NewlinePolicy = NewlinePolicy.LF
    mapping_policy: MappingPolicy = MappingPolicy.REQUIRE_OBJECT
    representability: RepresentabilityPolicy = RepresentabilityPolicy.EXACT_ONLY
    limits: MaterializationLimits = field(default_factory=MaterializationLimits)

    @classmethod
    def new(
        cls, target_profile: ProfileId, style: MaterializationStyleId
    ) -> MaterializationRequest:
        """Creates a strict request with UTF-8, LF, Object-only, and ExactOnly
        defaults (materialization.rs)."""
        return cls(target_profile=target_profile, style=style)

    def with_encoding(self, encoding: SourceEncoding) -> MaterializationRequest:
        return replace(self, encoding=encoding)

    def with_newline(self, newline: NewlinePolicy) -> MaterializationRequest:
        return replace(self, newline=newline)

    def with_mapping_policy(self, policy: MappingPolicy) -> MaterializationRequest:
        return replace(self, mapping_policy=policy)

    def with_limits(self, limits: MaterializationLimits) -> MaterializationRequest:
        return replace(self, limits=limits)


class MaterializationFidelity(enum.Enum):
    """Whole-operation semantic fidelity (materialization.rs)."""

    EXACT = "Exact"
    TRANSFORMED = "Transformed"


@dataclass(frozen=True, slots=True)
class MaterializationReport:
    """Complete ordered materialization report (materialization.rs).

    Report events are stable, ordered, machine-readable diagnostics; human
    wording is not a contract (RFC 0004 §7, lines 189-191).
    """

    events: tuple[object, ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def new(
        cls, events: list[object], limits: MaterializationLimits
    ) -> MaterializationReport:
        """Creates a report after enforcing its configured event limit."""
        if len(events) > limits.max_report_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="report-entries"
            )
        return cls(events=tuple(events))


class MaterializationInputLocationKind(enum.Enum):
    """Portable input location kind (materialization.rs; RFC 0004 §8)."""

    VALUE = "Value"
    ASSOCIATION = "Association"


@dataclass(frozen=True, slots=True)
class MaterializationInputLocation:
    """Portable input value or association location (materialization.rs).

    The location payload is the protocol/core value-path or association-
    location record (ValuePath / AssociationLocation of the semantic model,
    RFC 0004 §8); it is typed opaquely here — consema.protocol is a sibling
    package in this implementation, and the opaque typing keeps this
    module's import graph acyclic.
    """

    kind: MaterializationInputLocationKind
    location: object = None

    @classmethod
    def value(cls, path: object) -> MaterializationInputLocation:
        return cls(kind=MaterializationInputLocationKind.VALUE, location=path)

    @classmethod
    def association(cls, location: object) -> MaterializationInputLocation:
        return cls(kind=MaterializationInputLocationKind.ASSOCIATION, location=location)


class MaterializationRelation(enum.Enum):
    """Relationship from portable input fact to generated target syntax
    (materialization.rs; RFC 0004 §8)."""

    DIRECT = "Direct"
    REENCODED = "Reencoded"
    GENERATED = "Generated"


@dataclass(frozen=True, slots=True)
class MaterializedOrigin:
    """One exact output origin in the newly materialized snapshot
    (materialization.rs)."""

    snapshot: SnapshotIdentity
    node: NodeRef
    span: Span
    relation: MaterializationRelation


@dataclass(frozen=True, slots=True)
class MaterializationProvenanceEntry:
    """One input location mapped to one or more target origins
    (materialization.rs)."""

    input: MaterializationInputLocation
    outputs: tuple[MaterializedOrigin, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MaterializationProvenanceMap:
    """Complete input-to-output provenance map (materialization.rs).

    Provenance points from portable input locations to the new Document; it
    is not the reverse-direction Projection provenance map (RFC 0004 §8).
    """

    entries: tuple[MaterializationProvenanceEntry, ...] = field(default_factory=tuple)

    @classmethod
    def new(
        cls,
        entries: list[MaterializationProvenanceEntry],
        target: SnapshotIdentity,
        limits: MaterializationLimits,
    ) -> MaterializationProvenanceMap:
        """Validates snapshot binding, non-empty outputs, and configured size
        (materialization.rs)."""
        units = len(entries)
        for entry in entries:
            if not entry.outputs:
                raise MaterializationFailure(
                    MaterializationFailureKind.INVALID_REQUEST,
                    detail="provenance entry has no output",
                )
            units += len(entry.outputs)
            for origin in entry.outputs:
                if (
                    origin.snapshot != target
                    or origin.node.snapshot != target
                    or origin.span.snapshot != target
                ):
                    raise MaterializationFailure(
                        MaterializationFailureKind.INVALID_REQUEST,
                        detail="provenance origin uses another snapshot",
                    )
        if units > limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        return cls(entries=tuple(entries))


class MaterializationFailureKind(enum.Enum):
    """Stable materialization failure category (materialization.rs)."""

    INVALID_REQUEST = "invalid-request"
    UNSUPPORTED_PROFILE = "unsupported-profile"
    UNSUPPORTED_STYLE = "unsupported-style"
    UNSUPPORTED_ENCODING = "unsupported-encoding"
    UNSUPPORTED_NEWLINE = "unsupported-newline"
    UNREPRESENTABLE = "unrepresentable"
    RESOURCE_LIMIT = "resource-limit"
    FORMATION_FAILED = "formation-failed"


_CODE_BY_MATERIALIZATION_KIND = {
    MaterializationFailureKind.INVALID_REQUEST: "core.materialization.invalid-request@1",
    MaterializationFailureKind.UNSUPPORTED_PROFILE: "core.materialization.unsupported-profile@1",
    MaterializationFailureKind.UNSUPPORTED_STYLE: "core.materialization.unsupported-style@1",
    MaterializationFailureKind.UNSUPPORTED_ENCODING: "core.materialization.unsupported-encoding@1",
    MaterializationFailureKind.UNSUPPORTED_NEWLINE: "core.materialization.unsupported-newline@1",
    MaterializationFailureKind.UNREPRESENTABLE: "core.materialization.unrepresentable@1",
    MaterializationFailureKind.RESOURCE_LIMIT: "core.materialization.resource-limit@1",
    MaterializationFailureKind.FORMATION_FAILED: "core.materialization.formation-failed@1",
}


class MaterializationFailure(Exception):
    """Stable materialization failure with a frozen registered code.

    Code mapping authority: materialization.rs and
    https://github.com/consema/consema-rs/blob/main/consema-protocol/src/error_registry.rs. Error text is
    human presentation only (RFC 0016 §6).
    """

    def __init__(
        self,
        kind: MaterializationFailureKind,
        *,
        name: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.name = name
        self.detail = detail

    @property
    def code(self) -> str:
        return _CODE_BY_MATERIALIZATION_KIND[self.kind]


@dataclass(frozen=True, slots=True)
class FailedMaterializationAttempt:
    """Failed attempt without a Document or partial output bytes
    (materialization.rs; RFC 0004 §7)."""

    failure: MaterializationFailure
    report: MaterializationReport = field(default_factory=MaterializationReport)
    analyzed_input_paths: tuple[object, ...] = field(default_factory=tuple, repr=False)


@dataclass(frozen=True, slots=True, eq=False)
class CompleteMaterialization:
    """Complete successful materialization; its document and audit facts are
    never partial (materialization.rs; RFC 0004 §7)."""

    document: object
    fidelity: MaterializationFidelity
    report: MaterializationReport = field(default_factory=MaterializationReport)
    provenance: MaterializationProvenanceMap = field(default_factory=MaterializationProvenanceMap)


# The closed materialization completion algebra (materialization.rs;
# RFC 0004 §7): a format materializer returns exactly one of these two
# shapes. The protocol-layer transport core.materialization-result@1
# (RFC 0004 §18) distinguishes present complete results from failure without
# overloading PortableValue Null.
MaterializationResult = CompleteMaterialization | FailedMaterializationAttempt
