"""HCL materialization: `hcl.canonical-document@1` with reparse closure
(RFC 0014 §9).

Golden cases transcribed verbatim from conformance/vectors/hcl-v1.json
(suite "consema.hcl.conformance@1"); each test cites the vector case id.
Cases covered:

- hcl-v1.json: hcl.materialization.canonical-document (1153-1283),
  hcl.materialization.reparse-closure (1285-1329),
  hcl.materialization.unrepresentable (1331-1411),
  hcl.materialization.typed-member-form (1413-1460),
  hcl.materialization.tfvars-canonical (1973-2045).
"""

from __future__ import annotations

from consema.core.value import Decimal, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import MaterializationRequest
from consema.hcl import HclProfile, parse
from consema.hcl.materialization import materialization_failure_code, materialize

STYLE = MaterializationStyleId.new("hcl.canonical-document", 1)


def request(profile: str) -> MaterializationRequest:
    return MaterializationRequest.new(ProfileId.new(profile, 1), STYLE)


def attribute(name: str, value: PortableValue) -> PortableValue:
    return PortableValue.object(
        (
            ("kind", PortableValue.string("attribute")),
            ("name", PortableValue.string(name)),
            ("value", value),
        )
    )


def value_record(kind: str, **members) -> PortableValue:
    return PortableValue.object(
        (("kind", PortableValue.string(kind)),)
        + tuple((key, value) for key, value in members.items())
    )


def test_canonical_document_golden():
    # Case hcl.materialization.canonical-document (hcl-v1.json:1153-1283).
    record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.body@1")),
            (
                "items",
                PortableValue.sequence(
                    (
                        attribute("name", value_record("string", text=PortableValue.string("hello"))),
                        attribute(
                            "escaped",
                            value_record("string", text=PortableValue.string("a\nb\t\"c\\d")),
                        ),
                        attribute("count", value_record("integer", value=PortableValue.integer(42))),
                        attribute("ratio", value_record("real", value=PortableValue.decimal(Decimal(15, -1)))),
                        attribute("enabled", value_record("boolean", value=PortableValue.boolean(True))),
                        attribute("nothing", value_record("null")),
                        attribute(
                            "tags",
                            value_record(
                                "tuple",
                                elements=PortableValue.sequence(
                                    (
                                        value_record("string", text=PortableValue.string("a")),
                                        value_record("string", text=PortableValue.string("b")),
                                    )
                                ),
                            ),
                        ),
                        attribute(
                            "labels",
                            value_record(
                                "object",
                                entries=PortableValue.sequence(
                                    (
                                        PortableValue.sequence(
                                            (
                                                PortableValue.string("env"),
                                                value_record("string", text=PortableValue.string("prod")),
                                            )
                                        ),
                                    )
                                ),
                            ),
                        ),
                        attribute("empty_tuple", value_record("tuple", elements=PortableValue.sequence([]))),
                        attribute("empty_obj", value_record("object", entries=PortableValue.sequence([]))),
                        PortableValue.object(
                            (
                                ("kind", PortableValue.string("block")),
                                ("type", PortableValue.string("server")),
                                (
                                    "labels",
                                    PortableValue.sequence(
                                        (PortableValue.string("web"), PortableValue.string("1"))
                                    ),
                                ),
                                (
                                    "body",
                                    PortableValue.object(
                                        (
                                            ("record", PortableValue.string("hcl.body@1")),
                                            (
                                                "items",
                                                PortableValue.sequence(
                                                    (
                                                        attribute(
                                                            "port",
                                                            value_record("integer", value=PortableValue.integer(8080)),
                                                        ),
                                                    )
                                                ),
                                            ),
                                        )
                                    ),
                                ),
                            )
                        ),
                    )
                ),
            ),
        )
    )
    result = materialize(record, request("hcl.native"))
    assert result.__class__.__name__ == "CompleteMaterialization"
    expected = (
        "name = \"hello\"\nescaped = \"a\\nb\\t\\\"c\\\\d\"\ncount = 42\n"
        "ratio = 1.5\nenabled = true\nnothing = null\ntags = [\n  \"a\",\n  \"b\"\n]\n"
        "labels = {\n  env = \"prod\"\n}\nempty_tuple = []\nempty_obj = {}\n"
        "server \"web\" \"1\" {\n  port = 8080\n}\n"
    )
    assert result.document.render().decode("utf-8") == expected
    assert result.fidelity.value == "Exact"


def test_reparse_closure_with_expression():
    # Case hcl.materialization.reparse-closure (hcl-v1.json:1285-1329):
    # `hcl.expression@1` values emit their canonical text and must reparse
    # to the same structural fingerprint.
    record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.body@1")),
            (
                "items",
                PortableValue.sequence(
                    (
                        attribute(
                            "derived",
                            value_record(
                                "expression",
                                expression=PortableValue.object(
                                    (
                                        ("record", PortableValue.string("hcl.expression@1")),
                                        ("kind", PortableValue.string("binary")),
                                        ("text", PortableValue.string("1 + 2")),
                                    )
                                ),
                            ),
                        ),
                        attribute("big", value_record("integer", value=PortableValue.integer(1000))),
                        attribute("small", value_record("real", value=PortableValue.decimal(Decimal(15, -1)))),
                    )
                ),
            ),
        )
    )
    result = materialize(record, request("hcl.native"))
    assert result.__class__.__name__ == "CompleteMaterialization"
    assert result.document.render().decode("utf-8") == "derived = 1 + 2\nbig = 1000\nsmall = 1.5\n"
    # The reparse closure holds: the reparsed document is Complete and the
    # promised semantics matched (numbers by canonical-decimal equality).
    assert result.document.formation_status().value == "Complete"


