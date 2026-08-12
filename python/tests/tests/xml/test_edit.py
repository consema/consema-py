"""Edit intent tests: the frozen eight-operation XML surface (RFC 0012
§11, RFC 0004 §13-§16).

Authority: conformance/vectors/xml-1-0-safe-v1.json (case ids cited per
test); crates/consema-xml/src/edit.rs (byte/registry arbitration);
RFC 0012 §11 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:374-404): each
operation targets one exact NodeRef; commit preserves every byte outside
operation-owned spans, reparses the target, produces a complete ChangeSet,
derives an UntouchedByteProof, and emits a replayable SourcePatch.

These tests are intent documents written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7); no
gate is claimed to have passed.
"""

from __future__ import annotations

import pytest

from consema.document.edit_plan import EditPlanSourceId
from consema.document.source import SourceLimits
from consema.document.source_patch import SourcePatchLimits
from consema.document.structural import NodeRole

from consema.xml import (
    AttributePlacement,
    ContentPlacement,
    EditTransactionBuilder,
    NameFacts,
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    parse,
)
from consema.xml.errors import XmlEditFailure, XmlEditFailureKind

from conftest import find_case, form_document


def _doc(source: bytes):
    return parse(
        source, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
    )


def _element_ref(doc, name, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "element":
            if content.data.qname.local == name:
                if occurrence == ordinal:
                    return doc.node_ref(content.data.index, NodeRole.XML_ELEMENT)
                occurrence += 1
    raise AssertionError(f"element {name} occurrence {ordinal} not found")


def _attribute_ref(doc, name, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "element":
            for attribute in content.data.attributes:
                if attribute.qname.local == name:
                    if occurrence == ordinal:
                        return doc.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE)
                    occurrence += 1
    raise AssertionError(f"attribute {name} occurrence {ordinal} not found")


def _text_ref(doc, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "text":
            if occurrence == ordinal:
                return doc.occurrence_node_ref(content.data.ordinal, NodeRole.XML_TEXT)
            occurrence += 1
    raise AssertionError(f"text occurrence {ordinal} not found")


# ---------------------------------------------------------------------------
# Golden vector transcriptions
# ---------------------------------------------------------------------------


def test_vector_set_attribute_value(xml_vectors):
    """Golden transcription of ``xml.edit.set-attribute-value``
    (xml-1-0-safe-v1.json:436-453): only the value span between the quotes
    is owned."""
    case = find_case(xml_vectors, "xml.edit.set-attribute-value")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.set_attribute_value(_attribute_ref(doc, "a"), "2")
    commit = doc.commit(builder.build())
    assert commit.document.render() == b'<root a="2"/>'


def test_vector_insert_and_remove_element(xml_vectors):
    """Golden transcription of ``xml.edit.insert-and-remove-element``
    (xml-1-0-safe-v1.json:454-475): insert-element appends mixed content
    and remove-element consumes the whole subtree."""
    case = find_case(xml_vectors, "xml.edit.insert-and-remove-element")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.insert_element(
        doc.root().node_ref(),
        NameFacts.new(None, "x", None),
        "c",
        ContentPlacement.end(),
    )
    builder.remove_element(_element_ref(doc, "a"))
    commit = doc.commit(builder.build())
    assert commit.document.render() == b"<root><x>c</x></root>"


def test_vector_rename_element_both_tags(xml_vectors):
    """Golden transcription of ``xml.edit.rename-element-both-tags``
    (xml-1-0-safe-v1.json:476-493): both the start-tag and end-tag names
    are renamed."""
    case = find_case(xml_vectors, "xml.edit.rename-element-both-tags")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.rename_element(_element_ref(doc, "old"), NameFacts.new(None, "new", None))
    commit = doc.commit(builder.build())
    assert commit.document.render() == b"<new><child>t</child></new>"


def test_vector_insert_attribute_end(xml_vectors):
    """Golden transcription of ``xml.edit.insert-attribute-end``
    (xml-1-0-safe-v1.json:494-513): End placement inserts before the
    closing ``/>`` or ``>``."""
    case = find_case(xml_vectors, "xml.edit.insert-attribute-end")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.insert_attribute(
        _element_ref(doc, "root"),
        NameFacts.new(None, "b", None),
        "2",
        AttributePlacement.end(),
    )
    commit = doc.commit(builder.build())
    assert commit.document.render() == b'<root a="1" b="2"/>'


def test_vector_remove_attribute(xml_vectors):
    """Golden transcription of ``xml.edit.remove-attribute``
    (xml-1-0-safe-v1.json:514-530): removal owns the leading whitespace."""
    case = find_case(xml_vectors, "xml.edit.remove-attribute")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.remove_attribute(_attribute_ref(doc, "b"))
    commit = doc.commit(builder.build())
    assert commit.document.render() == b'<root a="1"/>'


def test_vector_replace_text_occurrence(xml_vectors):
    """Golden transcription of ``xml.edit.replace-text-occurrence``
    (xml-1-0-safe-v1.json:531-548): replace-text targets one exact text
    occurrence by its document-order ordinal."""
    case = find_case(xml_vectors, "xml.edit.replace-text-occurrence")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.replace_text(_text_ref(doc, 1), "TWO")
    commit = doc.commit(builder.build())
    assert commit.document.render() == b"<root><a>one</a><b>TWO</b></root>"


def test_vector_rename_attribute(xml_vectors):
    """Golden transcription of ``xml.edit.rename-attribute``
    (xml-1-0-safe-v1.json:549-566): the attribute name span is replaced and
    the value is preserved."""
    case = find_case(xml_vectors, "xml.edit.rename-attribute")
    doc = form_document(case)
    builder = EditTransactionBuilder(doc)
    builder.rename_attribute(_attribute_ref(doc, "a"), NameFacts.new(None, "renamed", None))
    commit = doc.commit(builder.build())
    assert commit.document.render() == b'<root renamed="1"/>'


# ---------------------------------------------------------------------------
# ReplaceText excludes CDATA (RoleXmlText only)
# ---------------------------------------------------------------------------


def test_replace_text_rejects_cdata_target():
    """replace-text targets RoleXmlText only; a CDATA occurrence is never a
    target (RFC 0012 §11; the Rust text_for maps the role mismatch to
    WrongSnapshot, edit.rs:1101-1108)."""
    doc = _doc(b"<root><![CDATA[x]]></root>")
    cdata_ordinal = next(
        content.data.ordinal
        for content in doc.nodes()
        if content.kind.value == "cdata"
    )
    builder = EditTransactionBuilder(doc)
    builder.replace_text(
        doc.occurrence_node_ref(cdata_ordinal, NodeRole.XML_CDATA), "y"
    )
    with pytest.raises(XmlEditFailure) as raised:
        doc.commit(builder.build())
    assert raised.value.kind in (
        XmlEditFailureKind.WRONG_SNAPSHOT,
        XmlEditFailureKind.WRONG_ROLE,
    )
    assert raised.value.code == "core.edit.wrong-snapshot@1"


# ---------------------------------------------------------------------------
# Commit artifacts (RFC 0004 §13-§16)
# ---------------------------------------------------------------------------


def test_commit_artifacts_are_complete_and_verifiable():
    """One atomic commit produces the new Document, a complete ChangeSet,
    a replayable SourcePatch, and a verifiable UntouchedByteProof
    (edit.rs:410-570; RFC 0004 §13-§16)."""
    doc = _doc(b"<root>old</root>")
    builder = EditTransactionBuilder(doc)
    builder.replace_text(_text_ref(doc, 0), "new")
    transaction = builder.build()
    commit = doc.commit(transaction)
    assert commit.document.render() == b"<root>new</root>"
    assert commit.change_set.old_snapshot == doc.snapshot_identity()
    assert commit.change_set.new_snapshot == commit.document.snapshot_identity()
    assert len(commit.change_set.source_edits) == 1
    assert len(commit.change_set.node_mappings) == 1
    assert commit.change_set.node_mappings[0].status.value == "Replaced"
    commit.untouched_proof.verify(
        doc.source(), commit.document.source(), list(commit.source_patch.replacements)
    )
    patch_limits = SourcePatchLimits(
        source=SourceLimits.unbounded(), max_replacements=16, max_patch_bytes=1 << 20
    )
    reapplied = commit.source_patch.apply(doc.source(), patch_limits)
    assert reapplied.bytes() == commit.document.render()
    assert commit.source_patch.metadata == {"operation.0": "xml.edit.replace-text@1"}
    # An attribute-value replacement maps Unmapped: attributes are not
    # arena content nodes, so no reparsed node is uniquely located by the
    # value span (find_node_by_span iterates content nodes only,
    # edit.rs:1309-1336).
    doc = _doc(b'<root a="1"/>')
    builder = EditTransactionBuilder(doc)
    builder.set_attribute_value(_attribute_ref(doc, "a"), "2")
    commit = doc.commit(builder.build())
    assert commit.change_set.node_mappings[0].status.value == "Unmapped"
    assert commit.change_set.node_mappings[0].reason == "reparsed-node-not-uniquely-located"


def test_dry_run_matches_commit_exactly():
    """Dry-run performs every deterministic validation and byte-planning
    step; dry-run and commit have identical replacement sets and target
    digest (edit.rs:572-588; RFC 0004 §14, lines 338-356)."""
    doc = _doc(b"<root>old</root>")
    builder = EditTransactionBuilder(doc)
    builder.replace_text(_text_ref(doc, 0), "new")
    transaction = builder.build()
    commit = doc.commit(transaction)
    plan = doc.dry_run(transaction, EditPlanSourceId.new("intent.xml"))
    assert plan.base_digest() == commit.source_patch.base_digest
    assert plan.target_digest() == commit.source_patch.target_digest
    assert plan.replacements() == commit.source_patch.replacements
    assert plan.profile == doc.profile()
    assert [summary.operation.to_string() for summary in plan.operations] == [
        "xml.edit.replace-text@1"
    ]


def test_transaction_conflicts_fail_atomically():
    """Two operations on the same exact target are a conflict; no partial
    document is published (validate_dependencies, edit.rs:598-641)."""
    doc = _doc(b'<root a="1"/>')
    attribute = _attribute_ref(doc, "a")
    builder = EditTransactionBuilder(doc)
    builder.set_attribute_value(attribute, "2")
    builder.set_attribute_value(attribute, "3")
    with pytest.raises(XmlEditFailure) as raised:
        doc.commit(builder.build())
    assert raised.value.kind is XmlEditFailureKind.CONFLICTING_EDITS


def test_wrong_snapshot_is_rejected():
    """A transaction bound to another snapshot is rejected before any span
    is computed (edit.rs:413-416)."""
    first = _doc(b"<root/>")
    second = _doc(b"<root/>")
    builder = EditTransactionBuilder(second)
    transaction = builder.build()
    with pytest.raises(XmlEditFailure) as raised:
        first.commit(transaction)
    assert raised.value.kind is XmlEditFailureKind.WRONG_SNAPSHOT


def test_root_element_cannot_be_removed():
    """The document element cannot be removed (edit.rs:1011-1016)."""
    doc = _doc(b"<root/>")
    builder = EditTransactionBuilder(doc)
    builder.remove_element(doc.root().node_ref())
    with pytest.raises(XmlEditFailure) as raised:
        doc.commit(builder.build())
    assert raised.value.kind is XmlEditFailureKind.CANNOT_REMOVE_ROOT


def test_duplicate_expanded_attribute_is_rejected():
    """Inserting to a duplicate expanded attribute fails before commit
    (edit.rs:1289-1306). The duplicate check needs name facts that promise
    a namespace: unprefixed facts promise none, so the duplicate surfaces
    as a reparse failure instead (the reparsed document would be
    Recovered, NewDocumentFormationFailed, edit.rs:467-476)."""
    doc = _doc(b'<root xmlns:p="urn:u" p:a="1"/>')
    builder = EditTransactionBuilder(doc)
    builder.insert_attribute(
        _element_ref(doc, "root"),
        NameFacts.new("p", "a", "urn:u"),
        "2",
        AttributePlacement.end(),
    )
    with pytest.raises(XmlEditFailure) as raised:
        doc.commit(builder.build())
    assert raised.value.kind is XmlEditFailureKind.DUPLICATE_EXPANDED_ATTRIBUTE
    # An unprefixed duplicate promises no namespace; the commit reparse
    # fails atomically instead of fabricating a Complete target.
    doc = _doc(b'<root a="1"/>')
    builder = EditTransactionBuilder(doc)
    builder.insert_attribute(
        _element_ref(doc, "root"),
        NameFacts.new(None, "a", None),
        "2",
        AttributePlacement.end(),
    )
    with pytest.raises(XmlEditFailure) as raised:
        doc.commit(builder.build())
    assert raised.value.kind is XmlEditFailureKind.NEW_DOCUMENT_FORMATION_FAILED


def test_new_literal_content_is_escaped_never_interpolated():
    """Semantic replacement accepts text, never raw untrusted markup; new
    literal content is XML-escaped under the existing encoding (RFC 0012
    §11, lines 393-396)."""
    doc = _doc(b"<root>a</root>")
    builder = EditTransactionBuilder(doc)
    builder.replace_text(_text_ref(doc, 0), "x < y & z")
    commit = doc.commit(builder.build())
    assert commit.document.render() == b"<root>x &lt; y &amp; z</root>"
