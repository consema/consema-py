"""Edit golden transcriptions and one full round-trip.

Cases covered:

- json-family-v2.json: json5.edit.move-member (174-178),
  json5.edit.move-cross-object-rejected (180-184),
  json5.edit.preserve-scalars (186-190).
- v1.json: edit.scalar-minimal (107-111), edit.preserve-decimal-scale
  (113-117), edit.preserve-exponent-style (119-123),
  edit.canonical-for-profile (125-129), edit.preserve-else-canonical
  (131-135), edit.preserve-incompatible-rejected (137-141),
  edit.wrong-snapshot (173-177).

Round-trip (RFC 0004 §16): the committed SourcePatch reapplies to the base
snapshot and reproduces the exact committed bytes; the untouched-byte
proof verifies; dry-run and commit produce the identical replacement set
and target digest (RFC 0004 §20).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue, decimal
from consema.document.ids import ProfileId
from consema.document.limits import ParseLimits
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import AssociationPlacement
from consema.json import (
    EditTransactionBuilder,
    JsonEditFailure,
    JsonEditFailureKind,
    JsonProfile,
    RepresentationPolicy,
    commit,
    dry_run,
    parse,
)

DEFAULT_LIMITS = ParseLimits()


def member_refs(document):
    availability = document.root().object_members()
    assert availability.is_available
    return list(availability.value)


def member_value_ref(document, ordinal: int):
    members = member_refs(document)
    assert len(members) > ordinal
    return members[ordinal].value_node_ref()


def object_root_ref(document):
    return document.root().node_ref()


# ---------------------------------------------------------------------------
# json-family-v2.json edit cases
# ---------------------------------------------------------------------------


def test_json5_edit_move_member():
    # Case json5.edit.move-member (json-family-v2.json:174-178).
    source = "{ /*before*/ a:1, /*stay*/ b:2, c:3, }"
    expected = "{ /*before*/ b:2,a:1, /*stay*/  c:3, }"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    members = member_refs(document)
    assert len(members) == 3
    transaction = (
        EditTransactionBuilder(document)
        .move_member(members[1].node_ref(), AssociationPlacement("Start"))
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render().decode("utf-8") == expected
    # patch equality and proof validity (vector fields patch_equal / proof_valid).
    result.untouched_proof.verify(
        document.source, result.document.source, list(result.source_patch.replacements)
    )


def test_json5_edit_move_cross_object_rejected():
    # Case json5.edit.move-cross-object-rejected (json-family-v2.json:180-184).
    source = "{left:{a:1},right:{b:2}}"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    root_members = member_refs(document)
    left_members_availability = root_members[0].value().object_members()
    right_members_availability = root_members[1].value().object_members()
    assert left_members_availability.is_available
    assert right_members_availability.is_available
    left_a = left_members_availability.value[0].node_ref()
    right_b = right_members_availability.value[0].node_ref()
    transaction = (
        EditTransactionBuilder(document)
        .move_member(
            left_a, AssociationPlacement("Before", anchor=right_b)
        )
        .build()
    )
    with pytest.raises(JsonEditFailure) as caught:
        commit(document, transaction)
    assert caught.value.name == "TargetNotFound"


def test_json5_edit_preserve_scalars():
    # Case json5.edit.preserve-scalars (json-family-v2.json:186-190).
    source = "{hex:+0X0f,point:+.50,string:'a\\x20\\v',nf:+Infinity}"
    expected = "{hex:+0X10,point:+.75,string:'a\\x20\\v',nf:+NaN}"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    members = member_refs(document)
    builder = EditTransactionBuilder(document)
    builder.semantic_scalar(
        members[0].value_node_ref(),
        PortableValue.integer(16),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    builder.semantic_scalar(
        members[1].value_node_ref(),
        PortableValue.decimal(decimal(75, -2)),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    builder.semantic_scalar(
        members[2].value_node_ref(),
        PortableValue.string("a \u000b"),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    builder.semantic_scalar(
        members[3].value_node_ref(),
        PortableValue.binary_float64(0x7FF8000000000000),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    result = commit(document, builder.build())
    assert result.document.render().decode("utf-8") == expected


# ---------------------------------------------------------------------------
# v1.json scalar edit cases
# ---------------------------------------------------------------------------


def test_edit_scalar_minimal():
    # Case edit.scalar-minimal (v1.json:107-111).
    source = "{ /* lead */ \"a\" : 1 // tail\n}"
    expected = "{ /* lead */ \"a\" : 200 // tail\n}"
    document = parse(source.encode("utf-8"), JsonProfile.JSONC_BOUNDED_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.integer(200),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render() == expected.encode("utf-8")
    assert len(result.change_set.source_edits) == 1


def test_edit_preserve_decimal_scale():
    # Case edit.preserve-decimal-scale (v1.json:113-117).
    document = parse(b'{"a": 1.00}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.decimal(decimal(25, -1)),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render() == b'{"a": 2.50}'


def test_edit_preserve_exponent_style():
    # Case edit.preserve-exponent-style (v1.json:119-123).
    document = parse(b'{"a": 1E+02}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.integer(2),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render() == b'{"a": 2E+0}'


def test_edit_canonical_for_profile():
    # Case edit.canonical-for-profile (v1.json:125-129).
    document = parse(b'{"a": 1.00}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.decimal(decimal(25, -1)),
            RepresentationPolicy.CANONICAL_FOR_PROFILE,
        )
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render() == b'{"a": 25e-1}'


def test_edit_preserve_else_canonical():
    # Case edit.preserve-else-canonical (v1.json:131-135).
    document = parse(b'{"a": 1.000}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.decimal(decimal(1, -4)),
            RepresentationPolicy.PRESERVE_ELSE_CANONICAL,
        )
        .build()
    )
    result = commit(document, transaction)
    assert result.document.render() == b'{"a": 1e-4}'
    fallbacks = [
        diagnostic
        for diagnostic in result.change_set.diagnostics
        if diagnostic.code == "json.edit.representation-fallback@1"
    ]
    assert len(fallbacks) == 1


def test_edit_preserve_incompatible_rejected():
    # Case edit.preserve-incompatible-rejected (v1.json:137-141).
    document = parse(b'{"a": 1.000}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            member_value_ref(document, 0),
            PortableValue.decimal(decimal(1, -4)),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    with pytest.raises(JsonEditFailure) as caught:
        commit(document, transaction)
    assert caught.value.name == "RepresentationIncompatible"


def test_edit_wrong_snapshot():
    # Case edit.wrong-snapshot (v1.json:173-177).
    first = parse(b"1", JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    second = parse(b"2", JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    transaction = (
        EditTransactionBuilder(first)
        .literal_scalar(first.root().node_ref(), b"3")
        .build()
    )
    with pytest.raises(JsonEditFailure) as caught:
        commit(second, transaction)
    assert caught.value.name == "WrongSnapshot"
    # The second document is unchanged.
    assert second.render() == b"2"


def test_edit_round_trip_patch_proof_dry_run():
    # One full edit round-trip (RFC 0004 §16/§20): commit -> patch reapply
    # -> untouched-proof verify -> dry-run equality.
    source = '{\n  "a": 1,\n  "b": [1, 2],\n  "c": {"x": true}\n}'
    document = parse(source.encode("utf-8"), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    members = member_refs(document)
    builder = EditTransactionBuilder(document)
    builder.remove_member(members[1].node_ref())
    builder.insert_member(
        object_root_ref(document),
        "d",
        PortableValue.boolean(False),
        AssociationPlacement("End"),
    )
    transaction = builder.build()

    result = commit(document, transaction)
    rendered = result.document.render()
    assert b'"b": [1, 2]' not in rendered
    assert b'"d":false' in rendered

    # SourcePatch reapplies to the base and reproduces the committed bytes.
    patch_limits = SourcePatchLimits(
        source=SourceLimits(
            max_raw_bytes=DEFAULT_LIMITS.max_source_bytes,
            max_decoded_utf8_bytes=DEFAULT_LIMITS.max_source_bytes,
            max_decoded_scalars=DEFAULT_LIMITS.max_source_bytes,
        ),
        max_replacements=len(result.change_set.source_edits),
        max_patch_bytes=DEFAULT_LIMITS.max_source_bytes * 2,
    )
    reapplied = result.source_patch.apply(document.source, patch_limits)
    assert reapplied.bytes() == rendered
    assert reapplied.digest() == result.document.source.digest()

    # Untouched-byte proof verifies and detects tampering.
    result.untouched_proof.verify(
        document.source, result.document.source, list(result.source_patch.replacements)
    )

    # Dry-run and commit produce the identical replacement set and digest.
    from consema.document.edit_plan import EditPlanSourceId

    plan = dry_run(document, transaction, EditPlanSourceId.new("test.json"))
    assert plan.target_digest() == result.document.source.digest()
    assert list(plan.replacements()) == list(result.source_patch.replacements)
    # The plan's patch reapplies identically.
    plan_reapplied = plan.source_patch().apply(document.source, patch_limits)
    assert plan_reapplied.bytes() == rendered
