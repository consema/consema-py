"""Formation golden transcriptions for the plist family (XML and binary).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json
(suite "consema.plist.conformance@1"); each test cites the vector case id.
Assertions check the language-neutral facts the vectors pin (formation
status, diagnostics, exact renders, native value facts, binary trailer and
offset facts, exhaustive coverage, and fatal limits).

Cases covered here:

- plist-v1.json: plist.xml-formation.all-value-types (lines 9-45),
  plist.xml-formation.doctype-violations (59-88),
  plist.xml-formation.root-contracts (89-128),
  plist.xml-formation.integer-matrix (178-236),
  plist.xml-formation.date-matrix (253-293),
  plist.xml-formation.base64-matrix (294-334),
  plist.xml-formation.empty-value-matrix (389-435),
  plist.xml-formation.trailing-content (360-371),
  plist.xml-formation.utf16le-input (436-449),
  plist.binary-formation.minimal-document (450-467),
  plist.binary-formation.all-types-document (468-508),
  plist.binary-formation.integer-width-matrix (509-598),
  plist.binary-formation.strings-matrix (599-641),
  plist.binary-formation.uid-matrix (642-683),
  plist.binary-formation.header-and-trailer (774-819),
  plist.binary-formation.offset-and-reference (820-859),
  plist.binary-formation.extended-size-and-cycle (860-886),
  plist.binary-formation.value-integrity (887-916).
"""

from __future__ import annotations

import pytest

from consema.plist import (
    PlistEncodingSelection,
    PlistFormationFailure,
    PlistParseLimits,
    PlistProfile,
    parse,
)

DEFAULT_LIMITS = PlistParseLimits()


