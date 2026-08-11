"""Formation golden transcriptions (java-properties-v1.json cases).

Golden cases transcribed verbatim from conformance/vectors/java-properties-
v1.json (suite "consema.java-properties.conformance@1"); each test cites
the vector case id. Assertions check the language-neutral facts the
vectors pin: formation status, natural/logical line counts, comments,
properties, escape counts, decoded keys/values, value states, duplicate
groups, exact syntax coverage, and byte-exact rendering.

Cases covered here:

- java-properties-v1.json: formation.reader-lines-escapes-duplicates
  (lines 5-9), formation.empty-blank-comment-empty-key (11-14),
  formation.mixed-line-terminators (16-19),
  formation.continuation-and-backslash-parity (21-29),
  formation.escape-and-java-utf16-matrix (31-34) — the UTF-16 escape
  semantics test (named/backslash/dropped/unicode kinds, surrogate pair
  and unpaired-surrogate statuses, non-recursive escapes),
  formation.malformed-unicode-recovery-matrix (36-39),
  formation.reader-explicit-encodings (41-49),
  formation.latin1-byte-and-bom-content (51-54) — the Latin-1 versus
  Reader dialect test,
  formation.recovery-never-publishes-partial-operation (56-59).

Formation closure: a Complete parse renders the exact original source
bytes (byte-exact roundtrip; RFC 0010 section 3).
"""

from __future__ import annotations

import pytest

from consema.document.source import SourceEncoding, WindowsCodePage
from consema.properties import (
    JavaStringStatus,
    PropertiesEscapeKind,
    PropertiesFormationFailure,
    PropertiesParseLimits,
    PropertiesProfile,
    PropertiesSyntaxKind,
    PropertiesValueState,
    parse,
    parse_latin1,
    parse_reader,
)
from consema.properties.limits import PropertiesEncodingSelection

DEFAULT_LIMITS = PropertiesParseLimits()


def hex_value(document, ordinal: int) -> str:
    return document.properties[ordinal].value.utf16be_bytes().hex()


def syntax_kind_names(document) -> list[str]:
    return [kind.value for kind in document.lossless_syntax_kinds()]


def diagnostics_of(document) -> list[str]:
    return [diagnostic.code for diagnostic in document.diagnostics]


# ---------------------------------------------------------------------------
# Golden transcriptions
# ---------------------------------------------------------------------------


def test_reader_lines_escapes_duplicates():
    # Case formation.reader-lines-escapes-duplicates
    # (java-properties-v1.json:5-9).
    source = b"  # retained comment\\\r\nkey\\ with\\ spaces : first\\\r\n \tsecond\\u0021\ndup=first\rdup:last\nempty\nexplicit="
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert len(document.natural_lines) == 7
    assert len(document.logical_lines) == 5
    assert len(document.comments) == 1
    assert len(document.properties) == 5
    assert len(document.escapes) == 3
    assert [p.key.to_unicode() for p in document.properties] == [
        "key with spaces",
        "dup",
        "dup",
        "empty",
        "explicit",
    ]
    assert [p.value.to_unicode() for p in document.properties] == [
        "firstsecond!",
        "first",
        "last",
        "",
        "",
    ]
    assert [p.value_state for p in document.properties] == [
        PropertiesValueState.PRESENT,
        PropertiesValueState.PRESENT,
        PropertiesValueState.PRESENT,
        PropertiesValueState.IMPLICIT_EMPTY,
        PropertiesValueState.EXPLICIT_EMPTY,
    ]
    assert document.properties[1].duplicate_group == document.properties[2].duplicate_group
    assert document.properties[1].duplicate_group is not None
    # Exact coverage: the first piece starts at byte 0, the last ends at
    # the source length, and pieces are contiguous (exact_coverage fact).
    pieces = document.structural_index.pieces
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(source)
    assert all(
        pieces[i].span.end_byte == pieces[i + 1].span.start_byte
        for i in range(len(pieces) - 1)
    )
    # Continuation and escape syntax facts are retained.
    names = syntax_kind_names(document)
    assert "ContinuationMarker" in names
    assert "EscapeMarker" in names
    # Fragmented value provenance (two natural-line fragments).
    assert len(document.properties[0].value_fragments) == 2
    assert len(document.properties[0].key_fragments) == 1


