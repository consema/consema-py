"""Conformance runner test: pins the milestone gate
(https://github.com/consema/consema/blob/main/docs/go-implementation-plan.md §4.2;
https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §4.2 — the shared aggregate
pin): the aggregate vector digest matches the Feature-Complete Manifest and
the frozen constant, the inventory is exactly 18 suites / 519 cases, every
suite is conformant with zero skips (any skip is a failure — the (N, 0, 0)
hard pin), and each suite matches its frozen per-suite applicable surface.
The executing surface is NOT five runners (wave-5, correcting the previous
"five-runner shared pin" wording): this test executes the assertion against
the provisioned manifest, together with the go/kt runners, the rs vendored
conformance/DIGEST and the mother-repo shared-conformance-digest job;
consema-ts does not execute it — its runner skips without a provisioned
manifest.

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
    run_argv,
)

# The shared aggregate pin (https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md
# §4.2; fc-manifest-0.13.0.json; wave-5: the executing surface is the
# go/py/kt runners + the rs vendored conformance/DIGEST + the mother-repo
# shared-conformance-digest job — consema-ts's assertion is a permanent
# documented skip without a provisioned manifest).
RECORDED_AGGREGATE = "cfd6e296da5b22b62d37b076d35bf6bbf58b0678ceddb37eea51a8b47200ab6a"

# Per-suite applicable surface {passed, skipped, failed} — the current L5
# surface executes every case (https://github.com/consema/consema-go/blob/main/go/conformance/conformance_test.go).
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
    "consema.yaml.conformance@1": (31, 0, 0),
    "consema.semantic-model-v6.conformance@1": (25, 0, 0),
    "consema.ini.conformance@1": (20, 0, 0),
    "consema.java-properties.conformance@1": (25, 0, 0),
    "consema.xml-1-0-safe.conformance@1": (34, 0, 0),
    "consema.plist.conformance@1": (49, 0, 0),
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
    assert digest.cases == EXPECTED_CASES == 519
    assert digest.ok


def test_run_is_conformant():
    report = _repository_runner().run()
    assert report.digest.ok
    assert report.total == 519
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


def _run_argv_with_manifest(manifest_text: str) -> int:
    import tempfile

    vectors, fixtures = repository_paths()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write(manifest_text)
        path = handle.name
    try:
        return run_argv(["--manifest", path, "--vectors", vectors, "--fixtures", fixtures, "--quiet"])
    finally:
        import os

        os.unlink(path)


def test_cli_corrupt_manifest_is_data_error_exit_2():
    """Wave-4 R18: a manifest that fails strict JSON decode is corrupted
    input data (RFC 0015 §5.1 request/plan files failing strict decode),
    classified exit 2 — never the internal exit 5."""
    assert _run_argv_with_manifest("{not json") == 2


def test_cli_manifest_missing_key_is_data_error_exit_2():
    """Wave-4 R18: a manifest missing the frozen digests.conformance_suite
    key is a strict-decode failure of the input file, classified exit 2."""
    assert _run_argv_with_manifest('{"digests": {}}') == 2


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

