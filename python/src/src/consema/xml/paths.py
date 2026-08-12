"""Portable input locations shared by projection and materialization.

Authority:

- RFC 0004 §8 (docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md:193-217): input locations are ``Value(ValuePath)`` and
  ``Association(AssociationLocation)``; the process-local map is complete
  for every emitted value and supported association.
- The ValuePath/AssociationLocation shapes are the semantic-model records
  of consema-core (crates/consema-core/src/value_path.rs and
  association_location.rs); the segment vocabulary is frozen:
  ``ObjectValue(String)``, ``SequenceElement(u64)``, ``EntryKey(u64)``,
  ``EntryValue(u64)`` (crates/consema-xml/src/projection.rs:594-598 and
  materialization.rs:215-255 use exactly these segments).
- AssociationRole is closed at ObjectEntry / ObjectKey / EntryMappingEntry
  (crates/consema-core/src/association_location.rs); the XML family uses
  EntryMappingEntry for simple-entry-mapping associations and ObjectEntry
  for materialization-generated namespace bindings
  (materialization.rs:558-562).

These records are typed opaquely by consema.document
(materialization.py:169-195 MaterializationInputLocation). If the protocol
agent later publishes the semantic-model v6 value-path records, this module
should delegate to them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ValuePathSegmentKind(enum.Enum):
    """One immutable path segment (value_path.rs; the four closed kinds)."""

    OBJECT_VALUE = "ObjectValue"
    SEQUENCE_ELEMENT = "SequenceElement"
    ENTRY_KEY = "EntryKey"
    ENTRY_VALUE = "EntryValue"


@dataclass(frozen=True, slots=True)
class ValuePathSegment:
    """One immutable path segment."""

    kind: ValuePathSegmentKind
    value: str | int | None = None

    @classmethod
    def object_value(cls, key: str) -> ValuePathSegment:
        return cls(kind=ValuePathSegmentKind.OBJECT_VALUE, value=key)

    @classmethod
    def sequence_element(cls, ordinal: int) -> ValuePathSegment:
        return cls(kind=ValuePathSegmentKind.SEQUENCE_ELEMENT, value=ordinal)

    @classmethod
    def entry_key(cls, ordinal: int) -> ValuePathSegment:
        return cls(kind=ValuePathSegmentKind.ENTRY_KEY, value=ordinal)

    @classmethod
    def entry_value(cls, ordinal: int) -> ValuePathSegment:
        return cls(kind=ValuePathSegmentKind.ENTRY_VALUE, value=ordinal)

    def __str__(self) -> str:
        return f"{self.kind.value}({self.value!r})"


@dataclass(frozen=True, slots=True)
class ValuePath:
    """One immutable portable value path from the root (value_path.rs).

    The root path is the empty segment sequence.
    """

    segments: tuple[ValuePathSegment, ...] = ()

    @classmethod
    def root(cls) -> ValuePath:
        return cls()

    def child(self, segment: ValuePathSegment) -> ValuePath:
        return ValuePath(segments=self.segments + (segment,))

    def __str__(self) -> str:
        return "$" + "".join(f".{segment}" for segment in self.segments)


class AssociationRole(enum.Enum):
    """Closed association role (association_location.rs)."""

    OBJECT_ENTRY = "ObjectEntry"
    OBJECT_KEY = "ObjectKey"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"


@dataclass(frozen=True, slots=True)
class AssociationLocation:
    """One ordered association inside one portable container
    (association_location.rs): the owning value path, the zero-based
    association ordinal, and the role."""

    path: ValuePath
    ordinal: int
    role: AssociationRole
