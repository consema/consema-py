"""HCL structural edit: the six frozen operations with atomic commit,
dry-run equivalence, untouched-byte proofs, and SourcePatch replay
(RFC 0014 §10).

Golden cases transcribed verbatim from conformance/vectors/hcl-v1.json
(suite "consema.hcl.conformance@1"); each test cites the vector case id.
Cases covered:

- hcl-v1.json: hcl.edit.attribute-operations (1462-1504),
  hcl.edit.block-operations (1506-1549), hcl.edit.conflicts (1551-1647),
  hcl.edit.dry-run-equivalence (2047-2080).
"""

from __future__ import annotations

import pytest

from consema.document.edit_plan import EditPlanSourceId
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatchLimits


def _patch_limits() -> SourcePatchLimits:
    return SourcePatchLimits(
        source=SourceLimits.unbounded(),
        max_replacements=1_000_000,
        max_patch_bytes=64 * 1024 * 1024,
    )
from consema.hcl import (
    BodyPath,
    BodyPlacement,
    EditTransactionBuilder,
    EditValue,
    HclEditFailure,
    HclProfile,
    NodeRef,
    commit,
    dry_run,
    parse,
)


def test_attribute_operations_golden():
    # Case hcl.edit.attribute-operations (hcl-v1.json:1462-1504).
    document = parse(b"region = \"us-east-1\"\ncount = 2\nenabled = true\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    builder.insert_attribute(
        BodyPath.root(), "zone", EditValue.string("a"), BodyPlacement.first()
    )
    builder.set_attribute_value(BodyPath.root(), "count", EditValue.integer(3))
    builder.rename_attribute(BodyPath.root(), "enabled", "active")
    builder.remove_attribute(BodyPath.root(), "region")
    result = commit(document, builder.build())
    assert result.document.render().decode("utf-8") == "zone = \"a\"\ncount = 3\nactive = true\n"
    # Reparse closure: the target is a Complete document.
    assert result.document.formation_status().value == "Complete"
    # Untouched-byte proof verifies against the committed bytes
    # (RFC 0004 §15).
    result.untouched_proof.verify(
        document.source, result.document.source, list(result.source_patch.replacements)
    )
    # The derived SourcePatch reapplies to the base and reproduces the
    # committed bytes (RFC 0004 §16).
    patched = result.source_patch.apply(document.source, _patch_limits())
    assert patched.bytes() == result.document.source.bytes()
    # ChangeSet carries the ordered source edits.
    assert len(result.change_set.source_edits) >= 3


def test_block_operations_golden():
    # Case hcl.edit.block-operations (hcl-v1.json:1506-1549).
    document = parse(b"server \"web\" {\n  port = 8080\n}\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    builder.insert_block(
        BodyPath.root(),
        "server",
        ["db"],
        [("port", EditValue.integer(5432))],
        BodyPlacement.last(),
    )
    builder.remove_block(BodyPath.root(), "server", ["web"], 0)
    result = commit(document, builder.build())
    assert result.document.render().decode("utf-8") == "server \"db\" {\n  port = 5432\n}\n"
    assert result.document.render().startswith(b"server \"db\"")
    # Labels always render quoted (RFC 0014 §9).
    block = result.document.body.items[0].as_block()
    assert block.labels[0].quoted is True
    assert block.labels[0].text == "db"
    result.untouched_proof.verify(
        document.source, result.document.source, list(result.source_patch.replacements)
    )
    assert result.source_patch.apply(document.source, _patch_limits()).bytes() == result.document.source.bytes()


