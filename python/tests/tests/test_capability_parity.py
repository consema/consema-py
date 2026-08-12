"""Capability parity test: the Python mandatory capability set must match
the Feature-Complete Manifest (docs/fc-manifest-0.13.0.json:30-34) with no
"Rust only" mandatory behavior (docs/five-language-ci-design.md 搂5.3;
go/capability_parity_test.go cross-reference).

Runs under pytest or directly (python tests/test_capability_parity.py).
"""

from __future__ import annotations

import sys

from consema.capability_parity import (
    EXPECTED_ERROR_CODES,
    EXPECTED_FAMILIES,
    EXPECTED_OPERATION_REGISTRIES,
    EXPECTED_PROFILES,
    EXPECTED_QUERY_DOMAINS,
    actual_capability_counts,
    assert_python_capability_parity,
    manifest_capability_counts,
)


def test_python_inventory_matches_frozen_pins():
    actual = actual_capability_counts()
    assert actual["families"] == EXPECTED_FAMILIES
    assert actual["profiles"] == EXPECTED_PROFILES
    assert actual["query_domains"] == EXPECTED_QUERY_DOMAINS
    assert actual["operation_registries"] == EXPECTED_OPERATION_REGISTRIES
    assert actual["error_codes"] == EXPECTED_ERROR_CODES


def test_manifest_record_matches_frozen_pins():
    manifest = manifest_capability_counts()
    assert manifest["families"] == EXPECTED_FAMILIES
    assert manifest["profiles"] == EXPECTED_PROFILES
    assert manifest["query_domains"] == EXPECTED_QUERY_DOMAINS
    assert manifest["operation_registries"] == EXPECTED_OPERATION_REGISTRIES
    assert manifest["error_codes"] == EXPECTED_ERROR_CODES


def test_parity_assertion_passes():
    actual = assert_python_capability_parity()
    assert actual["error_codes"] == 187


def test_every_profile_resolves_an_operation_registry():
    # No "Rust only" mandatory behavior: all 16 profile registries resolve.
    from consema.registry import operation_registry, profiles

    for entry in profiles():
        assert operation_registry(entry.profile_id()) is not None


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

