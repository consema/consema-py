"""Verifiable proof that planned replacements did not alter surrounding bytes.

Authority:
- RFC 0004 §15 (docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md:358-371): every successful edit commit includes UntouchedByteProof
  — an ordered cover of all old-source intervals outside replacements, mapped
  to target intervals; verification requires old regions exactly cover every
  non-replaced old byte once, new regions exactly cover every non-inserted
  new byte once, each mapped region has equal length and equal bytes, region
  order is monotonic, and base and target digests match the proof. The proof
  says only that bytes outside planned replacements are identical.
- crates/consema-document/src/untouched_proof.rs — arbitration:
  UntouchedByteRegion untouched_proof.rs:8-59; UntouchedByteProof::create /
  from_facts / verify untouched_proof.rs:71-132; canonical region computation
  (maximal, adjacent regions merged) untouched_proof.rs:182-295; region
  validation untouched_proof.rs:297-317.

go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.ids import ContentDigest
from consema.document.source import SourceSnapshot
from consema.document.source_patch import SourceReplacement


@dataclass(frozen=True, slots=True)
class UntouchedByteRegion:
    """One maximal unchanged raw-byte interval mapped across two source
    snapshots (untouched_proof.rs:8-59)."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int

    @classmethod
    def new(
        cls, old_start: int, old_end: int, new_start: int, new_end: int
    ) -> UntouchedByteRegion:
        return cls(old_start=old_start, old_end=old_end, new_start=new_start, new_end=new_end)


class UntouchedByteProofErrorKind(enum.Enum):
    """Proof construction or verification failure (untouched_proof.rs:135-172)."""

    ENCODING_MISMATCH = "encoding-mismatch"
    INVALID_REPLACEMENT = "invalid-replacement"
    REPLACEMENT_ORDER = "replacement-order"
    DUPLICATE_INSERTION = "duplicate-insertion"
    ORIGINAL_MISMATCH = "original-mismatch"
    TARGET_MISMATCH = "target-mismatch"
    COORDINATE_OVERFLOW = "coordinate-overflow"
    INVALID_REGION = "invalid-region"
    DIGEST_MISMATCH = "digest-mismatch"
    PROOF_MISMATCH = "proof-mismatch"


class UntouchedByteProofError(Exception):
    """Proof construction or verification failure (untouched_proof.rs:135-172).

    Carries no registered error code; the proof is an internal edit-commit
    artifact (RFC 0004 §15).
    """

    def __init__(self, kind: UntouchedByteProofErrorKind, *, index: int | None = None) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.index = index


@dataclass(frozen=True, slots=True)
class UntouchedByteProof:
    """Immutable evidence for every byte outside one exact replacement plan
    (untouched_proof.rs:62-132; RFC 0004 §15)."""

    base_digest: ContentDigest
    target_digest: ContentDigest
    regions: tuple[UntouchedByteRegion, ...] = field(default_factory=tuple)

    @classmethod
    def create(
        cls,
        base: SourceSnapshot,
        target: SourceSnapshot,
        replacements: list[SourceReplacement],
    ) -> UntouchedByteProof:
        """Creates a proof only when the replacements exactly produce the
        supplied target snapshot (untouched_proof.rs:71-82)."""
        regions = _expected_regions(base, target, replacements)
        return cls(
            base_digest=base.digest(),
            target_digest=target.digest(),
            regions=regions,
        )

    @classmethod
    def from_facts(
        cls,
        base_digest: ContentDigest,
        target_digest: ContentDigest,
        regions: list[UntouchedByteRegion],
    ) -> UntouchedByteProof:
        """Constructs transferable proof facts after validating their
        canonical structure (untouched_proof.rs:85-96)."""
        _validate_regions(regions)
        return cls(base_digest=base_digest, target_digest=target_digest, regions=tuple(regions))

    def verify(
        self,
        base: SourceSnapshot,
        target: SourceSnapshot,
        replacements: list[SourceReplacement],
    ) -> None:
        """Rechecks digests, replacement preconditions, exact target bytes,
        and every region fact (untouched_proof.rs:99-113)."""
        if base.digest() != self.base_digest or target.digest() != self.target_digest:
            raise UntouchedByteProofError(UntouchedByteProofErrorKind.DIGEST_MISMATCH)
        expected = _expected_regions(base, target, replacements)
        if expected != self.regions:
            raise UntouchedByteProofError(UntouchedByteProofErrorKind.PROOF_MISMATCH)


# ---------------------------------------------------------------------------
# Canonical region computation
# ---------------------------------------------------------------------------


