"""TOML lossless syntax kinds and the exhaustive source tokenizer.

Authority:

- The closed twelve-kind vocabulary is frozen by crates/consema-toml/src/
  lib.rs:41-109 (TomlSyntaxKind and its stable query/protocol names
  "Whitespace", "Newline", "Comment", "String", "Bare", "Equals",
  "LeftBracket", "RightBracket", "LeftBrace", "RightBrace", "Comma", "Dot").
  The Python query validation table already freezes the same spellings
  (consema.protocol query.py:1075-1079 _is_toml_syntax_kind).
- The tokenizer transcribes the byte classification of
  crates/consema-toml/src/parser.rs:360-501 (tokenize / is_punctuation /
  punctuation_kind / string_end): space and tab are Whitespace trivia; LF,
  CRLF, and a bare CR are Newline trivia; ``#`` to end of line is Comment
  trivia; ``'``/``"`` start a String token scanned to its closing quote
  (escape-aware for basic strings, triple-quote aware); the punctuation
  bytes ``= [ ] { } , .`` are one-byte tokens; everything else forms a Bare
  token ending at whitespace, ``#``, punctuation, or a quote.
- StructuralPiece kinds: Token for string/bare/punctuation, Trivia for
  whitespace/newline/comment (parser.rs:370-412).
- RFC 0001 §2 (docs/rfcs/0001-toml-1.0-profile.md:18): the token/trivia
  index must cover all input with no gaps and no overlaps; the
  LosslessStructuralIndex validates exactly that.
- max_token_count bounds tokens plus trivia/error regions during
  tokenization (parser.rs:413-420; consema-document lib.rs:629-639
  default 2_000_000); the delimiter-nesting preflight enforces
  max_nesting_depth before the semantic parse (parser.rs:433-461) so a
  pathological open-delimiter document fails before deep recursion.
"""

from __future__ import annotations

import enum

from consema.document.structural import (
    DocumentAuthority,
    LosslessStructuralIndex,
    Span,
    StructuralPiece,
    StructuralPieceKind,
)

from consema.toml.errors import TomlFormationFailure


