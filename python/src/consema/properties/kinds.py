"""The frozen Java Properties profile, syntax-kind, and value-state
vocabularies.

Frozen names/numbers with authority citations (language-neutral first;
Rust only for registry/byte arbitration):

- ``PropertiesProfile``: the two profile identities —
  java-properties.reader@1 / java-properties.latin1@1 — and their source
  contracts — RFC 0010 §1/§3 (https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-
  v1.md) and https://github.com/consema/consema-rs/blob/main/consema-properties/src/lib.rs
  (enum + id()).
- ``PropertiesSyntaxKind``: the closed 12-kind lossless classification
  ("Bom", "Whitespace", "LineBreak", "CommentMarker", "CommentText",
  "Key", "Separator", "Value", "EscapeMarker", "EscapeBody",
  "ContinuationMarker", "ErrorRegion") — RFC 0010 §10
  (https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md) and lib.rs (enum, as_str,
  from_name).
- ``PropertiesValueState``: ImplicitEmpty | ExplicitEmpty | Present —
  RFC 0010 §6 (https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md) and lib.rs.
- ``PropertiesLogicalLineKind``: Property | Error — lib.rs.
- ``PropertiesEscapeKind``: Named | Backslash | Unicode |
  DroppedBackslash — RFC 0010 §7 (https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md) and
  lib.rs.
- The query domains ``java-properties.native-semantic-query@1`` and
  ``java-properties.lossless-syntax-query@1`` with their eight + four
  operators — RFC 0010 §10 (https://github.com/consema/consema/blob/main/docs/rfcs/0010-java-properties-profiles-v1.md) and
  https://github.com/consema/consema-rs/blob/main/consema-properties/src/query.rs.

The profile ids are the frozen language-neutral spellings also pinned by
the vector suite ``consema.java-properties.conformance@1``
(conformance/vectors/java-properties-v1.json:2-3). https://github.com/consema/consema-go/blob/main/go/properties is a
cross-reference only.
"""

from __future__ import annotations

import enum

from consema.document.ids import ProfileId


class PropertiesProfile(enum.Enum):
    """Frozen Java Properties formation profile (lib.rs).

    ``ReaderV1`` corresponds to ``Properties.load(Reader)`` (character
    source under an explicit published text encoding); ``Latin1V1``
    corresponds to ``Properties.load(InputStream)`` (every byte maps
    one-to-one to U+0000..U+00FF, BOM bytes are content).
    """

    READER_V1 = "java-properties.reader"
    LATIN1_V1 = "java-properties.latin1"

    # -- profile identity (lib.rs) -----------------------------------

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs)."""
        return ProfileId.new(self.value, 1)

    def is_latin1(self) -> bool:
        """Whether the InputStream-compatible one-byte profile is selected
        (RFC 0010 §3.2)."""
        return self is PropertiesProfile.LATIN1_V1


class PropertiesSyntaxKind(enum.Enum):
    """Closed lossless syntax-piece classification (lib.rs; the
    closed 12-kind set of RFC 0010 §10)."""

    BOM = "Bom"
    WHITESPACE = "Whitespace"
    LINE_BREAK = "LineBreak"
    COMMENT_MARKER = "CommentMarker"
    COMMENT_TEXT = "CommentText"
    KEY = "Key"
    SEPARATOR = "Separator"
    VALUE = "Value"
    ESCAPE_MARKER = "EscapeMarker"
    ESCAPE_BODY = "EscapeBody"
    CONTINUATION_MARKER = "ContinuationMarker"
    ERROR_REGION = "ErrorRegion"

    def as_str(self) -> str:
        """Stable query and protocol name (lib.rs)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> PropertiesSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs)."""
        try:
            return cls(name)
        except ValueError:
            return None


class PropertiesValueState(enum.Enum):
    """Semantic empty/present state with exact separator provenance
    (lib.rs; RFC 0010 §6)."""

    IMPLICIT_EMPTY = "ImplicitEmpty"
    EXPLICIT_EMPTY = "ExplicitEmpty"
    PRESENT = "Present"


class PropertiesLogicalLineKind(enum.Enum):
    """Kind of one logical Properties record (lib.rs)."""

    PROPERTY = "Property"
    ERROR = "Error"


class PropertiesEscapeKind(enum.Enum):
    """Kind of one retained escape occurrence (lib.rs;
    RFC 0010 §7)."""

    NAMED = "Named"
    BACKSLASH = "Backslash"
    UNICODE = "Unicode"
    DROPPED_BACKSLASH = "DroppedBackslash"


# -- frozen query domain ids (RFC 0010 §10; query.rs) ------

NATIVE_QUERY_DOMAIN_ID = "java-properties.native-semantic-query"
SYNTAX_QUERY_DOMAIN_ID = "java-properties.lossless-syntax-query"
