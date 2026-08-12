"""The frozen JSON-family profile, syntax-kind, and value-kind vocabularies.

Frozen names/numbers with authority citations (language-neutral first; Rust
only for registry/byte arbitration):

- ``JsonProfile``: the three profile identities and their extension surface —
  crates/consema-json/src/lib.rs:38-45 (enum), lib.rs:137-159 (id() and
  permits_jsonc_extensions()/is_json5()); the profile ids json.strict@1 /
  jsonc.bounded@1 / json5.standard@1 are the frozen language-neutral
  spellings (RFC 0005 §1, docs/rfcs/0005-json-family-production-v1.md:15-20;
  RFC 0004 §4, docs/rfcs/0004-...:106-113).
- ``JsonSyntaxKind``: the closed 17-kind lossless classification with the
  exact stable names ("Bom", "Whitespace", "LineComment", "BlockComment",
  "LeftBrace", "RightBrace", "LeftBracket", "RightBracket", "Colon",
  "Comma", "String", "Identifier", "Number", "True", "False", "Null",
  "ErrorRegion") — crates/consema-json/src/lib.rs:49-108 (enum + as_str),
  lib.rs:113-134 (from_name). ``Identifier`` is a v2-domain kind added by
  RFC 0005 §7 (docs/rfcs/0005-...:161-172).
- ``JsonValueKind``: the native semantic categories (Null, Boolean,
  Integer, Decimal, BinaryFloat64, String, Array, Object) — lib.rs:323 and
  the kind mapping lib.rs:364-388; BinaryFloat64 is the JSON5 extension
  (RFC 0005 §6, docs/rfcs/0005-...:142-145).
- ``SemanticUnavailable`` reasons (Missing, InvalidLiteral, ErrorRegion) —
  lib.rs:310-321; ``SemanticAvailability`` mirror of lib.rs:290-307.
- JSON5 whitespace / line-terminator / IdentifierName character rules —
  RFC 0005 §3/§4 (docs/rfcs/0005-...:51-88) and the explicit character
  tables crates/consema-json/src/parser.rs:590-623.

Unicode note (blind-write disclosure): RFC 0005 §4 pins Unicode 17.0.0
identifier tables (docs/rfcs/0005-...:82-85). This implementation classifies
via the host ``str.isidentifier`` semantics (CPython 3.12, Unicode 15.0),
which matches JSON5's ID_Start/ID_Continue + ``$``/``_`` + U+200C/U+200D
rule for all codepoints whose classification is stable across 15.0..17.0;
the vectors' identifier cases (``$``, ``_``, ``while``, ``true``, ``π``,
``a``, U+200C, U+200D, ``\\u0061``) all fall in that stable set. A
differential run against the pinned unicode-id-start 1.4.0 tables is a
verification item, not a claim.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Generic, TypeVar

from consema.document.ids import ProfileId

T = TypeVar("T")


class JsonProfile(enum.Enum):
    """Frozen JSON language profile (crates/consema-json/src/lib.rs:38-45)."""

    STRICT_V1 = "json.strict"
    JSONC_BOUNDED_V1 = "jsonc.bounded"
    JSON5_STANDARD_V1 = "json5.standard"

    # -- profile identity (lib.rs:137-159) ----------------------------------

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:140-146)."""
        return ProfileId.new(self.value, 1)

    def permits_jsonc_extensions(self) -> bool:
        """Whether bounded comments and trailing commas are accepted
        (lib.rs:149-152)."""
        return self is JsonProfile.JSONC_BOUNDED_V1 or self is JsonProfile.JSON5_STANDARD_V1

    def is_json5(self) -> bool:
        """Whether the Standard JSON5 lexical surface is accepted
        (lib.rs:155-158)."""
        return self is JsonProfile.JSON5_STANDARD_V1


