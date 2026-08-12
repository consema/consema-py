"""Edit golden transcriptions, duplicate-group semantics, and patch
round-trips (INI).

Cases covered here (conformance/vectors/ini-v1.json, suite
"consema.ini.conformance@1"):

- edit.all-eight-operations (lines 90-100): all eight operations produce
  the exact expected bytes, one source edit each;
- edit.dry-run-patch-proof-and-atomic-failure (104-105): dry-run equals
  commit, the derived SourcePatch replays against the base and reproduces
  the committed bytes, the untouched-byte proof verifies, a foreign
  snapshot fails with core.edit.wrong-snapshot@1, and the base document
  remains unchanged.

RFC 0009 §12 contract facts pinned here: duplicate/case-collision rules
are validated before any patch exists (docs/rfcs/0009-ini-family-profiles-
v1.md:463-466: Rename validates portable character rules, Windows ASCII
case equivalence, or Python optionxform collisions); removing a section
removes its owned entries atomically without reparenting (lines 465-466);
Windows keeps ordered case-equivalent occurrences (RFC 0009 §6, lines
207-213); Python multiline entries own their continuations only
(edit.rs:2347-2356); comments are never moved or deleted without explicit
ownership (RFC 0009 §12, lines 459-462).
"""

from __future__ import annotations

import pytest

from consema.document.edit_plan import EditPlanSourceId
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import AssociationPlacement
from consema.ini import (
    IniEditFailure,
    IniEditFailureKind,
    IniEncodingSelection,
    IniParseLimits,
    IniProfile,
    RepresentationPolicy,
    commit,
    dry_run,
    parse,
)
from consema.ini.edit import EditTransactionBuilder

DEFAULT_LIMITS = IniParseLimits()


def portable(source: bytes):
    return parse(
        source, IniProfile.PORTABLE_V1, IniEncodingSelection.profile_default(), DEFAULT_LIMITS
    )


def windows(source: bytes):
    return parse(
        source, IniProfile.WINDOWS_V1, IniEncodingSelection.profile_default(), DEFAULT_LIMITS
    )


