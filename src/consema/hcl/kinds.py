"""The frozen HCL profile, syntax-kind, and expression-kind vocabularies.

Frozen names/numbers with authority citations (language-neutral first;
Rust only for registry/byte arbitration):

- ``HclProfile``: the two profile identities — crates/consema-hcl/src/
  lib.rs:100-118 (enum and id()); the profile ids hcl.native@1 /
  hcl.tfvars@1 are the frozen language-neutral spellings (RFC 0014 §1,
  docs/rfcs/0014-hcl-family-profiles-v1.md:25-30).
- ``HclSyntaxKind``: the closed 30-kind lossless classification with the
  exact stable names ("Whitespace", "LineBreak", "LineComment",
  "InlineComment", "Identifier", "Equals", "Number", "StringOpen",
  "StringContent", "StringClose", "InterpolationOpen",
  "InterpolationContent", "InterpolationClose", "DirectiveOpen",
  "DirectiveContent", "DirectiveClose", "HeredocOpen", "HeredocContent",
  "HeredocClose", "BraceOpen", "BraceClose", "BracketOpen",
  "BracketClose", "ParenOpen", "ParenClose", "Comma", "Colon",
  "QuestionMark", "Operator", "ErrorRegion") — RFC 0014 §7.2
  (docs/rfcs/0014-...:490-501) and crates/consema-hcl/src/native.rs:327-475
  (enum, as_str, from_name). There is no ``Bom`` kind (RFC 0014 §7.2).
- ``HclExpressionKindName``: the closed payload-free expression kind set of
  RFC 0014 §7.1 `hcl.expression-kind-is@1` — crates/consema-hcl/src/
  expression.rs:561-642 (enum + as_str/from_name); spellings "number",
  "boolean", "null", "template", "function-call", "variable-ref",
  "traversal", "unary", "binary", "conditional", "for-tuple",
  "for-object", "tuple", "object", "parenthesized".
- The kind-family spellings of the `hcl.expression@1` record (RFC 0014
  §8.2) — crates/consema-hcl/src/projection.rs:996-1040: variable and
  traversal are one "variable" family, for-tuple and for-object are one
  "for" family.
- Identifier character rules — RFC 0014 §4.1 (UAX #31:
  ``Identifier = ID_Start (ID_Continue | "-")*``, underscore excluded at
  the start, §12 D-4); the Rust lexer's tables are
  crates/consema-hcl/src/lexer.rs:2098-2108.

Unicode note (blind-write disclosure): RFC 0014 §4.1 pins Unicode ID_Start
and ID_Continue via UAX #31. This implementation classifies via the host
``str.isidentifier`` semantics (CPython 3.12), matching the JSON5
precedent of consema.json.kinds (kinds.py:29-37): the identifier matrix
vectors (ASCII, Unicode letters, digits, hyphen, leading-digit and
leading-underscore rejection, keyword spellings) all fall in the stable
set. A differential run against the pinned unicode-ident tables is a
verification item, not a claim.
"""

from __future__ import annotations

import enum

from consema.document.ids import ProfileId


class HclProfile(enum.Enum):
    """Frozen HCL language profile (crates/consema-hcl/src/lib.rs:100-107).

    The two profiles share one grammar and one native semantic model;
    ``hcl.tfvars@1`` is ``hcl.native@1`` under the top-level
    attributes-only restriction (RFC 0014 §1, §5).
    """

    NATIVE_V1 = "hcl.native"
    TFVARS_V1 = "hcl.tfvars"

    # -- profile identity (lib.rs:109-118) ----------------------------------

    def id(self) -> ProfileId:
        """Immutable profile identifier (lib.rs:110-117)."""
        return ProfileId.new(self.value, 1)


class HclSyntaxKind(enum.Enum):
    """Closed 30-kind HCL lossless syntax-piece classification (RFC 0014
    §7.2; crates/consema-hcl/src/native.rs:327-398).

    Every non-empty raw byte of a formed document belongs to exactly one
    ordered structural piece with one of these kinds; there is no ``Bom``
    kind because a BOM is excluded at formation (RFC 0014 §2).
    ``HeredocOpen`` covers the ``<<``/``<<-`` introducer and the marker
    identifier; ``HeredocClose`` covers the closing marker line.
    """

    WHITESPACE = "Whitespace"
    LINE_BREAK = "LineBreak"
    LINE_COMMENT = "LineComment"
    INLINE_COMMENT = "InlineComment"
    IDENTIFIER = "Identifier"
    EQUALS = "Equals"
    NUMBER = "Number"
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
    BRACE_OPEN = "BraceOpen"
    BRACE_CLOSE = "BraceClose"
    BRACKET_OPEN = "BracketOpen"
    BRACKET_CLOSE = "BracketClose"
    PAREN_OPEN = "ParenOpen"
    PAREN_CLOSE = "ParenClose"
    COMMA = "Comma"
    COLON = "Colon"
    QUESTION_MARK = "QuestionMark"
    OPERATOR = "Operator"
    ERROR_REGION = "ErrorRegion"

    def as_str(self) -> str:
        """Stable query and protocol name (native.rs:400-436)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> HclSyntaxKind | None:
        """Resolves one exact stable kind name (native.rs:438-474)."""
        try:
            return cls(name)
        except ValueError:
            return None


class HclExpressionKindName(enum.Enum):
    """Closed payload-free expression kind name set (RFC 0014 §7.1
    `hcl.expression-kind-is@1`; expression.rs:561-595)."""

    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"
    TEMPLATE = "template"
    FUNCTION_CALL = "function-call"
    VARIABLE_REF = "variable-ref"
    TRAVERSAL = "traversal"
    UNARY = "unary"
    BINARY = "binary"
    CONDITIONAL = "conditional"
    FOR_TUPLE = "for-tuple"
    FOR_OBJECT = "for-object"
    TUPLE = "tuple"
    OBJECT = "object"
    PARENTHESIZED = "parenthesized"

    def as_str(self) -> str:
        """Stable kind spelling (expression.rs:597-618)."""
        return self.value

    @classmethod
    def from_name(cls, name: str) -> HclExpressionKindName | None:
        """Resolves one stable kind spelling (expression.rs:620-641)."""
        try:
            return cls(name)
        except ValueError:
            return None

    def kind_family(self) -> str:
        """Kind-family spelling of the `hcl.expression@1` record (RFC 0014
        §8.2; projection.rs:996-1020).

        Variable and traversal expressions are one family; for-expressions
        are one family over the tuple and object forms.
        """
        if self is HclExpressionKindName.VARIABLE_REF:
            return "variable"
        if self is HclExpressionKindName.TRAVERSAL:
            return "variable"
        if self is HclExpressionKindName.FOR_TUPLE:
            return "for"
        if self is HclExpressionKindName.FOR_OBJECT:
            return "for"
        return self.value


# -- UAX #31 identifier character classes (RFC 0014 §4.1, §12 D-4;
#    lexer.rs:2098-2108) -----------------------------------------------------


def is_identifier_start(character: str) -> bool:
    """ID_Start with the underscore excluded at the start (RFC 0014 §4.1,
    §12 D-4; lexer.rs:2100-2102)."""
    return character != "_" and character.isidentifier()


def is_identifier_continue(character: str) -> bool:
    """ID_Continue; the hyphen continuation is a scan-loop fact (lexer.rs:
    2104-2108)."""
    return ("a" + character).isidentifier()
