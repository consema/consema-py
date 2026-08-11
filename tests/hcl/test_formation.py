"""HCL formation: golden transcriptions, the no-evaluation contract, and
the expression syntax-only contract.

Golden cases transcribed verbatim from conformance/vectors/hcl-v1.json
(suite "consema.hcl.conformance@1"); each test cites the vector case id.
Assertions check the language-neutral facts the vectors pin (formation
status, exact render round-trip, diagnostics, canonical decimals, fatal
limits). Cases covered:

- hcl-v1.json: hcl.native-formation.body-basic (lines 9-20),
  hcl.native-formation.comments (22-32), hcl.native-formation.heredoc
  (58-68), hcl.native-formation.number-matrix (82-169),
  hcl.native-formation.identifiers-keywords (171-224),
  hcl.native-formation.source-contract (387-430),
  hcl.native-formation.recovery-matrix (432-492),
  hcl.native-formation.empty-body-eof-termination (1649-1682),
  hcl.native-formation.for-key-ambiguity (1751-1779),
  hcl.native-formation.invalid-escapes (1709-1737),
  hcl.tfvars-formation.attributes-only (506-516),
  hcl.tfvars-formation.block-rejected (518-528),
  hcl.tfvars-formation.expression-grammar-full (530-540),
  hcl.tfvars-formation.duplicate-attribute (542-552),
  hcl.limit.expression-depth (1781-1793), hcl.limit.body-nesting
  (1811-1824), hcl.limit.number-digits (1826-1839),
  hcl.limit.arithmetic-overflow (1841-1851), hcl.limit.attribute-count
  (1853-1866), hcl.limit.block-count (1868-1881),
  hcl.limit.body-item-count (1883-1896), hcl.limit.label-count
  (1898-1911), hcl.limit.template-size (1913-1926),
  hcl.limit.heredoc-size (1928-1941), hcl.limit.tuple-elements
  (1943-1956), hcl.limit.object-entries (1958-1971).

Hard gate 1 (RFC 0014 §1, §13; SECURITY.md:36): HCL is never evaluated.
The no-evaluation and expression-syntax-only tests pin that an expression
is an AST fact with exact source text — no variable binding, function
table, template expansion, or iteration exists anywhere in formation.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from consema.hcl import (
    HclExpressionKindName,
    HclFormationFailure,
    HclProfile,
    HclSyntaxKind,
    parse,
)
from consema.hcl.limits import HclParseLimits
from tests.hcl.conftest import diagnostic_codes


def limited(**kwargs) -> HclParseLimits:
    return replace(HclParseLimits(), **kwargs)


def fatal_code(source: bytes, limits: HclParseLimits) -> str | None:
    try:
        parse(source, HclProfile.NATIVE_V1, limits=limits)
        return None
    except HclFormationFailure as error:
        return error.code


# ---------------------------------------------------------------------------
# Golden transcriptions
# ---------------------------------------------------------------------------


def test_body_basic_forms_complete_and_renders_exactly():
    # Case hcl.native-formation.body-basic (hcl-v1.json:9-20).
    source = (
        "region = \"us-east-1\"\n\n"
        "server \"web\" \"1\" {\n  port = 8080\n}\n\n"
        "plain {\n  x = 1\n}\n\n"
        "oneline { y = 2 }\n\n"
        "shared = 1\nshared \"b\" {\n  z = 3\n}"
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    items = document.body.items
    assert len(items) == 6
    assert items[1].as_block() is not None
    block = items[1].as_block()
    assert block.block_type == "server"
    assert tuple(label.text for label in block.labels) == ("web", "1")
    assert block.body.items[0].as_attribute().name == "port"


def test_comments_are_complete_and_byte_exact():
    # Case hcl.native-formation.comments (hcl-v1.json:22-32).
    source = (
        "# leading hash\na = 1 // trailing slash\n"
        "b = 2 /* inline */\nc = 3 /* spans\nlines */\n"
        "d = 4 # comment terminates the attribute\n"
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    names = [kind.value for kind in document.lossless_syntax_kinds()]
    assert "LineComment" in names
    assert "InlineComment" in names


def test_heredoc_matrix_is_complete_and_byte_exact():
    # Case hcl.native-formation.heredoc (hcl-v1.json:58-68).
    source = (
        "plain = <<EOT\nalpha\nbeta\nEOT\n"
        "indented = <<-EOT\n    one\n      two\n    EOT\n"
        "notclosing = <<EOT\nEOT has content\nEOT\n"
        "trimmed = <<EOT\ntail\nEOT  \n"
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    heredoc = document.body.items[3].as_attribute().expression
    assert heredoc.kind.name is HclExpressionKindName.TEMPLATE
    parts, facts = heredoc.kind.payload
    assert facts.closing_span.end_byte == len(source.encode("utf-8")) - 1


def test_number_matrix_statuses_diagnostics_and_canonical_values():
    # Case hcl.native-formation.number-matrix (hcl-v1.json:82-169).
    samples = [
        ("a = 0\n", "Complete", None, "0"),
        ("a = 42\n", "Complete", None, "42"),
        ("a = 1.50\n", "Complete", None, "1.5"),
        ("a = 1e3\n", "Complete", None, "1000"),
        ("a = 15e-1\n", "Complete", None, "1.5"),
        ("a = 1E+2\n", "Complete", None, "100"),
        ("a = 0.5\n", "Complete", None, "0.5"),
        ("a = 1e\n", "Recovered", "hcl.parse.invalid-number@1", None),
        ("a = 1.\n", "Recovered", "hcl.parse.newline@1", None),
        ("a = 1.e3\n", "Recovered", "hcl.parse.newline@1", None),
        ("a = 0x1F\n", "Recovered", "hcl.parse.invalid-number@1", None),
        ("a = 1_000\n", "Recovered", "hcl.parse.invalid-number@1", None),
    ]
    for source, status, code, canonical in samples:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == status, source
        if code is not None:
            assert code in diagnostic_codes(document), source
        if canonical is not None:
            expression = document.body.items[0].as_attribute().expression
            assert expression.kind.payload.canonical_decimal == canonical, source


def test_identifier_keyword_matrix():
    # Case hcl.native-formation.identifiers-keywords (hcl-v1.json:171-224).
    samples = [
        ("foo-bar = 1\n", "Complete"),
        ("变量 = 2\n", "Complete"),
        ("true = 1\n", "Complete"),
        ("false = 2\n", "Complete"),
        ("null = 3\n", "Complete"),
        ("true { x = 1 }\n", "Complete"),
        ("_foo = 1\n", "Recovered"),
        ("a = _bar\n", "Recovered"),
    ]
    for source, status in samples:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == status, source
        if status == "Recovered":
            assert "hcl.parse.identifier@1" in diagnostic_codes(document)


def test_source_contract_bom_lone_cr_invalid_utf8():
    # Case hcl.native-formation.source-contract (hcl-v1.json:387-430).
    bom = b"\xef\xbb\xbfa = 1\n"
    document = parse(bom, HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.byte-order-mark@1" in diagnostic_codes(document)

    document = parse(b"a = 1\n\xef\xbb\xbfb = 2\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.byte-order-mark@1" in diagnostic_codes(document)

    document = parse(b"a = 1\rb = 2\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.lone-cr@1" in diagnostic_codes(document)

    document = parse(b"a = 1\r\nb = 2\r\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"

    with pytest.raises(HclFormationFailure) as raised:
        parse(b"a = 1\n\xff", HclProfile.NATIVE_V1)
    assert raised.value.code == "hcl.parse.invalid-utf8@1"


def test_recovery_matrix_boundaries():
    # Case hcl.native-formation.recovery-matrix (hcl-v1.json:432-492).
    samples = [
        ("a = \"abc\n", "hcl.parse.unterminated-string@1"),
        ("a = <<EOT\ncontent\n", "hcl.parse.unterminated-heredoc@1"),
        ("a = \"${ 1 +\"\n", "hcl.parse.unterminated-interpolation@1"),
        ("a = [1, 2\n", "hcl.parse.expression@1"),
        ("a = 1 @ 2\nb = 3\n", "hcl.parse.invalid-character@1"),
        ("a = 1 /* one /* two */ still\n", "hcl.parse.newline@1"),
        ("a = <<\"EOT\"\ncontent\nEOT\n", "hcl.parse.expression@1"),
    ]
    for source, code in samples:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == "Recovered", source
        assert code in diagnostic_codes(document), source


def test_recovery_keeps_proven_attributes():
    # Case hcl.native-formation.recovery-matrix sample 5
    # (hcl-v1.json:450-454, 480-485): "a" and "b" stay proven attributes.
    document = parse(b"a = 1 @ 2\nb = 3\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    names = [item.as_attribute().name for item in document.body.items]
    assert names == ["a", "b"]


def test_empty_body_and_eof_termination():
    # Case hcl.native-formation.empty-body-eof-termination
    # (hcl-v1.json:1649-1682).
    for source in ["", "a = 1", "b {\n}\n", "oneline { y = 2 }"]:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == "Complete", repr(source)
        assert document.render().decode("utf-8") == source


def test_for_key_ambiguity():
    # Case hcl.native-formation.for-key-ambiguity (hcl-v1.json:1751-1779).
    document = parse(b"a = { for = 1 }\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.expression@1" in diagnostic_codes(document)
    document = parse(b"a = { (for) = 1 }\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    document = parse(b"a = { \"for\" = 1 }\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"


def test_invalid_escapes_are_recovered():
    # Case hcl.native-formation.invalid-escapes (hcl-v1.json:1709-1737).
    for source in ["a = \"bad \\q\"\n", "a = \"\\u12\"\n", "a = \"\\U00110000\"\n"]:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == "Recovered", source
        assert "hcl.parse.invalid-escape@1" in diagnostic_codes(document)


def test_tfvars_attributes_only_is_complete():
    # Case hcl.tfvars-formation.attributes-only (hcl-v1.json:506-516).
    source = (
        "region = \"us-east-1\"\ncount = 3\nratio = 0.5\nenabled = true\n"
        "tags = [\"a\", \"b\"]\nlabels = {\n  env = \"prod\"\n}\n"
    )
    document = parse(source.encode("utf-8"), HclProfile.TFVARS_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    assert all(item.as_attribute() is not None for item in document.body.items)


def test_tfvars_block_rejected_with_profile_code():
    # Case hcl.tfvars-formation.block-rejected (hcl-v1.json:518-528).
    source = "region = \"us-east-1\"\nblock \"x\" {\n  a = 1\n}\n"
    document = parse(source.encode("utf-8"), HclProfile.TFVARS_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.tfvars.block-not-allowed@1" in diagnostic_codes(document)
    # The rejected block stays a native item of the Recovered document
    # (RFC 0014 §3, §7).
    assert document.body.items[1].as_block() is not None
    # The gate emits diagnostics, never error regions.
    assert document.error_regions == ()


def test_tfvars_accepts_the_full_expression_grammar():
    # Case hcl.tfvars-formation.expression-grammar-full (hcl-v1.json:530-540).
    # Terraform's static-only evaluation rule is application-layer policy,
    # never replicated at formation (RFC 0014 §5, hard gate 3).
    source = "computed = max(1, 2)\nref = var.other\njoined = \"prefix-${var.suffix}\"\n"
    document = parse(source.encode("utf-8"), HclProfile.TFVARS_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source


def test_tfvars_duplicate_attribute():
    # Case hcl.tfvars-formation.duplicate-attribute (hcl-v1.json:542-552).
    document = parse(b"a = 1\na = 2\n", HclProfile.TFVARS_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.duplicate-attribute@1" in diagnostic_codes(document)
    # The duplicate never enters the native model (RFC 0014 §3).
    attributes = [item.as_attribute() for item in document.body.items]
    assert [attribute.name for attribute in attributes if attribute is not None] == ["a"]


def test_unary_compound_matrix():
    # Case hcl.native-formation.unary-compound (hcl-v1.json:226-279).
    samples = [
        ("a = -1 + 2\n", "Complete"),
        ("a = 2 * -1\n", "Complete"),
        ("a = -1 * 2\n", "Complete"),
        ("a = !!x\n", "Complete"),
        ("a = !true\n", "Complete"),
        ("a = - (1 + 2)\n", "Complete"),
        ("a = -x\n", "Complete"),
        ("a = +1\n", "Recovered"),
    ]
    for source, status in samples:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == status, source
        if status == "Recovered":
            assert "hcl.parse.expression@1" in diagnostic_codes(document)


def test_operators_precedence_matrix():
    # Case hcl.native-formation.operators-precedence (hcl-v1.json:281-349).
    samples = [
        ("a = 1 + 2 * 3\n", "Complete"),
        ("a = (1 + 2) * 3\n", "Complete"),
        ("a = 2 > 1 && 3 <= 3\n", "Complete"),
        ('a = x ? y : z\n', "Complete"),
        ("a = -a == b\n", "Complete"),
        ("a = (\n  1 +\n  2\n)\n", "Complete"),
        ("a = myfunc(1, 2,)\n", "Complete"),
        ("a = merge(m1, m2...)\n", "Complete"),
        ("a = 2 ** 3\n", "Recovered"),
        ("a = foo.0\n", "Recovered"),
        ("a = foo::bar()\n", "Recovered"),
    ]
    for source, status in samples:
        document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
        assert document.formation_status().value == status, source
    # The namespaced call form is an invalid character (RFC 0014 §12 D-6).
    document = parse(b"a = foo::bar()\n", HclProfile.NATIVE_V1)
    assert "hcl.parse.invalid-character@1" in diagnostic_codes(document)
    # The numeric attribute access is a grammar error (RFC 0014 §12 D-5).
    document = parse(b"a = foo.0\n", HclProfile.NATIVE_V1)
    assert "hcl.parse.expression@1" in diagnostic_codes(document)


def test_constructors_and_for_expressions():
    # Cases hcl.native-formation.constructors (hcl-v1.json:351-361) and
    # hcl.native-formation.for-expressions (hcl-v1.json:363-373).
    source = (
        "nlsep = [\n  1,\n  2,\n]\nobj = {\n  a = 1\n  b = 2\n}\n"
        "dups = { a = 1, a = 2 }\nnumkey = { 1 = \"one\" }\n"
        'colon = { "k" : 3 }\nforkey = { "for" = 1 }\nparenkey = { (x) = 2 }\n'
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    source = (
        "ftuple = [for x in list : x * 2]\n"
        "fobj = {for k, v in map : k => v if v != null}\n"
        "fgroup = {for k, v in map : k => v...}\n"
        "fcond = [for x in list : x if x > 0]\n"
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    fobj = document.body.items[1].as_attribute().expression
    assert fobj.kind.name is HclExpressionKindName.FOR_OBJECT
    fgroup = document.body.items[2].as_attribute().expression
    _, _, _, grouping, _ = fgroup.kind.payload
    assert grouping is True


def test_directive_strip_markers():
    # Case hcl.native-formation.directive-strip-markers (hcl-v1.json:1739-
    # 1749): the `~` strip markers are source facts, never applied.
    source = 'a = "%{~ if x ~}yes%{ endif }"\nb = "%{ for k, v in m ~}${k}%{ endfor }"\n'
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    parts, _ = document.body.items[0].as_attribute().expression.kind.payload
    assert [part.kind for part in parts] == ["directive", "literal", "directive"]
    assert parts[0].directive.kind == "if"
    assert parts[2].directive.kind == "endif"


def test_leading_digit_rejection():
    # Case hcl.native-formation.leading-digit-rejection (hcl-v1.json:1684-
    # 1707).
    document = parse(b"1abc = 1\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.invalid-number@1" in diagnostic_codes(document)
    document = parse(b"a = 1abc\n", HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Recovered"
    assert "hcl.parse.expression@1" in diagnostic_codes(document)


def test_native_production_shape():
    # Case hcl.native-formation.production-shape (hcl-v1.json:494-504).
    source = (
        'terraform {\n  required_version = ">= 1.5"\n}\n\n'
        'variable "region" {\n  type    = string\n  default = "us-east-1"\n}\n\n'
        "locals {\n  common_tags = {\n    Env = \"prod\"\n  }\n}\n\n"
        'resource "aws_instance" "web" {\n  ami           = "ami-0abcdef1234567890"\n'
        '  instance_type = "t3.micro"\n  count         = 2\n'
        "  tags          = local.common_tags\n}\n\n"
        'module "vpc" {\n  source  = "./modules/vpc"\n'
        '  cidr    = "10.0.0.0/16"\n  enabled = true\n}\n'
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source
    assert len(document.body.items) == 5


def test_tfvars_production_shape():
    # Case hcl.tfvars-formation.production-shape (hcl-v1.json:554-564).
    source = (
        "# Production-shaped terraform.tfvars fixture\n"
        'region = "us-east-1"\ninstance_type = "t3.micro"\n'
        'ami = "ami-0abcdef1234567890"\ncount = 2\nmonitoring = true\n'
        "tags = {\n  Name = \"web-server\"\n  Env  = \"prod\"\n}\n"
        "security_groups = [\n  \"sg-0123456789abcdef0\",\n  \"sg-1123456789abcdef0\",\n]\n"
        'launch_template = {\n  id      = "lt-0123456789abcdef0"\n  version = 1\n}\n'
    )
    document = parse(source.encode("utf-8"), HclProfile.TFVARS_V1)
    assert document.formation_status().value == "Complete"
    assert document.render().decode("utf-8") == source


# ---------------------------------------------------------------------------
# Hard gate 1: HCL is never evaluated
# ---------------------------------------------------------------------------


def test_no_evaluation_binary_is_syntax_only():
    # RFC 0014 §1, §13 hard gate 1 (SECURITY.md:36): no arithmetic is ever
    # computed. `bin = 1 + 2 * 3` is a Binary AST fact, never the value 7.
    document = parse(b"bin = 1 + 2 * 3\n", HclProfile.NATIVE_V1)
    expression = document.body.items[0].as_attribute().expression
    assert expression.kind.name is HclExpressionKindName.BINARY
    op, lhs, rhs = expression.kind.payload
    assert op.value == "+"
    assert lhs.kind.name is HclExpressionKindName.NUMBER
    assert rhs.kind.name is HclExpressionKindName.BINARY
    # The exact source text is derived from the span (RFC 0014 §6 double
    # preservation).
    assert expression.text(document.source) == "1 + 2 * 3"
    # No evaluated value exists anywhere on the node.
    assert not hasattr(expression, "value")


def test_no_evaluation_interpolation_is_a_template_part():
    # `interp = "value: ${name}"` is a Template with an Interpolation part
    # holding the variable expression — never a resolved string.
    document = parse(b'interp = "value: ${name}"\n', HclProfile.NATIVE_V1)
    expression = document.body.items[0].as_attribute().expression
    assert expression.kind.name is HclExpressionKindName.TEMPLATE
    parts, heredoc = expression.kind.payload
    assert heredoc is None
    assert [part.kind for part in parts] == ["literal", "interpolation"]
    interpolation = parts[1]
    assert interpolation.expression.kind.name is HclExpressionKindName.VARIABLE_REF
    assert interpolation.expression.kind.payload == "name"
    assert expression.text(document.source) == '"value: ${name}"'


def test_no_evaluation_directive_and_for_are_syntax_facts():
    # Directives and for-expressions are native facts; no iteration or
    # template expansion is ever performed.
    document = parse(
        b'f = "if: %{ if x }yes%{ endif }"\ng = "%{ for k, v in m }${k}%{ endfor }"\n',
        HclProfile.NATIVE_V1,
    )
    directive = document.body.items[0].as_attribute().expression
    parts, _ = directive.kind.payload
    assert parts[1].kind == "directive"
    assert parts[1].directive.kind == "if"
    for_expression = document.body.items[1].as_attribute().expression
    assert for_expression.kind.name is HclExpressionKindName.TEMPLATE
    parts, _ = for_expression.kind.payload
    assert [part.kind for part in parts] == ["directive", "interpolation", "directive"]
    for_directive = parts[0].directive
    assert for_directive.kind == "for"
    assert for_directive.intro.key == "k"
    assert for_directive.intro.value == "v"


# ---------------------------------------------------------------------------
# Expression syntax-only contract
# ---------------------------------------------------------------------------


def test_expression_kinds_are_syntax_facts():
    # The expression-matrix sample of hcl-v1.json:47-50: every expression
    # parses to its frozen kind with exact source text, never a value.
    source = (
        "int = 42\nreal = 1.5\nexp = 1e3\nneg = -7\nyes = true\nno = false\n"
        "nil = null\nstr = \"hello\"\ncall = max(1, 2, 3)\nv = my_var\n"
        "bin = 1 + 2 * 3\ncmp = a == b\nlogic = a && b || !c\n"
        "cond = x ? \"yes\" : \"no\"\ntup = [1, \"two\", true]\n"
        "obj = {key = 1, \"quoted\" = 2}\nparen = (1 + 2) * 3\n"
    )
    document = parse(source.encode("utf-8"), HclProfile.NATIVE_V1)
    assert document.formation_status().value == "Complete"
    expected = [
        ("int", "number", "42"),
        ("real", "number", "1.5"),
        ("exp", "number", "1e3"),
        ("neg", "unary", "-7"),
        ("yes", "boolean", "true"),
        ("no", "boolean", "false"),
        ("nil", "null", "null"),
        ("str", "template", '"hello"'),
        ("call", "function-call", "max(1, 2, 3)"),
        ("v", "variable-ref", "my_var"),
        ("bin", "binary", "1 + 2 * 3"),
        ("cmp", "binary", "a == b"),
        ("logic", "binary", "a && b || !c"),
        ("cond", "conditional", 'x ? "yes" : "no"'),
        ("tup", "tuple", '[1, "two", true]'),
        ("obj", "object", '{key = 1, "quoted" = 2}'),
        ("paren", "binary", "(1 + 2) * 3"),
    ]
    for item, (name, kind, text) in zip(document.body.items, expected):
        assert item.as_attribute().name == name
        expression = item.as_attribute().expression
        assert expression.kind.as_str() == kind, name
        # The exact source text is always derived from the span (RFC 0014
        # §6 double preservation).
        assert expression.text(document.source) == text, name


def test_function_call_arguments_and_expansion_are_syntax_facts():
    # hcl-v1.json:53-54 (`call = max(1, 2, 3)`): the call is a FunctionCall
    # with ordered arguments; nothing is invoked.
    document = parse(b"call = max(1, 2, 3)\n", HclProfile.NATIVE_V1)
    expression = document.body.items[0].as_attribute().expression
    assert expression.kind.name is HclExpressionKindName.FUNCTION_CALL
    name, name_span, args = expression.kind.payload
    assert name == "max"
    assert [argument.expand for argument in args] == [False, False, False]
    assert len(args) == 3
    # merge(m1, m2...) keeps the expansion marker fact (hcl-v1.json:308).
    document = parse(b"a = merge(m1, m2...)\n", HclProfile.NATIVE_V1)
    expression = document.body.items[0].as_attribute().expression
    _, _, args = expression.kind.payload
    assert args[-1].expand is True


def test_traversal_steps_are_never_resolved():
    # hcl-v1.json:375-384 (`v = foo` ... `expridx = foo[1 + 1]`): traversal
    # facts with ordered steps, never resolved.
    document = parse(
        b"v = foo\nattr = foo.bar\nidx = foo[0]\nsplat1 = foo.*.bar\n"
        b"splat2 = foo[*].bar\nchain = foo[0].bar[*].baz\nkwroot = true.bar\n"
        b"expridx = foo[1 + 1]\n",
        HclProfile.NATIVE_V1,
    )
    assert document.formation_status().value == "Complete"
    traversal = document.body.items[1].as_attribute().expression
    assert traversal.kind.name is HclExpressionKindName.TRAVERSAL
    root, steps = traversal.kind.payload
    assert root.name == "foo"
    assert [step.kind for step in steps] == ["get-attr"]
    chain = document.body.items[5].as_attribute().expression
    _, steps = chain.kind.payload
    assert [step.kind for step in steps] == ["index", "get-attr", "full-splat"]


# ---------------------------------------------------------------------------
# Limits (RFC 0014 §11; hcl.limit.*@1 fatal failures)
# ---------------------------------------------------------------------------


def test_limit_expression_depth_and_binary_chain():
    # Cases hcl.limit.expression-depth (hcl-v1.json:1781-1793) and
    # hcl.limit.binary-chain-depth (hcl-v1.json:1796-1809).
    assert (
        fatal_code(b"a = (((1)))\n", limited(max_expression_depth=3))
        == "hcl.limit.expression-depth@1"
    )
    assert (
        fatal_code(b"a = 1 + 1 + 1 + 1 + 1\n", limited(max_expression_depth=3))
        == "hcl.limit.expression-depth@1"
    )


def test_limit_body_nesting():
    # Case hcl.limit.body-nesting (hcl-v1.json:1811-1824).
    source = b"a = 1\nb {\nc {\nd = 1\n}\n}\n"
    assert (
        fatal_code(source, limited(max_body_depth=2)) == "hcl.limit.body-depth@1"
    )


def test_limit_number_digits_and_arithmetic_overflow():
    # Cases hcl.limit.number-digits (hcl-v1.json:1826-1839) and
    # hcl.limit.arithmetic-overflow (hcl-v1.json:1841-1851).
    assert (
        fatal_code(b"a = 1e10\n", limited(max_number_digits=5))
        == "hcl.limit.number-digits@1"
    )
    assert (
        fatal_code(b"a = 1e99999999999999999999\n", HclParseLimits())
        == "hcl.limit.number-digits@1"
    )


def test_limit_count_family():
    # Cases hcl.limit.attribute-count (hcl-v1.json:1853-1866),
    # hcl.limit.block-count (1868-1881), hcl.limit.body-item-count
    # (1883-1896), hcl.limit.label-count (1898-1911).
    assert (
        fatal_code(b"a = 1\nb = 2\nc = 3\n", limited(max_attribute_count=2))
        == "hcl.limit.attribute-count@1"
    )
    assert (
        fatal_code(b"a {\n}\nb {\n}\n", limited(max_block_count=1))
        == "hcl.limit.block-count@1"
    )
    assert (
        fatal_code(b"a = 1\nb = 2\nc = 3\n", limited(max_body_item_count=2))
        == "hcl.limit.body-item-count@1"
    )
    assert (
        fatal_code(b"b \"x\" \"y\" {\n}\n", limited(max_label_count=1))
        == "hcl.limit.label-count@1"
    )


def test_limit_template_heredoc_and_constructor_extents():
    # Cases hcl.limit.template-size (hcl-v1.json:1913-1926),
    # hcl.limit.heredoc-size (1928-1941), hcl.limit.tuple-elements
    # (1943-1956), hcl.limit.object-entries (1958-1971).
    assert (
        fatal_code(b"a = \"xxxxxxxxxxxxxxxxxxxxxxxxxx\"\n", limited(max_template_len=8))
        == "hcl.limit.template-len@1"
    )
    assert (
        fatal_code(b"h = <<E\none\ntwo\nthree\nE\n", limited(max_heredoc_bytes=12))
        == "hcl.limit.heredoc-bytes@1"
    )
    assert (
        fatal_code(b"a = [1, 2, 3]\n", limited(max_tuple_elements=2))
        == "hcl.limit.tuple-elements@1"
    )
    assert (
        fatal_code(b"a = {x = 1, y = 2, z = 3}\n", limited(max_object_entries=2))
        == "hcl.limit.object-entries@1"
    )


def test_lossless_coverage_is_exhaustive_and_contiguous():
    # RFC 0014 §7.2: every non-empty raw byte belongs to exactly one
    # ordered structural piece with one of the closed 30 kinds.
    source = b"# c\nregion = \"us-east-1\"\n"
    document = parse(source, HclProfile.NATIVE_V1)
    pieces = document.lossless_structural_index().pieces
    kinds = document.lossless_syntax_kinds()
    assert len(pieces) == len(kinds)
    # There is no Bom kind: a BOM is excluded at formation (RFC 0014 §7.2).
    assert HclSyntaxKind.from_name("Bom") is None
    next_byte = 0
    for piece, kind in zip(pieces, kinds):
        assert piece.span.start_byte == next_byte
        next_byte = piece.span.end_byte
    assert next_byte == len(source)
