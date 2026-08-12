"""Formation intent tests: golden vector transcriptions, entity deny-by-
default, namespaces, and byte-exact spans (RFC 0012 §2-§7).

Authority: conformance/vectors/xml-1-0-safe-v1.json (case ids cited per
test); crates/consema-xml/src/parser.rs (byte/registry arbitration);
RFC 0012 §2-§7 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:46-283).

These tests are intent documents written before the Python toolchain
verification gate (docs/multi-language-implementation-plan.md §3/§7); no
gate is claimed to have passed.
"""

from __future__ import annotations

import pytest

from consema.document.structural import FormationStatus

from consema.xml import (
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    parse,
)

from conftest import find_case, form_document


# ---------------------------------------------------------------------------
# Golden vector transcriptions
# ---------------------------------------------------------------------------


def test_vector_basic_complete(xml_vectors):
    """Golden transcription of ``xml.formation.basic-complete``
    (xml-1-0-safe-v1.json:5-16): a simple complete document renders
    byte-exact."""
    case = find_case(xml_vectors, "xml.formation.basic-complete")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == b'<root a="1"><child>t</child></root>'


def test_vector_default_namespace_on_elements(xml_vectors):
    """Golden transcription of ``xml.formation.default-namespace-on-elements``
    (xml-1-0-safe-v1.json:17-28): the default namespace applies to element
    names only; unprefixed attributes stay unnamespaced."""
    case = find_case(xml_vectors, "xml.formation.default-namespace-on-elements")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == b'<root xmlns="urn:app" version="1"><child/></root>'
    root = doc.root()
    assert root.expanded() is not None
    assert root.expanded().namespace == "urn:app"
    assert root.expanded().local == "root"
    assert root.attributes()[0].expanded.namespace is None


def test_vector_prefixed_namespace_resolution(xml_vectors):
    """Golden transcription of ``xml.formation.prefixed-namespace-resolution``
    (xml-1-0-safe-v1.json:29-40): scoped prefix bindings resolve exactly."""
    case = find_case(xml_vectors, "xml.formation.prefixed-namespace-resolution")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == (
        b'<p:root xmlns:p="urn:one"><p:child xmlns:q="urn:two" q:attr="x"/></p:root>'
    )
    root = doc.root()
    assert root.expanded().namespace == "urn:one"
    child = root.children()[0].data
    assert child.expanded.namespace == "urn:one"
    attribute = child.attributes[0]
    assert attribute.expanded.namespace == "urn:two"
    assert attribute.expanded.local == "attr"


def test_vector_internal_entity_expansion(xml_vectors):
    """Golden transcription of ``xml.formation.internal-entity-expansion``
    (xml-1-0-safe-v1.json:53-64): admitted internal general text entities
    expand and the declaration spelling round-trips."""
    case = find_case(xml_vectors, "xml.formation.internal-entity-expansion")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == b'<!DOCTYPE root [<!ENTITY greeting "hello">]><root>&greeting;</root>'
    text = doc.root().children()[0].data
    assert text.fragments[0].kind.value == "general-entity"
    assert text.fragments[0].resolved == "hello"
    assert text.fragments[0].declaration_span.start_byte == 16


def test_vector_utf16le_with_bom(xml_vectors):
    """Golden transcription of ``xml.formation.utf16le-with-bom``
    (xml-1-0-safe-v1.json:89-101): UTF-16LE with BOM forms and renders the
    exact original bytes (RFC 0012 §2: UTF-16 requires its BOM)."""
    case = find_case(xml_vectors, "xml.formation.utf16le-with-bom")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render().hex() == "fffe3c0072006f006f0074003e002d4e87653c002f0072006f006f0074003e00"


def test_vector_duplicate_expanded_attribute_recovered(xml_vectors):
    """Golden transcription of
    ``xml.formation.duplicate-expanded-attribute-recovered``
    (xml-1-0-safe-v1.json:102-113): two prefixes bound to one URI produce
    one expanded-name duplicate and a Recovered document with the frozen
    diagnostic."""
    case = find_case(xml_vectors, "xml.formation.duplicate-expanded-attribute-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.namespace.duplicate-attribute@1" for d in doc.diagnostics())


def test_vector_unbound_prefix_recovered(xml_vectors):
    """Golden transcription of ``xml.formation.unbound-prefix-recovered``
    (xml-1-0-safe-v1.json:114-125): an unbound prefix is a deterministic
    recovery, never a fabricated expanded name."""
    case = find_case(xml_vectors, "xml.formation.unbound-prefix-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.namespace.unbound-prefix@1" for d in doc.diagnostics())
    root = doc.root()
    assert root.expanded() is None
    assert root._data().namespace_error is not None


