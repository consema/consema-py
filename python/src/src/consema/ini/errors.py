"""Typed INI-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

Frozen code names with authority citations (all registry spellings are
transcribed from crates/consema-protocol/src/error_registry.rs:978-1097;
the format failure enums and their code mappings are the Rust family's
StableFailure impls):

- INI diagnostic codes: error_registry.rs:979 (ini.edit.canonical-fallback
  @1), :985 (ini.edit.case-collision@1), :991 (ini.edit.invalid-name@1),
  :997 (ini.edit.invalid-placement@1), :1003 (ini.formation.case-collision
  @1), :1009 (ini.formation.duplicate-entry@1), :1015
  (ini.formation.duplicate-section@1), :1021 (ini.materialization.round-
  trip-mismatch@1), :1027 (ini.parse.invalid-character@1), :1033
  (ini.parse.invalid-continuation@1), :1039 (ini.parse.malformed-line@1),
  :1045 (ini.parse.malformed-section@1), :1051 (ini.parse.missing-
  delimiter@1), :1057 (ini.parse.missing-section@1), :1063
  (ini.profile.encoding@1), :1069 (ini.profile.mismatch@1), :1075
  (ini.projection.collision@1), :1081 (ini.projection.duplicate-collapsed
  @1), :1087 (ini.projection.incomplete-document@1), :1093
  (ini.query.invalid-name-mode@1).
- Fatal formation codes: core.parse.resource-limit@1 error_registry.rs:39;
  ini.profile.encoding@1 error_registry.rs:1063 (parser.rs:22-32, 61-94);
  the source-layer codes core.source.invalid-utf8@1 error_registry.rs:207,
  core.source.encoding-conflict@1 :366, core.source.invalid-sequence@1
  :372, core.source.unsupported-bom@1 :405, core.source.resource-limit@1
  :399, core.source.code-page-required@1 :967, core.source.unsupported-
  code-page@1 :973.
- Formation diagnostic emission: crates/consema-ini/src/parser.rs:1158-1195
  (category per code, severity Error when recovered, occurrence ordinal)
  and the deterministic sort (Diagnostic::sort_deterministically,
  consema-core/src/diagnostic.rs:107-123).
- Projection failure code mapping: crates/consema-ini/src/projection.rs:
  886-893 (RecoveredDocument -> ini.projection.incomplete-document@1,
  Collision -> ini.projection.collision@1, ResourceLimit ->
  core.projection.resource-limit@1, CoreInvariant ->
  core.projection.target-not-applicable@1) and the failed-attempt
  arguments projection.rs:852-884 (reason, limit, profile).
- Edit failure code mapping: crates/consema-ini/src/edit.rs:1754-1779
  (every EditFailure variant; see the module docstring of
  consema.ini.edit).
- Query failures reuse the common core.query.*@1 codes
  (error_registry.rs:108-118) through consema.protocol.query.QueryFailure
  — no new type is needed.

Design: the INI family raises typed exceptions whose stable ``code`` is the
registered code (RFC 0016 §6). Error text is human presentation only and
never participates in conformance comparison. The vector-facing failure
*names* ("RecoveredDocument", "Collision", ...) are exposed as ``name``
properties using the exact Rust variant spellings that the conformance
vectors reference (conformance/vectors/ini-v1.json:42, 67, 86).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory

# ---------------------------------------------------------------------------
# SDK-internal diagnostic record (mirror of consema_core::Diagnostic)
# ---------------------------------------------------------------------------


class IniSeverity(enum.Enum):
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
class IniDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code; category, severity,
    primary span, arguments, related locations, and the stable occurrence
    ordinal follow the core record shape (RFC 0011 §8). This SDK-internal
    record is distinct from the protocol-layer ``core.diagnostic@1``
    transfer record (consema.protocol.diagnostic.Diagnostic), which binds
    caller-supplied source IDs.
    """

    code: str
    category: DiagnosticCategory
    severity: IniSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)
    related: tuple[RelatedLocation, ...] = field(default_factory=tuple, repr=False)
    notes: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (diagnostic.rs:107-123).

        Missing primary sorts last; the None-in-tuple comparison trap is
        avoided with an explicit sentinel.
        """
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[IniDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)
    (diagnostic.rs:107-123)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures
# ---------------------------------------------------------------------------


class IniFormationFailureKind(enum.Enum):
    """Fatal formation failure categories (FatalFormationFailure of
    consema-document; the resource names follow the Rust spellings used by
    parser.rs and pinned by conformance/vectors/ini-v1.json:108-128)."""

    SOURCE_BYTES = "source-bytes"
    TOKEN_COUNT = "token-count"
    NODE_COUNT = "node-count"
    NESTING_DEPTH = "nesting-depth"
    DIAGNOSTICS = "diagnostics"
    DECODED_UTF8_BYTES = "decoded-utf8-bytes"
    DECODED_SCALARS = "decoded-scalars"
    PHYSICAL_LINES = "physical-lines"
    PHYSICAL_LINE_BYTES = "physical-line-bytes"
    PHYSICAL_LINE_SCALARS = "physical-line-scalars"
    LOGICAL_LINES = "logical-lines"
    LOGICAL_LINE_BYTES = "logical-line-bytes"
    LOGICAL_LINE_SCALARS = "logical-line-scalars"
    CONTINUATION_LINES = "continuation-lines"
    SECTIONS = "sections"
    ENTRIES = "entries"
    DUPLICATE_GROUP_MEMBERS = "duplicate-group-members"
    RECOVERY_REGIONS = "recovery-regions"
    SYNTAX_PIECES = "syntax-pieces"
    PROFILE_ENCODING = "profile-encoding"
    SOURCE = "source"


class IniFormationFailure(Exception):
    """Fatal formation failure; no Document exists.

    Exceeding a configured limit is fatal with no truncation-then-success
    (RFC 0016 §6; RFC 0009 §13, docs/rfcs/0009-...:476-489); an invalid or
    profile-conflicting encoding is likewise fatal before a Document exists
    (RFC 0009 §3, docs/rfcs/0009-...:68-73; parser.rs:22-32, 61-94). The
    frozen codes are core.parse.resource-limit@1 (error_registry.rs:39),
    ini.profile.encoding@1 (error_registry.rs:1063), or the wrapped
    source-layer code (core.source.*@1).
    """

    def __init__(
        self,
        kind: IniFormationFailureKind,
        *,
        resource_name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        source=None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.resource_name = resource_name
        self.observed = observed
        self.limit = limit
        self.source = source

    @property
    def name(self) -> str:
        """The exact resource-name or failure spelling the conformance
        vectors reference (ini-v1.json:109-127)."""
        if self.kind is IniFormationFailureKind.PROFILE_ENCODING:
            return "profile-encoding"
        if self.kind is IniFormationFailureKind.SOURCE:
            assert self.source is not None
            return self.source.kind.value
        return self.kind.value

    @property
    def code(self) -> str:
        if self.kind is IniFormationFailureKind.PROFILE_ENCODING:
            return "ini.profile.encoding@1"
        if self.kind is IniFormationFailureKind.SOURCE:
            assert self.source is not None
            return self.source.code
        return "core.parse.resource-limit@1"

    def __str__(self) -> str:
        if self.kind is IniFormationFailureKind.PROFILE_ENCODING:
            return "INI source encoding conflicts with the selected profile"
        if self.kind is IniFormationFailureKind.SOURCE:
            return f"INI source failure: {self.source}"
        return (
            f"INI formation limit {self.resource_name or self.kind.value}: "
            f"observed {self.observed} > limit {self.limit}"
        )


# ---------------------------------------------------------------------------
# Projection failures
# ---------------------------------------------------------------------------


class IniProjectionFailureKind(enum.Enum):
    """Stable projection failure categories (projection.rs:272-286)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    COLLISION = "Collision"
    RESOURCE_LIMIT = "ResourceLimit"
    CORE_INVARIANT = "CoreInvariant"


class IniProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping authority: projection.rs:886-893. ``name`` is the exact
    Rust variant spelling the conformance vectors reference
    (ini-v1.json:67 "rejects" via the Collision diagnostic reason).
    """

    def __init__(
        self,
        kind: IniProjectionFailureKind,
        *,
        container=None,
        name_value: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.container = container
        self.name_value = name_value
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _PROJECTION_CODES[self.kind]

    @property
    def reason(self) -> str:
        """Stable failed-attempt ``reason`` argument (projection.rs:861-868)."""
        return {
            IniProjectionFailureKind.RECOVERED_DOCUMENT: "incomplete-document",
            IniProjectionFailureKind.COLLISION: "collision",
            IniProjectionFailureKind.RESOURCE_LIMIT: "resource-limit",
            IniProjectionFailureKind.CORE_INVARIANT: "target-not-applicable",
        }[self.kind]


_PROJECTION_CODES = {
    IniProjectionFailureKind.RECOVERED_DOCUMENT: "ini.projection.incomplete-document@1",
    IniProjectionFailureKind.COLLISION: "ini.projection.collision@1",
    IniProjectionFailureKind.RESOURCE_LIMIT: "core.projection.resource-limit@1",
    IniProjectionFailureKind.CORE_INVARIANT: "core.projection.target-not-applicable@1",
}


# ---------------------------------------------------------------------------
# Edit failures
# ---------------------------------------------------------------------------


class IniEditFailureKind(enum.Enum):
    """Stable edit failure categories (edit.rs:260-303)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    DUPLICATE_TARGET = "DuplicateTarget"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    ANCESTOR_DESCENDANT_CONFLICT = "AncestorDescendantConflict"
    PLACEMENT_ANCHOR_REMOVED = "PlacementAnchorRemoved"
    TARGET_NOT_FOUND = "TargetNotFound"
    INVALID_PLACEMENT = "InvalidPlacement"
    INVALID_NAME = "InvalidName"
    NAME_COLLISION = "NameCollision"
    INVALID_KEY = "InvalidKey"
    DUPLICATE_KEY = "DuplicateKey"
    KEY_COLLISION = "KeyCollision"
    REPRESENTATION_INCOMPATIBLE = "RepresentationIncompatible"
    EXACT_LITERAL_REQUIRES_LITERAL_OPERATION = "ExactLiteralRequiresLiteralOperation"
    UNREPRESENTABLE_VALUE = "UnrepresentableValue"
    ENCODING_UNREPRESENTABLE = "EncodingUnrepresentable"
    INVALID_LITERAL = "InvalidLiteral"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


