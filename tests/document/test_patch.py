"""SourcePatch construction, application, and failure-code tests.

Golden case transcribed from conformance/vectors/source-v1.json:
- ``source.patch.success`` (lines 119-124): base 6e616d65203d206f6c640a
  ("name = old\\n"), replacements [(0,0,"","2320"), (7,10,"6f6c64","6e6577")]
  -> target 23206e616d65203d206e65770a ("# name = new\\n");
- rejection cases: ``reject-stale-base`` (125-130) ->
  core.source.patch-base-mismatch@1; ``reject-original-mismatch`` (131-136)
  -> core.source.patch-original-mismatch@1; ``reject-overlap`` (137-142) ->
  core.protocol.invalid-value@1; ``reject-target-mismatch`` (143-148) ->
  core.source.patch-target-mismatch@1; ``reject-encoding-change`` (149-154)
  -> core.source.encoding-conflict@1; ``source.resource.patch-count-limit``
  (167-172) -> core.source.resource-limit@1.

Contract: RFC 0003 §10 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:
250-291); arbitration crates/consema-document/src/source_patch.rs:143-280,
469-554; codes crates/consema-protocol/src/error_registry.rs:381,387,393,399,
366,87.
"""

from __future__ import annotations

import pytest

from consema.document import (
    ChangeSet,
    ContentDigest,
    DocumentAuthority,
    EncodingRequest,
    SourceEdit,
    SourceEncoding,
    SourceLimits,
    SourcePatch,
    SourcePatchError,
    SourcePatchErrorKind,
    SourcePatchLimits,
    SourceReplacement,
    SourceSnapshot,
)


def _utf8(raw: bytes) -> SourceSnapshot:
    return SourceSnapshot.from_utf8(raw)


def test_patch_success_golden() -> None:
    """Vector case source.patch.success — create, apply, round-trip."""
    base = _utf8(bytes.fromhex("6e616d65203d206f6c640a"))
    replacements = [
        SourceReplacement.new(0, 0, b"", bytes.fromhex("2320")),
        SourceReplacement.new(7, 10, bytes.fromhex("6f6c64"), bytes.fromhex("6e6577")),
    ]
    patch = SourcePatch.create(base, replacements, {"actor": "test"}, SourcePatchLimits())
    first = patch.apply(base, SourcePatchLimits())
    second = patch.apply(base, SourcePatchLimits())
    assert first.bytes() == bytes.fromhex("23206e616d65203d206e65770a")
    assert first.digest() == patch.target_digest
    assert first == second  # deterministic round-trip
    assert patch.metadata["actor"] == "test"


def test_reject_stale_base() -> None:
    """Vector case source.patch.reject-stale-base: base 616263, stale 616264."""
    base = _utf8(b"abc")
    patch = SourcePatch.create(
        base,
        [SourceReplacement.new(1, 2, b"b", b"B")],
        {},
        SourcePatchLimits(),
    )
    with pytest.raises(SourcePatchError) as caught:
        patch.apply(_utf8(b"abd"), SourcePatchLimits())
    assert caught.value.kind is SourcePatchErrorKind.BASE_MISMATCH
    assert caught.value.code == "core.source.patch-base-mismatch@1"


def test_reject_original_mismatch() -> None:
    """Vector case source.patch.reject-original-mismatch: declared original
    "78" (x) but base range holds "62" (b)."""
    base = _utf8(b"abc")
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.create(
            base,
            [SourceReplacement.new(1, 2, b"x", b"B")],
            {},
            SourcePatchLimits(),
        )
    assert caught.value.kind is SourcePatchErrorKind.ORIGINAL_MISMATCH
    assert caught.value.code == "core.source.patch-original-mismatch@1"


def test_reject_overlap() -> None:
    """Vector case source.patch.reject-overlap: [(1,4,"626364",""),
    (3,5,"6465","")] -> core.protocol.invalid-value@1."""
    base = _utf8(b"abcdef")
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.create(
            base,
            [
                SourceReplacement.new(1, 4, b"bcd", b""),
                SourceReplacement.new(3, 5, b"de", b""),
            ],
            {},
            SourcePatchLimits(),
        )
    assert caught.value.kind is SourcePatchErrorKind.REPLACEMENT_ORDER
    assert caught.value.code == "core.protocol.invalid-value@1"


