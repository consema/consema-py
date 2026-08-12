"""Typed HCL-family failures with frozen registered codes, and the
SDK-internal diagnostic record.

The `hcl.*@1` diagnostic codes are registered by RFC 0014 §11 and are part
of the `hcl.native@1` and `hcl.tfvars@1` contracts; they deliberately do not
enter the consema-protocol core error registry, which covers only
core/protocol and line-format contract codes (RFC 0014 §11,
docs/rfcs/0014-...:696-715). The spelling authority for every code below is
the vector suite plus the Rust family's StableFailure impls:

- Parser recovery codes: crates/consema-hcl/src/parser.rs:77-98
  (item/attribute/block/label/expression/directive/newline/separator/
  duplicate-attribute) and lexer.rs:457-487 (byte-order-mark, lone-cr,
  invalid-utf8, identifier, invalid-number, invalid-character,
  invalid-escape, unterminated-comment, unterminated-string,
  unterminated-interpolation, unterminated-directive,
  unterminated-heredoc, heredoc-marker).
- Fatal limit codes: ``hcl.limit.<name>@1`` (parser.rs:2549-2565,
  lexer.rs:2126-2142; the conformance vectors pin
  hcl.limit.expression-depth@1, hcl.limit.body-depth@1,
  hcl.limit.number-digits@1, hcl.limit.attribute-count@1,
  hcl.limit.block-count@1, hcl.limit.body-item-count@1,
  hcl.limit.label-count@1, hcl.limit.template-len@1,
  hcl.limit.heredoc-bytes@1, hcl.limit.tuple-elements@1,
  hcl.limit.object-entries@1).
- Profile gate: hcl.tfvars.block-not-allowed@1 (document.rs:46-48).
- Projection codes: projection.rs:468-476 (incomplete-document,
  non-literal-expression, unrepresentable, resource-limit,
  core-invariant).
- Edit codes: edit.rs:599-611 (hcl.edit.duplicate-attribute@1,
  hcl.edit.block-in-tfvars@1, hcl.edit.unrepresentable@1, plus the shared
  core.edit.*@1 codes).
- Materialization codes: the suite maps the shared MaterializationFailure
  to hcl.materialization.unrepresentable@1 /
  hcl.materialization.resource-limit@1 and the InvalidRequest spelling
  ``"invalid-record"`` (crates/consema-conformance/src/hcl_v1.rs:1611-1616).
- Query failures: the suite maps the shared QueryFailure to the
  hcl.query.*@1 spellings (crates/consema-conformance/src/hcl_v1.rs:658-668;
  hcl.query.type-mismatch@1 for RequiredTypeMismatch,
  hcl.query.non-literal@1 for TargetUnavailable).

Design: the HCL family raises typed exceptions whose stable ``code`` is the
registered code (RFC 0016 §6). Error text is human presentation only and
never participates in conformance comparison.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import Span
from consema.protocol.error_registry import DiagnosticCategory


class HclSeverity(enum.Enum):
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
class HclDiagnostic:
    """One format-layer diagnostic record (mirror of consema_core::Diagnostic).

    The code is always one registered public code (RFC 0014 §11); category,
    severity, primary span, and the stable occurrence ordinal follow the
    core record shape.
    """

    code: str
    category: DiagnosticCategory
    severity: HclSeverity
    primary: Span | None
    occurrence: int = 0
    arguments: dict[str, str] = field(default_factory=dict, repr=False)
    related: tuple[RelatedLocation, ...] = field(default_factory=tuple, repr=False)
    notes: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def sort_key(self) -> tuple:
        """Deterministic order key (consema-core diagnostic.rs:107-123).

        Missing primary sorts last (u64::MAX in Rust; Python's None-in-
        tuple comparison is avoided by using an explicit sentinel).
        """
        start = self.primary.start_byte if self.primary is not None else 2**64 - 1
        return (start, self.category.value, self.code, self.occurrence)


def sort_diagnostics(diagnostics: list[HclDiagnostic]) -> None:
    """Sorts in place by (primary start, category, code, occurrence)
    (diagnostic.rs:107-123)."""
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key())


# ---------------------------------------------------------------------------
# Fatal formation failures
# ---------------------------------------------------------------------------


class HclFormationFailureKind(enum.Enum):
    """Fatal formation failure categories (RFC 0014 §3, §11).

    Every limit failure is a fatal ``hcl.limit.<name>@1`` failure; a limit
    failure never masquerades as a partial document (hard gate 4).
    """

    RESOURCE_LIMIT = "resource-limit"
    INVALID_UTF8 = "invalid-utf8"
    ENCODING = "encoding"
    COVERAGE = "coverage"
    COORDINATES = "coordinates"
    INTERNAL = "internal"


class HclFormationFailure(Exception):
    """Fatal formation failure; no Document exists.

    The frozen code is ``hcl.limit.<name>@1`` (the ``resource_name`` names
    the bound resource, for example "expression-depth"), or one of the
    fatal ``hcl.parse.*@1`` source-contract codes
    (hcl.parse.invalid-utf8@1, hcl.parse.encoding@1, hcl.parse.coverage@1,
    hcl.parse.coordinates@1, hcl.parse.internal@1).
    """

    def __init__(
        self,
        kind: HclFormationFailureKind,
        *,
        resource_name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        valid_up_to: int | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.name = kind.value
        self.resource_name = resource_name
        self.observed = observed
        self.limit = limit
        self.valid_up_to = valid_up_to

    @property
    def code(self) -> str:
        if self.kind is HclFormationFailureKind.RESOURCE_LIMIT:
            return f"hcl.limit.{self.resource_name}@1"
        if self.kind is HclFormationFailureKind.INVALID_UTF8:
            return "hcl.parse.invalid-utf8@1"
        if self.kind is HclFormationFailureKind.ENCODING:
            return "hcl.parse.encoding@1"
        if self.kind is HclFormationFailureKind.COVERAGE:
            return "hcl.parse.coverage@1"
        if self.kind is HclFormationFailureKind.COORDINATES:
            return "hcl.parse.coordinates@1"
        return "hcl.parse.internal@1"


# ---------------------------------------------------------------------------
# Projection failures
# ---------------------------------------------------------------------------


class HclProjectionFailureKind(enum.Enum):
    """Stable projection failure categories (projection.rs:432-451)."""

    INCOMPLETE_DOCUMENT = "IncompleteDocument"
    NON_LITERAL_EXPRESSION = "NonLiteralExpression"
    UNREPRESENTABLE = "Unrepresentable"
    RESOURCE_LIMIT = "ResourceLimit"
    CORE_INVARIANT = "CoreInvariant"


class HclProjectionFailure(Exception):
    """Stable projection failure with a frozen registered code.

    Code mapping authority: projection.rs:468-476 (RFC 0014 §8, hard
    gate 4). ``name`` is the exact Rust variant spelling.
    """

    def __init__(
        self,
        kind: HclProjectionFailureKind,
        *,
        text: str | None = None,
        fact: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.text = text
        self.fact = fact
        self.resource_name = resource_name

    @property
    def name(self) -> str:
        return self.kind.value

    @property
    def code(self) -> str:
        return _PROJECTION_CODES[self.kind]


_PROJECTION_CODES = {
    HclProjectionFailureKind.INCOMPLETE_DOCUMENT: "hcl.projection.incomplete-document@1",
    HclProjectionFailureKind.NON_LITERAL_EXPRESSION: "hcl.projection.non-literal-expression@1",
    HclProjectionFailureKind.UNREPRESENTABLE: "hcl.projection.unrepresentable@1",
    HclProjectionFailureKind.RESOURCE_LIMIT: "hcl.projection.resource-limit@1",
    HclProjectionFailureKind.CORE_INVARIANT: "hcl.projection.core-invariant@1",
}


# ---------------------------------------------------------------------------
# Edit failures
# ---------------------------------------------------------------------------


class HclEditFailureKind(enum.Enum):
    """Stable edit failure categories (edit.rs:547-578)."""

    WRONG_SNAPSHOT = "WrongSnapshot"
    WRONG_ROLE = "WrongRole"
    INCOMPLETE_TARGET = "IncompleteTarget"
    DUPLICATE_ATTRIBUTE = "DuplicateAttribute"
    BLOCK_IN_TFVARS = "BlockInTfvars"
    CONFLICTING_EDITS = "ConflictingEdits"
    OVERLAPPING_OWNERSHIP = "OverlappingOwnership"
    UNREPRESENTABLE_VALUE = "UnrepresentableValue"
    RESOURCE_LIMIT = "ResourceLimit"
    NEW_DOCUMENT_FORMATION_FAILED = "NewDocumentFormationFailed"


class HclEditFailure(Exception):
    """Stable edit failure with a frozen registered code.

    Code mapping authority: edit.rs:599-611 (RFC 0014 §10; the conformance
    vectors pin hcl.edit.duplicate-attribute@1, hcl.edit.block-in-tfvars@1,
    hcl.edit.unrepresentable@1, core.edit.incomplete-target@1,
    core.edit.wrong-snapshot@1).
    """

    def __init__(
        self,
        kind: HclEditFailureKind,
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
    HclEditFailureKind.WRONG_SNAPSHOT: "core.edit.wrong-snapshot@1",
    HclEditFailureKind.WRONG_ROLE: "core.edit.wrong-role@1",
    HclEditFailureKind.INCOMPLETE_TARGET: "core.edit.incomplete-target@1",
    HclEditFailureKind.DUPLICATE_ATTRIBUTE: "hcl.edit.duplicate-attribute@1",
    HclEditFailureKind.BLOCK_IN_TFVARS: "hcl.edit.block-in-tfvars@1",
    HclEditFailureKind.CONFLICTING_EDITS: "core.edit.conflicting-edits@1",
    HclEditFailureKind.OVERLAPPING_OWNERSHIP: "core.edit.conflicting-edits@1",
    HclEditFailureKind.UNREPRESENTABLE_VALUE: "hcl.edit.unrepresentable@1",
    HclEditFailureKind.RESOURCE_LIMIT: "core.edit.resource-limit@1",
    HclEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED: "core.edit.formation-failed@1",
}


# ---------------------------------------------------------------------------
# Materialization failures
# ---------------------------------------------------------------------------


class HclMaterializationFailureKind(enum.Enum):
    """Stable materialization failure categories of the HCL suite mapping.

    The shared MaterializationFailure surface of consema.document.materialization
    is reused (RFC 0004 §7); the HCL suite maps Unrepresentable to
    hcl.materialization.unrepresentable@1, ResourceLimit to
    hcl.materialization.resource-limit@1, and InvalidRequest to the
    published spelling ``"invalid-record"``
    (crates/consema-conformance/src/hcl_v1.rs:1611-1616; RFC 0014 §9).
    """

    UNREPRESENTABLE = "Unrepresentable"
    RESOURCE_LIMIT = "ResourceLimit"
    INVALID_REQUEST = "InvalidRequest"
    FORMATION_FAILED = "FormationFailed"
    UNSUPPORTED_PROFILE = "UnsupportedProfile"
    UNSUPPORTED_STYLE = "UnsupportedStyle"
    UNSUPPORTED_ENCODING = "UnsupportedEncoding"
    UNSUPPORTED_NEWLINE = "UnsupportedNewline"
    INVALID_INPUT = "InvalidInput"


class HclMaterializationFailure(Exception):
    """Stable materialization failure.

    ``name`` is the exact Rust variant spelling referenced by the
    conformance vectors; ``code`` is the suite-published code.
    """

    def __init__(
        self,
        kind: HclMaterializationFailureKind,
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
        if self.kind is HclMaterializationFailureKind.UNREPRESENTABLE:
            return "hcl.materialization.unrepresentable@1"
        if self.kind is HclMaterializationFailureKind.RESOURCE_LIMIT:
            return "hcl.materialization.resource-limit@1"
        if self.kind is HclMaterializationFailureKind.INVALID_REQUEST:
            return "invalid-record"
        if self.kind is HclMaterializationFailureKind.FORMATION_FAILED:
            return "hcl.materialization.formation-failed@1"
        if self.kind is HclMaterializationFailureKind.UNSUPPORTED_PROFILE:
            return "core.materialization.unsupported-profile@1"
        if self.kind is HclMaterializationFailureKind.UNSUPPORTED_STYLE:
            return "core.materialization.unsupported-style@1"
        if self.kind is HclMaterializationFailureKind.UNSUPPORTED_ENCODING:
            return "core.materialization.unsupported-encoding@1"
        if self.kind is HclMaterializationFailureKind.UNSUPPORTED_NEWLINE:
            return "core.materialization.unsupported-newline@1"
        return "core.materialization.invalid-request@1"