def test_vector_external_subset_recovered(xml_vectors):
    """Golden transcription of ``xml.formation.external-subset-recovered``
    (xml-1-0-safe-v1.json:126-137): a DOCTYPE SYSTEM external subset is
    denied with the stable security diagnostic; no I/O ever happens."""
    case = find_case(xml_vectors, "xml.formation.external-subset-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.dtd.external-subset@1" for d in doc.diagnostics())


def test_vector_unknown_entity_recovered(xml_vectors):
    """Golden transcription of ``xml.formation.unknown-entity-recovered``
    (xml-1-0-safe-v1.json:138-149): unknown references produce no partial
    native text (RFC 0012 §3, lines 127-130)."""
    case = find_case(xml_vectors, "xml.formation.unknown-entity-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.entity.unknown@1" for d in doc.diagnostics())


def test_vector_missing_root_recovered(xml_vectors):
    """Golden transcription of ``xml.formation.missing-root-recovered``
    (xml-1-0-safe-v1.json:150-161): a document without an element is
    Recovered; the parser never invents a second root."""
    case = find_case(xml_vectors, "xml.formation.missing-root-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.tree.missing-root@1" for d in doc.diagnostics())
    assert doc.root() is None


def test_vector_dtd_comment_not_excluded_markup(xml_vectors):
    """Golden transcription of ``xml.formation.dtd-comment-not-excluded-markup``
    (xml-1-0-safe-v1.json:162-173): a comment inside the internal subset is
    character data; ``<!ELEMENT>`` inside it is not a declaration."""
    case = find_case(xml_vectors, "xml.formation.dtd-comment-not-excluded-markup")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == b"<!DOCTYPE root [<!-- <!ELEMENT not-a-decl> -->]><root/>"


# ---------------------------------------------------------------------------
# Entity deny-by-default (RFC 0012 §3)
# ---------------------------------------------------------------------------


def test_entity_deny_by_default():
    """Deny-by-default: unknown, parameter, external, and markup-creating
    entities are rejected with their frozen diagnostics and never produce
    native text (RFC 0012 §3, lines 101-131)."""
    cases = [
        (b"<root>&unknown;</root>", "xml.entity.unknown@1"),
        (b"<!DOCTYPE root [<!ENTITY % p \"x\">]><root/>", "xml.dtd.parameter-entity@1"),
        (b'<!DOCTYPE root [<!ENTITY e SYSTEM "http://evil/x">]><root/>', "xml.dtd.external-entity@1"),
        (b'<!DOCTYPE root [<!ENTITY e "<b>markup</b>">]><root/>', "xml.entity.markup@1"),
        (b'<!DOCTYPE root [<!ENTITY lt "x">]><root/>', "xml.entity.reserved-name@1"),
        (b'<!DOCTYPE root [<!ENTITY e "a"><!ENTITY e "b">]><root/>', "xml.entity.duplicate@1"),
    ]
    for source, expected_code in cases:
        doc = parse(
            source, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
        )
        assert doc.status is FormationStatus.RECOVERED, source
        assert any(d.code == expected_code for d in doc.diagnostics()), (source, expected_code)


def test_predefined_entities_always_available():
    """The five predefined entities resolve without any declaration
    (RFC 0012 §3, lines 115-119; crates/consema-xml/src/entity.rs:19-40)."""
    doc = parse(
        b"<root>&lt; &gt; &amp; &apos; &quot;</root>",
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        XmlParseLimits(),
    )
    assert doc.status is FormationStatus.COMPLETE
    from consema.xml import text_semantic

    text = doc.root().children()[0].data
    assert text_semantic(text) == "< > & ' \""


def test_entity_amplification_ratio_bounds_expansion(xml_vectors):
    """Golden transcription of ``xml.limit.entity-amplification-recovered``
    (xml-1-0-safe-v1.json:568-579): a 20-byte declaration at ratio 2 allows
    at most 40 expanded bytes; the third reference breaches."""
    case = find_case(xml_vectors, "xml.limit.entity-amplification-recovered")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.entity.amplification@1" for d in doc.diagnostics())


def test_mixed_content_limit(xml_vectors):
    """Golden transcription of ``xml.limit.mixed-content-diagnostic``
    (xml-1-0-safe-v1.json:580-592): the child-element budget is the same
    hard mixed-content budget; the overflow is dropped with a diagnostic."""
    case = find_case(xml_vectors, "xml.limit.mixed-content-diagnostic")
    doc = form_document(case)
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.limit.mixed-content@1" for d in doc.diagnostics())
    root = doc.root()
    assert len(root._data().children) == 1


# ---------------------------------------------------------------------------
# Namespaces (RFC 0012 §5)
# ---------------------------------------------------------------------------


