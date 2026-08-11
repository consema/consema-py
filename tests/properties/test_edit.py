"""Edit golden transcriptions and one full round-trip.

Cases covered:

- java-properties-v1.json: edit.all-five-operations (lines 106-109),
  edit.dry-run-patch-proof-conflict-atomicity (111-114).
- Round-trip (RFC 0004 section 16): the committed SourcePatch reapplies
  to the base snapshot and reproduces the exact committed bytes; the
  untouched-byte proof verifies; dry-run and commit produce the identical
  replacement set and target digest (RFC 0004 section 20).
- Conflict matrix: duplicate target, removed placement anchor, and shared
  insertion boundary fail before any document is published
  (edit.rs:1347-1392).
"""

from __future__ import annotations

import pytest

from consema.document.edit_plan import EditPlanSourceId
from consema.document.source import SourceEncoding, SourceLimits
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import AssociationPlacement
from consema.properties import (
    EditTransactionBuilder,
    JavaString,
    PropertiesEditFailure,
    PropertiesEditFailureKind,
    PropertiesParseLimits,
    commit,
    dry_run,
    parse_reader,
)

DEFAULT_LIMITS = PropertiesParseLimits()


def reader(source: bytes):
    return parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)


def text(document) -> str:
    return document.render().decode("utf-8")


def commit_one(document, operation):
    builder = EditTransactionBuilder(document)
    operation(builder)
    return commit(document, builder.build())


def test_all_five_operations():
    # Case edit.all-five-operations (java-properties-v1.json:106-109).
    document = reader(b"a=1\nb=2\n")
    first = document.properties[0].node
    second = document.properties[1].node

    semantic = commit_one(document, lambda builder: builder.semantic_value(
        first, JavaString.from_unicode("changed")
    ))
    assert text(semantic.document) == "a=changed\nb=2\n"

    literal = commit_one(document, lambda builder: builder.literal_value(
        first, b"raw\\ value"
    ))
    assert text(literal.document) == "a=raw\\ value\nb=2\n"
    assert literal.document.properties[0].value.to_unicode() == "raw value"

    inserted = commit_one(document, lambda builder: builder.insert_property(
        document.node_ref(),
        JavaString.from_unicode("c"),
        JavaString.from_unicode("3"),
        AssociationPlacement("End"),
    ))
    assert text(inserted.document) == "a=1\nb=2\nc=3\n"

    removed = commit_one(document, lambda builder: builder.remove_property(first))
    assert text(removed.document) == "b=2\n"

    renamed = commit_one(document, lambda builder: builder.rename_property(
        first, JavaString.from_unicode("renamed")
    ))
    assert text(renamed.document) == "renamed=1\nb=2\n"


def test_semantic_value_preserves_direct_style_and_falls_back_only_when_required():
    # Direct style preservation versus the canonical fallback diagnostic
    # (edit.rs:1186-1213).
    direct = reader(b"a=one\n")
    direct_commit = commit_one(direct, lambda builder: builder.semantic_value(
        direct.properties[0].node, JavaString.from_unicode("two words")
    ))
    assert text(direct_commit.document) == "a=two words\n"
    assert direct_commit.change_set.diagnostics == ()

    escaped = reader(b"a=one\\ value\n")
    fallback_commit = commit_one(escaped, lambda builder: builder.semantic_value(
        escaped.properties[0].node, JavaString.from_unicode("next value")
    ))
    assert text(fallback_commit.document) == "a=next value\n"
    assert (
        fallback_commit.change_set.diagnostics[0].code
        == "java-properties.edit.canonical-fallback@1"
    )


def test_semantic_value_preserves_exact_unpaired_java_units():
    # An exact unpaired surrogate enters through canonical uppercase
    # escapes (edit.rs:1215-1224).
    document = reader(b"a=x\n")
    exact = JavaString.from_code_units([0xD800])
    commit_result = commit_one(document, lambda builder: builder.semantic_value(
        document.properties[0].node, exact
    ))
    assert text(commit_result.document) == "a=\\uD800\n"
    assert commit_result.document.properties[0].value == exact


