"""Self-owned HCL tokenizer: the token stream, the 30-kind lossless piece
assembly, and the lexical half of the RFC 0014 §12 divergence inventory
(RFC 0014 §2, §4.1, §7.2).

There is no third-party HCL tokenizer (RFC 0014 §12). ``lex`` is one
deterministic forward pass over the decoded UTF-8 text. Every non-empty raw
byte of the source belongs to exactly one ordered token, and the token
stream maps one-to-one to the ordered lossless pieces of the closed 30-kind
HclSyntaxKind set — there is no ``Bom`` kind because a BOM is excluded at
formation (RFC 0014 §7.2).

The scanner is a faithful Python-idiomatic port of the lexical contract,
arbitrated by crates/consema-hcl/src/lexer.rs:

- Token kinds and piece mapping: lexer.rs:137-314.
- Scan dispatch (root / template interior absorb / quoted / heredoc):
  lexer.rs:632-643; root token table lexer.rs:654-887; interior absorb
  lexer.rs:892-1127.
- Identifiers: lexer.rs:1594-1615 (UAX #31 with hyphen continuation; the
  leading-underscore rejection lexer.rs:864-872 is the §12 D-4 exclusion).
- Numbers: lexer.rs:1619-1684 (decimal-only grammar; a continuation that
  cannot start a fresh token makes the whole run one invalid-number
  region).
- Quoted templates: lexer.rs:1131-1254 (escape validation lexer.rs:1535-
  1591; unterminated-string boundary lexer.rs:1738-1777).
- Heredocs: lexer.rs:1257-1375 (closing-line TrimSpace matching; the
  introducer gate lexer.rs:1396-1484).
- Interpolation/directive sequences: lexer.rs:1489-1531 (strip markers)
  and the absorb close lexer.rs:892-967.
- Recovery semantics: the module docstring of lexer.rs:46-69.
- Fatal limit failures: `hcl.limit.<name>@1` (lexer.rs:2126-2142).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.source import (
    BomPolicy,
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.structural import (
    DocumentAuthority,
    LosslessStructuralIndex,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.hcl.errors import (
    HclDiagnostic,
    HclFormationFailure,
    HclFormationFailureKind,
    HclSeverity,
    RelatedLocation,
    sort_diagnostics,
)
from consema.hcl.kinds import (
    HclSyntaxKind,
    is_identifier_continue,
    is_identifier_start,
)
from consema.hcl.limits import HclParseLimits
from consema.hcl.native import HclErrorRegion
from consema.protocol.error_registry import DiagnosticCategory


class HclTokenKind(enum.Enum):
    """Closed token kind set of the self-owned HCL tokenizer (RFC 0014 §2,
    §4.1; lexer.rs:137-245).

    The set is richer than the 30-piece HclSyntaxKind closure: operator
    spellings, the exact trivia runs, and the zero-length Eof terminal are
    token facts. Keyword spellings are Identifier tokens — the literal
    reading is a parser fact.
    """

    WHITESPACE = "Whitespace"
    LINE_BREAK = "LineBreak"
    LINE_COMMENT = "LineComment"
    INLINE_COMMENT = "InlineComment"
    IDENTIFIER = "Identifier"
    NUMBER = "Number"
    EQUALS = "Equals"
    STRING_OPEN = "StringOpen"
    STRING_CONTENT = "StringContent"
    STRING_CLOSE = "StringClose"
    INTERPOLATION_OPEN = "InterpolationOpen"
    INTERPOLATION_CONTENT = "InterpolationContent"
    INTERPOLATION_CLOSE = "InterpolationClose"
    DIRECTIVE_OPEN = "DirectiveOpen"
    DIRECTIVE_CONTENT = "DirectiveContent"
    DIRECTIVE_CLOSE = "DirectiveClose"
    HEREDOC_OPEN = "HeredocOpen"
    HEREDOC_CONTENT = "HeredocContent"
    HEREDOC_CLOSE = "HeredocClose"
    DOT = "Dot"
    COMMA = "Comma"
    COLON = "Colon"
    QUESTION_MARK = "QuestionMark"
    ARROW = "Arrow"
    ELLIPSIS = "Ellipsis"
    STAR = "Star"
    BRACE_OPEN = "BraceOpen"
    BRACE_CLOSE = "BraceClose"
    BRACKET_OPEN = "BracketOpen"
    BRACKET_CLOSE = "BracketClose"
    PAREN_OPEN = "ParenOpen"
    PAREN_CLOSE = "ParenClose"
    OP_EQUAL = "OpEqual"
    OP_NOT_EQUAL = "OpNotEqual"
    OP_LESS = "OpLess"
    OP_GREATER = "OpGreater"
    OP_LESS_EQUAL = "OpLessEqual"
    OP_GREATER_EQUAL = "OpGreaterEqual"
    OP_ADD = "OpAdd"
    OP_SUBTRACT = "OpSubtract"
    OP_NOT = "OpNot"
    OP_DIVIDE = "OpDivide"
    OP_MODULO = "OpModulo"
    OP_AND = "OpAnd"
    OP_OR = "OpOr"
    ERROR_REGION = "ErrorRegion"
    EOF = "Eof"

    def syntax_kind(self) -> HclSyntaxKind | None:
        """The closed lossless syntax kind of this token; None for the
        zero-length Eof terminal (RFC 0014 §7.2; lexer.rs:247-301)."""
        if self is HclTokenKind.EOF:
            return None
        if self in (
            HclTokenKind.DOT,
            HclTokenKind.ARROW,
            HclTokenKind.ELLIPSIS,
            HclTokenKind.STAR,
            HclTokenKind.OP_EQUAL,
            HclTokenKind.OP_NOT_EQUAL,
            HclTokenKind.OP_LESS,
            HclTokenKind.OP_GREATER,
            HclTokenKind.OP_LESS_EQUAL,
            HclTokenKind.OP_GREATER_EQUAL,
            HclTokenKind.OP_ADD,
            HclTokenKind.OP_SUBTRACT,
            HclTokenKind.OP_NOT,
            HclTokenKind.OP_DIVIDE,
            HclTokenKind.OP_MODULO,
            HclTokenKind.OP_AND,
            HclTokenKind.OP_OR,
        ):
            return HclSyntaxKind.OPERATOR
        return HclSyntaxKind.from_name(self.value)

    def structural_kind(self) -> StructuralPieceKind:
        """The structural classification of this token's piece
        (lexer.rs:303-314)."""
        if self in (
            HclTokenKind.WHITESPACE,
            HclTokenKind.LINE_BREAK,
            HclTokenKind.LINE_COMMENT,
            HclTokenKind.INLINE_COMMENT,
        ):
            return StructuralPieceKind.TRIVIA
        if self is HclTokenKind.ERROR_REGION:
            return StructuralPieceKind.ERROR_REGION
        return StructuralPieceKind.TOKEN


@dataclass(frozen=True, slots=True)
class HclToken:
    """One lexical token with its exact half-open raw-byte span
    (lexer.rs:105-135)."""

    kind: HclTokenKind
    span: object  # consema.document.Span


class _TemplateFrame:
    """One open template construct of the scanner stack (lexer.rs:489-537)."""

    __slots__ = ("kind", "open", "marker", "content_start", "bytes", "lines", "interpolations", "depth", "directive", "interior_start", "buffer")

    def __init__(
        self,
        kind: str,
        open: int = 0,
        marker: str = "",
        content_start: int = 0,
        depth: int = 0,
        directive: bool = False,
        interior_start: int = 0,
    ) -> None:
        self.kind = kind  # "quoted" | "heredoc" | "interp"
        self.open = open
        self.marker = marker
        self.content_start = content_start
        self.bytes = 0
        self.lines = 0
        self.interpolations = 0
        self.depth = depth
        self.directive = directive
        self.interior_start = interior_start
        self.buffer: list[HclToken] = []


class _DiagnosticSink:
    """Bounded ordered diagnostic recording with the house truncation
    marker (lexer.rs:539-578)."""

    def __init__(self, max_diagnostics: int) -> None:
        self.diagnostics: list[HclDiagnostic] = []
        self.max = max_diagnostics
        self.occurrence = 0
        self.truncated = False

    def push(self, diagnostic: HclDiagnostic) -> None:
        occurrence = self.occurrence
        self.occurrence += 1
        if len(self.diagnostics) < self.max:
            self.diagnostics.append(
                HclDiagnostic(
                    code=diagnostic.code,
                    category=diagnostic.category,
                    severity=diagnostic.severity,
                    primary=diagnostic.primary,
                    occurrence=occurrence,
                    arguments=diagnostic.arguments,
                    related=diagnostic.related,
                    notes=diagnostic.notes,
                )
            )
        elif not self.truncated:
            self.truncated = True
            self.diagnostics.append(
                HclDiagnostic(
                    code="core.diagnostic.truncated@1",
                    category=DiagnosticCategory.RESOURCE,
                    severity=HclSeverity.WARNING,
                    primary=None,
                    occurrence=self.occurrence,
                )
            )

    def finish(self) -> list[HclDiagnostic]:
        sort_diagnostics(self.diagnostics)
        return self.diagnostics


@dataclass(frozen=True, slots=True)
class HclLexOutput:
    """Result of one lexer pass: the ordered token stream, the recovered
    error regions, the ordered diagnostics, and the lossless 30-kind piece
    index (RFC 0014 §2, §7.2; lexer.rs:316-386).

    ``syntax`` is None for a region lex (an interpolation interior), whose
    tokens still carry exact spans bound to the same authority.
    """

    source: SourceSnapshot
    tokens: tuple[HclToken, ...]
    error_regions: tuple[HclErrorRegion, ...]
    diagnostics: tuple[HclDiagnostic, ...]
    recovered: bool
    syntax: LosslessStructuralIndex | None
    syntax_kinds: tuple[HclSyntaxKind, ...]
    authority: DocumentAuthority


def _utf8_width(byte: int) -> int:
    if byte < 0x80:
        return 1
    if byte < 0xE0:
        return 2
    if byte < 0xF0:
        return 3
    return 4


class _Lexer:
    def __init__(
        self,
        source: SourceSnapshot,
        decoded: str,
        authority: DocumentAuthority,
        limits: HclParseLimits,
        start: int,
        end: int,
        build_index: bool,
    ) -> None:
        self.source = source
        self.decoded = decoded
        self.bytes = decoded.encode("utf-8")
        self.authority = authority
        self.limits = limits
        self.pos = start
        self.end = end
        self.build_index = build_index
        self.tokens: list[HclToken] = []
        self.error_regions: list[HclErrorRegion] = []
        self.sink = _DiagnosticSink(limits.common.max_diagnostics)
        self.recovered = False
        self.buffered = 0
        self.stack: list[_TemplateFrame] = []

    # -- cursor helpers -----------------------------------------------------

    def byte(self) -> int | None:
        if self.pos < len(self.bytes):
            return self.bytes[self.pos]
        return None

    def byte_at(self, offset: int) -> int | None:
        index = self.pos + offset
        if 0 <= index < len(self.bytes):
            return self.bytes[index]
        return None

    def char_at(self, position: int) -> str | None:
        """The decoded character whose UTF-8 encoding starts at the byte
        `position` (lexer.rs:2045-2047: `decoded.get(pos..).chars().next()`).

        Scan positions are char boundaries; under the UTF-8-only source
        contract the byte slice at `position` decodes to exactly one
        character.
        """
        if position >= len(self.bytes):
            return None
        width = _utf8_width(self.bytes[position])
        return self.bytes[position : position + width].decode("utf-8")

    def span(self, start: int, end: int) -> object:
        if start > end or end > self.source.len():
            raise HclFormationFailure(HclFormationFailureKind.COORDINATES)
        return self.authority.span(start, end)

    # -- main scan ----------------------------------------------------------

    def scan(self) -> None:
        while self.pos < self.end:
            top = self.stack[-1] if self.stack else None
            if top is None:
                self.scan_root()
            elif top.kind == "interp":
                self.scan_absorb()
            elif top.kind == "quoted":
                self.scan_quoted()
            else:
                self.scan_heredoc()
        self.finish_eof()

    def emitting(self) -> bool:
        return all(frame.kind != "interp" for frame in self.stack)

    # -- token emission -----------------------------------------------------

    def emit(self, token: HclToken) -> None:
        count = len(self.tokens) + self.buffered + 1
        if count > self.limits.common.max_token_count:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="token-count",
                observed=count,
                limit=self.limits.common.max_token_count,
            )
        if count > self.limits.max_syntax_pieces:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="syntax-pieces",
                observed=count,
                limit=self.limits.max_syntax_pieces,
            )
        if self.stack:
            first = self.stack[0]
            if first.kind in ("quoted", "heredoc"):
                first.buffer.append(token)
                self.buffered += 1
                return
        self.tokens.append(token)

    def emit_kind(self, kind: HclTokenKind, start: int, end: int) -> None:
        self.emit(HclToken(kind, self.span(start, end)))

    def emit_error_region(self, start: int, end: int, code: str, category) -> None:
        self.recovered = True
        span = self.span(start, end)
        self.sink.push(
            HclDiagnostic(
                code=code,
                category=category,
                severity=HclSeverity.ERROR,
                primary=span,
            )
        )
        if end > start:
            self.emit(HclToken(HclTokenKind.ERROR_REGION, span))
            self.error_regions.append(HclErrorRegion(span, code))
            if len(self.error_regions) > self.limits.max_recovery_regions:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="recovery-regions",
                    observed=len(self.error_regions),
                    limit=self.limits.max_recovery_regions,
                )
            if len(self.error_regions) > self.limits.max_error_regions:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="error-regions",
                    observed=len(self.error_regions),
                    limit=self.limits.max_error_regions,
                )

    def recover(self, code: str, category, start: int, end: int) -> None:
        self.recovered = True
        span = self.span(start, end)
        self.sink.push(
            HclDiagnostic(
                code=code,
                category=category,
                severity=HclSeverity.ERROR,
                primary=span,
            )
        )

    # -- root-level scanning (lexer.rs:654-887) ------------------------------

    def scan_root(self) -> None:
        byte = self.byte()
        assert byte is not None
        if byte in (0x20, 0x09):
            start = self.pos
            while self.byte() in (0x20, 0x09):
                self.pos += 1
            self.emit_kind(HclTokenKind.WHITESPACE, start, self.pos)
        elif byte == 0x0A:
            self.emit_kind(HclTokenKind.LINE_BREAK, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x0D:
            if self.byte_at(1) == 0x0A:
                self.emit_kind(HclTokenKind.LINE_BREAK, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_error_region(
                    self.pos, self.pos + 1, "hcl.parse.lone-cr@1", DiagnosticCategory.LEXICAL
                )
                self.pos += 1
        elif byte == 0x23:  # '#'
            self.scan_line_comment(True)
        elif byte == 0x2F:  # '/'
            if self.byte_at(1) == 0x2F:
                self.scan_line_comment(True)
            elif self.byte_at(1) == 0x2A:
                self.scan_inline_comment(True)
            else:
                self.emit_kind(HclTokenKind.OP_DIVIDE, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x22:  # '"'
            self.open_quoted(True)
        elif byte == 0x3C:  # '<'
            if self.byte_at(1) == 0x3C:
                self.open_heredoc(True)
            elif self.byte_at(1) == 0x3D:
                self.emit_kind(HclTokenKind.OP_LESS_EQUAL, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_kind(HclTokenKind.OP_LESS, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x3E:  # '>'
            if self.byte_at(1) == 0x3D:
                self.emit_kind(HclTokenKind.OP_GREATER_EQUAL, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_kind(HclTokenKind.OP_GREATER, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x3D:  # '='
            if self.byte_at(1) == 0x3D:
                self.emit_kind(HclTokenKind.OP_EQUAL, self.pos, self.pos + 2)
                self.pos += 2
            elif self.byte_at(1) == 0x3E:
                self.emit_kind(HclTokenKind.ARROW, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_kind(HclTokenKind.EQUALS, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x21:  # '!'
            if self.byte_at(1) == 0x3D:
                self.emit_kind(HclTokenKind.OP_NOT_EQUAL, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_kind(HclTokenKind.OP_NOT, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x2D:  # '-'
            self.emit_kind(HclTokenKind.OP_SUBTRACT, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x2B:  # '+'
            self.emit_kind(HclTokenKind.OP_ADD, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x2A:  # '*'
            self.emit_kind(HclTokenKind.STAR, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x25:  # '%'
            self.emit_kind(HclTokenKind.OP_MODULO, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x26:  # '&'
            if self.byte_at(1) == 0x26:
                self.emit_kind(HclTokenKind.OP_AND, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_error_region(
                    self.pos, self.pos + 1, "hcl.parse.invalid-character@1", DiagnosticCategory.SYNTAX
                )
                self.pos += 1
        elif byte == 0x7C:  # '|'
            if self.byte_at(1) == 0x7C:
                self.emit_kind(HclTokenKind.OP_OR, self.pos, self.pos + 2)
                self.pos += 2
            else:
                self.emit_error_region(
                    self.pos, self.pos + 1, "hcl.parse.invalid-character@1", DiagnosticCategory.SYNTAX
                )
                self.pos += 1
        elif byte == 0x3F:  # '?'
            self.emit_kind(HclTokenKind.QUESTION_MARK, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x3A:  # ':'
            if self.byte_at(1) == 0x3A:
                self.emit_error_region(
                    self.pos, self.pos + 2, "hcl.parse.invalid-character@1", DiagnosticCategory.SYNTAX
                )
                self.pos += 2
            else:
                self.emit_kind(HclTokenKind.COLON, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x2C:  # ','
            self.emit_kind(HclTokenKind.COMMA, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x2E:  # '.'
            if self.byte_at(1) == 0x2E and self.byte_at(2) == 0x2E:
                self.emit_kind(HclTokenKind.ELLIPSIS, self.pos, self.pos + 3)
                self.pos += 3
            else:
                self.emit_kind(HclTokenKind.DOT, self.pos, self.pos + 1)
                self.pos += 1
        elif byte == 0x7B:  # '{'
            self.emit_kind(HclTokenKind.BRACE_OPEN, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x7D:  # '}'
            self.emit_kind(HclTokenKind.BRACE_CLOSE, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x5B:  # '['
            self.emit_kind(HclTokenKind.BRACKET_OPEN, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x5D:  # ']'
            self.emit_kind(HclTokenKind.BRACKET_CLOSE, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x28:  # '('
            self.emit_kind(HclTokenKind.PAREN_OPEN, self.pos, self.pos + 1)
            self.pos += 1
        elif byte == 0x29:  # ')'
            self.emit_kind(HclTokenKind.PAREN_CLOSE, self.pos, self.pos + 1)
            self.pos += 1
        elif byte in (0x7E, 0x5C, 0x24):  # '~' | '\\' | '$'
            self.emit_error_region(
                self.pos, self.pos + 1, "hcl.parse.invalid-character@1", DiagnosticCategory.SYNTAX
            )
            self.pos += 1
        elif 0x30 <= byte <= 0x39:
            self.scan_number(True)
        else:
            ch = self.char_at(self.pos)
            assert ch is not None
            if ch == "﻿":
                self.emit_error_region(
                    self.pos, self.pos + 3, "hcl.parse.byte-order-mark@1", DiagnosticCategory.ENCODING
                )
                self.pos += 3
            elif ch == "_":
                self.emit_error_region(
                    self.pos, self.pos + 1, "hcl.parse.identifier@1", DiagnosticCategory.SYNTAX
                )
                self.pos += 1
            elif is_identifier_start(ch):
                self.scan_identifier(True)
            else:
                self.emit_error_region(
                    self.pos,
                    self.pos + len(ch.encode("utf-8")),
                    "hcl.parse.invalid-character@1",
                    DiagnosticCategory.SYNTAX,
                )
                self.pos += len(ch.encode("utf-8"))

    # -- interior absorb (lexer.rs:892-1127) ---------------------------------

    def scan_absorb(self) -> None:
        byte = self.byte()
        assert byte is not None
        frame = self.stack[-1]
        if byte == 0x7B:  # '{'
            frame.depth += 1
            self.pos += 1
        elif byte in (0x7D, 0x7E):  # '}' | '~'
            depth = frame.depth
            directive = frame.directive
            interior_start = frame.interior_start
            close_width = None
            if byte == 0x7E:
                if depth == 0 and self.byte_at(1) == 0x7D:
                    close_width = 2
            elif depth == 0:
                close_width = 1
            if close_width is not None:
                close_start = self.pos
                self.pos += close_width
                content_kind = (
                    HclTokenKind.DIRECTIVE_CONTENT
                    if directive
                    else HclTokenKind.INTERPOLATION_CONTENT
                )
                close_kind = (
                    HclTokenKind.DIRECTIVE_CLOSE
                    if directive
                    else HclTokenKind.INTERPOLATION_CLOSE
                )
                content = HclToken(content_kind, self.span(interior_start, close_start))
                close_token = HclToken(close_kind, self.span(close_start, self.pos))
                self.stack.pop()
                if self.emitting():
                    self.emit(content)
                    self.emit(close_token)
            else:
                if byte == 0x7D:
                    frame.depth -= 1
                    self.pos += 1
                else:
                    self.recover(
                        "hcl.parse.invalid-character@1",
                        DiagnosticCategory.SYNTAX,
                        self.pos,
                        self.pos + 1,
                    )
                    self.pos += 1
        elif byte == 0x22:  # '"'
            open = self.pos
            self.pos += 1
            self.check_template_depth()
            self.stack.append(_TemplateFrame("quoted", open=open))
        elif byte == 0x3C:  # '<'
            if self.byte_at(1) == 0x3C:
                self.open_heredoc(False)
            elif self.byte_at(1) == 0x3D:
                self.pos += 2
            else:
                self.pos += 1
        elif byte in (0x3E, 0x21):  # '>' | '!'
            if self.byte_at(1) == 0x3D:
                self.pos += 2
            else:
                self.pos += 1
        elif byte == 0x3D:  # '='
            if self.byte_at(1) in (0x3D, 0x3E):
                self.pos += 2
            else:
                self.pos += 1
        elif byte == 0x26:  # '&'
            if self.byte_at(1) == 0x26:
                self.pos += 2
            else:
                self.recover(
                    "hcl.parse.invalid-character@1",
                    DiagnosticCategory.SYNTAX,
                    self.pos,
                    self.pos + 1,
                )
                self.pos += 1
        elif byte == 0x7C:  # '|'
            if self.byte_at(1) == 0x7C:
                self.pos += 2
            else:
                self.recover(
                    "hcl.parse.invalid-character@1",
                    DiagnosticCategory.SYNTAX,
                    self.pos,
                    self.pos + 1,
                )
                self.pos += 1
        elif byte == 0x3A:  # ':'
            if self.byte_at(1) == 0x3A:
                self.recover(
                    "hcl.parse.invalid-character@1",
                    DiagnosticCategory.SYNTAX,
                    self.pos,
                    self.pos + 2,
                )
                self.pos += 2
            else:
                self.pos += 1
        elif byte == 0x2E:  # '.'
            if self.byte_at(1) == 0x2E and self.byte_at(2) == 0x2E:
                self.pos += 3
            else:
                self.pos += 1
        elif byte in (
            0x2B, 0x2D, 0x2A, 0x25, 0x3F, 0x2C, 0x28, 0x29, 0x5B, 0x5D,
            0x20, 0x09,
        ):
            self.pos += 1
        elif byte in (0x5C, 0x24):  # '\\' | '$'
            self.recover(
                "hcl.parse.invalid-character@1",
                DiagnosticCategory.SYNTAX,
                self.pos,
                self.pos + 1,
            )
            self.pos += 1
        elif byte == 0x0A:  # '\n'
            self.pos += 1
            self.note_heredoc_line()
        elif byte == 0x0D:  # '\r'
            if self.byte_at(1) == 0x0A:
                self.pos += 2
                self.note_heredoc_line()
            else:
                self.recover(
                    "hcl.parse.lone-cr@1",
                    DiagnosticCategory.LEXICAL,
                    self.pos,
                    self.pos + 1,
                )
                self.pos += 1
        elif byte == 0x2F:  # '/'
            if self.byte_at(1) == 0x2F:
                self.scan_line_comment(False)
            elif self.byte_at(1) == 0x2A:
                self.scan_inline_comment(False)
            else:
                self.pos += 1
        elif byte == 0x23:  # '#'
            self.scan_line_comment(False)
        elif 0x30 <= byte <= 0x39:
            self.scan_number(False)
        else:
            ch = self.char_at(self.pos)
            assert ch is not None
            if ch == "﻿":
                self.recover(
                    "hcl.parse.byte-order-mark@1",
                    DiagnosticCategory.ENCODING,
                    self.pos,
                    self.pos + 3,
                )
                self.pos += 3
            elif ch == "_":
                self.recover(
                    "hcl.parse.identifier@1",
                    DiagnosticCategory.SYNTAX,
                    self.pos,
                    self.pos + 1,
                )
                self.pos += 1
            elif is_identifier_start(ch):
                self.scan_identifier(False)
            else:
                self.recover(
                    "hcl.parse.invalid-character@1",
                    DiagnosticCategory.SYNTAX,
                    self.pos,
                    self.pos + len(ch.encode("utf-8")),
                )
                self.pos += len(ch.encode("utf-8"))

    # -- comments ------------------------------------------------------------

    def scan_line_comment(self, emit: bool) -> None:
        start = self.pos
        while self.pos < self.end and self.byte() not in (0x0A, 0x0D):
            self.pos += 1
        if emit:
            self.emit_kind(HclTokenKind.LINE_COMMENT, start, self.pos)

    def scan_inline_comment(self, emit: bool) -> None:
        start = self.pos
        self.pos += 2
        while (
            self.pos + 1 < self.end
            and not (self.bytes[self.pos] == 0x2A and self.bytes[self.pos + 1] == 0x2F)
        ):
            self.pos += 1
        if self.pos + 1 < self.end:
            self.pos += 2
            if emit:
                self.emit_kind(HclTokenKind.INLINE_COMMENT, start, self.pos)
        else:
            if emit:
                self.emit_error_region(
                    start,
                    self.end,
                    "hcl.parse.unterminated-comment@1",
                    DiagnosticCategory.SYNTAX,
                )
            else:
                self.recover(
                    "hcl.parse.unterminated-comment@1",
                    DiagnosticCategory.SYNTAX,
                    start,
                    self.end,
                )
            self.pos = self.end

    # -- identifiers and numbers (lexer.rs:1594-1684) ------------------------

    def scan_identifier(self, emit: bool) -> None:
        start = self.pos
        while True:
            ch = self.char_at(self.pos)
            if ch is None:
                break
            if is_identifier_continue(ch) or ch == "-":
                self.pos += len(ch.encode("utf-8"))
            else:
                break
        length = self.pos - start
        if length > self.limits.max_identifier_len:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="identifier-len",
                observed=length,
                limit=self.limits.max_identifier_len,
            )
        if emit:
            self.emit_kind(HclTokenKind.IDENTIFIER, start, self.pos)

    def scan_number(self, emit: bool) -> None:
        start = self.pos
        while self.byte() is not None and 0x30 <= self.byte() <= 0x39:
            self.pos += 1
        if (
            self.byte() == 0x2E
            and self.byte_at(1) is not None
            and 0x30 <= self.byte_at(1) <= 0x39
        ):
            self.pos += 2
            while self.byte() is not None and 0x30 <= self.byte() <= 0x39:
                self.pos += 1
        if self.byte() in (0x65, 0x45):  # 'e' | 'E'
            sign = self.byte_at(1) in (0x2B, 0x2D)
            digits_start = 2 if sign else 1
            if (
                self.byte_at(digits_start) is not None
                and 0x30 <= self.byte_at(digits_start) <= 0x39
            ):
                self.pos += 1
                if sign:
                    self.pos += 1
                while self.byte() is not None and 0x30 <= self.byte() <= 0x39:
                    self.pos += 1
        end = self.pos
        while True:
            ch = self.char_at(end)
            if ch is None:
                break
            if is_identifier_continue(ch):
                end += len(ch.encode("utf-8"))
            elif (
                ch == "."
                and self.char_at(end + 1) is not None
                and self.char_at(end + 1).isdigit()
            ):
                end += 2
            else:
                break
        if end > self.pos:
            if emit:
                self.emit_error_region(
                    start,
                    end,
                    "hcl.parse.invalid-number@1",
                    DiagnosticCategory.SYNTAX,
                )
            else:
                self.recover(
                    "hcl.parse.invalid-number@1",
                    DiagnosticCategory.SYNTAX,
                    start,
                    end,
                )
            self.pos = end
        elif emit:
            self.emit_kind(HclTokenKind.NUMBER, start, self.pos)

    # -- quoted templates (lexer.rs:1131-1254) -------------------------------

    def scan_quoted(self) -> None:
        emit = self.emitting()
        run_start = self.pos
        while True:
            byte = self.byte()
            if byte is None:
                break
            if byte == 0x22:  # '"'
                self.end_run(run_start, emit, HclTokenKind.STRING_CONTENT)
                close_start = self.pos
                self.pos += 1
                frame = self.stack[-1]
                open = frame.open
                span_len = self.pos - open
                if span_len > self.limits.max_string_len:
                    raise HclFormationFailure(
                        HclFormationFailureKind.RESOURCE_LIMIT,
                        resource_name="string-len",
                        observed=span_len,
                        limit=self.limits.max_string_len,
                    )
                if span_len > self.limits.max_template_len:
                    raise HclFormationFailure(
                        HclFormationFailureKind.RESOURCE_LIMIT,
                        resource_name="template-len",
                        observed=span_len,
                        limit=self.limits.max_template_len,
                    )
                if emit:
                    self.flush_buffer()
                    self.stack.pop()
                    self.emit_kind(HclTokenKind.STRING_CLOSE, close_start, self.pos)
                else:
                    self.stack.pop()
                return
            if byte == 0x24:  # '$'
                if self.byte_at(1) == 0x24 and self.byte_at(2) == 0x7B:
                    self.pos += 3
                elif self.byte_at(1) == 0x7B:
                    self.end_run(run_start, emit, HclTokenKind.STRING_CONTENT)
                    self.open_interpolation(False, emit)
                    return
                else:
                    self.pos += 1
            elif byte == 0x25:  # '%'
                if self.byte_at(1) == 0x25 and self.byte_at(2) == 0x7B:
                    self.pos += 3
                elif self.byte_at(1) == 0x7B:
                    self.end_run(run_start, emit, HclTokenKind.STRING_CONTENT)
                    self.open_interpolation(True, emit)
                    return
                else:
                    self.pos += 1
            elif byte == 0x5C:  # '\\'
                if self.byte_at(1) == 0x0A:
                    self.recover(
                        "hcl.parse.invalid-escape@1",
                        DiagnosticCategory.SYNTAX,
                        self.pos,
                        self.pos + 2,
                    )
                    self.pos += 2
                elif self.byte_at(1) == 0x0D and self.byte_at(2) == 0x0A:
                    self.recover(
                        "hcl.parse.invalid-escape@1",
                        DiagnosticCategory.SYNTAX,
                        self.pos,
                        self.pos + 3,
                    )
                    self.pos += 3
                else:
                    self.scan_escape()
            elif byte == 0x0A:  # '\n'
                self.terminate_string(self.pos)
                return
            elif byte == 0x0D:  # '\r'
                if self.byte_at(1) == 0x0A:
                    self.terminate_string(self.pos)
                    return
                self.end_run(run_start, emit, HclTokenKind.STRING_CONTENT)
                if emit:
                    self.emit_error_region(
                        self.pos,
                        self.pos + 1,
                        "hcl.parse.lone-cr@1",
                        DiagnosticCategory.LEXICAL,
                    )
                else:
                    self.recover(
                        "hcl.parse.lone-cr@1",
                        DiagnosticCategory.LEXICAL,
                        self.pos,
                        self.pos + 1,
                    )
                self.pos += 1
                run_start = self.pos
            else:
                ch = self.char_at(self.pos)
                assert ch is not None
                self.pos += len(ch.encode("utf-8"))
        self.terminate_string(self.end)

    def scan_escape(self) -> None:
        start = self.pos
        self.pos += 1
        ch = self.char_at(self.pos)
        if ch is None:
            self.recover(
                "hcl.parse.invalid-escape@1",
                DiagnosticCategory.SYNTAX,
                start,
                self.pos,
            )
            return
        self.pos += len(ch.encode("utf-8"))
        valid = False
        if ch in ("n", "r", "t", '"', "\\"):
            valid = True
        elif ch == "u":
            digits_start = self.pos
            consumed = self.consume_hex(4)
            digits = self.decoded[digits_start : self.pos]
            try:
                value = int(digits, 16) if consumed == 4 else None
            except ValueError:
                value = None
            valid = consumed == 4 and value is not None and not (0xD800 <= value <= 0xDFFF)
        elif ch == "U":
            digits_start = self.pos
            consumed = self.consume_hex(8)
            digits = self.decoded[digits_start : self.pos]
            try:
                value = int(digits, 16) if consumed == 8 else None
            except ValueError:
                value = None
            valid = (
                consumed == 8
                and value is not None
                and value <= 0x10FFFF
                and not (0xD800 <= value <= 0xDFFF)
            )
        if not valid:
            self.recover(
                "hcl.parse.invalid-escape@1",
                DiagnosticCategory.SYNTAX,
                start,
                self.pos,
            )

    def consume_hex(self, count: int) -> int:
        consumed = 0
        while consumed < count:
            byte = self.byte()
            if byte is not None and byte in b"0123456789abcdefABCDEF":
                self.pos += 1
                consumed += 1
            else:
                break
        return consumed

    def terminate_string(self, end: int) -> None:
        frame = self.stack[-1]
        open = frame.open
        buffer_len = len(frame.buffer)
        span_len = end - open
        if span_len > self.limits.max_string_len:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="string-len",
                observed=span_len,
                limit=self.limits.max_string_len,
            )
        if span_len > self.limits.max_template_len:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="template-len",
                observed=span_len,
                limit=self.limits.max_template_len,
            )
        if self.emitting():
            self.buffered = max(0, self.buffered - buffer_len)
            self.stack.pop()
            self.emit_error_region(
                open + 1,
                end,
                "hcl.parse.unterminated-string@1",
                DiagnosticCategory.SYNTAX,
            )
        else:
            self.stack.pop()
            self.recover(
                "hcl.parse.unterminated-string@1",
                DiagnosticCategory.SYNTAX,
                open + 1,
                end,
            )

    def terminate_heredoc(self, end: int) -> None:
        frame = self.stack[-1]
        content_start = frame.content_start
        buffer_len = len(frame.buffer)
        if self.emitting():
            self.buffered = max(0, self.buffered - buffer_len)
            self.stack.pop()
            self.emit_error_region(
                content_start,
                end,
                "hcl.parse.unterminated-heredoc@1",
                DiagnosticCategory.SYNTAX,
            )
        else:
            self.stack.pop()
            self.recover(
                "hcl.parse.unterminated-heredoc@1",
                DiagnosticCategory.SYNTAX,
                content_start,
                end,
            )

    def end_run(self, run_start: int, emit: bool, kind: HclTokenKind) -> None:
        if emit and self.pos > run_start:
            self.emit_kind(kind, run_start, self.pos)

    def flush_buffer(self) -> None:
        if self.stack:
            frame = self.stack[-1]
            if frame.kind in ("quoted", "heredoc"):
                self.tokens.extend(frame.buffer)
                self.buffered = max(0, self.buffered - len(frame.buffer))
                frame.buffer.clear()

    def note_heredoc_line(self) -> None:
        for frame in self.stack:
            if frame.kind == "heredoc":
                frame.lines += 1
                if frame.lines > self.limits.max_heredoc_lines:
                    raise HclFormationFailure(
                        HclFormationFailureKind.RESOURCE_LIMIT,
                        resource_name="heredoc-lines",
                        observed=frame.lines,
                        limit=self.limits.max_heredoc_lines,
                    )

    def note_heredoc_content(self) -> None:
        if self.stack and self.stack[-1].kind == "heredoc":
            frame = self.stack[-1]
            frame.bytes = self.pos - frame.content_start
            if frame.bytes > self.limits.max_heredoc_bytes:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="heredoc-bytes",
                    observed=frame.bytes,
                    limit=self.limits.max_heredoc_bytes,
                )
            if frame.bytes > self.limits.max_template_len:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="template-len",
                    observed=frame.bytes,
                    limit=self.limits.max_template_len,
                )

    def check_template_depth(self) -> None:
        depth = len(self.stack) + 1
        if depth > self.limits.max_template_depth:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="template-depth",
                observed=depth,
                limit=self.limits.max_template_depth,
            )

    # -- heredocs (lexer.rs:1257-1375) ---------------------------------------

    def scan_heredoc(self) -> None:
        if self.pos >= self.end:
            self.terminate_heredoc(self.end)
            return
        self.note_heredoc_content()
        emit = self.emitting()
        at_line_start = self.pos == 0 or self.bytes[self.pos - 1] == 0x0A
        line_end = self.find_line_end()
        if at_line_start:
            trimmed = self.bytes[self.pos : line_end].decode("utf-8").strip()
            frame = self.stack[-1]
            is_closing = trimmed == frame.marker
            if is_closing:
                if emit:
                    self.flush_buffer()
                self.stack.pop()
                if emit:
                    self.emit_kind(HclTokenKind.HEREDOC_CLOSE, self.pos, line_end)
                if line_end < self.end:
                    if emit:
                        self.emit_kind(HclTokenKind.LINE_BREAK, line_end, line_end + 1)
                    self.pos = line_end + 1
                else:
                    self.pos = line_end
                return
        self.scan_heredoc_line(line_end)

    def scan_heredoc_line(self, line_end: int) -> None:
        emit = self.emitting()
        run_start = self.pos
        while True:
            if self.pos >= line_end:
                break
            byte = self.bytes[self.pos]
            if byte == 0x24:  # '$'
                if self.byte_at(1) == 0x24 and self.byte_at(2) == 0x7B:
                    self.pos += 3
                elif self.byte_at(1) == 0x7B:
                    self.end_run(run_start, emit, HclTokenKind.HEREDOC_CONTENT)
                    self.open_interpolation(False, emit)
                    return
                else:
                    self.pos += 1
            elif byte == 0x25:  # '%'
                if self.byte_at(1) == 0x25 and self.byte_at(2) == 0x7B:
                    self.pos += 3
                elif self.byte_at(1) == 0x7B:
                    self.end_run(run_start, emit, HclTokenKind.HEREDOC_CONTENT)
                    self.open_interpolation(True, emit)
                    return
                else:
                    self.pos += 1
            elif byte == 0x0D:  # '\r'
                if self.pos + 1 == line_end and self.byte_at(1) == 0x0A:
                    self.pos += 1
                else:
                    self.end_run(run_start, emit, HclTokenKind.HEREDOC_CONTENT)
                    if emit:
                        self.emit_error_region(
                            self.pos,
                            self.pos + 1,
                            "hcl.parse.lone-cr@1",
                            DiagnosticCategory.LEXICAL,
                        )
                    else:
                        self.recover(
                            "hcl.parse.lone-cr@1",
                            DiagnosticCategory.LEXICAL,
                            self.pos,
                            self.pos + 1,
                        )
                    self.pos += 1
                    run_start = self.pos
            else:
                ch = self.char_at(self.pos)
                assert ch is not None
                self.pos += len(ch.encode("utf-8"))
        self.end_run(run_start, emit, HclTokenKind.HEREDOC_CONTENT)
        if line_end < self.end:
            if emit:
                self.emit_kind(HclTokenKind.LINE_BREAK, line_end, line_end + 1)
            self.pos = line_end + 1
        else:
            self.pos = line_end
        self.note_heredoc_line()
        self.note_heredoc_content()

    def find_line_end(self) -> int:
        at = self.bytes.find(b"\n", self.pos, self.end)
        if at == -1:
            return self.end
        return at

    # -- template opening helpers --------------------------------------------

    def open_quoted(self, emit: bool) -> None:
        open = self.pos
        self.pos += 1
        self.check_template_depth()
        if emit:
            self.emit_kind(HclTokenKind.STRING_OPEN, open, self.pos)
        self.stack.append(_TemplateFrame("quoted", open=open))

    def open_heredoc(self, emit: bool) -> None:
        start = self.pos
        self.pos += 2
        if self.byte() == 0x2D:  # '-'
            self.pos += 1
        ch = self.char_at(self.pos)
        if ch is not None and is_identifier_start(ch):
            marker_start = self.pos
            while True:
                ch = self.char_at(self.pos)
                if ch is None:
                    break
                if is_identifier_continue(ch) or ch == "-":
                    self.pos += len(ch.encode("utf-8"))
                else:
                    break
            marker_len = self.pos - marker_start
            if marker_len > self.limits.max_identifier_len:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="identifier-len",
                    observed=marker_len,
                    limit=self.limits.max_identifier_len,
                )
            marker = self.bytes[marker_start : self.pos].decode("utf-8")
            line_cursor = self.pos
            while line_cursor < self.end and self.bytes[line_cursor] in (0x20, 0x09):
                line_cursor += 1
            newline_ok = line_cursor >= self.end or self.bytes[line_cursor] == 0x0A or (
                self.bytes[line_cursor] == 0x0D
                and line_cursor + 1 < self.end
                and self.bytes[line_cursor + 1] == 0x0A
            )
            if newline_ok:
                if emit:
                    self.emit_kind(HclTokenKind.HEREDOC_OPEN, start, self.pos)
                    if line_cursor > self.pos:
                        self.emit_kind(HclTokenKind.WHITESPACE, self.pos, line_cursor)
                    if line_cursor < self.end:
                        newline_end = (
                            line_cursor + 2 if self.bytes[line_cursor] == 0x0D else line_cursor + 1
                        )
                        self.emit_kind(HclTokenKind.LINE_BREAK, line_cursor, newline_end)
                        self.pos = newline_end
                    else:
                        self.pos = line_cursor
                else:
                    if line_cursor < self.end:
                        self.pos = (
                            line_cursor + 2
                            if self.bytes[line_cursor] == 0x0D
                            else line_cursor + 1
                        )
                    else:
                        self.pos = line_cursor
                self.check_template_depth()
                self.stack.append(
                    _TemplateFrame("heredoc", marker=marker, content_start=self.pos)
                )
                return
        if emit:
            self.emit_error_region(
                start,
                self.pos,
                "hcl.parse.heredoc-marker@1",
                DiagnosticCategory.SYNTAX,
            )
        else:
            self.recover(
                "hcl.parse.heredoc-marker@1",
                DiagnosticCategory.SYNTAX,
                start,
                self.pos,
            )

    def open_interpolation(self, directive: bool, emit: bool) -> None:
        open_start = self.pos
        self.pos += 2
        if self.byte() == 0x7E:  # '~'
            self.pos += 1
        frame = self.stack[-1]
        count = frame.interpolations + 1
        frame.interpolations = count
        if count > self.limits.max_template_interpolations:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="template-interpolations",
                observed=count,
                limit=self.limits.max_template_interpolations,
            )
        self.check_template_depth()
        if emit:
            kind = (
                HclTokenKind.DIRECTIVE_OPEN if directive else HclTokenKind.INTERPOLATION_OPEN
            )
            self.emit_kind(kind, open_start, self.pos)
        self.stack.append(
            _TemplateFrame(
                "interp",
                directive=directive,
                interior_start=self.pos,
            )
        )

    # -- end of source -------------------------------------------------------

    def finish_eof(self) -> None:
        while self.stack:
            frame = self.stack[-1]
            if frame.kind == "interp":
                code = (
                    "hcl.parse.unterminated-directive@1"
                    if frame.directive
                    else "hcl.parse.unterminated-interpolation@1"
                )
                interior_start = frame.interior_start
                self.stack.pop()
                self.recover(code, DiagnosticCategory.SYNTAX, interior_start, self.end)
            elif frame.kind == "quoted":
                self.terminate_string(self.end)
            else:
                self.terminate_heredoc(self.end)

    def finish(self) -> HclLexOutput:
        self.tokens.append(HclToken(HclTokenKind.EOF, self.span(self.end, self.end)))
        if self.build_index:
            pieces: list[StructuralPiece] = []
            kinds: list[HclSyntaxKind] = []
            for token in self.tokens:
                kind = token.kind.syntax_kind()
                if kind is None:
                    continue
                pieces.append(
                    StructuralPiece(
                        span=token.span,
                        kind=token.kind.structural_kind(),
                    )
                )
                kinds.append(kind)
            try:
                index = LosslessStructuralIndex.new(
                    self.authority.identity, len(self.bytes), pieces
                )
            except Exception:
                raise HclFormationFailure(HclFormationFailureKind.COVERAGE) from None
        else:
            index = None
            kinds = []
        return HclLexOutput(
            source=self.source,
            tokens=tuple(self.tokens),
            error_regions=tuple(self.error_regions),
            diagnostics=tuple(self.sink.finish()),
            recovered=self.recovered,
            syntax=index,
            syntax_kinds=tuple(kinds),
            authority=self.authority,
        )


def lex(raw: bytes, limits: HclParseLimits) -> HclLexOutput:
    """Lexes one whole HCL source under the frozen UTF-8 source contract
    (RFC 0014 §2; lexer.rs:388-431).

    The source is decoded with ``BomPolicy.TREAT_AS_CONTENT``: a UTF-8 BOM
    stays in the decoded text where the lexer reports it as
    ``hcl.parse.byte-order-mark@1`` instead of stripping it (RFC 0014
    §12 D-1). Invalid UTF-8 is a fatal formation failure with
    ``hcl.parse.invalid-utf8@1`` (RFC 0014 §2, §12 D-3).
    """
    if len(raw) > limits.common.max_source_bytes:
        raise HclFormationFailure(
            HclFormationFailureKind.RESOURCE_LIMIT,
            resource_name="source-bytes",
            observed=len(raw),
            limit=limits.common.max_source_bytes,
        )
    try:
        source = SourceSnapshot.from_raw(
            raw,
            EncodingRequest.new(SourceEncoding.utf8()).with_bom_policy(
                BomPolicy.TREAT_AS_CONTENT
            ),
            SourceLimits(
                max_raw_bytes=limits.common.max_source_bytes,
                max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
                max_decoded_scalars=limits.max_decoded_scalars,
            ),
        )
    except SourceError as error:
        if error.kind is SourceErrorKind.INVALID_UTF8 or error.kind is SourceErrorKind.INVALID_SEQUENCE:
            raise HclFormationFailure(
                HclFormationFailureKind.INVALID_UTF8,
                valid_up_to=error.valid_up_to if error.kind is SourceErrorKind.INVALID_UTF8 else error.byte_offset,
            ) from None
        if error.kind is SourceErrorKind.RESOURCE_LIMIT:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name=error.name,
                observed=error.observed,
                limit=error.limit,
            ) from None
        if error.kind is SourceErrorKind.OFFSET_OVERFLOW:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="offset-overflow",
            ) from None
        raise HclFormationFailure(HclFormationFailureKind.COORDINATES) from None
    decoded = source.decoded_text()
    if decoded is None:
        raise HclFormationFailure(HclFormationFailureKind.INVALID_UTF8)
    authority = DocumentAuthority.fresh()
    lexer = _Lexer(source, decoded, authority, limits, 0, len(raw), True)
    lexer.scan()
    return lexer.finish()


def lex_region(
    source: SourceSnapshot,
    authority: DocumentAuthority,
    start: int,
    end: int,
    limits: HclParseLimits,
) -> HclLexOutput:
    """Lexes one expression region of an already-formed source (the M3
    re-lex of interpolation/directive interiors; lexer.rs:423-431).

    The region's spans are bound to the caller's authority; the returned
    output carries no source-covering index.
    """
    if start > end or end > len(source.bytes()):
        raise HclFormationFailure(HclFormationFailureKind.COORDINATES)
    decoded = source.decoded_text()
    assert decoded is not None
    lexer = _Lexer(source, decoded, authority, limits, start, end, False)
    lexer.scan()
    return lexer.finish()
