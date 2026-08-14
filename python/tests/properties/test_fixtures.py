"""Java Properties fixture round-trip gate.

The production-shaped fixtures under conformance/fixtures/properties (the
single-authority tree provisioned from the consema spec repository in CI)
must close byte-exactly under their profile: parse -> render == source
bytes, complete formation, and a reparse of the rendered bytes must be
byte-stable. The adversarial fixtures (fixtures/properties/README.md) pin
the transport facts:

- ``utf16-edge.properties`` — a supplementary scalar and legal unpaired
  Java UTF-16 code units through ``\\uXXXX`` escapes; the unpaired units
  are valid native content (RFC 0010 §4) and block ordinary String
  projection.
- ``latin1-resource.properties.hex`` — non-UTF-8 Latin-1 resource bytes
  under the Latin-1 profile.

Facts (property counts, scalar-projectable) mirror https://github.com/consema/consema-rs/blob/main/consema-
conformance/tests/line_format_fixtures.rs:115-179. When the shared tree
is not reachable the tests FAIL (G68 guard, same as tests/toml/conftest.py)
— a partially provisioned checkout must not go green. Fixtures are read-only; tests never modify them.
"""

from __future__ import annotations

import pathlib

import pytest

from consema.document.source import SourceEncoding
from consema.properties import (
    PropertiesParseLimits,
    parse_latin1,
    parse_reader,
)

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "conformance" / "fixtures" / "properties"

DEFAULT_LIMITS = PropertiesParseLimits()

PROPERTIES_FIXTURES = {
    "utf16-edge.properties": (3, False),
    "latin1-resource.properties.hex": (3, True),
}


def _fixture_bytes(name: str) -> bytes:
    """The canonical byte container: ``.hex`` files are lowercase-hex text
    (fixtures/properties/README.md); other fixtures are raw bytes."""
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
    if name.endswith(".hex"):
        document = parse_latin1(raw, DEFAULT_LIMITS)
    else:
        document = parse_reader(raw, SourceEncoding.utf8(), DEFAULT_LIMITS)
    return document, raw


def test_properties_fixtures_round_trip_byte_exact():
    # parse -> render must reproduce the source bytes exactly (including
    # the Latin-1 bytes), with complete formation and no diagnostics.
    for name, (count, _scalar_projectable) in PROPERTIES_FIXTURES.items():
        document, raw = _form(name)
        assert document.formation_status().value == "Complete", name
        assert not document.diagnostics, name
        assert document.render() == raw, name
        assert len(document.properties) == count, name


def test_properties_fixtures_reparse_is_byte_stable():
    # The rendered bytes must reparse to an identical document: the
    # round-trip is stable, never normalized.
    for name in PROPERTIES_FIXTURES:
        document, raw = _form(name)
        rendered = document.render()
        assert rendered == raw, name
        reparsed, _ = _form(name)
        assert reparsed.render() == raw, name
        assert reparsed.formation_status().value == "Complete", name


def test_utf16_edge_fixture_keeps_exact_java_units():
    # The supplementary scalar decodes to U+1F680 and the unpaired units
    # stay unpaired (RFC 0010 §4): native content, never U+FFFD
    # (line_format_fixtures.rs:341-352).
    document, _raw = _form("utf16-edge.properties")
    by_key = {p.key.to_unicode(): p for p in document.properties}
    assert list(by_key) == ["rocket", "unpaired.high", "unpaired.low"]
    assert by_key["rocket"].value.to_unicode() == "\U0001F680"
    assert by_key["unpaired.high"].value.status().value == "UnpairedSurrogate"
    assert by_key["unpaired.low"].value.status().value == "UnpairedSurrogate"


def test_latin1_resource_fixture_decodes_latin1():
    # The Latin-1 bytes decode through the profile, not as accidental
    # UTF-8 (line_format_fixtures.rs:373-379).
    document, raw = _form("latin1-resource.properties.hex")
    assert raw.count(b"\xe9") >= 1  # "caf\xe9"
    assert raw.count(b"\xa3") >= 1  # "£"
    assert raw.count(b"\xef") >= 1  # "na\xefve"
    by_key = {p.key.to_unicode(): p.value.to_unicode() for p in document.properties}
    assert by_key == {"title": "café", "currency": "£", "author": "naïve"}
