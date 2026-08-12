"""Query golden transcriptions and the duplicate-group contract (INI).

Cases covered here (conformance/vectors/ini-v1.json, suite
"consema.ini.conformance@1"):

- query.native-order-and-profile-equivalence (lines 46-47),
  query.syntax-decoded-structure-order (51-52),
  query.validation-limit-cancellation (56-57).

The duplicate-group test pins RFC 0009 §9 (docs/rfcs/0009-ini-family-
profiles-v1.md:330-335): ``ini.duplicate-group@1`` expands each input
occurrence to every same-role occurrence carrying the same non-absent
group identity, in source order; an occurrence without a group produces no
match; repeated input groups may repeat output. Name filters require
OriginalExact | ProfileEquivalent explicitly (RFC 0009 §9, lines 301-304);
syntax text comparison uses decoded scalar text (lines 337-341).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.ini import (
    IniCancellationToken,
    IniEncodingSelection,
    IniParseLimits,
    IniProfile,
    IniQueryLimits,
    execute_ini_query,
    execute_ini_syntax_query,
    parse,
)
from consema.protocol.query import (
    CapabilityId,
    CapabilitySet,
    ExpressionKind,
    OperatorCall,
    QueryDomain,
    QueryDefinition,
    QueryExpression,
    QueryFailure,
    QueryFailureKind,
)

DEFAULT_LIMITS = IniParseLimits()
QUERY_LIMITS = IniQueryLimits()


def capabilities() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def executable(domain_id: str, version: int, operators: list[OperatorCall]):
    definition = QueryDefinition(QueryDomain(domain_id, version))
    expression = QueryExpression(ExpressionKind.INPUT)
    for operator in operators:
        expression = expression.then(operator)
    return definition.with_expression(expression).validate().bind(capabilities())


def merged_executable(domain_id: str, version: int, branches: list[QueryExpression]):
    definition = QueryDefinition(QueryDomain(domain_id, version))
    merged = QueryExpression(ExpressionKind.STRUCTURE_ORDER_MERGE, branches=branches)
    return definition.with_expression(merged).validate().bind(capabilities())


def windows_document(source: bytes):
    return parse(
        source,
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


# ---------------------------------------------------------------------------
# query.native-order-and-profile-equivalence (ini-v1.json:46-47)
# ---------------------------------------------------------------------------


def test_native_order_and_profile_equivalence():
    # Case query.native-order-and-profile-equivalence (ini-v1.json:46-47).
    document = windows_document(b"[Main]\r\nName=one\r\nname=two\r\n[Other]\r\nempty=\r\n")
    query = executable(
        "ini.native-semantic-query",
        1,
        [
            OperatorCall("ini.document-sections", 1),
            OperatorCall("ini.section-name-equals", 1)
            .with_argument("name", PortableValue.string("MAIN"))
            .with_argument("comparison", PortableValue.string("ProfileEquivalent")),
            OperatorCall("ini.section-entries", 1),
        ],
    )
    result = execute_ini_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.key for match in result.matches] == ["Name", "name"]
    assert [match.node.role.value for match in result.matches] == ["IniEntry", "IniEntry"]
    assert all(match.duplicate_group is not None for match in result.matches)


def test_duplicate_group_expands_each_occurrence_in_source_order():
    # RFC 0009 §9 (docs/rfcs/0009-...:330-335): duplicate-group expands
    # each input occurrence to every same-role occurrence carrying the same
    # non-absent group identity, in source order.
    document = windows_document(b"[Main]\r\nName=one\r\nname=two\r\n[Other]\r\nempty=\r\n")
    query = executable(
        "ini.native-semantic-query",
        1,
        [
            OperatorCall("ini.all-entries", 1),
            OperatorCall("ini.entry-key-equals", 1)
            .with_argument("key", PortableValue.string("Name"))
            .with_argument("comparison", PortableValue.string("OriginalExact")),
            OperatorCall("ini.duplicate-group", 1),
        ],
    )
    result = execute_ini_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.key for match in result.matches] == ["Name", "name"]
    assert result.matches[0].duplicate_group == result.matches[1].duplicate_group

    # An occurrence without a group produces no match.
    query = executable(
        "ini.native-semantic-query",
        1,
        [
            OperatorCall("ini.all-entries", 1),
            OperatorCall("ini.entry-key-equals", 1)
            .with_argument("key", PortableValue.string("empty"))
            .with_argument("comparison", PortableValue.string("OriginalExact")),
            OperatorCall("ini.duplicate-group", 1),
        ],
    )
    result = execute_ini_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert len(result.matches) == 0


def test_section_duplicate_group_expands_sections():
    # RFC 0009 §9: duplicate-group on a section expands to the section
    # group; Windows case-equivalent headers share a group.
    document = windows_document(b"[Main]\r\nName=one\r\n[main]\r\nOther=three\r\n")
    query = executable(
        "ini.native-semantic-query",
        1,
        [
            OperatorCall("ini.document-sections", 1),
            OperatorCall("ini.section-name-equals", 1)
            .with_argument("name", PortableValue.string("Main"))
            .with_argument("comparison", PortableValue.string("OriginalExact")),
            OperatorCall("ini.duplicate-group", 1),
        ],
    )
    result = execute_ini_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.name for match in result.matches] == ["Main", "main"]


def test_entry_section_ownership_and_value_state():
    # RFC 0009 §9 operator schemas: entry-section maps to the owning
    # section; entry-value-state-is filters the exact state.
    document = windows_document(b"[Main]\r\nName=one\r\nname=two\r\n[Other]\r\nempty=\r\n")
    query = executable(
        "ini.native-semantic-query",
        1,
        [
            OperatorCall("ini.all-entries", 1),
            OperatorCall("ini.entry-value-state-is", 1).with_argument(
                "state", PortableValue.string("Empty")
            ),
            OperatorCall("ini.entry-section", 1),
        ],
    )
    result = execute_ini_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.name for match in result.matches] == ["Other"]


def test_physical_and_logical_lines_queries():
    # RFC 0009 §9: physical-lines and logical-lines expose the ordered
    # line identities with their record kinds.
    document = windows_document(b"[S]\r\nk=1\r\n")
    physical = executable(
        "ini.native-semantic-query", 1, [OperatorCall("ini.physical-lines", 1)]
    )
    result = execute_ini_query(physical, document, QUERY_LIMITS, IniCancellationToken())
    assert len(result.matches) == 2
    assert [match.kind.value for match in result.matches] == ["PhysicalLine", "PhysicalLine"]
    logical = executable(
        "ini.native-semantic-query", 1, [OperatorCall("ini.logical-lines", 1)]
    )
    result = execute_ini_query(logical, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.logical_kind.value for match in result.matches] == ["Section", "Entry"]


def test_name_filters_require_explicit_comparison_mode():
    # RFC 0009 §9: a query never silently uses case folding; an implicit
    # comparison mode is rejected at validation.
    with pytest.raises(QueryFailure) as caught:
        executable(
            "ini.native-semantic-query",
            1,
            [
                OperatorCall("ini.document-sections", 1),
                OperatorCall("ini.section-name-equals", 1)
                .with_argument("name", PortableValue.string("S"))
                .with_argument("comparison", PortableValue.string("Implicit")),
            ],
        )
    assert caught.value.kind is QueryFailureKind.INVALID_ARGUMENT
    assert caught.value.argument == "comparison"


# ---------------------------------------------------------------------------
# query.syntax-decoded-structure-order (ini-v1.json:51-52)
# ---------------------------------------------------------------------------


def test_syntax_decoded_structure_order():
    # Case query.syntax-decoded-structure-order (ini-v1.json:51-52).
    document = windows_document(b"[S]\r\nName=\" value \"\r\n")
    quote_branch = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("ini.syntax-kind-is", 1).with_argument(
            "kind", PortableValue.string("Quote")
        )
    )
    name_branch = QueryExpression(ExpressionKind.INPUT).then(
        OperatorCall("ini.syntax-text-equals", 1).with_argument(
            "text", PortableValue.string("Name")
        )
    )
    query = merged_executable(
        "ini.lossless-syntax-query", 1, [quote_branch, name_branch]
    )
    result = execute_ini_syntax_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert [match.kind.value for match in result.matches] == ["EntryKey", "Quote", "Quote"]
    assert all(match.node.role.value == "IniSyntaxPiece" for match in result.matches)
    ordinals = [match.ordinal for match in result.matches]
    assert ordinals == sorted(ordinals)


def test_syntax_text_equals_uses_decoded_scalars():
    # RFC 0009 §9: text comparison uses the decoded Unicode scalar text of
    # the exact piece span, keeping UTF-16LE queries identical to UTF-8.
    raw = b"\xff\xfe" + "[S]\r\nName=1\r\n".encode("utf-16-le")
    document = windows_document(raw)
    query = executable(
        "ini.lossless-syntax-query",
        1,
        [
            OperatorCall("ini.syntax-text-equals", 1).with_argument(
                "text", PortableValue.string("Name")
            )
        ],
    )
    result = execute_ini_syntax_query(query, document, QUERY_LIMITS, IniCancellationToken())
    assert len(result.matches) == 1
    assert result.matches[0].kind.value == "EntryKey"


# ---------------------------------------------------------------------------
# query.validation-limit-cancellation (ini-v1.json:56-57)
# ---------------------------------------------------------------------------


def test_query_validation_limit_and_cancellation():
    # Case query.validation-limit-cancellation (ini-v1.json:56-57): the
    # result budget fails with core.query.resource-limit@1 and a cursor
    # yields the completed prefix before cancellation.
    document = parse(
        b"[s]\na=1\nb=2\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    query = executable(
        "ini.native-semantic-query", 1, [OperatorCall("ini.all-entries", 1)]
    )
    with pytest.raises(QueryFailure) as caught:
        execute_ini_query(query, document, IniQueryLimits(max_results=1), IniCancellationToken())
    assert caught.value.kind is QueryFailureKind.RESOURCE_LIMIT
    assert caught.value.code == "core.query.resource-limit@1"

    cancellation = IniCancellationToken()
    result = execute_ini_query(query, document, QUERY_LIMITS, cancellation)
    assert len(result.matches) == 2


def test_query_domain_mismatch():
    # A validated query from another domain is rejected at binding time.
    with pytest.raises(QueryFailure):
        executable(
            "json.native-semantic-query",
            1,
            [OperatorCall("ini.all-entries", 1)],
        )
