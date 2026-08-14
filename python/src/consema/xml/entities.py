"""Safe internal DTD/entity boundary (RFC 0012 §3).

Authority:

- RFC 0012 §3 (https://github.com/consema/consema/blob/main/docs/rfcs/0012-xml-1.0-safe-profile-v1.md): the
  Profile permits no DOCTYPE or an internal-only DOCTYPE with a bounded
  subset; external subsets, external/unparsed/parameter entities, notation,
  and validation declarations never trigger fallback behavior; the five
  predefined entities are always available with their XML meanings; internal
  general entity names are unique; expansion is guarded before and during
  allocation across the whole document, not independently per reference.
- The vocabulary transcribes https://github.com/consema/consema-rs/blob/main/consema-xml/src/entity.rs
  (PREDEFINED_ENTITIES:19-40, predefined_value:42-49, is_xml_char:51-59,
  ReplacementError:61-72, validate_replacement_text:74-89, ExpansionBreach:
 EntityExpansionLimits, EntityExpansionState) —
  byte/registry arbitration only; this module is a Python-idiomatic
  reimplementation.
- The entity diagnostic codes are frozen by the parser
  (https://github.com/consema/consema-rs/blob/main/consema-xml/src/parser.rs): markup@1, illegal-character@1,
  parameter-entity@1, reserved-name@1, duplicate@1,
  unknown@1, cyclic@1, amplification@1, limit@1 — code names are the
  anchors.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

PREDEFINED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("lt", "<"),
    ("gt", ">"),
    ("amp", "&"),
    ("apos", "'"),
    ("quot", '"'),
)


def predefined_value(name: str) -> str | None:
    """Returns the replacement value of a predefined entity by exact name
    (entity.rs)."""
    for entity_name, value in PREDEFINED_ENTITIES:
        if entity_name == name:
            return value
    return None


def is_xml_char(character: str) -> bool:
    """Returns whether ``c`` is a legal XML 1.0 character (entity.rs)."""
    value = ord(character)
    return (
        value in (0x09, 0x0A, 0x0D)
        or 0x20 <= value <= 0xD7FF
        or 0xE000 <= value <= 0xFFFD
        or 0x0001_0000 <= value <= 0x0010_FFFF
    )


class ReplacementErrorKind(enum.Enum):
    """Replacement-text validation failure category (entity.rs)."""

    CONTAINS_MARKUP = "ContainsMarkup"
    ILLEGAL_CHARACTER = "IllegalCharacter"


class ReplacementError(Exception):
    """One replacement-text validation failure (entity.rs)."""

    def __init__(self, kind: ReplacementErrorKind, *, scalar: int | None = None) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.scalar = scalar


def validate_replacement_text(text: str) -> None:
    """Validates one internal general entity value (entity.rs).

    An admitted value may contain character data, character references,
    predefined entity references, or references to another admitted internal
    general entity, but never ``<``. Raises ReplacementError."""
    if "<" in text:
        raise ReplacementError(ReplacementErrorKind.CONTAINS_MARKUP)
    for character in text:
        if not is_xml_char(character):
            raise ReplacementError(
                ReplacementErrorKind.ILLEGAL_CHARACTER, scalar=ord(character)
            )


class ExpansionBreachKind(enum.Enum):
    """Entity expansion breach category (entity.rs)."""

    DECLARATION_LIMIT = "DeclarationLimit"
    REFERENCE_LIMIT = "ReferenceLimit"
    DEPTH_LIMIT = "DepthLimit"
    EXPANDED_BYTES = "ExpandedBytes"
    EXPANDED_SCALARS = "ExpandedScalars"
    AMPLIFICATION = "Amplification"


class ExpansionBreach(Exception):
    """One entity expansion breach (entity.rs)."""

    def __init__(self, kind: ExpansionBreachKind) -> None:
        super().__init__(kind.value)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class EntityExpansionLimits:
    """Entity expansion limits derived from the XML parse limits
    (entity.rs; lib.rs)."""

    max_declarations: int
    max_references: int
    max_expansion_depth: int
    max_expanded_bytes: int
    max_expanded_scalars: int
    max_amplification_ratio: int


class EntityExpansionState:
    """Document-wide entity expansion accounting (entity.rs).

    Counters apply across the whole document, not independently per
    reference, so an attack cannot split its budget across references.
    """

    __slots__ = (
        "declarations",
        "references",
        "declared_bytes",
        "declared_scalars",
        "expanded_bytes",
        "expanded_scalars",
        "expansion_depth",
    )

    def __init__(self) -> None:
        self.declarations = 0
        self.references = 0
        self.declared_bytes = 0
        self.declared_scalars = 0
        self.expanded_bytes = 0
        self.expanded_scalars = 0
        self.expansion_depth = 0

    def record_declaration(
        self, replacement_bytes: int, replacement_scalars: int, limits: EntityExpansionLimits
    ) -> None:
        """Records one collected declaration with its replacement text size
        (entity.rs). Raises ExpansionBreach."""
        if self.declarations >= limits.max_declarations:
            raise ExpansionBreach(ExpansionBreachKind.DECLARATION_LIMIT)
        self.declarations += 1
        self.declared_bytes += replacement_bytes
        self.declared_scalars += replacement_scalars

    def enter_reference(
        self, expanded_bytes: int, expanded_scalars: int, limits: EntityExpansionLimits
    ) -> None:
        """Enters one reference expansion and accounts its resolved size
        (entity.rs). Raises ExpansionBreach."""
        if self.references >= limits.max_references:
            raise ExpansionBreach(ExpansionBreachKind.REFERENCE_LIMIT)
        if self.expansion_depth >= limits.max_expansion_depth:
            raise ExpansionBreach(ExpansionBreachKind.DEPTH_LIMIT)
        self.references += 1
        self.expansion_depth += 1
        self.expanded_bytes += expanded_bytes
        self.expanded_scalars += expanded_scalars
        if self.expanded_bytes > limits.max_expanded_bytes:
            raise ExpansionBreach(ExpansionBreachKind.EXPANDED_BYTES)
        if self.expanded_scalars > limits.max_expanded_scalars:
            raise ExpansionBreach(ExpansionBreachKind.EXPANDED_SCALARS)
        if self.expanded_bytes > self._amplification_bound(limits):
            raise ExpansionBreach(ExpansionBreachKind.AMPLIFICATION)

    def leave_reference(self) -> None:
        """Leaves one completed reference expansion (entity.rs)."""
        if self.expansion_depth > 0:
            self.expansion_depth -= 1

    def _amplification_bound(self, limits: EntityExpansionLimits) -> int:
        return self.declared_bytes * limits.max_amplification_ratio


def expansion_breach_code(breach: ExpansionBreach) -> str:
    """The frozen diagnostic code of one expansion breach
    (parser.rs: amplification vs everything else)."""
    if breach.kind is ExpansionBreachKind.AMPLIFICATION:
        return "xml.entity.amplification@1"
    return "xml.entity.limit@1"
