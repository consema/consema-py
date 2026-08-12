"""Resource limit matrices (java-properties-v1.json cases).

Cases covered:

- java-properties-v1.json: resource.formation-limit-matrix (lines 116-140)
  — every PropertiesParseLimits bound is fatal with no partial document,
  and resource.projection-limit-matrix (142-145) — every projection
  bound fails atomically with core.projection.resource-limit@1.
"""

from __future__ import annotations

import pytest

from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding
from consema.properties import (
    DuplicatePolicy,
    FailedProjectionAttempt,
    ProjectionLimits,
    ProjectionRequest,
    PropertiesFormationFailure,
    PropertiesParseLimits,
    parse_reader,
    project,
)

DEFAULT_LIMITS = PropertiesParseLimits()

# The four common limits live on ParseLimits (lib.rs:64-66); the rest are
# format-owned fields (lib.rs:67-97).
_COMMON_LIMIT_NAMES = frozenset(
    ("max_source_bytes", "max_token_count", "max_node_count", "max_diagnostics")
)


def limits_with(name: str, value: int) -> PropertiesParseLimits:
    if name in _COMMON_LIMIT_NAMES:
        return PropertiesParseLimits(common=ParseLimits(**{name: value}))
    return PropertiesParseLimits(**{name: value})


def formation_fails(source: str, limits: PropertiesParseLimits) -> bool:
    try:
        parse_reader(source.encode("utf-8"), SourceEncoding.utf8(), limits)
    except PropertiesFormationFailure:
        return True
    except Exception:
        return True
    return False


def test_formation_limit_matrix():
    # Case resource.formation-limit-matrix (java-properties-v1.json:116-140):
    # all twenty limits are fatal (fatal_count fact) and no partial
    # document is published (no_partial_documents fact).
    cases = [
        ("max_source_bytes", "a=1\n", 2),
        ("max_token_count", "a=1\n", 1),
        ("max_node_count", "a=1\n", 1),
        ("max_diagnostics", "a=\\u\nb=\\u\n", 0),
        ("max_decoded_utf8_bytes", "a=1\n", 1),
        ("max_decoded_scalars", "a=1\n", 1),
        ("max_natural_lines", "a=1\nb=2\n", 1),
        ("max_natural_line_bytes", "long=value\n", 3),
        ("max_natural_line_scalars", "long=value\n", 3),
        ("max_logical_lines", "a=1\nb=2\n", 1),
        ("max_logical_line_natural_lines", "a=one\\\n two\n", 1),
        ("max_logical_line_scalars", "a=one\\\n two\n", 3),
        ("max_properties", "a=1\nb=2\n", 1),
        ("max_comments", "# a\n# b\n", 1),
        ("max_escapes", "a=\\t\\n\n", 1),
        ("max_unicode_escapes", "a=\\u0041\\u0042\n", 1),
        ("max_java_code_units_per_string", "long=value\n", 3),
        ("max_total_java_code_units", "a=1\nb=2\n", 3),
        ("max_duplicate_group_members", "a=1\na=2\n", 1),
        ("max_recovery_regions", "a=\\u\nb=\\u\n", 1),
    ]
    fatal = 0
    for name, source, value in cases:
        limits = limits_with(name, value)
        assert formation_fails(source, limits), name
        fatal += 1
    assert fatal == 20


def test_projection_limit_matrix():
    # Case resource.projection-limit-matrix (java-properties-v1.json:142-145):
    # every projection bound fails atomically with
    # core.projection.resource-limit@1 (failed_count fact).
    document = parse_reader(b"a=1\n", SourceEncoding.utf8(), DEFAULT_LIMITS)
    failed = 0
    for name, value in [
        ("max_source_associations", 0),
        ("max_value_nodes", 1),
        ("max_provenance_units", 1),
    ]:
        limits = ProjectionLimits(**{name: value})
        result = project(
            document,
            ProjectionRequest.best_exact_entry_mapping().with_limits(limits),
        )
        assert isinstance(result, FailedProjectionAttempt)
        assert result.diagnostics[0].code == "core.projection.resource-limit@1"
        assert result.report.events == ()
        failed += 1

    # The duplicate report budget fails before any collapse event exists.
    duplicate = parse_reader(b"a=1\na=2\n", SourceEncoding.utf8(), DEFAULT_LIMITS)
    result = project(
        duplicate,
        ProjectionRequest.require_object(DuplicatePolicy.FIRST_WINS).with_limits(
            ProjectionLimits(max_report_entries=0)
        ),
    )
    assert isinstance(result, FailedProjectionAttempt)
    assert result.diagnostics[0].code == "core.projection.resource-limit@1"
    failed += 1
    assert failed == 4
