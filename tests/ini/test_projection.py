"""Projection golden transcriptions and provenance facts (INI).

Cases covered here (conformance/vectors/ini-v1.json, suite
"consema.ini.conformance@1"):

- projection.exact-duplicate-entry-mapping (lines 61-62),
  projection.explicit-object-collapse (66-67),
  projection.fragmented-value-provenance (71-72),
  resource.projection-limit-matrix (131-133).

RFC 0009 §10 contract facts pinned here: the default exact projection is
``ini.projection.best-exact-entry-mapping@1`` producing a nested
EntryMapping in source order with duplicate spellings preserved
(docs/rfcs/0009-ini-family-profiles-v1.md:349-353); the Python DEFAULT
section is an ordinary association (lines 355-358); RequireObjectV1 needs
an explicit NameComparison and CollisionPolicy (Reject | First | Last) and
every authorized collapse is Transformed with one report event per
discarded association and retained/discarded provenance (lines 364-381);
provenance distinguishes Direct, Derived, ContinuationFragment,
QuoteDerived, and Collapsed relations (lines 383-385; projection.rs:146-
159).
"""

from __future__ import annotations

from consema.ini import (
    CollisionPolicy,
    IniEncodingSelection,
    IniParseLimits,
    IniProfile,
    NameComparison,
    parse,
    project,
)
from consema.ini.projection import (
    CompleteProjection,
    FailedProjectionAttempt,
    ProjectionRequest,
    ProvenanceRelation,
)

DEFAULT_LIMITS = IniParseLimits()

WINDOWS_SOURCE = b"[Main]\r\nName=one\r\nname=two\r\n[main]\r\nOther=three\r\n"


