"""MaterializationRequest, limits, provenance, and failure-code tests.

Contract: RFC 0004 §3/§7/§8 (docs/rfcs/0004-materialization-conversion-and-
structural-edit-v1.md:56-94, 170-217); arbitration
crates/consema-document/src/materialization.rs:95-203, 282-391; codes
crates/consema-protocol/src/error_registry.rs:556-604.
"""

from __future__ import annotations

import pytest

from consema.document import (
    DocumentAuthority,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationInputLocation,
    MaterializationInputLocationKind,
    MaterializationLimits,
    MaterializationProvenanceEntry,
    MaterializationProvenanceMap,
    MaterializationRelation,
    MaterializationReport,
    MaterializationRequest,
    MaterializationStyleId,
    MaterializedOrigin,
    MappingPolicy,
    NewlinePolicy,
    NodeRole,
    ProfileId,
    RepresentabilityPolicy,
    SourceEncoding,
)


def test_request_defaults_and_policies() -> None:
    """new() creates a strict request with UTF-8, LF, Object-only, and
    ExactOnly defaults (materialization.rs:122-132)."""
    request = MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("json.canonical-pretty", 1),
    )
    assert request.target_profile == ProfileId.new("json.strict", 1)
    assert request.style == MaterializationStyleId.new("json.canonical-pretty", 1)
    assert request.encoding == SourceEncoding.utf8()
    assert request.newline is NewlinePolicy.LF
    assert request.mapping_policy is MappingPolicy.REQUIRE_OBJECT
    assert request.representability is RepresentabilityPolicy.EXACT_ONLY
    assert request.limits == MaterializationLimits()

    changed = (
        request.with_encoding(SourceEncoding.utf16le())
        .with_newline(NewlinePolicy.CRLF)
        .with_mapping_policy(MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT)
        .with_limits(MaterializationLimits(max_output_bytes=10))
    )
    assert changed.encoding == SourceEncoding.utf16le()
    assert changed.newline is NewlinePolicy.CRLF
    assert changed.mapping_policy is MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT
    assert changed.limits.max_output_bytes == 10
    assert request.encoding == SourceEncoding.utf8()  # immutable


def test_newline_bytes() -> None:
    assert NewlinePolicy.NONE.bytes == b""
    assert NewlinePolicy.LF.bytes == b"\n"
    assert NewlinePolicy.CRLF.bytes == b"\r\n"


def test_representability_is_closed_v1() -> None:
    assert list(RepresentabilityPolicy) == [RepresentabilityPolicy.EXACT_ONLY]


def test_report_enforces_event_limit() -> None:
    report = MaterializationReport.new([], MaterializationLimits())
    assert report.events == ()
    with pytest.raises(MaterializationFailure) as caught:
        MaterializationReport.new([object()] * 2, MaterializationLimits(max_report_entries=1))
    assert caught.value.kind is MaterializationFailureKind.RESOURCE_LIMIT
    assert caught.value.code == "core.materialization.resource-limit@1"


def test_provenance_is_target_bound_and_limited() -> None:
    """Provenance origins must all bind to the target snapshot
    (materialization.rs:289-318; RFC 0004 §8)."""
    target = DocumentAuthority.fresh()
    origin = MaterializedOrigin(
        snapshot=target.identity,
        node=target.node_ref(0, NodeRole.VALUE),
        span=target.span(0, 1),
        relation=MaterializationRelation.DIRECT,
    )
    entry = MaterializationProvenanceEntry(
        input=MaterializationInputLocation.association("(root, 0)"),
        outputs=(origin,),
    )
    mapping = MaterializationProvenanceMap.new([entry], target.identity, MaterializationLimits())
    assert mapping.entries == (entry,)

    with pytest.raises(MaterializationFailure) as caught:
        MaterializationProvenanceMap.new(
            [
                MaterializationProvenanceEntry(
                    input=MaterializationInputLocation.value("(root)"), outputs=()
                )
            ],
            target.identity,
            MaterializationLimits(),
        )
    assert caught.value.kind is MaterializationFailureKind.INVALID_REQUEST
    assert caught.value.code == "core.materialization.invalid-request@1"

    foreign = MaterializedOrigin(
        snapshot=DocumentAuthority.fresh().identity,
        node=target.node_ref(0, NodeRole.VALUE),
        span=target.span(0, 1),
        relation=MaterializationRelation.GENERATED,
    )
    with pytest.raises(MaterializationFailure) as caught:
        MaterializationProvenanceMap.new(
            [
                MaterializationProvenanceEntry(
                    input=MaterializationInputLocation.value("(root)"),
                    outputs=(foreign,),
                )
            ],
            target.identity,
            MaterializationLimits(),
        )
    assert caught.value.kind is MaterializationFailureKind.INVALID_REQUEST

    with pytest.raises(MaterializationFailure) as caught:
        MaterializationProvenanceMap.new(
            [entry], target.identity, MaterializationLimits(max_provenance_entries=1)
        )
    assert caught.value.kind is MaterializationFailureKind.RESOURCE_LIMIT


def test_failure_kind_code_mapping() -> None:
    """The full failure-to-code mapping (materialization.rs:379-390;
    error_registry.rs:556-604)."""
    expected = {
        MaterializationFailureKind.INVALID_REQUEST: "core.materialization.invalid-request@1",
        MaterializationFailureKind.UNSUPPORTED_PROFILE: "core.materialization.unsupported-profile@1",
        MaterializationFailureKind.UNSUPPORTED_STYLE: "core.materialization.unsupported-style@1",
        MaterializationFailureKind.UNSUPPORTED_ENCODING: "core.materialization.unsupported-encoding@1",
        MaterializationFailureKind.UNSUPPORTED_NEWLINE: "core.materialization.unsupported-newline@1",
        MaterializationFailureKind.UNREPRESENTABLE: "core.materialization.unrepresentable@1",
        MaterializationFailureKind.RESOURCE_LIMIT: "core.materialization.resource-limit@1",
        MaterializationFailureKind.FORMATION_FAILED: "core.materialization.formation-failed@1",
    }
    for kind, code in expected.items():
        assert MaterializationFailure(kind).code == code


def test_fidelity_is_closed_two_value() -> None:
    assert list(MaterializationFidelity) == [
        MaterializationFidelity.EXACT,
        MaterializationFidelity.TRANSFORMED,
    ]


def test_input_location_kinds() -> None:
    value = MaterializationInputLocation.value("(root)")
    association = MaterializationInputLocation.association("(root, 0)")
    assert value.kind is MaterializationInputLocationKind.VALUE
    assert association.kind is MaterializationInputLocationKind.ASSOCIATION
