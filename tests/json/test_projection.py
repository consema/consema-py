"""Projection golden transcriptions and fidelity checks.

Cases covered:

- json-family-v2.json: json5.projection.duplicates-nonfinite (126-130),
  json5.projection.old-target-rejected (132-136).
- v1.json: projection.best-exact-duplicate-mapping (89-93),
  projection.object-reject-duplicates (95-99), projection.object-last-wins
  (101-105), projection.object-key-provenance (155-159).

Projection fidelity check: project -> materialize -> reparse -> project
must reproduce the identical PortableValue (RFC 0004 §20 materialization
closure; the JSON materializer reprojects before completion).
"""

from __future__ import annotations

from consema.core.value import Kind, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    MaterializationRequest,
    NewlinePolicy,
)
from consema.json import (
    DuplicateKeyPolicy,
    Fidelity,
    JsonProfile,
    ProjectionRequestBuilder,
    ProjectionTarget,
    materialize,
    parse,
    project,
)
from consema.json.parser import BITS_POSITIVE_INFINITY, BITS_NEGATIVE_NAN

DEFAULT_LIMITS = ParseLimits()


def test_json5_projection_duplicates_nonfinite():
    # Case json5.projection.duplicates-nonfinite (json-family-v2.json:126-130).
    document = parse(b"{a:Infinity,a:-NaN}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.JSON5_BEST_EXACT_CORE_V1).build(),
    )
    assert hasattr(result, "value")
    assert result.fidelity is Fidelity.TRANSFORMED
    value = result.value
    assert value.kind is Kind.ENTRY_MAPPING
    entries = value.as_entry_mapping()
    assert len(entries) == 2
    assert [entry[0].as_string() for entry in entries] == ["a", "a"]
    bits = [entry[1].as_binary_float64() for entry in entries]
    assert bits == [BITS_POSITIVE_INFINITY, BITS_NEGATIVE_NAN]


def test_json5_projection_old_target_rejected():
    # Case json5.projection.old-target-rejected (json-family-v2.json:132-136).
    document = parse(b"{a:1}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    assert not hasattr(result, "value")
    assert result.diagnostics[0].code == "core.projection.target-not-applicable@1"


def test_best_exact_duplicate_mapping():
    # Case projection.best-exact-duplicate-mapping (v1.json:89-93).
    document = parse(b'{"a":1,"a":2}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    assert result.fidelity is Fidelity.TRANSFORMED
    value = result.value
    assert value.kind is Kind.ENTRY_MAPPING
    entries = value.as_entry_mapping()
    assert len(entries) == 2
    assert [entry[0].as_string() for entry in entries] == ["a", "a"]
    assert [entry[1].as_integer() for entry in entries] == [1, 2]
    # association_origins: 2 (one per entry association)
    association_origins = sum(
        1
        for entry in result.provenance.entries
        if entry.projected.kind.value == "Association"
        and entry.projected.association.role.value == "EntryMappingEntry"
    )
    assert association_origins == 2


def test_object_reject_duplicates():
    # Case projection.object-reject-duplicates (v1.json:95-99).
    document = parse(b'{"a":1,"a":2}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.PROJECT_AS_OBJECT_V1)
        .global_duplicate_policy(DuplicateKeyPolicy.REJECT)
        .build(),
    )
    assert not hasattr(result, "value")
    assert result.diagnostics[0].code == "json.projection.duplicate-keys@1"


def test_object_last_wins():
    # Case projection.object-last-wins (v1.json:101-105).
    document = parse(b'{"a":1,"a":2}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.PROJECT_AS_OBJECT_V1)
        .global_duplicate_policy(DuplicateKeyPolicy.LAST_WINS)
        .build(),
    )
    assert result.fidelity is Fidelity.LOSSY
    value = result.value
    assert value.kind is Kind.OBJECT
    assert value.as_object() == (("a", PortableValue.integer(2)),)
    events = [event for event in result.report.events if event.kind.value == "DuplicateCollapsed"]
    assert len(events) == 1


def test_object_key_provenance():
    # Case projection.object-key-provenance (v1.json:155-159).
    document = parse(b'{"a":1,"b":2}', JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    result = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.PROJECT_AS_OBJECT_V1)
        .global_duplicate_policy(DuplicateKeyPolicy.REJECT)
        .build(),
    )
    assert result.fidelity is Fidelity.EXACT
    roles = [
        entry.projected.association.role.value
        for entry in result.provenance.entries
        if entry.projected.kind.value == "Association"
    ]
    assert roles.count("ObjectKey") == 2
    assert roles.count("ObjectEntry") == 2


def test_projection_fidelity_round_trip():
    # Projection fidelity check: project -> materialize -> reparse ->
    # reproject produces the identical PortableValue (RFC 0004 §20).
    source = '{"a":[1,2.5,true,null],"b":"text","c":{"x":-3}}'
    document = parse(source.encode("utf-8"), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    first = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    assert first.fidelity is Fidelity.EXACT
    request = MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("json.canonical-compact", 1),
    ).with_newline(NewlinePolicy.NONE)
    result = materialize(first.value, request)
    assert hasattr(result, "document")
    reparsed = parse(result.document.render(), JsonProfile.STRICT_V1, DEFAULT_LIMITS)
    second = project(
        reparsed,
        ProjectionRequestBuilder(ProjectionTarget.BEST_EXACT_CORE_V1).build(),
    )
    assert second.value == first.value


def test_projection_json5_nonfinite_round_trip():
    # JSON5 projection preserves frozen non-finite bits through
    # materialization (RFC 0005 §9 closure).
    document = parse(b"{a:Infinity,a:-NaN}", JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    first = project(
        document,
        ProjectionRequestBuilder(ProjectionTarget.JSON5_BEST_EXACT_CORE_V1).build(),
    )
    request = MaterializationRequest.new(
        ProfileId.new("json5.standard", 1),
        MaterializationStyleId.new("json5.canonical-compact", 1),
    ).with_newline(NewlinePolicy.NONE)
    result = materialize(first.value, request)
    assert result.document.render() == b'{"a":Infinity,"a":-NaN}'
    reparsed = parse(result.document.render(), JsonProfile.JSON5_STANDARD_V1, DEFAULT_LIMITS)
    second = project(
        reparsed,
        ProjectionRequestBuilder(ProjectionTarget.JSON5_BEST_EXACT_CORE_V1).build(),
    )
    assert second.value == first.value
