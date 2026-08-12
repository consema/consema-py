"""The lossless YAML syntax tokenizer: exhaustive byte coverage plus the
closed YamlSyntaxKind classification, and the named anchor/alias occurrences.

Authority: crates/consema-yaml/src/syntax.rs --the Scanner dispatch order
(syntax.rs:111-217), the plain-scalar continuation rule (syntax.rs:120-134,
257-282), quoted scanning (syntax.rs:284-317), block-content scanning
(syntax.rs:335-364), the indicator dispatch (syntax.rs:366-379), and the
anchor/alias occurrence extraction (syntax.rs:52-60). The vector surface is
conformance/vectors/yaml-v1.json case ``syntax.styles-and-trivia``
(piece_count 48, required kinds) and ``regression.plain-property-characters``
(no Anchor/Tag pieces inside plain scalars).

go/yaml/syntax.go is a cross-reference only (it documents itself as a
faithful replicate of syntax.rs:86-421).
"""

from __future__ import annotations

from dataclasses import dataclass

from consema.document.source import SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    LocationError,
    LosslessStructuralIndex,
    Span,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.yaml.errors import YamlFormationFailure, YamlFormationFailureKind
from consema.yaml.kinds import YamlSyntaxKind


@dataclass(frozen=True, slots=True)
class _Lexeme:
    start: int
    end: int
    kind: YamlSyntaxKind


@dataclass(frozen=True, slots=True)
class NamedOccurrence:
    """One anchor or alias occurrence with its raw span (syntax.rs:73-77)."""

    name: str
    span: Span


@dataclass(frozen=True, slots=True)
class Tokenized:
    """The tokenizer result (syntax.rs:79-84)."""

    index: LosslessStructuralIndex
    kinds: tuple[YamlSyntaxKind, ...]
    anchors: tuple[NamedOccurrence, ...]
    aliases: tuple[NamedOccurrence, ...]


def _is_separation(value: str) -> bool:
    return value in (" ", "\t", "\r", "\n")


def _is_flow_indicator(value: str) -> bool:
    return value in ("[", "]", "{", "}", ",")


