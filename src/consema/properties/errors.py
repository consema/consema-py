"""Typed Properties-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

Frozen code names with authority citations (all registry spellings are
transcribed from crates/consema-protocol/src/error_registry.rs:1098-1169;
the failure enums and their code mappings follow the Rust family's
StableFailure impls):

- Properties diagnostics: java-properties.parse.malformed-unicode-escape@1
  (error_registry.rs:1129), java-properties.edit.canonical-fallback@1
  (:1099, a Warning), java-properties.profile.mismatch@1 (:1135, query
  role validation), java-properties.source.profile-encoding@1 (:1165,
  fatal profile/source contract mismatch, parser.rs:83-91).
- Fatal formation resource limits: core.parse.resource-limit@1
  (error_registry.rs:39) for every PropertiesParseLimits bound
  (lib.rs:61-122); source snapshot failures surface the typed
  SourceError codes (core.source.*@1) unchanged.
- Projection failure code mapping: crates/consema-properties/src/
  projection.rs:741-752 — RecoveredDocument ->
  java-properties.projection.incomplete-document@1, UnpairedSurrogate ->
  java-properties.projection.unpaired-surrogate@1 (:745), DuplicateKey /
  CoreInvariant -> core.projection.target-not-applicable@1 (:748),
  ResourceLimit -> core.projection.resource-limit@1 (:750).
- Edit failure code mapping: crates/consema-properties/src/edit.rs:237-252
  — RecoveredDocument -> core.edit.incomplete-target@1, WrongSnapshot ->
  core.edit.wrong-snapshot@1, WrongRole -> core.edit.wrong-role@1,
  DuplicateTarget/OverlappingOwnership/PlacementAnchorRemoved ->
  core.edit.conflicting-edits@1, InvalidPlacement ->
  java-properties.edit.invalid-placement@1 (:245), TargetNotFound ->
  core.edit.target-not-found@1, EncodingUnrepresentable ->
  core.edit.representation-incompatible@1 (:247), InvalidLiteral ->
  core.edit.invalid-literal@1, ResourceLimit -> core.edit.resource-limit@1,
  NewDocumentFormationFailed -> core.edit.formation-failed@1.
- Query failures reuse the common core.query.*@1 codes
  (error_registry.rs:108-118) through consema.protocol.query.QueryFailure
  — no new type is needed.
- Diagnostic ordering: Diagnostic::sort_deterministically,
  crates/consema-core/src/diagnostic.rs:107-123 (primary start, category,
  code, occurrence; missing primary sorts last).

Design: the Properties family raises typed exceptions whose stable
``code`` is the registered code (RFC 0016 §6). Error text is human
presentation only and never participates in conformance comparison. The
vector-facing failure *names* ("RecoveredDocument", "UnpairedSurrogate",
"InvalidLiteral", ...) are exposed as ``name`` properties using the exact
Rust variant spellings the conformance vectors reference
(conformance/vectors/java-properties-v1.json:58, :83, :88).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory

# ---------------------------------------------------------------------------
# SDK-internal diagnostic record (mirror of consema_core::Diagnostic)
# ---------------------------------------------------------------------------


class PropertiesSeverity(enum.Enum):
    """The three frozen presentation severities (diagnostic.rs)."""

    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    """One related source location with its stable relationship role."""

    role: str
    location: Span


@dataclass(frozen=True, slots=True)
class PropertiesDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code; category, severity,
    primary span, arguments, related locations, and the stable occurrence
    ordinal follow the core record shape (RFC 0011 §8). This SDK-internal
    record is distinct from the protocol-layer ``core.diagnostic@1``
    transfer record (consema.protocol.diagnostic.Diagnostic).
    """

    code: str
    category: DiagnosticCategory
    severity: PropertiesSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)
    related: tuple[RelatedLocation, ...] = field(default_factory=tuple, repr=False)
    notes: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (diagnostic.rs:107-123); missing primary
        sorts last."""
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[PropertiesDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)
    (diagnostic.rs:107-123)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures
# ---------------------------------------------------------------------------


class PropertiesFormationFailureKind(enum.Enum):
    """Fatal formation failure categories.

    Every limit bound of PropertiesParseLimits (lib.rs:61-122) is fatal
    with no truncation-then-success (RFC 0016 §6); the resource names are
    the exact Rust spellings used by parser.rs check_limit calls. The
    profile/source mismatch is the one fatal formation failure carrying a
    format-owned code (parser.rs:83-91).
    """

    SOURCE_BYTES = "source-bytes"
    TOKEN_COUNT = "token-count"
    NODE_COUNT = "nodes"
    DIAGNOSTICS = "diagnostics"
    DECODED_UTF8_BYTES = "decoded-utf8-bytes"
    DECODED_SCALARS = "decoded-scalars"
    NATURAL_LINES = "natural-lines"
    NATURAL_LINE_BYTES = "natural-line-bytes"
    NATURAL_LINE_SCALARS = "natural-line-scalars"
    LOGICAL_LINES = "logical-lines"
    LOGICAL_LINE_NATURAL_LINES = "logical-line-natural-lines"
    LOGICAL_LINE_SCALARS = "logical-line-scalars"
    PROPERTIES = "properties"
    COMMENTS = "comments"
    ESCAPES = "escapes"
    UNICODE_ESCAPES = "unicode-escapes"
    JAVA_CODE_UNITS_PER_STRING = "java-code-units-per-string"
    TOTAL_JAVA_CODE_UNITS = "total-java-code-units"
    DUPLICATE_GROUP_MEMBERS = "duplicate-group-members"
    RECOVERY_REGIONS = "recovery-regions"
    PROFILE_ENCODING = "profile-encoding"


class PropertiesFormationFailure(Exception):
    """Fatal formation failure; no Document exists (parser.rs:17-36).

    Exceeding a configured limit is fatal (RFC 0016 §6); the profile/
    source-encoding mismatch is likewise fatal and carries
    java-properties.source.profile-encoding@1 (parser.rs:83-91). Source
    snapshot construction failures (invalid sequences, unsupported BOM,
    source resource limits) surface the typed SourceError unchanged with
    its own core.source.*@1 code.
    """

    def __init__(
        self,
        kind: PropertiesFormationFailureKind,
        *,
        observed: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.name = kind.value
        self.observed = observed
        self.limit = limit

    @property
    def code(self) -> str:
        if self.kind is PropertiesFormationFailureKind.PROFILE_ENCODING:
            return "java-properties.source.profile-encoding@1"
        return "core.parse.resource-limit@1"


# ---------------------------------------------------------------------------
# Projection failures
# ---------------------------------------------------------------------------


class PropertiesProjectionFailureKind(enum.Enum):
    """Stable projection failure categories (projection.rs:249-262)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    UNPAIRED_SURROGATE = "UnpairedSurrogate"
    DUPLICATE_KEY = "DuplicateKey"
    RESOURCE_LIMIT = "ResourceLimit"
    CORE_INVARIANT = "CoreInvariant"


class PropertiesProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping authority: projection.rs:741-752. ``name`` is the exact
    Rust variant spelling referenced by the conformance vectors.
    """

    def __init__(
        self,
        kind: PropertiesProjectionFailureKind,
        *,
        property_node=None,
        component: str | None = None,
        retained=None,
        duplicate=None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.property_node = property_node
        self.component = component
        self.retained = retained
        self.duplicate = duplicate
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _PROJECTION_CODES[self.kind]


_PROJECTION_CODES = {
    PropertiesProjectionFailureKind.RECOVERED_DOCUMENT: (
        "java-properties.projection.incomplete-document@1"
    ),
    PropertiesProjectionFailureKind.UNPAIRED_SURROGATE: (
        "java-properties.projection.unpaired-surrogate@1"
    ),
    PropertiesProjectionFailureKind.DUPLICATE_KEY: "core.projection.target-not-applicable@1",
    PropertiesProjectionFailureKind.RESOURCE_LIMIT: "core.projection.resource-limit@1",
    PropertiesProjectionFailureKind.CORE_INVARIANT: "core.projection.target-not-applicable@1",
}


# ---------------------------------------------------------------------------
# Edit failures
# ---------------------------------------------------------------------------


class PropertiesEditFailureKind(enum.Enum):
    """Stable edit failure categories (edit.rs:178-205)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    DUPLICATE_TARGET = "DuplicateTarget"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    INVALID_PLACEMENT = "InvalidPlacement"
    PLACEMENT_ANCHOR_REMOVED = "PlacementAnchorRemoved"
    TARGET_NOT_FOUND = "TargetNotFound"
    ENCODING_UNREPRESENTABLE = "EncodingUnrepresentable"
    INVALID_LITERAL = "InvalidLiteral"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


class PropertiesEditFailure(Exception):
    """Stable edit failure with a frozen registered code.

    Code mapping authority: edit.rs:237-252. ``name`` is the exact Rust
    variant spelling referenced by the conformance vectors.
    """

    def __init__(
        self,
        kind: PropertiesEditFailureKind,
        *,
        detail: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.detail = detail
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _EDIT_CODES[self.kind]


_EDIT_CODES = {
    PropertiesEditFailureKind.RECOVERED_DOCUMENT: "core.edit.incomplete-target@1",
    PropertiesEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    PropertiesEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    PropertiesEditFailureKind.DUPLICATE_TARGET: "core.edit.conflicting-edits@1",
    PropertiesEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    PropertiesEditFailureKind.INVALID_PLACEMENT: "java-properties.edit.invalid-placement@1",
    PropertiesEditFailureKind.PLACEMENT_ANCHOR_REMOVED: "core.edit.conflicting-edits@1",
    PropertiesEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    PropertiesEditFailureKind.ENCODING_UNREPRESENTABLE: (
        "core.edit.representation-incompatible@1"
    ),
    PropertiesEditFailureKind.INVALID_LITERAL: "core.edit.invalid-literal@1",
    PropertiesEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    PropertiesEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}