class IniEditFailure(Exception):
    """Stable edit failure with a frozen registered code.

    Code mapping authority: edit.rs:1754-1779 (RFC 0004 §17). ``name`` is
    the exact Rust variant spelling referenced by the conformance vectors
    (ini-v1.json:105 "wrong_snapshot_code").
    """

    def __init__(
        self,
        kind: IniEditFailureKind,
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
    IniEditFailureKind.RECOVERED_DOCUMENT: "core.edit.incomplete-target@1",
    IniEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    IniEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    IniEditFailureKind.DUPLICATE_TARGET: "core.edit.conflicting-edits@1",
    IniEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT: "core.edit.conflicting-edits@1",
    IniEditFailureKind.PLACEMENT_ANCHOR_REMOVED: "core.edit.conflicting-edits@1",
    IniEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    IniEditFailureKind.INVALID_PLACEMENT: "ini.edit.invalid-placement@1",
    IniEditFailureKind.INVALID_NAME: "ini.edit.invalid-name@1",
    IniEditFailureKind.NAME_COLLISION: "core.edit.duplicate-key@1",
    IniEditFailureKind.INVALID_KEY: "ini.edit.invalid-name@1",
    IniEditFailureKind.DUPLICATE_KEY: "core.edit.duplicate-key@1",
    IniEditFailureKind.KEY_COLLISION: "ini.edit.case-collision@1",
    IniEditFailureKind.REPRESENTATION_INCOMPATIBLE: "core.edit.representation-incompatible@1",
    IniEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION: (
        "core.edit.exact-literal-requires-literal@1"
    ),
    IniEditFailureKind.UNREPRESENTABLE_VALUE: "core.edit.unsupported-value@1",
    IniEditFailureKind.ENCODING_UNREPRESENTABLE: "core.edit.representation-incompatible@1",
    IniEditFailureKind.INVALID_LITERAL: "core.edit.invalid-literal@1",
    IniEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    IniEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}