def test_empty_blank_comment_empty_key():
    # Case formation.empty-blank-comment-empty-key
    # (java-properties-v1.json:11-14).
    samples = ["", "\n", "# comment\n", "! comment\r", "implicit", "explicit=", "=value", "a=1\nb=2\n"]
    for sample, expected_properties, expected_comments in zip(
        samples, [0, 0, 0, 0, 1, 1, 1, 2], [0, 0, 1, 1, 0, 0, 0, 0]
    ):
        document = parse_reader(sample.encode("utf-8"), SourceEncoding.utf8(), DEFAULT_LIMITS)
        assert document.formation_status().value == "Complete"
        assert len(document.properties) == expected_properties
        assert len(document.comments) == expected_comments
        assert document.render() == sample.encode("utf-8")


def test_mixed_line_terminators():
    # Case formation.mixed-line-terminators (java-properties-v1.json:16-19).
    source = b"a=1\nb=2\rc=3\r\nd=4"
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert len(document.natural_lines) == 4
    assert len(document.logical_lines) == 4
    assert len(document.properties) == 4
    breaks = [
        line.line_break_span is None
        and "Eof"
        or document.source.raw[line.line_break_span.start_byte : line.line_break_span.end_byte]
        .decode("ascii")
        .replace("\r\n", "CrLf")
        .replace("\n", "Lf")
        .replace("\r", "Cr")
        for line in document.natural_lines
    ]
    assert breaks == ["Lf", "Cr", "CrLf", "Eof"]
    pieces = document.structural_index.pieces
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(source)


def test_continuation_and_backslash_parity():
    # Case formation.continuation-and-backslash-parity
    # (java-properties-v1.json:21-29).
    samples = [
        (b"key=value\\", "00760061006c00750065", 1, 1),
        (b"key=value\\\\", "00760061006c00750065005c", 1, 1),
        (b"key=first\\\n  second", "00660069007200730074007300650063006f006e0064", 2, 1),
        (b"key=\\u00\\\n 41", "0041", 2, 1),
    ]
    for source, value_hex, natural_lines, logical_lines in samples:
        document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
        assert document.formation_status().value == "Complete"
        assert len(document.natural_lines) == natural_lines
        assert len(document.logical_lines) == logical_lines
        assert hex_value(document, 0) == value_hex
        pieces = document.structural_index.pieces
        assert pieces[0].span.start_byte == 0
        assert pieces[-1].span.end_byte == len(source)


def test_terminal_odd_backslash_matches_jdk_eof_rule():
    # The OpenJDK line-reader rule removes a final unmatched backslash at
    # end of source, retains it as a ContinuationMarker, emits no code
    # unit, and does not invent an empty following natural line
    # (RFC 0010 section 5; lib.rs test terminal_odd_backslash_matches_jdk
    # _line_reader_eof_rule, lib.rs:1007-1025).
    source = b"key=value\\"
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.properties[0].value.to_unicode() == "value"
    assert document.render() == source
    assert document.lossless_syntax_kinds()[-1] is PropertiesSyntaxKind.CONTINUATION_MARKER


