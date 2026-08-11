"""Token-level JSON/JSONC/JSON5 parser producing the immutable document.

Authority (Rust arbitration for exact semantics):

- Parse entry and fatal limits: crates/consema-json/src/parser.rs:73-166 —
  source-bytes limit (parser.rs:78-84), UTF-8 snapshot construction
  (parser.rs:85), structural-index construction (parser.rs:99-120),
  trailing-content recovery (parser.rs:136-144), formation status
  (parser.rs:145-149), deterministic diagnostic sorting
  (diagnostic.rs:107-123).
- Value/object/array parsing with recovery: parser.rs:829-1133 —
  missing-value (parser.rs:838-849), JSON5 identifier literals
  (parser.rs:910-935), object key / colon / comma recovery
  (parser.rs:971-1057), duplicate-member diagnostics with related
  first-member location (parser.rs:1007-1024), strict trailing-comma
  recovery (parser.rs:1025-1039), missing-close recovery
  (parser.rs:1059-1068, 1086-1096).
- String decoding: parser.rs:1232-1347 (JSON5 extensions per RFC 0005 §5,
  docs/rfcs/0005-...:93-113); the json5.string.unescaped-line-separator@1
  warning parser.rs:884-892.
- Number decoding: parser.rs:863-878 (strict) and parse_json5_number
  parser.rs:1375-1443 (RFC 0005 §6); the frozen non-finite bits
  parser.rs:1382-1401 and RFC 0005 §6 (docs/rfcs/0005-...:133-139).
- IdentifierName decoding: parser.rs:910-935 and decode_json5_identifier
  parser.rs:1349-1373.
- Entity allocation and the fatal node-count limit: parser.rs:1147-1189.

Decimal normalization (coefficient/exponent canonical form) is the core
value model's contract (consema.core.value.Decimal; crates/consema-core/
src/value.rs:277-292). Integer and Decimal values remain arbitrary
precision; parsing never rounds through a host float (RFC 0005 §6).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.core.value import Decimal
from consema.document.limits import ParseLimits
from consema.document.source import SourceSnapshot, SourceError
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
    StructuralPiece,
    StructuralPieceKind,
)
from consema.json.errors import (
    JsonDiagnostic,
    JsonFormationFailure,
    JsonFormationFailureKind,
    JsonSeverity,
    RelatedLocation,
    sort_diagnostics,
)
from consema.json.kinds import (
    JsonProfile,
    JsonSyntaxKind,
    SemanticUnavailable,
    is_json5_identifier_continue,
    is_json5_identifier_start,
    is_json5_line_terminator,
)
from consema.json.lexer import (
    DiagnosticSink,
    LexemeClass,
    Token,
    TokenKind,
    decode_identifier_escape,
    lex,
    source_diagnostic,
    source_warning,
)
from consema.protocol.error_registry import DiagnosticCategory


class InternalKind(enum.Enum):
    """Internal semantic kind of one value entity (mirror of
    InternalValueKind, lib.rs)."""

    NULL = "Null"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    BINARY_FLOAT64 = "BinaryFloat64"
    STRING = "String"
    ARRAY = "Array"
    OBJECT = "Object"
    UNAVAILABLE = "Unavailable"


@dataclass(frozen=True, slots=True)
class InternalValue:
    """One internal semantic payload (arbitrary precision; no float
    round-trip)."""

    kind: InternalKind
    payload: object = None


@dataclass(frozen=True, slots=True)
class ValueEntity:
    """One value entity (parser.rs:1161-1176)."""

    span: object  # consema.document.Span
    literal_span: object  # consema.document.Span | None
    complete: bool
    internal: InternalValue


@dataclass(frozen=True, slots=True)
class MemberEntity:
    """One exact object member association (parser.rs:997-1006)."""

    span: object
    key: int
    value: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class ElementEntity:
    """One exact array element association (parser.rs:1101-1106)."""

    span: object
    value: int
    ordinal: int


# The frozen non-finite binary64 bit patterns (RFC 0005 §6,
# docs/rfcs/0005-...:133-139; parser.rs:1382-1401).
BITS_POSITIVE_INFINITY = 0x7FF0000000000000
BITS_NEGATIVE_INFINITY = 0xFFF0000000000000
BITS_NAN = 0x7FF8000000000000
BITS_NEGATIVE_NAN = 0xFFF8000000000000


class _Parser:
    def __init__(
        self,
        source: str,
        profile: JsonProfile,
        authority: DocumentAuthority,
        tokens: tuple[Token, ...],
        diagnostics: DiagnosticSink,
        recovered: bool,
        limits: ParseLimits,
    ) -> None:
        # The parser indexes the source with raw BYTE offsets (token spans
        # are byte ranges); keep the encoded bytes and decode slices on
        # demand, exactly like Rust's &str byte slicing.
        self.source = source.encode("utf-8")
        self.profile = profile
        self.authority = authority
        self.tokens = tokens
        self.position = 0
        self.entities: list[object] = []
        self.diagnostics = diagnostics
        self.recovered = recovered
        self.limits = limits

    # -- value parsing -----------------------------------------------------

    def parse_value(self, depth: int) -> int:
        if depth > self.limits.max_nesting_depth:
            raise JsonFormationFailure(
                JsonFormationFailureKind.NESTING_DEPTH,
                observed=depth,
                limit=self.limits.max_nesting_depth,
            )
        token = self.peek()
        if token is None:
            offset = len(self.source)
            self.syntax_diagnostic("json.syntax.missing-value@1", offset, offset)
            self.recovered = True
            return self.alloc_value(
                offset,
                offset,
                None,
                False,
                InternalValue(InternalKind.UNAVAILABLE, SemanticUnavailable.MISSING),
            )
        if token.kind is TokenKind.NULL:
            self.position += 1
            return self.alloc_scalar(token, InternalValue(InternalKind.NULL))
        if token.kind is TokenKind.TRUE:
            self.position += 1
            return self.alloc_scalar(token, InternalValue(InternalKind.BOOLEAN, True))
        if token.kind is TokenKind.FALSE:
            self.position += 1
            return self.alloc_scalar(token, InternalValue(InternalKind.BOOLEAN, False))
        if token.kind is TokenKind.NUMBER:
            self.position += 1
            text = self.source[token.start : token.end].decode("utf-8")
            if self.profile.is_json5():
                internal = parse_json5_number(text)
            elif "." in text or "e" in text or "E" in text:
                internal = InternalValue(
                    InternalKind.DECIMAL, parse_json_decimal(text)
                )
            else:
                internal = InternalValue(InternalKind.INTEGER, int(text))
            return self.alloc_scalar(token, internal)
        if token.kind is TokenKind.STRING:
            self.position += 1
            decoded = decode_json_string(
                self.source[token.start : token.end].decode("utf-8"), self.profile
            )
            if decoded is not None:
                value, has_unescaped_line_separator = decoded
                if has_unescaped_line_separator:
                    self.diagnostics.push(
                        source_warning(
                            self.authority,
                            "json5.string.unescaped-line-separator@1",
                            DiagnosticCategory.CONFORMANCE,
                            token.start,
                            token.end,
                        )
                    )
                return self.alloc_scalar(
                    token, InternalValue(InternalKind.STRING, value)
                )
            self.syntax_diagnostic(
                "json.syntax.invalid-string-escape@1", token.start, token.end
            )
            self.recovered = True
            return self.alloc_value(
                token.start,
                token.end,
                (token.start, token.end),
                True,
                InternalValue(InternalKind.UNAVAILABLE, SemanticUnavailable.INVALID_LITERAL),
            )
        if token.kind is TokenKind.IDENTIFIER and self.profile.is_json5():
            self.position += 1
            text = decode_json5_identifier(
                self.source[token.start : token.end].decode("utf-8")
            )
            if text == "null":
                internal = InternalValue(InternalKind.NULL)
            elif text == "true":
                internal = InternalValue(InternalKind.BOOLEAN, True)
            elif text == "false":
                internal = InternalValue(InternalKind.BOOLEAN, False)
            elif text == "Infinity":
                internal = InternalValue(
                    InternalKind.BINARY_FLOAT64, BITS_POSITIVE_INFINITY
                )
            elif text == "NaN":
                internal = InternalValue(InternalKind.BINARY_FLOAT64, BITS_NAN)
            else:
                self.syntax_diagnostic(
                    "json.syntax.expected-value@1", token.start, token.end
                )
                self.recovered = True
                internal = InternalValue(
                    InternalKind.UNAVAILABLE, SemanticUnavailable.ERROR_REGION
                )
            return self.alloc_scalar(token, internal)
        if token.kind is TokenKind.LEFT_BRACE:
            return self.parse_object(depth)
        if token.kind is TokenKind.LEFT_BRACKET:
            return self.parse_array(depth)
        self.position += 1
        self.syntax_diagnostic("json.syntax.expected-value@1", token.start, token.end)
        self.recovered = True
        return self.alloc_value(
            token.start,
            token.end,
            None,
            False,
            InternalValue(InternalKind.UNAVAILABLE, SemanticUnavailable.ERROR_REGION),
        )

    def parse_object(self, depth: int) -> int:
        open_token = self.consume(TokenKind.LEFT_BRACE)
        members: list[int] = []
        names: dict[str, int] = {}
        while True:
            close = self.consume(TokenKind.RIGHT_BRACE)
            if close is not None:
                return self.alloc_value(
                    open_token.start,
                    close.end,
                    None,
                    True,
                    InternalValue(InternalKind.OBJECT, tuple(members)),
                )
            if self.peek() is None:
                break
            ordinal = len(members)
            peeked = self.peek()
            if peeked is not None and (
                peeked.kind is TokenKind.STRING
                or (self.profile.is_json5() and peeked.kind is TokenKind.IDENTIFIER)
            ):
                key = self.parse_object_key(depth + 1)
            else:
                offset = self.current_offset()
                self.syntax_diagnostic(
                    "json.syntax.expected-object-key@1", offset, offset
                )
                self.recovered = True
                key = self.alloc_value(
                    offset,
                    offset,
                    None,
                    False,
                    InternalValue(InternalKind.UNAVAILABLE, SemanticUnavailable.MISSING),
                )
            if self.consume(TokenKind.COLON) is None:
                offset = self.current_offset()
                self.syntax_diagnostic("json.syntax.missing-colon@1", offset, offset)
                self.recovered = True
            value = self.parse_value(depth + 1)
            member_start = self.value_entity(key).span.start_byte
            member_end = self.value_entity(value).span.end_byte
            member = self.alloc_entity(
                MemberEntity(
                    span=self.authority.span(member_start, member_end),
                    key=key,
                    value=value,
                    ordinal=ordinal,
                )
            )
            members.append(member)
            key_internal = self.value_entity(key).internal
            if (
                key_internal.kind is InternalKind.STRING
                and isinstance(key_internal.payload, str)
            ):
                first = names.get(key_internal.payload)
                if first is None:
                    names[key_internal.payload] = member
                else:
                    diagnostic = JsonDiagnostic(
                        code="json.object.duplicate-member@1",
                        category=DiagnosticCategory.SEMANTIC,
                        severity=JsonSeverity.ERROR,
                        primary=self.authority.span(
                            self.entities[member].span.start_byte,
                            self.entities[member].span.end_byte,
                        ),
                        arguments={"name": key_internal.payload},
                        related=(
                            RelatedLocation(
                                role="first-member",
                                location=self.entities[first].span,
                            ),
                        ),
                    )
                    self.diagnostics.push(diagnostic)
            if self.consume(TokenKind.COMMA) is not None:
                peeked = self.peek()
                if (
                    peeked is not None
                    and peeked.kind is TokenKind.RIGHT_BRACE
                    and not self.profile.permits_jsonc_extensions()
                ):
                    self.syntax_diagnostic(
                        "json.strict.trailing-comma@1",
                        max(peeked.start - 1, 0),
                        peeked.start,
                    )
                    self.recovered = True
                continue
            peeked = self.peek()
            if peeked is not None and peeked.kind is TokenKind.RIGHT_BRACE:
                continue
            offset = self.current_offset()
            self.syntax_diagnostic("json.syntax.missing-comma@1", offset, offset)
            self.recovered = True
            peeked = self.peek()
            if peeked is not None and peeked.kind not in (
                TokenKind.STRING,
                TokenKind.IDENTIFIER,
                TokenKind.RIGHT_BRACE,
            ):
                self.position += 1
        end = len(self.source)
        self.syntax_diagnostic("json.syntax.missing-object-close@1", end, end)
        self.recovered = True
        return self.alloc_value(
            open_token.start,
            end,
            None,
            False,
            InternalValue(InternalKind.OBJECT, tuple(members)),
        )

    def parse_array(self, depth: int) -> int:
        open_token = self.consume(TokenKind.LEFT_BRACKET)
        elements: list[int] = []
        while True:
            close = self.consume(TokenKind.RIGHT_BRACKET)
            if close is not None:
                return self.alloc_value(
                    open_token.start,
                    close.end,
                    None,
                    True,
                    InternalValue(InternalKind.ARRAY, tuple(elements)),
                )
            if self.peek() is None:
                end = len(self.source)
                self.syntax_diagnostic("json.syntax.missing-array-close@1", end, end)
                self.recovered = True
                return self.alloc_value(
                    open_token.start,
                    end,
                    None,
                    False,
                    InternalValue(InternalKind.ARRAY, tuple(elements)),
                )
            ordinal = len(elements)
            value = self.parse_value(depth + 1)
            span = self.value_entity(value).span
            element = self.alloc_entity(
                ElementEntity(span=span, value=value, ordinal=ordinal)
            )
            elements.append(element)
            if self.consume(TokenKind.COMMA) is not None:
                peeked = self.peek()
                if (
                    peeked is not None
                    and peeked.kind is TokenKind.RIGHT_BRACKET
                    and not self.profile.permits_jsonc_extensions()
                ):
                    self.syntax_diagnostic(
                        "json.strict.trailing-comma@1",
                        max(peeked.start - 1, 0),
                        peeked.start,
                    )
                    self.recovered = True
                continue
            peeked = self.peek()
            if peeked is not None and peeked.kind is TokenKind.RIGHT_BRACKET:
                continue
            offset = self.current_offset()
            self.syntax_diagnostic("json.syntax.missing-comma@1", offset, offset)
            self.recovered = True

    def parse_object_key(self, depth: int) -> int:
        token = self.peek()
        if token.kind is TokenKind.STRING:
            return self.parse_value(depth)
        self.position += 1
        name = decode_json5_identifier(
            self.source[token.start : token.end].decode("utf-8")
        )
        return self.alloc_scalar(
            token, InternalValue(InternalKind.STRING, name)
        )

    # -- entity allocation -------------------------------------------------

    def alloc_scalar(self, token: Token, internal: InternalValue) -> int:
        return self.alloc_value(
            token.start,
            token.end,
            (token.start, token.end),
            True,
            internal,
        )

    def alloc_value(
        self,
        start: int,
        end: int,
        literal: tuple[int, int] | None,
        complete: bool,
        internal: InternalValue,
    ) -> int:
        literal_span = None
        if literal is not None:
            literal_span = self.authority.span(literal[0], literal[1])
        return self.alloc_entity(
            ValueEntity(
                span=self.authority.span(start, end),
                literal_span=literal_span,
                complete=complete,
                internal=internal,
            )
        )

    def alloc_entity(self, entity: object) -> int:
        if len(self.entities) >= self.limits.max_node_count:
            raise JsonFormationFailure(
                JsonFormationFailureKind.NODE_COUNT,
                observed=len(self.entities) + 1,
                limit=self.limits.max_node_count,
            )
        index = len(self.entities)
        self.entities.append(entity)
        return index

    def value_entity(self, index: int) -> ValueEntity:
        return self.entities[index]

    def peek(self) -> Token | None:
        if self.position < len(self.tokens):
            return self.tokens[self.position]
        return None

    def consume(self, kind: TokenKind) -> Token | None:
        token = self.peek()
        if token is not None and token.kind is kind:
            self.position += 1
            return token
        return None

    def current_offset(self) -> int:
        token = self.peek()
        if token is None:
            return len(self.source)
        return token.start

    def syntax_diagnostic(self, code: str, start: int, end: int) -> None:
        self.diagnostics.push(
            source_diagnostic(
                self.authority, code, DiagnosticCategory.SYNTAX, start, end
            )
        )


def parse(
    raw: bytes,
    profile: JsonProfile,
    limits: ParseLimits,
) -> object:
    """Parses a complete immutable JSON/JSONC/JSON5 document snapshot
    (crates/consema-json/src/lib.rs:161-168, parser.rs:73-166).

    Returns the ``JsonDocument`` from :mod:`consema.json.document`; raises
    :class:`JsonFormationFailure` for fatal limits or invalid UTF-8.
    """
    if len(raw) > limits.max_source_bytes:
        raise JsonFormationFailure(
            JsonFormationFailureKind.SOURCE_BYTES,
            observed=len(raw),
            limit=limits.max_source_bytes,
        )
    try:
        source = SourceSnapshot.from_utf8(raw)
    except SourceError as error:
        raise JsonFormationFailure(
            JsonFormationFailureKind.INVALID_UTF8,
            valid_up_to=error.valid_up_to,
        ) from None
    authority = DocumentAuthority.fresh()
    sink = DiagnosticSink(limits.max_diagnostics)
    lexed = lex(raw, profile, authority, limits, sink)

    syntax_kinds = tuple(lexeme.syntax_kind_of() for lexeme in lexed.lexemes)
    pieces = [
        StructuralPiece(
            span=authority.span(lexeme.start, lexeme.end),
            kind=(
                StructuralPieceKind.TOKEN
                if lexeme.class_ is LexemeClass.TOKEN
                else (
                    StructuralPieceKind.TRIVIA
                    if lexeme.class_ is LexemeClass.TRIVIA
                    else StructuralPieceKind.ERROR_REGION
                )
            ),
        )
        for lexeme in lexed.lexemes
    ]
    structural_index = LosslessStructuralIndex.new(
        authority.identity, len(raw), pieces
    )

    text = source.decoded_text()
    assert text is not None
    parser = _Parser(
        source=text,
        profile=profile,
        authority=authority,
        tokens=lexed.tokens,
        diagnostics=sink,
        recovered=lexed.recovered,
        limits=limits,
    )
    root = parser.parse_value(0)
    if parser.position < len(parser.tokens):
        token = parser.tokens[parser.position]
        last = parser.tokens[-1]
        parser.syntax_diagnostic(
            "json.syntax.trailing-content@1",
            token.start,
            last.end,
        )
        parser.recovered = True
    formation_status = (
        FormationStatus.RECOVERED
        if parser.recovered
        else FormationStatus.COMPLETE
    )
    entities = tuple(parser.entities)
    diagnostics = sink.finish()
    sort_diagnostics(diagnostics)

    # Imported lazily to avoid a circular import (document imports nothing
    # from parser; parser's public entry returns the document type).
    from consema.json.document import JsonDocument

    return JsonDocument(
        authority=authority,
        source=source,
        profile=profile,
        structural_index=structural_index,
        syntax_kinds=syntax_kinds,
        _formation_status=formation_status,
        diagnostics=tuple(diagnostics),
        entities=entities,
        root_index=root,
        parse_limits=limits,
    )


# ---------------------------------------------------------------------------
# String decoding (parser.rs:1232-1347; RFC 0005 §5)
# ---------------------------------------------------------------------------


def decode_json_string(literal: str, profile: JsonProfile) -> tuple[str, bool] | None:
    """Decodes one complete string literal; None on invalid escapes
    (parser.rs:1232-1315). Returns (value, has_unescaped_line_separator)."""
    quote = literal[0] if literal else ""
    if quote != '"' and not (profile.is_json5() and quote == "'"):
        return None
    if not literal.startswith(quote) or not literal.endswith(quote):
        return None
    inner = literal[1 : len(literal) - len(quote)]
    output: list[str] = []
    has_unescaped_line_separator = False
    index = 0
    while index < len(inner):
        character = inner[index]
        if character == "\\":
            index += 1
            if index >= len(inner):
                return None
            escaped = inner[index]
            index += 1
            if escaped == '"':
                output.append('"')
            elif escaped == "'" and profile.is_json5():
                output.append("'")
            elif escaped == "\\":
                output.append("\\")
            elif escaped == "/":
                output.append("/")
            elif escaped == "b":
                output.append("\b")
            elif escaped == "f":
                output.append("\f")
            elif escaped == "n":
                output.append("\n")
            elif escaped == "r":
                output.append("\r")
            elif escaped == "t":
                output.append("\t")
            elif escaped == "v" and profile.is_json5():
                output.append("\u000b")
            elif escaped == "0" and profile.is_json5():
                if index < len(inner) and inner[index] in "0123456789":
                    return None
                output.append("\0")
            elif escaped == "x" and profile.is_json5():
                pair = inner[index : index + 2]
                if len(pair) != 2:
                    return None
                try:
                    output.append(chr(int(pair, 16)))
                except ValueError:
                    return None
                index += 2
            elif escaped == "u":
                quad = inner[index : index + 4]
                if len(quad) != 4:
                    return None
                try:
                    first = int(quad, 16)
                except ValueError:
                    return None
                index += 4
                if 0xD800 <= first <= 0xDBFF:
                    if (
                        index + 1 >= len(inner)
                        or inner[index] != "\\"
                        or inner[index + 1] != "u"
                    ):
                        return None
                    second_quad = inner[index + 2 : index + 6]
                    if len(second_quad) != 4:
                        return None
                    try:
                        second = int(second_quad, 16)
                    except ValueError:
                        return None
                    if not 0xDC00 <= second <= 0xDFFF:
                        return None
                    index += 6
                    scalar = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
                elif 0xDC00 <= first <= 0xDFFF:
                    return None
                else:
                    scalar = first
                try:
                    output.append(chr(scalar))
                except ValueError:
                    return None
            elif escaped in ("\n", "\u2028", "\u2029") and profile.is_json5():
                pass
            elif escaped == "\r" and profile.is_json5():
                if index < len(inner) and inner[index] == "\n":
                    index += 1
            elif (
                profile.is_json5()
                and escaped not in "0123456789"
                and not is_json5_line_terminator(escaped)
            ):
                output.append(escaped)
            else:
                return None
        elif character <= "\u001f":
            return None
        else:
            if character in ("\u2028", "\u2029"):
                has_unescaped_line_separator = True
            output.append(character)
            index += 1
    return "".join(output), has_unescaped_line_separator


# ---------------------------------------------------------------------------
# Number decoding (parser.rs:863-878, 1375-1443)
# ---------------------------------------------------------------------------


def parse_json_decimal(text: str) -> Decimal:
    """Parses one validated strict-JSON decimal number to exact Decimal."""
    sign = -1 if text.startswith("-") else 1
    unsigned = text[1:] if text[:1] in ("+", "-") else text
    exponent_text = ""
    mantissa = unsigned
    for marker in ("e", "E"):
        if marker in unsigned:
            mantissa, exponent_text = unsigned.split(marker, 1)
            break
    scale = 0
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
        scale = len(fraction)
        digits = whole + fraction
    else:
        digits = mantissa
    coefficient = sign * int(digits) if digits else 0
    exponent = int(exponent_text) - scale if exponent_text else -scale
    return Decimal(coefficient, exponent)


def parse_json5_number(text: str) -> InternalValue:
    """Decodes one validated JSON5 number (parser.rs:1375-1443)."""
    if text.startswith("-"):
        negative = True
        unsigned = text[1:]
    else:
        negative = False
        unsigned = text[1:] if text.startswith("+") else text
    if unsigned == "Infinity":
        return InternalValue(
            InternalKind.BINARY_FLOAT64,
            BITS_NEGATIVE_INFINITY if negative else BITS_POSITIVE_INFINITY,
        )
    if unsigned == "NaN":
        return InternalValue(
            InternalKind.BINARY_FLOAT64,
            BITS_NEGATIVE_NAN if negative else BITS_NAN,
        )
    if unsigned.startswith("0x") or unsigned.startswith("0X"):
        magnitude = int(unsigned[2:], 16)
        return InternalValue(InternalKind.INTEGER, -magnitude if negative else magnitude)
    normalized = "-" + unsigned if negative else unsigned
    sign_width = 1 if negative else 0
    if normalized[sign_width:].startswith("."):
        normalized = normalized[:sign_width] + "0" + normalized[sign_width:]
    exponent_index = len(normalized)
    for marker in ("e", "E"):
        if marker in normalized:
            exponent_index = normalized.index(marker)
            break
    if normalized[:exponent_index].endswith("."):
        normalized = normalized[:exponent_index] + "0" + normalized[exponent_index:]
    if "." in normalized or "e" in normalized or "E" in normalized:
        return InternalValue(InternalKind.DECIMAL, parse_json_decimal(normalized))
    return InternalValue(InternalKind.INTEGER, int(normalized))


# ---------------------------------------------------------------------------
# IdentifierName decoding (parser.rs:1349-1373)
# ---------------------------------------------------------------------------


def decode_json5_identifier(literal: str) -> str:
    """Decodes one validated JSON5 IdentifierName literal."""
    output: list[str] = []
    offset = 0
    first = True
    while offset < len(literal):
        character = literal[offset]
        if character == "\\":
            decoded = decode_identifier_escape(literal[offset:])
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
            raise ValueError("invalid JSON5 identifier")
        output.append(decoded)
        offset += width
        first = False
    if first:
        raise ValueError("empty JSON5 identifier")
    return "".join(output)