def python(source: bytes):
    return parse(
        source,
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


# ---------------------------------------------------------------------------
# edit.all-eight-operations (ini-v1.json:90-100)
# ---------------------------------------------------------------------------


def test_edit_all_eight_operations():
    # Case edit.all-eight-operations (ini-v1.json:90-100).
    document = portable(b"[one]\na=1\n[two]\nb=2\n")
    entry = document.entries[0]
    section = document.sections[0]

    builder = EditTransactionBuilder(document)
    builder.semantic_value(entry.node, "9", RepresentationPolicy.CANONICAL_FOR_PROFILE)
    assert commit(document, builder.build()).document.render() == b"[one]\na=9\n[two]\nb=2\n"

    builder = EditTransactionBuilder(document)
    builder.literal_value(entry.node, b"8")
    assert commit(document, builder.build()).document.render() == b"[one]\na=8\n[two]\nb=2\n"

    builder = EditTransactionBuilder(document)
    builder.insert_section(document.node_ref(), "three", AssociationPlacement("End"))
    assert (
        commit(document, builder.build()).document.render()
        == b"[one]\na=1\n[two]\nb=2\n[three]\n"
    )

    builder = EditTransactionBuilder(document)
    builder.remove_section(section.node)
    assert commit(document, builder.build()).document.render() == b"[two]\nb=2\n"

    builder = EditTransactionBuilder(document)
    builder.rename_section(section.node, "renamed")
    assert (
        commit(document, builder.build()).document.render()
        == b"[renamed]\na=1\n[two]\nb=2\n"
    )

    builder = EditTransactionBuilder(document)
    builder.insert_entry(section.node, "c", "3", AssociationPlacement("End"))
    assert (
        commit(document, builder.build()).document.render()
        == b"[one]\na=1\nc=3\n[two]\nb=2\n"
    )

    builder = EditTransactionBuilder(document)
    builder.remove_entry(entry.node)
    assert commit(document, builder.build()).document.render() == b"[one]\n[two]\nb=2\n"

    builder = EditTransactionBuilder(document)
    builder.rename_entry(entry.node, "renamed")
    assert (
        commit(document, builder.build()).document.render()
        == b"[one]\nrenamed=1\n[two]\nb=2\n"
    )


# ---------------------------------------------------------------------------
# edit.dry-run-patch-proof-and-atomic-failure (ini-v1.json:104-105)
# ---------------------------------------------------------------------------


def test_dry_run_patch_proof_and_atomic_failure():
    # Case edit.dry-run-patch-proof-and-atomic-failure (ini-v1.json:104-105).
    document = portable(b"; before\n[s]\nk=old\n; after\n")
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        document.entries[0].node, "new value", RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    transaction = builder.build()
    plan = dry_run(document, transaction, EditPlanSourceId.new("memory:ini-conformance"))
    commit_result = commit(document, transaction)
    assert plan.source_patch() == commit_result.source_patch
    assert commit_result.document.render() == b"; before\n[s]\nk=new value\n; after\n"

    # Patch round-trip: the derived patch reapplies to the base snapshot
    # and reproduces the exact committed bytes (RFC 0004 §16).
    replay = commit_result.source_patch.apply(document.source, SourcePatchLimits())
    assert replay.bytes() == commit_result.document.render()
    assert replay.digest() == commit_result.document.source.digest()

    commit_result.untouched_proof.verify(
        document.source,
        commit_result.document.source,
        list(commit_result.source_patch.replacements),
    )

    # A foreign snapshot fails atomically; the base stays byte-identical.
    other = portable(b"[x]\nk=other\n")
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        other.entries[0].node, "v", RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    with pytest.raises(IniEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is IniEditFailureKind.WRONG_SNAPSHOT
    assert caught.value.code == "core.edit.wrong-snapshot@1"
    assert document.render() == b"; before\n[s]\nk=old\n; after\n"


def test_patch_metadata_carries_operation_ids():
    # RFC 0004 §14 / edit.rs:1604-1627: each patch metadata key
    # operation.{index} equals the canonical id@version form.
    document = portable(b"[s]\nk=old\n")
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        document.entries[0].node, "new", RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    commit_result = commit(document, builder.build())
    assert (
        commit_result.source_patch.metadata.get("operation.0")
        == "ini.edit.replace-semantic-value@1"
    )


# ---------------------------------------------------------------------------
# duplicate-group semantics for edits (RFC 0009 §6/§12)
# ---------------------------------------------------------------------------


def test_windows_rename_keeps_ordered_case_equivalent_occurrences():
    # RFC 0009 §6 (docs/rfcs/0009-...:207-213): repeated/case-equivalent
    # keys stay ordered native facts marked as an ambiguity set; Windows
    # rename never rejects case equivalence (edit.rs:1048-1050).
    document = windows(b"[S]\r\nKey=1\r\nother=2\r\n")
    builder = EditTransactionBuilder(document)
    builder.rename_entry(document.entries[1].node, "KEY")
    result = commit(document, builder.build())
    assert [entry.comparison_key for entry in result.document.entries] == ["key", "key"]
    assert result.document.entries[0].duplicate_group == result.document.entries[1].duplicate_group


def test_python_optionxform_collision_is_rejected_before_a_patch_exists():
    # RFC 0009 §12 (docs/rfcs/0009-...:463-464): rename validates Python
    # optionxform collisions before any patch exists; the code is
    # ini.edit.case-collision@1 (edit.rs:1767).
    document = python(b"[S]\nKey=1\nother=2\n")
    builder = EditTransactionBuilder(document)
    builder.rename_entry(document.entries[1].node, "KEY")
    with pytest.raises(IniEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is IniEditFailureKind.KEY_COLLISION
    assert caught.value.code == "ini.edit.case-collision@1"


def test_portable_duplicate_key_insertion_is_rejected():
    # RFC 0009 §5: duplicate keys make formation Recovered, so a strict
    # edit that would create an exact duplicate fails with the frozen code.
    document = portable(b"[s]\na=1\n")
    builder = EditTransactionBuilder(document)
    builder.insert_entry(document.sections[0].node, "a", "2", AssociationPlacement("End"))
    with pytest.raises(IniEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is IniEditFailureKind.DUPLICATE_KEY
    assert caught.value.code == "core.edit.duplicate-key@1"


# ---------------------------------------------------------------------------
# ownership and representation guarantees (RFC 0009 §12)
# ---------------------------------------------------------------------------


def test_section_removal_owns_entries_atomically_but_not_comments():
    # RFC 0009 §12 (docs/rfcs/0009-...:465-466, 459-462): removing a
    # section removes its owned entries atomically; comments are not moved
    # or deleted without explicit ownership.
    document = python(b"[one]\nk=first\n  second\n\n  fourth\n# keep\n[two]\nx=y\n")
    builder = EditTransactionBuilder(document)
    builder.remove_section(document.sections[0].node)
    result = commit(document, builder.build())
    assert result.document.render() == b"# keep\n[two]\nx=y\n"
    assert len(result.document.entries) == 1


def test_python_multiline_entry_removal_owns_continuations_only():
    # RFC 0009 §7 continuation ownership (edit.rs:2347-2356).
    document = python(b"[S]\nmulti=first\n  second\n\n  fourth\n# keep\nnext=value\n")
    builder = EditTransactionBuilder(document)
    builder.remove_entry(document.entries[0].node)
    result = commit(document, builder.build())
    assert result.document.render() == b"[S]\n# keep\nnext=value\n"


def test_python_preserve_compatible_keeps_multiline_trivia():
    # edit.rs:1892-1917: PreserveCompatible retains the compatible
    # multiline representation line for line.
    source = b"[S]\nkey : first  \n\tsecond\t\n\n\tthird\nnext=x\n"
    document = python(source)
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        document.entries[0].node, "one\ntwo\n\nthree", RepresentationPolicy.PRESERVE_COMPATIBLE
    )
    result = commit(document, builder.build())
    assert result.document.render() == b"[S]\nkey : one  \n\ttwo\t\n\n\tthree\nnext=x\n"
    assert result.document.entries[0].value == "one\ntwo\n\nthree"


def test_windows_preserve_compatible_keeps_quotes():
    # edit.rs:1866-1889: semantic replacement preserves a compatible quote
    # representation; the fallback diagnostic ini.edit.canonical-fallback@1
    # is emitted for the unquoted whitespace case.
    document = windows(b"[S]\r\na='old'\r\nb=plain\r\n")
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        document.entries[0].node, " new ", RepresentationPolicy.PRESERVE_COMPATIBLE
    )
    builder.semantic_value(
        document.entries[1].node, " spaced ", RepresentationPolicy.PRESERVE_ELSE_CANONICAL
    )
    result = commit(document, builder.build())
    assert result.document.render() == b"[S]\r\na=' new '\r\nb=\" spaced \"\r\n"
    assert any(
        diagnostic.code == "ini.edit.canonical-fallback@1"
        for diagnostic in result.change_set.diagnostics
    )


def test_conflicting_edits_fail_before_a_patch_exists():
    # RFC 0004 §13: one operation removing a section while another edits
    # its owned entry is an ancestor-descendant conflict.
    document = portable(b"[one]\na=1\n[two]\nb=2\n")
    builder = EditTransactionBuilder(document)
    builder.remove_section(document.sections[0].node)
    builder.semantic_value(
        document.entries[0].node, "new", RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    with pytest.raises(IniEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is IniEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT
    assert caught.value.code == "core.edit.conflicting-edits@1"


def test_duplicate_target_fails_atomically():
    # edit.rs:328-332: more than one operation naming the same exact target
    # is rejected before any patch exists.
    document = portable(b"[s]\nk=old\n")
    builder = EditTransactionBuilder(document)
    builder.semantic_value(
        document.entries[0].node, "one", RepresentationPolicy.CANONICAL_FOR_PROFILE
    )
    builder.literal_value(document.entries[0].node, b"two")
    with pytest.raises(IniEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is IniEditFailureKind.DUPLICATE_TARGET


def test_recovered_documents_refuse_edits():
    # RFC 0009 §4: projection, materialization-from-document, and edit
    # commit require a Complete document (case
    # formation.recovery-never-fabricates-entry, ini-v1.json:42).
    recovered = portable(b"[s]\nbare\n")
    builder = EditTransactionBuilder(recovered)
    with pytest.raises(IniEditFailure) as caught:
        commit(recovered, builder.build())
    assert caught.value.kind is IniEditFailureKind.RECOVERED_DOCUMENT
    assert caught.value.code == "core.edit.incomplete-target@1"


def test_appending_after_eof_entry_introduces_one_profile_newline():
    # edit.rs:2179-2185.
    document = portable(b"[one]\na=1")
    builder = EditTransactionBuilder(document)
    builder.insert_section(document.node_ref(), "two", AssociationPlacement("End"))
    result = commit(document, builder.build())
    assert result.document.render() == b"[one]\na=1\n[two]\n"
