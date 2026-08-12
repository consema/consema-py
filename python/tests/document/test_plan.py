"""Dry-run EditPlan tests.

Contract: RFC 0004 §14 (docs/rfcs/0004-materialization-conversion-and-
structural-edit-v1.md:338-356); arbitration crates/consema-document/
src/edit_plan.rs:13-31, 34-70, 84-121, 200-211. A dry-run plan is not
authority to write a file and is never applied without rechecking base
digest and every original-byte precondition.
"""

from __future__ import annotations

import pytest

from consema.document import (
    EditOperationSummary,
    EditPlan,
    EditPlanError,
    EditPlanErrorKind,
    EditPlanSourceId,
    FormatOperationId,
    ProfileId,
    SourcePatch,
    SourcePatchLimits,
    SourceSnapshot,
)


def test_source_id_must_be_non_empty_and_bounded() -> None:
    with pytest.raises(EditPlanError) as caught:
        EditPlanSourceId.new("")
    assert caught.value.kind is EditPlanErrorKind.INVALID_SOURCE_ID
    with pytest.raises(EditPlanError) as caught:
        EditPlanSourceId.new("x" * 1025)
    assert caught.value.kind is EditPlanErrorKind.INVALID_SOURCE_ID
    assert EditPlanSourceId.new("config.json").as_str() == "config.json"


def test_summary_arguments_are_bounded_and_safe() -> None:
    operation = FormatOperationId.new("json.edit.remove-member", 1)
    summary = EditOperationSummary.new(
        operation, {"target_role": "json.object-member@1"}
    )
    assert summary.operation == operation
    assert summary.arguments["target_role"] == "json.object-member@1"
    with pytest.raises(EditPlanError) as caught:
        EditOperationSummary.new(operation, {"Bad Name": "x"})
    assert caught.value.kind is EditPlanErrorKind.INVALID_OPERATION_SUMMARY
    with pytest.raises(EditPlanError) as caught:
        EditOperationSummary.new(operation, {"x" * 65: "v"})
    assert caught.value.kind is EditPlanErrorKind.INVALID_OPERATION_SUMMARY


def test_plan_requires_stable_source_and_matching_operation_metadata() -> None:
    """The patch metadata key operation.{index} must equal the operation's
    canonical id@version form (edit_plan.rs:91-98)."""
    source = SourceSnapshot.from_utf8(b"a")
    patch = SourcePatch.create(
        source,
        [],
        {"operation.0": "json.edit.remove-member@1"},
        SourcePatchLimits(),
    )
    summary = EditOperationSummary.new(
        FormatOperationId.new("json.edit.remove-member", 1),
        {"target_role": "json.object-member@1"},
    )
    plan = EditPlan.new(
        EditPlanSourceId.new("config.json"),
        ProfileId.new("json.strict", 1),
        [summary],
        patch,
        [],
    )
    assert plan.source_id.as_str() == "config.json"
    assert plan.profile == ProfileId.new("json.strict", 1)
    assert plan.base_digest() == plan.target_digest()
    assert plan.replacements() == ()

    mismatched = SourcePatch.create(source, [], {}, SourcePatchLimits())
    with pytest.raises(EditPlanError) as caught:
        EditPlan.new(
            EditPlanSourceId.new("config.json"),
            ProfileId.new("json.strict", 1),
            [summary],
            mismatched,
            [],
        )
    assert caught.value.kind is EditPlanErrorKind.OPERATION_METADATA_MISMATCH

    extra = SourcePatch.create(
        source, [], {"operation.0": "json.edit.remove-member@1", "operation.1": "x@1"}, SourcePatchLimits()
    )
    with pytest.raises(EditPlanError) as caught:
        EditPlan.new(
            EditPlanSourceId.new("config.json"),
            ProfileId.new("json.strict", 1),
            [summary],
            extra,
            [],
        )
    assert caught.value.kind is EditPlanErrorKind.OPERATION_METADATA_MISMATCH
    assert caught.value.index == 1


def test_plan_redaction_does_not_remove_application_bytes() -> None:
    source = SourceSnapshot.from_utf8(b"secret")
    patch = SourcePatch.create(
        source,
        [],
        {},
        SourcePatchLimits(),
    )
    plan = EditPlan.new(
        EditPlanSourceId.new("config.json"),
        ProfileId.new("json.strict", 1),
        [],
        patch,
        [],
    ).with_all_replacements_redacted(True, True)
    assert plan.source_patch().apply(source, SourcePatchLimits()).bytes() == b"secret"
