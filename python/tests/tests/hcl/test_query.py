"""HCL query: native-semantic and lossless-syntax domains (RFC 0014 §7).

Golden cases transcribed verbatim from conformance/vectors/hcl-v1.json
(suite "consema.hcl.conformance@1"); each test cites the vector case id.
Cases covered:

- hcl-v1.json: hcl.query.native-body-walk (567-608),
  hcl.query.blocks-and-labels (610-688), hcl.query.literal-accessors
  (690-812), hcl.query.lossless-kind-filter (814-861),
  hcl.query.error-regions (863-887).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.hcl import (
    HclCancellationToken,
    HclProfile,
    HclQueryLimits,
    HclSyntaxKind,
    execute_hcl_native_query,
    execute_hcl_syntax_query,
    parse,
)
from consema.protocol.query import OperatorCall, QueryFailure, QueryFailureKind
from tests.hcl.conftest import executable

QUERY_LIMITS = HclQueryLimits()


def native(operators: list[OperatorCall], source: bytes):
    document = parse(source, HclProfile.NATIVE_V1)
    return execute_hcl_native_query(
        executable("hcl.native-semantic-query", 1, operators),
        document,
        QUERY_LIMITS,
        HclCancellationToken(),
    )


def syntax(operators: list[OperatorCall], source: bytes):
    document = parse(source, HclProfile.NATIVE_V1)
    return execute_hcl_syntax_query(
        executable("hcl.lossless-syntax-query", 1, operators),
        document,
        QUERY_LIMITS,
        HclCancellationToken(),
    )


def match_texts(execution, document) -> list[str]:
    raw = document.source.bytes()
    return [
        raw[match.expression.span.start_byte : match.expression.span.end_byte].decode("utf-8")
        for match in execution.matches
    ]


def test_native_body_walk():
    # Case hcl.query.native-body-walk (hcl-v1.json:567-608).
    source = b"region = \"us-east-1\"\nserver \"web\" {\n  port = 8080\n}\ncount = 3\n"
    execution = native(
        [
            OperatorCall("hcl.document-body", 1),
            OperatorCall("hcl.body-attributes", 1),
            OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                "name", PortableValue.string("count")
            ),
            OperatorCall("hcl.attribute-expression", 1),
            OperatorCall("hcl.expression-is-literal", 1),
            OperatorCall("hcl.expression-kind-is", 1).with_argument(
                "kind", PortableValue.string("number")
            ),
            OperatorCall("hcl.expression-text", 1),
        ],
        source,
    )
    document = parse(source, HclProfile.NATIVE_V1)
    assert len(execution.matches) == 1
    match = execution.matches[0]
    assert match.expression.kind.as_str() == "number"
    assert match.expression.text(document.source) == "3"


def test_blocks_and_labels_both_samples():
    # Case hcl.query.blocks-and-labels (hcl-v1.json:610-688).
    source = b"region = \"us-east-1\"\nserver \"web\" {\n  port = 8080\n}\ncount = 3\n"

    labels = native(
        [
            OperatorCall("hcl.document-body", 1),
            OperatorCall("hcl.body-blocks", 1),
            OperatorCall("hcl.block-type-equals", 1).with_argument(
                "type", PortableValue.string("server")
            ),
            OperatorCall("hcl.block-labels", 1),
            OperatorCall("hcl.block-label-equals", 1).with_argument(
                "label", PortableValue.string("web")
            ),
        ],
        source,
    )
    assert len(labels.matches) == 1
    label = labels.matches[0]
    assert label.text == "web"
    assert label.label.quoted is True

    nested = native(
        [
            OperatorCall("hcl.document-body", 1),
            OperatorCall("hcl.body-blocks", 1),
            OperatorCall("hcl.block-type-equals", 1).with_argument(
                "type", PortableValue.string("server")
            ),
            OperatorCall("hcl.block-nested-body", 1),
            OperatorCall("hcl.body-attributes", 1),
            OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                "name", PortableValue.string("port")
            ),
            OperatorCall("hcl.attribute-expression", 1),
            OperatorCall("hcl.expression-text", 1),
        ],
        source,
    )
    document = parse(source, HclProfile.NATIVE_V1)
    assert len(nested.matches) == 1
    assert nested.matches[0].expression.text(document.source) == "8080"


def test_literal_accessors():
    # Case hcl.query.literal-accessors (hcl-v1.json:690-812).
    def accessor(source: bytes, name: str):
        return native(
            [
                OperatorCall("hcl.document-body", 1),
                OperatorCall("hcl.body-attributes", 1),
                OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                    "name", PortableValue.string(name)
                ),
                OperatorCall("hcl.attribute-expression", 1),
                OperatorCall("hcl.attribute-literal-value", 1).with_argument(
                    "accessor", PortableValue.string("as-integer")
                ),
            ],
            source,
        )

    execution = accessor(b"count = 42\n", "count")
    assert len(execution.matches) == 1

    with pytest.raises(QueryFailure) as raised:
        accessor(b'name = "x"\n', "name")
    assert raised.value.kind is QueryFailureKind.REQUIRED_TYPE_MISMATCH

    with pytest.raises(QueryFailure) as raised:
        accessor(b"name = var.name\n", "name")
    assert raised.value.kind is QueryFailureKind.TARGET_UNAVAILABLE


def test_boolean_accessor_is_complete():
    # Case hcl.query.literal-accessors sample 4 (hcl-v1.json:763-771, 804-810).
    execution = native(
        [
            OperatorCall("hcl.document-body", 1),
            OperatorCall("hcl.body-attributes", 1),
            OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                "name", PortableValue.string("enabled")
            ),
            OperatorCall("hcl.attribute-expression", 1),
            OperatorCall("hcl.attribute-literal-value", 1).with_argument(
                "accessor", PortableValue.string("as-boolean-is")
            ),
        ],
        b"enabled = true\n",
    )
    assert len(execution.matches) == 1


def test_lossless_kind_filter_with_ordinals():
    # Case hcl.query.lossless-kind-filter (hcl-v1.json:814-861).
    source = b"# c\nregion = \"us-east-1\"\n"

    comments = syntax(
        [
            OperatorCall("hcl.syntax-kind-is", 1).with_argument(
                "kind", PortableValue.string("LineComment")
            )
        ],
        source,
    )
    assert len(comments.matches) == 1
    match = comments.matches[0]
    assert match.kind is HclSyntaxKind.LINE_COMMENT
    assert source[match.span.start_byte : match.span.end_byte] == b"# c"
    assert match.ordinal == 0

    contents = syntax(
        [
            OperatorCall("hcl.syntax-kind-is", 1).with_argument(
                "kind", PortableValue.string("StringContent")
            )
        ],
        source,
    )
    assert len(contents.matches) == 1
    match = contents.matches[0]
    assert match.kind is HclSyntaxKind.STRING_CONTENT
    assert source[match.span.start_byte : match.span.end_byte] == b"us-east-1"
    assert match.ordinal == 7


def test_error_regions_query():
    # Case hcl.query.error-regions (hcl-v1.json:863-887).
    execution = native(
        [
            OperatorCall("hcl.document-body", 1),
            OperatorCall("hcl.error-regions", 1),
        ],
        b"a = 1\nb {\n",
    )
    assert len(execution.matches) == 1
    match = execution.matches[0]
    assert match.region.code == "hcl.parse.block@1"
    assert match.position == 0


def test_template_parts_and_constructor_content():
    # RFC 0014 §7.1: hcl.template-parts exposes ordered parts;
    # hcl.tuple-elements and hcl.object-entries expose constructor content.
    document = parse(
        b'a = "x${y}z"\nb = [1, 2]\nc = {k = 3}\n',
        HclProfile.NATIVE_V1,
    )
    execution = execute_hcl_native_query(
        executable(
            "hcl.native-semantic-query",
            1,
            [
                OperatorCall("hcl.document-body", 1),
                OperatorCall("hcl.body-attributes", 1),
                OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                    "name", PortableValue.string("a")
                ),
                OperatorCall("hcl.attribute-expression", 1),
                OperatorCall("hcl.template-parts", 1),
            ],
        ),
        document,
        QUERY_LIMITS,
        HclCancellationToken(),
    )
    assert [match.part.kind for match in execution.matches] == [
        "literal",
        "interpolation",
        "literal",
    ]
    assert execution.matches[1].part.expression.text(document.source) == "y"

    execution = execute_hcl_native_query(
        executable(
            "hcl.native-semantic-query",
            1,
            [
                OperatorCall("hcl.document-body", 1),
                OperatorCall("hcl.body-attributes", 1),
                OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                    "name", PortableValue.string("b")
                ),
                OperatorCall("hcl.attribute-expression", 1),
                OperatorCall("hcl.tuple-elements", 1),
            ],
        ),
        document,
        QUERY_LIMITS,
        HclCancellationToken(),
    )
    assert match_texts(execution, document) == ["1", "2"]

    execution = execute_hcl_native_query(
        executable(
            "hcl.native-semantic-query",
            1,
            [
                OperatorCall("hcl.document-body", 1),
                OperatorCall("hcl.body-attributes", 1),
                OperatorCall("hcl.attribute-name-equals", 1).with_argument(
                    "name", PortableValue.string("c")
                ),
                OperatorCall("hcl.attribute-expression", 1),
                OperatorCall("hcl.object-entries", 1),
            ],
        ),
        document,
        QUERY_LIMITS,
        HclCancellationToken(),
    )
    assert match_texts(execution, document) == ["3"]
