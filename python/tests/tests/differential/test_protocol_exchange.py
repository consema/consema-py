"""Tests of the Python protocol-exchange harness
(python/src/consema/differential/protocol_exchange.py;
docs/five-language-ci-design.md §3.4).

TestCaseFileIntegrity always runs and guards the checked-in case set (file
level: manifest id, exact count, unique ids, known records, per-record
coverage, canonical transport JSON, registered expected codes).
TestProtocolExchange skips without the environment variables (documented
skip, never silent) and runs only when
scripts/python-verify-protocol-exchange.ps1 provisioned the Rust files and
the Python output directory: the Python encoder's bytes are compared with
the Rust files, the Rust bytes decode under the Python typed record codec
and re-encode byte-identically, and rejection cases reject with the same
registered code on both sides.
"""

from __future__ import annotations

import os

import pytest

from consema.differential import protocol_exchange


def test_case_file_integrity() -> None:
    """The checked-in case set passes every file-level integrity guard."""
    cases = protocol_exchange.load_case_file()
    assert len(cases) == 83
    accept_count = sum(
        1 for case in cases if not (case.get("expected") or {}).get("error_code")
    )
    reject_count = sum(
        1 for case in cases if (case.get("expected") or {}).get("error_code")
    )
    assert accept_count == 40
    assert reject_count == 43


def test_protocol_exchange() -> None:
    """Bidirectional cross-language exchange against the Rust files, with
    the known Python record-codec gaps documented (never silent fixes)."""
    rust_dir = os.environ.get(protocol_exchange.RUST_DIR_ENV)
    if not rust_dir:
        pytest.skip(
            f"{protocol_exchange.RUST_DIR_ENV} is not set: "
            "run scripts/python-verify-protocol-exchange.ps1 to provision the Rust files"
        )
    result = protocol_exchange.run_exchange(rust_dir)
    for failure in result.failures:
        print(failure)
    accept_total = result.accept_passed + result.accept_failed
    reject_total = result.reject_passed + result.reject_failed
    assert accept_total == 40, f"{result.accept_passed}/{accept_total} accept cases verified"
    assert reject_total == 43, f"{result.reject_passed}/{reject_total} reject cases verified"
    assert result.accept_passed == 40, f"{result.accept_passed}/{accept_total} accept cases verified"
    assert result.reject_passed == 43, f"{result.reject_passed}/{reject_total} reject cases verified"
