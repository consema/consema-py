"""Formation golden transcriptions and profile dialect coverage (INI).

Golden cases transcribed verbatim from conformance/vectors/ini-v1.json
(suite "consema.ini.conformance@1"); each test cites the vector case id.
Assertions check the language-neutral facts the vectors pin (formation
status, physical/logical line counts, section/entry names and values,
value states, comparison names, duplicate groups, diagnostics, exact raw
coverage, fatal limits).

Cases covered here:

- ini-v1.json: formation.portable-lossless (lines 6-8),
  formation.profile-counterexample-matrix (11-17),
  formation.windows-utf16-case-and-quote (20-22),
  formation.windows-explicit-code-page (25-27),
  formation.python-default-continuation-raw (30-32),
  formation.python-unicode16-optionxform (35-37),
  formation.recovery-never-fabricates-entry (40-42),
  resource.formation-limit-matrix (108-128).

Profile dialect coverage pins the RFC 0009 §5/§6/§7 dialect facts: the
portable subset (ASCII only, ``=`` only, ``;`` comments, no quotes, no
continuation), the Windows surface (first ``=`` splits, ``;`` comments,
outer quotes, ASCII case equivalence, ordered duplicate groups), and the
Python surface (``=``/``:`` delimiters, ``#``/``;`` comments, DEFAULT
section role, indentation continuation, empty lines in values, pinned
Unicode 16.0 optionxform).
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding
from consema.ini import (
    IniEncodingSelection,
    IniFormationFailure,
    IniParseLimits,
    IniProfile,
    IniValueState,
    parse,
)

DEFAULT_LIMITS = IniParseLimits()


def diagnostics_of(document) -> list[str]:
    return [diagnostic.code for diagnostic in document.diagnostics]


def syntax_kind_names(document) -> list[str]:
    return [kind.value for kind in document.lossless_syntax_kinds()]


def assert_exact_coverage(document, raw: bytes) -> None:
    pieces = document.lossless_structural_index().pieces
    assert pieces[0].span.start_byte == 0
    assert pieces[-1].span.end_byte == len(raw)
    for left, right in zip(pieces, pieces[1:]):
        assert left.span.end_byte == right.span.start_byte


# ---------------------------------------------------------------------------
# formation.portable-lossless (ini-v1.json:6-8)
# ---------------------------------------------------------------------------


def test_portable_lossless_formation():
    # Case formation.portable-lossless (ini-v1.json:6-8).
    source = "; heading\r\n[core]\r\nname=value\nempty="
    document = parse(
        source.encode("utf-8"),
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.render() == source.encode("utf-8")
    assert len(document.physical_lines) == 4
    assert len(document.logical_lines) == 3
    assert [section.name for section in document.sections] == ["core"]
    assert [entry.key for entry in document.entries] == ["name", "empty"]
    assert [entry.value for entry in document.entries] == ["value", ""]
    assert [entry.state for entry in document.entries] == [
        IniValueState.PRESENT,
        IniValueState.EMPTY,
    ]
    assert_exact_coverage(document, source.encode("utf-8"))


# ---------------------------------------------------------------------------
# formation.profile-counterexample-matrix (ini-v1.json:11-17)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (IniProfile.PORTABLE_V1, ["Complete", "Recovered", "Recovered"]),
        (IniProfile.WINDOWS_V1, ["Complete", "Recovered", "Fatal"]),
        (IniProfile.PYTHON_CONFIGPARSER_V1, ["Complete", "Complete", "Complete"]),
    ],
)
def test_profile_counterexample_matrix(profile, expected):
    # Case formation.profile-counterexample-matrix (ini-v1.json:11-17).
    samples = [b"[s]\nkey=value\n", b"[s]\nkey:value\n", b"[s]\nkey=\xc3\xa9\n"]
    for sample, want in zip(samples, expected):
        try:
            document = parse(
                sample, profile, IniEncodingSelection.profile_default(), DEFAULT_LIMITS
            )
            got = document.formation_status().value
        except IniFormationFailure:
            got = "Fatal"
        assert got == want, (profile, sample, got, want)


def test_portable_dialect_rejects_non_ascii_and_colon():
    # RFC 0009 §5: portable is the ASCII exchange subset; `=` is the only
    # delimiter; `#` is not a portable comment; quotes are not portable
    # value characters.
    for source in (
        b"[s]\nkey:value\n",  # colon is not a portable delimiter
        b"[s]\nkey='quoted'\n",  # quotes are not portable value characters
        b"# heading\n[s]\nk=1\n",  # `#` is not a portable comment
        b"[s]\nkey=\xc3\xa9\n",  # non-ASCII is not portable
    ):
        document = parse(
            source, IniProfile.PORTABLE_V1, IniEncodingSelection.profile_default(), DEFAULT_LIMITS
        )
        assert document.formation_status().value == "Recovered"


def test_portable_comment_marker_and_empty_value():
    # RFC 0009 §5: `;` as the first non-tab/non-space character is a
    # portable comment; `key=` has an Empty value while a bare `key` is a
    # malformed Missing-value line.
    document = parse(
        b"; comment\n[s]\na=\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.entries[0].state is IniValueState.EMPTY
    names = syntax_kind_names(document)
    assert "CommentMarker" in names
    assert "CommentText" in names

    recovered = parse(
        b"[s]\nbare\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert recovered.formation_status().value == "Recovered"
    assert len(recovered.entries) == 0
    assert recovered.error_lines[0].code == "ini.parse.missing-delimiter@1"


def test_portable_section_header_requires_line_end():
    # RFC 0009 §5 grammar: section = section-header line-end ...; a header
    # at EOF without a line ending is malformed (parser.rs:353-357).
    document = parse(
        b"[s]",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Recovered"
    assert "ini.parse.malformed-section@1" in diagnostics_of(document)


def test_windows_dialect_trivia_and_quotes():
    # RFC 0009 §6: leading/trailing horizontal whitespace outside a key is
    # trivia; an exactly single- or double-quoted value has a semantic
    # content span without the outer marks; quotes inside an otherwise
    # unquoted value are ordinary content. An unquoted value retains its
    # decoded scalar content exactly (RFC 0009 §6, lines 202-203).
    document = parse(
        b"[s]\r\n  key =  value  \r\nplain=\"q\"text\r\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.entries[0].key == "key"
    assert document.entries[0].value == "  value  "
    assert document.entries[1].value == '"q"text'
    assert document.entries[1].quote_style.value == "None"
    assert_exact_coverage(document, b"[s]\r\n  key =  value  \r\nplain=\"q\"text\r\n")


def test_windows_ascii_case_equivalence_is_ordered_and_ambiguous():
    # RFC 0009 §6: section and key comparison uses ASCII case-insensitive
    # equivalence while preserving original case; repeated/case-equivalent
    # records are an ambiguity set, never implicitly resolved.
    document = parse(
        b"[Main]\r\nName=one\r\nname=two\r\n[main]\r\nOther=three\r\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert [section.comparison_name for section in document.sections] == ["main", "main"]
    assert document.sections[0].duplicate_group == document.sections[1].duplicate_group
    assert document.entries[0].duplicate_group == document.entries[1].duplicate_group
    assert "ini.formation.case-collision@1" in diagnostics_of(document)
    assert "ini.formation.duplicate-section@1" not in diagnostics_of(document)


def test_windows_global_entries_are_invalid():
    # RFC 0009 §6: global entries are invalid.
    document = parse(
        b"k=1\n[s]\na=2\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Recovered"
    assert "ini.parse.missing-section@1" in diagnostics_of(document)


def test_python_dialect_delimiters_comments_and_default():
    # RFC 0009 §7: `=` and `:` are delimiters; `#` and `;` prefix
    # otherwise empty/comment lines after indentation; the exact section
    # name DEFAULT has the DefaultSection role but is not merged.
    document = parse(
        b"  # indented comment\n[DEFAULT]\nBase: one\n[s]\nother=2\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.sections[0].is_default is True
    assert document.sections[0].node.role.value == "IniDefaultSection"
    assert [entry.key for entry in document.entries] == ["Base", "other"]
    assert document.entries[0].value == "one"
    names = syntax_kind_names(document)
    assert "CommentMarker" in names
    assert "Whitespace" in names


def test_python_option_bare_line_and_comment_content():
    # RFC 0009 §7: allow_no_value=False makes a bare option invalid;
    # inline comment prefixes are disabled, so `#`/`;` in a value are
    # stored content.
    bare = parse(
        b"[s]\nbare\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert bare.formation_status().value == "Recovered"
    assert "ini.parse.missing-delimiter@1" in diagnostics_of(bare)

    literal = parse(
        b"[s]\nvalue = #hash ;semi\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert literal.formation_status().value == "Complete"
    assert literal.entries[0].value == "#hash ;semi"


def test_python_invalid_continuation_recovers():
    # RFC 0009 §7: an option line may continue on following more-indented
    # physical lines; a more-indented line without an active entry is an
    # invalid continuation.
    document = parse(
        b"[s]\n  indented\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Recovered"
    assert "ini.parse.invalid-continuation@1" in diagnostics_of(document)


# ---------------------------------------------------------------------------
# formation.windows-utf16-case-and-quote (ini-v1.json:20-22)
# ---------------------------------------------------------------------------


def test_windows_utf16_case_and_quote():
    # Case formation.windows-utf16-case-and-quote (ini-v1.json:20-22).
    raw = bytes.fromhex(
        "fffe5b004d00610069006e005d000d000a0020004e0061006d00650020003d0022002000760061006c0075006500200022000d000a005b006d00610069006e005d000d000a004e0041004d0045003d00740077006f00"
    )
    document = parse(
        raw,
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.source.encoding_facts().selected == SourceEncoding.utf16le()
    assert [section.name for section in document.sections] == ["Main", "main"]
    assert [section.comparison_name for section in document.sections] == ["main", "main"]
    assert [entry.key for entry in document.entries] == ["Name", "NAME"]
    assert [entry.comparison_key for entry in document.entries] == ["name", "name"]
    assert [entry.value for entry in document.entries] == [" value ", "two"]
    assert [entry.quote_style.value for entry in document.entries] == ["Double", "None"]
    assert "ini.formation.case-collision@1" in diagnostics_of(document)
    assert document.entries[0].duplicate_group == document.entries[1].duplicate_group
    assert_exact_coverage(document, raw)


# ---------------------------------------------------------------------------
# formation.windows-explicit-code-page (ini-v1.json:25-27)
# ---------------------------------------------------------------------------


def test_windows_explicit_code_page():
    # Case formation.windows-explicit-code-page (ini-v1.json:25-27).
    from consema.document.source import WindowsCodePage

    code_page = WindowsCodePage.from_number(1252)
    document = parse(
        bytes.fromhex("5b735d0d0a6b3d80"),
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.explicit(SourceEncoding.windows_code_page(code_page)),
        DEFAULT_LIMITS,
    )
    assert document.entries[0].value == "€"
    assert document.source.encoding_facts().selected.kind.value == "windows-code-page"
    assert document.source.encoding_facts().bom_policy.value == "TreatAsContent"
    assert_exact_coverage(document, bytes.fromhex("5b735d0d0a6b3d80"))


# ---------------------------------------------------------------------------
# formation.python-default-continuation-raw (ini-v1.json:30-32)
# ---------------------------------------------------------------------------


def test_python_default_continuation_raw():
    # Case formation.python-default-continuation-raw (ini-v1.json:30-32).
    source = "[DEFAULT]\nRoot = raw%(x)s\n[Sec]\nKey: first\n    second\n\n    third\nOther = #literal ;literal"
    document = parse(
        source.encode("utf-8"),
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert document.sections[0].is_default is True
    assert [entry.comparison_key for entry in document.entries] == ["root", "key", "other"]
    assert [entry.value for entry in document.entries] == [
        "raw%(x)s",
        "first\nsecond\n\nthird",
        "#literal ;literal",
    ]
    logical = document.resolve_logical_line(document.entries[1].logical_line)
    assert len(logical.physical_nodes) == 4
    assert_exact_coverage(document, source.encode("utf-8"))


# ---------------------------------------------------------------------------
# formation.python-unicode16-optionxform (ini-v1.json:35-37)
# ---------------------------------------------------------------------------


def test_python_unicode16_optionxform():
    # Case formation.python-unicode16-optionxform (ini-v1.json:35-37).
    source = "[S]\nİ=1\ni̇=2\n"
    document = parse(
        source.encode("utf-8"),
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Recovered"
    assert [entry.comparison_key for entry in document.entries] == ["i̇", "i̇"]
    assert document.entries[0].duplicate_group == document.entries[1].duplicate_group
    assert "ini.formation.case-collision@1" in diagnostics_of(document)


def test_python_optionxform_frozen_unicode_16_examples():
    # Pinned optionxform examples (python_case.rs:239-244).
    from consema.ini.python_case import optionxform

    assert optionxform("Key") == "key"
    assert optionxform("İ") == "i̇"
    assert optionxform("Kẞ") == "kß"
    assert optionxform("\U00010400") == "\U00010428"


# ---------------------------------------------------------------------------
# formation.recovery-never-fabricates-entry (ini-v1.json:40-42)
# ---------------------------------------------------------------------------


def test_recovery_never_fabricates_entry():
    # Case formation.recovery-never-fabricates-entry (ini-v1.json:40-42).
    document = parse(
        b"[s]\nbare\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Recovered"
    assert len(document.entries) == 0
    assert len(document.error_lines) == 1
    assert document.error_lines[0].code == "ini.parse.missing-delimiter@1"
    assert "ini.parse.missing-delimiter@1" in diagnostics_of(document)
    names = syntax_kind_names(document)
    assert "ErrorRegion" in names


def test_portable_empty_and_comment_only_sources_are_recovered():
    # RFC 0009 §5 grammar: document = blank-or-comment* section+ — an
    # empty or comment-only portable source is Recovered with
    # ini.parse.missing-section@1 and no error record.
    for source in (b"", b"; only\n", b"  \n"):
        document = parse(
            source, IniProfile.PORTABLE_V1, IniEncodingSelection.profile_default(), DEFAULT_LIMITS
        )
        assert document.formation_status().value == "Recovered"
        assert "ini.parse.missing-section@1" in diagnostics_of(document)
        assert len(document.error_lines) == 0


def test_windows_encoding_gates():
    # RFC 0009 §3.2: Windows ProfileDefault accepts UTF-16LE with BOM or
    # ASCII-only no-BOM UTF-8; non-ASCII no-BOM bytes are a profile error.
    with pytest.raises(IniFormationFailure) as caught:
        parse(
            b"[s]\nk=\xc3\xa9",
            IniProfile.WINDOWS_V1,
            IniEncodingSelection.profile_default(),
            DEFAULT_LIMITS,
        )
    assert caught.value.code == "ini.profile.encoding@1"

    document = parse(
        b"[s]\nk=1\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"


def test_portable_rejects_utf8_bom():
    # RFC 0009 §3.1: portable accepts UTF-8 without a BOM; a BOM is a
    # profile error (parser.rs:68-69).
    with pytest.raises(IniFormationFailure) as caught:
        parse(
            b"\xef\xbb\xbf[s]\n",
            IniProfile.PORTABLE_V1,
            IniEncodingSelection.profile_default(),
            DEFAULT_LIMITS,
        )
    assert caught.value.code == "ini.profile.encoding@1"


def test_python_profile_accepts_bom_selected_encoding():
    # RFC 0009 §3.3: any complete text source, provided the caller or a
    # BOM selected the encoding unambiguously; the BOM stays an observable
    # Bom syntax piece.
    document = parse(
        b"\xef\xbb\xbf[s]\nk=1\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    assert document.formation_status().value == "Complete"
    assert "Bom" in syntax_kind_names(document)
    assert document.source.encoding_facts().bom.value == "Utf8"


# ---------------------------------------------------------------------------
# resource.formation-limit-matrix (ini-v1.json:108-128)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "profile", "source"),
    [
        (IniParseLimits(common=ParseLimits(max_source_bytes=4)), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(common=ParseLimits(max_token_count=1)), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(common=ParseLimits(max_node_count=1)), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(common=ParseLimits(max_diagnostics=0)), IniProfile.PORTABLE_V1, b"[s]\nbare\nbad\n"),
        (IniParseLimits(max_decoded_utf8_bytes=1), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(max_decoded_scalars=1), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(max_physical_lines=1), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(max_physical_line_bytes=3), IniProfile.PORTABLE_V1, b"[section]\nkey=value\n"),
        (IniParseLimits(max_physical_line_scalars=3), IniProfile.PORTABLE_V1, b"[section]\nkey=value\n"),
        (IniParseLimits(max_logical_lines=1), IniProfile.PORTABLE_V1, b"[s]\nk=1\n"),
        (IniParseLimits(max_logical_line_bytes=8), IniProfile.PYTHON_CONFIGPARSER_V1, b"[s]\nk=one\n  two\n"),
        (IniParseLimits(max_logical_line_scalars=4), IniProfile.PYTHON_CONFIGPARSER_V1, b"[s]\nk=one\n  two\n"),
        (IniParseLimits(max_continuation_lines=0), IniProfile.PYTHON_CONFIGPARSER_V1, b"[s]\nk=one\n  two\n"),
        (IniParseLimits(max_sections=1), IniProfile.PORTABLE_V1, b"[a]\nx=1\n[b]\ny=2\n"),
        (IniParseLimits(max_entries=1), IniProfile.PORTABLE_V1, b"[s]\na=1\nb=2\n"),
        (IniParseLimits(max_duplicate_group_members=1), IniProfile.WINDOWS_V1, b"[s]\r\na=1\r\nA=2\r\n"),
        (IniParseLimits(max_recovery_regions=1), IniProfile.PORTABLE_V1, b"[s]\nbare\nbad\n"),
    ],
)
def test_formation_limit_matrix_is_fatal(limits, profile, source):
    # Case resource.formation-limit-matrix (ini-v1.json:108-128): all 17
    # configured limits fail fatally; no partial document exists. The
    # vector pins only the fatal outcomes and the no-partial-documents
    # rule, not the code: source-level budgets surface through the source
    # layer (core.source.resource-limit@1), the rest through
    # core.parse.resource-limit@1.
    with pytest.raises(IniFormationFailure) as caught:
        parse(source, profile, IniEncodingSelection.profile_default(), limits)
    assert caught.value.code in (
        "core.parse.resource-limit@1",
        "core.source.resource-limit@1",
    )
