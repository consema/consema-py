"""Shared fixtures for the toml family tests.

The conformance fixtures are read directly from the shared read-only
authority tree (conformance/fixtures/toml/*.toml), matching the vector
suite inputs (conformance/vectors/toml-v1.json). The corpus case
``toml.corpus.cargo-manifest`` reads the committed fixture
conformance/fixtures/toml/Cargo.toml (single authority since the six-repo
split; the workspace root no longer carries a Cargo.toml).
Tests never modify fixtures.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "conformance" / "fixtures"

# Fixture inventory guard (G68, 2026-08-14): every fixture file the toml
# suite consumes, pinned by name — a missing or renamed fixture FAILS the
# tests instead of skipping silently (a partially provisioned checkout must
# not go green).
_FIXTURE_MANIFEST = frozenset(
    {
        "toml/all-values.toml",
        "toml/trivia-and-strings.toml",
        "toml/application.toml",
        "toml/invalid-duplicate.toml",
        "toml/pyproject.toml",
        "toml/Cargo.toml",
    }
)


def _fixture_path(name: str) -> pathlib.Path:
    if name == "Cargo.toml":
        key = "toml/Cargo.toml"
        path = _FIXTURES / "toml" / "Cargo.toml"
    else:
        key = name
        path = _FIXTURES / name
    if key not in _FIXTURE_MANIFEST:
        raise FileNotFoundError(f"fixture not in the frozen inventory: {name}")
    if not path.exists():
        raise FileNotFoundError(f"shared fixture not available: {name}")
    return path


@pytest.fixture
def fixture_bytes():
    def load(name: str) -> bytes:
        return _fixture_path(name).read_bytes()

    return load


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return _fixture_path(name).read_text(encoding="utf-8")

    return load