class _Scanner:
    """Faithful port of the Rust Scanner (syntax.rs:86-421).

    Operates on the decoded text as a list of single-character strings so
    every lexeme boundary is a Unicode scalar offset exactly like the Rust
    ``chars`` slice.
    """

    def __init__(self, chars: list[str], max_tokens: int) -> None:
        self.chars = chars
        self.offset = 0
        self.line_start = 0
        self.max_tokens = max_tokens
        self.output: list[_Lexeme] = []
        self.pending_block_parent_indent: int | None = None
        self.plain_line_active = False
        self.plain_parent_indent: int | None = None

    def scan(self) -> list[_Lexeme]:
        while self.offset < len(self.chars):
            if (
                self.offset == self.line_start
                and self.pending_block_parent_indent is not None
                and self._scan_block_content()
            ):
                continue
            start = self.offset
            current = self.chars[start]
            if (
                current not in (" ", "\t", "\r", "\n")
                and not self.plain_line_active
                and self.plain_parent_indent is not None
            ):
                if (
                    self._line_indent() > self.plain_parent_indent
                    and not self._starts_indented_structure()
                ):
                    self._take_until_break()
                    self._push(start, self.offset, YamlSyntaxKind.PLAIN_SCALAR)
                    self.plain_line_active = True
                    continue
                self.plain_parent_indent = None
            if current == "\ufeff":
                self.offset += 1
                self._push(start, self.offset, YamlSyntaxKind.BOM)
                self._end_plain_scalar()
                if start == self.line_start:
                    self.line_start = self.offset
            elif current in (" ", "\t"):
                self._take_while(lambda item: item in (" ", "\t"))
                self._push(start, self.offset, YamlSyntaxKind.WHITESPACE)
            elif current in ("\r", "\n"):
                self._scan_newline(start)
            elif current == "#":
                self._take_until_break()
                self._push(start, self.offset, YamlSyntaxKind.COMMENT)
                self._end_plain_scalar()
            elif self._at_directive():
                self._take_until_break()
                self._push(start, self.offset, YamlSyntaxKind.DIRECTIVE)
                self._end_plain_scalar()
            elif self._at_document_indicator("-", "-", "-"):
                self.offset += 3
                self._push(start, self.offset, YamlSyntaxKind.DOCUMENT_START)
                self._end_plain_scalar()
            elif self._at_document_indicator(".", ".", "."):
                self.offset += 3
                self._push(start, self.offset, YamlSyntaxKind.DOCUMENT_END)
                self._end_plain_scalar()
            elif current in ("'", '"'):
                self._scan_quoted(current)
                self._push(
                    start,
                    self.offset,
                    YamlSyntaxKind.SINGLE_QUOTED_SCALAR
                    if current == "'"
                    else YamlSyntaxKind.DOUBLE_QUOTED_SCALAR,
                )
                self._end_plain_scalar()
            elif current in ("|", ">") and self._is_block_header():
                parent_indent = self._line_indent()
                self._take_until_break()
                self._push(
                    start,
                    self.offset,
                    YamlSyntaxKind.LITERAL_BLOCK_HEADER
                    if current == "|"
                    else YamlSyntaxKind.FOLDED_BLOCK_HEADER,
                )
                self.pending_block_parent_indent = parent_indent
                self._end_plain_scalar()
            elif current in ("&", "*", "!") and not self.plain_line_active:
                self.offset += 1
                self._take_while(
                    lambda item: not _is_separation(item) and not _is_flow_indicator(item)
                )
                self._push(
                    start,
                    self.offset,
                    {
                        "&": YamlSyntaxKind.ANCHOR,
                        "*": YamlSyntaxKind.ALIAS,
                        "!": YamlSyntaxKind.TAG,
                    }[current],
                )
                self._end_plain_scalar()
            else:
                kind = self._indicator_kind()
                if kind is not None:
                    self.offset += 1
                    self._push(start, self.offset, kind)
                    self._end_plain_scalar()
                else:
                    self._scan_plain()
                    self._push(start, self.offset, YamlSyntaxKind.PLAIN_SCALAR)
                    if not self.plain_line_active:
                        self.plain_parent_indent = self._line_indent()
                    self.plain_line_active = True
        return self.output

    # -- helpers -----------------------------------------------------------

    def _push(self, start: int, end: int, kind: YamlSyntaxKind) -> None:
        observed = len(self.output) + 1
        if observed > self.max_tokens:
            raise YamlFormationFailure(
                YamlFormationFailureKind.TOKEN_COUNT,
                name="syntax-pieces",
                observed=observed,
                limit=self.max_tokens,
            )
        self.output.append(_Lexeme(start, end, kind))

    def _scan_newline(self, start: int) -> None:
        if self.chars[self.offset] == "\r" and self.offset + 1 < len(self.chars) and self.chars[self.offset + 1] == "\n":
            self.offset += 2
        else:
            self.offset += 1
        self._push(start, self.offset, YamlSyntaxKind.NEWLINE)
        self.line_start = self.offset
        self.plain_line_active = False

    def _end_plain_scalar(self) -> None:
        self.plain_line_active = False
        self.plain_parent_indent = None

    def _starts_indented_structure(self) -> bool:
        if self.chars[self.offset] in ("-", "?") and (
            self.offset + 1 >= len(self.chars)
            or _is_separation(self.chars[self.offset + 1])
        ):
            return True
        cursor = self.offset
        while cursor < len(self.chars):
            character = self.chars[cursor]
            if character in ("\r", "\n", "#"):
                return False
            if character == ":" and (
                cursor + 1 >= len(self.chars)
                or _is_separation(self.chars[cursor + 1])
            ):
                return True
            cursor += 1
        return False

    def _scan_quoted(self, quote: str) -> None:
        self.offset += 1
        while self.offset < len(self.chars):
            current = self.chars[self.offset]
            self.offset += 1
            if quote == '"' and current == "\\" and self.offset < len(self.chars):
                if self.chars[self.offset] == "\r":
                    self.offset += 1
                    if self.offset < len(self.chars) and self.chars[self.offset] == "\n":
                        self.offset += 1
                    self.line_start = self.offset
                elif self.chars[self.offset] == "\n":
                    self.offset += 1
                    self.line_start = self.offset
                else:
                    self.offset += 1
            elif current == quote:
                if quote == "'" and self.offset < len(self.chars) and self.chars[self.offset] == "'":
                    self.offset += 1
                else:
                    break
            elif current == "\n":
                self.line_start = self.offset
            elif current == "\r":
                if self.offset < len(self.chars) and self.chars[self.offset] == "\n":
                    self.offset += 1
                self.line_start = self.offset

    def _scan_plain(self) -> None:
        self.offset += 1
        while self.offset < len(self.chars):
            current = self.chars[self.offset]
            if _is_separation(current) or _is_flow_indicator(current):
                break
            if current == ":":
                nxt = self.chars[self.offset + 1] if self.offset + 1 < len(self.chars) else None
                if nxt is None or _is_separation(nxt) or _is_flow_indicator(nxt):
                    break
            self.offset += 1

    def _scan_block_content(self) -> bool:
        parent_indent = self.pending_block_parent_indent
        assert parent_indent is not None
        start = self.offset
        cursor = start
        accepted_end = start
        while cursor < len(self.chars):
            line_end = _next_line_end(self.chars, cursor)
            content_end = _line_content_end(self.chars, cursor, line_end)
            indent = 0
            for item in self.chars[cursor:content_end]:
                if item != " ":
                    break
                indent += 1
            blank = all(item in (" ", "\t") for item in self.chars[cursor + indent : content_end])
            if not blank and indent <= parent_indent:
                break
            accepted_end = line_end
            cursor = line_end
        self.pending_block_parent_indent = None
        if accepted_end == start:
            return False
        self.offset = accepted_end
        self.line_start = accepted_end
        self._push(start, accepted_end, YamlSyntaxKind.BLOCK_SCALAR_CONTENT)
        return True

    def _indicator_kind(self) -> YamlSyntaxKind | None:
        current = self.chars[self.offset]
        if current == "[":
            return YamlSyntaxKind.FLOW_SEQUENCE_START
        if current == "]":
            return YamlSyntaxKind.FLOW_SEQUENCE_END
        if current == "{":
            return YamlSyntaxKind.FLOW_MAPPING_START
        if current == "}":
            return YamlSyntaxKind.FLOW_MAPPING_END
        if current == ",":
            return YamlSyntaxKind.FLOW_ENTRY
        if current == "-" and self._followed_by_separation(1):
            return YamlSyntaxKind.SEQUENCE_ENTRY
        if current == "?" and self._followed_by_separation(1):
            return YamlSyntaxKind.EXPLICIT_KEY
        if current == ":" and self._followed_by_separation(1):
            return YamlSyntaxKind.MAPPING_VALUE
        return None

    def _at_directive(self) -> bool:
        return self.offset == self.line_start and self.chars[self.offset] == "%"

    def _at_document_indicator(self, a: str, b: str, c: str) -> bool:
        return (
            self.offset == self.line_start
            and self.chars[self.offset : self.offset + 3] == [a, b, c]
            and self._followed_by_separation(3)
        )

    def _followed_by_separation(self, length: int) -> bool:
        nxt = self.chars[self.offset + length] if self.offset + length < len(self.chars) else None
        return nxt is None or _is_separation(nxt)

    def _is_block_header(self) -> bool:
        allowed = ("+", "-", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", " ", "\t", "#")
        for item in self.chars[self.offset + 1 :]:
            if item in ("\r", "\n"):
                break
            if item not in allowed:
                return False
        return True

    def _line_indent(self) -> int:
        count = 0
        for item in self.chars[self.line_start : self.offset]:
            if item != " ":
                break
            count += 1
        return count

    def _take_until_break(self) -> None:
        self._take_while(lambda item: item not in ("\r", "\n"))

    def _take_while(self, predicate) -> None:
        while self.offset < len(self.chars) and predicate(self.chars[self.offset]):
            self.offset += 1


def _next_line_end(chars: list[str], start: int) -> int:
    cursor = start
    while cursor < len(chars) and chars[cursor] not in ("\r", "\n"):
        cursor += 1
    if cursor < len(chars) and chars[cursor] == "\r":
        cursor += 1
        if cursor < len(chars) and chars[cursor] == "\n":
            cursor += 1
    elif cursor < len(chars) and chars[cursor] == "\n":
        cursor += 1
    return cursor


def _line_content_end(chars: list[str], start: int, line_end: int) -> int:
    end = line_end
    if end > start and chars[end - 1] == "\n":
        end -= 1
    if end > start and chars[end - 1] == "\r":
        end -= 1
    return end


def tokenize(
    source: SourceSnapshot,
    authority: DocumentAuthority,
    max_tokens: int,
) -> Tokenized:
    """Scans one source into the exhaustive piece index, the parallel kind
    list, and the anchor/alias occurrences (syntax.rs:16-71)."""
    text = source.decoded_text()
    if text is None:
        raise YamlFormationFailure(YamlFormationFailureKind.SYNTAX, code="yaml.native.invalid-source-span@1")
    chars = list(text)
    lexemes = _Scanner(chars, max_tokens).scan()
    pieces: list[StructuralPiece] = []
    kinds: list[YamlSyntaxKind] = []
    anchors: list[NamedOccurrence] = []
    aliases: list[NamedOccurrence] = []
    for lexeme in lexemes:
        try:
            start = source.raw_byte_at(_scalar_offset(lexeme.start))
            end = source.raw_byte_at(_scalar_offset(lexeme.end))
        except LocationError:
            raise YamlFormationFailure(
                YamlFormationFailureKind.SYNTAX, code="yaml.native.invalid-source-span@1"
            ) from None
        span = authority.span(start, end)
        pieces.append(
            StructuralPiece(
                span=span,
                kind=StructuralPieceKind.TRIVIA
                if lexeme.kind.is_trivia
                else StructuralPieceKind.ERROR_REGION
                if lexeme.kind is YamlSyntaxKind.ERROR_REGION
                else StructuralPieceKind.TOKEN,
            )
        )
        kinds.append(lexeme.kind)
        if lexeme.kind in (YamlSyntaxKind.ANCHOR, YamlSyntaxKind.ALIAS):
            name = "".join(chars[lexeme.start + 1 : lexeme.end])
            occurrence = NamedOccurrence(name=name, span=span)
            if lexeme.kind is YamlSyntaxKind.ANCHOR:
                anchors.append(occurrence)
            else:
                aliases.append(occurrence)
    try:
        index = LosslessStructuralIndex.new(authority.identity, source.len(), pieces)
    except LocationError:
        raise YamlFormationFailure(
            YamlFormationFailureKind.SYNTAX, code="yaml.native.invalid-source-span@1"
        ) from None
    return Tokenized(
        index=index,
        kinds=tuple(kinds),
        anchors=tuple(anchors),
        aliases=tuple(aliases),
    )


def _scalar_offset(value: int) -> object:
    """One unicode-scalar decoded offset (RFC 0003 s5)."""
    from consema.document.source import DecodedOffset

    return DecodedOffset.unicode_scalar(value)
