"""Materialization golden transcriptions (java-properties-v1.json cases).

Cases covered:

- java-properties-v1.json: materialization.canonical-styles-encodings-
  and-closure (lines 91-99), materialization.atomic-failures-and-limits
  (101-104).
- Closure (RFC 0010 section 12): canonical output reparses under the
  exact target profile and reprojects to the identical PortableValue.
"""

from __future__ import annotations

import pytest

from consema.core.value import EntryMappingBuilder, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding, WindowsCodePage
from consema.properties import (
    CompleteProjection,
    DuplicatePolicy,
    ProjectionRequest,
    materialize,
    project,
)

READER_PROFILE = ProfileId.new("java-properties.reader", 1)
LATIN1_PROFILE = ProfileId.new("java-properties.latin1", 1)
READER_STYLE = MaterializationStyleId.new("java-properties.reader-canonical", 1)
LATIN1_STYLE = MaterializationStyleId.new("java-properties.latin1-canonical", 1)


def reader_request() -> MaterializationRequest:
    return MaterializationRequest.new(READER_PROFILE, READER_STYLE)


def latin1_request() -> MaterializationRequest:
    return MaterializationRequest.new(LATIN1_PROFILE, LATIN1_STYLE).with_encoding(
        SourceEncoding.latin1()
    )


def mapping(entries: list[tuple[str, str]]) -> PortableValue:
    builder = EntryMappingBuilder()
    for key, value in entries:
        builder.push(PortableValue.string(key), PortableValue.string(value))
    return builder.build()


def test_reader_canonical_escapes_structure_and_controls():
    # Case materialization.canonical-styles-encodings-and-closure, reader
    # sample (java-properties-v1.json:93).
    value = mapping([(" a#", "  v:=!\\\t\b值")])
    result = materialize(value, reader_request())
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render() == "\\ a\\#=\\ \\ v\\:\\=\\!\\\\\\t\\u0008值\n".encode(
        "utf-8"
    )
    assert result.fidelity is MaterializationFidelity.EXACT
    assert result.report.events == ()
    # Closure: the output reprojects to the identical PortableValue.
    closure = project(result.document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(closure, CompleteProjection)
    assert closure.value == value
    assert len(result.provenance.entries) == 4


def test_latin1_canonical_uses_uppercase_utf16_escapes_without_bom():
    # Case materialization.canonical-styles-encodings-and-closure, latin1
    # sample (java-properties-v1.json:94).
    value = mapping([("emoji😀", "café")])
    result = materialize(value, latin1_request().with_newline(NewlinePolicy.CRLF))
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render() == "emoji\\uD83D\\uDE00=caf\\u00E9\\u007F\r\n".encode(
        "utf-8"
    )
    assert result.document.source.encoding_facts().selected == SourceEncoding.latin1()
    assert result.document.source.encoding_facts().bom is None


def test_reader_utf16_and_strict_code_pages_are_explicit():
    # Case materialization.canonical-styles-encodings-and-closure, utf16be
    # sample (java-properties-v1.json:95).
    unicode = mapping([("名", "值")])
    utf16 = reader_request().with_encoding(SourceEncoding.utf16be()).with_newline(
        NewlinePolicy.CRLF
    )
    result = materialize(unicode, utf16)
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render()[:2] == b"\xfe\xff"
    assert (
        result.document.source.encoding_facts().selected == SourceEncoding.utf16be()
    )
    # The decoded text is the BOM plus "名=值\r\n".
    assert result.document.source.decoded_text() == "﻿名=值\r\n"


def test_reader_strict_cp1252_encoding():
    # Case materialization.canonical-styles-encodings-and-closure, cp1252
    # sample (java-properties-v1.json:96): "name=caf\xE9\n" in the strict
    # code page, and the unrepresentable-scalar rejection.
    #
    # BLOCKED by a document-domain defect: consema/document/source.py:838
    # unpacks a two-tuple from the Python 3.12 incremental decoder, which
    # returns the decoded string only. The closure reparse depends on
    # SourceSnapshot.from_raw; the fix belongs to the document agent.
    # Verification item, not a claim.
    cp1252 = WindowsCodePage.from_number(1252)
    cp_request = reader_request().with_encoding(SourceEncoding.windows_code_page(cp1252))
    latin = mapping([("name", "café")])
    result = materialize(latin, cp_request)
    assert isinstance(result, CompleteMaterialization)
    assert result.document.render().hex() == "6e616d653d636166e90a"
    # 名 cannot be represented by the strict code page.
    failed = materialize(mapping([("名", "值")]), cp_request)
    assert isinstance(failed, FailedMaterializationAttempt)
    assert failed.failure.kind is MaterializationFailureKind.UNSUPPORTED_ENCODING


def test_duplicate_entry_mapping_and_unique_object_close_exactly():
    # Duplicate-preserving EntryMapping materialization and Object closure
    # (materialization.rs:760-796).
    duplicate = mapping([("a", "first"), ("a", "last")])
    result = materialize(duplicate, reader_request())
    assert isinstance(result, CompleteMaterialization)
    assert len(result.document.properties) == 2

    from consema.core.value import ObjectBuilder

    builder = ObjectBuilder()
    builder.insert("a", PortableValue.string("one"))
    builder.insert("b", PortableValue.string("two"))
    object_value = builder.build()
    result = materialize(object_value, reader_request())
    assert isinstance(result, CompleteMaterialization)
    closure = project(
        result.document,
        ProjectionRequest.require_object(DuplicatePolicy.REQUIRE_UNIQUE),
    )
    assert isinstance(closure, CompleteProjection)
    assert closure.value == object_value


def test_invalid_requests_shapes_and_limits_fail_atomically():
    # Case materialization.atomic-failures-and-limits
    # (java-properties-v1.json:101-104).
    value = mapping([("key", "value")])
    # A scalar value cannot become a property document.
    failed = materialize(PortableValue.string("scalar"), reader_request())
    assert isinstance(failed, FailedMaterializationAttempt)
    assert failed.failure.kind is MaterializationFailureKind.UNREPRESENTABLE
    assert failed.failure.code == "core.materialization.unrepresentable@1"
    # The Latin-1 profile requires the Latin-1 encoding.
    failed = materialize(
        value, latin1_request().with_encoding(SourceEncoding.utf8())
    )
    assert isinstance(failed, FailedMaterializationAttempt)
    assert failed.failure.kind is MaterializationFailureKind.UNSUPPORTED_ENCODING
    assert failed.failure.code == "core.materialization.unsupported-encoding@1"
    # Newline is required.
    failed = materialize(value, reader_request().with_newline(NewlinePolicy.NONE))
    assert isinstance(failed, FailedMaterializationAttempt)
    assert failed.failure.kind is MaterializationFailureKind.UNSUPPORTED_NEWLINE
    # The five materialization limits; the report limit never fires
    # because this materializer publishes no report events
    # (limit_outcomes fact, java-properties-v1.json:103).
    outcomes = []
    for limits in [
        MaterializationLimits(max_input_nodes=1),
        MaterializationLimits(max_output_bytes=2),
        MaterializationLimits(max_depth=0),
        MaterializationLimits(max_report_entries=0),
        MaterializationLimits(max_provenance_entries=1),
    ]:
        result = materialize(value, reader_request().with_limits(limits))
        if isinstance(result, CompleteMaterialization):
            outcomes.append("Complete")
        else:
            assert result.failure.code == "core.materialization.resource-limit@1"
            outcomes.append("Failed")
    assert outcomes == ["Failed", "Failed", "Failed", "Complete", "Failed"]
