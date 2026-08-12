"""Verifiable raw-byte patches between immutable source snapshots.

Authority (language-neutral first; Rust only for byte/registry arbitration):

- RFC 0003 §10 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md:250-291):
  the exact core.source-patch@1 fields (base_digest, target_digest,
  encoding, ordered replacements, metadata) and the application rules —
  old ranges are half-open, ordered, and non-overlapping; `original` exactly
  equals the base bytes in its range; zero-width insertions are permitted but
  two replacements may not target the same insertion point; applying uses the
  current raw bytes, never decoded character offsets; base digest, encoding
  facts, every original-byte precondition, and computed target digest must
  match; any mismatch fails atomically and returns no new SourceSnapshot;
  successful application reruns encoding resolution and requires the
  resulting encoding facts to equal the patch facts; metadata is
  deterministic audit data and cannot affect application; redaction flags
  control review/log presentation, not the bytes required for application.
- RFC 0003 §12 (lines 311-317): source limits cover raw bytes, decoded UTF-8
  bytes, decoded boundary count, and patch replacement count/bytes; limits
  apply before or during allocation.
- RFC 0004 §16 (lines 373-385): a committed edit derives core.source-patch@1
  from the exact old SourceSnapshot, prepared non-overlapping source edits,
  the exact new SourceSnapshot, and operation metadata; the derived patch
  must reapply to the old snapshot and reproduce the exact new digest.
- crates/consema-document/src/source_patch.rs — arbitration: SourcePatchLimits
  defaults source_patch.rs:19-27; SourceReplacement source_patch.rs:30-108;
  SourcePatch::new/create/apply/derive source_patch.rs:143-280; error-to-code
  mapping source_patch.rs:434-458; replacement validation source_patch.rs:469-
  512; application source_patch.rs:514-554.
- Error codes: crates/consema-protocol/src/error_registry.rs
  (core.source.patch-base-mismatch@1:381, patch-original-mismatch@1:387,
  patch-target-mismatch@1:393, resource-limit@1:399, encoding-conflict@1:366,
  unsupported-bom@1:405, invalid-sequence@1:372; structural patch defects map
  to core.protocol.invalid-value@1:87 per source_patch.rs:453-456).
- Vector cases: conformance/vectors/source-v1.json lines 119-154
  (source.patch.success / reject-stale-base / reject-original-mismatch /
  reject-overlap / reject-target-mismatch / reject-encoding-change) and
  lines 167-172 (source.resource.patch-count-limit).

go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.change_set import ChangeSet
from consema.document.ids import ContentDigest
from consema.document.source import (
    EncodingFacts,
    EncodingRequest,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
)

# Frozen defaults, crates/consema-document/src/source_patch.rs:19-27
_DEFAULT_MAX_REPLACEMENTS = 100_000
_DEFAULT_MAX_PATCH_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourcePatchLimits:
    """Resource bounds for constructing or applying one source patch
    (source_patch.rs:9-27; RFC 0003 §12)."""

    source: SourceLimits = field(default_factory=SourceLimits)
    max_replacements: int = _DEFAULT_MAX_REPLACEMENTS
    max_patch_bytes: int = _DEFAULT_MAX_PATCH_BYTES


@dataclass(frozen=True, slots=True)
class SourceReplacement:
    """One raw-byte precondition and replacement in a source patch
    (source_patch.rs:30-108; RFC 0003 §10).

    ``original`` must exactly equal the base bytes in [old_start, old_end);
    ``redact_original``/``redact_replacement`` control review/debug
    presentation only, never the bytes required for application.
    """

    old_start: int
    old_end: int
    original: bytes = field(repr=False)
    replacement: bytes = field(repr=False)
    redact_original: bool = False
    redact_replacement: bool = False

    @classmethod
    def new(
        cls,
        old_start: int,
        old_end: int,
        original: bytes,
        replacement: bytes,
    ) -> SourceReplacement:
        """Creates one half-open raw-byte replacement (source_patch.rs:43-57)."""
        return cls(old_start=old_start, old_end=old_end, original=original, replacement=replacement)

    def with_original_redacted(self, redacted: bool) -> SourceReplacement:
        return SourceReplacement(
            old_start=self.old_start,
            old_end=self.old_end,
            original=self.original,
            replacement=self.replacement,
            redact_original=redacted,
            redact_replacement=self.redact_replacement,
        )

    def with_replacement_redacted(self, redacted: bool) -> SourceReplacement:
        return SourceReplacement(
            old_start=self.old_start,
            old_end=self.old_end,
            original=self.original,
            replacement=self.replacement,
            redact_original=self.redact_original,
            redact_replacement=redacted,
        )

    def __repr__(self) -> str:
        original = "<redacted>" if self.redact_original else repr(self.original)
        replacement = "<redacted>" if self.redact_replacement else repr(self.replacement)
        return (
            f"SourceReplacement(old_start={self.old_start}, old_end={self.old_end}, "
            f"original={original}, replacement={replacement}, "
            f"redact_original={self.redact_original}, "
            f"redact_replacement={self.redact_replacement})"
        )


class SourcePatchErrorKind(enum.Enum):
    """Stable source patch construction or application failure
    (source_patch.rs:388-432)."""

    CHANGE_SET_MISMATCH = "change-set-mismatch"
    INVALID_REPLACEMENT = "invalid-replacement"
    REPLACEMENT_ORDER = "replacement-order"
    DUPLICATE_INSERTION = "duplicate-insertion"
    BASE_MISMATCH = "base-mismatch"
    ORIGINAL_MISMATCH = "original-mismatch"
    TARGET_MISMATCH = "target-mismatch"
    ENCODING_MISMATCH = "encoding-mismatch"
    RESOURCE_LIMIT = "resource-limit"
    SOURCE = "source"


_CODE_BY_PATCH_KIND = {
    SourcePatchErrorKind.BASE_MISMATCH: "core.source.patch-base-mismatch@1",
    SourcePatchErrorKind.ORIGINAL_MISMATCH: "core.source.patch-original-mismatch@1",
    SourcePatchErrorKind.TARGET_MISMATCH: "core.source.patch-target-mismatch@1",
    SourcePatchErrorKind.ENCODING_MISMATCH: "core.source.encoding-conflict@1",
    SourcePatchErrorKind.RESOURCE_LIMIT: "core.source.resource-limit@1",
    SourcePatchErrorKind.INVALID_REPLACEMENT: "core.protocol.invalid-value@1",
    SourcePatchErrorKind.REPLACEMENT_ORDER: "core.protocol.invalid-value@1",
    SourcePatchErrorKind.DUPLICATE_INSERTION: "core.protocol.invalid-value@1",
    SourcePatchErrorKind.CHANGE_SET_MISMATCH: "core.protocol.invalid-value@1",
}


class SourcePatchError(Exception):
    """Stable source patch construction or application failure with a frozen
    registered code (source_patch.rs:388-459; error_registry.rs citations in
    the module docstring). Structural defects map to
    core.protocol.invalid-value@1 (error_registry.rs:87); the SOURCE variant
    delegates to the wrapped SourceError's code (encoding-conflict,
    unsupported-bom, invalid-sequence, resource-limit). Error text is human
    presentation only (RFC 0016 §6).
    """

    def __init__(
        self,
        kind: SourcePatchErrorKind,
        *,
        index: int | None = None,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        source: SourceError | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.index = index
        self.name = name
        self.observed = observed
        self.limit = limit
        self.source = source

    @property
    def code(self) -> str:
        if self.kind is SourcePatchErrorKind.SOURCE:
            assert self.source is not None
            return self.source.code
        return _CODE_BY_PATCH_KIND[self.kind]

    def __str__(self) -> str:
        if self.kind is SourcePatchErrorKind.SOURCE:
            return f"source patch target source failure: {self.source}"
        if self.index is not None:
            return f"source patch {self.kind.value} at replacement {self.index}"
        if self.kind is SourcePatchErrorKind.RESOURCE_LIMIT:
            return (
                f"source patch limit {self.name}: observed {self.observed} > "
                f"limit {self.limit}"
            )
        return f"source patch {self.kind.value}"


@dataclass(frozen=True, slots=True)
class SourcePatch:
    """Immutable, transferable facts needed to verify one raw source
    transition (source_patch.rs:134-365; RFC 0003 §10).

    Applying rechecks the base digest, the encoding facts, every
    original-byte precondition, and the computed target digest; any mismatch
    fails atomically and returns no new SourceSnapshot. Metadata is
    deterministic audit data and cannot affect application. SourcePatch is
    not ChangeSet, semantic diff, merge, fuzzy patch, file-system write, or
    permission to alter a stale snapshot (RFC 0003 §10, lines 290-291).
    """

    base_digest: ContentDigest
    target_digest: ContentDigest
    encoding: EncodingFacts
    replacements: tuple[SourceReplacement, ...] = field(default_factory=tuple)
    metadata: dict[str, str] = field(default_factory=dict, repr=False)

    # -- construction -----------------------------------------------------

    @classmethod
    def new(
        cls,
        base_digest: ContentDigest,
        target_digest: ContentDigest,
        encoding: EncodingFacts,
        replacements: list[SourceReplacement],
        metadata: dict[str, str],
        limits: SourcePatchLimits,
    ) -> SourcePatch:
        """Creates a patch from externally supplied facts after structural
        and resource validation (source_patch.rs:208-224)."""
        _validate_replacements(replacements, limits)
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            encoding=encoding,
            replacements=tuple(replacements),
            metadata=dict(sorted(metadata.items())),
        )

    @classmethod
    def create(
        cls,
        base: SourceSnapshot,
        replacements: list[SourceReplacement],
        metadata: dict[str, str],
        limits: SourcePatchLimits,
    ) -> SourcePatch:
        """Builds a self-consistent patch against one immutable base snapshot
        (source_patch.rs:227-251)."""
        _validate_replacements(replacements, limits)
        target_bytes = _apply_replacements(base.bytes(), replacements, limits)
        target = _snapshot_from_bytes(base, target_bytes, limits)
        if target.encoding_facts() != base.encoding_facts():
            raise SourcePatchError(SourcePatchErrorKind.ENCODING_MISMATCH)
        return cls(
            base_digest=base.digest(),
            target_digest=target.digest(),
            encoding=base.encoding_facts(),
            replacements=tuple(replacements),
            metadata=dict(sorted(metadata.items())),
        )

    @classmethod
    def derive(
        cls,
        base: SourceSnapshot,
        target: SourceSnapshot,
        change_set: ChangeSet,
        metadata: dict[str, str],
        limits: SourcePatchLimits,
    ) -> SourcePatch:
        """Derives and verifies a portable patch from one complete
        document-level change fact (source_patch.rs:145-205; RFC 0004 §16)."""
        if base.encoding_facts() != target.encoding_facts():
            raise SourcePatchError(SourcePatchErrorKind.ENCODING_MISMATCH)
        edits = change_set.source_edits
        if len(edits) > limits.max_replacements:
            raise SourcePatchError(
                SourcePatchErrorKind.RESOURCE_LIMIT,
                name="patch-replacements",
                observed=len(edits),
                limit=limits.max_replacements,
            )
        replacements: list[SourceReplacement] = []
        previous_new: tuple[int, int] | None = None
        base_bytes = base.bytes()
        target_bytes = target.bytes()
        for index, edit in enumerate(edits):
            if (
                edit.old_span.snapshot != change_set.old_snapshot
                or edit.new_span.snapshot != change_set.new_snapshot
                or edit.old_span.end_byte > len(base_bytes)
                or edit.new_span.end_byte > len(target_bytes)
                or edit.replacement
                != target_bytes[edit.new_span.start_byte : edit.new_span.end_byte]
            ):
                raise SourcePatchError(
                    SourcePatchErrorKind.CHANGE_SET_MISMATCH, index=index
                )
            new_range = (edit.new_span.start_byte, edit.new_span.end_byte)
            if previous_new is not None:
                if new_range <= previous_new or new_range[0] < previous_new[1]:
                    raise SourcePatchError(
                        SourcePatchErrorKind.CHANGE_SET_MISMATCH, index=index
                    )
            replacements.append(
                SourceReplacement.new(
                    edit.old_span.start_byte,
                    edit.old_span.end_byte,
                    base_bytes[edit.old_span.start_byte : edit.old_span.end_byte],
                    edit.replacement,
                )
            )
            previous_new = new_range
        patch = cls.new(
            base.digest(),
            target.digest(),
            base.encoding_facts(),
            replacements,
            metadata,
            limits,
        )
        reapplied = patch.apply(base, limits)
        if reapplied.bytes() != target_bytes:
            raise SourcePatchError(SourcePatchErrorKind.TARGET_MISMATCH)
        return patch

    # -- application ------------------------------------------------------

    def apply(self, base: SourceSnapshot, limits: SourcePatchLimits) -> SourceSnapshot:
        """Applies all facts atomically and returns a new immutable snapshot
        only on complete success (source_patch.rs:254-280)."""
        _validate_replacements(list(self.replacements), limits)
        if base.digest() != self.base_digest:
            raise SourcePatchError(SourcePatchErrorKind.BASE_MISMATCH)
        if base.encoding_facts() != self.encoding:
            raise SourcePatchError(SourcePatchErrorKind.ENCODING_MISMATCH)
        target_bytes = _apply_replacements(base.bytes(), list(self.replacements), limits)
        target = _snapshot_from_bytes(base, target_bytes, limits)
        if target.encoding_facts() != self.encoding:
            raise SourcePatchError(SourcePatchErrorKind.ENCODING_MISMATCH)
        if target.digest() != self.target_digest:
            raise SourcePatchError(SourcePatchErrorKind.TARGET_MISMATCH)
        return target

    # -- accessors --------------------------------------------------------

    def with_all_replacements_redacted(
        self, redact_original: bool, redact_replacement: bool
    ) -> SourcePatch:
        """Marks every replacement payload for redacted review/debug
        presentation; exact bytes remain present for digest and original-byte
        precondition checks (source_patch.rs:315-336)."""
        return SourcePatch(
            base_digest=self.base_digest,
            target_digest=self.target_digest,
            encoding=self.encoding,
            replacements=tuple(
                replacement.with_original_redacted(redact_original).with_replacement_redacted(
                    redact_replacement
                )
                for replacement in self.replacements
            ),
            metadata=self.metadata,
        )

    def with_replacement_redacted(
        self, index: int, redact_original: bool, redact_replacement: bool
    ) -> SourcePatch:
        """Marks one exact replacement payload for redacted review/debug
        presentation (source_patch.rs:339-364)."""
        if index >= len(self.replacements):
            raise SourcePatchRedactionError(index=index)
        redacted = list(self.replacements)
        redacted[index] = redacted[index].with_original_redacted(
            redact_original
        ).with_replacement_redacted(redact_replacement)
        return SourcePatch(
            base_digest=self.base_digest,
            target_digest=self.target_digest,
            encoding=self.encoding,
            replacements=tuple(redacted),
            metadata=self.metadata,
        )


class SourcePatchRedactionError(Exception):
    """Review-redaction selection failure; patch bytes and application facts
    are unchanged (source_patch.rs:368-377). Carries no registered code."""

    def __init__(self, index: int | None = None) -> None:
        super().__init__("redaction-selection" if index is None else f"unknown-replacement-{index}")
        self.index = index


# ---------------------------------------------------------------------------
# Validation and application
# ---------------------------------------------------------------------------


def _validate_replacements(
    replacements: list[SourceReplacement], limits: SourcePatchLimits
) -> None:
    """Canonical structural validation (source_patch.rs:469-512; RFC 0003
    §10: half-open, ordered, non-overlapping, original length exact,
    duplicate insertion points rejected)."""
    _check_patch_limit("patch-replacements", len(replacements), limits.max_replacements)
    patch_bytes = 0
    previous: SourceReplacement | None = None
    for index, replacement in enumerate(replacements):
        if (
            replacement.old_start > replacement.old_end
            or len(replacement.original) != replacement.old_end - replacement.old_start
        ):
            raise SourcePatchError(SourcePatchErrorKind.INVALID_REPLACEMENT, index=index)
        if previous is not None:
            if (
                replacement.old_start == replacement.old_end
                and previous.old_start == previous.old_end
                and replacement.old_start == previous.old_start
            ):
                raise SourcePatchError(SourcePatchErrorKind.DUPLICATE_INSERTION, index=index)
            if (
                (replacement.old_start, replacement.old_end)
                <= (previous.old_start, previous.old_end)
                or replacement.old_start < previous.old_end
            ):
                raise SourcePatchError(SourcePatchErrorKind.REPLACEMENT_ORDER, index=index)
        patch_bytes += len(replacement.original) + len(replacement.replacement)
        _check_patch_limit("patch-bytes", patch_bytes, limits.max_patch_bytes)
        previous = replacement


def _apply_replacements(
    base: bytes,
    replacements: list[SourceReplacement],
    limits: SourcePatchLimits,
) -> bytes:
    """Applies replacements to exact base bytes, rechecking every original-byte
    precondition (source_patch.rs:514-554)."""
    target_len = len(base)
    for index, replacement in enumerate(replacements):
        if (
            replacement.old_end > len(base)
            or base[replacement.old_start : replacement.old_end]
            != replacement.original
        ):
            raise SourcePatchError(SourcePatchErrorKind.ORIGINAL_MISMATCH, index=index)
        target_len = target_len - len(replacement.original) + len(replacement.replacement)
        _check_patch_limit("target-raw-bytes", target_len, limits.source.max_raw_bytes)
    target = bytearray()
    cursor = 0
    for replacement in replacements:
        target.extend(base[cursor : replacement.old_start])
        target.extend(replacement.replacement)
        cursor = replacement.old_end
    target.extend(base[cursor:])
    return bytes(target)


def _snapshot_from_bytes(
    base: SourceSnapshot, target_bytes: bytes, limits: SourcePatchLimits
) -> SourceSnapshot:
    """Reruns encoding resolution on the result bytes (source_patch.rs:267-272;
    RFC 0003 §10: successful application reruns encoding resolution)."""
    try:
        return SourceSnapshot.from_raw(
            target_bytes,
            _resolution_request(base.encoding_facts()),
            limits.source,
        )
    except SourceError as error:
        raise SourcePatchError(
            SourcePatchErrorKind.SOURCE, source=error
        ) from None


def _resolution_request(facts: EncodingFacts) -> EncodingRequest:
    """Rebuilds the EncodingRequest from recorded facts (source.rs:371-378;
    crates/consema-protocol/src/source.rs:791-800)."""
    request = EncodingRequest.new(facts.profile_default).with_bom_policy(facts.bom_policy)
    if facts.declaration is not None:
        request = request.with_declaration(facts.declaration)
    if facts.caller_override is not None:
        request = request.with_caller_override(facts.caller_override)
    return request


def _check_patch_limit(name: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise SourcePatchError(
            SourcePatchErrorKind.RESOURCE_LIMIT,
            name=name,
            observed=observed,
            limit=limit,
        )
