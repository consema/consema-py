"""INI fixture round-trip gate.

The production-shaped fixtures under conformance/fixtures/ini (the
single-authority tree provisioned from the consema spec repository in CI)
must close byte-exactly under their profile: parse -> render == source
bytes, complete formation, and a reparse of the rendered bytes must be
byte-stable. The adversarial fixtures (fixtures/ini/README.md) pin the
transport facts:

- ``windows-cp1252.ini.hex`` — explicit Windows-1252 bytes (``é``/``€``)
  under the Windows profile with an explicit code page; no encoding guess
  is allowed.
- ``legacy-mixed-newline.ini.hex`` — deliberately mixed LF/CRLF
  terminators under the portable profile.

Facts (sections/entries) mirror https://github.com/consema/consema-rs/blob/main/consema-conformance/tests/
line_format_fixtures.rs:48-99. When the shared tree is not reachable the
tests FAIL (G68 guard, same as tests/toml/conftest.py) — a partially
provisioned checkout must not go green. Fixtures are read-only; tests
never modify them.
"""

from __future__ import annotations

import pathlib

import pytest

from consema.document.source import SourceEncoding, WindowsCodePage
from consema.ini import (
    IniEncodingSelection,
    IniParseLimits,
    IniProfile,
    parse,
)

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "conformance" / "fixtures" / "ini"

INI_FIXTURES = {
    "windows-cp1252.ini.hex": (IniProfile.WINDOWS_V1, True, 1, 3),
    "legacy-mixed-newline.ini.hex": (IniProfile.PORTABLE_V1, False, 2, 3),
}


def _fixture_bytes(name: str) -> bytes:
    """The canonical byte container: ``.hex`` files are lowercase-hex text
    (fixtures/ini/README.md); other fixtures are raw bytes."""
    path = _FIXTURES / name
    # G68 guard: a missing fixture FAILS the gate instead of skipping
    # silently (partially provisioned checkouts must not go green).
    if not path.exists():
        raise FileNotFoundError(f"shared fixture not available: {name}")
    raw = path.read_bytes()
    if name.endswith(".hex"):
        return bytes.fromhex(raw.decode("ascii"))
    return raw


def _form(name: str):
    """Parses one fixture under its profile with default limits."""
    raw = _fixture_bytes(name)
    profile, explicit_cp1252, _sections, _entries = INI_FIXTURES[name]
    if explicit_cp1252:
        code_page = WindowsCodePage.from_number(1252)
        selection = IniEncodingSelection.explicit(SourceEncoding.windows_code_page(code_page))
    else:
        selection = IniEncodingSelection.profile_default()
    return parse(raw, profile, selection, IniParseLimits()), raw


def test_ini_fixtures_round_trip_byte_exact():
    # parse -> render must reproduce the source bytes exactly (including
    # the non-UTF-8 Windows-1252 bytes and the mixed newlines), with
    # complete formation and the pinned section/entry counts.
    for name, (_profile, _explicit, sections, entries) in INI_FIXTURES.items():
        document, raw = _form(name)
        assert document.formation_status().value == "Complete", name
        assert not document.diagnostics, name
        assert document.render() == raw, name
        assert len(document.sections) == sections, name
        assert len(document.entries) == entries, name


def test_ini_fixtures_reparse_is_byte_stable():
    # The rendered bytes must reparse to an identical document: the
    # round-trip is stable, never normalized.
    for name in INI_FIXTURES:
        document, raw = _form(name)
        rendered = document.render()
        assert rendered == raw, name
        reparsed, _ = _form(name)
        assert reparsed.render() == raw, name
        assert reparsed.formation_status().value == "Complete", name


def test_windows_cp1252_fixture_keeps_declared_transport_facts():
    # The CP1252 bytes are not accidentally UTF-8, and the explicit code
    # page decodes the accents (line_format_fixtures.rs:267-277).
    document, raw = _form("windows-cp1252.ini.hex")
    assert raw.count(b"\xe9") >= 1  # "Montr\xe9al"
    assert raw.count(b"\x80") >= 1  # "€"
    assert document.source.encoding_facts().selected.kind.value == "windows-code-page"
    values = [entry.value for entry in document.entries]
    assert values[0] == "Montréal"
    assert values[1] == "€"
