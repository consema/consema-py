"""Formation golden transcriptions and closure (JSON/JSONC/JSON5).

Golden cases transcribed verbatim from conformance/vectors/json-family-v2.json
(suite "consema.json-family.conformance@2") and v1.json json cases; each
test cites the vector case id. Assertions check the language-neutral
facts the vectors pin (formation, root kind, member names/kinds,
syntax-kind presence, diagnostics, fatal limits).

Cases covered here:

- json-family-v2.json: json5.parse.full-surface (lines 5-10),
  json5.parse.identifiers (12-16), json5.parse.string-extensions (18-22),
  json5.parse.extended-whitespace-comments (24-28),
  json5.parse.unescaped-separator-warning (30-34),
  json5.reject.invalid-escaped-identifier (36-40),
  json5.reject.leading-zero-decimal (42-46), json5.reject.empty-hex
  (48-52), json5.reject.decimal-string-escape (54-58),
  json5.reject.isolated-surrogate (60-64), json5.reject.unterminated-
  comment (66-70), json.strict.reject-json5-surface (72-76),
  jsonc.complete-shared-surface (78-82), json5.complete-jsonc-surface
  (84-88), json5.number.positive-infinity (90-94), json5.number.negative-
  nan (96-100), json5.number.huge-hex-exact (102-106),
  json5.number.leading-trailing-exact (108-112), json5.security.depth-
  limit (198-202).
- v1.json: parse.strict-exact-roundtrip (41-45), parse.jsonc-comments-
  trailing-comma (47-51), parse.recovery-missing-close (53-57),
  parse.duplicate-members (59-63), parse.lossless-byte-coverage (65-69).

Formation closure: a Complete parse renders the exact original source
bytes (byte-exact roundtrip, v1.json:41-45).
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.json import (
    JsonFormationFailure,
    JsonFormationFailureKind,
    JsonProfile,
    JsonValueKind,
    parse,
)
from consema.json.parser import (
    BITS_NEGATIVE_INFINITY,
    BITS_NEGATIVE_NAN,
    BITS_POSITIVE_INFINITY,
)

DEFAULT_LIMITS = ParseLimits()


def member_names(document) -> list[str]:
    availability = document.root().object_members()
    assert availability.is_available
    return [member.name().value for member in availability.value]


def member_kinds(document) -> list[str]:
    availability = document.root().object_members()
    assert availability.is_available
    kinds = []
    for member in availability.value:
        kind = member.value().kind()
        assert kind.is_available
        kinds.append(kind.value.value)
    return kinds


def syntax_kind_names(document) -> list[str]:
    return [kind.value for kind in document.lossless_syntax_kinds()]


def diagnostics_of(document) -> list[str]:
    return [diagnostic.code for diagnostic in document.diagnostics]


# ---------------------------------------------------------------------------
# json-family-v2.json formation cases
# ---------------------------------------------------------------------------


def test_json5_parse_full_surface():
    # Case json5.parse.full-surface (json-family-v2.json:5-10).
    source = (
        "\ufeff{ // lead\nunquoted:'value',\\u0061:.5,hex:+0X10,trail:1.,"
        "exp:1.e+2,truth:true,nil:null,inf:-Infinity,nan:+NaN,}"
    )
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.OBJECT
    assert member_names(document) == [
        "unquoted", "a", "hex", "trail", "exp", "truth", "nil", "inf", "nan",
    ]
    assert member_kinds(document) == [
        "String", "Decimal", "Integer", "Decimal", "Decimal",
        "Boolean", "Null", "BinaryFloat64", "BinaryFloat64",
    ]
    names = syntax_kind_names(document)
    assert "Bom" in names
    assert "LineComment" in names
    assert "Identifier" in names


def test_json5_parse_identifiers():
    # Case json5.parse.identifiers (json-family-v2.json:12-16).
    source = "{$_:1,while:2,true:3,π:4,\\u0061:5,a\u200c:6,a\u200d:7}"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.OBJECT
    assert member_names(document) == ["$_", "while", "true", "π", "a", "a\u200c", "a\u200d"]
    assert "Identifier" in syntax_kind_names(document)


def test_json5_parse_string_extensions():
    # Case json5.parse.string-extensions (json-family-v2.json:18-22).
    source = "['single','\\x41','\\v','\\0','\\q','line\\\nnext','\\uD83D\\uDE00']"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.ARRAY
    availability = document.root().array_elements()
    assert availability.is_available
    strings = []
    for element in availability.value:
        value = element.value().as_string()
        assert value.is_available
        strings.append(value.value)
    assert strings == ["single", "A", "\u000b", "\u0000", "q", "linenext", "😀"]


def test_json5_parse_extended_whitespace_comments():
    # Case json5.parse.extended-whitespace-comments (json-family-v2.json:24-28).
    source = "\u00a0\u1680// line\u2028[1,/* block */2,]\u3000"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.ARRAY
    names = syntax_kind_names(document)
    assert "Whitespace" in names
    assert "LineComment" in names
    assert "BlockComment" in names


def test_json5_parse_unescaped_separator_warning():
    # Case json5.parse.unescaped-separator-warning (json-family-v2.json:30-34).
    source = "'a\u2028b'"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.STRING
    assert "json5.string.unescaped-line-separator@1" in diagnostics_of(document)


def test_json5_reject_invalid_escaped_identifier():
    # Case json5.reject.invalid-escaped-identifier (json-family-v2.json:36-40).
    document = parse(b"{\\u0030bad:1}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json5.syntax.invalid-identifier@1" in diagnostics_of(document)


def test_json5_reject_leading_zero_decimal():
    # Case json5.reject.leading-zero-decimal (json-family-v2.json:42-46).
    document = parse(b"01", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.invalid-number@1" in diagnostics_of(document)


def test_json5_reject_empty_hex():
    # Case json5.reject.empty-hex (json-family-v2.json:48-52).
    document = parse(b"0x", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.invalid-number@1" in diagnostics_of(document)


def test_json5_reject_decimal_string_escape():
    # Case json5.reject.decimal-string-escape (json-family-v2.json:54-58).
    document = parse(b"'\\1'", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.invalid-string-escape@1" in diagnostics_of(document)


def test_json5_reject_isolated_surrogate():
    # Case json5.reject.isolated-surrogate (json-family-v2.json:60-64).
    document = parse(b"'\\uD800'", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.invalid-string-escape@1" in diagnostics_of(document)


def test_json5_reject_unterminated_comment():
    # Case json5.reject.unterminated-comment (json-family-v2.json:66-70).
    document = parse(b"1/* open", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.unterminated-block-comment@1" in diagnostics_of(document)


def test_json_strict_reject_json5_surface():
    # Case json.strict.reject-json5-surface (json-family-v2.json:72-76).
    source = "// note\n{\"a\":1,}"
    document = parse(source.encode("utf-8"), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert document.root().kind().value is JsonValueKind.OBJECT
    codes = diagnostics_of(document)
    assert "json.strict.comment-not-allowed@1" in codes
    assert "json.strict.trailing-comma@1" in codes


def test_jsonc_complete_shared_surface():
    # Case jsonc.complete-shared-surface (json-family-v2.json:78-82).
    source = "// note\n{\"a\":1,}"
    document = parse(source.encode("utf-8"), JsonProfile.JSONC_BOUNDED_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.OBJECT


def test_json5_complete_jsonc_surface():
    # Case json5.complete-jsonc-surface (json-family-v2.json:84-88).
    source = "// note\n{\"a\":1,}"
    document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.root().kind().value is JsonValueKind.OBJECT


def test_json5_number_positive_infinity():
    # Case json5.number.positive-infinity (json-family-v2.json:90-94).
    document = parse(b"+Infinity", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    kind = document.root().kind()
    assert kind.is_available and kind.value is JsonValueKind.BINARY_FLOAT64
    bits = document.root().as_binary_float64()
    assert bits.is_available and bits.value == BITS_POSITIVE_INFINITY


def test_json5_number_negative_nan():
    # Case json5.number.negative-nan (json-family-v2.json:96-100).
    document = parse(b"-NaN", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    kind = document.root().kind()
    assert kind.is_available and kind.value is JsonValueKind.BINARY_FLOAT64
    bits = document.root().as_binary_float64()
    assert bits.is_available and bits.value == BITS_NEGATIVE_NAN


def test_json5_number_huge_hex_exact():
    # Case json5.number.huge-hex-exact (json-family-v2.json:102-106).
    document = parse(b"0xFFFFFFFFFFFFFFFFFFFFFFFF", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    kind = document.root().kind()
    assert kind.is_available and kind.value is JsonValueKind.INTEGER
    value = document.root().as_integer()
    assert value.is_available and value.value == 79228162514264337593543950335


def test_json5_number_leading_trailing_exact():
    # Case json5.number.leading-trailing-exact (json-family-v2.json:108-112).
    document = parse(b"[.5,1.,1.e2]", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    availability = document.root().array_elements()
    assert availability.is_available
    decimals = []
    for element in availability.value:
        value = element.value().as_decimal()
        assert value.is_available
        decimals.append([str(value.value.coefficient), str(value.value.exponent)])
    assert decimals == [["5", "-1"], ["1", "0"], ["1", "2"]]


def test_json5_security_depth_limit():
    # Case json5.security.depth-limit (json-family-v2.json:198-202).
    limits = ParseLimits(max_nesting_depth=2)
    with pytest.raises(JsonFormationFailure) as caught:
        parse(b"[[[[0]]]]", JsonProfile.JSON5_STANDARD_V1, limits)
    assert caught.value.kind is JsonFormationFailureKind.NESTING_DEPTH


# ---------------------------------------------------------------------------
# v1.json json formation cases
# ---------------------------------------------------------------------------


def test_parse_strict_exact_roundtrip():
    # Case parse.strict-exact-roundtrip (v1.json:41-45).
    source = " {\n  \"a\" : [1, 2]\n} "
    document = parse(source.encode("utf-8"), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.render() == source.encode("utf-8")


def test_parse_jsonc_comments_trailing_comma():
    # Case parse.jsonc-comments-trailing-comma (v1.json:47-51).
    source = "{/*x*/\"a\":1,}"
    document = parse(source.encode("utf-8"), JsonProfile.JSONC_BOUNDED_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert document.render() == source.encode("utf-8")


def test_parse_recovery_missing_close():
    # Case parse.recovery-missing-close (v1.json:53-57).
    document = parse(b'{"a":1', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Recovered"
    assert "json.syntax.missing-object-close@1" in diagnostics_of(document)


def test_parse_duplicate_members():
    # Case parse.duplicate-members (v1.json:59-63).
    document = parse(b'{"a":1,"a":2}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    assert document.formation_status().value == "Complete"
    assert member_names(document) == ["a", "a"]
    availability = document.root().object_members()
    assert availability.is_available
    first, second = availability.value
    assert first.node_ref() != second.node_ref()
    assert "json.object.duplicate-member@1" in diagnostics_of(document)


def test_parse_lossless_byte_coverage():
    # Case parse.lossless-byte-coverage (v1.json:65-69).
    source = " \n// c\n[1,] "
    document = parse(source.encode("utf-8"), JsonProfile.JSONC_BOUNDED_V1, DEFAULT_LIMITS)
    pieces = document.lossless_structural_index().pieces
    covered = sum(piece.span.len() for piece in pieces)
    assert covered == 12
    # No gap / no overlap is guaranteed by the structural-index invariant
    # (LosslessStructuralIndex.new); asserted here explicitly for the vector.
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(source.encode("utf-8"))


def test_formation_closure_render_equals_source():
    # Closure: every Complete parse renders the exact original bytes.
    jsonc_sources = [
        " {\n  \"a\" : [1, 2]\n} ",
        "{/*x*/\"a\":1,}",
        "// note\n{\"a\":1,}",
    ]
    for source in jsonc_sources:
        document = parse(source.encode("utf-8"), JsonProfile.JSONC_BOUNDED_V1, DEFAULT_LIMITS)
        assert document.formation_status().value == "Complete"
        assert document.render() == source.encode("utf-8")
    json5_sources = [
        "\ufeff{ // lead\nunquoted:'value',hex:+0X10,trail:1.,truth:true,nil:null,}",
    ]
    for source in json5_sources:
        document = parse(source.encode("utf-8"), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
        assert document.formation_status().value == "Complete"
        assert document.render() == source.encode("utf-8")