def _expected_regions(
    base: SourceSnapshot,
    target: SourceSnapshot,
    replacements: list[SourceReplacement],
) -> tuple[UntouchedByteRegion, ...]:
    """Computes the canonical maximal unchanged regions (untouched_proof.rs:182-245)."""
    if base.encoding_facts() != target.encoding_facts():
        raise UntouchedByteProofError(UntouchedByteProofErrorKind.ENCODING_MISMATCH)
    base_bytes = base.bytes()
    target_bytes = target.bytes()
    regions: list[UntouchedByteRegion] = []
    old_cursor = 0
    new_cursor = 0
    previous: SourceReplacement | None = None
    for index, replacement in enumerate(replacements):
        _validate_replacement(base_bytes, previous, replacement, index)
        unchanged_len = replacement.old_start - old_cursor
        new_unchanged_end = new_cursor + unchanged_len
        if (
            target_bytes[new_cursor:new_unchanged_end]
            != base_bytes[old_cursor : replacement.old_start]
        ):
            raise UntouchedByteProofError(UntouchedByteProofErrorKind.TARGET_MISMATCH)
        _push_region(
            regions,
            UntouchedByteRegion.new(old_cursor, replacement.old_start, new_cursor, new_unchanged_end),
        )
        replacement_end = new_unchanged_end + len(replacement.replacement)
        if target_bytes[new_unchanged_end:replacement_end] != replacement.replacement:
            raise UntouchedByteProofError(UntouchedByteProofErrorKind.TARGET_MISMATCH)
        old_cursor = replacement.old_end
        new_cursor = replacement_end
        previous = replacement
    tail_len = len(base_bytes) - old_cursor
    new_end = new_cursor + tail_len
    if new_end != len(target_bytes) or target_bytes[new_cursor:new_end] != base_bytes[old_cursor:]:
        raise UntouchedByteProofError(UntouchedByteProofErrorKind.TARGET_MISMATCH)
    _push_region(
        regions,
        UntouchedByteRegion.new(old_cursor, len(base_bytes), new_cursor, new_end),
    )
    _validate_regions(regions)
    return tuple(regions)


def _validate_replacement(
    base_bytes: bytes,
    previous: SourceReplacement | None,
    replacement: SourceReplacement,
    index: int,
) -> None:
    """Replacement structural and precondition validation
    (untouched_proof.rs:247-281)."""
    if (
        replacement.old_start > replacement.old_end
        or replacement.old_end > len(base_bytes)
        or len(replacement.original) != replacement.old_end - replacement.old_start
    ):
        raise UntouchedByteProofError(UntouchedByteProofErrorKind.INVALID_REPLACEMENT, index=index)
    if previous is not None:
        if (
            replacement.old_start == replacement.old_end
            and previous.old_start == previous.old_end
            and replacement.old_start == previous.old_start
        ):
            raise UntouchedByteProofError(
                UntouchedByteProofErrorKind.DUPLICATE_INSERTION, index=index
            )
        if (
            (replacement.old_start, replacement.old_end)
            <= (previous.old_start, previous.old_end)
            or replacement.old_start < previous.old_end
        ):
            raise UntouchedByteProofError(
                UntouchedByteProofErrorKind.REPLACEMENT_ORDER, index=index
            )
    if base_bytes[replacement.old_start : replacement.old_end] != replacement.original:
        raise UntouchedByteProofError(UntouchedByteProofErrorKind.ORIGINAL_MISMATCH, index=index)


def _push_region(
    regions: list[UntouchedByteRegion], region: UntouchedByteRegion
) -> None:
    """Appends a region, merging with the previous one when adjacent
    (untouched_proof.rs:283-295) so regions are maximal."""
    if region.old_start == region.old_end:
        return
    if regions and regions[-1].old_end == region.old_start and regions[-1].new_end == region.new_start:
        previous = regions[-1]
        regions[-1] = UntouchedByteRegion.new(
            previous.old_start, region.old_end, previous.new_start, region.new_end
        )
        return
    regions.append(region)


def _validate_regions(regions: list[UntouchedByteRegion]) -> None:
    """Canonical region validation (untouched_proof.rs:297-317): every region
    non-empty, equal lengths, monotonic, and non-adjacent (maximal)."""
    previous: UntouchedByteRegion | None = None
    for index, region in enumerate(regions):
        old_len = region.old_end - region.old_start
        new_len = region.new_end - region.new_start
        if (
            region.old_start >= region.old_end
            or region.new_start >= region.new_end
            or old_len != new_len
        ):
            raise UntouchedByteProofError(UntouchedByteProofErrorKind.INVALID_REGION, index=index)
        if previous is not None:
            if (
                region.old_start < previous.old_end
                or region.new_start < previous.new_end
                or (region.old_start == previous.old_end and region.new_start == previous.new_end)
            ):
                raise UntouchedByteProofError(
                    UntouchedByteProofErrorKind.INVALID_REGION, index=index
                )
        previous = region
