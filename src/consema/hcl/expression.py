"""HCL expression AST (RFC 0014 §4.3-§4.6, §6, §8.1).

An expression is a first-class native role: the AST retains the frozen
grammar as a kind with ordered children and exact half-open raw-byte spans,
and its exact source text is always derived from the span against the
immutable source — no re-encoding is needed and no information is lost
(RFC 0014 §6). Both representations are always available: the AST for
structure, the span-derived text for exactness.

Structural equality (RFC 0014 §6) is recursive over kind and children:
number equality is canonical-decimal equality, template equality is
part-wise with exact literal text and structural interpolation/directive
comparison, constructor equality is element-wise, and node identity and
source spans are never part of value equality. This is the equality used
by query filters, projection comparison, and the `hcl.expression@1`
contract.

The literal-complete boundary (RFC 0014 §8.1) is a purely syntactic
predicate: no evaluation, no arithmetic folding, no context. It is
decidable without any evaluator, and `literal_value` extracts the typed
literal projection that the `hcl.body@1` record consumes at projection
time. Numbers normalize to canonical decimal by pure decimal string
arithmetic — zero floating-point computation (hard gate 1) — so `1.50`,
`1.5`, and `15e-1` compare equal as values while remaining distinct source
facts.

Authority (language-neutral first; Rust only for byte/registry
arbitration):

- Kind model and equality: crates/consema-hcl/src/expression.rs:192-559.
- canonical_decimal: expression.rs:719-851 (pure decimal string
  arithmetic; the exponent folding is bounded by the frozen
  max_number_digits budget of HclParseLimits, expression.rs:736-851).
- Literal boundary and typed projection: expression.rs:1506-1786.
- Structural fingerprint serialization: crates/consema-hcl/src/
  materialization.rs:1496-1768 (FNV-1a 64-bit over the canonical
  structural serialization; the shared M6/M7 adaptation point of the
  `hcl.expression@1` codec).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import Span
from consema.hcl.kinds import HclExpressionKindName

# FNV-1a 64-bit parameters (materialization.rs:1507-1515).
_FNV_OFFSET_BASIS = 0xCBF2_9CE4_8422_2325
_FNV_PRIME = 0x0000_0100_0000_01B3


# ---------------------------------------------------------------------------
# Operators and traversal facts
# ---------------------------------------------------------------------------


class UnaryOp(enum.Enum):
    """Unary operator set; exactly `-` and `!` exist (RFC 0014 §4.3,
    expression.rs:853-882)."""

    MINUS = "-"
    NOT = "!"


class BinaryOp(enum.Enum):
    """Binary operator set, frozen by the RFC 0014 §4.3 precedence table
    (expression.rs:884-956)."""

    EQUAL = "=="
    NOT_EQUAL = "!="
    LESS = "<"
    GREATER = ">"
    LESS_EQUAL = "<="
    GREATER_EQUAL = ">="
    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "*"
    DIVIDE = "/"
    MODULO = "%"
    AND = "&&"
    OR = "||"


class HeredocMode(enum.Enum):
    """Heredoc mode fact: `<<` or `<<-` (RFC 0014 §4.5, expression.rs:1157-
    1176)."""

    PLAIN = "<<"
    STRIP_INDENT = "<<-"


class ObjectSeparator(enum.Enum):
    """Object-constructor key/value separator source fact (RFC 0014 §4.6,
    expression.rs:1486-1504)."""

    EQUALS = "="
    COLON = ":"


@dataclass(frozen=True, slots=True)
class HeredocFacts:
    """Heredoc representation facts of one template (RFC 0014 §4.5, §6;
    expression.rs:1178-1249).

    The mode, marker spelling, marker span, and closing-line span are
    preserved representation facts; the `<<-` indentation stripping is
    performed only when the template's literal value is read, never
    destructively. Structural equality compares the mode and marker
    spelling only (expression.rs:1236-1249).
    """

    mode: HeredocMode
    marker: str
    marker_span: Span
    closing_span: Span | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HeredocFacts):
            return NotImplemented
        return self.mode is other.mode and self.marker == other.marker

    def __hash__(self) -> int:
        return hash((self.mode, self.marker))


@dataclass(frozen=True, slots=True)
class HclTraversalRoot:
    """Traversal root; keyword spellings are dual-read roots (RFC 0014
    §4.1, expression.rs:958-970).

    ``kind`` is "variable" | "boolean" | "null"; ``name`` is the variable
    spelling when kind is "variable".
    """

    kind: str
    name: str | None = None

    @classmethod
    def variable(cls, name: str) -> HclTraversalRoot:
        return cls("variable", name)

    @classmethod
    def boolean(cls, value: bool) -> HclTraversalRoot:
        return cls("boolean", "true" if value else "false")

    @classmethod
    def null(cls) -> HclTraversalRoot:
        return cls("null")

    @property
    def boolean_value(self) -> bool | None:
        if self.kind == "boolean":
            return self.name == "true"
        return None


@dataclass(frozen=True, slots=True)
class HclTraversalStep:
    """One static traversal step (RFC 0014 §4.3, expression.rs:971-1040).

    Attribute steps admit identifiers only: the numeric form `foo.0` is a
    grammar error (RFC 0014 §12 D-5). Splat steps nest further steps.
    ``kind`` is "get-attr" | "index" | "attr-splat" | "full-splat".
    Structural equality never includes the step span (expression.rs:1005-
    1040).
    """

    kind: str
    name: str | None = None
    key: HclExpression | None = None
    steps: tuple[HclTraversalStep, ...] = ()
    span: Span | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclTraversalStep):
            return NotImplemented
        if self.kind != other.kind:
            return False
        if self.kind == "get-attr":
            return self.name == other.name
        if self.kind == "index":
            return self.key == other.key
        return self.steps == other.steps

    def __hash__(self) -> int:
        if self.kind == "get-attr":
            return hash((self.kind, self.name))
        if self.kind == "index":
            return hash((self.kind, self.key))
        return hash((self.kind, self.steps))

    @classmethod
    def get_attr(cls, name: str, span: Span | None = None) -> HclTraversalStep:
        return cls("get-attr", name=name, span=span)

    @classmethod
    def index(cls, key: HclExpression, span: Span | None = None) -> HclTraversalStep:
        return cls("index", key=key, span=span)

    @classmethod
    def attr_splat(cls, steps: tuple[HclTraversalStep, ...]) -> HclTraversalStep:
        return cls("attr-splat", steps=steps)

    @classmethod
    def full_splat(cls, steps: tuple[HclTraversalStep, ...]) -> HclTraversalStep:
        return cls("full-splat", steps=steps)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HclNumber:
    """Exact decimal number literal: source spelling plus canonical value
    (RFC 0014 §4.1, §6, §8; expression.rs:644-717).

    Numeric equality is canonical-decimal equality, so `1.50`, `1.5`, and
    `15e-1` compare equal as values while remaining distinct source facts
    (expression.rs:705-717).
    """

    span: Span
    canonical_decimal: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclNumber):
            return NotImplemented
        return self.canonical_decimal == other.canonical_decimal

    def __hash__(self) -> int:
        return hash(self.canonical_decimal)


def canonical_decimal(spelling: str, max_digits: int = 100_000) -> str | None:
    """Normalizes one decimal number spelling to its canonical form by pure
    decimal string arithmetic — zero floating-point computation (hard
    gate 1; RFC 0014 §4.1, §9; expression.rs:719-851).

    The canonical form strips leading zeros, strips trailing fraction
    zeros, and folds the exponent into the decimal point position, so
    `"1.50"` and `"15e-1"` both normalize to `"1.5"`, `"1e3"` to `"1000"`,
    and every zero spelling to `"0"`. Returns `None` for a grammar
    violation or a canonical spelling exceeding the `max_digits` digit
    budget — checked before any zero-padding loop runs (RFC 0014 §11).
    """
    index = 0
    while index < len(spelling) and spelling[index].isdigit():
        index += 1
    integer_len = index
    if integer_len == 0:
        return None
    fraction_len = 0
    if index < len(spelling) and spelling[index] == ".":
        index += 1
        fraction_start = index
        while index < len(spelling) and spelling[index].isdigit():
            index += 1
        fraction_len = index - fraction_start
        if fraction_len == 0:
            return None
    exponent = 0
    if index < len(spelling) and spelling[index] in ("e", "E"):
        index += 1
        negative = False
        if index < len(spelling) and spelling[index] in ("+", "-"):
            negative = spelling[index] == "-"
            index += 1
        exponent_start = index
        while index < len(spelling) and spelling[index].isdigit():
            index += 1
        if index == exponent_start:
            return None
        try:
            magnitude = int(spelling[exponent_start:index])
        except ValueError:
            return None
        exponent = -magnitude if negative else magnitude
    if index != len(spelling):
        return None
    digits = spelling[:integer_len]
    if fraction_len > 0:
        digits += spelling[integer_len + 1 : integer_len + 1 + fraction_len]
    stripped = digits.lstrip("0")
    point = integer_len + exponent - (len(digits) - len(stripped))
    if not stripped:
        return "0"
    if point <= 0:
        zeros = -point
        trimmed = stripped.rstrip("0")
        if zeros + len(trimmed) + 1 > max_digits:
            return None
        out = "0." + ("0" * zeros) + stripped
        while len(out) > 2 and out.endswith("0"):
            out = out[:-1]
        return out
    if point >= len(stripped):
        if point > max_digits:
            return None
        return stripped + ("0" * (point - len(stripped)))
    out = stripped[:point]
    fraction = stripped[point:].rstrip("0")
    if fraction:
        out += "." + fraction
    return out


def number_literal(canonical: str) -> "HclLiteralValue":
    """A canonical decimal without a fraction projects as an integer, one
    with a fraction as a real (RFC 0014 §8.2; expression.rs:1678-1684)."""
    if "." in canonical:
        return HclLiteralValue(kind="real", text=canonical)
    return HclLiteralValue(kind="integer", text=canonical)


# ---------------------------------------------------------------------------
# Template parts and directives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HclTemplatePart:
    """One ordered template part (RFC 0014 §6, expression.rs:1042-1130).

    A literal part keeps its exact escape-decoded text; the raw escaped
    spelling remains a source fact of the part's span. The `~` strip
    markers of interpolations and directives are span-internal source
    facts, never applied. ``kind`` is "literal" | "interpolation" |
    "directive". Structural equality (expression.rs:1091-1130): a literal
    part compares by decoded text, an interpolation by its expression, a
    directive by its kind.
    """

    kind: str
    span: Span
    text: str = ""
    expression: HclExpression | None = None
    directive: HclDirectiveKind | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclTemplatePart):
            return NotImplemented
        if self.kind == "literal":
            return other.kind == "literal" and self.text == other.text
        if self.kind == "interpolation":
            return other.kind == "interpolation" and self.expression == other.expression
        return other.kind == "directive" and self.directive == other.directive

    def __hash__(self) -> int:
        if self.kind == "literal":
            return hash(("literal", self.text))
        if self.kind == "interpolation":
            return hash(("interpolation", self.expression))
        return hash(("directive", self.directive))

    @classmethod
    def literal(cls, span: Span, text: str) -> HclTemplatePart:
        return cls("literal", span, text=text)

    @classmethod
    def interpolation(cls, span: Span, expression: HclExpression) -> HclTemplatePart:
        return cls("interpolation", span, expression=expression)

    @classmethod
    def directive_part(cls, span: Span, directive: HclDirectiveKind) -> HclTemplatePart:
        return cls("directive", span, directive=directive)

    def is_literal(self) -> bool:
        """Whether this part is a literal run with no interpolation or
        directive (expression.rs:1560-1566)."""
        return self.kind == "literal"


@dataclass(frozen=True, slots=True)
class HclDirectiveKind:
    """One template directive kind (RFC 0014 §4.4, expression.rs:1132-1155).

    The single-identifier for-directive `%{ for x in list }` is valid —
    the key is read only when a comma follows (RFC 0014 §12 D-7).
    ``kind`` is "if" | "else" | "endif" | "for" | "endfor".
    """

    kind: str
    condition: HclExpression | None = None
    intro: HclForIntro | None = None

    @classmethod
    def if_kind(cls, condition: HclExpression) -> HclDirectiveKind:
        return cls("if", condition=condition)

    @classmethod
    def else_kind(cls) -> HclDirectiveKind:
        return cls("else")

    @classmethod
    def endif(cls) -> HclDirectiveKind:
        return cls("endif")

    @classmethod
    def for_kind(cls, intro: HclForIntro) -> HclDirectiveKind:
        return cls("for", intro=intro)

    @classmethod
    def endfor(cls) -> HclDirectiveKind:
        return cls("endfor")


@dataclass(frozen=True, slots=True)
class HclForIntro:
    """The `for` introduction of a for-expression or for-directive (RFC
    0014 §4.6, expression.rs:1280-1347).

    Structural equality compares the key, value, and collection only
    (expression.rs:1333-1347).
    """

    key: str | None
    value: str
    collection: HclExpression
    span: Span

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclForIntro):
            return NotImplemented
        return (
            self.key == other.key
            and self.value == other.value
            and self.collection == other.collection
        )

    def __hash__(self) -> int:
        return hash((self.key, self.value, self.collection))


@dataclass(frozen=True, slots=True)
class HclCallArg:
    """One function-call argument with its expansion marker fact (RFC 0014
    §4.3, expression.rs:1251-1278)."""

    expression: HclExpression
    expand: bool = False


# ---------------------------------------------------------------------------
# Object constructors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HclObjectKey:
    """One object-constructor key (RFC 0014 §4.6, expression.rs:1349-1401).

    ``kind`` is "identifier" | "number" | "template" | "paren". Structural
    equality never includes spans (expression.rs:1366-1401).
    """

    kind: str
    name: str | None = None
    number: HclNumber | None = None
    template: HclTemplateKey | None = None
    inner: HclExpression | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclObjectKey) or self.kind != other.kind:
            return False
        if self.kind == "identifier":
            return self.name == other.name
        if self.kind == "number":
            return self.number == other.number
        if self.kind == "template":
            return self.template == other.template
        return self.inner == other.inner

    def __hash__(self) -> int:
        if self.kind == "identifier":
            return hash(("identifier", self.name))
        if self.kind == "number":
            return hash(("number", self.number))
        if self.kind == "template":
            return hash(("template", self.template))
        return hash(("paren", self.inner))

    @classmethod
    def identifier(cls, name: str) -> HclObjectKey:
        return cls("identifier", name=name)

    @classmethod
    def number_key(cls, number: HclNumber) -> HclObjectKey:
        return cls("number", number=number)

    @classmethod
    def template_key(cls, template: HclTemplateKey) -> HclObjectKey:
        return cls("template", template=template)

    @classmethod
    def paren(cls, inner: HclExpression) -> HclObjectKey:
        return cls("paren", inner=inner)


@dataclass(frozen=True, slots=True)
class HclTemplateKey:
    """A quoted-template object key (RFC 0014 §4.6, expression.rs:1403-
    1442).

    Structural equality compares the ordered parts only (expression.rs:1430-
    1442).
    """

    parts: tuple[HclTemplatePart, ...]
    span: Span

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HclTemplateKey):
            return NotImplemented
        return self.parts == other.parts

    def __hash__(self) -> int:
        return hash(self.parts)


@dataclass(frozen=True, slots=True)
class HclObjectEntry:
    """One ordered object-constructor entry: key, separator, and value
    (RFC 0014 §4.6, expression.rs:1444-1484).

    Duplicate keys are preserved as ordered native facts with independent
    spans and are never collapsed.
    """

    key: HclObjectKey
    separator: ObjectSeparator
    value: HclExpression


# ---------------------------------------------------------------------------
# Expression kinds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HclExpressionKind:
    """Closed native HCL expression kind (RFC 0014 §4.3-§4.6,
    expression.rs:192-312).

    ``name`` is the payload-free kind name of RFC 0014 §7.1; ``payload``
    is one variant-specific frozen record. A quoted template and a heredoc
    are one kind: a heredoc is a template whose HeredocFacts are carried
    explicitly.
    """

    name: HclExpressionKindName
    payload: object

    @classmethod
    def number(cls, number: HclNumber) -> HclExpressionKind:
        return cls(HclExpressionKindName.NUMBER, number)

    @classmethod
    def boolean(cls, value: bool) -> HclExpressionKind:
        return cls(HclExpressionKindName.BOOLEAN, value)

    @classmethod
    def null(cls) -> HclExpressionKind:
        return cls(HclExpressionKindName.NULL, None)

    @classmethod
    def template(
        cls,
        parts: tuple[HclTemplatePart, ...],
        heredoc: HeredocFacts | None = None,
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.TEMPLATE, (parts, heredoc))

    @classmethod
    def function_call(
        cls, name: str, name_span: Span, args: tuple[HclCallArg, ...]
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.FUNCTION_CALL, (name, name_span, args))

    @classmethod
    def variable_ref(cls, name: str) -> HclExpressionKind:
        return cls(HclExpressionKindName.VARIABLE_REF, name)

    @classmethod
    def traversal(
        cls, root: HclTraversalRoot, steps: tuple[HclTraversalStep, ...]
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.TRAVERSAL, (root, steps))

    @classmethod
    def unary(cls, op: UnaryOp, operand: HclExpression) -> HclExpressionKind:
        return cls(HclExpressionKindName.UNARY, (op, operand))

    @classmethod
    def binary(
        cls, op: BinaryOp, lhs: HclExpression, rhs: HclExpression
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.BINARY, (op, lhs, rhs))

    @classmethod
    def conditional(
        cls, condition: HclExpression, then: HclExpression, else_: HclExpression
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.CONDITIONAL, (condition, then, else_))

    @classmethod
    def for_tuple(
        cls,
        intro: HclForIntro,
        value: HclExpression,
        condition: HclExpression | None,
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.FOR_TUPLE, (intro, value, condition))

    @classmethod
    def for_object(
        cls,
        intro: HclForIntro,
        key: HclExpression,
        value: HclExpression,
        grouping: bool,
        condition: HclExpression | None,
    ) -> HclExpressionKind:
        return cls(HclExpressionKindName.FOR_OBJECT, (intro, key, value, grouping, condition))

    @classmethod
    def tuple(cls, elements: tuple[HclExpression, ...]) -> HclExpressionKind:
        return cls(HclExpressionKindName.TUPLE, elements)

    @classmethod
    def object(cls, entries: tuple[HclObjectEntry, ...]) -> HclExpressionKind:
        return cls(HclExpressionKindName.OBJECT, entries)

    @classmethod
    def paren(cls, inner: HclExpression) -> HclExpressionKind:
        return cls(HclExpressionKindName.PARENTHESIZED, inner)

    def as_str(self) -> str:
        """Stable kind spelling (expression.rs:314-342)."""
        return self.name.as_str()

    def kind_family(self) -> str:
        """Kind-family spelling of the `hcl.expression@1` record (RFC 0014
        §8.2; projection.rs:1004-1020)."""
        return self.name.kind_family()


@dataclass(frozen=True, slots=True)
class HclExpression:
    """Half-open raw-byte range of one expression AST node; the exact
    source text is always derived from the span against the frozen source
    (RFC 0014 §6 double preservation; expression.rs:40-176).

    Snapshot-bound query handles derive their ordinal from the document's
    deterministic pre-order tree walk (projection.rs:124-130); spans and
    identities are never part of structural equality.
    """

    kind: HclExpressionKind
    span: Span

    def __eq__(self, other: object) -> bool:
        """Structural equality: recursive over kind and children; node
        identity and source spans are never part of value equality (RFC
        0014 §6; expression.rs:178-190)."""
        if not isinstance(other, HclExpression):
            return NotImplemented
        return self.kind == other.kind

    def __hash__(self) -> int:
        return hash(self.kind)

    def text(self, source) -> str:
        """Exact source text derived from the span against one source
        snapshot (expression.rs:73-78). Spans are half-open raw-byte
        ranges; the UTF-8-only source contract makes the byte slice the
        exact original spelling."""
        raw = source.bytes()
        return raw[self.span.start_byte : self.span.end_byte].decode("utf-8")

    def children(self) -> list[HclExpression]:
        """Ordered direct child expressions in source order
        (expression.rs:88-175)."""
        children: list[HclExpression] = []
        name = self.kind.name
        payload = self.kind.payload
        if name is HclExpressionKindName.NUMBER:
            pass
        elif name is HclExpressionKindName.BOOLEAN:
            pass
        elif name is HclExpressionKindName.NULL:
            pass
        elif name is HclExpressionKindName.TEMPLATE:
            parts, _ = payload
            _collect_template_part_children(parts, children)
        elif name is HclExpressionKindName.FUNCTION_CALL:
            _, _, args = payload
            children.extend(argument.expression for argument in args)
        elif name is HclExpressionKindName.VARIABLE_REF:
            pass
        elif name is HclExpressionKindName.TRAVERSAL:
            _, steps = payload
            for step in steps:
                if step.kind == "index" and step.key is not None:
                    children.append(step.key)
                elif step.kind in ("attr-splat", "full-splat"):
                    for inner in step.steps:
                        if inner.kind == "index" and inner.key is not None:
                            children.append(inner.key)
        elif name is HclExpressionKindName.UNARY:
            _, operand = payload
            children.append(operand)
        elif name is HclExpressionKindName.BINARY:
            _, lhs, rhs = payload
            children.append(lhs)
            children.append(rhs)
        elif name is HclExpressionKindName.CONDITIONAL:
            condition, then, else_ = payload
            children.extend((condition, then, else_))
        elif name is HclExpressionKindName.FOR_TUPLE:
            intro, value, condition = payload
            children.append(intro.collection)
            children.append(value)
            if condition is not None:
                children.append(condition)
        elif name is HclExpressionKindName.FOR_OBJECT:
            intro, key, value, _, condition = payload
            children.append(intro.collection)
            children.append(key)
            children.append(value)
            if condition is not None:
                children.append(condition)
        elif name is HclExpressionKindName.TUPLE:
            children.extend(payload)
        elif name is HclExpressionKindName.OBJECT:
            for entry in payload:
                key = entry.key
                if key.kind == "paren" and key.inner is not None:
                    children.append(key.inner)
                elif key.kind == "template" and key.template is not None:
                    _collect_template_part_children(key.template.parts, children)
                children.append(entry.value)
        elif name is HclExpressionKindName.PARENTHESIZED:
            children.append(payload)
        return children


def _collect_template_part_children(
    parts: tuple[HclTemplatePart, ...], children: list[HclExpression]
) -> None:
    for part in parts:
        if part.kind == "interpolation" and part.expression is not None:
            children.append(part.expression)
        elif part.kind == "directive" and part.directive is not None:
            directive = part.directive
            if directive.kind == "if" and directive.condition is not None:
                children.append(directive.condition)
            elif directive.kind == "for" and directive.intro is not None:
                children.append(directive.intro.collection)


# ---------------------------------------------------------------------------
# Literal-complete boundary and typed literal projection (RFC 0014 §8.1)
# ---------------------------------------------------------------------------


def is_literal_complete(expression: HclExpression) -> bool:
    """Whether an expression is literal-complete: its value is uniquely
    determined by the source text alone — no evaluation, no context (RFC
    0014 §8.1; expression.rs:1506-1558).

    Exactly the following are literal-complete: a number literal; `true`,
    `false`, or `null`; a quoted or heredoc template containing zero
    interpolation and zero directive sequences; a tuple constructor whose
    elements are all literal-complete; an object constructor whose keys
    are identifiers, number literals, quoted literal templates, or
    parenthesized literal-complete expressions, and whose values are all
    literal-complete; a unary minus applied to a number literal; and a
    parenthesized literal-complete expression.
    """
    name = expression.kind.name
    payload = expression.kind.payload
    if name is HclExpressionKindName.NUMBER:
        return True
    if name is HclExpressionKindName.BOOLEAN:
        return True
    if name is HclExpressionKindName.NULL:
        return True
    if name is HclExpressionKindName.TEMPLATE:
        parts, _ = payload
        return all(part.is_literal() for part in parts)
    if name is HclExpressionKindName.TUPLE:
        return all(is_literal_complete(element) for element in payload)
    if name is HclExpressionKindName.OBJECT:
        for entry in payload:
            if not is_literal_complete(entry.value):
                return False
            if not _literal_complete_key(entry.key):
                return False
        return True
    if name is HclExpressionKindName.UNARY:
        op, operand = payload
        return op is UnaryOp.MINUS and operand.kind.name is HclExpressionKindName.NUMBER
    if name is HclExpressionKindName.PARENTHESIZED:
        return is_literal_complete(payload)
    return False


def _literal_complete_key(key: HclObjectKey) -> bool:
    if key.kind in ("identifier", "number"):
        return True
    if key.kind == "template" and key.template is not None:
        return all(part.is_literal() for part in key.template.parts)
    if key.kind == "paren" and key.inner is not None:
        return is_literal_complete(key.inner)
    return False


@dataclass(frozen=True, slots=True)
class HclLiteralKey:
    """One object-literal key (RFC 0014 §8.1-§8.2; expression.rs:1770-1786).

    ``kind`` is "identifier" | "number" | "string" | "value".
    """

    kind: str
    text: str = ""
    value: HclLiteralValue | None = None


@dataclass(frozen=True, slots=True)
class HclLiteralObjectEntry:
    """One ordered object literal entry (expression.rs:1744-1768)."""

    key: HclLiteralKey
    value: HclLiteralValue


@dataclass(frozen=True, slots=True)
class HclLiteralValue:
    """Typed literal projection of a literal-complete expression (RFC 0014
    §8.2; expression.rs:1714-1741).

    Integers and reals carry the exact canonical decimal spelling with an
    optional leading `-`; strings carry exact decoded code points,
    including the `<<-` indentation-stripped heredoc content. ``kind`` is
    "integer" | "real" | "string" | "boolean" | "null" | "tuple" |
    "object".
    """

    kind: str
    text: str = ""
    flag: bool = False
    elements: tuple[HclLiteralValue, ...] = ()
    entries: tuple[HclLiteralObjectEntry, ...] = ()

    @classmethod
    def integer(cls, text: str) -> HclLiteralValue:
        return cls("integer", text=text)

    @classmethod
    def real(cls, text: str) -> HclLiteralValue:
        return cls("real", text=text)

    @classmethod
    def string(cls, text: str) -> HclLiteralValue:
        return cls("string", text=text)

    @classmethod
    def boolean(cls, value: bool) -> HclLiteralValue:
        return cls("boolean", flag=value)

    @classmethod
    def null(cls) -> HclLiteralValue:
        return cls("null")

    @classmethod
    def tuple(cls, elements: tuple[HclLiteralValue, ...]) -> HclLiteralValue:
        return cls("tuple", elements=elements)

    @classmethod
    def object(cls, entries: tuple[HclLiteralObjectEntry, ...]) -> HclLiteralValue:
        return cls("object", entries=entries)


class NonLiteralExpression(Exception):
    """A literal-complete expression must be a typed literal value; this is
    the explicit-failure path of RFC 0014 §8 (expression.rs:1568-1582)."""


def literal_value(expression: HclExpression) -> HclLiteralValue:
    """Extracts the typed literal value of a literal-complete expression
    (RFC 0014 §8.1-§8.2; expression.rs:1584-1676).

    A derived expression raises NonLiteralExpression — never a null, empty,
    or converted result.
    """
    name = expression.kind.name
    payload = expression.kind.payload
    if name is HclExpressionKindName.NUMBER:
        return number_literal(payload.canonical_decimal)
    if name is HclExpressionKindName.BOOLEAN:
        return HclLiteralValue.boolean(payload)
    if name is HclExpressionKindName.NULL:
        return HclLiteralValue.null()
    if name is HclExpressionKindName.TEMPLATE:
        parts, heredoc = payload
        text = ""
        for part in parts:
            if part.kind == "literal":
                text += part.text
            else:
                raise NonLiteralExpression
        if heredoc is not None and heredoc.mode is HeredocMode.STRIP_INDENT:
            text = _strip_heredoc_indentation(text)
        return HclLiteralValue.string(text)
    if name is HclExpressionKindName.TUPLE:
        return HclLiteralValue.tuple(tuple(literal_value(element) for element in payload))
    if name is HclExpressionKindName.OBJECT:
        entries: list[HclLiteralObjectEntry] = []
        for entry in payload:
            key = entry.key
            if key.kind == "identifier":
                literal_key = HclLiteralKey("identifier", text=key.name or "")
            elif key.kind == "number" and key.number is not None:
                literal_key = HclLiteralKey("number", text=key.number.canonical_decimal)
            elif key.kind == "template" and key.template is not None:
                text = ""
                for part in key.template.parts:
                    if part.kind == "literal":
                        text += part.text
                    else:
                        raise NonLiteralExpression
                literal_key = HclLiteralKey("string", text=text)
            elif key.kind == "paren" and key.inner is not None:
                literal_key = HclLiteralKey("value", value=literal_value(key.inner))
            else:
                raise NonLiteralExpression
            entries.append(HclLiteralObjectEntry(literal_key, literal_value(entry.value)))
        return HclLiteralValue.object(tuple(entries))
    if name is HclExpressionKindName.UNARY:
        op, operand = payload
        if op is UnaryOp.MINUS and operand.kind.name is HclExpressionKindName.NUMBER:
            canonical = operand.kind.payload.canonical_decimal
            value = canonical if canonical == "0" else "-" + canonical
            return number_literal(value)
        raise NonLiteralExpression
    if name is HclExpressionKindName.PARENTHESIZED:
        return literal_value(payload)
    raise NonLiteralExpression


def _strip_heredoc_indentation(text: str) -> str:
    """Applies the `<<-` indentation stripping: removes the minimum number
    of leading spaces from each line's leading literal text (RFC 0014 §4.5;
    expression.rs:1686-1712)."""
    minimum: int | None = None
    for line in text.split("\n"):
        if not line:
            continue
        indent = len(line) - len(line.lstrip(" "))
        minimum = indent if minimum is None else min(minimum, indent)
    if minimum is None:
        return ""
    out: list[str] = []
    for index, line in enumerate(text.split("\n")):
        if index > 0:
            out.append("\n")
        out.append(line[minimum : len(line)])
    return "".join(out)


# ---------------------------------------------------------------------------
# Structural fingerprint serialization (RFC 0014 §8.2; materialization.rs:
# 1496-1768)
# ---------------------------------------------------------------------------


def structural_fingerprint(expression: HclExpression) -> int:
    """Structural fingerprint value of one expression: a 64-bit FNV-1a hash
    over the canonical structural serialization (RFC 0014 §8.2;
    materialization.rs:1507-1516).

    The serialization covers the frozen structural equality of RFC 0014 §6
    — kind, ordered children, canonical decimals, exact literal texts,
    operator spellings, heredoc mode and marker — and never source spans or
    identities.
    """
    bytes_ = bytearray()
    _write_expression_structure(expression, bytes_)
    hash_value = _FNV_OFFSET_BASIS
    for byte in bytes_:
        hash_value ^= byte
        hash_value = (hash_value * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return hash_value


def structural_fingerprint_hex(expression: HclExpression) -> str:
    """The hex of the structural fingerprint (materialization.rs:1518-1522)."""
    return f"{structural_fingerprint(expression):016x}"


def _push_text(out: bytearray, text: str) -> None:
    out.extend(len(text.encode("utf-8")).to_bytes(8, "little"))
    out.extend(text.encode("utf-8"))


def _write_expression_structure(expression: HclExpression, out: bytearray) -> None:
    name = expression.kind.name
    payload = expression.kind.payload
    if name is HclExpressionKindName.NUMBER:
        out.append(ord("N"))
        _push_text(out, payload.canonical_decimal)
    elif name is HclExpressionKindName.BOOLEAN:
        out.append(ord("B"))
        out.append(1 if payload else 0)
    elif name is HclExpressionKindName.NULL:
        out.append(ord("Z"))
    elif name is HclExpressionKindName.TEMPLATE:
        parts, heredoc = payload
        out.append(ord("T"))
        if heredoc is not None:
            out.append(ord("H"))
            _push_text(out, heredoc.mode.value)
            _push_text(out, heredoc.marker)
        else:
            out.append(ord("Q"))
        for part in parts:
            _write_part_structure(part, out)
    elif name is HclExpressionKindName.FUNCTION_CALL:
        call_name, _, args = payload
        out.append(ord("F"))
        _push_text(out, call_name)
        for argument in args:
            out.append(ord("X") if argument.expand else ord("x"))
            _write_expression_structure(argument.expression, out)
    elif name is HclExpressionKindName.VARIABLE_REF:
        out.append(ord("V"))
        _push_text(out, payload)
    elif name is HclExpressionKindName.TRAVERSAL:
        root, steps = payload
        out.append(ord("R"))
        if root.kind == "variable":
            out.append(ord("v"))
            _push_text(out, root.name or "")
        elif root.kind == "boolean":
            out.append(ord("b"))
            out.append(1 if root.boolean_value else 0)
        else:
            out.append(ord("n"))
        for step in steps:
            _write_traversal_step(step, out)
    elif name is HclExpressionKindName.UNARY:
        op, operand = payload
        out.append(ord("U"))
        _push_text(out, op.value)
        _write_expression_structure(operand, out)
    elif name is HclExpressionKindName.BINARY:
        op, lhs, rhs = payload
        out.append(ord("W"))
        _push_text(out, op.value)
        _write_expression_structure(lhs, out)
        _write_expression_structure(rhs, out)
    elif name is HclExpressionKindName.CONDITIONAL:
        condition, then, else_ = payload
        out.append(ord("C"))
        _write_expression_structure(condition, out)
        _write_expression_structure(then, out)
        _write_expression_structure(else_, out)
    elif name is HclExpressionKindName.FOR_TUPLE:
        intro, value, condition = payload
        out.append(ord("P"))
        _write_for_intro(intro, out)
        _write_expression_structure(value, out)
        if condition is not None:
            out.append(ord("c"))
            _write_expression_structure(condition, out)
        else:
            out.append(ord("n"))
    elif name is HclExpressionKindName.FOR_OBJECT:
        intro, key, value, grouping, condition = payload
        out.append(ord("O"))
        _write_for_intro(intro, out)
        _write_expression_structure(key, out)
        _write_expression_structure(value, out)
        out.append(ord("g") if grouping else ord("n"))
        if condition is not None:
            out.append(ord("c"))
            _write_expression_structure(condition, out)
        else:
            out.append(ord("n"))
    elif name is HclExpressionKindName.TUPLE:
        out.append(ord("L"))
        for element in payload:
            _write_expression_structure(element, out)
    elif name is HclExpressionKindName.OBJECT:
        out.append(ord("M"))
        for entry in payload:
            _write_object_key_structure(entry.key, out)
            _write_expression_structure(entry.value, out)
    elif name is HclExpressionKindName.PARENTHESIZED:
        out.append(ord("("))
        _write_expression_structure(payload, out)


def _write_part_structure(part: HclTemplatePart, out: bytearray) -> None:
    if part.kind == "literal":
        out.append(ord("L"))
        _push_text(out, part.text)
    elif part.kind == "interpolation":
        out.append(ord("I"))
        _write_expression_structure(part.expression, out)
    else:
        out.append(ord("D"))
        _write_directive_structure(part.directive, out)


def _write_directive_structure(directive: HclDirectiveKind | None, out: bytearray) -> None:
    if directive is None:
        return
    if directive.kind == "if":
        out.append(ord("f"))
        _write_expression_structure(directive.condition, out)
    elif directive.kind == "else":
        out.append(ord("e"))
    elif directive.kind == "endif":
        out.append(ord("E"))
    elif directive.kind == "for":
        out.append(ord("o"))
        _write_for_intro(directive.intro, out)
    else:
        out.append(ord("g"))


def _write_for_intro(intro: HclForIntro | None, out: bytearray) -> None:
    if intro is None:
        return
    if intro.key is not None:
        out.append(ord("k"))
        _push_text(out, intro.key)
    else:
        out.append(ord("n"))
    _push_text(out, intro.value)
    _write_expression_structure(intro.collection, out)


def _write_traversal_step(step: HclTraversalStep, out: bytearray) -> None:
    if step.kind == "get-attr":
        out.append(ord("a"))
        _push_text(out, step.name or "")
    elif step.kind == "index":
        out.append(ord("i"))
        _write_expression_structure(step.key, out)
    elif step.kind == "attr-splat":
        out.append(ord("s"))
        for inner in step.steps:
            _write_traversal_step(inner, out)
    else:
        out.append(ord("S"))
        for inner in step.steps:
            _write_traversal_step(inner, out)


def _write_object_key_structure(key: HclObjectKey, out: bytearray) -> None:
    if key.kind == "identifier":
        out.append(ord("K"))
        _push_text(out, key.name or "")
    elif key.kind == "number" and key.number is not None:
        out.append(ord("k"))
        _push_text(out, key.number.canonical_decimal)
    elif key.kind == "template" and key.template is not None:
        out.append(ord("t"))
        for part in key.template.parts:
            if part.kind == "literal":
                out.append(ord("l"))
                _push_text(out, part.text)
            elif part.kind == "interpolation":
                out.append(ord("i"))
                _write_expression_structure(part.expression, out)
            else:
                out.append(ord("d"))
                _write_directive_structure(part.directive, out)
    elif key.kind == "paren" and key.inner is not None:
        out.append(ord("p"))
        _write_expression_structure(key.inner, out)
