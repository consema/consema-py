"""Stable identifiers of the document domain.

Frozen names/numbers with authority citations:

- ``ContentDigest``: algorithm exactly ``"sha256"``, hex exactly 64 lowercase
  hexadecimal characters, digest of the complete original byte sequence with
  no decoding/BOM removal/newline normalization mixed in — RFC 0003 §3
  (https://github.com/consema/consema/blob/main/docs/rfcs/0003-source-syntax-query-and-patch-v1.md);
  https://github.com/consema/consema-rs/blob/main/consema-document/src/source.rs (arbitration).
  Golden values: conformance/vectors/source-v1.json cases
  ``source.digest.sha256-empty`` (lines 4-10) and ``source.digest.sha256-abc``
  (lines 11-16).
- ``ProfileId`` / ``FormatFamilyId``: namespaced ID + immutable version —
  https://github.com/consema/consema-rs/blob/main/consema-document/src/lib.rs.
- ``FormatOperationId``: namespaced operation identifier; its Display form
  ``id@version`` is frozen by the edit-plan metadata matching rule —
  https://github.com/consema/consema-rs/blob/main/consema-document/src/operation_registry.rs, edit_plan.rs.
- ``MaterializationStyleId``: versioned format-owned style identifier —
  https://github.com/consema/consema-rs/blob/main/consema-document/src/materialization.rs.

The vector suite name ``consema.source.conformance@1`` and the capability ids
of its cases (``core.source.snapshot@1``, ``core.source.encoding@1``,
``core.source.decoded-location@1``, ``core.source.binary-coverage@1``,
``core.source.patch@1``, ``core.source.limits@1``) are the machine-readable
capability inventory (conformance/vectors/source-v1.json:2,7,13,19,25,...).

https://github.com/consema/consema-go/blob/main/go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ContentDigest:
    """Stable SHA-256 identity of exact raw source bytes.

    Frozen by RFC 0003 §3 (algorithm "sha256", 64 lowercase hex characters);
    arbitration: https://github.com/consema/consema-rs/blob/main/consema-document/src/source.rs. Equal raw bytes
    always produce equal content digests across processes and languages; a
    digest mismatch proves different bytes. Digest equality is not a claim
    about Profile, encoding, native meaning, or document identity.
    """

    _bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self._bytes) != 32:
            raise ValueError("ContentDigest wraps exactly 32 bytes")

    @classmethod
    def of(cls, raw: bytes) -> ContentDigest:
        """Computes the digest of exact raw bytes (SHA-256, source.rs)."""
        return cls(hashlib.sha256(raw).digest())

    @classmethod
    def from_bytes(cls, value: bytes) -> ContentDigest:
        """Constructs a digest from an already decoded 32-byte record."""
        return cls(value)

    @property
    def algorithm(self) -> str:
        """Digest algorithm identifier frozen by the v1 source contract."""
        return "sha256"

    @property
    def digest_bytes(self) -> bytes:
        """Exact 32 digest bytes."""
        return self._bytes

    @property
    def hex(self) -> str:
        """Lowercase hexadecimal representation (64 characters)."""
        return self._bytes.hex()

    def to_hex(self) -> str:
        return self.hex

    def __repr__(self) -> str:
        return f"ContentDigest({self.hex})"


@dataclass(frozen=True, slots=True)
class ProfileId:
    """Immutable named language profile (lib.rs).

    Example: ``ProfileId.new("json.strict", 1)`` — supported target profiles
    are frozen by RFC 0004 §4 (https://github.com/consema/consema/blob/main/docs/rfcs/0004-materialization-conversion-and-structural-edit-v1.md).
    """

    id: str
    version: int

    @classmethod
    def new(cls, id: str, version: int) -> ProfileId:
        return cls(id=id, version=version)


@dataclass(frozen=True, slots=True)
class FormatFamilyId:
    """Stable namespaced format family contract (lib.rs)."""

    id: str
    version: int

    @classmethod
    def new(cls, id: str, version: int) -> FormatFamilyId:
        return cls(id=id, version=version)


@dataclass(frozen=True, slots=True)
class FormatOperationId:
    """Immutable namespaced operation identifier (operation_registry.rs).

    Its canonical string form ``id@version`` is frozen by the EditPlan
    operation-metadata matching rule (edit_plan.rs): the SourcePatch
    metadata key ``operation.{index}`` must equal this form.
    """

    id: str
    version: int

    @classmethod
    def new(cls, id: str, version: int) -> FormatOperationId:
        return cls(id=id, version=version)

    def to_string(self) -> str:
        """Canonical ``id@version`` spelling (operation_registry.rs)."""
        return f"{self.id}@{self.version}"


@dataclass(frozen=True, slots=True)
class MaterializationStyleId:
    """Versioned format-owned materialization style identifier
    (materialization.rs).

    Frozen style IDs for 0.5.0: ``json.canonical-compact@1``,
    ``json.canonical-pretty@1``, ``toml.canonical-document@1``
    (RFC 0004 §4, https://github.com/consema/consema/blob/main/docs/rfcs/0004-materialization-conversion-and-structural-edit-v1.md).
    """

    id: str
    version: int

    @classmethod
    def new(cls, id: str, version: int) -> MaterializationStyleId:
        return cls(id=id, version=version)
