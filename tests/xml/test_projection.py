"""Projection intent tests: the exact ``xml.element-tree@1`` record and the
explicit policy targets (RFC 0012 §9).

Authority: conformance/vectors/xml-1-0-safe-v1.json (case ids cited per
test); crates/consema-xml/src/projection.rs (byte/registry arbitration);
RFC 0012 §9 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:313-348) and
RFC 0004 §8 (provenance).

These tests are intent documents written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7); no
gate is claimed to have passed.
"""

from __future__ import annotations

import pytest

from consema.core.value import Kind, PortableValue

from consema.xml import (
    AttributePolicy,
    CollisionPolicy,
    ExpandedNameKeyPolicy,
    ProjectionLimits,
    ProjectionRequest,
    ProjectionTarget,
    RepeatedChildPolicy,
    TextContentInclude,
    TextKeyPolicy,
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    parse,
    project_document,
)

from conftest import find_case, form_document


def _doc(source: bytes):
    return parse(
        source, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
    )


def _object(value: PortableValue) -> dict:
    return dict(value.as_object())


# ---------------------------------------------------------------------------
# Golden vector transcriptions
# ---------------------------------------------------------------------------


def test_vector_element_tree_record(xml_vectors):
    """Golden transcription of ``xml.projection.element-tree-record``
    (xml-1-0-safe-v1.json:310-325): the exact default target is the
    versioned ``xml.element-tree@1`` record; the root keeps its attribute
    and its element child."""
    case = find_case(xml_vectors, "xml.projection.element-tree-record")
    doc = form_document(case)
    result = project_document(doc, ProjectionRequest.element_tree())
    assert result.value.kind is Kind.OBJECT
    record = _object(result.value)
    assert record["record"].as_string() == "xml.element-tree@1"
    root = _object(record["root"])
    assert _object(root["expanded-name"])["local"].as_string() == "root"
    attribute = _object(root["attributes"].as_sequence()[0])
    assert _object(attribute["expanded-name"])["local"].as_string() == "a"
    assert attribute["value"].as_string() == "1"
    content = root["content"].as_sequence()
    assert len(content) == 1
    assert "expanded-name" in _object(content[0])


def test_vector_namespace_record(xml_vectors):
    """Golden transcription of ``xml.projection.namespace-record``
    (xml-1-0-safe-v1.json:326-339): the projected root carries its resolved
    namespace URI."""
    case = find_case(xml_vectors, "xml.projection.namespace-record")
    doc = form_document(case)
    result = project_document(doc, ProjectionRequest.element_tree())
    root = _object(_object(result.value)["root"])
    expanded = _object(root["expanded-name"])
    assert expanded["namespace"].as_string() == "urn:p"
    assert expanded["local"].as_string() == "root"


def test_vector_recovered_never_projects(xml_vectors):
    """Golden transcription of ``xml.projection.recovered-never-projects``
    (xml-1-0-safe-v1.json:340-350): Recovered documents never publish
    partial semantic values (RFC 0012 §9, lines 329-331)."""
    case = find_case(xml_vectors, "xml.projection.recovered-never-projects")
    doc = form_document(case)
    result = project_document(doc, ProjectionRequest.element_tree())
    assert result.diagnostics[0].code == "xml.projection.recovered-document@1"


# ---------------------------------------------------------------------------
# Element-tree record semantics
# ---------------------------------------------------------------------------


def test_element_tree_record_is_not_a_portable_tree():
    """The ``xml.element-tree@1`` record is the XML domain record: an
    element tree with ordered mixed content, never a PortableValue tree
    (RFC 0012 §9, lines 321-327; task requirement)."""
    doc = _doc(b"<root>a<child/>b<![CDATA[c]]><!--d--><?pi e?></root>")
    result = project_document(doc, ProjectionRequest.element_tree())
    root = _object(_object(result.value)["root"])
    content = root["content"].as_sequence()
    kinds = []
    for item in content:
        fields = _object(item)
        if "expanded-name" in fields:
            kinds.append("element")
        else:
            kinds.append(fields["kind"].as_string())
    assert kinds == ["text", "element", "text", "cdata", "comment", "processing-instruction"]
    assert result.fidelity.value == "Exact"


