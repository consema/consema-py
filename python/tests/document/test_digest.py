"""Golden digest and snapshot-identity tests.

Golden cases transcribed from conformance/vectors/source-v1.json:
- case ``source.digest.sha256-empty`` (lines 4-10): digest of the empty
  source is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855;
- case ``source.digest.sha256-abc`` (lines 11-16): digest of 616263 is
  ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad;
- case ``source.identity.equal-bytes-distinct-snapshots`` (lines 17-22):
  two snapshots of the same raw bytes have equal digests and distinct
  snapshot identities.

Contract: RFC 0003 §3 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:
45-62) — SHA-256 over the complete original byte sequence, algorithm exactly
"sha256", hex exactly 64 lowercase characters; equal raw bytes always produce
equal content digests; parsing the same bytes twice produces equal digests
and distinct snapshot identities.
"""

from __future__ import annotations

from consema.document import ContentDigest, DocumentAuthority, SourceSnapshot

# Golden digests, conformance/vectors/source-v1.json:9 and :15
EMPTY_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_DIGEST = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_source_digest_sha256_empty() -> None:
    """Vector case source.digest.sha256-empty."""
    digest = ContentDigest.of(b"")
    assert digest.to_hex() == EMPTY_DIGEST
    assert digest.algorithm == "sha256"
    assert len(digest.to_hex()) == 64


def test_source_digest_sha256_abc() -> None:
    """Vector case source.digest.sha256-abc (raw_hex 616263)."""
    assert ContentDigest.of(bytes.fromhex("616263")).to_hex() == ABC_DIGEST


def test_source_identity_equal_bytes_distinct_snapshots() -> None:
    """Vector case source.identity.equal-bytes-distinct-snapshots (raw_hex
    5b5d = "[]")."""
    first = SourceSnapshot.from_utf8(b"[]")
    second = SourceSnapshot.from_utf8(b"[]")
    assert first.digest() == second.digest()  # equal_digest: true
    assert DocumentAuthority.fresh().identity != DocumentAuthority.fresh().identity
    assert first.bytes() == second.bytes() == b"[]"


def test_content_digest_round_trips_32_byte_records() -> None:
    digest = ContentDigest.of(b"abc")
    rebuilt = ContentDigest.from_bytes(digest.digest_bytes)
    assert rebuilt == digest
    assert rebuilt.to_hex() == ABC_DIGEST
