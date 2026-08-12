"""Materialization intent documents: PortableValue -> toml.1.0@1 in the
toml.canonical-document@1 style (RFC 0004 §4/§6).

The materialized bytes reparses and projects back to the required portable
value (RFC 0004 §20); fidelity, the explicit mapping conversion, and
unrepresentable-value failures follow crates/consema-toml/src/
materialization.rs.
"""

from __future__ import annotations

from consema.core import Decimal, PortableValue, decimal
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationFidelity,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.toml import ProjectionRequest, ProjectionTarget, materialize


def _request(newline: NewlinePolicy = NewlinePolicy.LF) -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("toml.1.0", 1),
        MaterializationStyleId.new("toml.canonical-document", 1),
    ).with_newline(newline)


def _complete(result):
    assert isinstance(result, CompleteMaterialization), result
    return result


def test_canonical_document_round_trips_all_core_kinds():
    """Scalar, container, and temporal values round-trip: the materialized
    document reparses and projects to the identical PortableValue
    (materialization.rs:908-959)."""
    local = PortableValue.local_date_time(
        PortableValue.date(2026, 8, 4),
        PortableValue.time(12, 34, 56, decimal(123, -3)),
    )
    offset = PortableValue.offset_date_time(local, 8 * 60 * 60)
    root = PortableValue.object(
        [
            ("date", PortableValue.date(2026, 8, 4)),
            ("time", PortableValue.time(12, 34, 56, decimal(123, -3))),
            ("local", local),
            ("offset", offset),
            ("items", PortableValue.sequence([PortableValue.integer(1), PortableValue.string("two")])),
            ("nested", PortableValue.object([("enabled", PortableValue.boolean(True))])),
            ("float", PortableValue.binary_float64(0x3FF8000000000000)),  # 1.5
            ("nan", PortableValue.binary_float64(0x7FF8000000000000)),  # canonical nan
            ("zero", PortableValue.binary_float64(0x8000000000000000)),  # -0.0
        ]
    )
    result = _complete(materialize(root, _request()))
    assert result.fidelity is MaterializationFidelity.EXACT
    assert result.document.render().endswith(b"\n")
    projection = result.document.project(
        ProjectionRequest.new(ProjectionTarget.BEST_EXACT_CORE_V1)
    )
    assert projection.value == root


def test_mapping_conversion_is_explicit_and_transformed():
    """UniqueStringEntriesToObject is explicit, reportable, and reversed
    by projection (materialization.rs:961-994)."""
    mapping = PortableValue.entry_mapping(
        [
            (PortableValue.string("a"), PortableValue.boolean(True)),
            (PortableValue.string("b"), PortableValue.integer(2)),
        ]
    )
    result = _complete(
        materialize(
            mapping,
            _request(NewlinePolicy.CRLF).with_mapping_policy(
                MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT
            ),
        )
    )
    assert result.fidelity is MaterializationFidelity.TRANSFORMED
    assert len(result.report.events) == 1
    assert result.report.events[0].code == "core.materialization.mapping-transformed@1"
    assert result.document.render() == b'"a" = true\r\n"b" = 2\r\n'
    projection = result.document.project(
        ProjectionRequest.new(ProjectionTarget.BEST_EXACT_CORE_V1)
    )
    assert projection.value == PortableValue.object(
        [("a", PortableValue.boolean(True)), ("b", PortableValue.integer(2))]
    )


def test_unrepresentable_values_fail_without_document():
    """Integer overflow, implicit mapping conversion, duplicate mapping
    keys, and non-canonical NaN payloads fail with no partial output
    (materialization.rs:996-1113)."""
    from consema.document.materialization import MaterializationFailure

    too_large = PortableValue.object(
        [("value", PortableValue.integer(2**63))]
    )
    result = materialize(too_large, _request())
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind.value == "unrepresentable"

    mapping = PortableValue.entry_mapping(
        [(PortableValue.string("x"), PortableValue.boolean(True))]
    )
    result = materialize(mapping, _request())
    assert isinstance(result, FailedMaterializationAttempt)

    duplicate = PortableValue.entry_mapping(
        [
            (PortableValue.string("x"), PortableValue.boolean(True)),
            (PortableValue.string("x"), PortableValue.boolean(False)),
        ]
    )
    result = materialize(
        duplicate,
        _request().with_mapping_policy(MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT),
    )
    assert isinstance(result, FailedMaterializationAttempt)

    nan_root = PortableValue.object(
        [("nan", PortableValue.binary_float64(0x7FF8000000000001))]
    )
    result = materialize(nan_root, _request())
    assert isinstance(result, FailedMaterializationAttempt)

    empty = PortableValue.object([])
    result = materialize(empty, _request(NewlinePolicy.NONE))
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.kind.value == "unsupported-newline"


def test_request_contract_enforced():
    """Unsupported profile, style, and encoding fail the request."""
    from consema.document.materialization import FailedMaterializationAttempt
    from consema.document.source import SourceEncoding

    empty = PortableValue.object([])
    wrong_profile = MaterializationRequest.new(
        ProfileId.new("json.strict", 1),
        MaterializationStyleId.new("toml.canonical-document", 1),
    )
    assert isinstance(materialize(empty, wrong_profile), FailedMaterializationAttempt)

    wrong_style = MaterializationRequest.new(
        ProfileId.new("toml.1.0", 1),
        MaterializationStyleId.new("json.canonical-compact", 1),
    )
    assert isinstance(materialize(empty, wrong_style), FailedMaterializationAttempt)

    utf16 = _request().with_encoding(SourceEncoding.utf16le())
    assert isinstance(materialize(empty, utf16), FailedMaterializationAttempt)


def test_provenance_covers_every_emitted_value_and_association():
    """Materialization provenance maps portable input locations to target
    origins within the configured limits (RFC 0004 §8)."""
    root = PortableValue.object(
        [
            ("enabled", PortableValue.boolean(True)),
            ("items", PortableValue.sequence([PortableValue.integer(1)])),
        ]
    )
    result = _complete(materialize(root, _request()))
    provenance = result.provenance
    assert provenance.entries
    for entry in provenance.entries:
        assert entry.outputs
        for origin in entry.outputs:
            assert origin.snapshot == result.document.snapshot_identity()
            assert origin.node.snapshot == result.document.snapshot_identity()
            assert origin.span.snapshot == result.document.snapshot_identity()
