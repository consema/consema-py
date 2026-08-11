"""The frozen INI profile, syntax-kind, value-state, and quote vocabularies.

Frozen names/numbers with authority citations (language-neutral first; Rust
only for registry/byte arbitration):

- ``IniProfile``: the three profile identities — crates/consema-ini/src/
  lib.rs:37-44 (enum), lib.rs:49-55 (id()); the profile ids
  ini.portable@1 / ini.windows@1 / ini.python-configparser@1 are the frozen
  language-neutral spellings (RFC 0009 §1,
  docs/rfcs/0009-ini-family-profiles-v1.md:22-26).
- ``IniSyntaxKind``: the closed 14-kind lossless classification with the
  exact stable names ("Bom", "Whitespace", "LineBreak", "CommentMarker",
  "CommentText", "SectionOpen", "SectionName", "SectionClose", "EntryKey",
  "Delimiter", "Quote", "EntryValue", "ContinuationMarker", "ErrorRegion")
  — lib.rs:123-152 (enum), lib.rs:157-173 (as_str), lib.rs:176-194
  (from_name); the lossless syntax domain lists exactly this vocabulary
  (RFC 0009 §9, docs/rfcs/0009-...:306-313).
- ``IniValueState``: Missing | Empty | Present — lib.rs:198-206; the
  ``ini.entry-value-state-is@1`` argument accepts exactly these three
  spellings (RFC 0009 §9, docs/rfcs/0009-...:330-331).
- ``IniQuoteStyle``: None | Single | Double — lib.rs:209-217 (Windows
  profile outer-quote facts, RFC 0009 §6, docs/rfcs/0009-...:199-202).
- ``IniLogicalLineKind``: Section | Entry | Error — lib.rs:220-228.
- ``IniEncodingSelection``: ProfileDefault | Explicit — lib.rs:59-65
  (RFC 0009 §3: no-BOM bytes never imply the machine's active code page;
  the caller must select an explicit code page).
- ``IniParseLimits`` defaults — lib.rs:100-118 (common ParseLimits plus
  the INI-specific decoded/line/record/group limits; the resource names
  are pinned by conformance/vectors/ini-v1.json:108-128,
  resource.formation-limit-matrix).
- Windows name/value character tables: parser.rs:1336-1339 (is_windows_name)
  and materialization.rs:869-872, 874-888 (is_windows_name /
  windows_value_needs_quotes); portable tables parser.rs:1327-1334.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import ProfileId
from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding

# Frozen defaults, crates/consema-ini/src/lib.rs:100-118.
_DEFAULT_MAX_DECODED_UTF8_BYTES = 128 * 1024 * 1024
_DEFAULT_MAX_DECODED_SCALARS = 64 * 1024 * 1024
_DEFAULT_MAX_PHYSICAL_LINES = 2_000_000
_DEFAULT_MAX_PHYSICAL_LINE_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_PHYSICAL_LINE_SCALARS = 2 * 1024 * 1024
_DEFAULT_MAX_LOGICAL_LINES = 2_000_000
_DEFAULT_MAX_LOGICAL_LINE_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_LOGICAL_LINE_SCALARS = 8 * 1024 * 1024
_DEFAULT_MAX_CONTINUATION_LINES = 100_000
_DEFAULT_MAX_SECTIONS = 1_000_000
_DEFAULT_MAX_ENTRIES = 1_000_000
_DEFAULT_MAX_DUPLICATE_GROUP_MEMBERS = 100_000
_DEFAULT_MAX_RECOVERY_REGIONS = 100_000


class IniProfile(enum.Enum):
    """Frozen INI formation profile (crates/consema-ini/src/lib.rs:37-44).

    The profiles share bounded source decoding, physical-line scanning,
    immutable snapshot identity, lossless coverage, transaction, proof, and
    patch infrastructure; they do not share accepted encoding, delimiter,
    comment, continuation, case-equivalence, quote, duplicate, or canonical
    generation rules (RFC 0009 §1, docs/rfcs/0009-...:29-32).
    """

    PORTABLE_V1 = "ini.portable"
    WINDOWS_V1 = "ini.windows"
    PYTHON_CONFIGPARSER_V1 = "ini.python-configparser"

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:49-55)."""
        return ProfileId.new(self.value, 1)


class IniEncodingSelection:
    """Explicit source-encoding selection; no host locale is consulted
    (crates/consema-ini/src/lib.rs:59-65; RFC 0009 §3)."""

    __slots__ = ("kind", "encoding")

    def __init__(self, kind: str, encoding: SourceEncoding | None = None) -> None:
        if kind not in ("ProfileDefault", "Explicit"):
            raise ValueError(f"unknown INI encoding selection {kind!r}")
        if kind == "Explicit" and encoding is None:
            raise ValueError("Explicit selection requires an encoding")
        self.kind = kind
        self.encoding = encoding

    @classmethod
    def profile_default(cls) -> IniEncodingSelection:
        """Apply only the selected profile's frozen default and BOM rules."""
        return cls("ProfileDefault")

    @classmethod
    def explicit(cls, encoding: SourceEncoding) -> IniEncodingSelection:
        """Use one caller-selected source encoding."""
        return cls("Explicit", encoding)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IniEncodingSelection):
            return NotImplemented
        return self.kind == other.kind and self.encoding == other.encoding

    def __hash__(self) -> int:
        return hash((self.kind, self.encoding))

    def __repr__(self) -> str:
        if self.kind == "ProfileDefault":
            return "IniEncodingSelection(ProfileDefault)"
        return f"IniEncodingSelection(Explicit({self.encoding.as_str}))"


