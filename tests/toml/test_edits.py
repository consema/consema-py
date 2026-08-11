"""Edit intent documents: atomic scalar and structural transactions.

Vector cases transcribed: toml.edit.literal-minimal,
toml.edit.reject-unrepresentable (conformance/vectors/toml-v1.json);
the Rust transaction tests of crates/consema-toml/src/edit.rs are the
semantic arbitration (byte-exact expected renders), RFC 0004 §12/§13 the
contract, and the operation ids the registry
(operation_registry.rs:16-74).
"""

from __future__ import annotations

import pytest

from consema.core import PortableValue
from consema.document.edit_plan import EditPlanSourceId
from consema.document.limits import ParseLimits
from consema.document.structural import AssociationPlacement
from consema.toml import (
    EditTransactionBuilder,
    RepresentationPolicy,
    TomlEditFailure,
    TomlEditFailureKind,
    TomlProfile,
    parse,
)


def _document(source: bytes):
    return parse(source, TomlProfile.TOML10_V1, ParseLimits())


def _root_item(document, name):
    for entry in document.root().table_entries():
        if entry.name() == name:
            return entry.item()
    raise AssertionError(f"no root entry {name}")


def _root_entry(document, name):
    for entry in document.root().table_entries():
        if entry.name() == name:
            return entry
    raise AssertionError(f"no root entry {name}")


def test_vector_literal_minimal():
    """toml.edit.literal-minimal: an exact literal replacement touches
    only the scalar span and preserves surrounding trivia."""
    document = _document(b"hex = 0x2A # keep\n")
    transaction = (
        EditTransactionBuilder(document)
        .literal_scalar(_root_item(document, "hex").node_ref(), b"0x2B")
        .build()
    )
    commit = document.commit(transaction)
    assert commit.document.render() == b"hex = 0x2B # keep\n"
    assert len(commit.change_set.source_edits) == 1
    source_edit = commit.change_set.source_edits[0]
    assert source_edit.replacement == b"0x2B"


def test_vector_reject_unrepresentable():
    """toml.edit.reject-unrepresentable: a non-canonical NaN payload fails
    with UnsupportedSemanticValue and leaves the source unchanged."""
    document = _document(b"float = 1.0\n")
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            _root_item(document, "float").node_ref(),
            PortableValue.binary_float64(0x7FF8000000000001),
            RepresentationPolicy.CANONICAL_FOR_PROFILE,
        )
        .build()
    )
    with pytest.raises(TomlEditFailure) as caught:
        document.commit(transaction)
    assert caught.value.kind is TomlEditFailureKind.UNSUPPORTED_SEMANTIC_VALUE
    assert document.render() == b"float = 1.0\n"