def test_conflict_codes():
    # Case hcl.edit.conflicts (hcl-v1.json:1551-1647).
    def conflict(source: bytes, profile, operations) -> str:
        document = parse(source, profile)
        builder = EditTransactionBuilder(document)
        for operation in operations:
            operation(builder)
        with pytest.raises(HclEditFailure) as raised:
            commit(document, builder.build())
        return raised.value.code

    # Sample 1: duplicate-attribute creation.
    code = conflict(
        b"count = 2\n",
        HclProfile.NATIVE_V1,
        [
            lambda builder: builder.insert_attribute(
                BodyPath.root(), "count", EditValue.integer(3), BodyPlacement.last()
            )
        ],
    )
    assert code == "hcl.edit.duplicate-attribute@1"

    # Sample 2: block insertion under the tfvars profile.
    code = conflict(
        b"region = \"x\"\n",
        HclProfile.TFVARS_V1,
        [
            lambda builder: builder.insert_block(
                BodyPath.root(), "server", ["db"], [], BodyPlacement.last()
            )
        ],
    )
    assert code == "hcl.edit.block-in-tfvars@1"

    # Sample 3: a derived expression value is unrepresentable (RFC 0014
    # §10, §14: expression-AST editing is an explicit non-goal).
    code = conflict(
        b"count = 2\n",
        HclProfile.NATIVE_V1,
        [
            lambda builder: builder.set_attribute_value(
                BodyPath.root(), "count", EditValue.expression("binary", "1 + 2")
            )
        ],
    )
    assert code == "hcl.edit.unrepresentable@1"

    # Sample 4: a missing target.
    code = conflict(
        b"count = 2\n",
        HclProfile.NATIVE_V1,
        [
            lambda builder: builder.set_attribute_value(
                BodyPath.root(), "missing", EditValue.integer(1)
            )
        ],
    )
    assert code == "core.edit.incomplete-target@1"

    # Sample 5: a transaction bound to another snapshot.
    base = parse(b"count = 2\n", HclProfile.NATIVE_V1)
    other = parse(b"other = 1\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(other)
    builder.set_attribute_value(BodyPath.root(), "count", EditValue.integer(9))
    with pytest.raises(HclEditFailure) as raised:
        commit(base, builder.build())
    assert raised.value.code == "core.edit.wrong-snapshot@1"


def test_dry_run_equivalence():
    # Case hcl.edit.dry-run-equivalence (hcl-v1.json:2047-2080): dry-run
    # and commit produce the identical replacement set and target digest.
    document = parse(b"region = \"us-east-1\"\ncount = 2\nenabled = true\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    builder.set_attribute_value(BodyPath.root(), "count", EditValue.integer(7))
    builder.insert_attribute(
        BodyPath.root(), "zone", EditValue.string("b"), BodyPlacement.last()
    )
    transaction = builder.build()
    result = commit(document, transaction)
    assert result.document.render().decode("utf-8") == (
        "region = \"us-east-1\"\ncount = 7\nenabled = true\nzone = \"b\"\n"
    )
    plan = dry_run(document, transaction, EditPlanSourceId.new("memory:hcl-conformance"))
    assert plan.target_digest() == result.document.source.digest()
    assert plan.base_digest() == document.source.digest()
    assert len(plan.replacements()) == len(result.source_patch.replacements)


def test_rename_preserves_expression_kind():
    # RFC 0014 §10: rename-attribute changes the attribute name, preserving
    # its expression.
    document = parse(b"enabled = true\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    builder.rename_attribute(BodyPath.root(), "enabled", "active")
    result = commit(document, builder.build())
    attribute = result.document.body.items[0].as_attribute()
    assert attribute.name == "active"
    assert attribute.expression.kind.as_str() == "boolean"
    assert result.document.render().decode("utf-8") == "active = true\n"


def test_after_anchor_placement():
    # RFC 0014 §10: insert-attribute at an exact NodeRef anchor.
    document = parse(b"a = 1\nc = 3\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    anchor = NodeRef.attribute(BodyPath.root(), "a")
    builder.insert_attribute(
        BodyPath.root(), "b", EditValue.integer(2), BodyPlacement.after(anchor)
    )
    result = commit(document, builder.build())
    assert result.document.render().decode("utf-8") == "a = 1\nb = 2\nc = 3\n"


def test_edit_never_evaluates():
    # Hard gate 1 (RFC 0014 §13): set-attribute-value renders the canonical
    # literal text; no expression is ever evaluated.
    document = parse(b"count = 2\n", HclProfile.NATIVE_V1)
    builder = EditTransactionBuilder(document)
    builder.set_attribute_value(BodyPath.root(), "count", EditValue.integer(3))
    result = commit(document, builder.build())
    attribute = result.document.body.items[0].as_attribute()
    assert attribute.expression.kind.as_str() == "number"
    assert attribute.expression.text(result.document.source) == "3"
