"""Formation golden transcriptions (conformance/vectors/yaml-v1.json cases).

Cases covered with the vector case ids cited:

- profile.yaml12-scalars (yaml-v1.json:5-9): plain scalar resolution under
  yaml.1.2-core@1 — kinds [String, Integer, Integer, String, String] and
  canonical spellings ["yes", "17", "15", "1:02:03", "2001-12-15"].
- profile.yaml11-scalars (yaml-v1.json:10-14): the same source under
  yaml.1.1-compat@1 with the %YAML 1.1 directive — kinds [Boolean,
  Integer, String, Integer, Timestamp] and canonical spellings
  ["true", "15", "0o17", "3723", "2001-12-15"].
- source.utf16le-bom (yaml-v1.json:15-18): UTF-16LE with BOM selects the
  Utf16Le encoding and forms one document.
- stream.empty (yaml-v1.json:19-22): document_count 0, alias_count 0.
- stream.multi-document (yaml-v1.json:23-28): two documents, one alias.
- syntax.styles-and-trivia (yaml-v1.json:29-33): 48 lossless pieces with
  every required kind.
- native.arbitrary-duplicate-mapping (yaml-v1.json:35-38): three ordered
  entries, duplicate keys preserved, sequence keys.
- formation.undefined-alias (yaml-v1.json:39-43): yaml.parse.syntax@1.
- resource.parse-source-bytes (yaml-v1.json:124-128): the fatal
  core.parse.resource-limit@1 code.
- regression.plain-property-characters (yaml-v1.json:134-138): a plain
  scalar keeps ``k:#foo &a !t s`` and defines no anchors.

Security: RFC 0007 s13 (lines 400-429) — no evaluation, no alias
expansion; the alias-bomb test pins that a large repeated alias stream
forms without exponential work (one edge per alias) and that the
document-scoped anchor limits hold.
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.document.source import SourceEncodingKind
from consema.yaml import (
    YamlFormationFailure,
    YamlFormationFailureKind,
    YamlNodeKind,
    YamlProfile,
    YamlScalarKind,
    YamlScalarStyle,
    YamlSyntaxKind,
    parse,
)
from consema.yaml.errors import resource_limit_failure
from tests.yaml.conftest import parse_source


def _scalar_facts(document, ordinal: int, item: int):
    entry = document.document(0).root().mapping_entry(ordinal)
    scalar = entry.value().scalar()
    return scalar


def test_profile_yaml12_scalars():
    # Case profile.yaml12-scalars (yaml-v1.json:5-9).
    document = parse_source("[yes, 017, 0o17, 1:02:03, 2001-12-15]", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.sequence_len() == 5
    kinds = []
    canonical = []
    for ordinal in range(5):
        scalar = root.sequence_item(ordinal).node().scalar()
        kinds.append(scalar.kind().value)
        canonical.append(scalar.canonical())
    assert kinds == ["String", "Integer", "Integer", "String", "String"]
    assert canonical == ["yes", "17", "15", "1:02:03", "2001-12-15"]


def test_profile_yaml11_scalars():
    # Case profile.yaml11-scalars (yaml-v1.json:10-14).
    source = "%YAML 1.1\n---\n[yes, 017, 0o17, 1:02:03, 2001-12-15]\n"
    document = parse_source(source, YamlProfile.YAML11_COMPAT_V1)
    assert document.document_count() == 1
    root = document.document(0).root()
    kinds = []
    canonical = []
    for ordinal in range(5):
        scalar = root.sequence_item(ordinal).node().scalar()
        kinds.append(scalar.kind().value)
        canonical.append(scalar.canonical())
    assert kinds == ["Boolean", "Integer", "String", "Integer", "Timestamp"]
    assert canonical == ["true", "15", "0o17", "3723", "2001-12-15"]


def test_source_utf16le_bom():
    # Case source.utf16le-bom (yaml-v1.json:15-18); the raw hex is
    # fffe 6100 3a00 2000 3100 0a00 ("a: 1\\n" in UTF-16LE with BOM).
    raw = bytes.fromhex("fffe61003a00200031000a00")
    document = parse(raw, YamlProfile.YAML12_CORE_V1, ParseLimits())
    assert document.source.encoding_facts().selected.kind is SourceEncodingKind.UTF16LE
    assert document.document_count() == 1
    # The BOM is retained in the source and classified as the first piece.
    assert document.render() == raw
    assert document.lossless_syntax_kinds()[0] is YamlSyntaxKind.BOM
    # The document still parses to the mapping {a: 1}.
    entry = document.document(0).root().mapping_entry(0)
    assert entry.key().scalar().canonical() == "a"
    assert entry.value().scalar().canonical() == "1"


def test_stream_empty():
    # Case stream.empty (yaml-v1.json:19-22).
    document = parse_source("", YamlProfile.YAML12_CORE_V1)
    assert document.document_count() == 0
    assert document.alias_count() == 0


def test_stream_multi_document():
    # Case stream.multi-document (yaml-v1.json:23-28).
    source = "---\n&a [one, *a]\n---\n{k: v}\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    assert document.document_count() == 2
    assert document.alias_count() == 1
    first = document.document(0)
    assert first.root().anchor() == "a"
    assert first.root().sequence_len() == 2
    alias = document.alias(0)
    assert alias.name() == "a"
    assert alias.target().node_ref() == first.root().node_ref()
    second = document.document(1)
    assert second.root().mapping_len() == 1


def test_syntax_styles_and_trivia():
    # Case syntax.styles-and-trivia (yaml-v1.json:29-33): 48 pieces and the
    # closed required-kind set.
    source = (
        "--- # doc\n"
        "plain: text\n"
        "single: 'x'\n"
        "double: \"y\"\n"
        "literal: |-\n"
        "  a\n"
        "folded: >+\n"
        "  b\n"
        "flow: [one, {k: v}]\n"
        "...\n"
    )
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    pieces = document.lossless_structural_index().pieces
    assert len(pieces) == 48
    kinds = document.lossless_syntax_kinds()
    required = [
        YamlSyntaxKind.DOCUMENT_START,
        YamlSyntaxKind.COMMENT,
        YamlSyntaxKind.PLAIN_SCALAR,
        YamlSyntaxKind.SINGLE_QUOTED_SCALAR,
        YamlSyntaxKind.DOUBLE_QUOTED_SCALAR,
        YamlSyntaxKind.LITERAL_BLOCK_HEADER,
        YamlSyntaxKind.FOLDED_BLOCK_HEADER,
        YamlSyntaxKind.BLOCK_SCALAR_CONTENT,
        YamlSyntaxKind.FLOW_SEQUENCE_START,
        YamlSyntaxKind.FLOW_MAPPING_START,
        YamlSyntaxKind.DOCUMENT_END,
    ]
    for kind in required:
        assert kind in kinds
    # Block scalar styles decode exactly (lib.rs:1235-1261).
    root = document.document(0).root()
    literal = root.mapping_entry(3).value().scalar()
    assert literal.decoded() == "a"
    assert literal.style() is YamlScalarStyle.LITERAL
    folded = root.mapping_entry(4).value().scalar()
    # The folded scalar decodes with its trailing newline: ``>+`` keeps the
    # final line break (decoded "b\n", not "b") — the Rust, Go, TS, and
    # saphyr authorities agree; see test_block_scalar_keywords_are_strings
    # for the same convention on ``>``.
    assert folded.decoded() == "b\n"
    assert folded.style() is YamlScalarStyle.FOLDED


def test_native_arbitrary_duplicate_mapping():
    # Case native.arbitrary-duplicate-mapping (yaml-v1.json:35-38).
    source = "? [a, b]\n: one\nk: two\nk: three\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.mapping_len() == 3
    key_kinds = []
    values = []
    for ordinal in range(3):
        entry = root.mapping_entry(ordinal)
        key_kinds.append(entry.key().kind().value)
        values.append(entry.value().scalar().decoded())
    assert key_kinds == ["Sequence", "Scalar", "Scalar"]
    assert values == ["one", "two", "three"]


def test_formation_undefined_alias():
    # Case formation.undefined-alias (yaml-v1.json:39-43): the undefined
    # alias fails at parse time with yaml.parse.syntax@1.
    with pytest.raises(YamlFormationFailure) as caught:
        parse_source("[*missing]\n", YamlProfile.YAML12_CORE_V1)
    assert caught.value.code == "yaml.parse.syntax@1"


def test_resource_parse_source_bytes():
    # Case resource.parse-source-bytes (yaml-v1.json:124-128): exceeding
    # max_source_bytes is fatal with core.parse.resource-limit@1.
    with pytest.raises(YamlFormationFailure) as caught:
        parse(
            b"a: 1\n",
            YamlProfile.YAML12_CORE_V1,
            ParseLimits(max_source_bytes=4),
        )
    assert caught.value.code == "core.parse.resource-limit@1"
    assert caught.value.kind is YamlFormationFailureKind.SOURCE_BYTES


def test_regression_plain_property_characters():
    # Case regression.plain-property-characters (yaml-v1.json:134-138):
    # ``&a`` and ``!t`` inside a more-indented plain continuation are scalar
    # text, never node properties (syntax.rs plain continuation rule).
    source = "---\nk:#foo\n &a !t s\n"
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    scalar = document.document(0).root().scalar()
    assert scalar.decoded() == "k:#foo &a !t s"
    assert document.alias_count() == 0
    assert YamlSyntaxKind.ANCHOR not in document.lossless_syntax_kinds()
    assert YamlSyntaxKind.TAG not in document.lossless_syntax_kinds()


def test_alias_bomb_forms_one_edge_per_alias():
    # RFC 0007 s8 (lines 194-213): "an alias is one edge regardless of
    # target size; parse and graph formation never perform recursive alias
    # expansion". An alias bomb must form linearly: each alias occurrence
    # is exactly one graph edge, never an expansion of the target.
    # Anchors are document-scoped (RFC 0007 s8: documents are independent
    # and cannot share anchors), so the copies live in the same document as
    # the definition.
    source = "bomb: &bomb {}\n" + "copy: *bomb\n" * 2000
    document = parse_source(source, YamlProfile.YAML12_CORE_V1)
    assert document.alias_count() == 2000
    # The native model holds the root, the "bomb" key, one anchored node,
    # and the 2000 distinct "copy" key scalars; each alias is one edge,
    # never an expansion of the target.
    assert len(document.native.nodes) == 1 + 1 + 1 + 2000
    anchored = document.document(0).root().mapping_entry(0).value()
    for ordinal in range(2000):
        alias = document.alias(ordinal)
        assert alias.name() == "bomb"
        assert alias.target().node_ref() == anchored.node_ref()


def test_nesting_depth_limit_is_fatal():
    # lib.rs:949-968: ``[[x]]`` with max_nesting_depth 1 fails with the
    # resource-limit code (no truncation-then-success, RFC 0016 s6).
    with pytest.raises(YamlFormationFailure) as caught:
        parse(
            b"[[x]]",
            YamlProfile.YAML12_CORE_V1,
            ParseLimits(max_nesting_depth=1),
        )
    assert caught.value.code == "core.parse.resource-limit@1"
    assert caught.value.name == "nesting-depth"


def test_profile_version_directive_conflict():
    # lib.rs:895-905: a %YAML directive that conflicts with the selected
    # profile is fatal before parsing.
    with pytest.raises(YamlFormationFailure) as caught:
        parse_source("%YAML 1.1\n---\nyes\n", YamlProfile.YAML12_CORE_V1)
    assert caught.value.code == "yaml.profile.version-directive@1"

    document = parse_source("%YAML 1.1\n---\nyes\n", YamlProfile.YAML11_COMPAT_V1)
    assert document.document_count() == 1


def test_quoted_keywords_are_exact_strings():
    # lib.rs:1049-1083: quoted scalars always resolve as strings with their
    # exact decoded content, never null/bool/int/float.
    for keyword in ("~", "null", "true", "0o17", "2001-12-15"):
        for quote in ('"', "'"):
            document = parse_source(f"{quote}{keyword}{quote}\n", YamlProfile.YAML12_CORE_V1)
            scalar = document.document(0).root().scalar()
            assert scalar.kind() is YamlScalarKind.STRING
            assert scalar.decoded() == keyword


def test_plain_null_spellings():
    # native.rs:746-748: the empty scalar, ``~``, and the three null cases
    # resolve to the null tag with empty canonical content.
    for spelling in ("~", "null", "Null", "NULL", ""):
        source = f"a: {spelling}\n" if spelling else "a:\n"
        document = parse_source(source, YamlProfile.YAML12_CORE_V1)
        scalar = document.document(0).root().mapping_entry(0).value().scalar()
        assert scalar.kind() is YamlScalarKind.NULL
        assert scalar.canonical() == ""


def test_explicit_standard_tag_validation():
    # native.rs:1375-1398: ``!!int nope`` fails scalar grammar validation
    # (yaml.scalar.invalid-explicit-tag@1) and ``!!seq {a: b}`` fails kind
    # validation (yaml.tag.kind-mismatch@1).
    with pytest.raises(YamlFormationFailure) as caught:
        parse_source("!!int nope\n", YamlProfile.YAML12_CORE_V1)
    assert caught.value.code == "yaml.scalar.invalid-explicit-tag@1"

    with pytest.raises(YamlFormationFailure) as caught:
        parse_source("!!seq {a: b}\n", YamlProfile.YAML12_CORE_V1)
    assert caught.value.code == "yaml.tag.kind-mismatch@1"


def test_block_scalar_keywords_are_strings():
    # lib.rs:1233-1261: block-style scalars carrying keyword text are
    # strings with their exact decoded content.
    document = parse_source("a: |\n  ~\nb: >\n  null\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    tilde = root.mapping_entry(0).value().scalar()
    assert tilde.kind() is YamlScalarKind.STRING
    assert tilde.decoded() == "~\n"
    null_text = root.mapping_entry(1).value().scalar()
    assert null_text.kind() is YamlScalarKind.STRING
    assert null_text.decoded() == "null\n"
