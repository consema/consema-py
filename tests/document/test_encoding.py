"""Encoding resolution, decoding, and error-code tests.

Golden cases transcribed from conformance/vectors/source-v1.json:
- round-trips: ``source.encoding.utf8-roundtrip`` (lines 23-28),
  ``utf16le-roundtrip`` (29-34), ``utf16be-roundtrip`` (35-40),
  ``latin1-roundtrip`` (41-46), ``binary-roundtrip`` (47-52);
- conflicts: ``source.encoding.bom-declaration-conflict`` (53-58),
  ``declaration-caller-conflict`` (59-64) -> core.source.encoding-conflict@1;
- ``source.encoding.reject-utf32-bom`` (65-70) ->
  core.source.unsupported-bom@1;
- ``source.encoding.reject-utf16-odd`` (71-76) and
  ``reject-utf16-surrogate`` (77-82) -> core.source.invalid-sequence@1.

Contract: RFC 0003 §4 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:
64-122). Code registry: crates/consema-protocol/src/error_registry.rs
(encoding-conflict@1:366, invalid-sequence@1:372, unsupported-bom@1:405).
"""

from __future__ import annotations

import pytest

from consema.document import (
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
)


def _snapshot(raw_hex: str, encoding: SourceEncoding) -> SourceSnapshot:
    return SourceSnapshot.from_raw(
        bytes.fromhex(raw_hex), EncodingRequest.new(encoding), SourceLimits()
    )


def _decoded_utf8_hex(snapshot: SourceSnapshot) -> str:
    text = snapshot.decoded_text()
    assert text is not None
    return text.encode("utf-8").hex()


def test_utf8_roundtrip() -> None:
    """Vector case source.encoding.utf8-roundtrip: raw efbbbf41f09f9880,
    selected utf-8, decoded utf-8 bytes equal the raw bytes (BOM retained as
    U+FEFF, RFC 0003 §4.3)."""
    snapshot = _snapshot("efbbbf41f09f9880", SourceEncoding.utf8())
    assert snapshot.bytes() == bytes.fromhex("efbbbf41f09f9880")
    assert snapshot.encoding_facts().selected == SourceEncoding.utf8()
    assert _decoded_utf8_hex(snapshot) == "efbbbf41f09f9880"
    assert snapshot.encoding_facts().bom is not None


def test_utf16le_roundtrip() -> None:
    """Vector case source.encoding.utf16le-roundtrip: raw fffe41003dd800de,
    selected utf-16le, decoded utf-8 efbbbf41f09f9880."""
    snapshot = _snapshot("fffe41003dd800de", SourceEncoding.utf16le())
    assert snapshot.bytes() == bytes.fromhex("fffe41003dd800de")
    assert snapshot.encoding_facts().selected == SourceEncoding.utf16le()
    assert _decoded_utf8_hex(snapshot) == "efbbbf41f09f9880"


def test_utf16be_roundtrip() -> None:
    """Vector case source.encoding.utf16be-roundtrip: raw feff0041d83dde00,
    selected utf-16be, decoded utf-8 efbbbf41f09f9880."""
    snapshot = _snapshot("feff0041d83dde00", SourceEncoding.utf16be())
    assert snapshot.encoding_facts().selected == SourceEncoding.utf16be()
    assert _decoded_utf8_hex(snapshot) == "efbbbf41f09f9880"


def test_latin1_roundtrip() -> None:
    """Vector case source.encoding.latin1-roundtrip: raw 41e9ff, selected
    latin-1, decoded utf-8 41c3a9c3bf (ISO-8859-1, not Windows-1252)."""
    snapshot = _snapshot("41e9ff", SourceEncoding.latin1())
    assert snapshot.encoding_facts().selected == SourceEncoding.latin1()
    assert _decoded_utf8_hex(snapshot) == "41c3a9c3bf"


def test_binary_roundtrip() -> None:
    """Vector case source.encoding.binary-roundtrip: raw fffe0000 retained
    exactly, no decoded text (decoded_utf8_hex null)."""
    snapshot = _snapshot("fffe0000", SourceEncoding.binary())
    assert snapshot.bytes() == bytes.fromhex("fffe0000")
    assert snapshot.encoding_facts().selected == SourceEncoding.binary()
    assert snapshot.decoded_text() is None


def test_bom_declaration_conflict() -> None:
    """Vector case source.encoding.bom-declaration-conflict: raw efbbbf41
    with utf-8 profile and declaration utf-16le."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_raw(
            bytes.fromhex("efbbbf41"),
            EncodingRequest.new(SourceEncoding.utf8()).with_declaration(
                SourceEncoding.utf16le()
            ),
            SourceLimits(),
        )
    assert caught.value.kind is SourceErrorKind.ENCODING_CONFLICT
    assert caught.value.code == "core.source.encoding-conflict@1"


def test_declaration_caller_conflict() -> None:
    """Vector case source.encoding.declaration-caller-conflict: declaration
    utf-8 with caller_override latin-1."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_raw(
            b"\x41",
            EncodingRequest.new(SourceEncoding.utf8())
            .with_declaration(SourceEncoding.utf8())
            .with_caller_override(SourceEncoding.latin1()),
            SourceLimits(),
        )
    assert caught.value.code == "core.source.encoding-conflict@1"


def test_reject_utf32_bom() -> None:
    """Vector case source.encoding.reject-utf32-bom: raw fffe0000 with utf-8
    profile -> core.source.unsupported-bom@1."""
    with pytest.raises(SourceError) as caught:
        _snapshot("fffe0000", SourceEncoding.utf8())
    assert caught.value.kind is SourceErrorKind.UNSUPPORTED_BOM
    assert caught.value.code == "core.source.unsupported-bom@1"


def test_reject_utf16_odd() -> None:
    """Vector case source.encoding.reject-utf16-odd: raw 4100ff (3 bytes)."""
    with pytest.raises(SourceError) as caught:
        _snapshot("4100ff", SourceEncoding.utf16le())
    assert caught.value.kind is SourceErrorKind.INVALID_SEQUENCE
    assert caught.value.code == "core.source.invalid-sequence@1"
    assert caught.value.byte_offset == 2


def test_reject_utf16_surrogate() -> None:
    """Vector case source.encoding.reject-utf16-surrogate: raw 3dd84100 —
    high surrogate followed by a non-low code unit."""
    with pytest.raises(SourceError) as caught:
        _snapshot("3dd84100", SourceEncoding.utf16le())
    assert caught.value.kind is SourceErrorKind.INVALID_SEQUENCE
    assert caught.value.code == "core.source.invalid-sequence@1"


def test_from_utf8_compat_surfaces_invalid_utf8_code() -> None:
    """from_utf8 maps invalid sequences to the invalid-utf8 code
    (error_registry.rs:207; source.rs:553-568)."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_utf8(b"\x80")
    assert caught.value.kind is SourceErrorKind.INVALID_UTF8
    assert caught.value.code == "core.source.invalid-utf8@1"
    assert caught.value.valid_up_to == 0


def test_from_binary_rejects_text_overrides() -> None:
    """RFC 0003 §4.2: declaration or caller text encodings are invalid for a
    binary profile."""
    with pytest.raises(SourceError) as caught:
        SourceSnapshot.from_raw(
            b"text",
            EncodingRequest.binary().with_caller_override(SourceEncoding.utf8()),
            SourceLimits(),
        )
    assert caught.value.kind is SourceErrorKind.ENCODING_CONFLICT
