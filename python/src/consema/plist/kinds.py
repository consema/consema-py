"""The frozen plist profile, syntax-kind, string-status, and limit vocabularies.

Frozen names/numbers with authority citations (language-neutral first; Rust
only for registry/byte arbitration):

- ``PlistProfile``: the two profile identities — crates/consema-plist/src/
  lib.rs:76-81 (enum), lib.rs:83-92 (id()); the profile ids plist.xml@1 /
  plist.binary@1 are the frozen language-neutral spellings (RFC 0013 §1,
  docs/rfcs/0013-plist-family-profiles-v1.md:29-47).
- ``PlistEncodingSelection``: ProfileDefault | Explicit — lib.rs:104-110;
  the XML profile follows the RFC 0012 UTF-8/UTF-16 document-entity table
  (RFC 0013 §2.1, lines 59-76) and the binary profile admits only the
  opaque Binary source (RFC 0013 §2.2, lines 78-88).
- ``PlistSyntaxKind``: the closed 47-kind lossless classification with the
  exact stable names ("Bom", "Whitespace", "LineBreak", "DeclarationOpen",
  ..., "ErrorRegion") — parser_xml.rs:173-232 (enum), parser_xml.rs:233-302
  (as_str); the lossless syntax domain lists exactly this vocabulary
  (RFC 0013 §8.2, lines 565-582).
- ``PlistStringStatus``: WellFormedUnicode | UnpairedSurrogate —
  native.rs:39-44 (RFC 0013 §6, lines 488-492; the
  ``core.java-utf16-string@1`` wire pattern).
- ``RealWidth``: Float64 | Float32 — native.rs:219-224 (the binary-only
  ``0x22`` width fact, RFC 0013 §5.5, lines 350-354).
- ``PlistParseLimits`` defaults — lib.rs:168-194 (RFC 0013 §12, lines
  718-732); every limit failure is fatal and never masquerades as an empty
  tree or truncated data (hard gate 4).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import ProfileId
from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding

# Frozen defaults, crates/consema-plist/src/lib.rs:168-194.
_DEFAULT_MAX_DECODED_UTF8_BYTES = 128 * 1024 * 1024
_DEFAULT_MAX_DECODED_SCALARS = 64 * 1024 * 1024
_DEFAULT_MAX_OBJECT_COUNT = 1_000_000
_DEFAULT_MAX_CONTAINER_DEPTH = 256
_DEFAULT_MAX_DICT_ENTRIES = 1_000_000
_DEFAULT_MAX_ARRAY_ELEMENTS = 1_000_000
_DEFAULT_MAX_DUPLICATE_KEY_GROUP_MEMBERS = 1_000_000
_DEFAULT_MAX_STRING_CODE_UNITS = 16 * 1024 * 1024
_DEFAULT_MAX_DATA_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_UID_COUNT = 100_000
_DEFAULT_MAX_EXTENDED_SIZE_INTEGERS = 10_000
_DEFAULT_MAX_EXTENDED_SIZE_VALUE = 1_000_000
_DEFAULT_MAX_OFFSET_INT_SIZE = 8
_DEFAULT_MAX_OBJECT_REF_SIZE = 8
_DEFAULT_MAX_OFFSET_TABLE_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_SYNTAX_PIECES = 2_000_000
_DEFAULT_MAX_BINARY_FACTS = 2_000_000
_DEFAULT_MAX_CONVERSION_NODES = 1_000_000
_DEFAULT_MAX_REPORT_EVENTS = 100_000
_DEFAULT_MAX_RECOVERY_REGIONS = 100_000


class PlistProfile(enum.Enum):
    """Frozen plist formation profiles (crates/consema-plist/src/lib.rs:76-81).

    The profile is selected by the caller before formation; neither the
    ``bplist00`` magic number nor a ``.plist`` extension selects semantics
    (RFC 0013 §1, docs/rfcs/0013-...:40-46). The two profiles are format
    identities, not dialects of one format, and share one native value model
    (RFC 0013 §7).
    """

    XML_V1 = "plist.xml"
    BINARY_V1 = "plist.binary"

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:83-92)."""
        return ProfileId.new(self.value, 1)


