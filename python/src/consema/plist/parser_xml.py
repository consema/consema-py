"""``plist.xml@1`` formation: the plist value vocabulary as XML 1.0
(RFC 0013 §2.1, §3, §4).

Authority (Rust arbitration for exact byte semantics and recovery):

- Source contract: crates/consema-plist/src/parser_xml.rs:396-460 — the
  RFC 0013 §2.1 document-entity table (no-BOM defaults to UTF-8; only
  UTF-8 / UTF-16LE-with-BOM / UTF-16BE-with-BOM are admitted; an
  incompatible selection is a fatal ``plist.xml.encoding@1`` failure).
- Tokenization and grammar: the state machine admits the prolog
  (declaration, comments, PIs), the optional exact Apple DOCTYPE
  (parser_xml.rs:999-1105, RFC 0013 §4.1), the ``<plist version="1.0">``
  root (parser_xml.rs:1107-1340, RFC 0013 §4.2), the closed element
  vocabulary (parser_xml.rs:503-584 classify_element), value grammars
  (parse_integer 2453-2516, parse_real 2521-2578, parse_date 2580-2632,
  decode_base64 2684-2757), text/reference resolution
  (resolve_fragments 1925-2060, RFC 0013 §4.9), and the trailing-content
  rule (stateAfterElements, RFC 0013 §4.10).
- Syntax pieces: the 47-kind classification of RFC 0013 §8.2 with the
  root open tag partition (PlistOpen on the name, Whitespace separator,
  PlistVersionName, PlistVersionValue, second PlistOpen on the closing
  ``>``); piece kinds and gap assembly parser_xml.rs:173-302, 2224-2316.
- Recovery: a tokenizer error becomes one deterministic error region plus
  ``plist.parse.well-formedness@1`` and the stream resumes at the next
  markup (parser_xml.rs:688-719, 2118-2149); every grammar violation is a
  Recovered diagnostic with the frozen code, and only independently proven
  constructs enter the native arena (RFC 0013 §3). Arena ordinals are
  close-tag order (parser_xml.rs:1602-1779).
- Limits: every configured limit is fatal (RFC 0013 §12, hard gate 4).

The tokenizer indexes the decoded text by Unicode scalar offsets and maps
every boundary back to exact raw bytes via
``SourceSnapshot.raw_byte_at(DecodedOffset.unicode_scalar(...))``, which is
exact for UTF-8 and UTF-16 sources alike (RFC 0013 §2.1). A leading decoded
U+FEFF (the UTF-8 BOM, or the UTF-16 BOM code unit) is skipped exactly like
the frozen tokenizer stream skip, and the raw BOM bytes form one Bom trivia
piece.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.source import (
    DecodedOffset,
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    NodeRef,
    NodeRole,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.plist.errors import (
    PlistDiagnostic,
    PlistFormationFailure,
    PlistFormationFailureKind,
    PlistSeverity,
    sort_diagnostics,
)
from consema.plist.kinds import (
    PlistEncodingSelection,
    PlistParseLimits,
    PlistSyntaxKind,
)
from consema.plist.native import (
    PlistArenaError,
    PlistArenaErrorKind,
    PlistArenaLimits,
    PlistArray,
    PlistBoolean,
    PlistData,
    PlistDate,
    PlistDict,
    PlistDictEntry,
    PlistDocument,
    PlistDocumentBuilder,
    PlistInteger,
    PlistKey,
    PlistReal,
    PlistString,
    PlistValue,
    PlistValueRef,
)
from consema.protocol.error_registry import DiagnosticCategory

_DECLARATION_OPEN_BYTES = len("<?xml")
_DOCTYPE_OPEN_BYTES = len("<!DOCTYPE")
_CDATA_OPEN_BYTES = len("<![CDATA[")
_COMMENT_OPEN_BYTES = len("<!--")

_PLIST_DOCTYPE_PUBLIC = "-//Apple//DTD PLIST 1.0//EN"
_PLIST_DOCTYPE_SYSTEM = "http://www.apple.com/DTDs/PropertyList-1.0.dtd"
_PLIST_VERSION = "1.0"


def _is_ws(character: str) -> bool:
    return character in (" ", "\t", "\n", "\r")


def _is_ws_byte(byte: int) -> bool:
    return byte in (0x20, 0x09, 0x0A, 0x0D)


def _is_xml_char(character: str) -> bool:
    codepoint = ord(character)
    return (
        character in ("\t", "\n", "\r")
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _is_name_start(character: str) -> bool:
    """XML 1.0 NameStartChar admission (parser_xml.rs tokenizer)."""
    codepoint = ord(character)
    return (
        character == "_"
        or "a" <= character <= "z"
        or "A" <= character <= "Z"
        or 0xC0 <= codepoint <= 0xD6
        or 0xD8 <= codepoint <= 0xF6
        or 0xF8 <= codepoint <= 0x2FF
        or 0x370 <= codepoint <= 0x37D
        or 0x37F <= codepoint <= 0x1FFF
        or 0x200C <= codepoint <= 0x200D
        or 0x2070 <= codepoint <= 0x218F
        or 0x2C00 <= codepoint <= 0x2FEF
        or 0x3001 <= codepoint <= 0xD7FF
        or 0xF900 <= codepoint <= 0xFDCF
        or 0xFDF0 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0xEFFFF
    )


def _is_name_char(character: str) -> bool:
    codepoint = ord(character)
    return (
        _is_name_start(character)
        or ("0" <= character <= "9")
        or character in ("-", ".", "·")
        or 0x0300 <= codepoint <= 0x036F
        or 0x203F <= codepoint <= 0x2040
    )


# ---------------------------------------------------------------------------
# Tokens and tokenizer (state machine over decoded scalar offsets)
# ---------------------------------------------------------------------------


class _TokenKind(enum.Enum):
    DECLARATION = "declaration"
    PROCESSING_INSTRUCTION = "processing-instruction"
    COMMENT = "comment"
    DTD_START = "dtd-start"
    DTD_END = "dtd-end"
    ELEMENT_START = "element-start"
    ATTRIBUTE = "attribute"
    ELEMENT_END = "element-end"
    TEXT = "text"
    CDATA = "cdata"


class _EndKind(enum.Enum):
    OPEN = "open"
    EMPTY = "empty"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _TokenKind
    start: int
    end: int
    prefix: tuple[int, int] | None = None
    local: tuple[int, int] | None = None
    value: tuple[int, int] | None = None
    end_kind: _EndKind | None = None
    version: tuple[int, int] | None = None
    encoding: tuple[int, int] | None = None
    has_encoding: bool = False
    dtd_name: tuple[int, int] | None = None
    dtd_public: tuple[int, int] | None = None
    dtd_system: tuple[int, int] | None = None


class _TokenizerError(Exception):
    """One tokenizer failure at a decoded offset (the stream resumes at the
    next markup)."""


class _Tokenizer:
    """Deterministic state machine over one decoded plist XML source
    (the frozen token stream of parser_xml.rs:688-719; see the module
    docstring for authority)."""

    _DECLARATION = 0
    _AFTER_DECLARATION = 1
    _DTD = 2
    _AFTER_DTD = 3
    _ELEMENTS = 4
    _ATTRIBUTES = 5
    _AFTER_ELEMENTS = 6
    _END = 7

    def __init__(self, text: str, fragment: bool = False, from_pos: int = 0) -> None:
        self.text = text
        self.pos = from_pos
        if not fragment and from_pos == 0:
            # The leading decoded U+FEFF (BOM) is skipped like the frozen
            # tokenizer stream skip.
            if text.startswith("\ufeff"):
                self.pos = 1
        self.state = self._ELEMENTS if fragment else self._DECLARATION
        self.depth = 0
        self.fragment = fragment

    def next(self) -> _Token | None:
        while True:
            if self.pos >= len(self.text):
                self.state = self._END
                return None
            state = self.state
            if state is self._DECLARATION:
                if self.text.startswith("<?xml ", self.pos):
                    return self._parse_declaration()
                self.state = self._AFTER_DECLARATION
            elif state is self._AFTER_DECLARATION:
                token = self._after_declaration_token()
                if token is not None:
                    return token
            elif state is self._DTD:
                token = self._dtd_token()
                if token is not None:
                    return token
            elif state is self._AFTER_DTD:
                token = self._after_dtd_token()
                if token is not None:
                    return token
            elif state is self._ELEMENTS:
                token = self._elements_token()
                if token is not None:
                    return token
            elif state is self._ATTRIBUTES:
                token = self._parse_attribute()
                if token is not None:
                    return token
            elif state is self._AFTER_ELEMENTS:
                token = self._after_elements_token()
                if token is not None:
                    return token
            else:
                return None

    # -- per-state token producers -------------------------------------------

    def _after_declaration_token(self) -> _Token | None:
        text = self.text
        pos = self.pos
        if text.startswith("<!DOCTYPE", pos):
            token = self._parse_doctype()
            if token.kind is _TokenKind.DTD_START:
                self.state = self._DTD
            else:
                self.state = self._AFTER_DTD
            return token
        if text.startswith("<!--", pos):
            return self._parse_comment()
        if text.startswith("<?", pos):
            if text.startswith("<?xml ", pos):
                raise _TokenizerError(self.pos)
            return self._parse_pi()
        if text[pos] in (" ", "\t", "\n", "\r"):
            self._skip_spaces()
            return None
        # Any other byte (including a bare `<`) falls through to the
        # after-DTD state, which admits the root element.
        self.state = self._AFTER_DTD
        return None

    def _dtd_token(self) -> _Token | None:
        text = self.text
        rest = text[self.pos :]
        if rest.startswith("<!ENTITY"):
            if not self._consume_decl():
                raise _TokenizerError(self.pos)
            return None
        if rest.startswith("<!--"):
            return self._parse_comment()
        if rest.startswith("<?"):
            if rest.startswith("<?xml "):
                raise _TokenizerError(self.pos)
            return self._parse_pi()
        if rest[0] == "]":
            start = self.pos
            self.pos += 1
            self._skip_spaces()
            if self.pos >= len(text) or text[self.pos] != ">":
                raise _TokenizerError(self.pos)
            self.pos += 1
            self.state = self._AFTER_DTD
            return _Token(_TokenKind.DTD_END, start, self.pos)
        if rest[0] in (" ", "\t", "\n", "\r"):
            self._skip_spaces()
            return None
        if rest.startswith("<!ELEMENT") or rest.startswith("<!ATTLIST") or rest.startswith("<!NOTATION"):
            if not self._consume_decl():
                raise _TokenizerError(self.pos)
            return None
        raise _TokenizerError(self.pos)

    def _after_dtd_token(self) -> _Token | None:
        text = self.text
        pos = self.pos
        rest = text[pos:]
        if rest.startswith("<!--"):
            return self._parse_comment()
        if rest.startswith("<?"):
            if rest.startswith("<?xml "):
                raise _TokenizerError(self.pos)
            return self._parse_pi()
        if rest.startswith("<!"):
            raise _TokenizerError(self.pos)
        if rest[0] == "<":
            self.state = self._ATTRIBUTES
            return self._parse_element_start()
        if rest[0] in (" ", "\t", "\n", "\r"):
            self._skip_spaces()
            return None
        raise _TokenizerError(self.pos)

    def _elements_token(self) -> _Token | None:
        text = self.text
        if text[self.pos] != "<":
            return self._parse_text()
        rest = text[self.pos :]
        if rest.startswith("<!--"):
            return self._parse_comment()
        if rest.startswith("<![CDATA["):
            return self._parse_cdata()
        if rest.startswith("<!"):
            raise _TokenizerError(self.pos)
        if rest.startswith("<?"):
            if rest.startswith("<?xml "):
                raise _TokenizerError(self.pos)
            return self._parse_pi()
        if len(rest) > 1 and rest[1] == "/":
            if self.depth > 0:
                self.depth -= 1
            if self.depth == 0 and not self.fragment:
                self.state = self._AFTER_ELEMENTS
            else:
                self.state = self._ELEMENTS
            return self._parse_close_element()
        self.state = self._ATTRIBUTES
        return self._parse_element_start()

    def _after_elements_token(self) -> _Token | None:
        text = self.text
        pos = self.pos
        rest = text[pos:]
        if rest.startswith("<!--"):
            return self._parse_comment()
        if rest.startswith("<?"):
            if rest.startswith("<?xml "):
                raise _TokenizerError(self.pos)
            return self._parse_pi()
        if rest[0] in (" ", "\t", "\n", "\r"):
            self._skip_spaces()
            return None
        raise _TokenizerError(self.pos)

    # -- token parsers -------------------------------------------------------

    def _parse_declaration(self) -> _Token:
        start = self.pos
        self.pos += 6  # "<?xml "
        if not self._skip_string("version"):
            raise _TokenizerError(self.pos)
        if not self._consume_eq():
            raise _TokenizerError(self.pos)
        quote, ok = self._consume_quote()
        if not ok:
            raise _TokenizerError(self.pos)
        version_start = self.pos
        if not self._skip_string("1."):
            raise _TokenizerError(self.pos)
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        version_end = self.pos
        if not self._consume_byte(quote):
            raise _TokenizerError(self.pos)
        if not self._consume_declaration_spaces():
            raise _TokenizerError(self.pos)
        encoding: tuple[int, int] | None = None
        has_encoding = False
        if self.text.startswith("encoding", self.pos):
            self.pos += 8
            if not self._consume_eq():
                raise _TokenizerError(self.pos)
            quote, ok = self._consume_quote()
            if not ok:
                raise _TokenizerError(self.pos)
            encoding_start = self.pos
            while self.pos < len(self.text) and self._is_encoding_byte(self.text[self.pos]):
                self.pos += 1
            encoding_end = self.pos
            if not self._consume_byte(quote):
                raise _TokenizerError(self.pos)
            encoding = (encoding_start, encoding_end)
            has_encoding = True
            if not self._consume_declaration_spaces():
                raise _TokenizerError(self.pos)
        if self.text.startswith("standalone", self.pos):
            self.pos += 10
            if not self._consume_eq():
                raise _TokenizerError(self.pos)
            quote, ok = self._consume_quote()
            if not ok:
                raise _TokenizerError(self.pos)
            value_start = self.pos
            while self.pos < len(self.text) and self.text[self.pos] != quote:
                self.pos += 1
            if self.pos >= len(self.text):
                raise _TokenizerError(self.pos)
            if self.text[value_start : self.pos] not in ("yes", "no"):
                raise _TokenizerError(self.pos)
            if not self._consume_byte(quote):
                raise _TokenizerError(self.pos)
        self._skip_spaces()
        if not self._skip_string("?>"):
            raise _TokenizerError(self.pos)
        return _Token(
            _TokenKind.DECLARATION,
            start,
            self.pos,
            version=(version_start, version_end),
            encoding=encoding,
            has_encoding=has_encoding,
        )

    def _parse_pi(self) -> _Token:
        start = self.pos
        self.pos += 2
        local, ok = self._consume_qname()
        if not ok:
            raise _TokenizerError(self.pos)
        if self.text.startswith("?>", self.pos):
            self.pos += 2
            return _Token(_TokenKind.PROCESSING_INSTRUCTION, start, self.pos, local=local)
        if self.pos >= len(self.text) or not _is_ws(self.text[self.pos]):
            raise _TokenizerError(self.pos)
        content_start = self.pos
        while self.pos < len(self.text) and not self.text.startswith("?>", self.pos):
            self.pos += 1
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        content_end = self.pos
        self.pos += 2
        return _Token(
            _TokenKind.PROCESSING_INSTRUCTION,
            start,
            self.pos,
            local=local,
            value=(content_start, content_end),
        )

    def _parse_comment(self) -> _Token:
        start = self.pos
        self.pos += 4
        text_start = self.pos
        while self.pos < len(self.text) and not self.text.startswith("-->", self.pos):
            self.pos += 1
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        text_end = self.pos
        if "--" in self.text[text_start:text_end]:
            raise _TokenizerError(self.pos)
        if text_end > text_start and self.text[text_end - 1] == "-":
            raise _TokenizerError(self.pos)
        self.pos += 3
        return _Token(_TokenKind.COMMENT, start, self.pos, value=(text_start, text_end))

    def _parse_doctype(self) -> _Token:
        start = self.pos
        self.pos += 9  # "<!DOCTYPE"
        self._skip_spaces()
        name, ok = self._consume_qname()
        if not ok:
            raise _TokenizerError(self.pos)
        self._skip_spaces()
        dtd_public: tuple[int, int] | None = None
        dtd_system: tuple[int, int] | None = None
        if self.text.startswith("SYSTEM", self.pos) or self.text.startswith("PUBLIC", self.pos):
            is_public = self.text.startswith("PUBLIC", self.pos)
            self.pos += 6
            self._skip_spaces()
            quote, ok = self._consume_quote()
            if not ok:
                raise _TokenizerError(self.pos)
            literal_start = self.pos
            while self.pos < len(self.text) and self.text[self.pos] != quote:
                self.pos += 1
            if self.pos >= len(self.text):
                raise _TokenizerError(self.pos)
            literal_end = self.pos
            if not self._consume_byte(quote):
                raise _TokenizerError(self.pos)
            if is_public:
                dtd_public = (literal_start, literal_end)
                self._skip_spaces()
                quote, ok = self._consume_quote()
                if not ok:
                    raise _TokenizerError(self.pos)
                sys_start = self.pos
                while self.pos < len(self.text) and self.text[self.pos] != quote:
                    self.pos += 1
                if self.pos >= len(self.text):
                    raise _TokenizerError(self.pos)
                sys_end = self.pos
                if not self._consume_byte(quote):
                    raise _TokenizerError(self.pos)
                dtd_system = (sys_start, sys_end)
            else:
                dtd_system = (literal_start, literal_end)
        self._skip_spaces()
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        if self.text[self.pos] == "[":
            self.pos += 1
            kind = _TokenKind.DTD_START
        elif self.text[self.pos] == ">":
            self.pos += 1
            kind = _TokenKind.DTD_END
        else:
            raise _TokenizerError(self.pos)
        return _Token(
            kind,
            start,
            self.pos,
            dtd_name=name,
            dtd_public=dtd_public,
            dtd_system=dtd_system,
        )

    def _consume_decl(self) -> bool:
        while self.pos < len(self.text) and self.text[self.pos] != ">":
            self.pos += 1
        if self.pos >= len(self.text):
            return False
        self.pos += 1
        return True

    def _parse_element_start(self) -> _Token:
        start = self.pos
        self.pos += 1
        local, ok = self._consume_qname()
        if not ok:
            raise _TokenizerError(self.pos)
        return _Token(_TokenKind.ELEMENT_START, start, self.pos, local=local)

    def _parse_close_element(self) -> _Token:
        start = self.pos
        self.pos += 2
        local, ok = self._consume_qname()
        if not ok:
            raise _TokenizerError(self.pos)
        self._skip_spaces()
        if self.pos >= len(self.text) or self.text[self.pos] != ">":
            raise _TokenizerError(self.pos)
        self.pos += 1
        return _Token(
            _TokenKind.ELEMENT_END, start, self.pos, local=local, end_kind=_EndKind.CLOSE
        )

    def _parse_attribute(self) -> _Token:
        attr_start = self.pos  # before the leading whitespace (Go tokenizer)
        self._skip_spaces()
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        if self.text[self.pos] == ">":
            start = self.pos
            self.pos += 1
            token = _Token(_TokenKind.ELEMENT_END, start, self.pos, end_kind=_EndKind.OPEN)
            self.depth += 1
            if self.depth == 0 and not self.fragment:
                self.state = self._AFTER_ELEMENTS
            else:
                self.state = self._ELEMENTS
            return token
        if self.text[self.pos] == "/":
            start = self.pos
            self.pos += 1
            if self.pos >= len(self.text) or self.text[self.pos] != ">":
                raise _TokenizerError(self.pos)
            self.pos += 1
            if self.depth == 0 and not self.fragment:
                self.state = self._AFTER_ELEMENTS
            else:
                self.state = self._ELEMENTS
            return _Token(_TokenKind.ELEMENT_END, start, self.pos, end_kind=_EndKind.EMPTY)
        local, ok = self._consume_qname()
        if not ok:
            raise _TokenizerError(self.pos)
        if not self._consume_eq():
            raise _TokenizerError(self.pos)
        quote, ok = self._consume_quote()
        if not ok:
            raise _TokenizerError(self.pos)
        value_start = self.pos
        while (
            self.pos < len(self.text)
            and self.text[self.pos] != quote
            and self.text[self.pos] != "<"
        ):
            self.pos += 1
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        value_end = self.pos
        if not self._consume_byte(quote):
            raise _TokenizerError(self.pos)
        return _Token(
            _TokenKind.ATTRIBUTE,
            attr_start,
            self.pos,
            local=local,
            value=(value_start, value_end),
        )

    def _parse_text(self) -> _Token:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] != "<":
            self.pos += 1
        if ">" in self.text[start : self.pos] and "]]>" in self.text[start : self.pos]:
            raise _TokenizerError(self.pos)
        return _Token(_TokenKind.TEXT, start, self.pos)

    def _parse_cdata(self) -> _Token:
        start = self.pos
        self.pos += 9
        text_start = self.pos
        while self.pos < len(self.text) and not self.text.startswith("]]>", self.pos):
            self.pos += 1
        if self.pos >= len(self.text):
            raise _TokenizerError(self.pos)
        text_end = self.pos
        self.pos += 3
        return _Token(_TokenKind.CDATA, start, self.pos, value=(text_start, text_end))

    # -- primitives ----------------------------------------------------------

    def _skip_spaces(self) -> None:
        while self.pos < len(self.text) and _is_ws(self.text[self.pos]):
            self.pos += 1

    def _skip_string(self, value: str) -> bool:
        if self.text.startswith(value, self.pos):
            self.pos += len(value)
            return True
        return False

    def _consume_byte(self, byte: str) -> bool:
        if self.pos < len(self.text) and self.text[self.pos] == byte:
            self.pos += 1
            return True
        return False

    def _consume_eq(self) -> bool:
        self._skip_spaces()
        if not self._consume_byte("="):
            return False
        self._skip_spaces()
        return True

    def _consume_quote(self) -> tuple[str, bool]:
        if self.pos < len(self.text) and self.text[self.pos] in ('"', "'"):
            quote = self.text[self.pos]
            self.pos += 1
            return quote, True
        return "", False

    def _consume_declaration_spaces(self) -> bool:
        if self.pos < len(self.text) and _is_ws(self.text[self.pos]):
            self._skip_spaces()
            return True
        if self.text.startswith("?>", self.pos):
            return True
        if self.pos >= len(self.text):
            return True
        return False

    def _consume_qname(self) -> tuple[tuple[int, int], bool]:
        start = self.pos
        if self.pos >= len(self.text) or not _is_name_start(self.text[self.pos]):
            return (start, start), False
        self.pos += 1
        while self.pos < len(self.text) and _is_name_char(self.text[self.pos]):
            self.pos += 1
        return (start, self.pos), True

    @staticmethod
    def _is_encoding_byte(character: str) -> bool:
        """EncName ::= [A-Za-z] ([A-Za-z0-9._] | '-')*"""
        return (
            "a" <= character <= "z"
            or "A" <= character <= "Z"
            or "0" <= character <= "9"
            or character in (".", "_", "-")
        )


# ---------------------------------------------------------------------------
# Element vocabulary (RFC 0013 §4.3)
# ---------------------------------------------------------------------------


class _ElementKind(enum.Enum):
    PLIST = "plist"
    DICT = "dict"
    ARRAY = "array"
    STRING = "string"
    KEY = "key"
    INTEGER = "integer"
    REAL = "real"
    TRUE = "true"
    FALSE = "false"
    DATA = "data"
    DATE = "date"

    def is_scalar(self) -> bool:
        return self in (
            _ElementKind.STRING,
            _ElementKind.KEY,
            _ElementKind.INTEGER,
            _ElementKind.REAL,
            _ElementKind.TRUE,
            _ElementKind.FALSE,
            _ElementKind.DATA,
            _ElementKind.DATE,
        )

    def open_kind(self) -> PlistSyntaxKind:
        return {
            _ElementKind.PLIST: PlistSyntaxKind.PLIST_OPEN,
            _ElementKind.DICT: PlistSyntaxKind.DICT_OPEN,
            _ElementKind.ARRAY: PlistSyntaxKind.ARRAY_OPEN,
            _ElementKind.STRING: PlistSyntaxKind.STRING_OPEN,
            _ElementKind.KEY: PlistSyntaxKind.KEY_OPEN,
            _ElementKind.INTEGER: PlistSyntaxKind.INTEGER_OPEN,
            _ElementKind.REAL: PlistSyntaxKind.REAL_OPEN,
            _ElementKind.TRUE: PlistSyntaxKind.TRUE,
            _ElementKind.FALSE: PlistSyntaxKind.FALSE,
            _ElementKind.DATA: PlistSyntaxKind.DATA_OPEN,
            _ElementKind.DATE: PlistSyntaxKind.DATE_OPEN,
        }[self]

    def close_kind(self) -> PlistSyntaxKind:
        return {
            _ElementKind.PLIST: PlistSyntaxKind.PLIST_CLOSE,
            _ElementKind.DICT: PlistSyntaxKind.DICT_CLOSE,
            _ElementKind.ARRAY: PlistSyntaxKind.ARRAY_CLOSE,
            _ElementKind.STRING: PlistSyntaxKind.STRING_CLOSE,
            _ElementKind.KEY: PlistSyntaxKind.KEY_CLOSE,
            _ElementKind.INTEGER: PlistSyntaxKind.INTEGER_CLOSE,
            _ElementKind.REAL: PlistSyntaxKind.REAL_CLOSE,
            _ElementKind.TRUE: PlistSyntaxKind.TRUE,
            _ElementKind.FALSE: PlistSyntaxKind.FALSE,
            _ElementKind.DATA: PlistSyntaxKind.DATA_CLOSE,
            _ElementKind.DATE: PlistSyntaxKind.DATE_CLOSE,
        }[self]


def _classify_element(local: str) -> _ElementKind | None:
    """Classifies an unqualified element name; None is unknown or prefixed
    (parser_xml.rs:568-584)."""
    try:
        return _ElementKind(local)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parser frames
# ---------------------------------------------------------------------------


class _Normalization(enum.Enum):
    TEXT = "text"
    ATTRIBUTE = "attribute"


@dataclass(slots=True)
class _DictState:
    entries: list[PlistDictEntry] = None  # type: ignore[assignment]
    groups: dict[PlistKey, int] = None  # type: ignore[assignment]
    pending_key: PlistKey | None = None
    expect_value: bool = False

    def __post_init__(self) -> None:
        self.entries = []
        self.groups = {}


class _FrameValue(enum.Enum):
    ROOT = "root"
    DICT = "dict"
    ARRAY = "array"
    NONE = "none"


@dataclass(slots=True)
class _Frame:
    kind: _ElementKind | None
    name: str
    value: _FrameValue
    dict_state: _DictState | None = None
    elements: list[PlistValueRef] | None = None
    content: str = ""
    value_allowed: bool = True
    scalar_unproven: bool = False
    tag_cursor: int = 0  # decoded offset after the last covered tag byte
    open_start: int = 0
    open_end: int = 0
    self_closing: bool = False
    root_version: str | None = None
    unknown_subtree_start: int | None = None


class _TextPosition(enum.Enum):
    OUTSIDE = "outside"
    CONTAINER = "container"
    BOOLEAN = "boolean"
    SCALAR = "scalar"


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, source: SourceSnapshot, limits: PlistParseLimits) -> None:
        self.source = source
        self.limits = limits
        self.authority = DocumentAuthority.fresh()
        self.recovered = False
        self.pieces: list[StructuralPiece] = []
        self.syntax_kinds: list[PlistSyntaxKind] = []
        self.stack: list[_Frame] = []
        self.unknown_depth = 0
        self.doctype_body_start: int | None = None
        self.any_top_level = False
        self.plist_root_seen = False
        self.root_value_count = 0
        self.root_value_ref: PlistValueRef | None = None
        self.arena = PlistDocumentBuilder(
            PlistArenaLimits(
                max_objects=limits.max_object_count,
                max_container_depth=limits.max_container_depth,
            )
        )
        self.diagnostics: list[PlistDiagnostic] = []
        self.occurrence = 0
        # Exact raw span of every arena node, in arena (close-tag) order;
        # consumed by projection provenance (RFC 0013 §9).
        self.value_spans: dict[int, object] = {}

    # -- entry ---------------------------------------------------------------

    def parse(self, decoded: str) -> PlistFormedXml:
        """Parses one complete decoded source (parser_xml.rs:688-719)."""
        self._cover_bom()
        tokenizer = _Tokenizer(decoded)
        while True:
            try:
                token = tokenizer.next()
            except _TokenizerError:
                end = min(tokenizer.pos, len(decoded))
                start = max(end - 1, 0)
                if end > 0:
                    self._recover_error_region(start, end)
                markup = decoded.find("<", end)
                if markup < 0:
                    break
                resume = markup
                if resume <= end:
                    # The error is at the resume `<` itself (e.g. `<!` markup
                    # the fragment state cannot start): restarting there would
                    # loop forever on the same position. Skip the byte — it
                    # becomes part of the gap coverage in finish().
                    resume = end + 1
                if resume >= len(decoded):
                    break
                tokenizer = _Tokenizer(decoded, fragment=True, from_pos=resume)
                continue
            if token is None:
                break
            self._token(token, decoded)
        return self._finish()

    # -- pieces and coordinates ----------------------------------------------

    def raw_span(self, decoded_start: int, decoded_end: int) -> object:
        """Maps decoded scalar offsets to a raw-byte span (parser_xml.rs
        raw_span_offset; exact for UTF-8 and UTF-16 via the source layer)."""
        try:
            start = self.source.raw_byte_at(DecodedOffset.unicode_scalar(decoded_start))
            end = self.source.raw_byte_at(DecodedOffset.unicode_scalar(decoded_end))
        except Exception:
            raise PlistFormationFailure(
                PlistFormationFailureKind.XML_COORDINATES,
                resource_name="source-coordinate-boundary",
                observed=1,
                limit=0,
            ) from None
        try:
            return self.authority.span(start, end)
        except Exception:
            raise PlistFormationFailure(
                PlistFormationFailureKind.XML_COORDINATES,
                resource_name="source-coordinate-boundary",
                observed=1,
                limit=0,
            ) from None

    def push_piece(
        self,
        span: object,
        kind: PlistSyntaxKind,
        structural: StructuralPieceKind,
    ) -> None:
        """One exhaustive source piece (parser_xml.rs:1084-1107 analog)."""
        observed = len(self.pieces) + 1
        if observed > self.limits.max_syntax_pieces:
            raise PlistFormationFailure(
                PlistFormationFailureKind.SYNTAX_PIECES,
                resource_name="syntax-pieces",
                observed=observed,
                limit=self.limits.max_syntax_pieces,
            )
        self.pieces.append(StructuralPiece(span=span, kind=structural))
        self.syntax_kinds.append(kind)

    def recover(
        self,
        code: str,
        category: DiagnosticCategory,
        primary: object,
        arguments: dict[str, str] | None = None,
    ) -> None:
        """Records one recovery diagnostic and marks the parse Recovered
        (parser_xml.rs:2318-2335)."""
        self.recovered = True
        diagnostic = PlistDiagnostic(
            code=code,
            category=category,
            severity=PlistSeverity.ERROR,
            primary=primary,
            occurrence=self.occurrence,
            arguments=dict(arguments or {}),
        )
        self.occurrence += 1
        self.diagnostics.append(diagnostic)

    def _recover_error_region(self, start: int, end: int) -> None:
        """Tokenizer-error path: one error region plus well-formedness@1
        (parser_xml.rs:2118-2149)."""
        span = self.raw_span(start, end)
        if self.unknown_depth == 0:
            self.push_piece(span, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
        self.recover(
            "plist.parse.well-formedness@1",
            DiagnosticCategory.SYNTAX,
            span,
        )

    def _cover_bom(self) -> None:
        """Covers a leading BOM as a trivia piece (parser_xml.rs:727-739)."""
        facts = self.source.encoding_facts()
        bom = facts.bom
        if bom is not None:
            length = 3 if bom.value == "Utf8" else 2
            if length > 0:
                span = self.authority.span(0, length)
                self.push_piece(span, PlistSyntaxKind.BOM, StructuralPieceKind.TRIVIA)

    # -- token dispatch ------------------------------------------------------

    def _token(self, token: _Token, decoded: str) -> None:
        kind = token.kind
        if kind is _TokenKind.DECLARATION:
            self._declaration(token)
        elif kind is _TokenKind.PROCESSING_INSTRUCTION:
            self._processing_instruction(token)
        elif kind is _TokenKind.COMMENT:
            self._comment(token)
        elif kind is _TokenKind.DTD_START:
            self._doctype_start(token)
        elif kind is _TokenKind.DTD_END:
            if token.dtd_name is not None:
                self._doctype_empty(token)
            else:
                self._dtd_end(token)
        elif kind is _TokenKind.ELEMENT_START:
            self._element_start(token)
        elif kind is _TokenKind.ATTRIBUTE:
            self._attribute(token, decoded)
        elif kind is _TokenKind.ELEMENT_END:
            self._element_end(token)
        elif kind is _TokenKind.TEXT:
            self._text(token)
        elif kind is _TokenKind.CDATA:
            self._cdata(token)

    # -- prolog handlers -----------------------------------------------------

    def _declaration(self, token: _Token) -> None:
        if self.unknown_depth > 0:
            return
        text = self.source.decoded_text()
        assert text is not None
        open_span = self.raw_span(token.start, token.start + _DECLARATION_OPEN_BYTES)
        self.push_piece(open_span, PlistSyntaxKind.DECLARATION_OPEN, StructuralPieceKind.TOKEN)
        rel = token.start + _DECLARATION_OPEN_BYTES
        rel = _skip_declaration_spaces(text, rel)
        version = token.version
        assert version is not None
        name_span = self.raw_span(rel, rel + 7)
        self.push_piece(name_span, PlistSyntaxKind.DECLARATION_NAME, StructuralPieceKind.TOKEN)
        value_span = self.raw_span(version[0], version[1])
        self.push_piece(value_span, PlistSyntaxKind.DECLARATION_VALUE, StructuralPieceKind.TOKEN)
        version_text = text[version[0] : version[1]]
        if version_text != "1.0":
            self.recover(
                "plist.parse.declaration-version@1",
                DiagnosticCategory.SYNTAX,
                value_span,
                {"version": version_text},
            )
        rel = version[1] + 1  # the closing quote of the version value
        if token.has_encoding:
            encoding = token.encoding
            assert encoding is not None
            rel = _skip_declaration_spaces(text, rel)
            name_span = self.raw_span(rel, rel + 8)
            self.push_piece(name_span, PlistSyntaxKind.DECLARATION_NAME, StructuralPieceKind.TOKEN)
            value_span = self.raw_span(encoding[0], encoding[1])
            self.push_piece(value_span, PlistSyntaxKind.DECLARATION_VALUE, StructuralPieceKind.TOKEN)
            declared = text[encoding[0] : encoding[1]].upper()
            selected = self.source.encoding_facts().selected
            agrees = (
                (selected == SourceEncoding.utf8() and declared == "UTF-8")
                or (
                    selected == SourceEncoding.utf16le()
                    and declared in ("UTF-16", "UTF-16LE")
                )
                or (
                    selected == SourceEncoding.utf16be()
                    and declared in ("UTF-16", "UTF-16BE")
                )
            )
            if not agrees:
                self.recover(
                    "plist.parse.declaration-conflict@1",
                    DiagnosticCategory.ENCODING,
                    value_span,
                    {"declared": declared, "selected": selected.as_str},
                )
        close_span = self.raw_span(token.end - 2, token.end)
        self.push_piece(close_span, PlistSyntaxKind.DECLARATION_CLOSE, StructuralPieceKind.TOKEN)

    def _processing_instruction(self, token: _Token) -> None:
        if self.doctype_body_start is not None or self.unknown_depth > 0:
            return
        local = token.local
        assert local is not None
        text = self.source.decoded_text()
        assert text is not None
        target_span = self.raw_span(local[0], local[1])
        if text[local[0] : local[1]].lower() == "xml":
            self.recover(
                "plist.parse.pi-target@1",
                DiagnosticCategory.SYNTAX,
                target_span,
            )
        open_span = self.raw_span(token.start, token.start + 2)
        self.push_piece(open_span, PlistSyntaxKind.PROCESSING_INSTRUCTION_OPEN, StructuralPieceKind.TRIVIA)
        self.push_piece(target_span, PlistSyntaxKind.PROCESSING_INSTRUCTION_TARGET, StructuralPieceKind.TRIVIA)
        if token.value is not None:
            content_span = self.raw_span(token.value[0], token.value[1])
            self.push_piece(content_span, PlistSyntaxKind.PROCESSING_INSTRUCTION_CONTENT, StructuralPieceKind.TRIVIA)
        close_span = self.raw_span(token.end - 2, token.end)
        self.push_piece(close_span, PlistSyntaxKind.PROCESSING_INSTRUCTION_CLOSE, StructuralPieceKind.TRIVIA)

    def _comment(self, token: _Token) -> None:
        if self.doctype_body_start is not None or self.unknown_depth > 0:
            return
        open_span = self.raw_span(token.start, token.start + _COMMENT_OPEN_BYTES)
        text_span = self.raw_span(token.value[0], token.value[1])
        close_span = self.raw_span(token.value[1], token.end)
        self.push_piece(open_span, PlistSyntaxKind.COMMENT_OPEN, StructuralPieceKind.TRIVIA)
        self.push_piece(text_span, PlistSyntaxKind.COMMENT_TEXT, StructuralPieceKind.TRIVIA)
        self.push_piece(close_span, PlistSyntaxKind.COMMENT_CLOSE, StructuralPieceKind.TRIVIA)

    def _doctype_start(self, token: _Token) -> None:
        raw = self.raw_span(token.start, token.end)
        open = self.authority.span(raw.start_byte, raw.start_byte + _DOCTYPE_OPEN_BYTES)
        self.push_piece(open, PlistSyntaxKind.DOCTYPE_OPEN, StructuralPieceKind.TOKEN)
        self._validate_doctype(token, raw)
        self.recover(
            "plist.parse.doctype-subset@1",
            DiagnosticCategory.SYNTAX,
            raw,
        )
        self.doctype_body_start = raw.start_byte + _DOCTYPE_OPEN_BYTES

    def _doctype_empty(self, token: _Token) -> None:
        raw = self.raw_span(token.start, token.end)
        open = self.authority.span(raw.start_byte, raw.start_byte + _DOCTYPE_OPEN_BYTES)
        self.push_piece(open, PlistSyntaxKind.DOCTYPE_OPEN, StructuralPieceKind.TOKEN)
        self._validate_doctype(token, raw)
        body_end = raw.end_byte - 1
        body = self.authority.span(raw.start_byte + _DOCTYPE_OPEN_BYTES, body_end)
        self.push_piece(body, PlistSyntaxKind.DOCTYPE_BODY, StructuralPieceKind.TOKEN)
        close = self.authority.span(body_end, raw.end_byte)
        self.push_piece(close, PlistSyntaxKind.DOCTYPE_CLOSE, StructuralPieceKind.TOKEN)

    def _dtd_end(self, token: _Token) -> None:
        raw = self.raw_span(token.start, token.end)
        body_end = raw.end_byte - 1
        body_start = self.doctype_body_start
        if body_start is not None:
            body = self.authority.span(body_start, body_end)
            self.push_piece(body, PlistSyntaxKind.DOCTYPE_BODY, StructuralPieceKind.TOKEN)
        self.doctype_body_start = None
        close = self.authority.span(body_end, raw.end_byte)
        self.push_piece(close, PlistSyntaxKind.DOCTYPE_CLOSE, StructuralPieceKind.TOKEN)

    def _validate_doctype(self, token: _Token, raw: object) -> None:
        """Validates the exact Apple plist DOCTYPE identity (RFC 0013 §4.1;
        parser_xml.rs:1053-1085)."""
        text = self.source.decoded_text()
        assert text is not None
        name = text[token.dtd_name[0] : token.dtd_name[1]]
        public = None
        system = None
        if token.dtd_public is not None:
            public = text[token.dtd_public[0] : token.dtd_public[1]]
            assert token.dtd_system is not None
            system = text[token.dtd_system[0] : token.dtd_system[1]]
        elif token.dtd_system is not None:
            system = text[token.dtd_system[0] : token.dtd_system[1]]
        identifiers_ok = public == _PLIST_DOCTYPE_PUBLIC and system == _PLIST_DOCTYPE_SYSTEM
        if name != "plist" or not identifiers_ok:
            arguments: dict[str, str] = {"name": name}
            if public is not None:
                arguments["public"] = public
            if system is not None:
                arguments["system"] = system
            self.recover(
                "plist.parse.doctype@1",
                DiagnosticCategory.SYNTAX,
                raw,
                arguments,
            )

    # -- element handlers ----------------------------------------------------

    def _element_start(self, token: _Token) -> None:
        if len(self.stack) >= self.limits.common.max_nesting_depth:
            raise PlistFormationFailure(
                PlistFormationFailureKind.NESTING_DEPTH,
                resource_name="nesting-depth",
                observed=len(self.stack) + 1,
                limit=self.limits.common.max_nesting_depth,
            )
        text = self.source.decoded_text()
        assert text is not None
        open_raw = self.raw_span(token.start, token.end)
        local_text = text[token.local[0] : token.local[1]]
        kind = _classify_element(local_text)
        name = text[token.start + 1 : token.end]
        top_level = not self.stack
        admitted_root = top_level and not self.plist_root_seen and kind is _ElementKind.PLIST
        is_unknown = (
            (not admitted_root)
            if top_level
            else (kind is None or kind is _ElementKind.PLIST)
        )
        if top_level:
            self.any_top_level = True
        if kind is _ElementKind.PLIST:
            frame_value = _FrameValue.ROOT
        elif kind is _ElementKind.DICT:
            frame_value = _FrameValue.DICT
        elif kind is _ElementKind.ARRAY:
            frame_value = _FrameValue.ARRAY
        else:
            frame_value = _FrameValue.NONE
        value_allowed = not is_unknown
        scalar_violation = False
        if not is_unknown:
            parent = self.stack[-1] if self.stack else None
            parent_kind = parent.kind if parent is not None else None
            parent_allowed = parent.value_allowed if parent is not None else True
            parent_expect_value = bool(
                parent is not None
                and parent.value is _FrameValue.DICT
                and parent.dict_state.expect_value
            )
            parent_scalar = bool(
                parent is not None
                and parent.kind is not None
                and parent.kind.is_scalar()
            )
            value_allowed = parent_allowed
            if kind is _ElementKind.KEY:
                if parent_kind is _ElementKind.DICT:
                    if parent_allowed and parent_expect_value:
                        self.recover(
                            "plist.parse.dict-missing-value@1",
                            DiagnosticCategory.SYNTAX,
                            open_raw,
                        )
                elif parent_kind in (_ElementKind.PLIST, _ElementKind.ARRAY):
                    self.recover(
                        "plist.parse.key-outside-dict@1",
                        DiagnosticCategory.SYNTAX,
                        open_raw,
                        {"name": name},
                    )
                elif parent_kind is not None:
                    scalar_violation = True
            elif kind in (_ElementKind.DICT, _ElementKind.ARRAY):
                if parent_scalar:
                    scalar_violation = True
            elif kind is not None:
                if parent_kind is _ElementKind.DICT:
                    if parent_allowed and not parent_expect_value:
                        self.recover(
                            "plist.parse.dict-key@1",
                            DiagnosticCategory.SYNTAX,
                            open_raw,
                            {"element": name},
                        )
                elif parent_kind in (_ElementKind.PLIST, _ElementKind.ARRAY):
                    pass
                elif parent_kind is not None:
                    scalar_violation = True
        if scalar_violation:
            self.recover(
                "plist.parse.scalar-content@1",
                DiagnosticCategory.SYNTAX,
                open_raw,
                {"element": name},
            )
            if self.stack:
                self.stack[-1].scalar_unproven = True
            value_allowed = False
        if is_unknown and self.unknown_depth == 0:
            self.recover(
                "plist.parse.element-name@1",
                DiagnosticCategory.SYNTAX,
                open_raw,
                {"name": name},
            )
        if self.unknown_depth == 0 and not is_unknown:
            assert kind is not None
            self.push_piece(open_raw, kind.open_kind(), StructuralPieceKind.TOKEN)
        unknown_marker = open_raw.start_byte if (is_unknown and self.unknown_depth == 0) else None
        if is_unknown:
            self.unknown_depth += 1
        dict_state = _DictState() if kind is _ElementKind.DICT else None
        elements: list[PlistValueRef] | None = [] if kind is _ElementKind.ARRAY else None
        self.stack.append(
            _Frame(
                kind=kind,
                name=name,
                value=frame_value,
                dict_state=dict_state,
                elements=elements,
                value_allowed=value_allowed,
                # The whitespace walk after the open-tag name starts at the
                # name's end (the tag-closing piece covers the rest).
                tag_cursor=token.end,
                open_start=open_raw.start_byte,
                open_end=open_raw.end_byte,
                unknown_subtree_start=unknown_marker,
            )
        )
        if admitted_root:
            self.plist_root_seen = True

    def _attribute(self, token: _Token, decoded: str) -> None:
        if self.unknown_depth > 0:
            return
        if not self.stack:
            return
        frame = self.stack[-1]
        is_root = frame.kind is _ElementKind.PLIST and len(self.stack) == 1
        version_unset = frame.root_version is None
        self._push_whitespace_pieces(frame.tag_cursor, token.start)
        local = token.local
        assert local is not None
        is_version = (
            is_root
            and version_unset
            and decoded[local[0] : local[1]] == "version"
        )
        if is_version:
            name_span = self.raw_span(local[0], local[1])
            self.push_piece(name_span, PlistSyntaxKind.PLIST_VERSION_NAME, StructuralPieceKind.TOKEN)
            eq_at = decoded.find("=", local[1], token.value[0])
            if eq_at < 0:
                eq_at = local[1]
            value_span = self.raw_span(eq_at, token.value[1] + 1)
            self.push_piece(value_span, PlistSyntaxKind.PLIST_VERSION_VALUE, StructuralPieceKind.TOKEN)
            normalized = self._normalize_attribute_value(token)
            if normalized != _PLIST_VERSION:
                self.recover(
                    "plist.parse.root-version@1",
                    DiagnosticCategory.SYNTAX,
                    value_span,
                    {"version": normalized},
                )
            frame.root_version = normalized
        else:
            attr_span = self.raw_span(token.start, token.value[1] + 1)
            self.push_piece(attr_span, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
            code = "plist.parse.root-attribute@1" if is_root else "plist.parse.element-attribute@1"
            self.recover(
                code,
                DiagnosticCategory.SYNTAX,
                attr_span,
                {"name": decoded[local[0] : local[1]]},
            )
        frame.tag_cursor = token.value[1] + 1

    def _normalize_attribute_value(self, token: _Token) -> str:
        """Normalized root version value: references resolve and literal
        whitespace collapses to one space (XML attribute normalization;
        parser_xml.rs:1343-1350)."""
        return self._resolve_fragments(
            token.value[0], token.value[1], _Normalization.ATTRIBUTE, False
        )

    def _element_end(self, token: _Token) -> None:
        end_kind = token.end_kind
        if end_kind is _EndKind.OPEN:
            self._open_tag_end(token)
        elif end_kind is _EndKind.EMPTY:
            self._empty_tag_end(token)
        else:
            self._close_tag_end(token)

    def _open_tag_end(self, token: _Token) -> None:
        if not self.stack:
            return
        frame = self.stack[-1]
        is_plist = frame.kind is _ElementKind.PLIST and len(self.stack) == 1
        raw = self.raw_span(token.start, token.end)
        if self.unknown_depth == 0:
            self._push_whitespace_pieces(frame.tag_cursor, token.start)
            kind_piece = (
                frame.kind.open_kind()
                if frame.kind is not None
                else PlistSyntaxKind.ERROR_REGION
            )
            self.push_piece(raw, kind_piece, StructuralPieceKind.TOKEN)
        if is_plist and frame.root_version is None:
            self.recover(
                "plist.parse.root-version@1",
                DiagnosticCategory.SYNTAX,
                raw,
                {"version": "<missing>"},
            )
        frame.tag_cursor = token.end
        frame.open_end = raw.end_byte

    def _empty_tag_end(self, token: _Token) -> None:
        if not self.stack:
            return
        frame = self.stack[-1]
        is_plist = frame.kind is _ElementKind.PLIST and len(self.stack) == 1
        raw = self.raw_span(token.start, token.end)
        if self.unknown_depth == 0:
            self._push_whitespace_pieces(frame.tag_cursor, token.start)
            kind_piece = (
                frame.kind.close_kind()
                if frame.kind is not None
                else PlistSyntaxKind.ERROR_REGION
            )
            self.push_piece(raw, kind_piece, StructuralPieceKind.TOKEN)
        if is_plist and frame.root_version is None:
            self.recover(
                "plist.parse.root-version@1",
                DiagnosticCategory.SYNTAX,
                raw,
                {"version": "<missing>"},
            )
        frame.self_closing = True
        if self.unknown_depth == 0:
            frame.open_end = raw.end_byte
        self._close_frame(raw)

    def _close_tag_end(self, token: _Token) -> None:
        raw = self.raw_span(token.start, token.end)
        text = self.source.decoded_text()
        assert text is not None
        close_name = text[token.start + 2 : token.end - 1].strip()
        if self.stack:
            frame = self.stack[-1]
            if frame.name != close_name:
                self.recover(
                    "plist.parse.mismatched-end-tag@1",
                    DiagnosticCategory.SYNTAX,
                    raw,
                    {"expected": frame.name, "found": close_name},
                )
        if self.unknown_depth == 0:
            if self.stack:
                kind_piece = (
                    self.stack[-1].kind.close_kind()
                    if self.stack[-1].kind is not None
                    else PlistSyntaxKind.ERROR_REGION
                )
                self.push_piece(raw, kind_piece, StructuralPieceKind.TOKEN)
        self._close_frame(raw)

    def _close_frame(self, end_span: object) -> None:
        if not self.stack:
            self.recover(
                "plist.parse.extra-end-tag@1",
                DiagnosticCategory.SYNTAX,
                end_span,
            )
            return
        frame = self.stack.pop()
        if frame.unknown_subtree_start is not None:
            span = self.authority.span(frame.unknown_subtree_start, end_span.end_byte)
            self.push_piece(span, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
        if frame.kind is None:
            self.unknown_depth -= 1
            return
        kind = frame.kind
        limits = self.limits
        if kind is _ElementKind.KEY:
            units = tuple(ord(character) for character in frame.content)
            if len(units) > limits.max_string_code_units:
                raise PlistFormationFailure(
                    PlistFormationFailureKind.STRING_CODE_UNITS,
                    resource_name="string-code-units",
                    observed=len(units),
                    limit=limits.max_string_code_units,
                )
            if frame.value_allowed:
                pending = None if frame.scalar_unproven else PlistKey.from_unicode(frame.content)
                if self.stack:
                    parent = self.stack[-1]
                    if parent.value_allowed and parent.value is _FrameValue.DICT:
                        parent.dict_state.pending_key = pending
                        parent.dict_state.expect_value = True
            return
        value_ref = self._build_value(frame, end_span) if frame.value_allowed else None
        if value_ref is not None and frame.open_end > 0:
            try:
                self.value_spans[value_ref.index] = self.authority.span(
                    frame.open_start, end_span.end_byte
                )
            except Exception:
                pass
        missing_value = False
        if not self.stack:
            return
        parent = self.stack[-1]
        if parent.value is _FrameValue.ROOT:
            self.root_value_count += 1
            if value_ref is not None and self.root_value_ref is None:
                self.root_value_ref = value_ref
        elif parent.value is _FrameValue.DICT:
            state = parent.dict_state
            if state.expect_value:
                state.expect_value = False
                pending = state.pending_key
                state.pending_key = None
                if pending is not None:
                    if value_ref is not None:
                        group = state.groups.get(pending, 0) + 1
                        state.groups[pending] = group
                        if group > limits.max_duplicate_key_group_members:
                            raise PlistFormationFailure(
                                PlistFormationFailureKind.DUPLICATE_KEY_GROUP,
                                resource_name="duplicate-key-group",
                                observed=group,
                                limit=limits.max_duplicate_key_group_members,
                            )
                        if len(state.entries) >= limits.max_dict_entries:
                            raise PlistFormationFailure(
                                PlistFormationFailureKind.DICT_ENTRIES,
                                resource_name="dict-entries",
                                observed=len(state.entries) + 1,
                                limit=limits.max_dict_entries,
                            )
                        state.entries.append(PlistDictEntry(pending, value_ref))
                    else:
                        missing_value = True
                else:
                    missing_value = True
        elif parent.value is _FrameValue.ARRAY:
            if value_ref is not None:
                if len(parent.elements) >= limits.max_array_elements:
                    raise PlistFormationFailure(
                        PlistFormationFailureKind.ARRAY_ELEMENTS,
                        resource_name="array-elements",
                        observed=len(parent.elements) + 1,
                        limit=limits.max_array_elements,
                    )
                parent.elements.append(value_ref)
        if missing_value:
            self.recover(
                "plist.parse.dict-missing-value@1",
                DiagnosticCategory.SYNTAX,
                end_span,
            )

    def _build_value(self, frame: _Frame, close_span: object) -> PlistValueRef | None:
        """Parses one closing element's native value and adds it to the
        arena (parser_xml.rs:1602-1779)."""
        limits = self.limits
        kind = frame.kind
        if kind is _ElementKind.DICT:
            if frame.dict_state.expect_value:
                self.recover(
                    "plist.parse.dict-missing-value@1",
                    DiagnosticCategory.SYNTAX,
                    close_span,
                )
            value = PlistValue.dict(PlistDict(tuple(frame.dict_state.entries)))
            return self._arena_add(value)
        if kind is _ElementKind.ARRAY:
            value = PlistValue.array(PlistArray(tuple(frame.elements)))
            return self._arena_add(value)
        if kind in (_ElementKind.STRING, _ElementKind.KEY):
            if frame.scalar_unproven:
                return None
            units = tuple(ord(character) for character in frame.content)
            if len(units) > limits.max_string_code_units:
                raise PlistFormationFailure(
                    PlistFormationFailureKind.STRING_CODE_UNITS,
                    resource_name="string-code-units",
                    observed=len(units),
                    limit=limits.max_string_code_units,
                )
            return self._arena_add(PlistValue.string(PlistString(units)))
        if kind is _ElementKind.INTEGER:
            if frame.scalar_unproven:
                return None
            if not frame.content:
                self.recover(
                    "plist.parse.empty-value@1",
                    DiagnosticCategory.SYNTAX,
                    close_span,
                    {"element": "integer"},
                )
                return None
            value = parse_integer(frame.content)
            if value is not None:
                return self._arena_add(PlistValue.integer(PlistInteger(value)))
            self.recover("plist.parse.integer@1", DiagnosticCategory.SYNTAX, close_span)
            return None
        if kind is _ElementKind.REAL:
            if frame.scalar_unproven:
                return None
            if not frame.content:
                self.recover(
                    "plist.parse.empty-value@1",
                    DiagnosticCategory.SYNTAX,
                    close_span,
                    {"element": "real"},
                )
                return None
            value = parse_real(frame.content)
            if value is not None:
                return self._arena_add(PlistValue.real(PlistReal.double(value)))
            self.recover("plist.parse.real@1", DiagnosticCategory.SYNTAX, close_span)
            return None
        if kind is _ElementKind.DATE:
            if frame.scalar_unproven:
                return None
            if not frame.content:
                self.recover(
                    "plist.parse.empty-value@1",
                    DiagnosticCategory.SYNTAX,
                    close_span,
                    {"element": "date"},
                )
                return None
            seconds = parse_date(frame.content)
            if seconds is not None:
                return self._arena_add(PlistValue.date(PlistDate.from_seconds(seconds)))
            self.recover("plist.parse.date@1", DiagnosticCategory.SYNTAX, close_span)
            return None
        if kind is _ElementKind.DATA:
            if not frame.content:
                if frame.self_closing:
                    self.recover(
                        "plist.parse.empty-value@1",
                        DiagnosticCategory.SYNTAX,
                        close_span,
                        {"element": "data"},
                    )
                    return None
                return self._arena_add(PlistValue.data(PlistData(b"")))
            if frame.scalar_unproven:
                return None
            decoded = decode_base64(frame.content)
            if decoded is not None:
                if len(decoded) > limits.max_data_bytes:
                    raise PlistFormationFailure(
                        PlistFormationFailureKind.DATA_BYTES,
                        resource_name="data-bytes",
                        observed=len(decoded),
                        limit=limits.max_data_bytes,
                    )
                return self._arena_add(PlistValue.data(PlistData(decoded)))
            self.recover("plist.parse.data@1", DiagnosticCategory.SYNTAX, close_span)
            return None
        if kind in (_ElementKind.TRUE, _ElementKind.FALSE):
            if frame.scalar_unproven:
                return None
            return self._arena_add(
                PlistValue.boolean(PlistBoolean(kind is _ElementKind.TRUE))
            )
        return None

    def _arena_add(self, value: PlistValue) -> PlistValueRef:
        try:
            return self.arena.add(value)
        except PlistArenaError as error:
            if error.kind is PlistArenaErrorKind.OBJECT_LIMIT_EXCEEDED:
                raise PlistFormationFailure(
                    PlistFormationFailureKind.OBJECT_COUNT,
                    resource_name="object-count",
                    observed=self.arena.node_count(),
                    limit=error.limit,
                ) from None
            raise PlistFormationFailure(PlistFormationFailureKind.XML_INTERNAL) from None

    # -- text and cdata ------------------------------------------------------

    def _text(self, token: _Token) -> None:
        if self.unknown_depth > 0:
            return
        text = self.source.decoded_text()
        assert text is not None
        position = self._text_position()
        content = text[token.start : token.end]
        if position in (_TextPosition.OUTSIDE, _TextPosition.CONTAINER):
            if all(_is_ws(character) for character in content):
                self._push_whitespace_pieces(token.start, token.end)
            else:
                raw = self.raw_span(token.start, token.end)
                self.push_piece(raw, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
                self.recover(
                    "plist.parse.text-outside-value@1",
                    DiagnosticCategory.SYNTAX,
                    raw,
                )
        elif position is _TextPosition.BOOLEAN:
            if all(_is_ws(character) for character in content):
                self._push_whitespace_pieces(token.start, token.end)
            else:
                raw = self.raw_span(token.start, token.end)
                self.push_piece(raw, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
                self.recover(
                    "plist.parse.boolean-content@1",
                    DiagnosticCategory.SYNTAX,
                    raw,
                )
                if self.stack:
                    self.stack[-1].scalar_unproven = True
        else:
            resolved = self._resolve_fragments(token.start, token.end, _Normalization.TEXT, True)
            if self.stack:
                self.stack[-1].content += resolved

    def _cdata(self, token: _Token) -> None:
        if self.unknown_depth > 0:
            return
        text = self.source.decoded_text()
        assert text is not None
        position = self._text_position()
        if position in (_TextPosition.OUTSIDE, _TextPosition.CONTAINER):
            raw = self.raw_span(token.start, token.end)
            self.push_piece(raw, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
            self.recover(
                "plist.parse.text-outside-value@1",
                DiagnosticCategory.SYNTAX,
                raw,
            )
        elif position is _TextPosition.BOOLEAN:
            raw = self.raw_span(token.start, token.end)
            self.push_piece(raw, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
            self.recover(
                "plist.parse.boolean-content@1",
                DiagnosticCategory.SYNTAX,
                raw,
            )
            if self.stack:
                self.stack[-1].scalar_unproven = True
        else:
            open_span = self.raw_span(token.start, token.start + _CDATA_OPEN_BYTES)
            text_span = self.raw_span(token.value[0], token.value[1])
            close_span = self.raw_span(token.value[1], token.end)
            self.push_piece(open_span, PlistSyntaxKind.CDATA_OPEN, StructuralPieceKind.TOKEN)
            self.push_piece(text_span, PlistSyntaxKind.CDATA_TEXT, StructuralPieceKind.TOKEN)
            self.push_piece(close_span, PlistSyntaxKind.CDATA_CLOSE, StructuralPieceKind.TOKEN)
            normalized = _append_normalized("", text[token.value[0] : token.value[1]], _Normalization.TEXT)
            if self.stack:
                self.stack[-1].content += normalized

    def _text_position(self) -> _TextPosition:
        if not self.stack:
            return _TextPosition.OUTSIDE
        frame = self.stack[-1]
        if frame.kind is None:
            return _TextPosition.OUTSIDE
        if frame.kind in (_ElementKind.PLIST, _ElementKind.DICT, _ElementKind.ARRAY):
            return _TextPosition.CONTAINER
        if frame.kind in (_ElementKind.TRUE, _ElementKind.FALSE):
            return _TextPosition.BOOLEAN
        return _TextPosition.SCALAR

    # -- references and whitespace -------------------------------------------

    def _resolve_fragments(
        self, start: int, end: int, mode: _Normalization, emit_pieces: bool
    ) -> str:
        """Splits one decoded span into Text/EntityReference/
        CharacterReference pieces and returns the resolved normalized
        content (parser_xml.rs:1925-1995). Failing references resolve to
        nothing and publish a diagnostic."""
        text = self.source.decoded_text()
        assert text is not None
        content_text = text[start:end]
        content = ""
        if "&" not in content_text:
            if emit_pieces:
                raw = self.raw_span(start, end)
                self.push_piece(raw, PlistSyntaxKind.TEXT, StructuralPieceKind.TOKEN)
            return _append_normalized("", content_text, mode)
        cursor = 0
        index = 0
        while index < len(content_text):
            relative = content_text.find("&", index)
            if relative < 0:
                break
            at = relative
            if at > cursor:
                if emit_pieces:
                    raw = self.raw_span(start + cursor, start + at)
                    self.push_piece(raw, PlistSyntaxKind.TEXT, StructuralPieceKind.TOKEN)
                content += _append_normalized("", content_text[cursor:at], mode)
            semi = content_text.find(";", at + 1)
            if semi < 0:
                raw = self.raw_span(start + at, end)
                self.recover(
                    "plist.parse.reference@1",
                    DiagnosticCategory.SYNTAX,
                    raw,
                )
                if emit_pieces:
                    self.push_piece(raw, PlistSyntaxKind.TEXT, StructuralPieceKind.TOKEN)
                content += _append_normalized("", content_text[at:], mode)
                return content
            body = content_text[at + 1 : semi]
            ref_raw = self.raw_span(start + at, start + semi + 1)
            resolved = self._resolve_reference(body, ref_raw)
            if resolved is not None:
                if emit_pieces:
                    piece_kind = (
                        PlistSyntaxKind.CHARACTER_REFERENCE
                        if body.startswith("#")
                        else PlistSyntaxKind.ENTITY_REFERENCE
                    )
                    self.push_piece(ref_raw, piece_kind, StructuralPieceKind.TOKEN)
                content += resolved
            cursor = semi + 1
            index = semi + 1
        if cursor < len(content_text):
            if emit_pieces:
                raw = self.raw_span(start + cursor, end)
                self.push_piece(raw, PlistSyntaxKind.TEXT, StructuralPieceKind.TOKEN)
            content += _append_normalized("", content_text[cursor:], mode)
        return content

    def _resolve_reference(self, body: str, raw: object) -> str | None:
        """Resolves one ``&...;`` reference body; None is a recovered
        failure that contributes nothing (parser_xml.rs:1997-2060)."""
        if body.startswith("#"):
            digits = body[1:]
            is_hex = digits.startswith("x") or digits.startswith("X")
            if is_hex:
                digits = digits[1:]
            valid = bool(digits) and all(
                (character in "0123456789abcdefABCDEF") if is_hex else character.isdigit()
                for character in digits
            )
            value = None
            if valid:
                try:
                    value = int(digits, 16 if is_hex else 10)
                except ValueError:
                    value = None
            if value is not None and 0 <= value <= 0x10FFFF:
                character = chr(value)
                if _is_xml_char(character):
                    return character
            self.recover("plist.parse.reference@1", DiagnosticCategory.SYNTAX, raw)
            return None
        if not body:
            self.recover("plist.parse.reference@1", DiagnosticCategory.SYNTAX, raw)
            return None
        value = {"lt": "<", "gt": ">", "amp": "&", "apos": "'", "quot": '"'}.get(body)
        if value is not None:
            return value
        self.recover(
            "plist.parse.entity@1",
            DiagnosticCategory.CONFORMANCE,
            raw,
            {"name": body},
        )
        return None

    def _push_whitespace_pieces(self, start: int, end: int) -> None:
        """Splits one decoded whitespace run into Whitespace and LineBreak
        trivia pieces; defensive non-whitespace bytes become error regions
        (parser_xml.rs:2062-2116)."""
        text = self.source.decoded_text()
        assert text is not None
        content = text[start:end]
        runs: list[tuple[int, int, PlistSyntaxKind, StructuralPieceKind]] = []
        cursor = 0
        while cursor < len(content):
            byte = content[cursor]
            if not _is_ws(byte):
                run_start = cursor
                while cursor < len(content) and not _is_ws(content[cursor]):
                    cursor += 1
                runs.append(
                    (
                        run_start,
                        cursor,
                        PlistSyntaxKind.ERROR_REGION,
                        StructuralPieceKind.ERROR_REGION,
                    )
                )
                continue
            line_break = byte in ("\n", "\r")
            run_start = cursor
            cursor += (
                2
                if byte == "\r" and cursor + 1 < len(content) and content[cursor + 1] == "\n"
                else 1
            )
            while cursor < len(content) and (content[cursor] in ("\n", "\r")) == line_break:
                cursor += 1
            runs.append(
                (
                    run_start,
                    cursor,
                    PlistSyntaxKind.LINE_BREAK if line_break else PlistSyntaxKind.WHITESPACE,
                    StructuralPieceKind.TRIVIA,
                )
            )
        for run_start, run_end, kind, structural in runs:
            raw = self.raw_span(start + run_start, start + run_end)
            self.push_piece(raw, kind, structural)

    # -- finish ---------------------------------------------------------------

    def _finish(self) -> PlistFormedXml:
        if self.stack:
            unclosed = self.stack[-1]
            span = None
            try:
                span = self.authority.span(unclosed.open_start, unclosed.open_end)
            except Exception:
                span = None
            self.recover(
                "plist.parse.unclosed-element@1",
                DiagnosticCategory.SYNTAX,
                span,
                {"element": unclosed.name},
            )
        unknown_tail = next(
            (
                frame.unknown_subtree_start
                for frame in self.stack
                if frame.unknown_subtree_start is not None
            ),
            None,
        )
        if unknown_tail is not None:
            span = self.authority.span(unknown_tail, self.source.len())
            self.push_piece(span, PlistSyntaxKind.ERROR_REGION, StructuralPieceKind.ERROR_REGION)
        if not self.any_top_level:
            self.recover(
                "plist.parse.missing-root@1",
                DiagnosticCategory.SYNTAX,
                None,
            )
        document = None
        if self.plist_root_seen:
            if self.root_value_count == 0:
                self.recover(
                    "plist.parse.root-value-count@1",
                    DiagnosticCategory.SYNTAX,
                    None,
                    {"count": "0"},
                )
            elif self.root_value_count == 1:
                root_ref = self.root_value_ref
                if root_ref is not None:
                    try:
                        document = self.arena.build(root_ref)
                    except PlistArenaError as error:
                        if error.kind is PlistArenaErrorKind.CONTAINER_DEPTH_LIMIT_EXCEEDED:
                            raise PlistFormationFailure(
                                PlistFormationFailureKind.CONTAINER_DEPTH,
                                resource_name="container-depth",
                                observed=error.node.index,
                                limit=error.limit,
                            ) from None
                        raise PlistFormationFailure(
                            PlistFormationFailureKind.XML_INTERNAL
                        ) from None
            else:
                self.recover(
                    "plist.parse.root-value-count@1",
                    DiagnosticCategory.SYNTAX,
                    None,
                    {"count": str(self.root_value_count)},
                )
        status = FormationStatus.RECOVERED if self.recovered else FormationStatus.COMPLETE
        source_len = self.source.len()
        paired = list(zip(self.pieces, self.syntax_kinds))
        paired.sort(key=lambda item: item[0].span.start_byte)
        final_pieces: list[StructuralPiece] = []
        final_kinds: list[PlistSyntaxKind] = []
        next_byte = 0
        gap_kind = (
            PlistSyntaxKind.ERROR_REGION
            if self.recovered
            else PlistSyntaxKind.WHITESPACE
        )
        gap_structural = (
            StructuralPieceKind.ERROR_REGION
            if self.recovered
            else StructuralPieceKind.TRIVIA
        )
        for piece, kind in paired:
            start = piece.span.start_byte
            if start > next_byte:
                gap = self.authority.span(next_byte, start)
                self.push_piece(gap, gap_kind, gap_structural)
            next_byte = piece.span.end_byte
            final_pieces.append(piece)
            final_kinds.append(kind)
        if next_byte < source_len:
            gap = self.authority.span(next_byte, source_len)
            self.push_piece(gap, gap_kind, gap_structural)
        # Gap pieces were appended after the original pieces.
        for piece, kind in zip(self.pieces[len(paired) :], self.syntax_kinds[len(paired) :]):
            final_pieces.append(piece)
            final_kinds.append(kind)
        all_paired = list(zip(final_pieces, final_kinds))
        all_paired.sort(key=lambda item: item[0].span.start_byte)
        structural = [piece for piece, _ in all_paired]
        kinds = [kind for _, kind in all_paired]
        error_regions = sum(
            1 for piece in structural if piece.kind is StructuralPieceKind.ERROR_REGION
        )
        if error_regions > self.limits.max_recovery_regions:
            raise PlistFormationFailure(
                PlistFormationFailureKind.RECOVERY_REGIONS,
                resource_name="recovery-regions",
                observed=error_regions,
                limit=self.limits.max_recovery_regions,
            )
        try:
            index = LosslessStructuralIndex.new(self.authority.identity, source_len, structural)
        except Exception:
            raise PlistFormationFailure(PlistFormationFailureKind.XML_COVERAGE) from None
        sort_diagnostics(self.diagnostics)
        return PlistFormedXml(
            source=self.source,
            authority=self.authority,
            status=status,
            diagnostics=tuple(self.diagnostics),
            document=document,
            syntax=index,
            syntax_kinds=tuple(kinds),
            limits=self.limits,
            root_node=self.authority.node_ref(0, NodeRole.PLIST_DOCUMENT),
            value_spans=dict(self.value_spans),
        )


# ---------------------------------------------------------------------------
# Value grammars (RFC 0013 §4.5-§4.8)
# ---------------------------------------------------------------------------


def parse_integer(content: str) -> int | None:
    """Signed 64-bit integer grammar (RFC 0013 §4.5): ``S*(-|+)?S*[0-9]+``
    and ``S*(-|+)?S*0[xX][0-9a-fA-F]+`` (parser_xml.rs:2453-2516)."""
    bytes_ = content.strip(" \t\n\r").encode("utf-8")
    index = 0
    negative = False
    if bytes_ and bytes_[0] in (0x2D, 0x2B):
        negative = bytes_[0] == 0x2D
        index = 1
    while index < len(bytes_) and _is_ws_byte(bytes_[index]):
        index += 1
    hex_mode = (
        index + 1 < len(bytes_)
        and bytes_[index] == 0x30
        and bytes_[index + 1] in (0x78, 0x58)
    )
    if hex_mode:
        index += 2
    start = index
    while index < len(bytes_) and (
        _is_hex_digit(bytes_[index]) if hex_mode else _is_decimal_digit(bytes_[index])
    ):
        index += 1
    if index == start:
        return None
    while index < len(bytes_) and _is_ws_byte(bytes_[index]):
        index += 1
    if index != len(bytes_):
        return None
    digits = bytes_[start:index].decode("ascii")
    try:
        magnitude = int(digits, 16 if hex_mode else 10)
    except ValueError:
        return None
    if negative:
        if magnitude > (1 << 63):
            return None
        if magnitude == (1 << 63):
            return -(1 << 63)
        return -magnitude
    if magnitude > (1 << 63) - 1:
        return None
    return magnitude


def _is_decimal_digit(byte: int) -> bool:
    return 0x30 <= byte <= 0x39


def _is_hex_digit(byte: int) -> bool:
    return 0x30 <= byte <= 0x39 or 0x41 <= byte <= 0x46 or 0x61 <= byte <= 0x66


def parse_real(content: str) -> float | None:
    """Real grammar (RFC 0013 §4.6): the special spellings ``nan``, ``inf``,
    ``±inf``, ``infinity``, ``±infinity`` (case-insensitive) and otherwise
    ``sign? digits ('.' digits)? ([eE] sign? digits)?``
    (parser_xml.rs:2521-2578)."""
    import math

    trimmed = content.strip(" \t\n\r")
    lower = trimmed.lower()
    if lower == "nan":
        return math.nan
    if lower in ("inf", "+inf", "infinity", "+infinity"):
        return math.inf
    if lower in ("-inf", "-infinity"):
        return -math.inf
    bytes_ = trimmed.encode("utf-8")
    index = 0
    if bytes_ and bytes_[0] in (0x2B, 0x2D):
        index += 1
    digits_start = index
    while index < len(bytes_) and _is_decimal_digit(bytes_[index]):
        index += 1
    if index == digits_start:
        return None
    if index < len(bytes_) and bytes_[index] == 0x2E:
        index += 1
        fraction_start = index
        while index < len(bytes_) and _is_decimal_digit(bytes_[index]):
            index += 1
        if index == fraction_start:
            return None
    if index < len(bytes_) and bytes_[index] in (0x65, 0x45):
        index += 1
        if index < len(bytes_) and bytes_[index] in (0x2B, 0x2D):
            index += 1
        exponent_start = index
        while index < len(bytes_) and _is_decimal_digit(bytes_[index]):
            index += 1
        if index == exponent_start:
            return None
    if index != len(bytes_):
        return None
    try:
        return float(trimmed)
    except ValueError:
        return None


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Proleptic Gregorian calendar days since the Unix epoch (Howard
    Hinnant's ``days_from_civil``; parser_xml.rs:2653-2660)."""
    year = year - 1 if month <= 2 else year
    era = (year if year >= 0 else year - 399) // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month - 3 if month > 2 else month + 9) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def _is_leap_year(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def _days_in_month(year: int, month: int) -> int:
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    if month == 2:
        return 29 if _is_leap_year(year) else 28
    return 0


