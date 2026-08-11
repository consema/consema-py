"""The frozen Java Properties profile, syntax-kind, and value-state
vocabularies.

Frozen names/numbers with authority citations (language-neutral first;
Rust only for registry/byte arbitration):

- ``PropertiesProfile``: the two profile identities —
  java-properties.reader@1 / java-properties.latin1@1 — and their source
  contracts — RFC 0010 §1/§3 (docs/rfcs/0010-java-properties-profiles-
  v1.md:14-31, 65-106) and crates/consema-properties/src/lib.rs:33-50
  (enum + id()).
- ``PropertiesSyntaxKind``: the closed 12-kind lossless classification
  ("Bom", "Whitespace", "LineBreak", "CommentMarker", "CommentText",
  "Key", "Separator", "Value", "EscapeMarker", "EscapeBody",
  "ContinuationMarker", "ErrorRegion") — RFC 0010 §10
  (docs/rfcs/0010-...:296-308) and lib.rs:208-274 (enum, as_str,
  from_name).
- ``PropertiesValueState``: ImplicitEmpty | ExplicitEmpty | Present —
  RFC 0010 §6 (docs/rfcs/0010-...:174-181) and lib.rs:276-285.
- ``PropertiesLogicalLineKind``: Property | Error — lib.rs:287-294.
- ``PropertiesEscapeKind``: Named | Backslash | Unicode |
  DroppedBackslash — RFC 0010 §7 (docs/rfcs/0010-...:183-197) and
  lib.rs:296-307.
- The query domains ``java-properties.native-semantic-query@1`` and
  ``java-properties.lossless-syntax-query@1`` with their eight + four
  operators — RFC 0010 §10 (docs/rfcs/0010-...:269-308) and
  crates/consema-properties/src/query.rs:124-150, 167-211.

The profile ids are the frozen language-neutral spellings also pinned by
the vector suite ``consema.java-properties.conformance@1``
(conformance/vectors/java-properties-v1.json:2-3). go/properties is a
cross-reference only.
"""

from __future__ import annotations

import enum

from consema.document.ids import ProfileId


class PropertiesProfile(enum.Enum):
    """Frozen Java Properties formation profile (lib.rs:33-39).

    ``ReaderV1`` corresponds to ``Properties.load(Reader)`` (character
    source under an explicit published text encoding); ``Latin1V1``
    corresponds to ``Properties.load(InputStream)`` (every byte maps
    one-to-one to U+0000..U+00FF, BOM bytes are content).
    """

    READER_V1 = "java-properties.reader"
    LATIN1_V1 = "java-properties.latin1"

    # -- profile identity (lib.rs:41-50) -----------------------------------

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:44-49)."""
        return ProfileId.new(self.value, 1)

    def is_latin1(self) -> bool:
        """Whether the InputStream-compatible one-byte profile is selected
        (RFC 0010 §3.2)."""
        return self is PropertiesProfile.LATIN1_V1


class PropertiesSyntaxKind(enum.Enum):
    """Closed lossless syntax-piece classification (lib.rs:208-274; the
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
        """Stable query and protocol name (lib.rs:240-254)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> PropertiesSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs:257-273)."""
        try:
            return cls(name)
        except ValueError:
            return None


class PropertiesValueState(enum.Enum):
    """Semantic empty/present state with exact separator provenance
    (lib.rs:276-285; RFC 0010 §6)."""

    IMPLICIT_EMPTY = "ImplicitEmpty"
    EXPLICIT_EMPTY = "ExplicitEmpty"
    PRESENT = "Present"


class PropertiesLogicalLineKind(enum.Enum):
    """Kind of one logical Properties record (lib.rs:287-294)."""

    PROPERTY = "Property"
    ERROR = "Error"


class PropertiesEscapeKind(enum.Enum):
    """Kind of one retained escape occurrence (lib.rs:296-307;
    RFC 0010 §7)."""

    NAMED = "Named"
    BACKSLASH = "Backslash"
    UNICODE = "Unicode"
    DROPPED_BACKSLASH = "DroppedBackslash"


# -- frozen query domain ids (RFC 0010 §10; query.rs:124-150, 167-211) ------

NATIVE_QUERY_DOMAIN_ID = "java-properties.native-semantic-query"
SYNTAX_QUERY_DOMAIN_ID = "java-properties.lossless-syntax-query"
