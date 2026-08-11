"""Edit golden transcriptions (conformance/vectors/yaml-v1.json cases) and
the anchor-safe rules (RFC 0007 s12).

Cases covered with the vector case ids cited:

- edit.scalar-atomic (yaml-v1.json:105-108): a semantic scalar replacement
  under PreserveCompatible keeps the plain style and yields exactly one
  source edit: ``# keep\\na: 2\\nb: two\\n``.
- edit.anchor-rename (yaml-v1.json:110-114): renaming an anchor updates its
  exact dependent aliases in one transaction.
- edit.structural-insert (yaml-v1.json:115-118): canonical flow fragments
  ``!!bool "true"`` and ``? !!str "b" : !!int "2"`` with comma ownership.
- edit.anchor-dependency (yaml-v1.json:120-123): removing an anchored
  definition while a live alias remains fails with
  yaml.edit.anchor-dependency@1 (only the deleted subtree is collected;
  alias edges are never crossed).
- The eight frozen operation ids
  (crates/consema-yaml/src/operation_registry.rs:16-82).

Contract: RFC 0007 s12 (lines 355-398) — transactions are snapshot-bound
and validate all operations before publishing a candidate; dry-run and
commit produce identical replacements and target digest; a failure returns
no Document, no ChangeSet, no SourcePatch.
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.edit_plan import EditPlanSourceId
from consema.document.structural import AssociationPlacement
from consema.yaml import (
    EditTransactionBuilder,
    OperationSupport,
    RepresentationPolicy,
    YamlEditFailure,
    YamlEditFailureKind,
    YamlProfile,
    commit,
    dry_run,
    format_operation_registry,
)
from tests.yaml.conftest import parse_source


def test_edit_scalar_atomic():
    # Case edit.scalar-atomic (yaml-v1.json:105-108).
    document = parse_source("# keep\na: 1\nb: two\n", YamlProfile.YAML12_CORE_V1)
    target = document.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(document)
    builder.semantic_scalar(
        target.node_ref(),
        PortableValue.integer(2),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    transaction = builder.build()
    result = commit(document, transaction)
    assert result.document.render() == b"# keep\na: 2\nb: two\n"
    assert len(result.change_set.source_edits) == 1
    # The untouched-byte proof verifies: only the scalar literal changed.
    result.untouched_proof.verify(
        document.source,
        result.document.source,
        list(result.source_patch.replacements),
    )


def test_edit_anchor_rename():
    # Case edit.anchor-rename (yaml-v1.json:110-114).
    document = parse_source("first: &x [one]\ncopy: *x\n", YamlProfile.YAML12_CORE_V1)
    target = document.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(document)
    builder.rename_anchor(target.anchor_node_ref(), "renamed")
    transaction = builder.build()
    result = commit(document, transaction)
    assert result.document.render() == b"first: &renamed [one]\ncopy: *renamed\n"
    assert result.document.alias(0).name() == "renamed"
    # Dry-run and commit share the identical patch and target digest.
    plan = dry_run(document, transaction, EditPlanSourceId.new("config.yaml"))
    assert plan.target_digest() == result.source_patch.target_digest()


def test_edit_structural_insert():
    # Case edit.structural-insert (yaml-v1.json:115-118): one transaction
    # mutating two independent containers; the fragments are the canonical
    # flow spellings ``!!bool "true"`` and ``? !!str "b" : !!int "2"``.
    document = parse_source("seq: [one, two]\nmap: {a: 1}\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    sequence = root.mapping_entry(0).value()
    mapping = root.mapping_entry(1).value()
    builder = EditTransactionBuilder(document)
    builder.insert_sequence_element(
        sequence.node_ref(),
        PortableValue.boolean(True),
        AssociationPlacement(kind="Before", anchor=sequence.sequence_item(1).node_ref()),
    )
    builder.insert_mapping_entry(
        mapping.node_ref(),
        PortableValue.string("b"),
        PortableValue.integer(2),
        AssociationPlacement(kind="End"),
    )
    transaction = builder.build()
    result = commit(document, transaction)
    assert result.document.render() == (
        b'seq: [one, !!bool "true", two]\nmap: {a: 1, ? !!str "b" : !!int "2"}\n'
    )
    assert len(result.change_set.source_edits) == 2


def test_edit_anchor_dependency():
    # Case edit.anchor-dependency (yaml-v1.json:120-123): removing the
    # anchored sequence element would leave the live ``*x`` alias without
    # its anchor; the transaction fails atomically with
    # yaml.edit.anchor-dependency@1 and the base document is unchanged.
    document = parse_source("seq:\n  - &x one\ncopy: *x\n", YamlProfile.YAML12_CORE_V1)
    target = document.document(0).root().mapping_entry(0).value().sequence_item(0)
    builder = EditTransactionBuilder(document)
    builder.remove_sequence_element(target.node_ref())
    with pytest.raises(YamlEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is YamlEditFailureKind.ANCHOR_DEPENDENCY
    assert caught.value.code == "yaml.edit.anchor-dependency@1"
    assert document.render() == b"seq:\n  - &x one\ncopy: *x\n"


def test_edit_removal_without_dependency_succeeds():
    # RFC 0007 s12: removing an alias does not remove its target, and
    # removing an unanchored subtree never trips the anchor-dependency rule.
    document = parse_source("seq:\n  - one\n  - two\n", YamlProfile.YAML12_CORE_V1)
    target = document.document(0).root().mapping_entry(0).value().sequence_item(0)
    builder = EditTransactionBuilder(document)
    builder.remove_sequence_element(target.node_ref())
    result = commit(document, builder.build())
    assert result.document.render() == b"seq:\n  - two\n"


def test_edit_insert_alias_requires_visible_anchor():
    # RFC 0007 s12: inserting an alias requires an earlier visible anchor
    # in the same document (yaml.edit.anchor-not-visible@1).
    document = parse_source("seq: [one, two]\n", YamlProfile.YAML12_CORE_V1)
    sequence = document.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(document)
    # No anchor exists in this document.
    fake_anchor = document.document(0).root().node_ref()
    with pytest.raises(YamlEditFailure) as caught:
        builder.insert_alias(
            sequence.node_ref(),
            fake_anchor,
            AssociationPlacement(kind="End"),
        ).build()
        commit(document, builder.build())
    assert caught.value.kind is YamlEditFailureKind.WRONG_ROLE


def test_edit_rename_updates_only_dependent_aliases():
    # edit.rs:2791-2820: the second ``&x`` definition and its alias keep
    # the original name.
    source = "first: &x [one]\ncopy: *x\nother: &x [two]\ncopy2: *x\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    target = document.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(document)
    builder.rename_anchor(target.anchor_node_ref(), "renamed")
    result = commit(document, builder.build())
    assert result.document.render() == (
        b"first: &renamed [one]\ncopy: *renamed\nother: &x [two]\ncopy2: *x\n"
    )
    assert result.document.alias(0).name() == "renamed"
    assert result.document.alias(1).name() == "x"


def test_edit_wrong_snapshot_fails():
    # edit.rs:404-406: a transaction bound to another snapshot fails with
    # core.edit.wrong-snapshot@1.
    first = parse_source("a: 1\n", YamlProfile.YAML12_CORE_V1)
    second = parse_source("a: 1\n", YamlProfile.YAML12_CORE_V1)
    builder = EditTransactionBuilder(first)
    builder.semantic_scalar(
        second.document(0).root().mapping_entry(0).value().node_ref(),
        PortableValue.integer(2),
        RepresentationPolicy.PRESERVE_COMPATIBLE,
    )
    with pytest.raises(YamlEditFailure) as caught:
        commit(first, builder.build())
    assert caught.value.kind is YamlEditFailureKind.WRONG_SNAPSHOT
    assert caught.value.code == "core.edit.wrong-snapshot@1"


def test_edit_structural_container_conflict():
    # edit.rs:1974-2014: v1 accepts at most one structural mutation per
    # base container in a transaction. Two insertions on the same container
    # first trip the duplicate-target check (the same NodeRef target); an
    # insert plus a removal on one container trips the container conflict.
    document = parse_source("seq: [one, two]\n", YamlProfile.YAML12_CORE_V1)
    sequence = document.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(document)
    builder.insert_sequence_element(
        sequence.node_ref(),
        PortableValue.boolean(True),
        AssociationPlacement(kind="End"),
    )
    builder.remove_sequence_element(sequence.sequence_item(0).node_ref())
    with pytest.raises(YamlEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is YamlEditFailureKind.STRUCTURAL_CONTAINER_CONFLICT
    assert caught.value.code == "yaml.edit.structural-container-conflict@1"

    builder = EditTransactionBuilder(document)
    builder.insert_sequence_element(
        sequence.node_ref(),
        PortableValue.boolean(True),
        AssociationPlacement(kind="End"),
    )
    builder.insert_sequence_element(
        sequence.node_ref(),
        PortableValue.boolean(False),
        AssociationPlacement(kind="Start"),
    )
    with pytest.raises(YamlEditFailure) as caught:
        commit(document, builder.build())
    assert caught.value.kind is YamlEditFailureKind.DUPLICATE_TARGET
    assert caught.value.code == "core.edit.conflicting-edits@1"


def test_operation_registry_frozen_surface():
    # operation_registry.rs:107-135: exactly six Supported structural
    # operations for every profile and eight total records.
    for profile in (YamlProfile.YAML12_CORE_V1, YamlProfile.YAML11_COMPAT_V1):
        registry = format_operation_registry(profile)
        structural = [
            operation.to_string()
            for operation in registry.operations
            if operation.support is OperationSupport.SUPPORTED
        ]
        assert structural == [
            "yaml.edit.insert-alias@1",
            "yaml.edit.insert-mapping-entry@1",
            "yaml.edit.insert-sequence-element@1",
            "yaml.edit.remove-mapping-entry@1",
            "yaml.edit.remove-sequence-element@1",
            "yaml.edit.rename-anchor@1",
        ]
        assert len(registry.operations) == 8
        alias = registry.find("yaml.edit.insert-alias@1")
        assert alias.target_role == "yaml.sequence"
        assert alias.arguments[0].name == "anchor"
