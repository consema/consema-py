"""Java Properties parse limits and the explicit source-encoding selection.

Authority:

- ``PropertiesParseLimits``: crates/consema-properties/src/lib.rs:61-98
  (field vocabulary) and lib.rs:100-122 (frozen defaults) — the common
  ParseLimits plus the sixteen format-owned bounds; RFC 0010 §14
  (docs/rfcs/0010-java-properties-profiles-v1.md:415-432) requires them
  to bound raw/decoded bytes, natural/logical lines, property/comment/
  escape/Unicode-escape counts, Java code units, duplicate-group members,
  syntax pieces, diagnostics, and recovery regions.
- ``PropertiesEncodingSelection``: lib.rs:52-59 — Reader input decoded
  through one exact published text encoding versus the InputStream-
  compatible one-byte ISO-8859-1 mapping with BOM bytes as content;
  RFC 0010 §3 (docs/rfcs/0010-...:65-106).

The profile is always selected by the caller; a ``.properties`` extension
does not choose between a character Reader and a Latin-1 byte stream, and
UTF-8 is not silently assumed for the InputStream profile (RFC 0010 §1,
docs/rfcs/0010-...:28-31).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding

# Frozen defaults, crates/consema-properties/src/lib.rs:104-120
_DEFAULT_MAX_DECODED_UTF8_BYTES = 128 * 1024 * 1024
_DEFAULT_MAX_DECODED_SCALARS = 64 * 1024 * 1024
_DEFAULT_MAX_NATURAL_LINES = 2_000_000
_DEFAULT_MAX_NATURAL_LINE_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_NATURAL_LINE_SCALARS = 2 * 1024 * 1024
_DEFAULT_MAX_LOGICAL_LINES = 2_000_000
_DEFAULT_MAX_LOGICAL_LINE_NATURAL_LINES = 100_000
_DEFAULT_MAX_LOGICAL_LINE_SCALARS = 16 * 1024 * 1024
_DEFAULT_MAX_PROPERTIES = 2_000_000
_DEFAULT_MAX_COMMENTS = 2_000_000
_DEFAULT_MAX_ESCAPES = 8_000_000
_DEFAULT_MAX_UNICODE_ESCAPES = 8_000_000
_DEFAULT_MAX_JAVA_CODE_UNITS_PER_STRING = 16 * 1024 * 1024
_DEFAULT_MAX_TOTAL_JAVA_CODE_UNITS = 64 * 1024 * 1024
_DEFAULT_MAX_DUPLICATE_GROUP_MEMBERS = 1_000_000
_DEFAULT_MAX_RECOVERY_REGIONS = 100_000


@dataclass(frozen=True, slots=True)
class PropertiesParseLimits:
    """Java Properties parse and recovery limits (lib.rs:61-122).

    Exceeding any limit is a fatal formation failure; there is no
    truncation-then-success (RFC 0016 §6; RFC 0010 §8). Continuation
    cannot amplify beyond the bounded source and logical-line limits;
    Unicode escapes produce exactly one code unit and are never
    recursively expanded (RFC 0010 §14).
    """

    common: ParseLimits = field(default_factory=ParseLimits)
    max_decoded_utf8_bytes: int = _DEFAULT_MAX_DECODED_UTF8_BYTES
    max_decoded_scalars: int = _DEFAULT_MAX_DECODED_SCALARS
    max_natural_lines: int = _DEFAULT_MAX_NATURAL_LINES
    max_natural_line_bytes: int = _DEFAULT_MAX_NATURAL_LINE_BYTES
    max_natural_line_scalars: int = _DEFAULT_MAX_NATURAL_LINE_SCALARS
    max_logical_lines: int = _DEFAULT_MAX_LOGICAL_LINES
    max_logical_line_natural_lines: int = _DEFAULT_MAX_LOGICAL_LINE_NATURAL_LINES
    max_logical_line_scalars: int = _DEFAULT_MAX_LOGICAL_LINE_SCALARS
    max_properties: int = _DEFAULT_MAX_PROPERTIES
    max_comments: int = _DEFAULT_MAX_COMMENTS
    max_escapes: int = _DEFAULT_MAX_ESCAPES
    max_unicode_escapes: int = _DEFAULT_MAX_UNICODE_ESCAPES
    max_java_code_units_per_string: int = _DEFAULT_MAX_JAVA_CODE_UNITS_PER_STRING
    max_total_java_code_units: int = _DEFAULT_MAX_TOTAL_JAVA_CODE_UNITS
    max_duplicate_group_members: int = _DEFAULT_MAX_DUPLICATE_GROUP_MEMBERS
    max_recovery_regions: int = _DEFAULT_MAX_RECOVERY_REGIONS


class PropertiesEncodingSelectionKind(enum.Enum):
    """Explicit source contract kind (lib.rs:52-59)."""

    READER = "reader"
    LATIN1 = "latin1"


@dataclass(frozen=True, slots=True)
class PropertiesEncodingSelection:
    """Explicit source contract; no extension, locale, or platform default
    is consulted (lib.rs:52-59; RFC 0010 §3).

    ``reader(encoding)`` decodes the Reader input through the exact
    published text encoding (a text encoding only); ``latin1()`` applies
    the InputStream-compatible one-byte ISO-8859-1 mapping with BOM bytes
    treated as content.
    """

    kind: PropertiesEncodingSelectionKind
    encoding: SourceEncoding | None = None

    def __post_init__(self) -> None:
        if self.kind is PropertiesEncodingSelectionKind.READER:
            if self.encoding is None or not self.encoding.is_text:
                raise ValueError("reader selection requires an exact text encoding")
        elif self.encoding is not None:
            raise ValueError("latin1 selection carries no encoding")

    @classmethod
    def reader(cls, encoding: SourceEncoding) -> PropertiesEncodingSelection:
        """Reader input decoded through one exact published text encoding
        (RFC 0010 §3.1)."""
        return cls(kind=PropertiesEncodingSelectionKind.READER, encoding=encoding)

    @classmethod
    def latin1(cls) -> PropertiesEncodingSelection:
        """InputStream-compatible Latin-1 bytes with marker bytes as
        content (RFC 0010 §3.2)."""
        return cls(kind=PropertiesEncodingSelectionKind.LATIN1)
