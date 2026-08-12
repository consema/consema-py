"""Projection golden transcriptions (java-properties-v1.json cases).

Cases covered:

- java-properties-v1.json: projection.exact-duplicates-and-fragments
  (lines 76-79), projection.unpaired-and-recovered-atomic-failure
  (81-84), projection.explicit-jdk-table-collapse (86-89).
- Empty keys and values produce exact zero-width provenance anchors
  (Rust test empty_keys_and_values_have_exact_zero_width_provenance
  _anchors, projection.rs:959-982).
"""

from __future__ import annotations

import pytest

from consema.core.value import Kind
from consema.document.source import SourceEncoding
from consema.properties import (
    CompleteProjection,
    DuplicatePolicy,
    FailedProjectionAttempt,
    Fidelity,
    ProjectionRequest,
    PropertiesParseLimits,
    ProvenanceRelation,
    parse_reader,
    project,
)

DEFAULT_LIMITS = PropertiesParseLimits()


def parse(source: str):
    return parse_reader(source.encode("utf-8"), SourceEncoding.utf8(), DEFAULT_LIMITS)


def relation_present(complete, relation: ProvenanceRelation) -> bool:
    for entry in complete.provenance.entries:
        for origin in entry.origins:
            if origin.relation is relation:
                return True
    return False


def test_exact_duplicates_and_fragments():
    # Case projection.exact-duplicates-and-fragments
    # (java-properties-v1.json:76-79).
    document = parse("a\\ key=one\\\n two\\u0021\na\\ key=last\n")
    result = project(document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert result.fidelity is Fidelity.EXACT
    assert result.report.events == ()
    assert result.value.kind is Kind.ENTRY_MAPPING
    entries = result.value.as_entry_mapping()
    assert len(entries) == 2
    assert entries[0][0].as_string() == "a key"
    assert entries[0][1].as_string() == "onetwo!"
    assert entries[1][0].as_string() == "a key"
    assert entries[1][1].as_string() == "last"
    # Escape spellings and the fragmented value are provenance facts: one
    # provenance entry carries exactly two ValueFragment origins (the
    # continued first value; two_value_fragments fact) and EscapeDerived
    # origins exist (escape_provenance fact).
    assert relation_present(result, ProvenanceRelation.ESCAPE_DERIVED)
    assert any(
        sum(
            1
            for origin in entry.origins
            if origin.relation is ProvenanceRelation.VALUE_FRAGMENT
        )
        == 2
        for entry in result.provenance.entries
    )
    assert any(
        entry.projected.kind.value == "Association"
        for entry in result.provenance.entries
    )


def test_unpaired_and_recovered_atomic_failure():
    # Case projection.unpaired-and-recovered-atomic-failure
    # (java-properties-v1.json:81-84).
    unpaired = parse("a=ok\nb=\\uD800")
    result = project(unpaired, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "java-properties.projection.unpaired-surrogate@1"
    # The failure primary span is the b=... property, starting at byte 5
    # (unpaired_start_byte fact).
    assert result.diagnostics[0].primary.start_byte == 5
    assert result.report.events == ()

    recovered = parse("good=ok\nbad=\\u12G4")
    result = project(recovered, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "java-properties.projection.incomplete-document@1"
    assert result.report.events == ()


def test_explicit_jdk_table_collapse():
    # Case projection.explicit-jdk-table-collapse
    # (java-properties-v1.json:86-89).
    document = parse("a=first\nb=middle\na=last\n")
    unique = project(
        document, ProjectionRequest.require_object(DuplicatePolicy.REQUIRE_UNIQUE)
    )
    assert isinstance(unique, FailedProjectionAttempt)
    assert unique.diagnostics[0].code == "core.projection.target-not-applicable@1"

    first = project(
        document, ProjectionRequest.require_object(DuplicatePolicy.FIRST_WINS)
    )
    assert isinstance(first, CompleteProjection)
    assert first.fidelity is Fidelity.LOSSY
    assert len(first.report.events) == 1
    event = first.report.events[0]
    assert event.code == "java-properties.projection.duplicate-collapsed@1"
    assert event.impact is Fidelity.LOSSY
    assert first.value.kind is Kind.OBJECT
    assert [(k, v.as_string()) for k, v in first.value.as_object()] == [
        ("a", "first"),
        ("b", "middle"),
    ]
    assert relation_present(first, ProvenanceRelation.COLLAPSED)
    assert (
        first.report.events[0].policy is DuplicatePolicy.FIRST_WINS
    )
    assert (
        DuplicatePolicy.FIRST_WINS.authorizing_rule
        == "java-properties.duplicate-key.first-wins@1"
    )

    last = project(
        document, ProjectionRequest.require_object(DuplicatePolicy.LAST_WINS_JDK_TABLE)
    )
    assert isinstance(last, CompleteProjection)
    assert last.fidelity is Fidelity.LOSSY
    assert [(k, v.as_string()) for k, v in last.value.as_object()] == [
        ("b", "middle"),
        ("a", "last"),
    ]
    assert last.report.events[0].retained == document.properties[2].node
    assert (
        DuplicatePolicy.LAST_WINS_JDK_TABLE.authorizing_rule
        == "java-properties.duplicate-key.last-wins-jdk-table@1"
    )


def test_empty_keys_and_values_have_zero_width_anchors():
    # Exact zero-width provenance anchors for empty keys and values
    # (projection.rs:959-982).
    document = parse("=x\nempty=\nimplicit\n")
    result = project(document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert len(result.provenance.entries) == 10
    zero_width_fragments = [
        origin
        for entry in result.provenance.entries
        for origin in entry.origins
        if origin.span.is_empty()
        and origin.relation
        in (ProvenanceRelation.KEY_FRAGMENT, ProvenanceRelation.VALUE_FRAGMENT)
    ]
    assert len(zero_width_fragments) == 3
