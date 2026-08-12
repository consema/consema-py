"""Typed JSON-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

Frozen code names with authority citations (all registry spellings are
transcribed from crates/consema-protocol/src/error_registry.rs; the format
failure enums and their code mappings are the Rust family's StableFailure
impls):

- JSON syntax/conformance/semantic diagnostic codes: error_registry.rs:213
  (json.edit.representation-fallback@1), :219 (json.object.duplicate-
  member@1), :225 (json.projection.duplicate-keys@1), :231
  (json.projection.semantic-unavailable@1), :237 (json.strict.comment-not-
  allowed@1), :243 (json.strict.leading-bom@1), :249 (json.strict.trailing-
  comma@1), :255-333 (the json.syntax.*@1 family), :610
  (json.projection.structure-reencoded@1), :649 (json5.string.unescaped-
  line-separator@1), :655 (json5.syntax.invalid-identifier@1), :1332
  (json.projection.incomplete-document@1).
- Fatal formation codes: core.parse.resource-limit@1 error_registry.rs:39;
  core.source.invalid-utf8@1 error_registry.rs:207.
- Projection failure code mapping: crates/consema-json/src/projection.rs:754-765.
- Edit failure code mapping: crates/consema-json/src/edit.rs:1299-1323
  (RFC 0004 §17, docs/rfcs/0004-...:386-423).
- Query failures reuse the common core.query.*@1 codes (error_registry.rs:108-118)
  through consema.protocol.query.QueryFailure — no new type is needed.
- Diagnostic ordering: Diagnostic::sort_deterministically,
  crates/consema-core/src/diagnostic.rs:107-123 (primary start, category,
  code, occurrence; missing primary sorts last).

Design: the JSON family raises typed exceptions whose stable ``code`` is the
registered code (RFC 0016 §6). Error text is human presentation only and
never participates in conformance comparison. The vector-facing failure
*names* ("TargetNotFound", "Unrepresentable", ...) are exposed as ``name``
properties using the exact Rust variant spellings that the conformance
vectors reference (conformance/vectors/json-family-v2.json:183,
:147, :153).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory

# ---------------------------------------------------------------------------
# SDK-internal diagnostic record (mirror of consema_core::Diagnostic)
# ---------------------------------------------------------------------------


class JsonSeverity(enum.Enum):
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
class JsonDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code; category, severity,
    primary span, arguments, related locations, and the stable occurrence
    ordinal follow the core record shape (RFC 0011 §8: diagnostics validate
    under the selected error registry). This SDK-internal record is distinct
    from the protocol-layer ``core.diagnostic@1`` transfer record
    (consema.protocol.diagnostic.Diagnostic), which binds caller-supplied
    source IDs.
    """

    code: str
    category: DiagnosticCategory
    severity: JsonSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)
    related: tuple[RelatedLocation, ...] = field(default_factory=tuple, repr=False)
    notes: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (diagnostic.rs:107-123).

        Missing primary sorts last (u64::MAX in Rust; Python's None-in-
        tuple comparison is avoided by using an explicit sentinel).
        """
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[JsonDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)
    (diagnostic.rs:107-123)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures
# ---------------------------------------------------------------------------


class JsonFormationFailureKind(enum.Enum):
    """Fatal formation failure categories (FatalFormationFailure of
    consema-document; the resource names follow the Rust spellings used by
    parser.rs:79-83, 390-394, 832-835, 1180-1184)."""

    SOURCE_BYTES = "source-bytes"
    TOKEN_COUNT = "token-count"
    NESTING_DEPTH = "nesting-depth"
    NODE_COUNT = "node-count"
    INVALID_UTF8 = "invalid-utf8"


class JsonFormationFailure(Exception):
    """Fatal formation failure; no Document exists.

    Exceeding a configured limit is fatal with no truncation-then-success
    (RFC 0016 §6); invalid UTF-8 at the JSON entry point is likewise fatal
    (RFC 0005 §2: JSON5 accepts UTF-8 source only). The frozen code is
    core.parse.resource-limit@1 (error_registry.rs:39) or
    core.source.invalid-utf8@1 (error_registry.rs:207).
    """

    def __init__(
        self,
        kind: JsonFormationFailureKind,
        *,
        observed: int | None = None,
        limit: int | None = None,
        valid_up_to: int | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.name = kind.value
        self.observed = observed
        self.limit = limit
        self.valid_up_to = valid_up_to

    @property
    def code(self) -> str:
        if self.kind is JsonFormationFailureKind.INVALID_UTF8:
            return "core.source.invalid-utf8@1"
        return "core.parse.resource-limit@1"


# ---------------------------------------------------------------------------
# Projection failures
# ---------------------------------------------------------------------------


class JsonProjectionFailureKind(enum.Enum):
    """Stable projection failure categories (projection.rs:328-355)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    CONFLICTING_POLICY_RULES = "ConflictingPolicyRules"
    WRONG_SNAPSHOT_POLICY = "WrongSnapshotPolicy"
    INVALID_POLICY_TARGET = "InvalidPolicyTarget"
    TARGET_NOT_APPLICABLE = "TargetNotApplicable"
    DUPLICATE_KEYS = "DuplicateKeys"
    SEMANTIC_UNAVAILABLE = "SemanticUnavailable"
    RESOURCE_LIMIT = "ResourceLimit"


class JsonProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping authority: projection.rs:754-765. ``name`` is the exact
    Rust variant spelling the conformance vectors reference.
    """

    def __init__(
        self,
        kind: JsonProjectionFailureKind,
        *,
        node=None,
        name_value: str | None = None,
        reason=None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.node = node
        self.name_value = name_value
        self.reason = reason
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _PROJECTION_CODES[self.kind]


_PROJECTION_CODES = {
    JsonProjectionFailureKind.RECOVERED_DOCUMENT: "json.projection.incomplete-document@1",
    JsonProjectionFailureKind.CONFLICTING_POLICY_RULES: "core.projection.conflicting-policy@1",
    JsonProjectionFailureKind.WRONG_SNAPSHOT_POLICY: "core.projection.wrong-snapshot-policy@1",
    JsonProjectionFailureKind.INVALID_POLICY_TARGET: "core.projection.invalid-policy-target@1",
    JsonProjectionFailureKind.TARGET_NOT_APPLICABLE: "core.projection.target-not-applicable@1",
    JsonProjectionFailureKind.DUPLICATE_KEYS: "json.projection.duplicate-keys@1",
    JsonProjectionFailureKind.SEMANTIC_UNAVAILABLE: "json.projection.semantic-unavailable@1",
    JsonProjectionFailureKind.RESOURCE_LIMIT: "core.projection.resource-limit@1",
}


# ---------------------------------------------------------------------------
# Edit failures
# ---------------------------------------------------------------------------


class JsonEditFailureKind(enum.Enum):
    """Stable edit failure categories (edit.rs:260-299)."""

    RECOVERED_DOCUMENT = "RecoveredDocument"
    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    INCOMPLETE_TARGET = "IncompleteTarget"
    SEMANTIC_UNAVAILABLE = "SemanticUnavailable"
    UNSUPPORTED_SEMANTIC_VALUE = "UnsupportedSemanticValue"
    INVALID_LITERAL = "InvalidLiteral"
    REPRESENTATION_INCOMPATIBLE = "RepresentationIncompatible"
    EXACT_LITERAL_REQUIRES_LITERAL_OPERATION = "ExactLiteralRequiresLiteralOperation"
    CONFLICTING_EDITS = "ConflictingEdits"
    DUPLICATE_TARGET = "DuplicateTarget"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    ANCESTOR_DESCENDANT_CONFLICT = "AncestorDescendantConflict"
    PLACEMENT_ANCHOR_REMOVED = "PlacementAnchorRemoved"
    PLACEMENT_ANCHOR_MODIFIED = "PlacementAnchorModified"
    TARGET_NOT_FOUND = "TargetNotFound"
    UNREPRESENTABLE_VALUE = "UnrepresentableValue"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


class JsonEditFailure(Exception):
    """Stable edit failure with a frozen registered code.

    Code mapping authority: edit.rs:1299-1323 (RFC 0004 §17). ``name`` is
    the exact Rust variant spelling referenced by the conformance vectors
    (json-family-v2.json:183 "TargetNotFound").
    """

    def __init__(
        self,
        kind: JsonEditFailureKind,
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
    JsonEditFailureKind.RECOVERED_DOCUMENT: "core.edit.incomplete-target@1",
    JsonEditFailureKind.INCOMPLETE_TARGET: "core.edit.incomplete-target@1",
    JsonEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    JsonEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    JsonEditFailureKind.SEMANTIC_UNAVAILABLE: "core.edit.semantic-unavailable@1",
    JsonEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE: "core.edit.unsupported-value@1",
    JsonEditFailureKind.UNREPRESENTABLE_VALUE: "core.edit.unsupported-value@1",
    JsonEditFailureKind.INVALID_LITERAL: "core.edit.invalid-literal@1",
    JsonEditFailureKind.REPRESENTATION_INCOMPATIBLE: "core.edit.representation-incompatible@1",
    JsonEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION: (
        "core.edit.exact-literal-requires-literal@1"
    ),
    JsonEditFailureKind.CONFLICTING_EDITS: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.DUPLICATE_TARGET: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.PLACEMENT_ANCHOR_REMOVED: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.PLACEMENT_ANCHOR_MODIFIED: "core.edit.conflicting-edits@1",
    JsonEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    JsonEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    JsonEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}