class PlistEncodingSelection:
    """Explicit source-encoding selection (lib.rs:104-110).

    For the XML profile the selection follows the RFC 0012 source contract:
    no-BOM source defaults to UTF-8, and an explicit caller choice is
    evidence, not permission to contradict a BOM or a declaration (RFC 0013
    §2.1). The binary profile has no text encoding and no BOM; only
    ProfileDefault and Explicit(Binary) are consistent with it (RFC 0013
    §2.2).
    """

    __slots__ = ("kind", "encoding")

    def __init__(self, kind: str, encoding: SourceEncoding | None = None) -> None:
        if kind not in ("ProfileDefault", "Explicit"):
            raise ValueError(f"unknown plist encoding selection {kind!r}")
        if kind == "Explicit" and encoding is None:
            raise ValueError("Explicit selection requires an encoding")
        self.kind = kind
        self.encoding = encoding

    @classmethod
    def profile_default(cls) -> PlistEncodingSelection:
        """Apply only the frozen profile default and BOM rules."""
        return cls("ProfileDefault")

    @classmethod
    def explicit(cls, encoding: SourceEncoding) -> PlistEncodingSelection:
        """Use one caller-selected source encoding."""
        return cls("Explicit", encoding)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlistEncodingSelection):
            return NotImplemented
        return self.kind == other.kind and self.encoding == other.encoding

    def __hash__(self) -> int:
        return hash((self.kind, self.encoding))

    def __repr__(self) -> str:
        if self.kind == "ProfileDefault":
            return "PlistEncodingSelection(ProfileDefault)"
        return f"PlistEncodingSelection(Explicit({self.encoding.as_str}))"


class PlistStringStatus(enum.Enum):
    """Whether exact UTF-16 code units form Unicode scalar text
    (crates/consema-plist/src/native.rs:39-44; RFC 0013 §6)."""

    WELL_FORMED_UNICODE = "WellFormedUnicode"
    UNPAIRED_SURROGATE = "UnpairedSurrogate"


class RealWidth(enum.Enum):
    """Width fact of one exact IEEE 754 real payload (native.rs:219-224)."""

    FLOAT64 = "Float64"
    FLOAT32 = "Float32"