def test_unrepresentable_cases():
    # Case hcl.materialization.unrepresentable (hcl-v1.json:1331-1411).
    block_record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.body@1")),
            (
                "items",
                PortableValue.sequence(
                    (
                        PortableValue.object(
                            (
                                ("kind", PortableValue.string("block")),
                                ("type", PortableValue.string("server")),
                                ("labels", PortableValue.sequence((PortableValue.string("x"),))),
                                (
                                    "body",
                                    PortableValue.object(
                                        (
                                            ("record", PortableValue.string("hcl.body@1")),
                                            ("items", PortableValue.sequence(())),
                                        )
                                    ),
                                ),
                            )
                        ),
                    )
                ),
            ),
        )
    )
    # Sample 1: a block under the tfvars profile fails unrepresentable
    # (RFC 0014 §5, §9).
    result = materialize(block_record, request("hcl.tfvars"))
    assert result.__class__.__name__ == "FailedMaterializationAttempt"
    assert materialization_failure_code(result.failure) == "hcl.materialization.unrepresentable@1"

    # Sample 2: a wrong record name fails with the published "invalid-record"
    # spelling (hcl_v1.rs:1611-1616).
    wrong_record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.something-else@1")),
            ("items", PortableValue.sequence(())),
        )
    )
    result = materialize(wrong_record, request("hcl.native"))
    assert result.__class__.__name__ == "FailedMaterializationAttempt"
    assert materialization_failure_code(result.failure) == "invalid-record"

    # Sample 3: the same block under the native profile materializes.
    result = materialize(block_record, request("hcl.native"))
    assert result.__class__.__name__ == "CompleteMaterialization"
    assert result.document.render().decode("utf-8") == "server \"x\" {\n}\n"


def test_typed_member_form():
    # Case hcl.materialization.typed-member-form (hcl-v1.json:1413-1460):
    # the RFC 0014 §8.2 raw typed member is accepted for every attribute
    # value and materializes identical bytes.
    record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.body@1")),
            (
                "items",
                PortableValue.sequence(
                    (
                        attribute("name", PortableValue.string("hello")),
                        attribute("count", PortableValue.integer(42)),
                        attribute("ratio", PortableValue.decimal(Decimal(15, -1))),
                        attribute("enabled", PortableValue.boolean(True)),
                        attribute("nothing", PortableValue.null()),
                        attribute(
                            "tags",
                            PortableValue.sequence(
                                (PortableValue.string("a"), PortableValue.string("b"))
                            ),
                        ),
                    )
                ),
            ),
        )
    )
    result = materialize(record, request("hcl.native"))
    assert result.__class__.__name__ == "CompleteMaterialization"
    assert result.document.render().decode("utf-8") == (
        "name = \"hello\"\ncount = 42\nratio = 1.5\nenabled = true\n"
        "nothing = null\ntags = [\n  \"a\",\n  \"b\"\n]\n"
    )


def test_tfvars_canonical():
    # Case hcl.materialization.tfvars-canonical (hcl-v1.json:1973-2045).
    record = PortableValue.object(
        (
            ("record", PortableValue.string("hcl.body@1")),
            (
                "items",
                PortableValue.sequence(
                    (
                        attribute("region", value_record("string", text=PortableValue.string("us-east-1"))),
                        attribute("count", value_record("integer", value=PortableValue.integer(3))),
                        attribute("ratio", value_record("real", value=PortableValue.decimal(Decimal(5, -1)))),
                        attribute(
                            "tags",
                            value_record(
                                "tuple",
                                elements=PortableValue.sequence(
                                    (
                                        value_record("string", text=PortableValue.string("a")),
                                        value_record("string", text=PortableValue.string("b")),
                                    )
                                ),
                            ),
                        ),
                        attribute(
                            "labels",
                            value_record(
                                "object",
                                entries=PortableValue.sequence(
                                    (
                                        PortableValue.sequence(
                                            (
                                                PortableValue.string("env"),
                                                value_record("string", text=PortableValue.string("prod")),
                                            )
                                        ),
                                    )
                                ),
                            ),
                        ),
                    )
                ),
            ),
        )
    )
    result = materialize(record, request("hcl.tfvars"))
    assert result.__class__.__name__ == "CompleteMaterialization"
    expected = (
        "region = \"us-east-1\"\ncount = 3\nratio = 0.5\n"
        "tags = [\n  \"a\",\n  \"b\"\n]\nlabels = {\n  env = \"prod\"\n}\n"
    )
    assert result.document.render().decode("utf-8") == expected
    # The tfvars target reparses as a Complete attribute-only document.
    reparsed = parse(result.document.render(), HclProfile.TFVARS_V1)
    assert reparsed.formation_status().value == "Complete"