class TomlSyntaxKind(enum.Enum):
    """One format-specific lossless classification of a source piece
    (crates/consema-toml/src/lib.rs:41-68)."""

    WHITESPACE = "Whitespace"
    NEWLINE = "Newline"
    COMMENT = "Comment"
    STRING = "String"
    BARE = "Bare"
    EQUALS = "Equals"
    LEFT_BRACKET = "LeftBracket"
    RIGHT_BRACKET = "RightBracket"
    LEFT_BRACE = "LeftBrace"
    RIGHT_BRACE = "RightBrace"
    COMMA = "Comma"
    DOT = "Dot"

    def as_str(self) -> str:
        """Stable query and protocol name (lib.rs:70-88)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> TomlSyntaxKind | None:
        """Resolves one exact stable kind name (lib.rs:90-108)."""
        try:
            return cls(name)
        except ValueError:
            return None


_PUNCTUATION_KINDS = {
    ord("="): TomlSyntaxKind.EQUALS,
    ord("["): TomlSyntaxKind.LEFT_BRACKET,
    ord("]"): TomlSyntaxKind.RIGHT_BRACKET,
    ord("{"): TomlSyntaxKind.LEFT_BRACE,
    ord("}"): TomlSyntaxKind.RIGHT_BRACE,
    ord(","): TomlSyntaxKind.COMMA,
    ord("."): TomlSyntaxKind.DOT,
}

_PUNCTUATION_BYTES = frozenset(_PUNCTUATION_KINDS)


def _is_ascii_whitespace(byte: int) -> bool:
    """Rust ``u8::is_ascii_whitespace``: 0x09-0x0D and 0x20."""
    return byte in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20)


def _is_punctuation(byte: int) -> bool:
    return byte in _PUNCTUATION_BYTES


def tokenize(
    source: str,
    authority: DocumentAuthority,
    max_token_count: int,
) -> tuple[list[StructuralPiece], list[TomlSyntaxKind]]:
    """Exhaustive ordered token/trivia coverage of one decoded UTF-8 source
    (parser.rs:360-431).

    Returns parallel lists (pieces, kinds); raises a fatal
    resource-limit failure when the token count (pieces, including trivia)
    exceeds ``max_token_count``.
    """
    bytes_source = source.encode("utf-8")
    pieces: list[StructuralPiece] = []
    kinds: list[TomlSyntaxKind] = []
    cursor = 0
    length = len(bytes_source)
    while cursor < length:
        byte = bytes_source[cursor]
        if byte in (0x20, 0x09):  # space or tab
            end = cursor + 1
            while end < length and bytes_source[end] in (0x20, 0x09):
                end += 1
            kind = StructuralPieceKind.TRIVIA
            syntax_kind = TomlSyntaxKind.WHITESPACE
        elif byte in (0x0D, 0x0A):  # CR or LF
            end = cursor + 2 if (byte == 0x0D and cursor + 1 < length and bytes_source[cursor + 1] == 0x0A) else cursor + 1
            kind = StructuralPieceKind.TRIVIA
            syntax_kind = TomlSyntaxKind.NEWLINE
        elif byte == 0x23:  # #
            end = cursor + 1
            while end < length and bytes_source[end] not in (0x0D, 0x0A):
                end += 1
            kind = StructuralPieceKind.TRIVIA
            syntax_kind = TomlSyntaxKind.COMMENT
        elif byte in (0x27, 0x22):  # ' or "
            end = _string_end(bytes_source, cursor)
            kind = StructuralPieceKind.TOKEN
            syntax_kind = TomlSyntaxKind.STRING
        elif _is_punctuation(byte):
            end = cursor + 1
            kind = StructuralPieceKind.TOKEN
            syntax_kind = _PUNCTUATION_KINDS[byte]
        else:
            # Bare runs stop at ASCII whitespace, '#', punctuation, and
            # quotes; non-ASCII bytes continue the run (parser.rs:402-410).
            end = cursor + 1
            while (
                end < length
                and not _is_ascii_whitespace(bytes_source[end])
                and bytes_source[end] != 0x23
                and not _is_punctuation(bytes_source[end])
                and bytes_source[end] not in (0x27, 0x22)
            ):
                end += 1
            kind = StructuralPieceKind.TOKEN
            syntax_kind = TomlSyntaxKind.BARE
        observed = len(pieces) + 1
        if observed > max_token_count:
            raise TomlFormationFailure.resource_limit(
                "token_count", observed, max_token_count
            )
        pieces.append(
            StructuralPiece(
                span=authority.span(cursor, end),
                kind=kind,
            )
        )
        kinds.append(syntax_kind)
        cursor = end
    return pieces, kinds


def _string_end(bytes_source: bytes, start: int) -> int:
    """Scan one string token to its closing quote
    (parser.rs:480-499): escape-aware for basic strings, triple-quote
    aware, and falling back to end-of-source for unterminated strings (the
    semantic parser reports the real syntax error; the tokenizer must still
    produce exact coverage)."""
    quote = bytes_source[start]
    triple = bytes_source[start : start + 3] == bytes((quote, quote, quote))
    cursor = start + (3 if triple else 1)
    length = len(bytes_source)
    while cursor < length:
        if quote == 0x22 and bytes_source[cursor] == 0x5C:  # basic string escape
            cursor = min(cursor + 2, length)
            continue
        if triple:
            if bytes_source[cursor : cursor + 3] == bytes((quote, quote, quote)):
                return cursor + 3
        elif bytes_source[cursor] == quote:
            return cursor + 1
        cursor += 1
    return length


def preflight_delimiter_nesting(
    source: str,
    pieces: list[StructuralPiece],
    max_depth: int,
) -> None:
    """Preflight [/{ nesting depth over tokens before the semantic parse
    (parser.rs:433-461): a depth exceeding ``max_nesting_depth`` is a fatal
    resource-limit failure named ``nesting_depth``."""
    depth = 0
    bytes_source = source.encode("utf-8")
    for piece in pieces:
        if piece.kind is not StructuralPieceKind.TOKEN:
            continue
        token = bytes_source[piece.span.start_byte : piece.span.end_byte]
        if token in (b"[", b"{"):
            depth += 1
            if depth > max_depth:
                raise TomlFormationFailure.resource_limit(
                    "nesting_depth", depth, max_depth
                )
        elif token in (b"]", b"}"):
            depth = max(depth - 1, 0)
