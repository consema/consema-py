"""consema.conformance — the Python conformance runner over the shared
language-neutral vectors (RFC 0016 §7; https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §2).

Imports the runner machinery and registers the frozen 18-suite inventory.
The vector files in ``conformance/vectors`` are the authority; the suite
count (18), the case count (519) and the aggregate digest are hard pins in
this module (runner.py), while the vector content itself is never copied —
the pins guard the inventory, the vector files stay authoritative
(conformance/README.md rules 3-4).
"""

from __future__ import annotations

from consema.conformance import loader  # noqa: F401
from consema.conformance.runner import (  # noqa: F401
    AGGREGATE_SHA256,
    EXPECTED_CASES,
    EXPECTED_SUITES,
    Case,
    CaseFailure,
    DigestResult,
    RunReport,
    Runner,
    SkipRecord,
    SuiteData,
    SuiteDefinition,
    SuiteReport,
    default_manifest_path,
    print_human_report,
    register_suite,
    repository_paths,
    run_argv,
)
from consema.conformance.suites import *  # noqa: F401,F403  (registers the suites)

__all__ = [
    "AGGREGATE_SHA256",
    "EXPECTED_CASES",
    "EXPECTED_SUITES",
    "Case",
    "CaseFailure",
    "DigestResult",
    "RunReport",
    "Runner",
    "SkipRecord",
    "SuiteData",
    "SuiteDefinition",
    "SuiteReport",
    "default_manifest_path",
    "print_human_report",
    "register_suite",
    "repository_paths",
    "run_argv",
]