def test_escape_and_java_utf16_matrix():
    # Case formation.escape-and-java-utf16-matrix
    # (java-properties-v1.json:31-34) — the UTF-16 escape semantics test.
    source = (
        b"named=\\t\\n\\r\\f\n"
        b"slash=\\\\\n"
        b"dropped=\\q\n"
        b"nonrecursive=\\u005Cu0041\n"
        b"pair=\\uD83D\\uDE00\n"
        b"high=\\uD800\n"
        b"low=\\uDC00\n"
        b"high-before=\\uD800A\n"
        b"low-after=A\\uDC00\n"
    )
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert [hex_value(document, i) for i in range(9)] == [
        "0009000a000d000c",
        "005c",
        "0071",
        "005c00750030003000340031",
        "d83dde00",
        "d800",
        "dc00",
        "d8000041",
        "0041dc00",
    ]
    assert [p.value.status() for p in document.properties] == [
        JavaStringStatus.WELL_FORMED_UNICODE,
        JavaStringStatus.WELL_FORMED_UNICODE,
        JavaStringStatus.WELL_FORMED_UNICODE,
        JavaStringStatus.WELL_FORMED_UNICODE,
        JavaStringStatus.WELL_FORMED_UNICODE,
        JavaStringStatus.UNPAIRED_SURROGATE,
        JavaStringStatus.UNPAIRED_SURROGATE,
        JavaStringStatus.UNPAIRED_SURROGATE,
        JavaStringStatus.UNPAIRED_SURROGATE,
    ]
    assert [e.kind for e in document.escapes] == [
        PropertiesEscapeKind.NAMED,
        PropertiesEscapeKind.NAMED,
        PropertiesEscapeKind.NAMED,
        PropertiesEscapeKind.NAMED,
        PropertiesEscapeKind.BACKSLASH,
        PropertiesEscapeKind.DROPPED_BACKSLASH,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
        PropertiesEscapeKind.UNICODE,
    ]
    # The pair converts to one Unicode scalar; an unpaired surrogate
    # blocks the Unicode conversion (RFC 0010 section 7).
    assert document.properties[4].value.to_unicode() == chr(0x1F600)
    with pytest.raises(Exception):
        document.properties[5].value.to_unicode()


def test_unicode_escape_is_not_recursively_decoded():
    # ``\u005C`` produces a backslash which is not rescanned as another
    # escape (RFC 0010 section 7; vector case
    # formation.escape-and-java-utf16-matrix, nonrecursive line: one
    # Unicode escape, value "005c00750030003000340031").
    document = parse_reader(
        b"nonrecursive=\\u005Cu0041\n", SourceEncoding.utf8(), DEFAULT_LIMITS
    )
    assert document.properties[0].value.to_unicode() == "\\u0041"
    assert len(document.escapes) == 1
    assert document.escapes[0].kind is PropertiesEscapeKind.UNICODE


