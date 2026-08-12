"""Decoded-location boundary tests (Span-adjacent coordinate conversion).

Golden cases transcribed from conformance/vectors/source-v1.json:
- ``source.location.utf8-boundaries`` (lines 83-88): raw 41f09f988042
  (A U+1F600 B), raw_byte 5 -> decoded_utf8_byte 5, unicode_scalar_offset 2,
  utf16_code_unit_offset 3; invalid_raw_byte 2 (inside the scalar) is
  rejected; invalid_utf16_offset 2 (inside the surrogate pair) is rejected.
- ``source.location.utf16-boundaries`` (lines 89-94): raw 41003dd800de4200
  under utf-16le, raw_byte 6 -> (5, 2, 3); invalid_raw_byte 3 rejected;
  invalid_utf16_offset 2 rejected.
- ``source.location.binary-no-text`` (lines 95-100): binary sources raise
  the NoDecodedText location error.

Contract: RFC 0003 §5 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:
124-141) — only scalar boundaries are addressable; a raw offset inside a
UTF-8 scalar or between a UTF-16 surrogate pair is rejected rather than
rounded; Binary snapshots have no decoded boundaries. Location variant
spellings per crates/consema-document/src/lib.rs:582-604.
"""

from __future__ import annotations

import pytest

from consema.document import (
    DecodedOffset,
    DecodedPosition,
    EncodingRequest,
    LocationError,
    LocationErrorKind,
    SourceEncoding,
    SourceLimits,
    SourceSnapshot,
)


def _snapshot(raw_hex: str, encoding: SourceEncoding) -> SourceSnapshot:
    return SourceSnapshot.from_raw(
        bytes.fromhex(raw_hex), EncodingRequest.new(encoding), SourceLimits()
    )


def test_utf8_boundaries() -> None:
    """Vector case source.location.utf8-boundaries."""
    snapshot = _snapshot("41f09f988042", SourceEncoding.utf8())
    assert snapshot.decoded_position(5) == DecodedPosition(
        raw_byte=5, decoded_utf8_byte=5, unicode_scalar_offset=2, utf16_code_unit_offset=3
    )
    with pytest.raises(LocationError) as caught:
        snapshot.decoded_position(2)  # inside U+1F600
    assert caught.value.kind is LocationErrorKind.NOT_DECODED_BOUNDARY
    assert caught.value.name == "NotDecodedBoundary"
    with pytest.raises(LocationError) as caught:
        snapshot.raw_byte_at(DecodedOffset.utf16_code_unit(2))  # inside pair
    assert caught.value.kind is LocationErrorKind.DECODED_OFFSET_NOT_BOUNDARY
    assert snapshot.raw_byte_at(DecodedOffset.unicode_scalar(2)) == 5


def test_utf16_boundaries() -> None:
    """Vector case source.location.utf16-boundaries."""
    snapshot = _snapshot("41003dd800de4200", SourceEncoding.utf16le())
    assert snapshot.decoded_text() == "A\U0001f600B"
    assert snapshot.decoded_position(6) == DecodedPosition(
        raw_byte=6, decoded_utf8_byte=5, unicode_scalar_offset=2, utf16_code_unit_offset=3
    )
    with pytest.raises(LocationError) as caught:
        snapshot.decoded_position(3)  # between the surrogate pair
    assert caught.value.kind is LocationErrorKind.NOT_DECODED_BOUNDARY
    with pytest.raises(LocationError) as caught:
        snapshot.raw_byte_at(DecodedOffset.utf16_code_unit(2))
    assert caught.value.kind is LocationErrorKind.DECODED_OFFSET_NOT_BOUNDARY
    assert snapshot.raw_byte_at(DecodedOffset.utf16_code_unit(3)) == 6


def test_binary_no_text() -> None:
    """Vector case source.location.binary-no-text: binary sources have no
    decoded coordinates; expected code "NoDecodedText"."""
    snapshot = _snapshot("00ff", SourceEncoding.binary())
    with pytest.raises(LocationError) as caught:
        snapshot.decoded_position(0)
    assert caught.value.kind is LocationErrorKind.NO_DECODED_TEXT
    assert caught.value.name == "NoDecodedText"
    with pytest.raises(LocationError) as caught:
        snapshot.raw_byte_at(DecodedOffset.unicode_scalar(0))
    assert caught.value.kind is LocationErrorKind.NO_DECODED_TEXT


def test_terminal_boundary_resolves() -> None:
    """The terminal raw offset is the valid half-open end of the source
    (source.rs:624-626; go/document/source.go:322-323): a piece ending
    exactly at the source end addresses the terminal DecodedPosition, and
    only offsets beyond the source are out of bounds."""
    snapshot = _snapshot("41f09f988042", SourceEncoding.utf8())
    assert snapshot.decoded_position(6) == DecodedPosition(
        raw_byte=6, decoded_utf8_byte=6, unicode_scalar_offset=3, utf16_code_unit_offset=4
    )


def test_out_of_bounds_is_rejected() -> None:
    snapshot = _snapshot("41f09f988042", SourceEncoding.utf8())
    with pytest.raises(LocationError) as caught:
        snapshot.decoded_position(7)  # beyond the source end
    assert caught.value.kind is LocationErrorKind.OUT_OF_BOUNDS
    with pytest.raises(LocationError) as caught:
        snapshot.raw_byte_at(DecodedOffset.unicode_scalar(4))
    assert caught.value.kind is LocationErrorKind.OUT_OF_BOUNDS
