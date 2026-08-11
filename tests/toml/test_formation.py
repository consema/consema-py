"""Formation intent documents for the toml family.

Golden transcriptions of the toml-v1.json formation/native/resource cases
(conformance/vectors/toml-v1.json), plus formation closure (parse -> render
-> reparse identity) and byte-exact source coverage.

Cases transcribed (with their vector ids):
- toml.parse.exact-roundtrip (all-values.toml)
- toml.parse.lossless-byte-coverage (trivia-and-strings.toml)
- toml.native.dotted-segments
- toml.native.table-flavors / array-aot-distinct (application.toml)
- toml.native.float-signed-zero
- toml.parse.reject-invalid (invalid-duplicate.toml)
- toml.resource.token-limit / node-depth-limits
- toml.corpus.cargo-manifest / pyproject
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.document.structural import FormationStatus, StructuralPieceKind
from consema.toml import TomlFormationFailure, TomlItemKind, TomlProfile, parse
from consema.toml.syntax import TomlSyntaxKind


def _parse(source: bytes, limits: ParseLimits | None = None):
    return parse(source, TomlProfile.TOML10_V1, limits or ParseLimits())


def test_vector_parse_exact_roundtrip(fixture_bytes):
    """toml.parse.exact-roundtrip: all-values.toml forms Complete and
    renders byte-for-byte equal to the input."""
    source = fixture_bytes("toml/all-values.toml")
    document = _parse(source)
    assert document.formation_status() is FormationStatus.COMPLETE
    assert document.render() == source


def test_vector_lossless_byte_coverage(fixture_bytes):
    """toml.parse.lossless-byte-coverage: trivia-and-strings.toml has
    gap-free, overlap-free, exhaustive token/trivia coverage."""
    source = fixture_bytes("toml/trivia-and-strings.toml")
    document = _parse(source)
    pieces = document.lossless_structural_index().pieces
    assert len(pieces) > 0
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(source)
    for first, second in zip(pieces, pieces[1:]):
        assert first.span.end_byte == second.span.start_byte
    # every piece carries a TOML syntax kind in the same order
    kinds = document.lossless_syntax_kinds()
    assert len(kinds) == len(pieces)
    assert all(isinstance(kind, TomlSyntaxKind) for kind in kinds)
    # the fixture exercises comments, multiline strings, and arrays
    assert any(kind is TomlSyntaxKind.COMMENT for kind in kinds)
    assert any(kind is TomlSyntaxKind.STRING for kind in kinds)
    assert any(kind is TomlSyntaxKind.LEFT_BRACKET for kind in kinds)


def test_formation_closure_is_render_reparse_identity():
    """Formation closure: rendering a formed document reparses to an equal
    snapshot (same bytes, same native facts, Complete again)."""
    source = b"title = \"TOML\"\nhex = 0x2A\nfloat = -0.0\nwhen = 1979-05-27T07:32:00Z\n"
    first = _parse(source)
    second = _parse(first.render())
    assert second.render() == source
    assert second.formation_status() is FormationStatus.COMPLETE
    assert second.root().kind() is TomlItemKind.ROOT_TABLE
    entries = second.root().table_entries()
    assert [entry.name() for entry in entries] == ["title", "hex", "float", "when"]


def test_native_dotted_segments():
    """toml.native.dotted-segments: alpha.beta.gamma = 1 keeps each logical
    key segment as a separate entry layer."""
    document = _parse(b"alpha.beta.gamma = 1\n")
    alpha = document.root().table_entries()[0]
    assert alpha.name() == "alpha"
    assert alpha.item().kind() is TomlItemKind.DOTTED_TABLE
    beta = alpha.item().table_entries()[0]
    assert beta.name() == "beta"
    gamma = beta.item().table_entries()[0]
    assert gamma.name() == "gamma"
    assert gamma.item().kind() is TomlItemKind.INTEGER
    assert gamma.item().as_integer() == 1


def test_native_table_flavors(fixture_bytes):
    """toml.native.table-flavors: service is a DottedTable, database a
    StandardTable, observability an ImplicitTable."""
    document = _parse(fixture_bytes("toml/application.toml"))
    flavors = {
        entry.name(): entry.item().kind()
        for entry in document.root().table_entries()
    }
    assert flavors["service"] is TomlItemKind.DOTTED_TABLE
    assert flavors["database"] is TomlItemKind.STANDARD_TABLE
    assert flavors["observability"] is TomlItemKind.IMPLICIT_TABLE


def test_native_array_aot_distinct(fixture_bytes):
    """toml.native.array-aot-distinct: database.timeouts is an Array,
    upstreams an ArrayOfTables with two elements."""
    document = _parse(fixture_bytes("toml/application.toml"))
    entries = {entry.name(): entry.item() for entry in document.root().table_entries()}
    database = entries["database"]
    database_entries = {entry.name(): entry.item() for entry in database.table_entries()}
    assert database_entries["timeouts"].kind() is TomlItemKind.ARRAY
    assert database_entries["timeouts"].array_elements()[2].item().as_float_bits() is not None
    upstreams = entries["upstreams"]
    assert upstreams.kind() is TomlItemKind.ARRAY_OF_TABLES
    assert len(upstreams.array_elements()) == 2
    assert upstreams.array_elements()[0].item().kind() is TomlItemKind.STANDARD_TABLE
    assert upstreams.array_elements()[1].item().kind() is TomlItemKind.STANDARD_TABLE


def test_native_float_signed_zero():
    """toml.native.float-signed-zero: 0.0 and -0.0 keep distinct IEEE-754
    binary64 bit patterns."""
    document = _parse(b"positive = 0.0\nnegative = -0.0\n")
    entries = {entry.name(): entry.item() for entry in document.root().table_entries()}
    assert entries["positive"].as_float_bits() == 0x0000000000000000
    assert entries["negative"].as_float_bits() == 0x8000000000000000


def test_parse_reject_invalid(fixture_bytes):
    """toml.parse.reject-invalid: duplicate keys are a FatalFormationFailure
    carrying toml.parse.syntax@1."""
    with pytest.raises(TomlFormationFailure) as caught:
        _parse(fixture_bytes("toml/invalid-duplicate.toml"))
    assert caught.value.code == "toml.parse.syntax@1"
    assert caught.value.diagnostics[0].arguments["parser_reason"]


def test_parse_reject_syntax_variants():
    """Syntax failures never form documents (RFC 0001 §3)."""
    for source in (
        b"value = [1,,2]\n",
        b"a = 1\na = 2\n",
        b"value = 'unterminated\n",
        b"a = 01\n",
        b"[a]\n[a]\n",
        b"a = 1 b = 2\n",
    ):
        with pytest.raises(TomlFormationFailure) as caught:
            _parse(source)
        assert caught.value.code == "toml.parse.syntax@1"


def test_resource_token_limit():
    """toml.resource.token-limit: max_token_count=3 fails fatally with
    core.parse.resource-limit@1 and no truncated success."""
    with pytest.raises(TomlFormationFailure) as caught:
        _parse(b"values = [1, 2, 3]", ParseLimits(max_token_count=3))
    assert caught.value.code == "core.parse.resource-limit@1"
    assert caught.value.diagnostics[0].arguments["name"] == "token_count"


def test_resource_node_depth_limits():
    """toml.resource.node-depth-limits: max_node_count=3 and
    max_nesting_depth=2 both fail fatally."""
    limits = ParseLimits(max_node_count=3, max_nesting_depth=2)
    with pytest.raises(TomlFormationFailure) as caught:
        _parse(b"value = [[[[1]]]]", limits)
    assert caught.value.code == "core.parse.resource-limit@1"


def test_corpus_cargo_manifest(fixture_bytes):
    """toml.corpus.cargo-manifest: the shared Cargo.toml forms Complete,
    renders byte-exact, and projects (see projection tests)."""
    source = fixture_bytes("Cargo.toml")
    document = _parse(source)
    assert document.formation_status() is FormationStatus.COMPLETE
    assert document.render() == source


def test_corpus_pyproject(fixture_bytes):
    """toml.corpus.pyproject: the PEP 621 fixture forms Complete and
    renders byte-exact."""
    source = fixture_bytes("toml/pyproject.toml")
    document = _parse(source)
    assert document.formation_status() is FormationStatus.COMPLETE
    assert document.render() == source


def test_native_inline_table_entries():
    """Inline tables are native items with their own entries, not JSON
    objects (RFC 0001 §1; IMPLEMENTATION.md:102)."""
    document = _parse(b"point = { x = 1, y = 2 }\n")
    point = document.root().table_entries()[0].item()
    assert point.kind() is TomlItemKind.INLINE_TABLE
    entries = point.table_entries()
    assert [entry.name() for entry in entries] == ["x", "y"]
    assert entries[1].item().as_integer() == 2


def test_snapshot_identity_is_fresh_per_parse():
    """Parsing the same bytes twice produces distinct snapshot identities
    with equal content digests."""
    first = _parse(b"x = 1\n")
    second = _parse(b"x = 1\n")
    assert first.snapshot_identity() != second.snapshot_identity()
    assert first.source().digest() == second.source().digest()


def test_structural_index_piece_kinds():
    """Trivia pieces cover whitespace/newlines/comments; token pieces cover
    the rest (parser.rs:370-412)."""
    document = _parse(b"a = 1 # note\nb = 2\n")
    kinds = [piece.kind for piece in document.lossless_structural_index().pieces]
    assert StructuralPieceKind.TRIVIA in kinds
    assert StructuralPieceKind.TOKEN in kinds
