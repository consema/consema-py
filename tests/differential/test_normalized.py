"""Tests of the Python normalized-result differential harness
(python/src/consema/differential/normalized.py; docs/five-language-ci-design.md
§3.3).

TestCaseFileIntegrity always runs and guards the checked-in case set.
TestNormalizedDifferential skips without the environment variable
(documented skip, never silent) and runs only when
scripts/python-verify-normalized-differential.ps1 provisioned the Rust
evidence directory: the Python pipeline executes the same input set, the
facts are compared field by field, and every divergence is reported as
case id + field + both values.

TestEmitPythonNormalizedResults emits the Python-side evidence files (the
reverse direction's input, consumed by the Rust example's --consume mode).
TestEmitFormatConsistency always runs and proves the emitted files
round-trip through the forward reader.
"""

from __future__ import annotations

import os

import pytest

from consema.differential import case_files, normalized


def test_case_file_integrity() -> None:
    """The checked-in case set passes every integrity guard (manifest id,
    exact count, unique ids, per-kind schema validity)."""
    cases = normalized.load_case_file()
    assert len(cases) == 108
    document_cases = [case for case in cases if case["kind"] == "document"]
    source_cases = [case for case in cases if case["kind"] == "source"]
    assert len(source_cases) == 11
    assert len(document_cases) == 97


def test_forward_differential() -> None:
    """The Python normalized facts equal the Rust evidence facts field by
    field for every case."""
    rust_dir = os.environ.get(normalized.RUST_DIR_ENV)
    if not rust_dir:
        pytest.skip(
            f"{normalized.RUST_DIR_ENV} is not set: "
            "run scripts/python-verify-normalized-differential.ps1 to provision the Rust evidence"
        )
    result = normalized.run_differential(rust_dir)
    for failure in result.failures:
        print(failure)
    assert result.total == 108, f"{result.passed}/{result.total} equal (normalized differential)"
    assert result.passed == 108, f"{result.passed}/{result.total} equal"


def test_emit_python_normalized_results(tmp_path) -> None:
    """The Python-side evidence files are emitted in the same shape the
    forward direction reads and the Rust consume mode reads."""
    cases = normalized.load_case_file()
    emitted = normalized.emit_evidence_to_dir(cases, str(tmp_path))
    assert emitted == 108
    for case in cases:
        lines = case_files.read_evidence_file(str(tmp_path), case["id"])
        computed = normalized.run_case(case)
        assert computed == lines


def test_emit_python_normalized_results_env() -> None:
    """Emits the Python-side normalized results for the whole input set into
    the directory named by CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR (the
    reverse direction of the bidirectional differential: the Rust example's
    --consume mode reads this directory and compares it with its own
    results). It skips without the environment variable (documented skip,
    never silent) and runs only when
    scripts/python-verify-normalized-differential.ps1 provisioned the
    directory."""
    python_dir = os.environ.get(normalized.PYTHON_DIR_ENV)
    if not python_dir:
        pytest.skip(
            f"{normalized.PYTHON_DIR_ENV} is not set: "
            "run scripts/python-verify-normalized-differential.ps1 to provision the Python evidence files"
        )
    cases = normalized.load_case_file()
    emitted = normalized.emit_evidence_to_dir(cases, python_dir)
    assert emitted == 108


def test_emit_format_consistency() -> None:
    """The emitted files round-trip through the forward reader and compare
    equal field by field with the computed facts (the Go
    TestEmitFormatConsistency twin)."""
    cases = normalized.load_case_file()
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        emitted = normalized.emit_evidence_to_dir(cases, directory)
        assert emitted == len(cases)
        for case in cases:
            lines = case_files.read_evidence_file(directory, case["id"])
            computed = normalized.run_case(case)
            assert normalized.compare_facts(case["id"], computed, lines) == []