class PlistSyntaxKind(enum.Enum):
    """Closed plist XML lossless syntax-piece classification
    (crates/consema-plist/src/parser_xml.rs:173-232; RFC 0013 §8.2,
    docs/rfcs/0013-...:565-582).

    The 47-kind vocabulary is exactly the lossless syntax domain list; every
    non-empty raw byte of an XML source belongs to exactly one ordered
    structural piece with one of these kinds.
    """

    BOM = "Bom"
    WHITESPACE = "Whitespace"
    LINE_BREAK = "LineBreak"
    DECLARATION_OPEN = "DeclarationOpen"
    DECLARATION_NAME = "DeclarationName"
    DECLARATION_VALUE = "DeclarationValue"
    DECLARATION_CLOSE = "DeclarationClose"
    DOCTYPE_OPEN = "DoctypeOpen"
    DOCTYPE_BODY = "DoctypeBody"
    DOCTYPE_CLOSE = "DoctypeClose"
    PLIST_OPEN = "PlistOpen"
    PLIST_VERSION_NAME = "PlistVersionName"
    PLIST_VERSION_VALUE = "PlistVersionValue"
    PLIST_CLOSE = "PlistClose"
    DICT_OPEN = "DictOpen"
    DICT_CLOSE = "DictClose"
    KEY_OPEN = "KeyOpen"
    KEY_CLOSE = "KeyClose"
    ARRAY_OPEN = "ArrayOpen"
    ARRAY_CLOSE = "ArrayClose"
    STRING_OPEN = "StringOpen"
    STRING_CLOSE = "StringClose"
    INTEGER_OPEN = "IntegerOpen"
    INTEGER_CLOSE = "IntegerClose"
    REAL_OPEN = "RealOpen"
    REAL_CLOSE = "RealClose"
    DATE_OPEN = "DateOpen"
    DATE_CLOSE = "DateClose"
    DATA_OPEN = "DataOpen"
    DATA_CLOSE = "DataClose"
    TRUE = "True"
    FALSE = "False"
    TEXT = "Text"
    ENTITY_REFERENCE = "EntityReference"
    CHARACTER_REFERENCE = "CharacterReference"
    CDATA_OPEN = "CdataOpen"
    CDATA_TEXT = "CdataText"
    CDATA_CLOSE = "CdataClose"
    COMMENT_OPEN = "CommentOpen"
    COMMENT_TEXT = "CommentText"
    COMMENT_CLOSE = "CommentClose"
    PROCESSING_INSTRUCTION_OPEN = "ProcessingInstructionOpen"
    PROCESSING_INSTRUCTION_TARGET = "ProcessingInstructionTarget"
    PROCESSING_INSTRUCTION_CONTENT = "ProcessingInstructionContent"
    PROCESSING_INSTRUCTION_CLOSE = "ProcessingInstructionClose"
    ERROR_REGION = "ErrorRegion"

    def as_str(self) -> str:
        """Stable query and protocol name (parser_xml.rs:233-302)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> PlistSyntaxKind | None:
        """Resolves one exact stable kind name."""
        try:
            return cls(name)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class PlistParseLimits:
    """Plist-specific formation, structure, recovery, and conversion limits
    (lib.rs:114-166; RFC 0013 §12, docs/rfcs/0013-...:718-732).

    ``common`` holds the shared source/node/nesting/token/diagnostic limits
    (including ``max_source_bytes``; the 42-byte binary minimum is far below
    the default). Every limit failure is a fatal formation failure or an
    atomic operation failure (hard gate 4).
    """

    common: ParseLimits = ParseLimits()
    max_decoded_utf8_bytes: int = _DEFAULT_MAX_DECODED_UTF8_BYTES
    max_decoded_scalars: int = _DEFAULT_MAX_DECODED_SCALARS
    max_object_count: int = _DEFAULT_MAX_OBJECT_COUNT
    max_container_depth: int = _DEFAULT_MAX_CONTAINER_DEPTH
    max_dict_entries: int = _DEFAULT_MAX_DICT_ENTRIES
    max_array_elements: int = _DEFAULT_MAX_ARRAY_ELEMENTS
    max_duplicate_key_group_members: int = _DEFAULT_MAX_DUPLICATE_KEY_GROUP_MEMBERS
    max_string_code_units: int = _DEFAULT_MAX_STRING_CODE_UNITS
    max_data_bytes: int = _DEFAULT_MAX_DATA_BYTES
    max_uid_count: int = _DEFAULT_MAX_UID_COUNT
    max_extended_size_integers: int = _DEFAULT_MAX_EXTENDED_SIZE_INTEGERS
    max_extended_size_value: int = _DEFAULT_MAX_EXTENDED_SIZE_VALUE
    max_offset_int_size: int = _DEFAULT_MAX_OFFSET_INT_SIZE
    max_object_ref_size: int = _DEFAULT_MAX_OBJECT_REF_SIZE
    max_offset_table_bytes: int = _DEFAULT_MAX_OFFSET_TABLE_BYTES
    max_syntax_pieces: int = _DEFAULT_MAX_SYNTAX_PIECES
    max_binary_facts: int = _DEFAULT_MAX_BINARY_FACTS
    max_conversion_nodes: int = _DEFAULT_MAX_CONVERSION_NODES
    max_report_events: int = _DEFAULT_MAX_REPORT_EVENTS
    max_recovery_regions: int = _DEFAULT_MAX_RECOVERY_REGIONS

    def arena_limits(self) -> tuple[int, int]:
        """(max_objects, max_container_depth) of the native arena
        (lib.rs:196-205)."""
        return (self.max_object_count, self.max_container_depth)
