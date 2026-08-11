"""UntouchedByteProof tests.

Intended behavior mirrors the Rust arbitration tests
(crates/consema-document/src/untouched_proof.rs:319-402) against the
contract of RFC 0004 §15 (docs/rfcs/0004-materialization-conversion-and-
structural-edit-v1.md:358-371): the proof is an ordered cover of all
old-source intervals outside replacements mapped to target intervals;
verification rechecks digests, original-byte preconditions, exact target
bytes, and every region fact; regions are canonical maximal intervals.
"""

from __future__ import annotations

import pytest

from consema.document import (
    ContentDigest,
    SourceSnapshot,
    SourceReplacement,
    UntouchedByteProof,
    UntouchedByteProofError,
    UntouchedByteProofErrorKind,
    UntouchedByteRegion,
)


def _utf8(raw: bytes) -> SourceSnapshot:
    return SourceSnapshot.from_utf8(raw)


def _replacements() -> list[SourceReplacement]:
    return [
        SourceReplacement.new(0, 0, b"", b">"),
        SourceReplacement.new(2, 4, b"XX", b"YYY"),
        SourceReplacement.new(6, 7, b"!", b""),
    ]


def test_proof_covers_every_and_only_untouched_byte() -> None:
    base = _utf8(b"abXXcd!")
    target = _utf8(b">abYYYcd")
    proof = UntouchedByteProof.create(base, target, _replacements())
    assert proof.regions == (
        UntouchedByteRegion.new(0, 2, 1, 3),
        UntouchedByteRegion.new(4, 6, 6, 8),
    )
    proof.verify(base, target, _replacements())


def test_proof_detects_region_digest_and_target_tampering() -> None:
    base = _utf8(b"abXXcd!")
    target = _utf8(b">abYYYcd")
    replacements = _replacements()
    proof = UntouchedByteProof.from_facts(
        base.digest(),
        target.digest(),
        [
            UntouchedByteRegion.new(0, 2, 0, 2),
            UntouchedByteRegion.new(4, 6, 6, 8),
        ],
    )
    with pytest.raises(UntouchedByteProofError) as caught:
        proof.verify(base, target, replacements)
    assert caught.value.kind is UntouchedByteProofErrorKind.PROOF_MISMATCH

    with pytest.raises(UntouchedByteProofError) as caught:
        proof.verify(base, _utf8(b">abYYYcD"), replacements)
    assert caught.value.kind is UntouchedByteProofErrorKind.DIGEST_MISMATCH

    with pytest.raises(UntouchedByteProofError) as caught:
        UntouchedByteProof.create(base, _utf8(b">aBYYYcd"), replacements)
    assert caught.value.kind is UntouchedByteProofErrorKind.TARGET_MISMATCH


def test_no_replacements_prove_the_complete_snapshot() -> None:
    source = _utf8(b"same")
    proof = UntouchedByteProof.create(source, source, [])
    assert proof.regions == (UntouchedByteRegion.new(0, 4, 0, 4),)
    proof.verify(source, source, [])


def test_transferred_proof_rejects_noncanonical_regions() -> None:
    """Adjacent regions must have been merged; from_facts rejects
    non-canonical covers (untouched_proof.rs:388-401)."""
    digest = ContentDigest.of(b"abc")
    with pytest.raises(UntouchedByteProofError) as caught:
        UntouchedByteProof.from_facts(
            digest,
            digest,
            [
                UntouchedByteRegion.new(0, 1, 0, 1),
                UntouchedByteRegion.new(1, 3, 1, 3),
            ],
        )
    assert caught.value.kind is UntouchedByteProofErrorKind.INVALID_REGION
    assert caught.value.index == 1
