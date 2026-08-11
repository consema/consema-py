"""One immutable document transition: ordered source edits and node mappings.

Authority:
- crates/consema-document/src/lib.rs:800-900 — SourceEdit (old_span,
  new_span, replacement), NodeMappingStatus (closed six-value vocabulary),
  NodeMapping (old, new, status, reason), ChangeSet (old_snapshot,
  new_snapshot, source_edits, node_mappings, diagnostics).
- RFC 0004 §13 (docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md:308-336) — one immutable transaction binds one base
  SnapshotIdentity; every operation is fully validated before any output is
  published; validation, source-edit preparation, output allocation, reparse,
  mapping, untouched proof, and SourcePatch derivation form one atomic
  commit.
- RFC 0016 §5.3 (lines 184-187) — ChangeSet remains the document-level
  change fact; SourcePatch remains the portable raw-byte application fact
  (RFC 0004 §16).

The diagnostics field carries ordered diagnostic records of the protocol
layer (consema.protocol Diagnostic, RFC 0015); it is typed opaquely here
because the protocol package is owned by another agent and lands in the same
milestone.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.structural import NodeRef, SnapshotIdentity, Span


@dataclass(frozen=True, slots=True)
class SourceEdit:
    """One ordered non-overlapping source replacement (lib.rs:800-809).

    ``old_span`` is the replaced old range, ``new_span`` the range occupied
    by the replacement bytes in the new snapshot, ``replacement`` the exact
    replacement bytes.
    """

    old_span: Span
    new_span: Span
    replacement: bytes = field(repr=False)


class NodeMappingStatus(enum.Enum):
    """Explicit node mapping status across immutable snapshots (lib.rs:811-826)."""

    PRESERVED = "Preserved"
    REPLACED = "Replaced"
    DELETED = "Deleted"
    SPLIT = "Split"
    MERGED = "Merged"
    UNMAPPED = "Unmapped"


@dataclass(frozen=True, slots=True)
class NodeMapping:
    """One explicit old-to-new node mapping fact (lib.rs:829-839)."""

    old: NodeRef
    new: NodeRef | None
    status: NodeMappingStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Complete immutable description of one atomic document transition
    (lib.rs:841-900)."""

    old_snapshot: SnapshotIdentity
    new_snapshot: SnapshotIdentity
    source_edits: tuple[SourceEdit, ...] = field(default_factory=tuple)
    node_mappings: tuple[NodeMapping, ...] = field(default_factory=tuple)
    diagnostics: tuple[object, ...] = field(default_factory=tuple, repr=False)
