"""Conversion golden transcriptions for the plist family (RFC 0013 §7).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json;
each test cites the vector case id. Conversion is a first-class transform:
every conversion emits a representation-change report event, reparses the
exact emitted bytes, and verifies native-model equality (the reparse
closure).

Cases covered here:

- plist-v1.json: plist.conversion.xml-to-binary-round-trip (lines 1565-
  1590), plist.conversion.binary-to-xml-round-trip (1591-1610),
  plist.conversion.uid-inexpressible-to-xml (1611-1622),
  plist.conversion.duplicate-keys-preserved (1623-1641).
"""

from __future__ import annotations

import pytest

from consema.plist import (
    PlistConversionFailure,
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
    parse,
)

DEFAULT_LIMITS = PlistParseLimits()


def xml_document(source: str):
    return parse(
        source.encode("utf-8"),
        PlistProfile.XML_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def binary_document(hex_string: str):
    return parse(
        bytes.fromhex(hex_string),
        PlistProfile.BINARY_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


# ---------------------------------------------------------------------------
# plist.conversion.xml-to-binary-round-trip (plist-v1.json:1565-1590)
# ---------------------------------------------------------------------------


def test_xml_to_binary_round_trip():
    # Case plist.conversion.xml-to-binary-round-trip (plist-v1.json:1566-
    # 1589).
    source = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "    <dict>\n"
        "        <key>name</key><string>Consema</string>\n"
        "        <key>count</key><integer>0x2A</integer>\n"
        "        <key>ratio</key><real>1.5e3</real>\n"
        "        <key>negative</key><integer>-7</integer>\n"
        "        <key>enabled</key><true/>\n"
        "        <key>disabled</key><false/>\n"
        "        <key>payload</key><data>AQID</data>\n"
        "        <key>born</key><date>2023-01-01T00:00:00Z</date>\n"
        "        <key>tags</key><array><string>a</string><dict/></array>\n"
        "        <key>empty</key><string></string>\n"
        "    </dict>\n"
        "</plist>\n"
    )
    document = xml_document(source)
    converted = document.convert_to(PlistProfile.BINARY_V1, DEFAULT_LIMITS)
    assert converted.report.representation_changed()
    target = converted.document
    assert target.representation().value == "Binary"
    assert target.formation_status().value == "Complete"
    # Reparse closure and native-model equality.
    reparsed = binary_document(target.render().hex())
    assert reparsed.document() == document.document()
    # Round trip back to XML preserves the native model.
    round_trip = target.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert round_trip.document.document() == document.document()


# ---------------------------------------------------------------------------
# plist.conversion.binary-to-xml-round-trip (plist-v1.json:1591-1610)
# ---------------------------------------------------------------------------


def test_binary_to_xml_round_trip():
    # Case plist.conversion.binary-to-xml-round-trip (plist-v1.json:1592-
    # 1609).
    document = binary_document(
        "62706c6973743030517810020908a20203233ff80000000000005161516251635164d40607080900010405080a0c0d0e111a1c1e20220000000000000101000000000000000b000000000000000a000000000000002b"
    )
    converted = document.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert converted.report.representation_changed()
    target = converted.document
    assert target.representation().value == "Xml"
    assert target.formation_status().value == "Complete"
    assert target.document() == document.document()
    round_trip = target.convert_to(PlistProfile.BINARY_V1, DEFAULT_LIMITS)
    assert round_trip.document.document() == document.document()


# ---------------------------------------------------------------------------
# plist.conversion.uid-inexpressible-to-xml (plist-v1.json:1611-1622)
# ---------------------------------------------------------------------------


def test_uid_inexpressible_to_xml():
    # Case plist.conversion.uid-inexpressible-to-xml (plist-v1.json:1612-
    # 1621): UID values are binary-only and block conversion to XML
    # atomically (RFC 0013 §7, hard gate 3).
    document = binary_document(
        "62706c6973743030800508000000000000010100000000000000010000000000000000000000000000000a"
    )
    with pytest.raises(PlistConversionFailure) as excinfo:
        document.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert excinfo.value.code == "plist.conversion.inexpressible@1"


def test_binary_to_xml_conversion_render_golden():
    # The conversion render of the normalization-and-conversion case
    # (plist-v1.json:1298-1308): the root value element is written at
    # depth 0 (document.rs:767-841).
    document = binary_document(
        "62706c6973743030d1010251611001080b0d000000000000010100000000000000030000000000000000000000000000000f"
    )
    converted = document.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    expected = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>a</key>\n"
        "    <integer>1</integer>\n"
        "</dict>\n"
        "</plist>\n"
    )
    assert converted.document.render() == expected.encode("utf-8")


def test_same_representation_is_not_a_conversion():
    # RFC 0013 §7: a target equal to the source representation is not a
    # conversion (document.rs:262-267).
    document = xml_document('<plist version="1.0"><string>ok</string></plist>')
    with pytest.raises(PlistConversionFailure) as excinfo:
        document.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert excinfo.value.code == "plist.conversion.same-representation@1"


# ---------------------------------------------------------------------------
# plist.conversion.duplicate-keys-preserved (plist-v1.json:1623-1641)
# ---------------------------------------------------------------------------


def test_duplicate_keys_preserved_through_conversion():
    # Case plist.conversion.duplicate-keys-preserved (plist-v1.json:1624-
    # 1640).
    source = (
        '<plist version="1.0"><dict>'
        "<key>a</key><integer>1</integer>"
        "<key>a</key><integer>2</integer>"
        "<key>b</key><string>three</string>"
        "</dict></plist>"
    )
    document = xml_document(source)
    converted = document.convert_to(PlistProfile.BINARY_V1, DEFAULT_LIMITS)
    target = converted.document
    native = target.document()
    dict_value = native.root_value().as_dict()
    assert [entry.key.to_unicode() for entry in dict_value.entries] == ["a", "a", "b"]
    round_trip = target.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert round_trip.document.document() == document.document()


def test_xml_binary_native_model_equivalence():
    """The two representations share one native value model (RFC 0013 §7):
    parsing the same value space under either profile produces equal native
    documents, and the conversion closure keeps native-model equality."""
    xml_source = (
        '<plist version="1.0"><dict>'
        "<key>name</key><string>Consema</string>"
        "<key>count</key><integer>42</integer>"
        "<key>ratio</key><real>1.5</real>"
        "<key>enabled</key><true/>"
        "<key>payload</key><data>AQID</data>"
        "<key>born</key><date>2023-01-01T00:00:00Z</date>"
        "<key>tags</key><array><string>a</string><string>b</string></array>"
        "</dict></plist>"
    )
    xml = xml_document(xml_source)
    converted = xml.convert_to(PlistProfile.BINARY_V1, DEFAULT_LIMITS)
    binary = converted.document
    # Native-model equality across the representation change (the round
    # trip contract of RFC 0013 §7).
    assert binary.document() == xml.document()
    back = binary.convert_to(PlistProfile.XML_V1, DEFAULT_LIMITS)
    assert back.document.document() == xml.document()
