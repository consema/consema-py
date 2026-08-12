"""Shared fixtures for the toml family tests.

The conformance fixtures are read directly from the shared read-only
authority tree (conformance/fixtures/toml/*.toml), matching the vector
suite inputs (conformance/vectors/toml-v1.json). The corpus case
``toml.corpus.cargo-manifest`` references the repository-root Cargo.toml,
resolved the same way as the Go runner (go/conformance/toml_v1.go
cargoManifestBytes: two directories up from the vectors directory).
Tests never modify fixtures.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "conformance" / "fixtures"


def _fixture_path(name: str) -> pathlib.Path:
    if name == "Cargo.toml":
        path = _ROOT / "Cargo.toml"
    else:
        path = _FIXTURES / name
    if not path.exists():
        pytest.skip(f"shared fixture not available: {name}")
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