def test_reject_target_mismatch() -> None:
    """Vector case source.patch.reject-target-mismatch: patch carries a
    forged target digest."""
    base = _utf8(b"ab")
    patch = SourcePatch.new(
        base.digest(),
        ContentDigest.of(b"not cd"),
        base.encoding_facts(),
        [SourceReplacement.new(0, 2, b"ab", b"cd")],
        {},
        SourcePatchLimits(),
    )
    with pytest.raises(SourcePatchError) as caught:
        patch.apply(base, SourcePatchLimits())
    assert caught.value.kind is SourcePatchErrorKind.TARGET_MISMATCH
    assert caught.value.code == "core.source.patch-target-mismatch@1"


def test_reject_encoding_change() -> None:
    """Vector case source.patch.reject-encoding-change: latin-1 base replaced
    with bytes that resolve to a different encoding -> encoding-conflict@1."""
    base = SourceSnapshot.from_raw(
        b"ab",
        EncodingRequest.new(SourceEncoding.latin1()),
        SourceLimits(),
    )
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.create(
            base,
            [SourceReplacement.new(0, 2, b"ab", bytes.fromhex("fffe4100"))],
            {},
            SourcePatchLimits(),
        )
    assert caught.value.kind is SourcePatchErrorKind.ENCODING_MISMATCH
    assert caught.value.code == "core.source.encoding-conflict@1"


def test_reject_patch_count_limit() -> None:
    """Vector case source.resource.patch-count-limit: max_replacements 0."""
    base = _utf8(b"a")
    limits = SourcePatchLimits(max_replacements=0)
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.create(
            base,
            [SourceReplacement.new(1, 1, b"", b"b")],
            {},
            limits,
        )
    assert caught.value.kind is SourcePatchErrorKind.RESOURCE_LIMIT
    assert caught.value.code == "core.source.resource-limit@1"


def test_duplicate_insertion_is_rejected() -> None:
    """Two zero-width replacements at one insertion point (RFC 0003 §10)."""
    base = _utf8(b"abc")
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.create(
            base,
            [
                SourceReplacement.new(2, 2, b"", b"x"),
                SourceReplacement.new(2, 2, b"", b"y"),
            ],
            {},
            SourcePatchLimits(),
        )
    assert caught.value.kind is SourcePatchErrorKind.DUPLICATE_INSERTION
    assert caught.value.code == "core.protocol.invalid-value@1"


def test_derive_from_change_set_reapplies_exactly() -> None:
    """SourcePatch::derive (source_patch.rs:145-205; RFC 0004 §16): the
    derived patch reapplies to the old snapshot and reproduces the exact new
    bytes and digest."""
    base = _utf8(b"abc")
    target = _utf8(b"aXYc")
    old_authority = DocumentAuthority.fresh()
    new_authority = DocumentAuthority.fresh()
    change_set = ChangeSet(
        old_snapshot=old_authority.identity,
        new_snapshot=new_authority.identity,
        source_edits=(
            SourceEdit(
                old_span=old_authority.span(1, 2),
                new_span=new_authority.span(1, 3),
                replacement=b"XY",
            ),
        ),
    )
    patch = SourcePatch.derive(base, target, change_set, {}, SourcePatchLimits())
    assert patch.apply(base, SourcePatchLimits()).bytes() == target.bytes()

    inconsistent = ChangeSet(
        old_snapshot=old_authority.identity,
        new_snapshot=new_authority.identity,
        source_edits=(
            SourceEdit(
                old_span=old_authority.span(1, 2),
                new_span=new_authority.span(1, 3),
                replacement=b"ZZ",
            ),
        ),
    )
    with pytest.raises(SourcePatchError) as caught:
        SourcePatch.derive(base, target, inconsistent, {}, SourcePatchLimits())
    assert caught.value.kind is SourcePatchErrorKind.CHANGE_SET_MISMATCH
    assert caught.value.code == "core.protocol.invalid-value@1"


def test_redaction_hides_payloads_from_repr_only() -> None:
    """Redaction controls review/debug presentation, not application
    (source_patch.rs:792-802; RFC 0003 §10)."""
    replacement = SourceReplacement.new(0, 6, b"secret", b"hidden").with_original_redacted(
        True
    ).with_replacement_redacted(True)
    assert replacement.original == b"secret"
    assert replacement.replacement == b"hidden"
    rendered = repr(replacement)
    assert "secret" not in rendered
    assert "hidden" not in rendered
    assert "<redacted>" in rendered
