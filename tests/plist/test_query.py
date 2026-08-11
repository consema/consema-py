"""Query golden transcriptions for the plist family (three domains).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json;
each test cites the vector case id. The transferable query model
(QueryDefinition / ExecutableQuery) is built through consema.protocol.query
with the plist domain identities.

Cases covered here:

- plist-v1.json: plist.query.dict-entries-order (lines 917-946),
  plist.query.typed-accessors (948-1042),
  plist.query.binary-structure (1043-1089).
"""

from __future__ import annotations

import pytest

from consema.plist import (
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
    execute_plist_binary_query,
    execute_plist_native_query,
    parse,
)
from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryDomain,
    QueryExpression,
    QueryFailure,
    QuerySelection,
    ValidatedQuery,
    domain_plist_binary_structure_v1,
    domain_plist_native_v1,
)

DEFAULT_LIMITS = PlistParseLimits()


def _capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def xml_document(source: str):
    return parse(
        source.encode("utf-8"),
        PlistProfile.XML_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def binary_document(hex_string: str):
    return parse(
        bytes.fromhex(hex_string),
        PlistProfile.BINARY_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def chain(domain: QueryDomain, operators: list[OperatorCall]) -> ValidatedQuery:
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    definition = QueryDefinition(domain).with_expression(expression).with_selection(
        QuerySelection.ALL
    )
    return definition.validate()


def _string_value(text: str):
    from consema.core.value import PortableValue

    return PortableValue.string(text)


# ---------------------------------------------------------------------------
# plist.query.dict-entries-order (plist-v1.json:917-946)
# ---------------------------------------------------------------------------


def test_query_dict_entries_order():
    # Case plist.query.dict-entries-order (plist-v1.json:918-945).
    source = (
        '<plist version="1.0"><dict><key>a</key><integer>1</integer>'
        "<key>b</key><array><string>x</string></array>"
        "<key>a</key><integer>2</integer></dict></plist>"
    )
    document = xml_document(source)
    executable = chain(
        domain_plist_native_v1(),
        [OperatorCall("plist.document-root", 1), OperatorCall("plist.dict-entries", 1)],
    ).bind(_capabilities())
    from consema.plist import PlistCancellationToken, PlistQueryLimits

    execution = execute_plist_native_query(
        executable, document, PlistQueryLimits(), PlistCancellationToken()
    )
    from consema.plist import PlistMatchKind

    matches = [
        match
        for match in execution.matches
        if match.kind is PlistMatchKind.DICT_ENTRY
    ]
    assert [match.key.to_unicode() for match in matches] == ["a", "b", "a"]
    assert [match.value_kind.value for match in matches] == ["integer", "array", "integer"]


# ---------------------------------------------------------------------------
# plist.query.typed-accessors (plist-v1.json:948-1042)
# ---------------------------------------------------------------------------


def test_query_typed_accessors():
    # Case plist.query.typed-accessors (plist-v1.json:949-1041).
    source = (
        '<plist version="1.0"><dict><key>count</key><integer>42</integer>'
        "<key>created</key><date>2023-01-01T00:00:00Z</date>"
        "<key>name</key><string>x</string></dict></plist>"
    )
    document = xml_document(source)
    executable = chain(
        domain_plist_native_v1(),
        [
            OperatorCall("plist.document-root", 1),
            OperatorCall("plist.dict-entries", 1),
            OperatorCall("plist.dict-key-equals", 1).with_argument("key", _string_value("count")),
            OperatorCall("plist.dict-entry-value", 1),
            OperatorCall("plist.value-type-is", 1).with_argument("kind", _string_value("integer")),
            OperatorCall("plist.value-as-integer", 1),
        ],
    ).bind(_capabilities())
    from consema.plist import PlistCancellationToken, PlistQueryLimits

    execution = execute_plist_native_query(
        executable, document, PlistQueryLimits(), PlistCancellationToken()
    )
    assert len(execution.matches) == 1
    match = execution.matches[0]
    assert match.value_kind.value == "integer"

    date_executable = chain(
        domain_plist_native_v1(),
        [
            OperatorCall("plist.document-root", 1),
            OperatorCall("plist.dict-entries", 1),
            OperatorCall("plist.dict-key-equals", 1).with_argument("key", _string_value("created")),
            OperatorCall("plist.dict-entry-value", 1),
            OperatorCall("plist.value-as-date", 1),
        ],
    ).bind(_capabilities())
    date_execution = execute_plist_native_query(
        date_executable, document, PlistQueryLimits(), PlistCancellationToken()
    )
    assert len(date_execution.matches) == 1

    mismatch = chain(
        domain_plist_native_v1(),
        [
            OperatorCall("plist.document-root", 1),
            OperatorCall("plist.dict-entries", 1),
            OperatorCall("plist.dict-key-equals", 1).with_argument("key", _string_value("count")),
            OperatorCall("plist.dict-entry-value", 1),
            OperatorCall("plist.value-as-string", 1),
        ],
    ).bind(_capabilities())
    with pytest.raises(QueryFailure) as excinfo:
        execute_plist_native_query(
            mismatch, document, PlistQueryLimits(), PlistCancellationToken()
        )
    # The conformance runner maps RequiredTypeMismatch to
    # plist.query.type-mismatch@1 (consema-conformance plist_v1.rs:1149).
    assert excinfo.value.kind.value == "required-type-mismatch"


# ---------------------------------------------------------------------------
# plist.query.binary-structure (plist-v1.json:1043-1089)
# ---------------------------------------------------------------------------


def test_query_binary_structure():
    # Case plist.query.binary-structure (plist-v1.json:1044-1088). The
    # binary structure facts are document-level (RFC 0013 §8.3): every
    # structure operator projects its fact set once from any binary-
    # structure match, and the runner executes each filter standalone
    # (consema-conformance plist_v1.rs:1400-1412).
    document = binary_document(
        "62706c6973743030d1010251611001080b0d000000000000010100000000000000030000000000000000000000000000000f"
    )
    # The full chain validates, binds, and executes (terminal Completed).
    executable = chain(
        domain_plist_binary_structure_v1(),
        [
            OperatorCall("plist.object-table", 1),
            OperatorCall("plist.offset-table", 1),
            OperatorCall("plist.trailer-facts", 1),
            OperatorCall("plist.top-object", 1),
        ],
    ).bind(_capabilities())
    from consema.plist import (
        PlistBinaryMatchKind,
        PlistCancellationToken,
        PlistQueryLimits,
    )

    execute_plist_binary_query(
        executable, document, PlistQueryLimits(), PlistCancellationToken()
    )

    def standalone(operator: str):
        one = chain(
            domain_plist_binary_structure_v1(), [OperatorCall(operator, 1)]
        ).bind(_capabilities())
        return execute_plist_binary_query(
            one, document, PlistQueryLimits(), PlistCancellationToken()
        ).matches

    objects = standalone("plist.object-table")
    assert [m.offset for m in objects] == [8, 11, 13]
    assert [f"{m.marker:02x}" for m in objects] == ["d1", "51", "10"]
    offsets = standalone("plist.offset-table")
    assert [m.offset for m in offsets] == [8, 11, 13]
    trailer = standalone("plist.trailer-facts")
    assert len(trailer) == 1
    assert trailer[0].num_objects == 3
    assert trailer[0].top_object == 0
    assert trailer[0].offset_int_size == 1
    assert trailer[0].object_ref_size == 1
    assert trailer[0].sort_version == 0
    assert trailer[0].offset_table_offset == 15
    top = standalone("plist.top-object")
    assert len(top) == 1
    assert f"{top[0].marker:02x}" == "d1"
    assert [(position, target) for position, target, _ in top[0].refs] == [
        (0, 1),
        (1, 2),
    ]


def test_query_domain_mismatch_on_wrong_representation():
    # A binary-structure query against an XML document is a DomainMismatch
    # (hard gate 1, query.rs:388-402).
    document = xml_document('<plist version="1.0"><string>ok</string></plist>')
    executable = chain(
        domain_plist_binary_structure_v1(),
        [OperatorCall("plist.object-table", 1)],
    ).bind(_capabilities())
    from consema.plist import PlistCancellationToken, PlistQueryLimits

    with pytest.raises(QueryFailure) as excinfo:
        execute_plist_binary_query(
            executable, document, PlistQueryLimits(), PlistCancellationToken()
        )
    assert excinfo.value.kind.value == "domain-mismatch"


