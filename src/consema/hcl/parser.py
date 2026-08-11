"""Body/expression grammar with recovery, producing the immutable native
HCL document (RFC 0014 §3, §4).

One deterministic parser pass over the frozen lexer token stream assembles
the native body tree with the §3 recovery semantics:

- `Complete` requires exhaustive byte coverage under the frozen grammar and
  every configured limit. `Recovered` retains the immutable source,
  exhaustive piece coverage, ordered diagnostics, the merged error regions,
  and every independently proven construct; the native tree is always
  present — an empty body is a valid body.
- The recovery boundary for an expression that fails to parse is the end of
  its line, except that an unterminated bracket/paren/brace extends the
  region to the matching close if one exists and to end of line otherwise;
  an unterminated quoted string extends to end of line; an unterminated
  heredoc extends to end of file (bounded by the heredoc size limit). After
  an expression region ends, body parsing resumes at the next line.
- The duplicate-attribute rule (RFC 0014 §3): a second attribute with the
  same name in one body makes formation Recovered with
  `hcl.parse.duplicate-attribute@1`; the duplicate stays a proven syntax
  piece but never a native attribute.
- Every limit failure is a fatal `hcl.limit.<name>@1` failure; a limit
  failure never masquerades as a partial document (hard gate 4).

Authority (language-neutral first; Rust only for byte/registry
arbitration): crates/consema-hcl/src/parser.rs — codes parser.rs:77-98,
formation entry parser.rs:200-218, the parser body parser.rs:313-726,
attributes/blocks parser.rs:729-978, the expression grammar parser.rs:980-
2016, templates and heredocs parser.rs:2018-2366, literal decoding
parser.rs:2387-2531, fatal limit mapping parser.rs:2549-2565.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.source import SourceSnapshot
from consema.document.structural import (
    DocumentAuthority,
    FormationStatus,
    LosslessStructuralIndex,
)
from consema.hcl.errors import (
    HclDiagnostic,
    HclFormationFailure,
    HclFormationFailureKind,
    HclSeverity,
    sort_diagnostics,
)
from consema.hcl.expression import (
    BinaryOp,
    HclCallArg,
    HclDirectiveKind,
    HclExpression,
    HclExpressionKind,
    HclForIntro,
    HclNumber,
    HclObjectEntry,
    HclObjectKey,
    HclTemplateKey,
    HclTemplatePart,
    HclTraversalRoot,
    HclTraversalStep,
    HeredocFacts,
    HeredocMode,
    ObjectSeparator,
    UnaryOp,
    canonical_decimal,
)
from consema.hcl.kinds import HclExpressionKindName, HclSyntaxKind
from consema.hcl.lexer import (
    HclLexOutput,
    HclToken,
    HclTokenKind,
    lex,
    lex_region,
)
from consema.hcl.limits import HclParseLimits
from consema.hcl.native import (
    HclAttribute,
    HclBlock,
    HclBlockLabel,
    HclBody,
    HclBodyItem,
    HclErrorRegion,
)
from consema.protocol.error_registry import DiagnosticCategory

# Stable `hcl.parse.*@1` parser diagnostic codes (RFC 0014 §3, §4, §11;
# parser.rs:77-98).
CODE_ITEM = "hcl.parse.item@1"
CODE_ATTRIBUTE = "hcl.parse.attribute@1"
CODE_BLOCK = "hcl.parse.block@1"
CODE_LABEL = "hcl.parse.label@1"
CODE_EXPRESSION = "hcl.parse.expression@1"
CODE_DIRECTIVE = "hcl.parse.directive@1"
CODE_NEWLINE = "hcl.parse.newline@1"
CODE_SEPARATOR = "hcl.parse.separator@1"
CODE_DUPLICATE_ATTRIBUTE = "hcl.parse.duplicate-attribute@1"


class ExprMode(enum.Enum):
    """Expression context: whether newline sequences are whitespace
    (parser.rs:261-269)."""

    TOP = "Top"  # body-level: newlines and line comments end the expression
    NESTED = "Nested"  # inside brackets/parens/calls/templates: ignored


class BodyEnd(enum.Enum):
    """The terminator of one body parse (parser.rs:271-278)."""

    EOF = "Eof"
    BRACE_CLOSE = "BraceClose"


class Delim(enum.Enum):
    """One open bracket of the expression parser (parser.rs:280-296)."""

    BRACE = "Brace"
    BRACKET = "Bracket"
    PAREN = "Paren"

    def matches(self, kind: HclTokenKind) -> bool:
        if self is Delim.BRACE:
            return kind is HclTokenKind.BRACE_CLOSE
        if self is Delim.BRACKET:
            return kind is HclTokenKind.BRACKET_CLOSE
        return kind is HclTokenKind.PAREN_CLOSE


def _delim_of(kind: HclTokenKind) -> Delim | None:
    if kind is HclTokenKind.BRACE_OPEN:
        return Delim.BRACE
    if kind is HclTokenKind.BRACKET_OPEN:
        return Delim.BRACKET
    if kind is HclTokenKind.PAREN_OPEN:
        return Delim.PAREN
    return None


class AttributeFailure(enum.Enum):
    """Why one attribute occurrence failed to form (parser.rs:298-311)."""

    MISSING_EQUALS = "MissingEquals"
    MISSING_EXPRESSION = "MissingExpression"

    def code(self) -> str:
        if self is AttributeFailure.MISSING_EQUALS:
            return CODE_ATTRIBUTE
        return CODE_EXPRESSION


class AttributeOutcome:
    """The outcome of one attribute parse (parser.rs:307-311)."""

    __slots__ = ("attribute", "failure")

    def __init__(self, attribute: HclAttribute | None, failure: AttributeFailure | None):
        self.attribute = attribute
        self.failure = failure

    @classmethod
    def formed(cls, attribute: HclAttribute) -> AttributeOutcome:
        return cls(attribute, None)

    @classmethod
    def failed(cls, failure: AttributeFailure) -> AttributeOutcome:
        return cls(None, failure)


class _DiagnosticSink:
    """Bounded ordered diagnostic recording with the house truncation
    marker (parser.rs:220-259)."""

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
class HclFormed:
    """One formed native HCL document (RFC 0014 §3; parser.rs:100-187).

    The profile layer that gates Complete formation (the tfvars top-level
    restriction) is added by consema.hcl.document.
    """

    source: SourceSnapshot
    authority: DocumentAuthority
    status: FormationStatus
    diagnostics: tuple[HclDiagnostic, ...]
    body: HclBody
    error_regions: tuple[HclErrorRegion, ...]
    syntax: LosslessStructuralIndex
    syntax_kinds: tuple[HclSyntaxKind, ...]
    limits: HclParseLimits
    ordinals: dict[int, int]
    tree_nodes: int


class _Parser:
    def __init__(
        self,
        lexed: HclLexOutput,
        source: SourceSnapshot,
        decoded: str,
        authority: DocumentAuthority,
        limits: HclParseLimits,
        sink_cap: int,
        recovered: bool,
        error_regions: list[HclErrorRegion],
        sink: _DiagnosticSink,
        brackets: list[Delim],
    ) -> None:
        self.lexed = lexed
        self.source = source
        self.decoded = decoded
        self.bytes = decoded.encode("utf-8")
        self.authority = authority
        self.limits = limits
        self.tokens = lexed.tokens
        self.pos = 0
        self.sink = sink
        self.recovered = recovered
        self.error_regions = error_regions
        self.brackets = brackets

    # -- token cursor --------------------------------------------------------

    def peek(self) -> HclToken:
        return self.tokens[min(self.pos, len(self.tokens) - 1)]

    def peek_kind(self) -> HclTokenKind:
        return self.peek().kind

    def advance(self) -> HclToken:
        token = self.peek()
        if token.kind is not HclTokenKind.EOF:
            self.pos += 1
        return token

    def at(self, kind: HclTokenKind) -> bool:
        return self.peek_kind() is kind

    def eat(self, kind: HclTokenKind) -> HclToken | None:
        if self.at(kind):
            return self.advance()
        return None

    def skip_trivia(self) -> None:
        while self.peek_kind() in (HclTokenKind.WHITESPACE, HclTokenKind.INLINE_COMMENT):
            self.pos += 1

    def skip_structural(self) -> None:
        while self.peek_kind() in (
            HclTokenKind.WHITESPACE,
            HclTokenKind.INLINE_COMMENT,
            HclTokenKind.LINE_BREAK,
            HclTokenKind.LINE_COMMENT,
        ):
            self.pos += 1

    def skip_expression_trivia(self, mode: ExprMode) -> None:
        if mode is ExprMode.TOP:
            self.skip_trivia()
        else:
            self.skip_structural()

    def text(self, token: HclToken) -> str:
        """Exact token text derived from the frozen source; spans are raw
        byte ranges (parser.rs:451-456)."""
        return self.bytes[token.span.start_byte : token.span.end_byte].decode("utf-8")

    def span(self, start: int, end: int) -> object:
        if start > end or end > self.source.len():
            raise HclFormationFailure(HclFormationFailureKind.COORDINATES)
        return self.authority.span(start, end)

    # -- diagnostics and recovery -------------------------------------------

    def diagnose(self, code: str, start: int, end: int, category) -> None:
        self.recovered = True
        self.sink.push(
            HclDiagnostic(
                code=code,
                category=category,
                severity=HclSeverity.ERROR,
                primary=self.span(start, end),
            )
        )

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
            self.error_regions.append(HclErrorRegion(span, code))
            self.check_error_region_limits()

    def check_error_region_limits(self) -> None:
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

    def fail_item(self, start: int, code: str) -> None:
        brackets = self.brackets
        self.brackets = []
        boundary = self.scan_recovery(brackets)
        self.emit_error_region(start, boundary, code, DiagnosticCategory.SYNTAX)

    def scan_recovery(self, stack: list[Delim]) -> int:
        while True:
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.EOF:
                return self.source.len()
            if kind in (HclTokenKind.LINE_BREAK, HclTokenKind.LINE_COMMENT):
                if not stack:
                    return token.span.start_byte
                self.pos += 1
            elif kind in (
                HclTokenKind.BRACE_OPEN,
                HclTokenKind.BRACKET_OPEN,
                HclTokenKind.PAREN_OPEN,
            ):
                delim = _delim_of(kind)
                if delim is not None:
                    stack.append(delim)
                self.pos += 1
            elif kind in (
                HclTokenKind.BRACE_CLOSE,
                HclTokenKind.BRACKET_CLOSE,
                HclTokenKind.PAREN_CLOSE,
            ):
                if not stack:
                    return token.span.start_byte
                if stack[-1].matches(kind):
                    stack.pop()
                    if not stack:
                        self.pos += 1
                        return token.span.end_byte
                else:
                    stack.pop()
                self.pos += 1
            else:
                self.pos += 1

    def scan_to_close_brace(self) -> int | None:
        braces = 0
        while True:
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.EOF:
                return None
            if kind is HclTokenKind.BRACE_OPEN:
                braces += 1
                self.pos += 1
            elif kind is HclTokenKind.BRACE_CLOSE:
                if braces == 0:
                    self.pos += 1
                    return token.span.end_byte
                braces -= 1
                self.pos += 1
            else:
                self.pos += 1

    # -- body grammar --------------------------------------------------------

    def parse_body(self, depth: int, end: BodyEnd) -> HclBody:
        if depth > self.limits.max_body_depth:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="body-depth",
                observed=depth,
                limit=self.limits.max_body_depth,
            )
        items: list[HclBodyItem] = []
        attribute_count = 0
        block_count = 0
        item_count = 0
        names: set[str] = set()
        while True:
            self.skip_structural()
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.EOF:
                break
            if kind is HclTokenKind.BRACE_CLOSE and end is BodyEnd.BRACE_CLOSE:
                break
            if kind is HclTokenKind.IDENTIFIER:
                token = self.advance()
                name = self.text(token)
                self.skip_trivia()
                if self.peek_kind() is HclTokenKind.EQUALS:
                    item_count += 1
                    attribute_count += 1
                    if item_count > self.limits.max_body_item_count:
                        raise HclFormationFailure(
                            HclFormationFailureKind.RESOURCE_LIMIT,
                            resource_name="body-item-count",
                            observed=item_count,
                            limit=self.limits.max_body_item_count,
                        )
                    if attribute_count > self.limits.max_attribute_count:
                        raise HclFormationFailure(
                            HclFormationFailureKind.RESOURCE_LIMIT,
                            resource_name="attribute-count",
                            observed=attribute_count,
                            limit=self.limits.max_attribute_count,
                        )
                    outcome = self.parse_attribute(token, name, False)
                    if outcome.attribute is not None:
                        if name not in names:
                            names.add(name)
                            items.append(HclBodyItem.of_attribute(outcome.attribute))
                        else:
                            self.diagnose(
                                CODE_DUPLICATE_ATTRIBUTE,
                                token.span.start_byte,
                                token.span.end_byte,
                                DiagnosticCategory.SYNTAX,
                            )
                    else:
                        self.fail_item(token.span.start_byte, outcome.failure.code())
                elif self.peek_kind() in (
                    HclTokenKind.STRING_OPEN,
                    HclTokenKind.BRACE_OPEN,
                    HclTokenKind.IDENTIFIER,
                ):
                    item_count += 1
                    block_count += 1
                    if item_count > self.limits.max_body_item_count:
                        raise HclFormationFailure(
                            HclFormationFailureKind.RESOURCE_LIMIT,
                            resource_name="body-item-count",
                            observed=item_count,
                            limit=self.limits.max_body_item_count,
                        )
                    if block_count > self.limits.max_block_count:
                        raise HclFormationFailure(
                            HclFormationFailureKind.RESOURCE_LIMIT,
                            resource_name="block-count",
                            observed=block_count,
                            limit=self.limits.max_block_count,
                        )
                    block = self.parse_block(token, depth)
                    if block is not None:
                        items.append(HclBodyItem.of_block(block))
                else:
                    self.fail_item(token.span.start_byte, CODE_ITEM)
            else:
                if kind in (
                    HclTokenKind.BRACE_CLOSE,
                    HclTokenKind.BRACKET_CLOSE,
                    HclTokenKind.PAREN_CLOSE,
                ):
                    self.diagnose(
                        CODE_ITEM, token.span.start_byte, token.span.end_byte, DiagnosticCategory.SYNTAX
                    )
                    self.advance()
                else:
                    self.fail_item(token.span.start_byte, CODE_ITEM)
        return HclBody(items=tuple(items))

    def parse_attribute(self, name_token: HclToken, name: str, single_line: bool) -> AttributeOutcome:
        self.skip_trivia()
        equals = self.eat(HclTokenKind.EQUALS)
        if equals is None:
            return AttributeOutcome.failed(AttributeFailure.MISSING_EQUALS)
        self.skip_trivia()
        expression = self.parse_expression(ExprMode.TOP, 0)
        if expression is None:
            return AttributeOutcome.failed(AttributeFailure.MISSING_EXPRESSION)
        if not single_line:
            self.skip_trivia()
            if self.peek_kind() in (
                HclTokenKind.LINE_BREAK,
                HclTokenKind.LINE_COMMENT,
                HclTokenKind.EOF,
            ):
                pass
            else:
                self.diagnose(
                    CODE_NEWLINE,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                self.scan_recovery([])
        return AttributeOutcome.formed(
            HclAttribute(
                name=name,
                name_span=name_token.span,
                equals_span=equals.span,
                expression=expression,
            )
        )

    def parse_block(self, type_token: HclToken, depth: int) -> HclBlock | None:
        block_start = type_token.span.start_byte
        block_type = self.text(type_token)
        labels: list[HclBlockLabel] = []
        while True:
            self.skip_trivia()
            kind = self.peek_kind()
            if kind is HclTokenKind.IDENTIFIER:
                token = self.advance()
                labels.append(
                    HclBlockLabel(text=self.text(token), span=token.span, quoted=False)
                )
                if len(labels) > self.limits.max_label_count:
                    raise HclFormationFailure(
                        HclFormationFailureKind.RESOURCE_LIMIT,
                        resource_name="label-count",
                        observed=len(labels),
                        limit=self.limits.max_label_count,
                    )
            elif kind is HclTokenKind.STRING_OPEN:
                label = self.parse_quoted_label()
                if label is None:
                    self.fail_item(block_start, CODE_LABEL)
                    return None
                labels.append(label)
                if len(labels) > self.limits.max_label_count:
                    raise HclFormationFailure(
                        HclFormationFailureKind.RESOURCE_LIMIT,
                        resource_name="label-count",
                        observed=len(labels),
                        limit=self.limits.max_label_count,
                    )
            elif kind is HclTokenKind.BRACE_OPEN:
                break
            else:
                self.fail_item(block_start, CODE_BLOCK)
                return None
        self.advance()  # open brace
        self.skip_trivia()
        kind = self.peek_kind()
        if kind in (HclTokenKind.LINE_BREAK, HclTokenKind.LINE_COMMENT):
            self.skip_structural()
            body = self.parse_body(depth + 1, BodyEnd.BRACE_CLOSE)
            if self.at(HclTokenKind.BRACE_CLOSE):
                close = self.advance()
                close_end = close.span.end_byte
            else:
                self.fail_item(block_start, CODE_BLOCK)
                return None
        elif kind is HclTokenKind.BRACE_CLOSE:
            close = self.advance()
            body = HclBody(items=tuple())
            close_end = close.span.end_byte
        elif kind is HclTokenKind.EOF:
            self.fail_item(block_start, CODE_BLOCK)
            return None
        else:
            formed = self.parse_one_line_body(block_start)
            if formed is None:
                return None
            body, close_end = formed
        self.skip_trivia()
        if self.peek_kind() in (
            HclTokenKind.LINE_BREAK,
            HclTokenKind.LINE_COMMENT,
            HclTokenKind.EOF,
        ):
            pass
        else:
            self.diagnose(
                CODE_NEWLINE,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            self.scan_recovery([])
        return HclBlock(
            block_type=block_type,
            labels=tuple(labels),
            body=body,
            span=self.span(block_start, close_end),
        )

    def parse_quoted_label(self) -> HclBlockLabel | None:
        open = self.advance()
        text = ""
        while True:
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.STRING_CONTENT:
                self.advance()
                text += decode_quoted_literal(self.text(token))
            elif kind is HclTokenKind.STRING_CLOSE:
                close = self.advance()
                return HclBlockLabel(
                    text=text,
                    span=self.span(open.span.start_byte, close.span.end_byte),
                    quoted=True,
                )
            elif kind in (HclTokenKind.ERROR_REGION, HclTokenKind.EOF):
                return None
            else:
                self.diagnose(
                    CODE_LABEL,
                    token.span.start_byte,
                    token.span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None

    def parse_one_line_body(self, block_start: int) -> tuple[HclBody, int] | None:
        kind = self.peek_kind()
        if kind is HclTokenKind.BRACE_CLOSE:
            close = self.advance()
            return HclBody(items=tuple()), close.span.end_byte
        if kind is HclTokenKind.EOF:
            self.fail_item(block_start, CODE_BLOCK)
            return None
        if kind is HclTokenKind.IDENTIFIER:
            name_token = self.advance()
            name = self.text(name_token)
            outcome = self.parse_attribute(name_token, name, True)
            if outcome.attribute is not None:
                self.skip_trivia()
                kind = self.peek_kind()
                if kind is HclTokenKind.BRACE_CLOSE:
                    close = self.advance()
                    return (
                        HclBody(items=(HclBodyItem.of_attribute(outcome.attribute),)),
                        close.span.end_byte,
                    )
                if kind is HclTokenKind.EOF:
                    self.fail_item(block_start, CODE_BLOCK)
                    return None
                self.diagnose(
                    CODE_BLOCK,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                close_end = self.scan_to_close_brace()
                if close_end is None:
                    self.fail_item(block_start, CODE_BLOCK)
                    return None
                return (
                    HclBody(items=(HclBodyItem.of_attribute(outcome.attribute),)),
                    close_end,
                )
            self.fail_item(name_token.span.start_byte, outcome.failure.code())
            close_end = self.scan_to_close_brace()
            if close_end is None:
                self.fail_item(block_start, CODE_BLOCK)
                return None
            return HclBody(items=tuple()), close_end
        self.diagnose(
            CODE_BLOCK,
            self.peek().span.start_byte,
            self.peek().span.end_byte,
            DiagnosticCategory.SYNTAX,
        )
        close_end = self.scan_to_close_brace()
        if close_end is None:
            self.fail_item(block_start, CODE_BLOCK)
            return None
        return HclBody(items=tuple()), close_end

    # -- expression grammar (RFC 0014 §4.3; parser.rs:980-2016) -------------

    def parse_expression(self, mode: ExprMode, depth: int) -> HclExpression | None:
        if depth >= self.limits.max_expression_depth:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="expression-depth",
                observed=depth + 1,
                limit=self.limits.max_expression_depth,
            )
        return self.parse_conditional(mode, depth)

    def parse_conditional(self, mode: ExprMode, depth: int) -> HclExpression | None:
        condition = self.parse_or(mode, depth)
        if condition is None:
            return None
        self.skip_trivia()
        if not self.at(HclTokenKind.QUESTION_MARK):
            return condition
        self.advance()
        then = self.parse_conditional(mode, depth + 1)
        if then is None:
            return None
        self.skip_trivia()
        if self.eat(HclTokenKind.COLON) is None:
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        else_ = self.parse_conditional(mode, depth + 1)
        if else_ is None:
            return None
        return HclExpression(
            HclExpressionKind.conditional(condition, then, else_),
            self.span(condition.span.start_byte, else_.span.end_byte),
        )

    def _binary_level(self, mode: ExprMode, depth: int, parse_next, ops: list) -> HclExpression | None:
        lhs = parse_next(mode, depth)
        if lhs is None:
            return None
        chain = 0
        while True:
            self.skip_trivia()
            op = None
            kind = self.peek_kind()
            for candidate, token_kind in ops:
                if kind is token_kind:
                    op = candidate
                    break
            if op is None:
                break
            chain += 1
            if chain > self.limits.max_expression_depth:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="expression-depth",
                    observed=chain,
                    limit=self.limits.max_expression_depth,
                )
            self.advance()
            self.skip_expression_trivia(mode)
            rhs = parse_next(mode, depth)
            if rhs is None:
                return None
            lhs = self.binary(op, lhs, rhs)
        return lhs

    def parse_or(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode, depth, self.parse_and, [(BinaryOp.OR, HclTokenKind.OP_OR)]
        )

    def parse_and(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode, depth, self.parse_equality, [(BinaryOp.AND, HclTokenKind.OP_AND)]
        )

    def parse_equality(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode,
            depth,
            self.parse_relational,
            [
                (BinaryOp.EQUAL, HclTokenKind.OP_EQUAL),
                (BinaryOp.NOT_EQUAL, HclTokenKind.OP_NOT_EQUAL),
            ],
        )

    def parse_relational(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode,
            depth,
            self.parse_additive,
            [
                (BinaryOp.LESS, HclTokenKind.OP_LESS),
                (BinaryOp.GREATER, HclTokenKind.OP_GREATER),
                (BinaryOp.LESS_EQUAL, HclTokenKind.OP_LESS_EQUAL),
                (BinaryOp.GREATER_EQUAL, HclTokenKind.OP_GREATER_EQUAL),
            ],
        )

    def parse_additive(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode,
            depth,
            self.parse_multiplicative,
            [
                (BinaryOp.ADD, HclTokenKind.OP_ADD),
                (BinaryOp.SUBTRACT, HclTokenKind.OP_SUBTRACT),
            ],
        )

    def parse_multiplicative(self, mode: ExprMode, depth: int) -> HclExpression | None:
        return self._binary_level(
            mode,
            depth,
            self.parse_term,
            [
                (BinaryOp.MULTIPLY, HclTokenKind.STAR),
                (BinaryOp.DIVIDE, HclTokenKind.OP_DIVIDE),
                (BinaryOp.MODULO, HclTokenKind.OP_MODULO),
            ],
        )

    def binary(
        self, op: BinaryOp, lhs: HclExpression, rhs: HclExpression
    ) -> HclExpression:
        return HclExpression(
            HclExpressionKind.binary(op, lhs, rhs),
            self.span(lhs.span.start_byte, rhs.span.end_byte),
        )

    def parse_term(self, mode: ExprMode, depth: int) -> HclExpression | None:
        if depth >= self.limits.max_expression_depth:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="expression-depth",
                observed=depth + 1,
                limit=self.limits.max_expression_depth,
            )
        self.skip_expression_trivia(mode)
        token = self.peek()
        kind = token.kind
        if kind in (HclTokenKind.OP_SUBTRACT, HclTokenKind.OP_NOT):
            op_token = self.advance()
            op = UnaryOp.MINUS if op_token.kind is HclTokenKind.OP_SUBTRACT else UnaryOp.NOT
            operand = self.parse_term(mode, depth + 1)
            if operand is None:
                return None
            return HclExpression(
                HclExpressionKind.unary(op, operand),
                self.span(op_token.span.start_byte, operand.span.end_byte),
            )
        if kind is HclTokenKind.NUMBER:
            token = self.advance()
            number = self.number(token)
            return HclExpression(HclExpressionKind.number(number), token.span)
        if kind is HclTokenKind.STRING_OPEN:
            parsed = self.parse_quoted_template(depth)
            if parsed is None:
                return None
            parts, span = parsed
            return HclExpression(HclExpressionKind.template(parts), span)
        if kind is HclTokenKind.HEREDOC_OPEN:
            return self.parse_heredoc(depth)
        if kind is HclTokenKind.PAREN_OPEN:
            return self.parse_paren(depth)
        if kind is HclTokenKind.BRACKET_OPEN:
            return self.parse_bracket(depth)
        if kind is HclTokenKind.BRACE_OPEN:
            return self.parse_brace(depth)
        if kind is HclTokenKind.IDENTIFIER:
            return self.parse_identifier_term(mode, depth)
        self.diagnose(
            CODE_EXPRESSION, token.span.start_byte, token.span.end_byte, DiagnosticCategory.SYNTAX
        )
        return None

    def number(self, token: HclToken) -> HclNumber:
        spelling = self.text(token)
        canonical = canonical_decimal(spelling, self.limits.max_number_digits)
        if canonical is None:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="number-digits",
                observed=2**63 - 1,
                limit=self.limits.max_number_digits,
            )
        digits = sum(1 for byte in canonical.encode("utf-8") if 0x30 <= byte <= 0x39)
        if digits > self.limits.max_number_digits:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="number-digits",
                observed=digits,
                limit=self.limits.max_number_digits,
            )
        return HclNumber(span=token.span, canonical_decimal=canonical)

    def parse_identifier_term(self, mode: ExprMode, depth: int) -> HclExpression | None:
        name_token = self.peek()
        name = self.text(name_token)
        self.advance()
        self.skip_expression_trivia(mode)
        if self.at(HclTokenKind.PAREN_OPEN):
            return self.parse_call(name_token, depth)
        if name == "true":
            base = HclExpressionKind.boolean(True)
        elif name == "false":
            base = HclExpressionKind.boolean(False)
        elif name == "null":
            base = HclExpressionKind.null()
        else:
            base = HclExpressionKind.variable_ref(name)
        steps: list[HclTraversalStep] = []
        end = name_token.span.end_byte
        while True:
            self.skip_expression_trivia(mode)
            kind = self.peek_kind()
            if kind is HclTokenKind.DOT:
                dot = self.advance()
                self.skip_expression_trivia(mode)
                next_kind = self.peek_kind()
                if next_kind is HclTokenKind.IDENTIFIER:
                    ident = self.advance()
                    steps.append(
                        HclTraversalStep.get_attr(
                            self.text(ident),
                            self.span(dot.span.start_byte, ident.span.end_byte),
                        )
                    )
                    end = ident.span.end_byte
                elif next_kind is HclTokenKind.STAR:
                    star = self.advance()
                    end = star.span.end_byte
                    nested: list[HclTraversalStep] = []
                    while True:
                        self.skip_expression_trivia(mode)
                        if not self.at(HclTokenKind.DOT):
                            break
                        ndot = self.advance()
                        self.skip_expression_trivia(mode)
                        if not self.at(HclTokenKind.IDENTIFIER):
                            self.diagnose(
                                CODE_EXPRESSION,
                                self.peek().span.start_byte,
                                self.peek().span.end_byte,
                                DiagnosticCategory.SYNTAX,
                            )
                            return None
                        nident = self.advance()
                        nested.append(
                            HclTraversalStep.get_attr(
                                self.text(nident),
                                self.span(ndot.span.start_byte, nident.span.end_byte),
                            )
                        )
                        end = nident.span.end_byte
                    steps.append(HclTraversalStep.attr_splat(tuple(nested)))
                else:
                    self.diagnose(
                        CODE_EXPRESSION,
                        self.peek().span.start_byte,
                        self.peek().span.end_byte,
                        DiagnosticCategory.SYNTAX,
                    )
                    return None
            elif kind is HclTokenKind.BRACKET_OPEN:
                self.brackets.append(Delim.BRACKET)
                open = self.advance()
                self.skip_structural()
                if self.at(HclTokenKind.STAR):
                    self.advance()
                    self.skip_structural()
                    if not self.at(HclTokenKind.BRACKET_CLOSE):
                        self.diagnose(
                            CODE_EXPRESSION,
                            self.peek().span.start_byte,
                            self.peek().span.end_byte,
                            DiagnosticCategory.SYNTAX,
                        )
                        return None
                    close = self.advance()
                    end = close.span.end_byte
                    nested: list[HclTraversalStep] = []
                    while True:
                        self.skip_expression_trivia(mode)
                        if self.at(HclTokenKind.DOT):
                            dot = self.advance()
                            self.skip_expression_trivia(mode)
                            if not self.at(HclTokenKind.IDENTIFIER):
                                self.diagnose(
                                    CODE_EXPRESSION,
                                    self.peek().span.start_byte,
                                    self.peek().span.end_byte,
                                    DiagnosticCategory.SYNTAX,
                                )
                                return None
                            ident = self.advance()
                            nested.append(
                                HclTraversalStep.get_attr(
                                    self.text(ident),
                                    self.span(dot.span.start_byte, ident.span.end_byte),
                                )
                            )
                            end = ident.span.end_byte
                        elif self.at(HclTokenKind.BRACKET_OPEN):
                            index_open = self.advance()
                            self.brackets.append(Delim.BRACKET)
                            self.skip_structural()
                            key = self.parse_expression(ExprMode.NESTED, depth + 1)
                            if key is None:
                                return None
                            self.skip_structural()
                            if not self.at(HclTokenKind.BRACKET_CLOSE):
                                self.diagnose(
                                    CODE_EXPRESSION,
                                    self.peek().span.start_byte,
                                    self.peek().span.end_byte,
                                    DiagnosticCategory.SYNTAX,
                                )
                                return None
                            index_close = self.advance()
                            self.brackets.pop()
                            nested.append(
                                HclTraversalStep.index(
                                    key,
                                    self.span(index_open.span.start_byte, index_close.span.end_byte),
                                )
                            )
                            end = index_close.span.end_byte
                        else:
                            break
                    steps.append(HclTraversalStep.full_splat(tuple(nested)))
                    self.brackets.pop()
                else:
                    key = self.parse_expression(ExprMode.NESTED, depth + 1)
                    if key is None:
                        return None
                    self.skip_structural()
                    if not self.at(HclTokenKind.BRACKET_CLOSE):
                        self.diagnose(
                            CODE_EXPRESSION,
                            self.peek().span.start_byte,
                            self.peek().span.end_byte,
                            DiagnosticCategory.SYNTAX,
                        )
                        return None
                    close = self.advance()
                    self.brackets.pop()
                    steps.append(
                        HclTraversalStep.index(
                            key,
                            self.span(open.span.start_byte, close.span.end_byte),
                        )
                    )
                    end = close.span.end_byte
            else:
                break
        if not steps:
            return HclExpression(base, name_token.span)
        if name == "true":
            root = HclTraversalRoot.boolean(True)
        elif name == "false":
            root = HclTraversalRoot.boolean(False)
        elif name == "null":
            root = HclTraversalRoot.null()
        else:
            root = HclTraversalRoot.variable(name)
        return HclExpression(
            HclExpressionKind.traversal(root, tuple(steps)),
            self.span(name_token.span.start_byte, end),
        )

    def parse_call(self, name_token: HclToken, depth: int) -> HclExpression | None:
        self.brackets.append(Delim.PAREN)
        self.advance()  # open paren
        args: list[HclCallArg] = []
        close = None
        while True:
            self.skip_structural()
            if self.at(HclTokenKind.PAREN_CLOSE):
                close = self.advance()
                break
            expression = self.parse_expression(ExprMode.NESTED, depth + 1)
            if expression is None:
                return None
            expand = False
            self.skip_structural()
            if self.at(HclTokenKind.ELLIPSIS):
                self.advance()
                expand = True
                self.skip_structural()
                if self.at(HclTokenKind.COMMA):
                    self.advance()
                    self.skip_structural()
                if not self.at(HclTokenKind.PAREN_CLOSE):
                    self.diagnose(
                        CODE_EXPRESSION,
                        self.peek().span.start_byte,
                        self.peek().span.end_byte,
                        DiagnosticCategory.SYNTAX,
                    )
                    return None
            args.append(HclCallArg(expression, expand))
            if self.at(HclTokenKind.PAREN_CLOSE):
                close = self.advance()
                break
            if self.at(HclTokenKind.COMMA) or self.at(HclTokenKind.LINE_BREAK) or self.at(
                HclTokenKind.LINE_COMMENT
            ):
                self.advance()
                continue
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        self.brackets.pop()
        return HclExpression(
            HclExpressionKind.function_call(
                self.text(name_token), name_token.span, tuple(args)
            ),
            self.span(name_token.span.start_byte, close.span.end_byte),
        )

    def parse_paren(self, depth: int) -> HclExpression | None:
        self.brackets.append(Delim.PAREN)
        open = self.advance()
        self.skip_structural()
        inner = self.parse_expression(ExprMode.NESTED, depth + 1)
        if inner is None:
            return None
        self.skip_structural()
        if not self.at(HclTokenKind.PAREN_CLOSE):
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        close = self.advance()
        self.brackets.pop()
        return HclExpression(
            HclExpressionKind.paren(inner),
            self.span(open.span.start_byte, close.span.end_byte),
        )

    def parse_bracket(self, depth: int) -> HclExpression | None:
        self.brackets.append(Delim.BRACKET)
        open = self.advance()
        self.skip_structural()
        if self.at(HclTokenKind.IDENTIFIER) and self.text(self.peek()) == "for":
            return self.parse_for_tuple(open, depth)
        elements: list[HclExpression] = []
        close = None
        while True:
            self.skip_structural()
            if self.at(HclTokenKind.BRACKET_CLOSE):
                close = self.advance()
                break
            element = self.parse_expression(ExprMode.NESTED, depth + 1)
            if element is None:
                return None
            if len(elements) >= self.limits.max_tuple_elements:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="tuple-elements",
                    observed=len(elements) + 1,
                    limit=self.limits.max_tuple_elements,
                )
            elements.append(element)
            self.skip_trivia()
            kind = self.peek_kind()
            if kind in (HclTokenKind.COMMA, HclTokenKind.LINE_BREAK, HclTokenKind.LINE_COMMENT):
                self.advance()
            elif kind is HclTokenKind.BRACKET_CLOSE:
                pass
            else:
                self.diagnose(
                    CODE_SEPARATOR,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
        self.brackets.pop()
        return HclExpression(
            HclExpressionKind.tuple(tuple(elements)),
            self.span(open.span.start_byte, close.span.end_byte),
        )

    def parse_brace(self, depth: int) -> HclExpression | None:
        self.brackets.append(Delim.BRACE)
        open = self.advance()
        self.skip_structural()
        if self.at(HclTokenKind.IDENTIFIER) and self.text(self.peek()) == "for":
            return self.parse_for_object(open, depth)
        entries: list[HclObjectEntry] = []
        close = None
        while True:
            self.skip_structural()
            if self.at(HclTokenKind.BRACE_CLOSE):
                close = self.advance()
                break
            kind = self.peek_kind()
            if kind is HclTokenKind.IDENTIFIER:
                token = self.advance()
                key = HclObjectKey.identifier(self.text(token))
            elif kind is HclTokenKind.NUMBER:
                token = self.advance()
                key = HclObjectKey.number_key(self.number(token))
            elif kind is HclTokenKind.STRING_OPEN:
                parsed = self.parse_quoted_template(depth)
                if parsed is None:
                    return None
                parts, span = parsed
                key = HclObjectKey.template_key(HclTemplateKey(parts, span))
            elif kind is HclTokenKind.PAREN_OPEN:
                inner = self.parse_paren(depth)
                if inner is None:
                    return None
                key = HclObjectKey.paren(inner)
            else:
                self.diagnose(
                    CODE_EXPRESSION,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            self.skip_structural()
            kind = self.peek_kind()
            if kind is HclTokenKind.EQUALS:
                self.advance()
                separator = ObjectSeparator.EQUALS
            elif kind is HclTokenKind.COLON:
                self.advance()
                separator = ObjectSeparator.COLON
            else:
                self.diagnose(
                    CODE_EXPRESSION,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            self.skip_structural()
            value = self.parse_expression(ExprMode.NESTED, depth + 1)
            if value is None:
                return None
            if len(entries) >= self.limits.max_object_entries:
                raise HclFormationFailure(
                    HclFormationFailureKind.RESOURCE_LIMIT,
                    resource_name="object-entries",
                    observed=len(entries) + 1,
                    limit=self.limits.max_object_entries,
                )
            entries.append(HclObjectEntry(key, separator, value))
            self.skip_trivia()
            kind = self.peek_kind()
            if kind in (HclTokenKind.COMMA, HclTokenKind.LINE_BREAK, HclTokenKind.LINE_COMMENT):
                self.advance()
            elif kind is HclTokenKind.BRACE_CLOSE:
                pass
            else:
                self.diagnose(
                    CODE_SEPARATOR,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
        self.brackets.pop()
        return HclExpression(
            HclExpressionKind.object(tuple(entries)),
            self.span(open.span.start_byte, close.span.end_byte),
        )

    def parse_for_intro(self, for_start: int, depth: int, expect_colon: bool) -> HclForIntro | None:
        self.skip_structural()
        if self.at(HclTokenKind.IDENTIFIER):
            first_token = self.advance()
        else:
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        key = None
        self.skip_structural()
        if self.at(HclTokenKind.COMMA):
            self.advance()
            self.skip_structural()
            if self.at(HclTokenKind.IDENTIFIER):
                value_token = self.advance()
            else:
                self.diagnose(
                    CODE_EXPRESSION,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            key = self.text(first_token)
            value = self.text(value_token)
            self.skip_structural()
        else:
            value = self.text(first_token)
        if not (self.at(HclTokenKind.IDENTIFIER) and self.text(self.peek()) == "in"):
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        self.advance()
        self.skip_structural()
        collection = self.parse_expression(ExprMode.NESTED, depth + 1)
        if collection is None:
            return None
        end = collection.span.end_byte
        if expect_colon:
            self.skip_structural()
            if not self.at(HclTokenKind.COLON):
                self.diagnose(
                    CODE_EXPRESSION,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            end = self.advance().span.end_byte
        return HclForIntro(
            key=key,
            value=value,
            collection=collection,
            span=self.span(for_start, end),
        )

    def parse_for_condition(self, depth: int) -> HclExpression | None:
        if self.at(HclTokenKind.IDENTIFIER) and self.text(self.peek()) == "if":
            self.advance()
            self.skip_structural()
            return self.parse_expression(ExprMode.NESTED, depth + 1)
        return None

    def check_for_extent(self, span) -> None:
        extent = span.end_byte - span.start_byte
        if extent > self.limits.max_for_extent:
            raise HclFormationFailure(
                HclFormationFailureKind.RESOURCE_LIMIT,
                resource_name="for-extent",
                observed=extent,
                limit=self.limits.max_for_extent,
            )

    def parse_for_tuple(self, open: HclToken, depth: int) -> HclExpression | None:
        for_token = self.advance()
        intro = self.parse_for_intro(for_token.span.start_byte, depth, True)
        if intro is None:
            return None
        self.skip_structural()
        value = self.parse_expression(ExprMode.NESTED, depth + 1)
        if value is None:
            return None
        self.skip_structural()
        condition = self.parse_for_condition(depth)
        self.skip_structural()
        if not self.at(HclTokenKind.BRACKET_CLOSE):
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        close = self.advance()
        self.brackets.pop()
        span = self.span(open.span.start_byte, close.span.end_byte)
        self.check_for_extent(span)
        return HclExpression(HclExpressionKind.for_tuple(intro, value, condition), span)

    def parse_for_object(self, open: HclToken, depth: int) -> HclExpression | None:
        for_token = self.advance()
        intro = self.parse_for_intro(for_token.span.start_byte, depth, True)
        if intro is None:
            return None
        self.skip_structural()
        key = self.parse_expression(ExprMode.NESTED, depth + 1)
        if key is None:
            return None
        self.skip_structural()
        if not self.at(HclTokenKind.ARROW):
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        self.advance()
        self.skip_structural()
        value = self.parse_expression(ExprMode.NESTED, depth + 1)
        if value is None:
            return None
        grouping = False
        self.skip_structural()
        if self.at(HclTokenKind.ELLIPSIS):
            self.advance()
            grouping = True
        self.skip_structural()
        condition = self.parse_for_condition(depth)
        self.skip_structural()
        if not self.at(HclTokenKind.BRACE_CLOSE):
            self.diagnose(
                CODE_EXPRESSION,
                self.peek().span.start_byte,
                self.peek().span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        close = self.advance()
        self.brackets.pop()
        span = self.span(open.span.start_byte, close.span.end_byte)
        self.check_for_extent(span)
        return HclExpression(
            HclExpressionKind.for_object(intro, key, value, grouping, condition), span
        )

    # -- templates and heredocs (RFC 0014 §4.4-§4.5) -------------------------

    def parse_quoted_template(self, depth: int) -> tuple[tuple[HclTemplatePart, ...], object] | None:
        open = self.advance()
        parts: list[HclTemplatePart] = []
        while True:
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.STRING_CLOSE:
                close = self.advance()
                span = self.span(open.span.start_byte, close.span.end_byte)
                return tuple(parts), span
            if kind is HclTokenKind.STRING_CONTENT:
                self.advance()
                parts.append(
                    HclTemplatePart.literal(token.span, decode_quoted_literal(self.text(token)))
                )
            elif kind in (HclTokenKind.INTERPOLATION_OPEN, HclTokenKind.DIRECTIVE_OPEN):
                directive = kind is HclTokenKind.DIRECTIVE_OPEN
                part_open = self.advance()
                content_kind = (
                    HclTokenKind.DIRECTIVE_CONTENT if directive else HclTokenKind.INTERPOLATION_CONTENT
                )
                close_kind = (
                    HclTokenKind.DIRECTIVE_CLOSE if directive else HclTokenKind.INTERPOLATION_CLOSE
                )
                content = self.eat(content_kind)
                if content is None:
                    return None
                part_close = self.eat(close_kind)
                if part_close is None:
                    return None
                part_span = self.span(part_open.span.start_byte, part_close.span.end_byte)
                if directive:
                    kind = self.parse_directive_region(content.span, depth + 1)
                    if kind is None:
                        return None
                    parts.append(HclTemplatePart.directive_part(part_span, kind))
                else:
                    expression = self.parse_region_expression(content.span, depth + 1)
                    if expression is None:
                        return None
                    parts.append(HclTemplatePart.interpolation(part_span, expression))
            elif kind in (HclTokenKind.ERROR_REGION, HclTokenKind.EOF):
                return None
            else:
                self.diagnose(
                    CODE_EXPRESSION,
                    token.span.start_byte,
                    token.span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None

    def parse_heredoc(self, depth: int) -> HclExpression | None:
        open = self.advance()
        self.skip_trivia()
        if not self.at(HclTokenKind.LINE_BREAK):
            return None
        self.advance()
        parts: list[HclTemplatePart] = []
        while True:
            token = self.peek()
            kind = token.kind
            if kind is HclTokenKind.HEREDOC_CLOSE:
                close = self.advance()
                heredoc_span = self.span(open.span.start_byte, close.span.end_byte)
                text = self.text(open)
                if text.startswith("<<-"):
                    mode = HeredocMode.STRIP_INDENT
                else:
                    mode = HeredocMode.PLAIN
                marker_start = open.span.start_byte + (3 if mode is HeredocMode.STRIP_INDENT else 2)
                marker = self.bytes[marker_start : open.span.end_byte].decode("utf-8")
                facts = HeredocFacts(
                    mode=mode,
                    marker=marker,
                    marker_span=self.span(marker_start, open.span.end_byte),
                    closing_span=close.span,
                )
                return HclExpression(
                    HclExpressionKind.template(tuple(parts), facts), heredoc_span
                )
            if kind is HclTokenKind.HEREDOC_CONTENT:
                self.advance()
                parts.append(
                    HclTemplatePart.literal(token.span, decode_heredoc_literal(self.text(token)))
                )
            elif kind is HclTokenKind.LINE_BREAK:
                token = self.advance()
                parts.append(HclTemplatePart.literal(token.span, "\n"))
            elif kind in (HclTokenKind.INTERPOLATION_OPEN, HclTokenKind.DIRECTIVE_OPEN):
                directive = kind is HclTokenKind.DIRECTIVE_OPEN
                part_open = self.advance()
                content_kind = (
                    HclTokenKind.DIRECTIVE_CONTENT if directive else HclTokenKind.INTERPOLATION_CONTENT
                )
                close_kind = (
                    HclTokenKind.DIRECTIVE_CLOSE if directive else HclTokenKind.INTERPOLATION_CLOSE
                )
                content = self.eat(content_kind)
                if content is None:
                    return None
                part_close = self.eat(close_kind)
                if part_close is None:
                    return None
                part_span = self.span(part_open.span.start_byte, part_close.span.end_byte)
                if directive:
                    kind = self.parse_directive_region(content.span, depth + 1)
                    if kind is None:
                        return None
                    parts.append(HclTemplatePart.directive_part(part_span, kind))
                else:
                    expression = self.parse_region_expression(content.span, depth + 1)
                    if expression is None:
                        return None
                    parts.append(HclTemplatePart.interpolation(part_span, expression))
            else:
                return None

    def parse_region_expression(self, span, depth: int) -> HclExpression | None:
        return self.with_region(span, lambda sub: sub.parse_expression_region(depth))

    def parse_directive_region(self, span, depth: int) -> HclDirectiveKind | None:
        return self.with_region(span, lambda sub: sub.parse_directive(depth))

    def with_region(self, span, parse) -> object | None:
        output = lex_region(
            self.source,
            self.authority,
            span.start_byte,
            span.end_byte,
            self.limits,
        )
        self.recovered = self.recovered or output.recovered
        for diagnostic in output.diagnostics:
            self.sink.push(diagnostic)
        for region in output.error_regions:
            self.error_regions.append(region)
        self.check_error_region_limits()
        sub = _Parser(
            lexed=output,
            source=self.source,
            decoded=self.decoded,
            authority=self.authority,
            limits=self.limits,
            sink_cap=2**63 - 1,
            recovered=output.recovered,
            error_regions=[],
            sink=_DiagnosticSink(2**63 - 1),
            brackets=[],
        )
        result = parse(sub)
        self.recovered = self.recovered or sub.recovered
        for diagnostic in sub.sink.finish():
            self.sink.push(diagnostic)
        for region in sub.error_regions:
            self.error_regions.append(region)
        self.check_error_region_limits()
        return result

    def parse_expression_region(self, depth: int) -> HclExpression | None:
        expression = self.parse_expression(ExprMode.NESTED, depth)
        if expression is None:
            return None
        self.skip_structural()
        if self.at(HclTokenKind.EOF):
            return expression
        self.diagnose(
            CODE_EXPRESSION,
            self.peek().span.start_byte,
            self.peek().span.end_byte,
            DiagnosticCategory.SYNTAX,
        )
        return None

    def parse_directive(self, depth: int) -> HclDirectiveKind | None:
        self.skip_structural()
        token = self.peek()
        if token.kind is not HclTokenKind.IDENTIFIER:
            self.diagnose(
                CODE_DIRECTIVE,
                token.span.start_byte,
                token.span.end_byte,
                DiagnosticCategory.SYNTAX,
            )
            return None
        text = self.text(token)
        if text == "if":
            self.advance()
            self.skip_structural()
            condition = self.parse_expression(ExprMode.NESTED, depth + 1)
            if condition is None:
                return None
            self.skip_structural()
            if not self.at(HclTokenKind.EOF):
                self.diagnose(
                    CODE_DIRECTIVE,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            return HclDirectiveKind.if_kind(condition)
        if text in ("else", "endif", "endfor"):
            self.advance()
            self.skip_structural()
            if not self.at(HclTokenKind.EOF):
                self.diagnose(
                    CODE_DIRECTIVE,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            if text == "else":
                return HclDirectiveKind.else_kind()
            if text == "endif":
                return HclDirectiveKind.endif()
            return HclDirectiveKind.endfor()
        if text == "for":
            for_token = self.advance()
            intro = self.parse_for_intro(for_token.span.start_byte, depth, False)
            if intro is None:
                return None
            self.skip_structural()
            if not self.at(HclTokenKind.EOF):
                self.diagnose(
                    CODE_DIRECTIVE,
                    self.peek().span.start_byte,
                    self.peek().span.end_byte,
                    DiagnosticCategory.SYNTAX,
                )
                return None
            return HclDirectiveKind.for_kind(intro)
        self.diagnose(
            CODE_DIRECTIVE,
            token.span.start_byte,
            token.span.end_byte,
            DiagnosticCategory.SYNTAX,
        )
        return None


def parse_hcl(raw: bytes, limits: HclParseLimits) -> HclFormed:
    """Forms one native HCL document from raw bytes under the frozen UTF-8
    source contract (RFC 0014 §2; parser.rs:200-218).

    The source contract is enforced by the lexer: UTF-8 only, BOM as
    content with `hcl.parse.byte-order-mark@1` recovery, lone CR never a
    newline, invalid UTF-8 fatal. The parser then consumes the token stream
    and assembles the native body tree with the §3 recovery semantics. The
    whole formation is side-effect free: nothing is ever evaluated (hard
    gate 1).
    """
    lexed = lex(raw, limits)
    sink = _DiagnosticSink(limits.common.max_diagnostics)
    for diagnostic in lexed.diagnostics:
        sink.push(diagnostic)
    error_regions = list(lexed.error_regions)
    recovered = lexed.recovered
    decoded = lexed.source.decoded_text()
    assert decoded is not None
    parser = _Parser(
        lexed=lexed,
        source=lexed.source,
        decoded=decoded,
        authority=lexed.authority,
        limits=limits,
        sink_cap=limits.common.max_diagnostics,
        recovered=recovered,
        error_regions=error_regions,
        sink=sink,
        brackets=[],
    )
    body = parser.parse_body(1, BodyEnd.EOF)
    status = FormationStatus.RECOVERED if parser.recovered else FormationStatus.COMPLETE
    error_regions.sort(key=lambda region: region.span.start_byte)
    ordinals, tree_nodes = _assign_ordinals(body)
    return HclFormed(
        source=lexed.source,
        authority=lexed.authority,
        status=status,
        diagnostics=tuple(sink.finish()),
        body=body,
        error_regions=tuple(error_regions),
        syntax=lexed.syntax,
        syntax_kinds=lexed.syntax_kinds,
        limits=limits,
        ordinals=ordinals,
        tree_nodes=tree_nodes,
    )


def _assign_ordinals(body: HclBody) -> tuple[dict[int, int], int]:
    """Assigns the deterministic pre-order tree ordinals (projection.rs:
    124-130): the root body first, then each item in source order; an
    attribute consumes one ordinal for itself and then every node of its
    expression subtree; a block consumes one ordinal for itself, one per
    label, and then its nested body's items. Template parts are tree nodes;
    traversal steps and non-template object keys are not."""
    ordinals: dict[int, int] = {}
    counter = 0

    def walk_part(part: HclTemplatePart) -> None:
        nonlocal counter
        ordinals[id(part)] = counter
        counter += 1
        if part.kind == "interpolation" and part.expression is not None:
            walk_expr(part.expression)
        elif part.kind == "directive" and part.directive is not None:
            directive = part.directive
            if directive.kind == "if" and directive.condition is not None:
                walk_expr(directive.condition)
            elif directive.kind == "for" and directive.intro is not None:
                walk_expr(directive.intro.collection)

    def walk_expr(expr: HclExpression) -> None:
        nonlocal counter
        ordinals[id(expr)] = counter
        counter += 1
        name = expr.kind.name
        payload = expr.kind.payload
        if name is HclExpressionKindName.TEMPLATE:
            parts, _ = payload
            for part in parts:
                walk_part(part)
        elif name is HclExpressionKindName.OBJECT:
            for entry in payload:
                key = entry.key
                if key.kind == "paren" and key.inner is not None:
                    walk_expr(key.inner)
                elif key.kind == "template" and key.template is not None:
                    for part in key.template.parts:
                        walk_part(part)
                walk_expr(entry.value)
        else:
            for child in expr.children():
                walk_expr(child)

    def walk_body(current: HclBody) -> None:
        nonlocal counter
        ordinals[id(current)] = counter
        counter += 1
        for item in current.items:
            attribute = item.as_attribute()
            if attribute is not None:
                ordinals[id(attribute)] = counter
                counter += 1
                walk_expr(attribute.expression)
            else:
                block = item.as_block()
                ordinals[id(block)] = counter
                counter += 1
                for label in block.labels:
                    ordinals[id(label)] = counter
                    counter += 1
                walk_body(block.body)

    walk_body(body)
    return ordinals, counter


# ---------------------------------------------------------------------------
# Literal decoding (parser.rs:2387-2531)
# ---------------------------------------------------------------------------


def decode_quoted_literal(text: str) -> str:
    """Decodes one quoted-template literal run: the frozen escape sequences
    `\\n` `\\r` `\\t` `\\"` `\\\\` `\\uNNNN` `\\UNNNNNNNN` and the escaped
    openers `$${`/`%%{` (RFC 0014 §4.4). An invalid escape (already
    recovered by the lexer) passes through unchanged (parser.rs:2387-2482).
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        byte = text[index]
        if byte == "\\":
            if index + 1 >= len(text):
                out.append("\\")
                index += 1
                continue
            escaped = text[index + 1]
            if escaped == "n":
                out.append("\n")
                index += 2
            elif escaped == "r":
                out.append("\r")
                index += 2
            elif escaped == "t":
                out.append("\t")
                index += 2
            elif escaped == '"':
                out.append('"')
                index += 2
            elif escaped == "\\":
                out.append("\\")
                index += 2
            elif escaped == "u":
                hex_text = text[index + 2 : index + 6]
                if len(hex_text) == 4:
                    try:
                        value = int(hex_text, 16)
                        out.append(chr(value))
                        index += 6
                        continue
                    except (ValueError, OverflowError):
                        pass
                out.append("\\")
                index += 1
            elif escaped == "U":
                hex_text = text[index + 2 : index + 10]
                if len(hex_text) == 8:
                    try:
                        value = int(hex_text, 16)
                        out.append(chr(value))
                        index += 10
                        continue
                    except (ValueError, OverflowError):
                        pass
                out.append("\\")
                index += 1
            else:
                out.append("\\")
                index += 1
        elif byte == "$":
            if text[index + 1 : index + 3] == "${":
                out.append("${")
                index += 3
            else:
                out.append("$")
                index += 1
        elif byte == "%":
            if text[index + 1 : index + 3] == "%{":
                out.append("%{")
                index += 3
            else:
                out.append("%")
                index += 1
        else:
            out.append(byte)
            index += 1
    return "".join(out)


def decode_heredoc_literal(text: str) -> str:
    """Decodes one heredoc literal run: only the `$${`/`%%{` escapes apply;
    heredoc text is otherwise raw (RFC 0014 §4.5; parser.rs:2484-2518)."""
    out: list[str] = []
    index = 0
    while index < len(text):
        byte = text[index]
        if byte == "$":
            if text[index + 1 : index + 3] == "${":
                out.append("${")
                index += 3
            else:
                out.append("$")
                index += 1
        elif byte == "%":
            if text[index + 1 : index + 3] == "%{":
                out.append("%{")
                index += 3
            else:
                out.append("%")
                index += 1
        else:
            out.append(byte)
            index += 1
    return "".join(out)
