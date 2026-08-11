"""Byte-level JSON/JSONC/JSON5 lexer: exhaustive token/trivia/error-region
coverage with bounded recovery.

Authority (Rust arbitration for exact lexical boundaries):

- Strict/JSONC lexing: crates/consema-json/src/parser.rs:174-402 — BOM piece
  plus json.strict.leading-bom@1 under the strict profile (parser.rs:193-214),
  whitespace (parser.rs:218-225), line/block comments with strict rejection
  (parser.rs:226-278), strings (parser.rs:285-314), numbers scanned over
  ``[0-9+-.eE]`` and validated by valid_json_number (parser.rs:315-338,
  776-815), words (parser.rs:339-362), unexpected-character fallback with
  UTF-8 width (parser.rs:363-375), the token-count fatal limit
  (parser.rs:389-395).
- JSON5 lexing: parser.rs:404-581 — U+FEFF BOM piece (parser.rs:414-421),
  whitespace/line/block comments (parser.rs:425-460), strings with either
  quote and CRLF continuations (parser.rs:469-502), numbers via
  scan_json5_number_candidate + valid_json5_number (parser.rs:503-524,
  689-760), identifiers via scan_json5_identifier +
  decode_identifier_escape (parser.rs:525-541, 625-687), and the
  json5.syntax.invalid-identifier@1 recovery (parser.rs:531-539).
- Character classes: parser.rs:590-623 (also re-exported from kinds.py).
- Fatal token-count limit: parser.rs:389-395 (strict) and 568-574 (JSON5);
  the limit binds lexemes (tokens plus trivia plus error regions).
- Diagnostics: source_diagnostic/source_warning shapes parser.rs:1458-1498;
  the strict BOM warning severity parser.rs:200-212.

Every source byte is partitioned into exactly one lexeme (no gap, no
overlap); the parser layer validates the resulting structural index.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import DocumentAuthority
from consema.document.limits import ParseLimits
from consema.json.errors import (
    JsonDiagnostic,
    JsonFormationFailure,
    JsonFormationFailureKind,
    JsonSeverity,
)
from consema.json.kinds import (
    JsonProfile,
    JsonSyntaxKind,
    is_json5_identifier_continue,
    is_json5_identifier_start,
    is_json5_line_terminator,
    is_json5_whitespace,
)
from consema.protocol.error_registry import DiagnosticCategory


class TokenKind(enum.Enum):
    """Token kinds consumed by the parser (parser.rs:15-29)."""

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


_TOKEN_SYNTAX_KIND = {
    TokenKind.LEFT_BRACE: JsonSyntaxKind.LEFT_BRACE,
    TokenKind.RIGHT_BRACE: JsonSyntaxKind.RIGHT_BRACE,
    TokenKind.LEFT_BRACKET: JsonSyntaxKind.LEFT_BRACKET,
    TokenKind.RIGHT_BRACKET: JsonSyntaxKind.RIGHT_BRACKET,
    TokenKind.COLON: JsonSyntaxKind.COLON,
    TokenKind.COMMA: JsonSyntaxKind.COMMA,
    TokenKind.STRING: JsonSyntaxKind.STRING,
    TokenKind.IDENTIFIER: JsonSyntaxKind.IDENTIFIER,
    TokenKind.NUMBER: JsonSyntaxKind.NUMBER,
    TokenKind.TRUE: JsonSyntaxKind.TRUE,
    TokenKind.FALSE: JsonSyntaxKind.FALSE,
    TokenKind.NULL: JsonSyntaxKind.NULL,
}


@dataclass(frozen=True, slots=True)
class Token:
    """One token over original raw bytes (parser.rs:31-36)."""

    kind: TokenKind
    start: int
    end: int


class LexemeClass(enum.Enum):
    """Lexeme classification (parser.rs:45-50)."""

    TOKEN = "Token"
    TRIVIA = "Trivia"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class Lexeme:
    """One exhaustive source byte interval (parser.rs:38-43)."""

    start: int
    end: int
    class_: LexemeClass
    token_kind: TokenKind | None = None
    syntax_kind: JsonSyntaxKind | None = None

    def syntax_kind_of(self) -> JsonSyntaxKind:
        """Stable syntax classification of this lexeme (parser.rs:52-70)."""
        if self.class_ is LexemeClass.TOKEN:
            return _TOKEN_SYNTAX_KIND[self.token_kind]
        if self.class_ is LexemeClass.TRIVIA:
            return self.syntax_kind
        return JsonSyntaxKind.ERROR_REGION


@dataclass(frozen=True, slots=True)
class Lexed:
    """Lexing outcome: ordered lexemes, parser tokens, recovery flag
    (parser.rs:168-172)."""

    lexemes: tuple[Lexeme, ...]
    tokens: tuple[Token, ...]
    recovered: bool


class DiagnosticSink:
    """Bounded diagnostic accumulation with stable occurrence ordinals
    (parser.rs:1500-1537)."""

    def __init__(self, max_diagnostics: int) -> None:
        self._max = max_diagnostics
        self._occurrence = 0
        self._truncated = False
        self._diagnostics: list[JsonDiagnostic] = []

    def push(self, diagnostic: JsonDiagnostic) -> None:
        diagnostic = JsonDiagnostic(
            code=diagnostic.code,
            category=diagnostic.category,
            severity=diagnostic.severity,
            primary=diagnostic.primary,
            occurrence=self._occurrence,
            arguments=dict(diagnostic.arguments),
            related=diagnostic.related,
            notes=diagnostic.notes,
        )
        self._occurrence += 1
        if len(self._diagnostics) < self._max:
            self._diagnostics.append(diagnostic)
        elif not self._truncated:
            self._truncated = True
            self._diagnostics.append(
                JsonDiagnostic(
                    code="core.diagnostic.truncated@1",
                    category=DiagnosticCategory.RESOURCE,
                    severity=JsonSeverity.WARNING,
                    primary=None,
                    occurrence=self._occurrence,
                )
            )

    def finish(self) -> list[JsonDiagnostic]:
        return self._diagnostics


def lex(
    bytes_: bytes,
    profile: JsonProfile,
    authority: DocumentAuthority,
    limits: ParseLimits,
    sink: DiagnosticSink,
) -> Lexed:
    """Lexes one complete UTF-8 source into exhaustive coverage (parser.rs:174-187)."""
    if profile.is_json5():
        return lex_json5(
            bytes_.decode("utf-8"),
            authority,
            limits,
            sink,
        )
    return lex_strict(bytes_, profile, authority, limits, sink)


def lex_strict(
    bytes_: bytes,
    profile: JsonProfile,
    authority: DocumentAuthority,
    limits: ParseLimits,
    sink: DiagnosticSink,
) -> Lexed:
    """Strict/JSONC lexer (parser.rs:189-401)."""
    lexemes: list[Lexeme] = []
    tokens: list[Token] = []
    offset = 0
    recovered = False

    if bytes_.startswith(b"\xef\xbb\xbf"):
        lexemes.append(Lexeme(0, 3, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.BOM))
        if profile is JsonProfile.STRICT_V1:
            sink.push(
                source_diagnostic(
                    authority,
                    "json.strict.leading-bom@1",
                    DiagnosticCategory.CONFORMANCE,
                    0,
                    3,
                    severity=JsonSeverity.WARNING,
                )
            )
        offset = 3

    while offset < len(bytes_):
        start = offset
        octet = bytes_[offset]
        if octet in (0x20, 0x09, 0x0D, 0x0A):
            offset += 1
            while offset < len(bytes_) and bytes_[offset] in (0x20, 0x09, 0x0D, 0x0A):
                offset += 1
            lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.WHITESPACE)
        elif octet == 0x2F and offset + 1 < len(bytes_) and bytes_[offset + 1] == 0x2F:
            offset += 2
            while offset < len(bytes_) and bytes_[offset] not in (0x0D, 0x0A):
                offset += 1
            if not profile.permits_jsonc_extensions():
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.strict.comment-not-allowed@1",
                        DiagnosticCategory.CONFORMANCE,
                        start,
                        offset,
                    )
                )
            lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.LINE_COMMENT)
        elif octet == 0x2F and offset + 1 < len(bytes_) and bytes_[offset + 1] == 0x2A:
            offset += 2
            closed = False
            while offset + 1 < len(bytes_):
                if bytes_[offset] == 0x2A and bytes_[offset + 1] == 0x2F:
                    offset += 2
                    closed = True
                    break
                offset += 1
            if closed:
                if not profile.permits_jsonc_extensions():
                    recovered = True
                    sink.push(
                        source_diagnostic(
                            authority,
                            "json.strict.comment-not-allowed@1",
                            DiagnosticCategory.CONFORMANCE,
                            start,
                            offset,
                        )
                    )
                lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.BLOCK_COMMENT)
            else:
                offset = len(bytes_)
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.unterminated-block-comment@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif octet == 0x7B:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.LEFT_BRACE)
        elif octet == 0x7D:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.RIGHT_BRACE)
        elif octet == 0x5B:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.LEFT_BRACKET)
        elif octet == 0x5D:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.RIGHT_BRACKET)
        elif octet == 0x3A:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.COLON)
        elif octet == 0x2C:
            offset += 1
            lexeme = single_token_lexeme(start, offset, TokenKind.COMMA)
        elif octet == 0x22:
            offset += 1
            escaped = False
            closed = False
            while offset < len(bytes_):
                current = bytes_[offset]
                offset += 1
                if escaped:
                    escaped = False
                elif current == 0x5C:
                    escaped = True
                elif current == 0x22:
                    closed = True
                    break
            if closed:
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.STRING)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.unterminated-string@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif octet == 0x2D or 0x30 <= octet <= 0x39:
            offset += 1
            while (
                offset < len(bytes_)
                and bytes_[offset] in b"0123456789+-.eE"
            ):
                offset += 1
            if valid_json_number(bytes_[start:offset]):
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.NUMBER)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.invalid-number@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif 0x61 <= octet <= 0x7A or 0x41 <= octet <= 0x5A or octet == 0x5F:
            offset += 1
            while (
                offset < len(bytes_)
                and (
                    bytes_[offset] in b"abcdefghijklmnopqrstuvwxyz"
                    or bytes_[offset] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    or bytes_[offset] in b"0123456789"
                    or bytes_[offset] == 0x5F
                )
            ):
                offset += 1
            word = bytes_[start:offset]
            if word == b"true":
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.TRUE)
            elif word == b"false":
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.FALSE)
            elif word == b"null":
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.NULL)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.unexpected-word@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        else:
            width = utf8_width(octet)
            offset = min(offset + width, len(bytes_))
            recovered = True
            sink.push(
                source_diagnostic(
                    authority,
                    "json.syntax.unexpected-character@1",
                    DiagnosticCategory.SYNTAX,
                    start,
                    offset,
                )
            )
            lexeme = Lexeme(start, offset, LexemeClass.ERROR)

        lexemes.append(lexeme)
        if lexeme.class_ is LexemeClass.TOKEN:
            tokens.append(Token(lexeme.token_kind, lexeme.start, lexeme.end))
        if len(lexemes) > limits.max_token_count:
            raise JsonFormationFailure(
                JsonFormationFailureKind.TOKEN_COUNT,
                observed=len(lexemes),
                limit=limits.max_token_count,
            )

    return Lexed(tuple(lexemes), tuple(tokens), recovered)


def lex_json5(
    source: str,
    authority: DocumentAuthority,
    limits: ParseLimits,
    sink: DiagnosticSink,
) -> Lexed:
    """JSON5 lexer over decoded text (parser.rs:404-581).

    The lexer walks scalar indices (Python str indices); every emitted
    lexeme/token span is converted to raw BYTE offsets via a cumulative
    UTF-8 width table, because the structural index and all downstream
    spans are byte ranges (RFC 0003 \u00a75).
    """
    lexemes: list[Lexeme] = []
    tokens: list[Token] = []
    byte_at = [0]
    for character in source:
        byte_at.append(byte_at[-1] + len(character.encode("utf-8")))
    offset = 0
    recovered = False

    if source.startswith("\ufeff"):
        lexemes.append(Lexeme(0, 1, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.BOM))
        offset = 1

    while offset < len(source):
        start = offset
        character = char_at(source, offset)
        if is_json5_whitespace(character):
            offset += len(character)
            while offset < len(source) and is_json5_whitespace(char_at(source, offset)):
                offset += len(char_at(source, offset))
            lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.WHITESPACE)
        elif source.startswith("//", start):
            offset += 2
            while offset < len(source) and not is_json5_line_terminator(char_at(source, offset)):
                offset += len(char_at(source, offset))
            lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.LINE_COMMENT)
        elif source.startswith("/*", start):
            offset += 2
            closed = False
            while offset < len(source):
                if source.startswith("*/", offset):
                    offset += 2
                    closed = True
                    break
                offset += len(char_at(source, offset))
            if closed:
                lexeme = Lexeme(start, offset, LexemeClass.TRIVIA, syntax_kind=JsonSyntaxKind.BLOCK_COMMENT)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.unterminated-block-comment@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif character in "{}[]:,":
            offset += 1
            lexeme = single_token_lexeme(start, offset, _PUNCTUATION[character])
        elif character in ("'", '"'):
            quote = character
            offset += len(quote)
            closed = False
            while offset < len(source):
                current = char_at(source, offset)
                offset += len(current)
                if current == "\\":
                    if offset < len(source):
                        escaped = char_at(source, offset)
                        offset += len(escaped)
                        if escaped == "\r" and source.startswith("\n", offset):
                            offset += 1
                elif current == quote:
                    closed = True
                    break
            if closed:
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.STRING)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.unterminated-string@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif (
            character in "+-.0123456789"
            and (
                character != "."
                or (
                    offset + 1 < len(source)
                    and char_at(source, offset + 1).isascii()
                    and char_at(source, offset + 1).isdigit()
                )
            )
        ):
            offset = scan_json5_number_candidate(source, offset)
            if valid_json5_number(source[start:offset]):
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.NUMBER)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json.syntax.invalid-number@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        elif character == "\\" or is_json5_identifier_start(character):
            end, valid = scan_json5_identifier(source, start)
            offset = end
            if valid:
                lexeme = Lexeme(start, offset, LexemeClass.TOKEN, token_kind=TokenKind.IDENTIFIER)
            else:
                recovered = True
                sink.push(
                    source_diagnostic(
                        authority,
                        "json5.syntax.invalid-identifier@1",
                        DiagnosticCategory.SYNTAX,
                        start,
                        offset,
                    )
                )
                lexeme = Lexeme(start, offset, LexemeClass.ERROR)
        else:
            offset += len(character)
            recovered = True
            sink.push(
                source_diagnostic(
                    authority,
                    "json.syntax.unexpected-character@1",
                    DiagnosticCategory.SYNTAX,
                    start,
                    offset,
                )
            )
            lexeme = Lexeme(start, offset, LexemeClass.ERROR)

        lexemes.append(lexeme)
        if lexeme.class_ is LexemeClass.TOKEN:
            tokens.append(Token(lexeme.token_kind, lexeme.start, lexeme.end))
        if len(lexemes) > limits.max_token_count:
            raise JsonFormationFailure(
                JsonFormationFailureKind.TOKEN_COUNT,
                observed=len(lexemes),
                limit=limits.max_token_count,
            )

    remapped_lexemes = tuple(
        Lexeme(
            byte_at[lexeme.start],
            byte_at[lexeme.end],
            lexeme.class_,
            token_kind=lexeme.token_kind,
            syntax_kind=lexeme.syntax_kind,
        )
        for lexeme in lexemes
    )
    remapped_tokens = tuple(
        Token(token.kind, byte_at[token.start], byte_at[token.end])
        for token in tokens
    )
    return Lexed(remapped_lexemes, remapped_tokens, recovered)


_PUNCTUATION = {
    "{": TokenKind.LEFT_BRACE,
    "}": TokenKind.RIGHT_BRACE,
    "[": TokenKind.LEFT_BRACKET,
    "]": TokenKind.RIGHT_BRACKET,
    ":": TokenKind.COLON,
    ",": TokenKind.COMMA,
}


def single_token_lexeme(start: int, end: int, kind: TokenKind) -> Lexeme:
    return Lexeme(start, end, LexemeClass.TOKEN, token_kind=kind)


def char_at(source: str, offset: int) -> str:
    """One scalar at a validated offset (parser.rs:583-588)."""
    return source[offset : offset + 1]


def utf8_width(leading: int) -> int:
    """UTF-8 leading-byte width (parser.rs:767-774)."""
    if leading <= 0x7F:
        return 1
    if 0xC0 <= leading <= 0xDF:
        return 2
    if 0xE0 <= leading <= 0xEF:
        return 3
    return 4


def valid_json_number(bytes_: bytes) -> bool:
    """Strict JSON number grammar (parser.rs:776-815)."""
    index = 0
    if index < len(bytes_) and bytes_[index] == 0x2D:
        index += 1
    if index >= len(bytes_):
        return False
    octet = bytes_[index]
    if octet == 0x30:
        index += 1
    elif 0x31 <= octet <= 0x39:
        index += 1
        while index < len(bytes_) and 0x30 <= bytes_[index] <= 0x39:
            index += 1
    else:
        return False
    if index < len(bytes_) and bytes_[index] == 0x2E:
        index += 1
        fraction_start = index
        while index < len(bytes_) and 0x30 <= bytes_[index] <= 0x39:
            index += 1
        if index == fraction_start:
            return False
    if index < len(bytes_) and bytes_[index] in (0x65, 0x45):
        index += 1
        if index < len(bytes_) and bytes_[index] in (0x2B, 0x2D):
            index += 1
        exponent_start = index
        while index < len(bytes_) and 0x30 <= bytes_[index] <= 0x39:
            index += 1
        if index == exponent_start:
            return False
    return index == len(bytes_)


def scan_json5_number_candidate(source: str, start: int) -> int:
    """Scans the bounded JSON5 number-candidate character class
    (parser.rs:689-699)."""
    offset = start
    while offset < len(source):
        character = char_at(source, offset)
        if not (
            character.isascii() and (character.isalnum() or character in "+-._")
        ):
            break
        offset += 1
    return offset


def valid_json5_number(text: str) -> bool:
    """JSON5 number grammar (parser.rs:701-760; RFC 0005 §6)."""
    unsigned = text[1:] if text[:1] in ("+", "-") else text
    if unsigned in ("Infinity", "NaN"):
        return True
    if unsigned.startswith("0x") or unsigned.startswith("0X"):
        hex_digits = unsigned[2:]
        return bool(hex_digits) and all(character in "0123456789abcdefABCDEF" for character in hex_digits)
    index = 0
    if index < len(unsigned) and unsigned[index] == ".":
        index += 1
        start = index
        while index < len(unsigned) and unsigned[index].isdigit():
            index += 1
        if index == start:
            return False
    else:
        if index >= len(unsigned):
            return False
        if unsigned[index] == "0":
            index += 1
            if index < len(unsigned) and unsigned[index].isdigit():
                return False
        elif unsigned[index] in "123456789":
            index += 1
            while index < len(unsigned) and unsigned[index].isdigit():
                index += 1
        else:
            return False
        if index < len(unsigned) and unsigned[index] == ".":
            index += 1
            while index < len(unsigned) and unsigned[index].isdigit():
                index += 1
    if index < len(unsigned) and unsigned[index] in ("e", "E"):
        index += 1
        if index < len(unsigned) and unsigned[index] in ("+", "-"):
            index += 1
        exponent_start = index
        while index < len(unsigned) and unsigned[index].isdigit():
            index += 1
        if index == exponent_start:
            return False
    return index == len(unsigned)


def scan_json5_identifier(source: str, start: int) -> tuple[int, bool]:
    """Scans one IdentifierName token with bounded ``\\u`` escapes
    (parser.rs:625-658)."""
    offset = start
    first = True
    valid = True
    while offset < len(source):
        character = char_at(source, offset)
        if character == "\\":
            decoded = decode_identifier_escape(source[offset:])
            if decoded is None:
                valid = False
                offset = scan_json5_invalid_word(source, offset)
                break
            width = 6
        else:
            decoded = character
            width = 1
        permitted = (
            is_json5_identifier_start(decoded)
            if first
            else is_json5_identifier_continue(decoded)
        )
        if not permitted:
            if first or character == "\\":
                valid = False
                offset = scan_json5_invalid_word(source, offset)
            break
        offset += width
        first = False
    return offset, valid and not first


def scan_json5_invalid_word(source: str, start: int) -> int:
    """Bounded recovery scan to the next delimiter (parser.rs:660-675)."""
    offset = start
    while offset < len(source):
        character = char_at(source, offset)
        if is_json5_whitespace(character) or character in "{}[]:,'\"/":
            break
        offset += 1
    return max(offset, start + 1)


def decode_identifier_escape(source: str) -> str | None:
    """Decodes one exact ``\\uXXXX`` escape (parser.rs:677-687)."""
    if not source.startswith("\\u") or len(source) < 6:
        return None
    try:
        value = int(source[2:6], 16)
    except ValueError:
        return None
    try:
        return chr(value)
    except ValueError:
        return None


def source_diagnostic(
    authority: DocumentAuthority,
    code: str,
    category: DiagnosticCategory,
    start: int,
    end: int,
    severity: JsonSeverity = JsonSeverity.ERROR,
) -> JsonDiagnostic:
    """One primary diagnostic over an exact raw span (parser.rs:1458-1498)."""
    return JsonDiagnostic(
        code=code,
        category=category,
        severity=severity,
        primary=authority.span(start, end),
    )


def source_warning(
    authority: DocumentAuthority,
    code: str,
    category: DiagnosticCategory,
    start: int,
    end: int,
) -> JsonDiagnostic:
    """One primary warning over an exact raw span (parser.rs:1479-1498)."""
    return JsonDiagnostic(
        code=code,
        category=category,
        severity=JsonSeverity.WARNING,
        primary=authority.span(start, end),
    )