def test_namespace_scope_is_ancestry_derived():
    """Namespace scope is immutable ancestry-derived data: rebinding in a
    child does not mutate the parent scope
    (crates/consema-xml/src/namespace.rs:91-218)."""
    doc = parse(
        b'<a xmlns:p="urn:one"><b xmlns:p="urn:two"><c/></b></a>',
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        XmlParseLimits(),
    )
    assert doc.status is FormationStatus.COMPLETE
    root = doc.root()
    a = root._data()
    b = root.children()[0].data
    assert a.expanded.namespace is None
    assert b.expanded.namespace is None


def test_xml_prefix_permanently_bound():
    """The ``xml`` prefix is permanently bound to its standard URI and
    cannot be rebound (RFC 0012 §5, lines 213-214)."""
    doc = parse(
        b'<root xml:lang="en"/>',
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        XmlParseLimits(),
    )
    assert doc.status is FormationStatus.COMPLETE
    attribute = doc.root().attributes()[0]
    assert attribute.expanded.namespace == "http://www.w3.org/XML/1998/namespace"
    doc = parse(
        b'<root xmlns:xml="urn:wrong"/>',
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        XmlParseLimits(),
    )
    assert doc.status is FormationStatus.RECOVERED
    assert any(d.code == "xml.namespace.xml-rebinding@1" for d in doc.diagnostics())


def test_xmlns_reserved_everywhere():
    """``xmlns`` is reserved for namespace declarations and cannot be used
    as an ordinary name or rebound (RFC 0012 §5, line 215)."""
    doc = parse(
        b"<root xmlns:x=\"urn:u\" xmlns:x2=\"urn:v\"/>",
        XmlProfile.SAFE_V1,
        XmlEncodingSelection.profile_default(),
        XmlParseLimits(),
    )
    assert doc.status is FormationStatus.COMPLETE
    assert len(doc.root().namespace_bindings()) == 2


# ---------------------------------------------------------------------------
# Byte-exact spans (RFC 0012 §2/§7)
# ---------------------------------------------------------------------------


def test_spans_are_raw_byte_ranges():
    """Every public span is a half-open raw-byte range; unmodified
    rendering returns the exact original bytes including line endings
    (RFC 0012 §2, lines 48-53)."""
    source = b"<root>\r\n  <child>t</child>\r\n</root>"
    doc = parse(
        source, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
    )
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == source
    root = doc.root()
    # The element span covers its full start tag, not the end tag
    # (document.rs:274-296).
    assert root.span().start_byte == 0
    pieces = doc.lossless_structural_index().pieces
    next_byte = 0
    for piece in pieces:
        assert piece.span.start_byte == next_byte
        next_byte = piece.span.end_byte
    assert next_byte == len(source)


def test_utf16_spans_cover_original_code_units():
    """UTF-16 pieces cover original code units, not a temporary UTF-8
    buffer (RFC 0012 §7, lines 279-282)."""
    source = "<root>中文</root>"
    raw = b"\xff\xfe" + source.encode("utf-16-le")
    doc = parse(
        raw, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), XmlParseLimits()
    )
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == raw
    pieces = doc.lossless_structural_index().pieces
    next_byte = 0
    for piece in pieces:
        assert piece.span.start_byte == next_byte
        next_byte = piece.span.end_byte
    assert next_byte == len(raw)


def test_mixed_content_order_is_preserved(xml_vectors):
    """Golden transcription of ``xml.formation.mixed-content-order``
    (xml-1-0-safe-v1.json:65-76): text, element, text, CDATA, comment, PI
    keep their exact source order and spelling."""
    case = find_case(xml_vectors, "xml.formation.mixed-content-order")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    kinds = [child.kind.value for child in doc.root().children()]
    assert kinds == [
        "text",
        "element",
        "text",
        "cdata",
        "comment",
        "processing-instruction",
        "text",
    ]
    assert doc.render() == b"<root>a<child/>b<![CDATA[c]]><!--d--><?pi e?>f</root>"


def test_crlf_normalization_is_semantic_not_destructive(xml_vectors):
    """Golden transcription of ``xml.formation.crlf-semantic-normalization``
    (xml-1-0-safe-v1.json:77-88): raw CRLF stays in the source while native
    text is line-end normalized to LF (RFC 0012 §2, lines 83-85)."""
    case = find_case(xml_vectors, "xml.formation.crlf-semantic-normalization")
    doc = form_document(case)
    assert doc.status is FormationStatus.COMPLETE
    assert doc.render() == b"<root>line1\r\nline2</root>"
    from consema.xml import text_semantic

    text = doc.root().children()[0].data
    assert text_semantic(text) == "line1\nline2"
