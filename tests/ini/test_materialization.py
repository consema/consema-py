"""Materialization golden transcriptions and atomic failure algebra (INI).

Cases covered here (conformance/vectors/ini-v1.json, suite
"consema.ini.conformance@1"):

- materialization.all-canonical-styles (lines 76-81),
  materialization.atomic-failures-and-limits (85-86).

RFC 0009 §11 contract facts pinned here: the canonical styles
ini.portable-canonical@1 / ini.windows-canonical@1 /
ini.python-configparser-canonical@1 with exact request combinations
(portable requires UTF-8 plus LF; Windows requires UTF-16LE plus BOM or an
explicit registered Windows code page, plus CRLF; Python requires one
non-Binary registered text encoding plus LF), strict encoding, and
closure: output reparses under the exact target profile and reprojects to
the identical PortableValue before success (docs/rfcs/0009-ini-family-
profiles-v1.md:393-435). Failure returns no Document and no partial bytes
(RFC 0004 §7).
"""

from __future__ import annotations

from consema.core.value import EntryMappingBuilder, PortableValue
from consema.document.ids import ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationFailureKind,
    MaterializationLimits,
    MaterializationRequest,
    MaterializationStyleId,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding
from consema.ini import materialize


def nested_entry_mapping(sections: list[tuple[str, list[tuple[str, str]]]]):
    outer = EntryMappingBuilder()
    for section, entries in sections:
        inner = EntryMappingBuilder()
        for key, value in entries:
            inner.push(PortableValue.string(key), PortableValue.string(value))
        outer.push(PortableValue.string(section), inner.build())
    return outer.build()


def portable_request() -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("ini.portable", 1),
        MaterializationStyleId.new("ini.portable-canonical", 1),
    )


def windows_request() -> MaterializationRequest:
    return (
        MaterializationRequest.new(
            ProfileId.new("ini.windows", 1),
            MaterializationStyleId.new("ini.windows-canonical", 1),
        )
        .with_encoding(SourceEncoding.utf16le())
        .with_newline(NewlinePolicy.CRLF)
    )


def python_request() -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("ini.python-configparser", 1),
        MaterializationStyleId.new("ini.python-configparser-canonical", 1),
    )


# ---------------------------------------------------------------------------
# materialization.all-canonical-styles (ini-v1.json:76-81)
# ---------------------------------------------------------------------------


def test_portable_canonical_style():
    # Case materialization.all-canonical-styles (ini-v1.json:76-81).
    value = nested_entry_mapping([("main", [("key", "value"), ("empty", "")])])
    result = materialize(value, portable_request())
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render() == b"[main]\nkey=value\nempty=\n"
    assert result.fidelity.value == "Exact"
    assert result.document.formation_status().value == "Complete"


def test_windows_canonical_style():
    # Case materialization.all-canonical-styles (ini-v1.json:76-81).
    value = nested_entry_mapping([("Main", [("quoted", " value "), ("plain", "value")])])
    result = materialize(value, windows_request())
    assert isinstance(result, CompleteMaterialization)
    assert result.document.source.decoded_text() == (
        "﻿[Main]\r\nquoted=\" value \"\r\nplain=value\r\n"
    )
    assert result.document.source.encoding_facts().selected == SourceEncoding.utf16le()
    assert result.document.entries[0].value == " value "
    assert result.fidelity.value == "Exact"


def test_python_canonical_style():
    # Case materialization.all-canonical-styles (ini-v1.json:76-81).
    value = nested_entry_mapping(
        [("DEFAULT", [("raw", "%(name)s"), ("multi", "first\n\nthird")])]
    )
    result = materialize(value, python_request())
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render() == b"[DEFAULT]\nraw = %(name)s\nmulti = first\n\n    third\n"
    assert result.document.entries[1].value == "first\n\nthird"
    assert any(len(entry.outputs) > 1 for entry in result.provenance.entries)


def test_materialization_closure_reprojects_identically():
    # RFC 0009 §11 (docs/rfcs/0009-...:432-435): all styles reparse under
    # the exact target profile and reproject under the request's policy
    # before success.
    for value, request in (
        (nested_entry_mapping([("main", [("key", "value")])]), portable_request()),
        (
            nested_entry_mapping([("Main", [("plain", "value")])]),
            windows_request(),
        ),
        (
            nested_entry_mapping([("DEFAULT", [("raw", "%(name)s")])]),
            python_request(),
        ),
    ):
        result = materialize(value, request)
        assert isinstance(result, CompleteMaterialization)
        assert result.fidelity.value == "Exact"