def parse_date(content: str) -> float | None:
    """XML date grammar (RFC 0013 §4.7): ``[-]YYYY-MM-DDTHH:MM:SSZ`` with
    calendar validation; returns exact double seconds since the plist epoch
    (parser_xml.rs:2580-2632)."""
    from consema.plist.native import PLIST_EPOCH_OFFSET_UNIX

    text = content
    index = 0
    negative = text.startswith("-")
    if negative:
        index = 1
    year_start = index
    while index < len(text) and text[index].isdigit():
        index += 1
    if index == year_start:
        return None
    try:
        year = int(text[year_start:index])
    except ValueError:
        return None
    if year > 0xFFFFFFFF:
        return None
    fields = _expect_two_digits(text, index, "-")
    if fields is None:
        return None
    index, month = fields
    fields = _expect_two_digits(text, index, "-")
    if fields is None:
        return None
    index, day = fields
    fields = _expect_two_digits(text, index, "T")
    if fields is None:
        return None
    index, hour = fields
    fields = _expect_two_digits(text, index, ":")
    if fields is None:
        return None
    index, minute = fields
    fields = _expect_two_digits(text, index, ":")
    if fields is None:
        return None
    index, second = fields
    if index >= len(text) or text[index] != "Z":
        return None
    index += 1
    if index != len(text):
        return None
    year_signed = -year if negative else year
    if not 1 <= month <= 12:
        return None
    days_in_month = _days_in_month(year_signed, month)
    if day == 0 or day > days_in_month:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    days = _days_from_civil(year_signed, month, day)
    time = hour * 3600 + minute * 60 + second
    unix = days * 86400 + time
    return float(unix) - PLIST_EPOCH_OFFSET_UNIX


