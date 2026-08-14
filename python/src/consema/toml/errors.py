"""Typed TOML family errors with frozen registered codes.

Authority (language-neutral first; Rust only for registry arbitration):

- The four toml-family codes are frozen by
  https://github.com/consema/consema-rs/blob/main/consema-protocol/src/error_registry.rs
  ``toml.edit.representation-fallback@1`` :339,
  ``toml.parse.syntax@1`` :345,
  ``toml.projection.core-invariant@1`` :351,
  ``toml.projection.unrepresentable-datetime@1`` :357.
- The core codes consumed by the TOML family are registered at
  error_registry.rs — core.parse.resource-limit@1,
  core.projection.resource-limit@1, the core.query.*@1 family and the
  core.edit.*@1 family (code names are the anchors; the edit codes were
  introduced by RFC 0004 §17, https://github.com/consema/consema/blob/main/docs/rfcs/0004-materialization-conversion-and-structural-edit-v1.md)
- The edit failure vocabulary follows https://github.com/consema/consema-rs/blob/main/consema-toml/src/edit.rs
  and its code mapping edit.rs (StableFailure impl).
- The projection failure vocabulary follows
  https://github.com/consema/consema-rs/blob/main/consema-toml/src/projection.rs and the diagnostic mapping
  projection.rs.
- Diagnostic categories follow the eleven frozen semantic categories
  (https://github.com/consema/consema-rs/blob/main/consema-protocol/src/error_registry.rs; Python
  consema.protocol DiagnosticCategory). Severity follows the three frozen
  presentation severities (protocol diagnostic.rs).
- RFC 0016 §6 (https://github.com/consema/consema/blob/main/docs/rfcs/0016-go-api-mapping-v1.md): SDK operations
  return typed errors whose stable code is always the registered code; error
  text is human presentation only and never participates in conformance
  comparison.

Design note: formation and projection/edit failures are snapshot-bound, so
they carry a ``TomlDiagnostic`` record whose primary location is a
snapshot-bound ``Span`` (the Rust ``DiagnosticLocation`` with a snapshot
identity; https://github.com/consema/consema-rs/blob/main/consema-core/src/diagnostic.rs). The wire record
``core.diagnostic@1`` (consema.protocol Diagnostic) instead requires a
caller-stable source id, so the two surfaces stay distinct here. If the
protocol agent later publishes a snapshot-bound location variant, this
record should be promoted to the shared module.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory
from consema.protocol.diagnostic import Severity


class TomlFormationFailureKind(enum.Enum):
    """Closed formation failure category (https://github.com/consema/consema-rs/blob/main/consema-toml parser.rs
    and consema-document FatalFormationFailure)."""

    SYNTAX = "syntax"
    RESOURCE_LIMIT = "resource-limit"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class TomlDiagnostic:
    """One snapshot-bound diagnostic of the TOML family.

    Mirrors the frozen code/category/severity/primary/arguments/occurrence
    record shape of ``core.diagnostic@1`` (RFC 0015; protocol diagnostic.py)
    with a snapshot-bound primary location (Rust DiagnosticLocation carries
    ``snapshot: Option<SnapshotIdentity>``;
    https://github.com/consema/consema-rs/blob/main/consema-core/src/diagnostic.rs).
    The vector suite references only the code
    (conformance/vectors/toml-v1.json ``"toml.parse.syntax@1"``) and the
    argument names used by the resource-limit records ("name", "observed",
    "limit"; parser.rs).
    """

    code: str
    category: DiagnosticCategory
    severity: Severity
    primary: Span | None = None
    arguments: dict[str, str] = field(default_factory=dict)
    occurrence: int = 0


class TomlFormationFailure(Exception):
    """Fatal formation failure; no document is ever returned
    (RFC 0001 §3, https://github.com/consema/consema/blob/main/docs/rfcs/0001-toml-1.0-profile.md: formation runs
    max_source_bytes, UTF-8 validation, TOML syntax/semantic validation,
    then max_token_count / max_node_count / max_nesting_depth; any limit hit
    is a fatal resource-limit failure; syntax failure carries the backend-
    provable minimal span and stable arguments).

    Code mapping: ``toml.parse.syntax@1`` (error_registry.rs) for
    syntax/semantic failures; ``core.parse.resource-limit@1``
    (error_registry.rs) for every resource-limit failure
    (RFC 0001 §3: any resource limit hit returns the clear
    core.parse.resource-limit@1 code); source failures delegate to the
    wrapped core.source.* code.
    """

    def __init__(self, diagnostics: list[TomlDiagnostic], source=None) -> None:
        self.diagnostics: tuple[TomlDiagnostic, ...] = tuple(diagnostics)
        self.source = source
        super().__init__(self.diagnostics[0].code if self.diagnostics else "formation-failed")

    @classmethod
    def syntax(cls, span: Span | None, reason: str) -> TomlFormationFailure:
        """One syntax failure carrying the minimal provable span and the
        stable ``parser_reason`` argument (parser.rs)."""
        diagnostic = TomlDiagnostic(
            code="toml.parse.syntax@1",
            category=DiagnosticCategory.SYNTAX,
            severity=Severity.ERROR,
            primary=span,
            arguments={"parser_reason": reason},
            occurrence=0,
        )
        return cls([diagnostic])

    @classmethod
    def resource_limit(cls, name: str, observed: int, limit: int) -> TomlFormationFailure:
        """One fatal resource-limit failure with stable arguments
        (parser.rs)."""
        diagnostic = TomlDiagnostic(
            code="core.parse.resource-limit@1",
            category=DiagnosticCategory.RESOURCE,
            severity=Severity.ERROR,
            primary=None,
            arguments={
                "name": name,
                "observed": str(observed),
                "limit": str(limit),
            },
            occurrence=0,
        )
        return cls([diagnostic])

    @property
    def code(self) -> str:
        """The frozen registered failure code (RFC 0016 §6)."""
        return self.diagnostics[0].code

    def __str__(self) -> str:
        return f"{self.code}: {self.diagnostics[0].arguments}"


class TomlProjectionFailureKind(enum.Enum):
    """Stable projection failure category
    (https://github.com/consema/consema-rs/blob/main/consema-toml/src/projection.rs)."""

    UNREPRESENTABLE_DATETIME = "unrepresentable-datetime"
    RESOURCE_LIMIT = "resource-limit"
    CORE_INVARIANT = "core-invariant"


_CODE_BY_PROJECTION_KIND = {
    TomlProjectionFailureKind.UNREPRESENTABLE_DATETIME: "toml.projection.unrepresentable-datetime@1",
    TomlProjectionFailureKind.RESOURCE_LIMIT: "core.projection.resource-limit@1",
    TomlProjectionFailureKind.CORE_INVARIANT: "toml.projection.core-invariant@1",
}


class TomlProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping: projection.rs — UnrepresentableDateTime →
    toml.projection.unrepresentable-datetime@1 (error_registry.rs),
    ResourceLimit → core.projection.resource-limit@1 (error_registry.rs)
    with the ``limit`` argument, CoreInvariant →
    toml.projection.core-invariant@1 (error_registry.rs).
    """

    def __init__(
        self,
        kind: TomlProjectionFailureKind,
        *,
        limit_name: str | None = None,
        span: Span | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.limit_name = limit_name
        self.span = span

    @property
    def code(self) -> str:
        return _CODE_BY_PROJECTION_KIND[self.kind]

    def to_diagnostic(self) -> TomlDiagnostic:
        """One snapshot-bound failure diagnostic (projection.rs)."""
        category = (
            DiagnosticCategory.PROJECTION
            if self.kind is not TomlProjectionFailureKind.RESOURCE_LIMIT
            else DiagnosticCategory.RESOURCE
        )
        arguments: dict[str, str] = {}
        if self.kind is TomlProjectionFailureKind.RESOURCE_LIMIT:
            arguments["limit"] = self.limit_name or ""
        return TomlDiagnostic(
            code=self.code,
            category=category,
            severity=Severity.ERROR,
            primary=self.span,
            arguments=arguments,
            occurrence=0,
        )

    def __str__(self) -> str:
        return f"{self.code}: {self.kind.value}"


class TomlEditFailureKind(enum.Enum):
    """Stable TOML edit validation or commit failure
    (https://github.com/consema/consema-rs/blob/main/consema-toml/src/edit.rs, transcribed verbatim)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    UNSUPPORTED_SEMANTIC_VALUE = "UnsupportedSemanticValue"
    INVALID_LITERAL = "InvalidLiteral"
    REPRESENTATION_INCOMPATIBLE = "RepresentationIncompatible"
    EXACT_LITERAL_REQUIRES_LITERAL = "ExactLiteralRequiresLiteralOperation"
    CONFLICTING_EDITS = "ConflictingEdits"
    DUPLICATE_TARGET = "DuplicateTarget"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    ANCESTOR_DESCENDANT_CONFLICT = "AncestorDescendantConflict"
    PLACEMENT_ANCHOR_REMOVED = "PlacementAnchorRemoved"
    TARGET_NOT_FOUND = "TargetNotFound"
    DUPLICATE_KEY = "DuplicateKey"
    UNSUPPORTED_OPERATION = "UnsupportedOperation"
    UNREPRESENTABLE_VALUE = "UnrepresentableValue"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


_CODE_BY_EDIT_KIND = {
    TomlEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    TomlEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE: "core.edit.unsupported-value@1",
    TomlEditFailureKind.INVALID_LITERAL: "core.edit.invalid-literal@1",
    TomlEditFailureKind.REPRESENTATION_INCOMPATIBLE: "core.edit.representation-incompatible@1",
    TomlEditFailureKind.EXACT_LITERAL_REQUIRES_LITERAL: "core.edit.exact-literal-requires-literal@1",
    TomlEditFailureKind.CONFLICTING_EDITS: "core.edit.conflicting-edits@1",
    TomlEditFailureKind.DUPLICATE_TARGET: "core.edit.conflicting-edits@1",
    TomlEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    TomlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT: "core.edit.conflicting-edits@1",
    TomlEditFailureKind.PLACEMENT_ANCHOR_REMOVED: "core.edit.conflicting-edits@1",
    TomlEditFailureKind.TARGET_NOT_FOUND: "core.edit.target-not-found@1",
    TomlEditFailureKind.DUPLICATE_KEY: "core.edit.duplicate-key@1",
    TomlEditFailureKind.UNSUPPORTED_OPERATION: "core.edit.operation-unsupported@1",
    TomlEditFailureKind.UNREPRESENTABLE_VALUE: "core.edit.unsupported-value@1",
    TomlEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    TomlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}


class TomlEditFailure(Exception):
    """Stable TOML edit validation or commit failure with a frozen
    registered code (RFC 0004 §13; the code mapping is the Rust
    StableFailure impl, edit.rs; registry lines in the module
    docstring). The failure kind spellings are the exact Rust variant
    names (RFC 0016 §5.3/§8: one vocabulary per code)."""

    def __init__(
        self,
        kind: TomlEditFailureKind,
        *,
        value_kind: str | None = None,
        limit_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.value_kind = value_kind
        self.limit_name = limit_name

    @property
    def code(self) -> str:
        return _CODE_BY_EDIT_KIND[self.kind]

    def __str__(self) -> str:
        return f"{self.code}: {self.kind.value}"