def test_semantic_scalar_replacement_preserves_category():
    """PreserveCompatible replaces the value keeping the native scalar
    category; the commit renders canonically."""
    document = _document(b"name = 'old'\nfloat = 1.0\n")
    transaction = (
        EditTransactionBuilder(document)
        .semantic_scalar(
            _root_item(document, "name").node_ref(),
            PortableValue.string("new\nvalue"),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .semantic_scalar(
            _root_item(document, "float").node_ref(),
            PortableValue.binary_float64(0x8000000000000000),  # -0.0
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    commit = document.commit(transaction)
    assert commit.document.render() == b'name = "new\\nvalue"\nfloat = -0.0\n'
    assert len(commit.change_set.node_mappings) == 2
    assert all(mapping.new is not None for mapping in commit.change_set.node_mappings)


def test_edit_round_trip_change_set_patch_proof():
    """One edit round trip: ChangeSet -> SourcePatch applies to the base
    and reproduces the committed bytes; the untouched proof verifies."""
    document = _document(b"hex = 0x2A # keep\nname = 'old'\n")
    transaction = (
        EditTransactionBuilder(document)
        .literal_scalar(_root_item(document, "hex").node_ref(), b"0x2B")
        .semantic_scalar(
            _root_item(document, "name").node_ref(),
            PortableValue.string("new"),
            RepresentationPolicy.PRESERVE_COMPATIBLE,
        )
        .build()
    )
    commit = document.commit(transaction)
    from consema.document.source_patch import SourcePatchLimits
    from consema.document.source import SourceLimits

    patch_limits = SourcePatchLimits(
        source=SourceLimits.unbounded(),
        max_replacements=10,
        max_patch_bytes=10_000,
    )
    reapplied = commit.source_patch.apply(document.source(), patch_limits)
    assert reapplied.bytes() == commit.document.render()
    commit.untouched_proof.verify(
        document.source(), commit.document.source(), list(commit.source_patch.replacements)
    )


def test_structural_insert_entry_root_and_table():
    """insert-entry preserves table ownership: a root insertion lands
    before the [service] header, a standard-table insertion after its
    entries (edit.rs:1840-1892)."""
    document = _document(b"root = 1\n\n[service]\nport = 80\n")
    service = _root_entry(document, "service").item()

    root_commit = document.commit(
        EditTransactionBuilder(document)
        .insert_entry(
            document.root().node_ref(),
            "enabled",
            PortableValue.boolean(True),
            AssociationPlacement("End"),
        )
        .build()
    )
    assert root_commit.document.render() == b'root = 1\n\n"enabled" = true\n[service]\nport = 80\n'

    table_commit = document.commit(
        EditTransactionBuilder(document)
        .insert_entry(
            service.node_ref(),
            "host",
            PortableValue.string("localhost"),
            AssociationPlacement("End"),
        )
        .build()
    )
    assert table_commit.document.render() == (
        b'root = 1\n\n[service]\nport = 80\n"host" = "localhost"'
    )


def test_inline_table_operations_preserve_association_identity():
    """Inline-table insert/rename/remove touch exact association spans
    (edit.rs:1894-1940)."""
    document = _document(b"point = { a = 1, b = 2 }\n")
    point = _root_entry(document, "point").item()
    entries = point.table_entries()

    insert = document.commit(
        EditTransactionBuilder(document)
        .insert_entry(
            point.node_ref(),
            "axis",
            PortableValue.sequence([PortableValue.boolean(True)]),
            AssociationPlacement("Before", entries[1].node_ref()),
        )
        .build()
    )
    assert insert.document.render() == b'point = { a = 1, "axis" = [true],b = 2 }\n'

    rename = document.commit(
        EditTransactionBuilder(document).rename_entry(entries[1].node_ref(), "beta").build()
    )
    assert rename.document.render() == b'point = { a = 1, "beta" = 2 }\n'

    remove = document.commit(
        EditTransactionBuilder(document).remove_entry(entries[0].node_ref()).build()
    )
    assert remove.document.render() == b"point = {  b = 2 }\n"


def test_array_insert_and_remove_cover_empty_and_commented_arrays():
    """Array element insert/remove around empty and commented arrays
    (edit.rs:1942-1977)."""
    empty = _document(b"items = [ ]\n")
    array = _root_entry(empty, "items").item()
    start = empty.commit(
        EditTransactionBuilder(empty)
        .insert_array_element(
            array.node_ref(),
            PortableValue.integer(1),
            AssociationPlacement("Start"),
        )
        .build()
    )
    assert start.document.render() == b"items = [1 ]\n"

    document = _document(b"items = [1, # keep\n 2, 3,]\n")
    array = _root_entry(document, "items").item()
    elements = array.array_elements()
    insert = document.commit(
        EditTransactionBuilder(document)
        .insert_array_element(
            array.node_ref(),
            PortableValue.string("end"),
            AssociationPlacement("After", elements[2].node_ref()),
        )
        .build()
    )
    assert insert.document.render() == b'items = [1, # keep\n 2, 3,"end",]\n'

    remove = document.commit(
        EditTransactionBuilder(document)
        .remove_array_element(elements[1].node_ref())
        .build()
    )
    assert remove.document.render() == b"items = [1, # keep\n  3,]\n"


def test_conflicts_fail_atomically():
    """Duplicate keys, duplicate targets, removed anchors, table removal,
    and cross-container anchors fail without changing the base
    (edit.rs:1979-2093)."""
    document = _document(b"a = 1\nb = 2\n\n[service]\nport = 80\n")
    entries = document.root().table_entries()
    a = next(entry for entry in entries if entry.name() == "a")
    b = next(entry for entry in entries if entry.name() == "b")
    service = next(entry for entry in entries if entry.name() == "service")

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .insert_entry(
                document.root().node_ref(),
                "a",
                PortableValue.boolean(True),
                AssociationPlacement("Start"),
            )
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.DUPLICATE_KEY

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document).rename_entry(b.node_ref(), "a").build()
        )
    assert caught.value.kind is TomlEditFailureKind.DUPLICATE_KEY

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .remove_entry(a.node_ref())
            .insert_entry(
                document.root().node_ref(),
                "x",
                PortableValue.boolean(True),
                AssociationPlacement("Before", a.node_ref()),
            )
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.PLACEMENT_ANCHOR_REMOVED

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .rename_entry(a.node_ref(), "x")
            .remove_entry(a.node_ref())
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.DUPLICATE_TARGET

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document).remove_entry(service.node_ref()).build()
        )
    assert caught.value.kind is TomlEditFailureKind.UNSUPPORTED_OPERATION

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .insert_entry(
                service.item().node_ref(),
                "x",
                PortableValue.boolean(True),
                AssociationPlacement("Before", a.node_ref()),
            )
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.TARGET_NOT_FOUND

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .insert_entry(
                document.root().node_ref(),
                "x",
                PortableValue.boolean(True),
                AssociationPlacement("End"),
            )
            .insert_entry(
                document.root().node_ref(),
                "y",
                PortableValue.boolean(False),
                AssociationPlacement("End"),
            )
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.OVERLAPPING_OWNERSHIP

    with pytest.raises(TomlEditFailure) as caught:
        document.commit(
            EditTransactionBuilder(document)
            .semantic_scalar(
                a.item().node_ref(),
                PortableValue.integer(3),
                RepresentationPolicy.PRESERVE_COMPATIBLE,
            )
            .remove_entry(a.node_ref())
            .build()
        )
    assert caught.value.kind is TomlEditFailureKind.ANCESTOR_DESCENDANT_CONFLICT

    assert document.render() == b"a = 1\nb = 2\n\n[service]\nport = 80\n"