def _expect_two_digits(text: str, index: int, sep: str) -> tuple[int, int] | None:
    """Consumes ``sep`` then exactly two decimal digits
    (parser_xml.rs:2634-2649)."""
    if index >= len(text) or text[index] != sep:
        return None
    index += 1
    if index + 2 > len(text) or not text[index].isdigit() or not text[index + 1].isdigit():
        return None
    value = int(text[index]) * 10 + int(text[index + 1])
    return (index + 2, value)


def decode_base64(content: str) -> bytes | None:
    """Strict base64 decoding with the standard alphabet (RFC 0013 §4.8):
    ASCII whitespace between characters, padding exactly as required for the
    final incomplete group, and nothing else (parser_xml.rs:2684-2757)."""
    compact = bytearray()
    for byte in content.encode("utf-8"):
        if _is_ws_byte(byte):
            continue
        compact.append(byte)
    length = len(compact)
    if length == 0:
        return b""
    if length % 4 == 1:
        return None
    end = length
    padding = 0
    if compact[end - 1] == 0x3D:
        padding += 1
        if end >= 2 and compact[end - 2] == 0x3D:
            padding += 1
    if padding > 0:
        if length % 4 != 0:
            return None
        if padding > 2:
            return None
        for index in range(length - padding, length):
            if compact[index] != 0x3D:
                return None
        end = length - padding
    if end % 4 == 1:
        return None
    out = bytearray()
    for index in range(0, end, 4):
        if index + 4 > end:
            return None
        values = [
            _base64_value(compact[index]),
            _base64_value(compact[index + 1]),
            _base64_value(compact[index + 2]),
            _base64_value(compact[index + 3]),
        ]
        if any(value is None for value in values):
            return None
        first, second, third, fourth = values
        out.append((first << 2) | (second >> 4))
        if index + 2 < end:
            out.append(((second & 0x0F) << 4) | (third >> 2))
        if index + 3 < end:
            out.append(((third & 0x03) << 6) | fourth)
    return bytes(out)