def parse_xml_source(source: str):
    return parse(
        source.encode("utf-8"),
        PlistProfile.XML_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def parse_binary_hex(hex_string: str):
    return parse(
        bytes.fromhex(hex_string),
        PlistProfile.BINARY_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def diagnostics_of(document) -> list[str]:
    return [diagnostic.code for diagnostic in document.diagnostics]


def assert_exact_coverage(document, raw: bytes) -> None:
    pieces = document.lossless_structural_index().pieces
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(raw)
    for left, right in zip(pieces, pieces[1:]):
        assert left.span.end_byte == right.span.start_byte


def assert_binary_coverage(document, raw: bytes) -> None:
    regions = document.binary_structural_index().regions
    assert regions[0].span.start_byte == 0
    assert regions[-1].span.end_byte == len(raw)
    for left, right in zip(regions, regions[1:]):
        assert left.span.end_byte == right.span.start_byte


# ---------------------------------------------------------------------------
# plist.xml-formation.all-value-types (plist-v1.json:9-45)
# ---------------------------------------------------------------------------


def test_xml_all_value_types_golden():
    # Case plist.xml-formation.all-value-types (plist-v1.json:10-44).
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
    document = parse_xml_source(source)
    assert document.formation_status().value == "Complete"
    assert document.render() == source.encode("utf-8")
    native = document.document()
    assert native is not None
    root = native.root_value()
    assert root.kind.value == "dict"
    dict_value = root.as_dict()
    keys = [entry.key.to_unicode() for entry in dict_value.entries]
    assert keys == [
        "name",
        "count",
        "ratio",
        "negative",
        "enabled",
        "disabled",
        "payload",
        "born",
        "tags",
        "empty",
    ]
    values = [native.get(entry.value) for entry in dict_value.entries]
    by_key = dict(zip(keys, values))
    assert by_key["count"].as_integer().value == 42
    assert by_key["negative"].as_integer().value == -7
    assert by_key["ratio"].as_real().as_f64() == 1500.0
    assert by_key["enabled"].as_boolean().value is True
    assert by_key["disabled"].as_boolean().value is False
    assert by_key["payload"].as_data().bytes == bytes.fromhex("010203")
    assert by_key["born"].as_date().seconds == 694224000.0
    tags = by_key["tags"].as_array()
    tag_values = [native.get(ref) for ref in tags.elements]
    assert tag_values[0].as_string().to_unicode() == "a"
    assert tag_values[1].kind.value == "dict"
    assert by_key["empty"].as_string().to_unicode() == ""
    assert_exact_coverage(document, source.encode("utf-8"))


def test_xml_doctype_violations_are_recovered():
    # Case plist.xml-formation.doctype-violations (plist-v1.json:60-87).
    samples = [
        (
            '<!DOCTYPE wrong PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><string>ok</string></plist>'
        ),
        (
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd" [<!ENTITY x "y">]>\n'
            '<plist version="1.0"><string>ok</string></plist>'
        ),
        (
            '<!DOCTYPE plist SYSTEM "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><string>ok</string></plist>'
        ),
    ]
    expected_codes = [
        "plist.parse.doctype@1",
        "plist.parse.doctype-subset@1",
        "plist.parse.doctype@1",
    ]
    for source, code in zip(samples, expected_codes):
        document = parse_xml_source(source)
        assert document.formation_status().value == "Recovered"
        assert code in diagnostics_of(document)


def test_xml_root_contracts_are_recovered():
    # Case plist.xml-formation.root-contracts (plist-v1.json:90-127).
    samples = [
        ("<plist><string>ok</string></plist>", "plist.parse.root-version@1"),
        ('<plist version="2.0"><string>ok</string></plist>', "plist.parse.root-version@1"),
        ('<plist version="1.0" extra="1"><string>ok</string></plist>', "plist.parse.root-attribute@1"),
        ('<plist version="1.0"></plist>', "plist.parse.root-value-count@1"),
        ('<plist version="1.0"><string>a</string><string>b</string></plist>', "plist.parse.root-value-count@1"),
    ]
    for source, code in samples:
        document = parse_xml_source(source)
        assert document.formation_status().value == "Recovered"
        assert code in diagnostics_of(document)


def test_xml_integer_matrix():
    # Case plist.xml-formation.integer-matrix (plist-v1.json:179-236).
    samples = [
        ("<integer>-42</integer>", "Complete", -42),
        ("<integer>0x2A</integer>", "Complete", 42),
        ("<integer>+ 7</integer>", "Complete", 7),
        ("<integer>007</integer>", "Complete", 7),
        ("<integer>12a</integer>", "Recovered", None),
        ("<integer>9223372036854775808</integer>", "Recovered", None),
        ("<integer>-9223372036854775809</integer>", "Recovered", None),
    ]
    for text, status, expected in samples:
        document = parse_xml_source(f'<plist version="1.0">{text}</plist>')
        assert document.formation_status().value == status, text
        if status == "Recovered":
            assert "plist.parse.integer@1" in diagnostics_of(document)
        else:
            assert document.document().root_value().as_integer().value == expected


def test_xml_real_special_values_admitted():
    # Case plist.xml-formation.real-special-values (plist-v1.json:238-251).
    source = (
        '<plist version="1.0"><array><real>nan</real><real>-inf</real>'
        "<real>+infinity</real><real>1.5e3</real></array></plist>"
    )
    document = parse_xml_source(source)
    assert document.formation_status().value == "Complete"
    native = document.document()
    elements = native.root_value().as_array().elements
    values = [native.get(ref).as_real().as_f64() for ref in elements]
    assert values[0] != values[0]  # nan
    assert values[1] == float("-inf")
    assert values[2] == float("inf")
    assert values[3] == 1500.0


def test_xml_date_matrix():
    # Case plist.xml-formation.date-matrix (plist-v1.json:254-293).
    samples = [
        ("2023-01-01T00:00:00Z", "Complete", 694224000.0),
        ("2023-01-01T00:00:00.5Z", "Recovered", None),
        ("2024-02-30T00:00:00Z", "Recovered", None),
        ("2023-01-01T00:00:00", "Recovered", None),
    ]
    for text, status, expected in samples:
        document = parse_xml_source(f'<plist version="1.0"><date>{text}</date></plist>')
        assert document.formation_status().value == status, text
        if status == "Recovered":
            assert "plist.parse.date@1" in diagnostics_of(document)
        else:
            assert document.document().root_value().as_date().seconds == expected


def test_xml_base64_matrix():
    # Case plist.xml-formation.base64-matrix (plist-v1.json:295-334).
    samples = [
        ("<data>QUJD</data>", "Complete", "414243"),
        ("<data>QU\nJD</data>", "Complete", "414243"),
        ("<data>QUJ</data>", "Recovered", None),
        ("<data>AB$C</data>", "Recovered", None),
    ]
    for text, status, expected_hex in samples:
        document = parse_xml_source(f'<plist version="1.0">{text}</plist>')
        assert document.formation_status().value == status, text
        if status == "Recovered":
            assert "plist.parse.data@1" in diagnostics_of(document)
        else:
            data = document.document().root_value().as_data()
            assert data.bytes.hex() == expected_hex


def test_xml_empty_value_matrix():
    # Case plist.xml-formation.empty-value-matrix (plist-v1.json:390-435).
    samples = [
        ("<date/>", "Recovered", "plist.parse.empty-value@1"),
        ("<integer/>", "Recovered", "plist.parse.empty-value@1"),
        ("<data/>", "Recovered", "plist.parse.empty-value@1"),
        ("<data></data>", "Complete", None),
        ("<string/>", "Complete", None),
    ]
    for text, status, code in samples:
        document = parse_xml_source(f'<plist version="1.0">{text}</plist>')
        assert document.formation_status().value == status, text
        if code is not None:
            assert code in diagnostics_of(document)
        else:
            root = document.document().root_value()
            if text.startswith("<data>"):
                assert root.as_data().bytes == b""
            else:
                assert root.as_string().to_unicode() == ""


def test_xml_trailing_content_is_recovered():
    # Case plist.xml-formation.trailing-content (plist-v1.json:361-371).
    source = '<plist version="1.0"><string>ok</string></plist> trailing'
    document = parse_xml_source(source)
    assert document.formation_status().value == "Recovered"
    assert "plist.parse.well-formedness@1" in diagnostics_of(document)
    assert document.document() is not None
    assert document.render() == source.encode("utf-8")


def test_xml_utf16le_input_golden():
    # Case plist.xml-formation.utf16le-input (plist-v1.json:437-449).
    source = '<plist version="1.0"><string>中文</string></plist>'
    raw = ("﻿" + source).encode("utf-16-le")
    document = parse(
        raw,
        PlistProfile.XML_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.document().root_value().as_string().to_unicode() == "中文"
    assert document.render().hex() == (
        "fffe3c0070006c006900730074002000760065007200730069006f006e003d00220031"
        "002e00300022003e003c0073007400720069006e0067003e002d4e87653c002f007300"
        "7400720069006e0067003e003c002f0070006c006900730074003e00"
    )


# ---------------------------------------------------------------------------
# plist.binary-formation.minimal-document (plist-v1.json:450-467)
# ---------------------------------------------------------------------------


def test_binary_minimal_document_golden():
    # Case plist.binary-formation.minimal-document (plist-v1.json:451-466).
    raw = bytes.fromhex(
        "62706c697374303050080000000000000101000000000000000100000000000000000000000000000009"
    )
    document = parse_binary_hex(raw.hex())
    assert document.formation_status().value == "Complete"
    assert document.render() == raw
    assert document.document().root_value().as_string().to_unicode() == ""
    facts = document.binary_facts()
    assert facts.trailer.num_objects == 1
    assert facts.trailer.top_object == 0
    assert facts.trailer.offset_int_size == 1
    assert facts.trailer.object_ref_size == 1
    assert facts.trailer.offset_table_offset == 9
    assert facts.trailer.sort_version == 0
    assert_binary_coverage(document, raw)


# ---------------------------------------------------------------------------
# plist.binary-formation.all-types-document (plist-v1.json:468-508)
# ---------------------------------------------------------------------------


def test_binary_all_types_document_golden():
    # Case plist.binary-formation.all-types-document (plist-v1.json:469-507).
    raw = bytes.fromhex(
        "62706c6973743030d90102030405060708090a0d0e0f101112131455617272617954626f6f"
        "6c54646174615464617465536633325a6672616374696f6e616c53696e74547265616c537374"
        "72a20b0c100110020943010203330000000000000000223f000000333ff8000000000000102a"
        "233ff8000000000000526869081b21262b30343f43484c4f5153545861666f717a00000000"
        "0000010100000000000000150000000000000000000000000000007d"
    )
    document = parse_binary_hex(raw.hex())
    assert document.formation_status().value == "Complete"
    native = document.document()
    root = native.root_value()
    assert root.kind.value == "dict"
    dict_value = root.as_dict()
    keys = [entry.key.to_unicode() for entry in dict_value.entries]
    assert keys == [
        "array",
        "bool",
        "data",
        "date",
        "f32",
        "fractional",
        "int",
        "real",
        "str",
    ]
    values = {entry.key.to_unicode(): native.get(entry.value) for entry in dict_value.entries}
    assert values["int"].as_integer().value == 42
    assert values["real"].as_real().as_f64() == 1.5
    assert values["f32"].as_real().as_f64() == 0.5
    assert values["f32"].as_real().width.value == "Float32"
    assert values["data"].as_data().bytes == bytes.fromhex("010203")
    assert values["date"].as_date().seconds == 0.0
    assert values["fractional"].as_date().seconds == 1.5
    assert values["bool"].as_boolean().value is True
    array = values["array"].as_array()
    assert [native.get(ref).as_integer().value for ref in array.elements] == [1, 2]
    assert values["str"].as_string().to_unicode() == "hi"
    facts = document.binary_facts()
    assert facts.trailer.num_objects == 21
    assert facts.trailer.offset_int_size == 1
    assert facts.trailer.object_ref_size == 1
    assert facts.trailer.offset_table_offset == 125
    assert len(facts.objects) == 21
    assert_binary_coverage(document, raw)


def test_binary_integer_width_matrix():
    # Case plist.binary-formation.integer-width-matrix (plist-v1.json:510-598).
    samples = [
        "62706c6973743030100008000000000000010100000000000000010000000000000000000000000000000a",
        "62706c6973743030100108000000000000010100000000000000010000000000000000000000000000000a",
        "62706c697374303010ff08000000000000010100000000000000010000000000000000000000000000000a",
        "62706c697374303011010008000000000000010100000000000000010000000000000000000000000000000b",
        "62706c697374303011ffff08000000000000010100000000000000010000000000000000000000000000000b",
        "62706c6973743030120001000008000000000000010100000000000000010000000000000000000000000000000d",
        "62706c697374303012ffffffff08000000000000010100000000000000010000000000000000000000000000000d",
        "62706c6973743030130000000000000005080000000000000101000000000000000100000000000000000000000000000011",
        "62706c697374303013ffffffffffffffff080000000000000101000000000000000100000000000000000000000000000011",
        "62706c697374303013ffffffffffffffd6080000000000000101000000000000000100000000000000000000000000000011",
        "62706c6973743030137fffffffffffffff080000000000000101000000000000000100000000000000000000000000000011",
        "62706c6973743030138000000000000000080000000000000101000000000000000100000000000000000000000000000011",
    ]
    expected = [
        0,
        1,
        255,
        256,
        65535,
        65536,
        4294967295,
        5,
        -1,
        -42,
        9223372036854775807,
        -9223372036854775808,
    ]
    for hex_string, value in zip(samples, expected):
        document = parse_binary_hex(hex_string)
        assert document.formation_status().value == "Complete", hex_string
        assert document.document().root_value().as_integer().value == value


def test_binary_strings_matrix():
    # Case plist.binary-formation.strings-matrix (plist-v1.json:600-641).
    ascii_good = "62706c69737430305548656c6c6f08000000000000010100000000000000010000000000000000000000000000000e"
    utf16_good = "62706c6973743030624e16754c08000000000000010100000000000000010000000000000000000000000000000d"
    high_bit = "62706c697374303051e908000000000000010100000000000000010000000000000000000000000000000a"
    unpaired = "62706c697374303062d800004108000000000000010100000000000000010000000000000000000000000000000d"
    document = parse_binary_hex(ascii_good)
    assert document.formation_status().value == "Complete"
    assert document.document().root_value().as_string().to_unicode() == "Hello"
    document = parse_binary_hex(utf16_good)
    assert document.formation_status().value == "Complete"
    assert document.document().root_value().as_string().to_unicode() == "世界"
    document = parse_binary_hex(high_bit)
    assert document.formation_status().value == "Recovered"
    assert "plist.binary.string@1" in diagnostics_of(document)
    document = parse_binary_hex(unpaired)
    assert document.formation_status().value == "Complete"
    string = document.document().root_value().as_string()
    assert string.status().value == "UnpairedSurrogate"
    assert string.utf16be_bytes().hex() == "d8000041"


def test_binary_uid_matrix():
    # Case plist.binary-formation.uid-matrix (plist-v1.json:642-683).
    good = "62706c6973743030800508000000000000010100000000000000010000000000000000000000000000000a"
    wide = "62706c6973743030830102030408000000000000010100000000000000010000000000000000000000000000000d"
    non_minimal = "62706c69737430308500000000000508000000000000010100000000000000010000000000000000000000000000000f"
    overflow = "62706c697374303084010000000008000000000000010100000000000000010000000000000000000000000000000e"
    document = parse_binary_hex(good)
    assert document.document().root_value().as_uid().value == 5
    document = parse_binary_hex(wide)
    assert document.document().root_value().as_uid().value == 16909060
    document = parse_binary_hex(non_minimal)
    assert document.document().root_value().as_uid().value == 5
    document = parse_binary_hex(overflow)
    assert document.formation_status().value == "Recovered"
    assert "plist.binary.uid@1" in diagnostics_of(document)


# ---------------------------------------------------------------------------
# Trailer hardening: no false Complete (plist-v1.json:774-859)
# ---------------------------------------------------------------------------


def test_binary_header_and_trailer_matrix():
    # Case plist.binary-formation.header-and-trailer (plist-v1.json:775-818).
    bad_version = "62706c697374303150080000000000000101000000000000000100000000000000000000000000000009"
    sort_one = "62706c697374303050080000000000010101000000000000000100000000000000000000000000000009"
    sort_two = "62706c697374303050080100000000000101000000000000000100000000000000000000000000000009"
    unused_nonzero = "62706c697374303050080000000000000101000000000000000000000000000000000000000000000009"
    total_length_bad = "62706c697374303050080000000000000101000000000000000100000000000000010000000000000009"
    ref_size_zero = "62706c6973743030514100000000000000080000000000000001000000000000000100000000000000000000000000000009"
    document = parse_binary_hex(bad_version)
    assert document.formation_status().value == "Recovered"
    assert "plist.binary.header@1" in diagnostics_of(document)
    document = parse_binary_hex(sort_one)
    assert document.formation_status().value == "Complete"
    assert document.binary_facts().trailer.sort_version == 1
    for hex_string, code in [
        (sort_two, "plist.binary.trailer@1"),
        (unused_nonzero, "plist.binary.trailer@1"),
        (total_length_bad, "plist.binary.trailer@1"),
        (ref_size_zero, "plist.binary.trailer@1"),
    ]:
        document = parse_binary_hex(hex_string)
        assert document.formation_status().value == "Recovered", hex_string
        assert code in diagnostics_of(document), hex_string
        # A recovered trailer never fabricates a native document root.
        assert document.document() is None


def test_binary_offset_and_reference_matrix():
    # Case plist.binary-formation.offset-and-reference (plist-v1.json:821-858).
    offset_below_8 = "62706c697374303050500805000000000000010100000000000000020000000000000000000000000000000a"
    offset_past_table = "62706c69737430305050080a000000000000010100000000000000020000000000000000000000000000000a"
    ref_out_of_range = "62706c6973743030a10250080a000000000000010100000000000000020000000000000000000000000000000b"
    offset_width_small = (
        "62706c69737430305f10fa" + "61" * 250
        + "08090000000000000101000000000000000200000000000000000000000000000105"
    )
    extra_byte = "62706c6973743030500808080000000000000101000000000000000200000000000000000000000000000009"
    for hex_string, code in [
        (offset_below_8, "plist.binary.offset-table@1"),
        (offset_past_table, "plist.binary.offset-table@1"),
        (ref_out_of_range, "plist.binary.reference@1"),
        (offset_width_small, "plist.binary.trailer@1"),
        (extra_byte, "plist.binary.trailer@1"),
    ]:
        document = parse_binary_hex(hex_string)
        assert document.formation_status().value == "Recovered", hex_string[:32]
        assert code in diagnostics_of(document), hex_string[:32]


def test_binary_extended_size_and_cycle():
    # Case plist.binary-formation.extended-size-and-cycle (plist-v1.json:861-886).
    extended = (
        "62706c6973743030af101002030405060708090a0b0c0d0e0f1011"
        + "50" * 16
        + "08091b1c1d1e1f202122232425262728292a00000000000001010000000000000012"
        "0000000000000000000000000000002b"
    )
    document = parse_binary_hex(extended)
    assert document.formation_status().value == "Complete"
    native = document.document()
    root = native.root_value()
    array = root.as_array()
    assert len(array.elements) == 16
    cycle = "62706c6973743030a10008000000000000010100000000000000010000000000000000000000000000000a"
    document = parse_binary_hex(cycle)
    assert document.formation_status().value == "Recovered"
    assert "plist.binary.cycle@1" in diagnostics_of(document)
    assert document.document() is None


def test_binary_value_integrity_matrix():
    # Case plist.binary-formation.value-integrity (plist-v1.json:888-916).
    non_finite_date = "62706c6973743030337ff8000000000000080000000000000101000000000000000100000000000000000000000000000011"
    non_string_key = "62706c6973743030d1010210015161080b0d000000000000010100000000000000030000000000000000000000000000000f"
    bad_extended = (
        "62706c6973743030af5001010101010101010101010101010101"
        "0809000000000000010100000000000000020000000000000000000000000000001a"
    )
    for hex_string, code in [
        (non_finite_date, "plist.binary.date@1"),
        (non_string_key, "plist.binary.non-string-key@1"),
        (bad_extended, "plist.binary.extended-size@1"),
    ]:
        document = parse_binary_hex(hex_string)
        assert document.formation_status().value == "Recovered", hex_string[:32]
        assert code in diagnostics_of(document), hex_string[:32]


def test_binary_minimum_size_is_fatal():
    # RFC 0013 §2.2: a minimum size of 42 bytes is enforced before a
    # Document can exist (parser_binary.rs:529-540).
    with pytest.raises(PlistFormationFailure) as excinfo:
        parse_binary_hex("62706c6973743030")
    assert excinfo.value.code == "plist.binary.minimum-size@1"


def test_binary_encoding_selection_conflict_is_fatal():
    # RFC 0013 §2.2: the binary profile admits only ProfileDefault and
    # Explicit(Binary); any other selection is plist.binary.encoding@1
    # (lib.rs:241-260).
    from consema.document.source import SourceEncoding

    with pytest.raises(PlistFormationFailure) as excinfo:
        parse(
            bytes.fromhex(
                "62706c697374303050080000000000000101000000000000000100000000000000000000000000000009"
            ),
            PlistProfile.BINARY_V1,
            PlistEncodingSelection.explicit(SourceEncoding.utf8()),
            DEFAULT_LIMITS,
        )
    assert excinfo.value.code == "plist.binary.encoding@1"


def test_binary_duplicate_keys_preserved():
    # Case plist.binary-formation.duplicate-keys (plist-v1.json:704-723).
    raw = bytes.fromhex(
        "62706c6973743030d201010203516b10011002080d0f110000000000000101000000000000000400000000000000000000000000000013"
    )
    document = parse_binary_hex(raw.hex())
    assert document.formation_status().value == "Complete"
    native = document.document()
    dict_value = native.root_value().as_dict()
    assert [entry.key.to_unicode() for entry in dict_value.entries] == ["k", "k"]
    assert [native.get(entry.value).as_integer().value for entry in dict_value.entries] == [1, 2]
