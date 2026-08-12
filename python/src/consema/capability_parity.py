"""Python capability parity assertion against the Feature-Complete
Manifest (docs/fc-manifest-0.13.0.json; go/capability_parity_test.go
cross-reference; docs/five-language-ci-design.md §5.3: the L4
L-conformance job asserts capability parity).

The mandatory capability set of the Python implementation must match the
manifest's ``capability_set`` record (fc-manifest-0.13.0.json:30-34):
8 families / 16 profiles / 21 query domains / 16 operation registries /
187 error codes. There is no "Rust only" mandatory behavior: every
mandatory capability of the manifest is implemented by this package — the
facade enumeration derives from the backend packages, every profile
resolves its per-profile operation registry, and every registered error
code is present in the v7 error registry.

The assertion is evidence-producing: it recomputes the actual inventory
from the implementation (never from the manifest) and compares against the
manifest record, so a backend change fails this module's own check.
"""

from __future__ import annotations

import json
import os
import re

from consema.protocol.error_registry import ErrorCodeRegistry
from consema.registry import format_families, operation_registry, profiles, query_domains

# The frozen manifest inventory (fc-manifest-0.13.0.json:31). These pins are
# the five-runner shared capability assertion (docs/five-language-ci-design.md
# §4.2); the runtime check still recomputes the actual inventory.
EXPECTED_FAMILIES = 8
EXPECTED_PROFILES = 16
EXPECTED_QUERY_DOMAINS = 21
EXPECTED_OPERATION_REGISTRIES = 16
EXPECTED_ERROR_CODES = 187

_CAPABILITY_RE = re.compile(
    r"(\d+) families\s*/\s*(\d+) profiles\s*/\s*(\d+) query domains\s*/\s*"
    r"(\d+) operation registries\s*/\s*(\d+) error codes"
)


class CapabilityParityError(AssertionError):
    """One capability-parity mismatch between the Python implementation and
    the Feature-Complete Manifest."""


def actual_capability_counts() -> dict[str, int]:
    """Recomputes the Python capability inventory from the implementation."""
    resolved = 0
    for entry in profiles():
        if operation_registry(entry.profile_id()) is not None:
            resolved += 1
    return {
        "families": len(format_families()),
        "profiles": len(profiles()),
        "query_domains": len(query_domains()),
        "operation_registries": resolved,
        "error_codes": len(ErrorCodeRegistry(7).codes()),
    }


def manifest_capability_counts(manifest_path: str | None = None) -> dict[str, int]:
    """Parses the manifest's ``capability_set`` record."""
    path = manifest_path or default_manifest_path()
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    value = manifest["digests"]["capability_set"]["value"]
    match = _CAPABILITY_RE.search(value)
    if match is None:
        raise CapabilityParityError(f"capability_set record {value!r} is not parseable")
    families, profiles_count, domains, registries, codes = (int(g) for g in match.groups())
    return {
        "families": families,
        "profiles": profiles_count,
        "query_domains": domains,
        "operation_registries": registries,
        "error_codes": codes,
    }


def default_manifest_path() -> str:
    """The repository Feature-Complete Manifest path (this module lives at
    python/src/consema, so the repository root is three levels up)."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    python_dir = os.path.dirname(os.path.dirname(package_dir))
    repo_root = os.path.dirname(python_dir)
    return os.path.join(repo_root, "docs", "fc-manifest-0.13.0.json")


def assert_python_capability_parity(manifest_path: str | None = None) -> dict[str, int]:
    """Asserts the Python mandatory capability set matches the manifest.

    Returns the actual inventory. Raises :class:`CapabilityParityError` on
    any mismatch, including a manifest record that disagrees with the
    frozen five-runner pins.
    """
    actual = actual_capability_counts()
    manifest = manifest_capability_counts(manifest_path)
    expected = {
        "families": EXPECTED_FAMILIES,
        "profiles": EXPECTED_PROFILES,
        "query_domains": EXPECTED_QUERY_DOMAINS,
        "operation_registries": EXPECTED_OPERATION_REGISTRIES,
        "error_codes": EXPECTED_ERROR_CODES,
    }
    for name in expected:
        if manifest[name] != expected[name]:
            raise CapabilityParityError(
                f"manifest {name} record {manifest[name]} != frozen pin {expected[name]}"
            )
    for name in expected:
        if actual[name] != expected[name]:
            raise CapabilityParityError(
                f"python {name} inventory {actual[name]} != mandatory capability {expected[name]}"
            )
    return actual
