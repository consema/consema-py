"""Conformance runner test: pins the milestone gate (docs/go-implementation
plan 搂4.2; docs/five-language-ci-design.md 搂4.2 鈥?the five-runner shared
pin): the aggregate vector digest matches the Feature-Complete Manifest and
the frozen constant, the inventory is exactly 18 suites / 508 cases, every
suite is conformant (documented skips count as success), and each suite
matches its frozen per-suite applicable surface.

Runs under pytest or directly (python tests/conformance/test_runner.py).
The runner reads the repository vectors by repo-relative path; pytest is
not required.
"""

from __future__ import annotations

import sys

from consema.conformance.runner import (
    AGGREGATE_SHA256,
    EXPECTED_CASES,
    EXPECTED_SUITES,
    Runner,
    default_manifest_path,
    repository_paths,
)

# The five-runner shared aggregate pin (docs/five-language-ci-design.md
# 搂4.2; fc-manifest-0.13.0.json:38).
RECORDED_AGGREGATE = "35bebc8d384d71740f7c1a886bc50f4e095ff52fe05d2a407f04b842ee6922fa"

# Per-suite applicable surface {passed, skipped, failed} 鈥?the current L4
# surface executes every case (go/conformance/conformance_test.go:60-97).
EXPECTED_SUITE_COUNTS = {
    "consema.conformance@1": (30, 0, 0),
    "consema.toml.conformance@1": (18, 0, 0),
    "consema.protocol.conformance@1": (32, 0, 0),
    "consema.source.conformance@1": (28, 0, 0),
    "consema.syntax-query.conformance@1": (19, 0, 0),
    "consema.protocol.conformance@2": (11, 0, 0),
    "consema.operations.conformance@1": (35, 0, 0),
    "consema.json-family.conformance@2": (33, 0, 0),
    "consema.portable-graph.conformance@1": (10, 0, 0),
    "consema.semantic-model-v5.conformance@1": (22, 0, 0),
    "consema.yaml.conformance@1": (27, 0, 0),
    "consema.semantic-model-v6.conformance@1": (25, 0, 0),
    "consema.ini.conformance@1": (20, 0, 0),
    "consema.java-properties.conformance@1": (22, 0, 0),
    "consema.xml-1-0-safe.conformance@1": (34, 0, 0),
    "consema.plist.conformance@1": (45, 0, 0),
    "consema.hcl.conformance@1": (57, 0, 0),
    "consema.cli.conformance@1": (40, 0, 0),
}


def _repository_runner() -> Runner:
    vectors, fixtures = repository_paths()
    return Runner(vectors, fixtures, default_manifest_path(vectors))


def test_digest_algorithm_matches_manifest():
    digest = _repository_runner().verify_vectors_digest()
    assert digest.recorded == RECORDED_AGGREGATE
    assert digest.computed == RECORDED_AGGREGATE
    assert digest.suites == EXPECTED_SUITES == 18
    assert digest.cases == EXPECTED_CASES == 508
    assert digest.ok


def test_run_is_conformant():
    report = _repository_runner().run()
    assert report.digest.ok
    assert report.total == 508
    for suite in report.suites:
        assert suite.conformant(), f"suite {suite.suite} is not conformant"
        for failure in suite.failed:
            print(f"suite {suite.suite} failure: {failure.id}: {failure.message}")
        for skip in suite.skipped:
            assert skip.capability and skip.reason, f"skip {skip.id} lacks capability or reason"
    assert report.conformant()
    assert report.failed == 0


def test_applicable_suite_counts():
    report = _repository_runner().run()
    seen = set()
    for suite in report.suites:
        seen.add(suite.suite)
        expected = EXPECTED_SUITE_COUNTS[suite.suite]
        actual = (len(suite.passed), len(suite.skipped), len(suite.failed))
        assert actual == expected, f"suite {suite.suite}: {actual} != {expected}"
    assert seen == set(EXPECTED_SUITE_COUNTS)


def main() -> None:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {error}")
    print(f"passed={sum(1 for n in globals() if n.startswith('test_')) - failures} failed={failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