def test_text_fragments_are_preserved():
    """Text content keeps its exact fragments: literals, character
    references, predefined and general entity references (RFC 0012 §6,
    lines 227-252)."""
    doc = _doc(b'<!DOCTYPE root [<!ENTITY e "expanded">]><root>a&lt;b&#65;&e;</root>')
    result = project_document(doc, ProjectionRequest.element_tree())
    root = _object(_object(result.value)["root"])
    text = _object(root["content"].as_sequence()[0])
    fragments = text["fragments"].as_sequence()
    assert [_object(f)["kind"].as_string() for f in fragments] == [
        "literal",
        "predefined-entity",
        "literal",
        "character-reference",
        "general-entity",
    ]
    assert _object(fragments[0])["text"].as_string() == "a"
    assert _object(fragments[1])["name"].as_string() == "lt"
    assert _object(fragments[3])["resolved"].as_string() == "A"
    assert _object(fragments[4])["resolved"].as_string() == "expanded"


def test_projection_provenance_is_direct_and_complete():
    """Every emitted value carries a source origin with a direct relation
    and exact raw span (RFC 0004 §8, lines 193-217; projection.rs:553-572)."""
    doc = _doc(b'<root a="1"><child>t</child></root>')
    result = project_document(doc, ProjectionRequest.element_tree())
    assert len(result.provenance.entries) > 0
    for entry in result.provenance.entries:
        origin = entry.origins[0]
        assert origin.snapshot == doc.snapshot_identity()
        assert origin.span.snapshot == doc.snapshot_identity()
        assert origin.span.start_byte < origin.span.end_byte


# ---------------------------------------------------------------------------
# Explicit policy targets (RFC 0012 §9)
# ---------------------------------------------------------------------------


def test_text_content_is_always_transformed():
    """text-content projection is always Transformed and reports every
    discarded element, attribute, comment, and PI (projection.rs:975-1095)."""
    doc = _doc(b"<root>a<child b=\"1\">c</child><!--d--><?pi e?>f</root>")
    result = project_document(doc, ProjectionRequest.text_content(
        doc.root().node_ref(), TextContentInclude.TEXT_AND_CDATA
    ))
    assert result.value.as_string() == "acf"
    assert result.fidelity.value == "Transformed"
    kinds = {event.kind.value for event in result.report.events}
    assert "element-discarded" in kinds
    assert "attribute-discarded" in kinds
    assert "comment-discarded" in kinds
    assert "processing-instruction-discarded" in kinds


def test_simple_entry_mapping_rejects_ambiguity():
    """Simple-entry-mapping is admitted only without ambiguity; the default
    for any omitted policy is failure, not LastWins (RFC 0012 §9, lines
    334-345). A repeated expanded child under Reject is a collision
    (projection.rs:1161-1198 -> xml.projection.collision@1)."""
    doc = _doc(b"<root><a>1</a><a>2</a></root>")
    result = project_document(doc, ProjectionRequest.simple_entry_mapping(
        doc.root().node_ref(),
        AttributePolicy.REJECT_ATTRIBUTES,
        TextKeyPolicy.REJECT_TEXT,
        RepeatedChildPolicy.REJECT,
        ExpandedNameKeyPolicy.LOCAL_ONLY,
        CollisionPolicy.REJECT,
    ))
    assert result.diagnostics[0].code == "xml.projection.collision@1"


def test_simple_entry_mapping_prefix_attribute_keys():
    """The attribute ``@`` prefix is an explicit policy, never an automatic
    convention (projection.rs:1269-1288)."""
    doc = _doc(b'<root a="1"><child>x</child></root>')
    result = project_document(doc, ProjectionRequest.simple_entry_mapping(
        doc.root().node_ref(),
        AttributePolicy.PREFIX_ATTRIBUTE_KEYS,
        TextKeyPolicy.REJECT_TEXT,
        RepeatedChildPolicy.REJECT,
        ExpandedNameKeyPolicy.LOCAL_ONLY,
        CollisionPolicy.REJECT,
    ))
    assert result.value.kind is Kind.ENTRY_MAPPING
    pairs = dict((key.as_string(), value) for key, value in result.value.as_entry_mapping())
    assert pairs["@a"].as_string() == "1"
    assert pairs["child"].as_string() == "x"
