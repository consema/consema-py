"""Transferable dry-run facts for one fully validated edit transaction.

Authority:
- RFC 0004 §14 (docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md:338-356): dry-run performs every deterministic validation and
  byte-planning step except publishing a new Document; its transferable form
  carries source_id, base_digest, profile, ordered operations with safe
  summaries, exact SourcePatch replacement facts, precomputed target_digest,
  and an ordered report. Secrets use the SourcePatch redaction rules. A
  dry-run plan is not authority to write a file and is never applied without
  rechecking base digest and every original-byte precondition.
- RFC 0016 §5.3 (docs/rfcs/0016-go-api-mapping-v1.md:184-187): dry-run
  semantics identical; nothing authorizes file writes.
- crates/consema-document/src/edit_plan.rs — arbitration: EditPlanSourceId
  bound edit_plan.rs:13-31 (non-empty, <= 1024); EditOperationSummary bounds
  edit_plan.rs:34-70 (<= 64 arguments, names match
  [a-z0-9_]*, values non-empty <= 1024); EditPlan::new operation-metadata
  matching edit_plan.rs:84-121 (each patch metadata key "operation.{index}"
  must equal the operation's canonical "id@version" form, and no extra
  operation.* keys); EditPlanError edit_plan.rs:200-211.

go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.ids import ContentDigest, FormatOperationId, ProfileId
from consema.document.source_patch import SourcePatch, SourceReplacement

_MAX_SOURCE_ID_LENGTH = 1024  # edit_plan.rs:20
_MAX_SUMMARY_ARGUMENTS = 64  # edit_plan.rs:46
_MAX_SUMMARY_NAME_LENGTH = 64  # edit_plan.rs:222
_MAX_SUMMARY_VALUE_LENGTH = 1024  # edit_plan.rs:48


@dataclass(frozen=True, slots=True)
class EditPlanSourceId:
    """Caller-stable source identity used by a transferable edit plan
    (edit_plan.rs:13-31)."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > _MAX_SOURCE_ID_LENGTH:
            raise EditPlanError(EditPlanErrorKind.INVALID_SOURCE_ID)

    @classmethod
    def new(cls, value: str) -> EditPlanSourceId:
        """Validates one non-empty bounded external source identity."""
        return cls(value=value)

    def as_str(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EditOperationSummary:
    """One safe, content-free summary of a declared edit operation
    (edit_plan.rs:34-70; RFC 0004 §14: safe summaries, never raw edited
    values)."""

    operation: FormatOperationId
    arguments: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.arguments) > _MAX_SUMMARY_ARGUMENTS or any(
            not _valid_summary_name(name)
            or not value
            or len(value) > _MAX_SUMMARY_VALUE_LENGTH
            for name, value in self.arguments.items()
        ):
            raise EditPlanError(EditPlanErrorKind.INVALID_OPERATION_SUMMARY)

    @classmethod
    def new(
        cls, operation: FormatOperationId, arguments: dict[str, str]
    ) -> EditOperationSummary:
        """Validates a bounded summary that must not contain raw edited values."""
        return cls(operation=operation, arguments=dict(sorted(arguments.items())))


@dataclass(frozen=True, slots=True)
class EditPlan:
    """Fully validated dry-run plan; possessing it does not authorize a write
    (edit_plan.rs:73-197; RFC 0004 §14)."""

    source_id: EditPlanSourceId
    profile: ProfileId
    patch: SourcePatch = field(repr=False)
    operations: tuple[EditOperationSummary, ...] = field(default_factory=tuple)
    report: tuple[object, ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def new(
        cls,
        source_id: EditPlanSourceId,
        profile: ProfileId,
        operations: list[EditOperationSummary],
        patch: SourcePatch,
        report: list[object],
    ) -> EditPlan:
        """Closes a plan only when its ordered operation metadata matches its
        exact patch (edit_plan.rs:84-121)."""
        for index, operation in enumerate(operations):
            key = f"operation.{index}"
            if patch.metadata.get(key) != operation.operation.to_string():
                raise EditPlanError(EditPlanErrorKind.OPERATION_METADATA_MISMATCH, index=index)
        operation_keys = [key for key in patch.metadata if key.startswith("operation.")]
        if operation_keys and len(operation_keys) != len(operations):
            raise EditPlanError(
                EditPlanErrorKind.OPERATION_METADATA_MISMATCH, index=len(operations)
            )
        return cls(
            source_id=source_id,
            profile=profile,
            operations=tuple(operations),
            patch=patch,
            report=tuple(report),
        )

    # -- accessors --------------------------------------------------------

    def base_digest(self) -> ContentDigest:
        """Required base content identity (delegates to the patch)."""
        return self.patch.base_digest

    def target_digest(self) -> ContentDigest:
        """Precomputed exact target content identity (RFC 0004 §14)."""
        return self.patch.target_digest

    def replacements(self) -> tuple[SourceReplacement, ...]:
        """Exact replacement facts, including review redaction flags."""
        return self.patch.replacements

    def source_patch(self) -> SourcePatch:
        """Underlying patch whose application rechecks digest and every
        original-byte precondition (edit_plan.rs:165-169)."""
        return self.patch

    def with_all_replacements_redacted(
        self, redact_original: bool, redact_replacement: bool
    ) -> EditPlan:
        """Redacts every original/replacement payload from review/debug
        presentation; this does not remove bytes required to apply and verify
        the plan's SourcePatch (edit_plan.rs:174-183)."""
        return EditPlan(
            source_id=self.source_id,
            profile=self.profile,
            operations=self.operations,
            patch=self.patch.with_all_replacements_redacted(
                redact_original, redact_replacement
            ),
            report=self.report,
        )

    def with_replacement_redacted(
        self, index: int, redact_original: bool, redact_replacement: bool
    ) -> EditPlan:
        """Redacts one exact replacement from review/debug presentation
        (edit_plan.rs:186-196)."""
        return EditPlan(
            source_id=self.source_id,
            profile=self.profile,
            operations=self.operations,
            patch=self.patch.with_replacement_redacted(
                index, redact_original, redact_replacement
            ),
            report=self.report,
        )


class EditPlanErrorKind(enum.Enum):
    """Edit-plan construction failure before a transferable plan exists
    (edit_plan.rs:200-211)."""

    INVALID_SOURCE_ID = "invalid-source-id"
    INVALID_OPERATION_SUMMARY = "invalid-operation-summary"
    OPERATION_METADATA_MISMATCH = "operation-metadata-mismatch"


class EditPlanError(Exception):
    """Edit-plan construction failure (edit_plan.rs:200-211).

    Carries no registered error code; the plan is an internal dry-run
    artifact (RFC 0004 §14). Error text is human presentation only.
    """

    def __init__(self, kind: EditPlanErrorKind, *, index: int | None = None) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.index = index


def _valid_summary_name(name: str) -> bool:
    """Frozen summary-name vocabulary [a-z0-9_] (edit_plan.rs:221-227)."""
    return (
        bool(name)
        and len(name) <= _MAX_SUMMARY_NAME_LENGTH
        and all(
            character.isascii()
            and (character.islower() or character.isdigit() or character == "_")
            for character in name
        )
    )
