"""Closed YAML kind vocabularies: profiles, syntax pieces, node kinds,
scalar presentation styles, and resolved scalar categories.

Authority (language-neutral first; Rust only for registry arbitration):

- Profiles: RFC 0007 §1 (docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md:14-33)
  — yaml.1.2-core@1 (YAML 1.2.2 presentation grammar + Core schema) and
  yaml.1.1-compat@1 (compatible presentation + frozen 1.1 scalar
  resolution); the profile id table is crates/consema-yaml/src/lib.rs:241-257.
- Syntax kinds: RFC 0007 §9 (lines 224-228: BOM, Directive,
  DocumentMarker, Indicator, Anchor, Alias, Tag, Scalar, Whitespace,
  Newline, Comment, ErrorRegion "with stable style subfacts"); the closed
  spellings are frozen by lib.rs:64-116 and their stable names by
  lib.rs:167-231.
- Node kinds: lib.rs:118-127 (Scalar/Sequence/Mapping); scalar styles
  lib.rs:130-142 (Plain/SingleQuoted/DoubleQuoted/Literal/Folded);
  scalar categories lib.rs:145-165 (Null/Boolean/Integer/Float/String/
  Timestamp/Binary/Custom/Tagged).

The profile wire identifiers (``yaml.1.2-core@1``, ``yaml.1.1-compat@1``)
are referenced verbatim by conformance/vectors/yaml-v1.json:3 and by every
vector case input.
"""

from __future__ import annotations

import enum


class YamlProfile(enum.Enum):
    """Frozen YAML language profile (lib.rs:55-61; RFC 0007 §1)."""

    YAML12_CORE_V1 = "yaml.1.2-core@1"
    YAML11_COMPAT_V1 = "yaml.1.1-compat@1"

    def id(self) -> tuple[str, int]:
        """Exact ``(id, version)`` pair (lib.rs:242-249)."""
        return {
            YamlProfile.YAML12_CORE_V1: ("yaml.1.2-core", 1),
            YamlProfile.YAML11_COMPAT_V1: ("yaml.1.1-compat", 1),
        }[self]

    def accepted_version(self) -> str:
        """Accepted ``%YAML`` version spelling (lib.rs:251-257)."""
        return {
            YamlProfile.YAML12_CORE_V1: "1.2",
            YamlProfile.YAML11_COMPAT_V1: "1.1",
        }[self]


class YamlSyntaxKind(enum.Enum):
    """Closed YAML lossless presentation-piece classification
    (lib.rs:64-116; stable names lib.rs:167-231).

    Values are the stable query/protocol spellings.
    """

    BOM = "Bom"
    WHITESPACE = "Whitespace"
    NEWLINE = "Newline"
    COMMENT = "Comment"
    DIRECTIVE = "Directive"
    DOCUMENT_START = "DocumentStart"
    DOCUMENT_END = "DocumentEnd"
    SEQUENCE_ENTRY = "SequenceEntry"
    EXPLICIT_KEY = "ExplicitKey"
    MAPPING_VALUE = "MappingValue"
    FLOW_SEQUENCE_START = "FlowSequenceStart"
    FLOW_SEQUENCE_END = "FlowSequenceEnd"
    FLOW_MAPPING_START = "FlowMappingStart"
    FLOW_MAPPING_END = "FlowMappingEnd"
    FLOW_ENTRY = "FlowEntry"
    ANCHOR = "Anchor"
    ALIAS = "Alias"
    TAG = "Tag"
    PLAIN_SCALAR = "PlainScalar"
    SINGLE_QUOTED_SCALAR = "SingleQuotedScalar"
    DOUBLE_QUOTED_SCALAR = "DoubleQuotedScalar"
    LITERAL_BLOCK_HEADER = "LiteralBlockHeader"
    FOLDED_BLOCK_HEADER = "FoldedBlockHeader"
    BLOCK_SCALAR_CONTENT = "BlockScalarContent"
    ERROR_REGION = "ErrorRegion"

    @classmethod
    def from_name(cls, name: str) -> YamlSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs:200-231)."""
        try:
            return cls(name)
        except ValueError:
            return None

    @property
    def is_trivia(self) -> bool:
        """BOM, whitespace, newline, and comment pieces are trivia
        (lib.rs:233-238)."""
        return self in (
            YamlSyntaxKind.BOM,
            YamlSyntaxKind.WHITESPACE,
            YamlSyntaxKind.NEWLINE,
            YamlSyntaxKind.COMMENT,
        )


class YamlNodeKind(enum.Enum):
    """YAML native representation node kind (lib.rs:118-127)."""

    SCALAR = "Scalar"
    SEQUENCE = "Sequence"
    MAPPING = "Mapping"


class YamlScalarStyle(enum.Enum):
    """Exact scalar presentation style (lib.rs:130-142)."""

    PLAIN = "Plain"
    SINGLE_QUOTED = "SingleQuoted"
    DOUBLE_QUOTED = "DoubleQuoted"
    LITERAL = "Literal"
    FOLDED = "Folded"


class YamlScalarKind(enum.Enum):
    """Resolved native scalar semantic category (lib.rs:145-165)."""

    NULL = "Null"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    FLOAT = "Float"
    STRING = "String"
    TIMESTAMP = "Timestamp"
    BINARY = "Binary"
    CUSTOM = "Custom"
    TAGGED = "Tagged"
