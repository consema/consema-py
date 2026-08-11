"""HCL projection: the `hcl.projection.body@1` record and the
`hcl.expression@1` ExtendedValue (RFC 0014 §8).

Golden cases transcribed verbatim from conformance/vectors/hcl-v1.json
(suite "consema.hcl.conformance@1"); each test cites the vector case id.
Cases covered:

- hcl-v1.json: hcl.projection.literal-complete-record (889-1009),
  hcl.projection.non-literal-expression (1011-1041),
  hcl.projection.project-expression-policy (1043-1081),
  hcl.projection.literal-complete-boundary (1083-1151).
"""

from __future__ import annotations

from consema.core.value import Decimal, Kind
from consema.hcl import (
    ExpressionPolicy,
    HclProfile,
    ProjectionRequest,
    parse,
    project,
)

NATIVE = HclProfile.NATIVE_V1


def projected_items(record) -> list[dict]:
    members = dict(record.as_object())
    assert members["record"].as_string() == "hcl.body@1"
    return [dict(item.as_object()) for item in members["items"].as_sequence()]


def projected_value(item: dict):
    """The raw typed member the projection publishes as the attribute
    value (RFC 0014 §8.2, §9: "the raw typed member is the form the
    projection publishes")."""
    return item["value"]


def test_literal_complete_record():
    # Case hcl.projection.literal-complete-record (hcl-v1.json:889-1009).
    source = (
        'name = "consema"\ncount = 42\nratio = 1.50\nbig = 1e3\nsmall = 15e-1\n'
        "enabled = true\nnothing = null\ntags = [\"a\", \"b\"]\n"
        "labels = { env = \"prod\" }\ndups = { a = 1, a = 2 }\n"
        'numkeys = { 1 = "one", 2 = "two" }\nnested = { "x" = { y = [1, 2] } }\n'
    )
    document = parse(source.encode("utf-8"), NATIVE)
    result = project(document, ProjectionRequest.body())
    assert result.__class__.__name__ == "CompleteProjection"
    items = projected_items(result.value)
    assert len(items) == 12
    expected = [
        ("name", "string", "consema"),
        ("count", "integer", 42),
        ("ratio", "real", 1.5),
        ("big", "integer", 1000),
        ("small", "real", 1.5),
        ("enabled", "boolean", True),
        ("nothing", "null", None),
    ]
    for item, (name, kind, value) in zip(items, expected):
        assert item["name"].as_string() == name
        member = projected_value(item)
        if kind == "string":
            assert member.kind is Kind.STRING and member.as_string() == value
        elif kind == "integer":
            assert member.kind is Kind.INTEGER and member.as_integer() == value
        elif kind == "real":
            assert member.kind is Kind.DECIMAL and member.as_decimal() == Decimal(15, -1)
        elif kind == "boolean":
            assert member.kind is Kind.BOOLEAN and member.as_boolean() is value
        elif kind == "null":
            assert member.kind is Kind.NULL
    # Duplicate object keys are preserved in order (RFC 0014 §6): the
    # projected object is the ordered entry-mapping form.
    dups = projected_value(items[9])
    assert dups.kind is Kind.ENTRY_MAPPING
    entries = list(dups.as_entry_mapping())
    assert len(entries) == 2
    assert entries[0][0].as_string() == "a"
    assert entries[1][0].as_string() == "a"
    # Number keys carry the canonical decimal spelling.
    numkeys = projected_value(items[10])
    first_key = numkeys.as_entry_mapping()[0][0].as_string()
    assert first_key == "1"
    # Attribute order is preserved exactly.
    assert [item["name"].as_string() for item in items] == [
        "name", "count", "ratio", "big", "small", "enabled", "nothing",
        "tags", "labels", "dups", "numkeys", "nested",
    ]
    assert result.fidelity.value == "Exact"


def test_non_literal_expression_fails_atomically():
    # Case hcl.projection.non-literal-expression (hcl-v1.json:1011-1041).
    samples = [
        "count = 1 + 2\n",
        "name = var.name\n",
        'msg = "hi ${name}"\n',
        "items = [for x in list : x]\n",
    ]
    for source in samples:
        document = parse(source.encode("utf-8"), NATIVE)
        result = project(document, ProjectionRequest.body())
        assert result.__class__.__name__ == "FailedProjectionAttempt", source
        assert result.diagnostics[0].code == "hcl.projection.non-literal-expression@1"
        # A failed attempt never contains a partial value (RFC 0004 §7).
        assert not hasattr(result, "value")