def windows_document():
    return parse(
        WINDOWS_SOURCE,
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


# ---------------------------------------------------------------------------
# projection.exact-duplicate-entry-mapping (ini-v1.json:61-62)
# ---------------------------------------------------------------------------


def test_exact_duplicate_entry_mapping():
    # Case projection.exact-duplicate-entry-mapping (ini-v1.json:61-62).
    document = windows_document()
    result = project(document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert result.fidelity.value == "Exact"
    sections = result.value.as_entry_mapping()
    assert [key.as_string() for key, _ in sections] == ["Main", "main"]
    first_entries = sections[0][1].as_entry_mapping()
    assert [key.as_string() for key, _ in first_entries] == ["Name", "name"]
    assert [value.as_string() for _, value in first_entries] == ["one", "two"]
    assert len(result.report.events) == 0
    assert any(
        entry.projected.association is not None
        and entry.projected.association.role.value == "EntryMappingEntry"
        for entry in result.provenance.entries
    )


def test_python_default_section_projects_as_ordinary_association():
    # RFC 0009 §10 (docs/rfcs/0009-...:355-358): the Python default section
    # is an ordinary association whose provenance carries the
    # DefaultSection role; it is not expanded into every section.
    document = parse(
        b"[DEFAULT]\nbase=1\n[s]\nvalue=2\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    result = project(document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    sections = result.value.as_entry_mapping()
    assert [key.as_string() for key, _ in sections] == ["DEFAULT", "s"]
    assert any(
        origin.node.role.value == "IniDefaultSection"
        for entry in result.provenance.entries
        for origin in entry.origins
    )


# ---------------------------------------------------------------------------
# projection.explicit-object-collapse (ini-v1.json:66-67)
# ---------------------------------------------------------------------------


def test_explicit_object_collapse():
    # Case projection.explicit-object-collapse (ini-v1.json:66-67).
    document = windows_document()

    rejected = project(
        document,
        ProjectionRequest.require_object(
            NameComparison.PROFILE_EQUIVALENT, CollisionPolicy.REJECT
        ),
    )
    assert isinstance(rejected, FailedProjectionAttempt)
    assert rejected.diagnostics[0].code == "ini.projection.collision@1"
    assert rejected.diagnostics[0].arguments["reason"] == "collision"

    first = project(
        document,
        ProjectionRequest.require_object(
            NameComparison.PROFILE_EQUIVALENT, CollisionPolicy.FIRST
        ),
    )
    assert isinstance(first, CompleteProjection)
    assert first.fidelity.value == "Transformed"
    assert len(first.report.events) == 2
    sections = first.value.as_object()
    assert sections[0][0] == "Main"
    entries = sections[0][1].as_object()
    assert entries[0][0] == "Name"
    assert entries[0][1].as_string() == "one"
    assert any(
        origin.relation is ProvenanceRelation.COLLAPSED
        for entry in first.provenance.entries
        for origin in entry.origins
    )

    last = project(
        document,
        ProjectionRequest.require_object(
            NameComparison.PROFILE_EQUIVALENT, CollisionPolicy.LAST
        ),
    )
    assert isinstance(last, CompleteProjection)
    sections = last.value.as_object()
    assert sections[0][0] == "main"
    entries = sections[0][1].as_object()
    assert entries[0][0] == "Other"
    assert entries[0][1].as_string() == "three"

    original = project(
        document,
        ProjectionRequest.require_object(
            NameComparison.ORIGINAL_EXACT, CollisionPolicy.REJECT
        ),
    )
    assert isinstance(original, CompleteProjection)
    assert original.fidelity.value == "Exact"
    assert len(original.value.as_object()) == 2


def test_recovered_documents_never_project():
    # RFC 0009 §10 (docs/rfcs/0009-...:362-363): Recovered documents do not
    # project; the failure code is ini.projection.incomplete-document@1
    # (case formation.recovery-never-fabricates-entry, ini-v1.json:42).
    document = parse(
        b"[s]\nbare\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    result = project(document, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "ini.projection.incomplete-document@1"
    assert result.diagnostics[0].arguments["reason"] == "incomplete-document"


# ---------------------------------------------------------------------------
# projection.fragmented-value-provenance (ini-v1.json:71-72)
# ---------------------------------------------------------------------------


def test_fragmented_value_provenance():
    # Case projection.fragmented-value-provenance (ini-v1.json:71-72).
    python = parse(
        b"[s]\nkey = first\n  second\n",
        IniProfile.PYTHON_CONFIGPARSER_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    result = project(python, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert any(
        origin.relation is ProvenanceRelation.CONTINUATION_FRAGMENT
        for entry in result.provenance.entries
        for origin in entry.origins
    )

    windows = parse(
        b"[s]\r\nk=\" value \"\r\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    result = project(windows, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert any(
        origin.relation is ProvenanceRelation.QUOTE_DERIVED
        for entry in result.provenance.entries
        for origin in entry.origins
    )
    # The value origin of the unquoted case stays Direct.
    plain = parse(
        b"[s]\r\nk=value\r\n",
        IniProfile.WINDOWS_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    result = project(plain, ProjectionRequest.best_exact_entry_mapping())
    assert isinstance(result, CompleteProjection)
    assert any(
        origin.relation is ProvenanceRelation.DIRECT
        for entry in result.provenance.entries
        for origin in entry.origins
    )


# ---------------------------------------------------------------------------
# resource.projection-limit-matrix (ini-v1.json:131-133)
# ---------------------------------------------------------------------------


def test_projection_limit_matrix():
    # Case resource.projection-limit-matrix (ini-v1.json:131-133): all
    # three declared limits fail with core.projection.resource-limit@1.
    from consema.ini.projection import ProjectionLimits

    document = parse(
        b"[s]\na=1\n",
        IniProfile.PORTABLE_V1,
        IniEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )
    failed_count = 0
    for limits in (
        ProjectionLimits(max_source_associations=1),
        ProjectionLimits(max_value_nodes=1),
        ProjectionLimits(max_provenance_units=1),
    ):
        result = project(
            document, ProjectionRequest.best_exact_entry_mapping().with_limits(limits)
        )
        assert isinstance(result, FailedProjectionAttempt)
        assert result.diagnostics[0].code == "core.projection.resource-limit@1"
        assert result.diagnostics[0].arguments["reason"] == "resource-limit"
        failed_count += 1
    assert failed_count == 3
