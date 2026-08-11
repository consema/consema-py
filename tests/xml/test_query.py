"""Query intent tests: native and lossless syntax domains (RFC 0012 §8).

Authority: conformance/vectors/xml-1-0-safe-v1.json (case ids cited per
test); crates/consema-xml/src/query.rs (byte/registry arbitration);
RFC 0012 §8 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:285-311).

These tests are intent documents written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7); no
gate is claimed to have passed.
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.structural import NodeRole
from consema.protocol.query import (
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryDomain,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
    QuerySelection,
)

from consema.xml import (
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    execute_xml_query,
    execute_xml_syntax_query,
    parse,
)

from conftest import find_case, form_document


def _capabilities():
    from consema.protocol.query import CapabilityId, CapabilitySet

    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _native(expression, selection=QuerySelection.ALL):
    definition = (
        QueryDefinition(QueryDomain("xml.native-semantic-query", 1))
        .with_expression(expression)
        .with_selection(selection)
    )
    return definition.validate().bind(_capabilities())


def _syntax(expression, selection=QuerySelection.ALL):
    definition = (
        QueryDefinition(QueryDomain("xml.lossless-syntax-query", 1))
        .with_expression(expression)
        .with_selection(selection)
    )
    return definition.validate().bind(_capabilities())


def _input_then(*operators):
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    return expression


def _op(name, **arguments):
    call = OperatorCall(name, 1)
    for key, value in arguments.items():
        call = call.with_argument(key, PortableValue.string(value))
    return call


def _doc(source: bytes):
    return parse(
        source, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
    )


# ---------------------------------------------------------------------------
# Golden vector transcriptions
# ---------------------------------------------------------------------------


def test_vector_syntax_kind_and_text_filter(xml_vectors):
    """Golden transcription of ``xml.syntax-query.kind-and-text-filter``
    (xml-1-0-safe-v1.json:174-202): the lossless domain filters by the
    frozen kind ``local-name`` in exact source order. (The vector ordinals
    are informational — both shared runners compare kind and text only,
    go/conformance/xml_1_0_safe_v1.go:189-191.)"""
    case = find_case(xml_vectors, "xml.syntax-query.kind-and-text-filter")
    doc = form_document(case)
    assert doc.status.value == "Complete"
    expression = _input_then(
        _op("xml.syntax-kind-is", kind="local-name")
    )
    matches = execute_xml_syntax_query(_syntax(expression), doc)
    assert [m.kind.as_str for m in matches] == ["local-name", "local-name"]
    assert [m.span.start_byte for m in matches] == [1, 15]
    assert doc.render()[matches[0].span.start_byte : matches[0].span.end_byte] == b"root"


def test_vector_syntax_entity_reference_kind(xml_vectors):
    """Golden transcription of ``xml.syntax-query.entity-reference-kind``
    (xml-1-0-safe-v1.json:203-226): ``&lt;`` is one entity-reference piece."""
    case = find_case(xml_vectors, "xml.syntax-query.entity-reference-kind")
    doc = form_document(case)
    expression = _input_then(
        _op("xml.syntax-kind-is", kind="entity-reference")
    )
    matches = execute_xml_syntax_query(_syntax(expression), doc)
    assert len(matches) == 1
    assert matches[0].kind.as_str == "entity-reference"
    assert doc.render()[matches[0].span.start_byte : matches[0].span.end_byte] == b"&lt;"


def test_vector_syntax_attribute_value_kind(xml_vectors):
    """Golden transcription of ``xml.syntax-query.attribute-value-kind``
    (xml-1-0-safe-v1.json:227-250): the value between the quotes is one
    attribute-value piece."""
    case = find_case(xml_vectors, "xml.syntax-query.attribute-value-kind")
    doc = form_document(case)
    expression = _input_then(
        _op("xml.syntax-kind-is", kind="attribute-value")
    )
    matches = execute_xml_syntax_query(_syntax(expression), doc)
    assert len(matches) == 1
    assert doc.render()[matches[0].span.start_byte : matches[0].span.end_byte] == b"1"


def test_vector_native_attributes_and_values(xml_vectors):
    """Golden transcription of ``xml.native-query.attributes-and-values``
    (xml-1-0-safe-v1.json:251-276): document-root then element-attributes
    yields the attribute association with its normalized value."""
    case = find_case(xml_vectors, "xml.native-query.attributes-and-values")
    doc = form_document(case)
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-attributes"),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert len(matches) == 1
    assert matches[0].kind.value == "attribute"
    assert matches[0].local == "a"
    assert matches[0].value == "1"


def test_vector_native_descendants_order(xml_vectors):
    """Golden transcription of ``xml.native-query.descendants-order``
    (xml-1-0-safe-v1.json:277-309): descendant traversal is bounded
    pre-order and never includes the input element itself."""
    case = find_case(xml_vectors, "xml.native-query.descendants-order")
    doc = form_document(case)
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-descendants"),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert [m.local for m in matches] == ["a", "b", "c"]
    assert all(m.kind.value == "element" for m in matches)


# ---------------------------------------------------------------------------
# Native domain semantics
# ---------------------------------------------------------------------------


def test_mixed_content_children_preserve_order():
    """element-children preserves mixed-content order
    (query.rs:696-723; RFC 0012 §8, lines 308-311)."""
    doc = _doc(b"<root>a<child/>b</root>")
    expression = _input_then(_op("xml.document-root"), _op("xml.element-children"))
    matches = execute_xml_query(_native(expression), doc)
    assert [m.kind.value for m in matches] == ["text", "element", "text"]
    assert matches[0].semantic == "a"
    assert matches[2].semantic == "b"


def test_text_references_kinds_and_names():
    """text-references exposes each reference with its kind, name, and
    resolved character data (query.rs:1006-1053)."""
    doc = _doc(b'<!DOCTYPE root [<!ENTITY e "expanded">]><root>&lt; &e; &#65;</root>')
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-child-text"),
        _op("xml.text-references"),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert [m.reference_kind.value for m in matches] == ["Predefined", "General", "Character"]
    assert matches[1].name == "e"
    assert matches[1].resolved == "expanded"
    assert matches[2].name == "&#x41;"
    assert matches[2].resolved == "A"


def test_name_equals_original_and_expanded():
    """name-equals compares original spelling or expanded names, never the
    prefix (query.rs:1055-1114)."""
    doc = _doc(b'<p:root xmlns:p="urn:x"><p:child/></p:root>')
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-child-elements"),
        _op(
            "xml.name-equals",
            prefix="p",
            local="child",
            namespace="",
            comparison="OriginalExact",
        ),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert len(matches) == 1
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-child-elements"),
        _op(
            "xml.name-equals",
            prefix="",
            local="child",
            namespace="urn:x",
            comparison="Expanded",
        ),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert len(matches) == 1
    assert matches[0].namespace == "urn:x"
    assert not matches[0].namespace_error


def test_in_scope_namespaces_include_ancestors():
    """element-in-scope-namespaces is the full ancestry-derived chain
    oldest first (query.rs:925-959)."""
    doc = _doc(b'<a xmlns="urn:a"><b xmlns:p="urn:p"><c/></b></a>')
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-child-elements"),
        _op("xml.element-child-elements"),
        _op("xml.element-in-scope-namespaces"),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert [m.name for m in matches] == ["urn:a", "urn:p"]


def test_node_kind_is_filters_mixed_output():
    """node-kind-is filters any match kind (query.rs:1197-1228)."""
    doc = _doc(b"<root><!--c--><?pi x?></root>")
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-children"),
        _op("xml.node-kind-is", kind="comment"),
    )
    matches = execute_xml_query(_native(expression), doc)
    assert len(matches) == 1
    assert matches[0].kind.value == "comment"
    assert matches[0].text == "c"


def test_core_take_and_distinct_by_identity():
    """core.take@1 and core.distinct-by-identity@1 are available in both
    domains (RFC 0012 §8, lines 302-306)."""
    doc = _doc(b"<root><a/><b/><c/></root>")
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-descendants"),
    )
    take = expression.then(OperatorCall("core.take", 1).with_argument(
        "count", PortableValue.integer(2)
    ))
    matches = execute_xml_query(_native(take), doc)
    assert [m.local for m in matches] == ["a", "b"]
    distinct = expression.then(OperatorCall("core.distinct-by-identity", 1))
    matches = execute_xml_query(_native(distinct), doc)
    assert len(matches) == 3


def test_domain_mismatch_is_rejected():
    """A foreign domain never executes against the XML executor
    (query.rs:223-235)."""
    doc = _doc(b"<root/>")
    expression = _input_then(_op("ini.all-entries"))
    definition = (
        QueryDefinition(QueryDomain("ini.native-semantic-query", 1))
        .with_expression(expression)
        .with_selection(QuerySelection.ALL)
    )
    executable = definition.validate().bind(_capabilities())
    with pytest.raises(QueryFailure) as raised:
        execute_xml_query(executable, doc)
    assert raised.value.kind is QueryFailureKind.DOMAIN_MISMATCH


def test_require_one_cardinality_is_enforced():
    """RequireOne cardinality is enforced on the final result
    (query.rs:252-269)."""
    doc = _doc(b"<root><a/><b/></root>")
    expression = _input_then(
        _op("xml.document-root"),
        _op("xml.element-child-elements"),
    )
    executable = _native(expression, selection=QuerySelection.REQUIRE_ONE)
    with pytest.raises(QueryFailure) as raised:
        execute_xml_query(executable, doc)
    assert raised.value.kind is QueryFailureKind.CARDINALITY_VIOLATION


# ---------------------------------------------------------------------------
# Lossless domain semantics
# ---------------------------------------------------------------------------


def test_syntax_text_equals_compares_raw_bytes():
    """syntax-text-equals compares the exact raw span bytes
    (query.rs:1345-1354)."""
    doc = _doc(b"<root>a<b/>c</root>")
    expression = _input_then(_op("xml.syntax-text-equals", text="a"))
    matches = execute_xml_syntax_query(_syntax(expression), doc)
    assert len(matches) == 1
    assert matches[0].kind.as_str == "text"


def test_syntax_kinds_cover_every_piece_exactly_once():
    """Every non-empty raw byte belongs to exactly one ordered piece
    (RFC 0012 §7, lines 258-261)."""
    doc = _doc(b'<?xml version="1.0"?><root a="1">t</root>')
    pieces = doc.lossless_structural_index().pieces
    kinds = doc.lossless_syntax_kinds()
    next_byte = 0
    for piece, kind in zip(pieces, kinds):
        assert piece.span.start_byte == next_byte
        next_byte = piece.span.end_byte
        assert kind is not None
    assert next_byte == len(doc.render())