class JsonSyntaxKind(enum.Enum):
    """Closed JSON/JSONC lossless syntax-piece classification
    (crates/consema-json/src/lib.rs:49-84; the v1 kind set; Identifier is
    the v2 addition per RFC 0005 §7)."""

    BOM = "Bom"
    WHITESPACE = "Whitespace"
    LINE_COMMENT = "LineComment"
    BLOCK_COMMENT = "BlockComment"
    LEFT_BRACE = "LeftBrace"
    RIGHT_BRACE = "RightBrace"
    LEFT_BRACKET = "LeftBracket"
    RIGHT_BRACKET = "RightBracket"
    COLON = "Colon"
    COMMA = "Comma"
    STRING = "String"
    IDENTIFIER = "Identifier"
    NUMBER = "Number"
    TRUE = "True"
    FALSE = "False"
    NULL = "Null"
    ERROR_REGION = "ErrorRegion"

    def as_str(self) -> str:
        """Stable query and protocol name (lib.rs:86-109)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> JsonSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs:113-134)."""
        try:
            return cls(name)
        except ValueError:
            return None


class JsonValueKind(enum.Enum):
    """Native semantic value category (lib.rs:323 and the mapping at
    lib.rs:364-388; BinaryFloat64 is the JSON5 extension, RFC 0005 §6)."""

    NULL = "Null"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    BINARY_FLOAT64 = "BinaryFloat64"
    STRING = "String"
    ARRAY = "Array"
    OBJECT = "Object"


class SemanticUnavailable(enum.Enum):
    """Regional reason why native semantics are unavailable (lib.rs:310-321)."""

    MISSING = "Missing"
    INVALID_LITERAL = "InvalidLiteral"
    ERROR_REGION = "ErrorRegion"


@dataclass(frozen=True, slots=True)
class SemanticAvailability(Generic[T]):
    """Regional semantic availability (lib.rs:290-307).

    ``available(value)`` carries a decoded fact; ``unavailable(reason)``
    carries the frozen reason. For kind queries the value is always present
    when available; for value accessors the value is None when the kind does
    not apply (the ``SemanticAvailability<Option<T>>`` shape of
    lib.rs:390-490).
    """

    value: T | None
    reason: SemanticUnavailable | None = None

    @property
    def is_available(self) -> bool:
        return self.reason is None

    @classmethod
    def available(cls, value: T) -> SemanticAvailability[T]:
        return cls(value=value, reason=None)

    @classmethod
    def unavailable(cls, reason: SemanticUnavailable) -> SemanticAvailability[T]:
        return cls(value=None, reason=reason)


# -- JSON5 character classes (parser.rs:590-623; RFC 0005 §3/§4) ------------

# Exact union table of parser.rs:594-614 (RFC 0005 §3): U+0009 U+000A U+000B
# U+000C U+000D U+0020 U+00A0 U+1680 U+2000..U+200A U+2028 U+2029 U+202F
# U+205F U+3000 U+FEFF.
_JSON5_WHITESPACE = frozenset(
    chr(code)
    for code in (
        *range(0x0009, 0x000E),
        0x0020,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    )
)


def is_json5_whitespace(character: str) -> bool:
    """Exact union table of parser.rs:594-614 (RFC 0005 §3)."""
    return character in _JSON5_WHITESPACE


def is_json5_line_terminator(character: str) -> bool:
    """Line comments terminate at LF, CR, U+2028, U+2029, or end of source
    (parser.rs:590-592; RFC 0005 §3)."""
    return character in ("\n", "\r", "\u2028", "\u2029")


def is_json5_identifier_start(character: str) -> bool:
    """IdentifierName start: ``$``, ``_``, or Unicode ID_Start
    (parser.rs:616-618; RFC 0005 §4)."""
    return character in ("$", "_") or _is_id_start(character)


def is_json5_identifier_continue(character: str) -> bool:
    """IdentifierName continue: start characters, ID_Continue, U+200C,
    U+200D (parser.rs:620-623; RFC 0005 §4)."""
    return character in ("$", "_", "\u200c", "\u200d") or _is_id_continue(character)


def _is_id_start(character: str) -> bool:
    # Host ID_Start classification; see the module docstring's Unicode note.
    return character.isidentifier()


def _is_id_continue(character: str) -> bool:
    # Host ID_Continue classification (the "a"+c trick is the CPython-idiomatic
    # single-character continue test; start chars are a subset of continue).
    return ("a" + character).isidentifier()
