"""The closed XML lossless syntax kind vocabulary (RFC 0012 §7).

Authority:

- RFC 0012 §7 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:258-283) freezes
  the v1 kind set — Bom, Whitespace, LineBreak, DeclarationOpen,
  DeclarationName, DeclarationValue, DeclarationClose, DoctypeOpen,
  DoctypeName, DtdMarkup, DoctypeClose, TagOpen, TagClose,
  EmptyElementClose, EndTagOpen, Prefix, LocalName, Colon, AttributeName,
  Equals, Quote, AttributeValue, NamespaceDeclaration, Text,
  EntityReference, CharacterReference, CdataOpen, CdataText, CdataClose,
  CommentOpen, CommentText, CommentClose, ProcessingInstructionOpen,
  ProcessingInstructionTarget, ProcessingInstructionContent,
  ProcessingInstructionClose, ErrorRegion — with the rule that format kinds
  align one-to-one with the common LosslessStructuralIndex pieces.
- The stable kind names transcribe
  crates/consema-xml/src/document.rs:801-889 (XmlSyntaxKind::as_str /
  from_name); the lossless-syntax query protocol validates the same
  vocabulary (consema.protocol query.py:1109-1121).
- consema.document LosslessStructuralIndex (document/structural.py) owns
  the common token/trivia/error-region piece classification; this module
  only owns the format kind parallel to it.

The protocol agent owns the wire validation of kind names
(consema.protocol query.py); this module is the family-side resolver.
"""

from __future__ import annotations

import enum


class XmlSyntaxKind(enum.Enum):
    """One lossless XML syntax category (RFC 0012 §7; document.rs:18-94)."""

    BOM = "bom"
    WHITESPACE = "whitespace"
    LINE_BREAK = "line-break"
    DECLARATION_OPEN = "declaration-open"
    DECLARATION_NAME = "declaration-name"
    DECLARATION_VALUE = "declaration-value"
    DECLARATION_CLOSE = "declaration-close"
    DOCTYPE_OPEN = "doctype-open"
    DOCTYPE_NAME = "doctype-name"
    DTD_MARKUP = "dtd-markup"
    DOCTYPE_CLOSE = "doctype-close"
    TAG_OPEN = "tag-open"
    TAG_CLOSE = "tag-close"
    EMPTY_ELEMENT_CLOSE = "empty-element-close"
    END_TAG_OPEN = "end-tag-open"
    PREFIX = "prefix"
    LOCAL_NAME = "local-name"
    COLON = "colon"
    ATTRIBUTE_NAME = "attribute-name"
    EQUALS = "equals"
    QUOTE = "quote"
    ATTRIBUTE_VALUE = "attribute-value"
    NAMESPACE_DECLARATION = "namespace-declaration"
    TEXT = "text"
    ENTITY_REFERENCE = "entity-reference"
    CHARACTER_REFERENCE = "character-reference"
    CDATA_OPEN = "cdata-open"
    CDATA_TEXT = "cdata-text"
    CDATA_CLOSE = "cdata-close"
    COMMENT_OPEN = "comment-open"
    COMMENT_TEXT = "comment-text"
    COMMENT_CLOSE = "comment-close"
    PROCESSING_INSTRUCTION_OPEN = "processing-instruction-open"
    PROCESSING_INSTRUCTION_TARGET = "processing-instruction-target"
    PROCESSING_INSTRUCTION_CONTENT = "processing-instruction-content"
    PROCESSING_INSTRUCTION_CLOSE = "processing-instruction-close"
    ERROR_REGION = "error-region"

    @property
    def as_str(self) -> str:
        """Stable kind name used by the lossless syntax query protocol
        (document.rs:801-844)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> XmlSyntaxKind | None:
        """Resolves a stable kind name (document.rs:846-889)."""
        try:
            return cls(name)
        except ValueError:
            return None