def test_project_expression_policy():
    # Case hcl.projection.project-expression-policy (hcl-v1.json:1043-1081).
    source = "count = 1 + 2\nname = var.name\nok = 42\n"
    document = parse(source.encode("utf-8"), NATIVE)
    result = project(
        document,
        ProjectionRequest.body_with_expression_policy(ExpressionPolicy.PROJECT_EXPRESSION),
    )
    assert result.__class__.__name__ == "CompleteProjection"
    items = projected_items(result.value)
    count_value = projected_value(items[0])
    # A derived expression is projected as the authorized `hcl.expression@1`
    # record itself (RFC 0014 §8.2; go/hcl/projection.go:559-573), not a
    # {kind, expression} wrapper.
    assert count_value.kind is Kind.OBJECT
    record = dict(count_value.as_object())
    assert record["record"].as_string() == "hcl.expression@1"
    assert record["kind"].as_string() == "binary"
    assert record["text"].as_string() == "1 + 2"
    # The fingerprint is the 16 lowercase hex digits of the structural
    # fingerprint (projection.rs:81-88).
    fingerprint = record["fingerprint"].as_string()
    assert len(fingerprint) == 16
    int(fingerprint, 16)
    name_value = projected_value(items[1])
    name_record = dict(name_value.as_object())
    assert name_record["kind"].as_string() == "variable"
    assert name_record["text"].as_string() == "var.name"
    ok_value = projected_value(items[2])
    assert ok_value.kind is Kind.INTEGER
    assert ok_value.as_integer() == 42
    # One Transformed event per substituted expression with value and
    # expression provenance (RFC 0014 §8.2).
    assert result.fidelity.value == "Transformed"
    assert len(result.report.events) == 2
    assert all(event.impact.value == "Transformed" for event in result.report.events)
    assert len(result.provenance.entries) > 0


def test_literal_complete_boundary():
    # Case hcl.projection.literal-complete-boundary (hcl-v1.json:1083-1151).
    samples = [
        ("a = -1\n", True),
        ("a = 1 + 2\n", False),
        ('a = {1 = "a"}\n', True),
        ('a = "no interpolation"\n', True),
        ('a = "x${y}"\n', False),
        ("a = <<EOT\nplain\nEOT\n", True),
        ("a = <<EOT\nhi ${x}\nEOT\n", False),
        ("a = (42)\n", True),
        ("a = -x\n", False),
        ('a = [1, "two", {k = 3}]\n', True),
        ("a = null\n", True),
        ("a = !true\n", False),
        ("a = max(1, 2)\n", False),
        ("a = 15e-1\n", True),
    ]
    from consema.hcl.expression import is_literal_complete

    for source, literal in samples:
        document = parse(source.encode("utf-8"), NATIVE)
        expression = document.body.items[0].as_attribute().expression
        assert is_literal_complete(expression) is literal, source
        result = project(document, ProjectionRequest.body())
        if literal:
            assert result.__class__.__name__ == "CompleteProjection", source
        else:
            assert result.__class__.__name__ == "FailedProjectionAttempt", source


def test_recovered_document_never_projects():
    # RFC 0014 §8.2: "A Recovered Document never projects".
    document = parse(b"a = 1\nb {\n", NATIVE)
    assert document.formation_status().value == "Recovered"
    result = project(document, ProjectionRequest.body())
    assert result.__class__.__name__ == "FailedProjectionAttempt"
    assert result.diagnostics[0].code == "hcl.projection.incomplete-document@1"


def test_expression_kind_family_spellings():
    # The closed kind-family table (projection.rs:996-1040): variable and
    # traversal are one family; for-expressions are one family.
    from consema.hcl.expression import HclExpressionKindName

    assert HclExpressionKindName.NUMBER.kind_family() == "number"
    assert HclExpressionKindName.VARIABLE_REF.kind_family() == "variable"
    assert HclExpressionKindName.TRAVERSAL.kind_family() == "variable"
    assert HclExpressionKindName.FOR_TUPLE.kind_family() == "for"
    assert HclExpressionKindName.FOR_OBJECT.kind_family() == "for"
    assert HclExpressionKindName.FUNCTION_CALL.kind_family() == "function-call"
    assert HclExpressionKindName.PARENTHESIZED.kind_family() == "parenthesized"