def test_dry_run_matches_commit_and_redacts_secrets():
    """Dry-run and commit produce the same replacement set and target
    digest; summaries never contain raw edited values (edit.rs:2122-2155)."""
    document = _document(b"value = 1\n")
    transaction = (
        EditTransactionBuilder(document)
        .insert_entry(
            document.root().node_ref(),
            "secret-key",
            PortableValue.string("secret-value"),
            AssociationPlacement("End"),
        )
        .build()
    )
    plan = document.dry_run(transaction, EditPlanSourceId.new("config.toml"))
    commit = document.commit(transaction)
    assert plan.replacements() == commit.source_patch.replacements
    assert plan.target_digest() == commit.source_patch.target_digest
    for operation in plan.operations:
        for argument in operation.arguments.values():
            assert "secret" not in argument


def test_wrong_snapshot_rejected():
    """A transaction bound to another snapshot is rejected."""
    first = _document(b"x = 1\n")
    second = _document(b"x = 2\n")
    transaction = (
        EditTransactionBuilder(first)
        .literal_scalar(_root_item(first, "x").node_ref(), b"3")
        .build()
    )
    with pytest.raises(TomlEditFailure) as caught:
        second.commit(transaction)
    assert caught.value.kind is TomlEditFailureKind.WRONG_SNAPSHOT