# ---------------------------------------------------------------------------
# materialization.atomic-failures-and-limits (ini-v1.json:85-86)
# ---------------------------------------------------------------------------


def test_non_mapping_root_fails_unrepresentable():
    # Case materialization.atomic-failures-and-limits (ini-v1.json:85-86):
    # a scalar input fails with core.materialization.unrepresentable@1.
    result = materialize(PortableValue.string("x"), portable_request())
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind is MaterializationFailureKind.UNREPRESENTABLE


def test_limit_outcomes_are_atomic():
    # Case materialization.atomic-failures-and-limits (ini-v1.json:85-86):
    # max_input_nodes / max_output_bytes / max_depth /
    # max_provenance_entries fail; max_report_entries is irrelevant to the
    # success path and stays Complete. All failures return no Document.
    value = nested_entry_mapping([("s", [("key", "value")])])
    outcomes = []
    for limits in (
        MaterializationLimits(max_input_nodes=1),
        MaterializationLimits(max_output_bytes=2),
        MaterializationLimits(max_depth=0),
        MaterializationLimits(max_report_entries=0),
        MaterializationLimits(max_provenance_entries=1),
    ):
        result = materialize(
            value,
            MaterializationRequest(
                target_profile=ProfileId.new("ini.portable", 1),
                style=MaterializationStyleId.new("ini.portable-canonical", 1),
                limits=limits,
            ),
        )
        outcomes.append("Complete" if isinstance(result, CompleteMaterialization) else "Failed")
        if isinstance(result, FailedMaterializationAttempt):
            assert result.failure.code == "core.materialization.resource-limit@1"
    assert outcomes == ["Failed", "Failed", "Failed", "Complete", "Failed"]


def test_windows_object_cannot_fabricate_case_collisions():
    # RFC 0009 §11 (docs/rfcs/0009-...:409-411): Object input cannot
    # fabricate a Windows case-equivalent collision even when its spellings
    # are distinct.
    from consema.core.value import ObjectBuilder

    inner = ObjectBuilder()
    inner.insert("Name", PortableValue.string("one"))
    inner.insert("name", PortableValue.string("two"))
    outer = ObjectBuilder()
    outer.insert("s", inner.build())
    result = materialize(outer.build(), windows_request())
    assert isinstance(result, FailedMaterializationAttempt)

    inner = ObjectBuilder()
    inner.insert("Name", PortableValue.string("one"))
    outer = ObjectBuilder()
    outer.insert("s", inner.build())
    result = materialize(outer.build(), windows_request())
    assert isinstance(result, CompleteMaterialization)


def test_python_trailing_empty_value_line_is_unrepresentable():
    # RFC 0009 §11 (docs/rfcs/0009-...:412-414): Python stored values whose
    # terminal empty line would be normalized away by the frozen parser are
    # unrepresentable rather than silently changed.
    value = nested_entry_mapping([("s", [("value", "line\n")])])
    result = materialize(value, python_request())
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind is MaterializationFailureKind.INVALID_REQUEST


def test_unsupported_style_newline_and_encoding_fail_atomically():
    # RFC 0009 §11 (docs/rfcs/0009-...:401-406): the request combinations
    # are exact.
    from consema.document.materialization import MaterializationFailureKind as Kind

    wrong_style = MaterializationRequest(
        target_profile=ProfileId.new("ini.portable", 1),
        style=MaterializationStyleId.new("ini.windows-canonical", 1),
    )
    result = materialize(nested_entry_mapping([("s", [("k", "v")])]), wrong_style)
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind is Kind.UNSUPPORTED_STYLE

    wrong_newline = portable_request().with_newline(NewlinePolicy.CRLF)
    result = materialize(nested_entry_mapping([("s", [("k", "v")])]), wrong_newline)
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind is Kind.UNSUPPORTED_NEWLINE

    wrong_encoding = portable_request().with_encoding(SourceEncoding.latin1())
    result = materialize(nested_entry_mapping([("s", [("k", "v")])]), wrong_encoding)
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind is Kind.UNSUPPORTED_ENCODING