def test_malformed_unicode_recovery_matrix():
    # Case formation.malformed-unicode-recovery-matrix
    # (java-properties-v1.json:36-39).
    samples = ["a=\\u", "a=\\u1", "a=\\u12", "a=\\u123", "a=\\u12G4", "a=\\U0041"]
    for sample, formation, property_count, error_count in zip(
        samples,
        ["Recovered"] * 5 + ["Complete"],
        [0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 0],
    ):
        document = parse_reader(sample.encode("utf-8"), SourceEncoding.utf8(), DEFAULT_LIMITS)
        assert document.formation_status().value == formation
        assert len(document.properties) == property_count
        assert len(document.error_lines) == error_count
        if error_count:
            assert (
                document.error_lines[0].code
                == "java-properties.parse.malformed-unicode-escape@1"
            )
            assert diagnostics_of(document) == [
                "java-properties.parse.malformed-unicode-escape@1"
            ]
    # The uppercase ``\U`` is a dropped backslash: the value is "U0041"
    # and the document is Complete (uppercase_u_value fact).
    document = parse_reader(b"a=\\U0041", SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.properties[0].value.to_unicode() == "U0041"


def test_reader_explicit_encodings():
    # Case formation.reader-explicit-encodings (java-properties-v1.json:41-49).
    cases = [
        (SourceEncoding.utf8(), bytes.fromhex("e5908d3de580bc0a"), "名", "值", None),
        (SourceEncoding.utf16le(), bytes.fromhex("fffe6b003d007600"), "k", "v", "Utf16Le"),
        (SourceEncoding.utf16be(), bytes.fromhex("feff006b003d0076"), "k", "v", "Utf16Be"),
    ]
    for encoding, source, key, value, bom in cases:
        document = parse_reader(source, encoding, DEFAULT_LIMITS)
        assert document.formation_status().value == "Complete"
        assert document.properties[0].key.to_unicode() == key
        assert document.properties[0].value.to_unicode() == value
        facts = document.source.encoding_facts()
        assert (facts.bom.value if facts.bom else None) == bom
        assert document.render() == source
        pieces = document.structural_index.pieces
        assert pieces[0].span.start_byte == 0
        assert pieces[-1].span.end_byte == len(source)


def test_reader_explicit_cp1252_encoding():
    # Case formation.reader-explicit-encodings, WindowsCodePage(1252)
    # sample (java-properties-v1.json:46-47): "name=caf\xE9\n" decodes
    # under the strict code page.
    #
    # BLOCKED by a document-domain defect: consema/document/source.py:838
    # unpacks a two-tuple from the Python 3.12 incremental decoder, which
    # returns the decoded string only. The properties family depends on
    # SourceSnapshot.from_raw for every decode; the fix belongs to the
    # document agent. Verification item, not a claim.
    encoding = SourceEncoding.windows_code_page(WindowsCodePage.from_number(1252))
    source = bytes.fromhex("6e616d653d636166e90a")
    document = parse_reader(source, encoding, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.properties[0].key.to_unicode() == "name"
    assert document.properties[0].value.to_unicode() == "café"
    assert document.render() == source


def test_latin1_treats_bom_bytes_as_content():
    # Case formation.latin1-byte-and-bom-content
    # (java-properties-v1.json:51-54) — the Latin-1 versus Reader dialect
    # test: a UTF-8 BOM byte sequence has no BOM meaning and is ordinary
    # Latin-1 data (RFC 0010 section 3.2).
    source = bytes.fromhex("efbbbf6b3dff")
    document = parse_latin1(source, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.properties[0].key.utf16be_bytes().hex() == "00ef00bb00bf006b"
    assert document.properties[0].value.utf16be_bytes().hex() == "00ff"
    assert document.source.encoding_facts().bom is None
    assert "Bom" not in syntax_kind_names(document)
    assert document.render() == source
    pieces = document.structural_index.pieces
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(source)


def test_reader_dialect_recognizes_a_supported_bom():
    # The Reader dialect honors an explicit matching UTF-16 BOM (RFC 0010
    # section 3.1; lib.rs test reader_honors_an_explicit_matching_utf16_bom,
    # lib.rs:971-989).
    source = bytes.fromhex("fffe6b003d007600")
    document = parse_reader(source, SourceEncoding.utf16le(), DEFAULT_LIMITS)
    assert document.properties[0].key.to_unicode() == "k"
    assert document.source.encoding_facts().bom.value == "Utf16Le"
    assert document.lossless_syntax_kinds()[0] is PropertiesSyntaxKind.BOM


def test_recovery_never_publishes_partial_operation():
    # Case formation.recovery-never-publishes-partial-operation
    # (java-properties-v1.json:56-59).
    source = b"good=ok\nbad=\\u12G4\nafter=yes"
    document = parse_reader(source, SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert [p.key.to_unicode() for p in document.properties] == ["good", "after"]
    assert len(document.error_lines) == 1
    assert (
        document.error_lines[0].code
        == "java-properties.parse.malformed-unicode-escape@1"
    )
    assert diagnostics_of(document) == [
        "java-properties.parse.malformed-unicode-escape@1"
    ]
    # The malformed logical line is an ErrorRegion in the syntax stream.
    assert "ErrorRegion" in syntax_kind_names(document)


def test_profile_source_mismatch_is_fatal():
    # The profile is always selected by the caller; a Latin-1 profile with
    # a Reader source contract is a fatal formation failure carrying
    # java-properties.source.profile-encoding@1 (RFC 0010 section 3;
    # parser.rs:57-91).
    with pytest.raises(PropertiesFormationFailure) as caught:
        parse(
            b"k=v",
            PropertiesProfile.LATIN1_V1,
            PropertiesEncodingSelection.reader(SourceEncoding.utf8()),
            DEFAULT_LIMITS,
        )
    assert caught.value.code == "java-properties.source.profile-encoding@1"


def test_empty_source_is_complete_with_zero_records():
    # An empty source decomposes into no natural lines and no properties
    # (vector case formation.empty-blank-comment-empty-key, first sample).
    document = parse_reader(b"", SourceEncoding.utf8(), DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert len(document.properties) == 0
    assert len(document.natural_lines) == 0
    assert len(document.structural_index.pieces) == 0
    assert document.render() == b""