def test_literal_value_requires_one_exact_value_ownership_interval():
    # Literal replacement owns exactly one raw value interval
    # (edit.rs:1226-1256); delimiters and newlines are never consumed.
    document = reader(b"a=one\nb=two\n")
    for invalid in (b" leading", b"line\nbreak", b"tail\\"):
        builder = EditTransactionBuilder(document)
        builder.literal_value(document.properties[0].node, invalid)
        with pytest.raises(PropertiesEditFailure) as caught:
            commit(document, builder.build())
        assert caught.value.kind is PropertiesEditFailureKind.INVALID_LITERAL


def test_insertions_honor_property_relative_placements():
    # Every placement around comments and duplicate keys (edit.rs:1258-1318).
    source = b"# head\na=1\n# middle\nb=2"
    plain_cases = [
        (AssociationPlacement("Start"), "# head\nx=0\na=1\n# middle\nb=2"),
        (AssociationPlacement("End"), "# head\na=1\n# middle\nb=2\nx=0\n"),
    ]
    for placement, expected in plain_cases:
        document = reader(source)
        commit_result = commit_one(document, lambda builder: builder.insert_property(
            document.node_ref(),
            JavaString.from_unicode("x"),
            JavaString.from_unicode("0"),
            placement,
        ))
        assert text(commit_result.document) == expected
    # Anchored placements resolve against the fresh snapshot
    # (edit.rs:1266-1273).
    document = reader(source)
    before_commit = commit_one(document, lambda builder: builder.insert_property(
        document.node_ref(),
        JavaString.from_unicode("x"),
        JavaString.from_unicode("0"),
        AssociationPlacement("Before", anchor=document.properties[1].node),
    ))
    assert text(before_commit.document) == "# head\na=1\n# middle\nx=0\nb=2"
    after_commit = commit_one(document, lambda builder: builder.insert_property(
        document.node_ref(),
        JavaString.from_unicode("x"),
        JavaString.from_unicode("0"),
        AssociationPlacement("After", anchor=document.properties[0].node),
    ))
    assert text(after_commit.document) == "# head\na=1\nx=0\n# middle\nb=2"

    duplicate = reader(b"a=1\na=2\n")
    commit_result = commit_one(duplicate, lambda builder: builder.insert_property(
        duplicate.node_ref(),
        JavaString.from_unicode("a"),
        JavaString.from_unicode("3"),
        AssociationPlacement("End"),
    ))
    assert len(commit_result.document.properties) == 3
    assert all(
        p.key.to_unicode() == "a" for p in commit_result.document.properties
    )


def test_removal_owns_continuation_lines_but_not_adjacent_comments():
    # Removal owns the property's natural lines and unambiguous
    # continuation markers, but not adjacent comments (edit.rs:1320-1329).
    document = reader(b"# before\nkey=first\\\n  second\n# after\nnext=v\n")
    commit_result = commit_one(document, lambda builder: builder.remove_property(
        document.properties[0].node
    ))
    assert text(commit_result.document) == "# before\n# after\nnext=v\n"
    assert len(commit_result.document.comments) == 2
    assert len(commit_result.document.properties) == 1


def test_rename_replaces_complete_continued_key_ownership():
    # Rename preserves value and trivia while escaping the new key
    # (edit.rs:1331-1345).
    document = reader(b"old\\\n key=value\n")
    commit_result = commit_one(document, lambda builder: builder.rename_property(
        document.properties[0].node, JavaString.from_unicode("new key")
    ))
    assert text(commit_result.document) == "new\\ key=value\n"
    assert commit_result.document.properties[0].key.to_unicode() == "new key"