def _base64_value(byte: int) -> int | None:
    """Standard alphabet value (parser_xml.rs:2758-2767)."""
    if 0x41 <= byte <= 0x5A:
        return byte - 0x41
    if 0x61 <= byte <= 0x7A:
        return byte - 0x61 + 26
    if 0x30 <= byte <= 0x39:
        return byte - 0x30 + 52
    if byte == 0x2B:
        return 62
    if byte == 0x2F:
        return 63
    return None


# ---------------------------------------------------------------------------
# Shared text helpers (parser_xml.rs:2396-2449)
# ---------------------------------------------------------------------------


def _skip_declaration_spaces(text: str, rel: int) -> int:
    while rel < len(text) and _is_ws(text[rel]):
        rel += 1
    return rel


def _append_normalized(target: str, text: str, mode: _Normalization) -> str:
    """Appends literal text with the requested normalization (RFC 0013 §4.9
    and XML 1.0 attribute normalization; parser_xml.rs:2415-2439)."""
    out = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            character = "\n"
        if mode is _Normalization.ATTRIBUTE and character in (" ", "\t", "\n"):
            character = " "
        out.append(character)
        index += 1
    return target + "".join(out)


# ---------------------------------------------------------------------------
# Formed XML document
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlistFormedXml:
    """One formed ``plist.xml@1`` document (parser_xml.rs:253-362).

    ``Complete`` requires exhaustive byte coverage under the Profile's
    grammar and every configured limit. ``Recovered`` retains the immutable
    source, exhaustive piece coverage, ordered diagnostics, every
    independently proven construct, and — when the native value graph is
    provable — the native document.
    """

    source: object  # consema.document.source.SourceSnapshot
    authority: DocumentAuthority
    status: FormationStatus
    diagnostics: tuple[PlistDiagnostic, ...]
    document: PlistDocument | None
    syntax: LosslessStructuralIndex
    syntax_kinds: tuple[PlistSyntaxKind, ...]
    limits: PlistParseLimits
    root_node: NodeRef
    value_spans: dict = field(default_factory=dict, repr=False)

    def render(self) -> bytes:
        """Exact original bytes; unmodified rendering is byte-exact."""
        return self.source.bytes()

    def lossless_structural_index(self) -> LosslessStructuralIndex:
        return self.syntax

    def lossless_syntax_kinds(self) -> tuple[PlistSyntaxKind, ...]:
        return self.syntax_kinds


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_xml(
    raw: bytes,
    selection: PlistEncodingSelection,
    limits: PlistParseLimits,
) -> PlistFormedXml:
    """Forms one ``plist.xml@1`` document from raw bytes (RFC 0013 §3;
    parser_xml.rs:372-393).

    The source contract follows RFC 0013 §2.1: no-BOM source defaults to
    UTF-8, a BOM or an explicit caller choice is evidence that never
    contradicts the other, and only the UTF-8/UTF-16 document-entity table
    is admitted. Any other selection is a fatal source-contract conflict
    (``plist.xml.encoding@1``)."""
    request = _encoding_request(selection)
    try:
        source = SourceSnapshot.from_raw(
            raw,
            request,
            SourceLimits(
                max_raw_bytes=limits.common.max_source_bytes,
                max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
                max_decoded_scalars=limits.max_decoded_scalars,
            ),
        )
    except SourceError as error:
        raise PlistFormationFailure(
            PlistFormationFailureKind.SOURCE, source=error
        ) from None
    _validate_profile_encoding(source, selection)
    decoded = source.decoded_text()
    if decoded is None:
        raise PlistFormationFailure(PlistFormationFailureKind.XML_ENCODING)
    return _Parser(source, limits).parse(decoded)


