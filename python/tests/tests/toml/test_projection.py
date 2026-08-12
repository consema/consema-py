"""Projection intent documents: explicit projection onto
toml.best-exact-core@1 with provenance.

Vector cases transcribed: toml.projection.all-core-kinds,
toml.projection.provenance, toml.projection.reject-leap-second
(conformance/vectors/toml-v1.json); RFC 0001 §5 mapping table; the
completion algebra and failure codes per crates/consema-toml/src/
projection.rs.
"""

from __future__ import annotations

import pytest

from consema.core import Kind, PortableValue
from consema.document.limits import ParseLimits
from consema.toml import (
    CompleteProjection,
    FailedProjectionAttempt,
    Fidelity,
    ProjectionLimits,
    ProjectionRequest,
    ProjectionTarget,
    TomlProfile,
    parse,
)


def _document(source: bytes):
    return parse(source, TomlProfile.TOML10_V1, ParseLimits())


def _project(document, limits=None):
    request = ProjectionRequest.new(ProjectionTarget.BEST_EXACT_CORE_V1)
    if limits is not None:
        request = request.with_limits(limits)
    return document.project(request)


def test_vector_all_core_kinds(fixture_bytes):
    """toml.projection.all-core-kinds: every TOML value category projects
    exactly to its core kind; the root is an Object; fidelity is Exact."""
    document = _document(fixture_bytes("toml/all-values.toml"))
    result = _project(document)
    assert isinstance(result, CompleteProjection)
    assert result.fidelity is Fidelity.EXACT
    assert result.report.events == ()
    root = result.value
    assert root.kind is Kind.OBJECT
    entries = dict(root.as_object())
    assert entries["title"].kind is Kind.STRING
    assert entries["enabled"].kind is Kind.BOOLEAN
    assert entries["integer"].kind is Kind.INTEGER
    assert entries["hex"].kind is Kind.INTEGER
    assert entries["float"].kind is Kind.BINARY_FLOAT64
    assert entries["positive_infinity"].kind is Kind.BINARY_FLOAT64
    assert entries["not_a_number"].kind is Kind.BINARY_FLOAT64
    assert entries["local_date"].kind is Kind.DATE
    assert entries["local_time"].kind is Kind.TIME
    assert entries["local_date_time"].kind is Kind.LOCAL_DATE_TIME
    assert entries["offset_date_time"].kind is Kind.OFFSET_DATE_TIME
    assert entries["ports"].kind is Kind.SEQUENCE
    assert entries["point"].kind is Kind.OBJECT


def test_projection_float_bits_are_exact():
    """-0.0 projects to the exact binary64 bit pattern (RFC 0001 §5:
    Float keeps its IEEE-754 binary64 bit pattern)."""
    document = _document(b"positive = 0.0\nnegative = -0.0\n")
    result = _project(document)
    assert isinstance(result, CompleteProjection)
    entries = dict(result.value.as_object())
    assert entries["positive"].as_binary_float64() == 0x0000000000000000
    assert entries["negative"].as_binary_float64() == 0x8000000000000000


def test_vector_provenance():
    """toml.projection.provenance: every projected value and object
    association maps back to snapshot-bound origins."""
    document = _document(b"point = { x = 1, y = 2 }\n")
    result = _project(document)
    assert isinstance(result, CompleteProjection)
    provenance = result.provenance
    assert provenance.entries
    for entry in provenance.entries:
        for origin in entry.origins:
            assert origin.snapshot == document.snapshot_identity()
            assert origin.node.snapshot == document.snapshot_identity()
            assert origin.span.snapshot == document.snapshot_identity()
    # the object association of the inline table entry is present
    association_entries = [
        entry
        for entry in provenance.entries
        if entry.projected.kind.value == "Association"
    ]
    assert association_entries


def test_vector_reject_leap_second():
    """toml.projection.reject-leap-second: 23:59:60 parses (TOML allows a
    leap second) but the projection fails whole with
    toml.projection.unrepresentable-datetime@1 and no partial value."""
    document = _document(b"time = 23:59:60\n")
    result = _project(document)
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "toml.projection.unrepresentable-datetime@1"
    assert result.partial_analysis == ()


def test_projection_resource_limit_fails_without_partial_value():
    """core.projection.resource-limit@1 with the limit argument; no
    partial value is ever returned."""
    document = _document(b"a = 1\nb = 2\n")
    result = _project(document, limits=ProjectionLimits(max_value_nodes=1))
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "core.projection.resource-limit@1"
    assert result.diagnostics[0].arguments["limit"] == "max_value_nodes"


def test_array_of_tables_projects_to_sequence_of_objects():
    """ArrayOfTables -> Sequence<Object> (RFC 0001 §5)."""
    document = _document(b"[[products]]\nname = \"one\"\n\n[[products]]\nname = \"two\"\n")
    result = _project(document)
    assert isinstance(result, CompleteProjection)
    products = dict(result.value.as_object())["products"]
    assert products.kind is Kind.SEQUENCE
    elements = products.as_sequence()
    assert len(elements) == 2
    assert all(element.kind is Kind.OBJECT for element in elements)


def test_projection_is_explicit_two_stage():
    """Projection is an explicit operation; the Document model itself
    keeps TOML tables distinct from JSON objects (RFC 0001 §1)."""
    document = _document(b"service.name = \"catalog\"\n")
    service = document.root().table_entries()[0].item()
    assert service.kind().value == "DottedTable"
    result = _project(document)
    assert isinstance(result, CompleteProjection)
    assert dict(result.value.as_object())["service"].kind is Kind.OBJECT