def test_transaction_conflicts_fail_before_any_document_is_published():
    # Conflict matrix (edit.rs:1347-1392): the base document never
    # changes.
    document = reader(b"a=1\nb=2\n")
    first = document.properties[0].node

    duplicate = EditTransactionBuilder(document)
    duplicate.semantic_value(first, JavaString.from_unicode("x")).rename_property(
        first, JavaString.from_unicode("renamed")
    )
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(document, duplicate.build())
    assert caught.value.kind is PropertiesEditFailureKind.DUPLICATE_TARGET
    assert caught.value.code == "core.edit.conflicting-edits@1"

    removed_anchor = EditTransactionBuilder(document)
    removed_anchor.remove_property(first).insert_property(
        document.node_ref(),
        JavaString.from_unicode("x"),
        JavaString.from_unicode("0"),
        AssociationPlacement("After", anchor=first),
    )
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(document, removed_anchor.build())
    assert caught.value.kind is PropertiesEditFailureKind.PLACEMENT_ANCHOR_REMOVED

    shared_boundary = EditTransactionBuilder(document)
    shared_boundary.insert_property(
        document.node_ref(),
        JavaString.from_unicode("x"),
        JavaString.from_unicode("0"),
        AssociationPlacement("Start"),
    ).insert_property(
        document.node_ref(),
        JavaString.from_unicode("y"),
        JavaString.from_unicode("0"),
        AssociationPlacement("Before", anchor=first),
    )
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(document, shared_boundary.build())
    assert caught.value.kind is PropertiesEditFailureKind.OVERLAPPING_OWNERSHIP
    assert document.render() == b"a=1\nb=2\n"


def test_snapshot_role_recovery_and_resource_contracts_are_enforced():
    # WrongSnapshot, WrongRole, Recovered, and ResourceLimit gates
    # (edit.rs:1394-1438).
    document = reader(b"a=1\n")
    other = reader(b"a=1\n")
    wrong_snapshot = EditTransactionBuilder(document)
    wrong_snapshot.semantic_value(
        other.properties[0].node, JavaString.from_unicode("x")
    )
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(document, wrong_snapshot.build())
    assert caught.value.kind is PropertiesEditFailureKind.WRONG_SNAPSHOT

    wrong_role = EditTransactionBuilder(document)
    wrong_role.semantic_value(document.node_ref(), JavaString.from_unicode("x"))
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(document, wrong_role.build())
    assert caught.value.kind is PropertiesEditFailureKind.WRONG_ROLE

    recovered = reader(b"bad=\\u12G4\n")
    transaction = EditTransactionBuilder(recovered).build()
    with pytest.raises(PropertiesEditFailure) as caught:
        commit(recovered, transaction)
    assert caught.value.kind is PropertiesEditFailureKind.RECOVERED_DOCUMENT
    assert caught.value.code == "core.edit.incomplete-target@1"


def test_dry_run_patch_proof_and_conflict_atomicity():
    # Case edit.dry-run-patch-proof-conflict-atomicity
    # (java-properties-v1.json:111-114).
    document = reader(b"a=one\nb=two\n")
    builder = EditTransactionBuilder(document)
    builder.rename_property(
        document.properties[0].node, JavaString.from_unicode("first")
    ).semantic_value(
        document.properties[1].node, JavaString.from_unicode("changed")
    )
    transaction = builder.build()
    commit_result = commit(document, transaction)
    assert text(commit_result.document) == "first=one\nb=changed\n"
    assert len(commit_result.change_set.source_edits) == 2

    # Patch replay: the derived patch reapplies to the base and
    # reproduces the exact committed bytes.
    replayed = commit_result.source_patch.apply(
        document.source,
        SourcePatchLimits(
            source=SourceLimits(),
            max_replacements=len(transaction.operations),
        ),
    )
    assert replayed.bytes() == commit_result.document.render()

    # Untouched-byte proof verifies over the committed replacements.
    commit_result.untouched_proof.verify(
        document.source,
        commit_result.document.source,
        list(commit_result.source_patch.replacements),
    )

    # Dry-run produces the identical patch and operation surface.
    plan = dry_run(
        document, transaction, EditPlanSourceId.new("fixture.properties")
    )
    assert plan.source_patch() == commit_result.source_patch
    assert len(plan.operations) == 2
    assert plan.operations[0].operation.to_string() == "java-properties.edit.rename-property@1"
    assert plan.base_digest() == document.source.digest()
    assert plan.target_digest() == commit_result.document.source.digest()


def test_empty_transaction_is_a_verified_identity_transition():
    # An empty transaction is a verified identity transition
    # (edit.rs:1531-1539).
    document = reader(b"a=1\n")
    transaction = EditTransactionBuilder(document).build()
    commit_result = commit(document, transaction)
    assert commit_result.document.render() == document.render()
    assert commit_result.source_patch.replacements == ()
    assert commit_result.change_set.source_edits == ()
