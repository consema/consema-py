"""Tests of the Python PVCE/PGCE byte-parity harness
(python/src/consema/differential/byte_parity.py; docs/five-language-ci-design.md
§3.2).

TestCaseFileIntegrity always runs and guards the checked-in case set, so
``pytest`` protects the input set even without the orchestrator.
TestDifferentialByteParity skips without the environment variable
(documented skip, never silent) and runs only when
scripts/python-verify-byte-parity.ps1 provisioned the Rust golden byte
directory.
"""

from __future__ import annotations

import os

import pytest

from consema.differential import byte_parity


def test_case_file_integrity() -> None:
    """The checked-in case set passes every integrity guard (manifest id,
    exact count, unique ids, known codecs, canonical PVCE values, buildable
    PGCE graphs, fifteen-kind coverage)."""
    cases = byte_parity.load_case_file()
    assert len(cases) == 68
    pvce_count = sum(1 for case in cases if case.codec == "pvce")
    pgce_count = sum(1 for case in cases if case.codec == "pgce")
    assert pvce_count == 51
    assert pgce_count == 17


def test_differential_byte_parity() -> None:
    """Every case's Python encoder bytes equal the Rust golden bytes, and the
    bidirectional direction holds: Rust bytes decode under the Python
    decoders and re-encode byte-identically."""
    rust_dir = os.environ.get(byte_parity.RUST_DIR_ENV)
    if not rust_dir:
        pytest.skip(
            f"{byte_parity.RUST_DIR_ENV} is not set: "
            "run scripts/python-verify-byte-parity.ps1 to provision the Rust golden bytes"
        )
    result = byte_parity.run_parity(rust_dir)
    for failure in result.failures:
        print(failure)
    assert result.total == 68
    assert result.passed == 68, f"{result.passed}/{result.total} equal (byte parity)"
    assert result.pvce == 51
    assert result.pgce == 17
