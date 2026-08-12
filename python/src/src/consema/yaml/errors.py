"""Typed YAML-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

Frozen code names with authority citations (all registry spellings are
transcribed verbatim from crates/consema-protocol/src/error_registry.rs):

- yaml.profile.version-directive@1 error_registry.rs:856;
- yaml.parse.syntax@1 error_registry.rs:850;
- yaml.anchor.unknown@1 error_registry.rs:748; yaml.alias.name-mismatch@1
  error_registry.rs:730; yaml.alias.name-unavailable@1 error_registry.rs:736;
  yaml.anchor.name-unavailable@1 error_registry.rs:742;
- yaml.native.invalid-source-span@1 :820; yaml.native.trailing-events@1 :826;
  yaml.native.trailing-named-occurrence@1 :832; yaml.native.unexpected-end@1
  :838; yaml.native.unexpected-event@1 :844;
- yaml.mapping.missing-value@1 :790; yaml.scalar.invalid-explicit-tag@1 :922;
  yaml.tag.kind-mismatch@1 :928;
- yaml.projection.cycle@1 :862; yaml.projection.document-cardinality@1 :868;
  yaml.projection.graph-invalid@1 :874; yaml.projection.invalid-canonical-
  scalar@1 :880; yaml.projection.mapping-not-object@1 :886;
  yaml.projection.provenance-limit@1 :892; yaml.projection.resource-limit@1
  :898; yaml.projection.sharing@1 :904; yaml.projection.unrepresentable-
  timestamp@1 :910; yaml.projection.unsupported-tag@1 :916;
- yaml.materialization.cross-document-sharing@1 :796;
  yaml.materialization.round-trip-mismatch@1 :802;
  yaml.materialization.tag-kind-mismatch@1 :808;
  yaml.materialization.unsupported-tag@1 :814;
- yaml.edit.anchor-dependency@1 :754; yaml.edit.anchor-not-visible@1 :760;
  yaml.edit.canonical-fallback@1 :766; yaml.edit.invalid-anchor-name@1 :772;
  yaml.edit.invalid-placement@1 :778; yaml.edit.structural-container-
  conflict@1 :784.

The common edit/materialization/query failures reuse the core codes:
core.edit.*@1 (RFC 0004 §17, error_registry.rs:388-410), the fatal formation
code core.parse.resource-limit@1 (error_registry.rs:39), and core.query.*@1
(error_registry.rs:108-118, raised through consema.protocol.query.QueryFailure).

Failure-code mappings for the YAML operations are the Rust StableFailure
impls: edit.rs:318-343, projection.rs:174-183 and 480-497,
materialization.rs:143-152, lib.rs backend_failure 833-858.

Design: the family raises typed exceptions whose stable ``code`` is the
registered code (RFC 0016 §6). Error text is human presentation only and
never participates in conformance comparison.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory


class YamlSeverity(enum.Enum):
    """The three frozen presentation severities (consema_core::Diagnostic)."""

    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    """One related source location with its stable relationship role."""

    role: str
    location: Span


@dataclass(frozen=True, slots=True)
class YamlDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code; category, severity,
    primary span, arguments, and the stable occurrence ordinal follow the
    core record shape. This SDK-internal record is distinct from the
    protocol-layer ``core.diagnostic@1`` transfer record.
    """

    code: str
    category: DiagnosticCategory
    severity: YamlSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (consema-core diagnostic.rs:107-123)."""
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[YamlDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures (RFC 0007 §4)
# ---------------------------------------------------------------------------


class YamlFormationFailureKind(enum.Enum):
    """Fatal formation failure categories (FatalFormationFailure of
    consema-document; resource names follow the Rust spellings used by
    lib.rs:266-272, 415-427 and backend.rs:147-156)."""

    SOURCE_BYTES = "source-bytes"
    TOKEN_COUNT = "token-count"
    NESTING_DEPTH = "nesting-depth"
    NODE_COUNT = "node-count"
    INVALID_UTF8 = "invalid-utf8"
    PROFILE_VERSION = "profile-version"
    SYNTAX = "syntax"
    SEMANTIC = "semantic"


class YamlFormationFailure(Exception):
    """Fatal formation failure; no Document exists (RFC 0007 §4: decoding
    failure, source-size overflow, or allocation/host-size overflow is fatal
    and returns no Document).

    Exceeding a configured limit is fatal with no truncation-then-success
    (RFC 0016 §6). The frozen codes are core.parse.resource-limit@1
    (error_registry.rs:39), core.source.invalid-utf8@1 (:207),
    yaml.parse.syntax@1 (:850), yaml.profile.version-directive@1 (:856),
    and the semantic formation codes of lib.rs/native.rs.
    """

    def __init__(
        self,
        kind: YamlFormationFailureKind,
        *,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        valid_up_to: int | None = None,
        code: str | None = None,
        arguments: dict[str, str] | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.name = name
        self.observed = observed
        self.limit = limit
        self.valid_up_to = valid_up_to
        self._code = code
        self.arguments = arguments or {}

    @property
    def code(self) -> str:
        if self._code is not None:
            return self._code
        if self.kind is YamlFormationFailureKind.INVALID_UTF8:
            return "core.source.invalid-utf8@1"
        if self.kind is YamlFormationFailureKind.SYNTAX:
            return "yaml.parse.syntax@1"
        if self.kind is YamlFormationFailureKind.PROFILE_VERSION:
            return "yaml.profile.version-directive@1"
        return "core.parse.resource-limit@1"


def resource_limit_failure(
    name: str, observed: int, limit: int
) -> YamlFormationFailure:
    """Fatal resource-limit failure (lib.rs:266-272, 415-427)."""
    return YamlFormationFailure(
        YamlFormationFailureKind.SOURCE_BYTES
        if name == "source-bytes"
        else YamlFormationFailureKind.NODE_COUNT
        if name == "native-nodes"
        else YamlFormationFailureKind.NESTING_DEPTH
        if name == "nesting-depth"
        else YamlFormationFailureKind.TOKEN_COUNT,
        name=name,
        observed=observed,
        limit=limit,
    )


def semantic_failure(code: str) -> YamlFormationFailure:
    """Fatal composition failure with an exact registered semantic code
    (native.rs:1148-1157)."""
    return YamlFormationFailure(
        YamlFormationFailureKind.SEMANTIC, code=code
    )


# ---------------------------------------------------------------------------
# Projection failures (RFC 0007 §10; projection.rs)
# ---------------------------------------------------------------------------


class YamlProjectionFailureKind(enum.Enum):
    """Value-projection failure categories (projection.rs:436-476)."""

    DOCUMENT_CARDINALITY = "document-cardinality"
    CYCLE = "cycle"
    SHARING = "sharing"
    UNSUPPORTED_TAG = "unsupported-tag"
    MAPPING_NOT_OBJECT = "mapping-not-object"
    INVALID_CANONICAL_SCALAR = "invalid-canonical-scalar"
    UNREPRESENTABLE_TIMESTAMP = "unrepresentable-timestamp"
    RESOURCE_LIMIT = "resource-limit"


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


class YamlProjectionFailure(Exception):
    """Value-projection failure; no partial value or provenance is returned
    (projection.rs:436-497; RFC 0007 §10: failure carries no PortableGraph/
    PortableValue and no partial provenance)."""

    def __init__(
        self,
        kind: YamlProjectionFailureKind,
        *,
        resource_name: str | None = None,
        tag: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.resource_name = resource_name
        self.tag = tag

    @property
    def code(self) -> str:
        return _CODE_BY_PROJECTION_KIND[self.kind]


class YamlGraphProjectionErrorKind(enum.Enum):
    """Exact graph projection failure categories (native.rs:97-103,
    projection.rs:154-161)."""

    UNSUPPORTED_TAG = "unsupported-tag"
    GRAPH = "graph"
    PROVENANCE_LIMIT = "provenance-limit"


_CODE_BY_GRAPH_ERROR_KIND = {
    YamlGraphProjectionErrorKind.UNSUPPORTED_TAG: "yaml.projection.unsupported-tag@1",
    YamlGraphProjectionErrorKind.PROVENANCE_LIMIT: "yaml.projection.provenance-limit@1",
}


class YamlGraphProjectionError(Exception):
    """Exact graph projection failure (projection.rs:154-202)."""

    def __init__(
        self,
        kind: YamlGraphProjectionErrorKind,
        *,
        tag: str | None = None,
        graph_message: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.tag = tag
        self.graph_message = graph_message

    @property
    def code(self) -> str:
        if self.kind is YamlGraphProjectionErrorKind.GRAPH:
            # GraphBuildError::ResourceLimit/SizeOverflow map to
            # resource-limit; every other graph failure is graph-invalid
            # (projection.rs:176-181).
            if self.graph_message and "resource" in self.graph_message:
                return "yaml.projection.resource-limit@1"
            return "yaml.projection.graph-invalid@1"
        return _CODE_BY_GRAPH_ERROR_KIND[self.kind]


# ---------------------------------------------------------------------------
# Materialization failures (RFC 0007 §11; materialization.rs)
# ---------------------------------------------------------------------------


class YamlGraphMaterializationFailureKind(enum.Enum):
    """Graph materialization failure categories (materialization.rs:87-111)."""

    MATERIALIZATION = "materialization"
    UNSUPPORTED_TAG = "unsupported-tag"
    TAG_KIND_MISMATCH = "tag-kind-mismatch"
    CROSS_DOCUMENT_SHARING = "cross-document-sharing"
    ROUND_TRIP_MISMATCH = "round-trip-mismatch"


_CODE_BY_GRAPH_MATERIALIZATION_KIND = {
    YamlGraphMaterializationFailureKind.UNSUPPORTED_TAG: "yaml.materialization.unsupported-tag@1",
    YamlGraphMaterializationFailureKind.TAG_KIND_MISMATCH: "yaml.materialization.tag-kind-mismatch@1",
    YamlGraphMaterializationFailureKind.CROSS_DOCUMENT_SHARING: (
        "yaml.materialization.cross-document-sharing@1"
    ),
    YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH: "yaml.materialization.round-trip-mismatch@1",
}


class YamlGraphMaterializationFailure(Exception):
    """Stable PortableGraph-to-YAML materialization failure
    (materialization.rs:87-152)."""

    def __init__(
        self,
        kind: YamlGraphMaterializationFailureKind,
        *,
        tag: str | None = None,
        node: object = None,
        materialization_code: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.tag = tag
        self.node = node
        self.materialization_code = materialization_code

    @property
    def code(self) -> str:
        if self.kind is YamlGraphMaterializationFailureKind.MATERIALIZATION:
            return self.materialization_code or "core.materialization.formation-failed@1"
        return _CODE_BY_GRAPH_MATERIALIZATION_KIND[self.kind]


# ---------------------------------------------------------------------------
# Edit failures (RFC 0007 §12; edit.rs:318-343)
# ---------------------------------------------------------------------------


class YamlEditFailureKind(enum.Enum):
    """Stable YAML edit failure categories (edit.rs:275-314)."""

    WRONG_SNAPSHOT = "wrong-snapshot"
    WRONG_ROLE = "wrong-role"
    TARGET_NOT_FOUND = "target-not-found"
    INCOMPLETE_TARGET = "incomplete-target"
    UNSUPPORTED_SEMANTIC_VALUE = "unsupported-semantic-value"
    INVALID_LITERAL = "invalid-literal"
    REPRESENTATION_INCOMPATIBLE = "representation-incompatible"
    EXACT_LITERAL_REQUIRES_LITERAL_OPERATION = "exact-literal-requires-literal-operation"
    INVALID_ANCHOR_NAME = "invalid-anchor-name"
    INVALID_PLACEMENT = "invalid-placement"
    ANCHOR_NOT_VISIBLE = "anchor-not-visible"
    ANCHOR_DEPENDENCY = "anchor-dependency"
    UNSUPPORTED_INSERTED_VALUE = "unsupported-inserted-value"
    STRUCTURAL_CONTAINER_CONFLICT = "structural-container-conflict"
    DUPLICATE_TARGET = "duplicate-target"
    OVERLAPPING_OWNERSHIP = "overlapping-ownership"
    ANCESTOR_DESCENDANT_CONFLICT = "ancestor-descendant-conflict"
    RESOURCE_LIMIT = "resource-limit"
    NEW_DOCUMENT_FORMATION_FAILED = "new-document-formation-failed"


_CODE_BY_EDIT_KIND = {
    YamlEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    YamlEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    YamlEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    YamlEditFailureKind.INCOMPLETE_TARGET: "core.edit.incomplete-target@1",
    YamlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE: "core.edit.unsupported-value@1",
    YamlEditFailureKind.UNSUPPORTED_INSERTED_VALUE: "core.edit.unsupported-value@1",
    YamlEditFailureKind.INVALID_LITERAL: "core.edit.invalid-literal@1",
    YamlEditFailureKind.REPRESENTATION_INCOMPATIBLE: "core.edit.representation-incompatible@1",
    YamlEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL_OPERATION: (
        "core.edit.exact-literal-requires-literal@1"
    ),
    YamlEditFailureKind.INVALID_ANCHOR_NAME: "yaml.edit.invalid-anchor-name@1",
    YamlEditFailureKind.INVALID_PLACEMENT: "yaml.edit.invalid-placement@1",
    YamlEditFailureKind.ANCHOR_NOT_VISIBLE: "yaml.edit.anchor-not-visible@1",
    YamlEditFailureKind.ANCHOR_DEPENDENCY: "yaml.edit.anchor-dependency@1",
    YamlEditFailureKind.STRUCTURAL_CONTAINER_CONFLICT: "yaml.edit.structural-container-conflict@1",
    YamlEditFailureKind.DUPLICATE_TARGET: "core.edit.conflicting-edits@1",
    YamlEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    YamlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT: "core.edit.conflicting-edits@1",
    YamlEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    YamlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}


class YamlEditFailure(Exception):
    """Stable YAML edit validation or commit failure (edit.rs:275-343)."""

    def __init__(
        self,
        kind: YamlEditFailureKind,
        *,
        resource_name: str | None = None,
        value_kind: object = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.resource_name = resource_name
        self.value_kind = value_kind

    @property
    def code(self) -> str:
        return _CODE_BY_EDIT_KIND[self.kind]