def _encoding_request(selection: PlistEncodingSelection) -> EncodingRequest:
    """Resolves the source encoding request under the RFC 0013 §2.1 table
    (parser_xml.rs:395-421)."""
    from consema.document.source import BomPolicy

    if selection.kind == "ProfileDefault":
        return EncodingRequest.new(SourceEncoding.utf8()).with_bom_policy(
            BomPolicy.DETECT_UNICODE
        )
    encoding = selection.encoding
    assert encoding is not None
    from consema.document.source import SourceEncodingKind

    admitted = encoding.kind in (
        SourceEncodingKind.UTF8,
        SourceEncodingKind.UTF16LE,
        SourceEncodingKind.UTF16BE,
    )
    if not admitted:
        raise PlistFormationFailure(PlistFormationFailureKind.XML_ENCODING)
    return EncodingRequest.new(SourceEncoding.utf8()).with_caller_override(encoding)


def _validate_profile_encoding(
    source: SourceSnapshot, selection: PlistEncodingSelection
) -> None:
    """Profile-specific encoding acceptance (parser_xml.rs:423-449)."""
    from consema.document.source import SourceEncodingKind

    facts = source.encoding_facts()
    if selection.kind == "ProfileDefault":
        valid = facts.selected.kind in (
            SourceEncodingKind.UTF8,
            SourceEncodingKind.UTF16LE,
            SourceEncodingKind.UTF16BE,
        )
    elif selection.encoding == SourceEncoding.utf8():
        valid = facts.selected == SourceEncoding.utf8()
    elif selection.encoding == SourceEncoding.utf16le():
        valid = (
            facts.selected == SourceEncoding.utf16le()
            and facts.bom is not None
            and facts.bom.value == "Utf16Le"
        )
    elif selection.encoding == SourceEncoding.utf16be():
        valid = (
            facts.selected == SourceEncoding.utf16be()
            and facts.bom is not None
            and facts.bom.value == "Utf16Be"
        )
    else:
        valid = False
    if not valid:
        raise PlistFormationFailure(PlistFormationFailureKind.XML_ENCODING)