class IniSyntaxKind(enum.Enum):
    """Closed INI lossless syntax-piece classification
    (crates/consema-ini/src/lib.rs:123-152; RFC 0009 §9,
    docs/rfcs/0009-...:306-313)."""

    BOM = "Bom"
    WHITESPACE = "Whitespace"
    LINE_BREAK = "LineBreak"
    COMMENT_MARKER = "CommentMarker"
    COMMENT_TEXT = "CommentText"
    SECTION_OPEN = "SectionOpen"
    SECTION_NAME = "SectionName"
    SECTION_CLOSE = "SectionClose"
    ENTRY_KEY = "EntryKey"
    DELIMITER = "Delimiter"
    QUOTE = "Quote"
    ENTRY_VALUE = "EntryValue"
    CONTINUATION_MARKER = "ContinuationMarker"
    ERROR_REGION = "ErrorRegion"

    def as_str(self) -> str:
        """Stable query and protocol name (lib.rs:157-173)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> IniSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs:176-194)."""
        try:
            return cls(name)
        except ValueError:
            return None


class IniValueState(enum.Enum):
    """Native value-presence fact (lib.rs:198-206).

    ``Missing`` is only carried by recovered error records in v1;
    ``key=`` is Empty, never converted to Missing (RFC 0009 §5,
    docs/rfcs/0009-...:176-177).
    """

    MISSING = "Missing"
    EMPTY = "Empty"
    PRESENT = "Present"


class IniQuoteStyle(enum.Enum):
    """Profile-recognized outer quote style (lib.rs:209-217; RFC 0009 §6)."""

    NONE = "None"
    SINGLE = "Single"
    DOUBLE = "Double"


class IniLogicalLineKind(enum.Enum):
    """Kind of one logical INI record (lib.rs:220-228)."""

    SECTION = "Section"
    ENTRY = "Entry"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class IniParseLimits:
    """INI-specific parse and recovery limits (lib.rs:69-98).

    ``common`` holds the shared source/node/piece/nesting/diagnostic limits;
    the remaining fields bound decoded text, physical/logical lines,
    continuations, sections, entries, duplicate-group members, and recovery
    regions. Exceeding any limit is a fatal formation failure; there is no
    truncation-then-success (RFC 0009 §13, docs/rfcs/0009-...:476-489).
    """

    common: ParseLimits = ParseLimits()
    max_decoded_utf8_bytes: int = _DEFAULT_MAX_DECODED_UTF8_BYTES
    max_decoded_scalars: int = _DEFAULT_MAX_DECODED_SCALARS
    max_physical_lines: int = _DEFAULT_MAX_PHYSICAL_LINES
    max_physical_line_bytes: int = _DEFAULT_MAX_PHYSICAL_LINE_BYTES
    max_physical_line_scalars: int = _DEFAULT_MAX_PHYSICAL_LINE_SCALARS
    max_logical_lines: int = _DEFAULT_MAX_LOGICAL_LINES
    max_logical_line_bytes: int = _DEFAULT_MAX_LOGICAL_LINE_BYTES
    max_logical_line_scalars: int = _DEFAULT_MAX_LOGICAL_LINE_SCALARS
    max_continuation_lines: int = _DEFAULT_MAX_CONTINUATION_LINES
    max_sections: int = _DEFAULT_MAX_SECTIONS
    max_entries: int = _DEFAULT_MAX_ENTRIES
    max_duplicate_group_members: int = _DEFAULT_MAX_DUPLICATE_GROUP_MEMBERS
    max_recovery_regions: int = _DEFAULT_MAX_RECOVERY_REGIONS


# -- profile character tables -------------------------------------------------
# Transcribed verbatim from crates/consema-ini/src/parser.rs:1323-1339 and
# materialization.rs:860-888; these tables are the byte authority for every
# formation and edit name/value validation.


def is_horizontal(byte: int) -> bool:
    """Space or horizontal tab (parser.rs:1323-1325)."""
    return byte in (0x20, 0x09)


def is_portable_name(byte: int) -> bool:
    """ASCII alphanumeric plus ``_`` ``-`` ``.`` (parser.rs:1327-1329)."""
    return (
        0x30 <= byte <= 0x39
        or 0x41 <= byte <= 0x5A
        or 0x61 <= byte <= 0x7A
        or byte in (0x5F, 0x2D, 0x2E)
    )


def is_portable_value(byte: int) -> bool:
    """ASCII graphic excluding quote/backslash/colon/#/; plus space
    (parser.rs:1331-1334)."""
    return (0x21 <= byte <= 0x7E and byte not in (0x22, 0x27, 0x5C, 0x3A, 0x23, 0x3B)) or byte == 0x20


def is_windows_name(byte: int) -> bool:
    """ASCII graphic or space, excluding ``[`` ``]`` ``=`` NUL CR LF
    (parser.rs:1336-1339)."""
    return (
        (0x21 <= byte <= 0x7E or byte == 0x20)
        and byte not in (0x5B, 0x5D, 0x3D, 0x00, 0x0D, 0x0A)
    )


def windows_value_needs_quotes(value: str) -> bool:
    """Deterministic Windows quoting decision (materialization.rs:874-888).

    Quotes are needed exactly when the value starts or ends with horizontal
    whitespace, or when it begins and ends with the same quote character.
    """
    raw = value.encode("utf-8")
    first = raw[0] if raw else None
    last = raw[-1] if raw else None
    if first in (0x20, 0x09) or last in (0x20, 0x09):
        return True
    return (
        len(raw) >= 2
        and ((raw[0], raw[-1]) in ((0x27, 0x27), (0x22, 0x22)))
    )
