"""YAML fixture round-trip gate (transcribed from the ad-hoc fixture
verifier, formerly tests/yaml/_verify_fixtures.py, deleted after
transcription).

The production-shaped fixtures under conformance/fixtures/yaml (the
single-authority tree provisioned from the consema spec repository in CI)
must close byte-exactly: parse -> render == source bytes, complete
formation, exhaustive lossless coverage, graph PGCE round trip, and the
anchor-heavy fixture's explicit sharing semantics (the consema-go/go/yaml
fixture_test.go surface: kubernetes-workload.yaml, github-actions-ci.yaml,
compose-services.yaml, anchor-heavy.yaml).

When the shared conformance tree is not reachable (a plain checkout
without provision), the tests report a documented skip — the same pattern
as tests/toml/conftest.py. The fixtures are read-only; tests never modify
them.
"""

from __future__ import annotations

import pathlib

import pytest

from consema.document.limits import ParseLimits
from consema.graph import PgceLimits, decode_pgce, encode_pgce
from consema.yaml import (
    SharingPolicy,
    YamlProfile,
    parse,
    project_graph,
    project_value,
)
from consema.yaml.projection import ValueProjectionRequest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURES = _ROOT / "conformance" / "fixtures" / "yaml"

# Rust consema-rs/consema-conformance/tests/yaml_fixtures.rs:22-51 fixture
# facts (document_count / alias_count).
FIXTURE_FACTS = {
    "kubernetes-workload.yaml": (2, 0),
    "github-actions-ci.yaml": (1, 0),
    "compose-services.yaml": (1, 0),
    "anchor-heavy.yaml": (1, 5),
}


def _fixture_bytes(name: str) -> bytes:
    path = _FIXTURES / name
    if not path.exists():
        pytest.skip(f"shared fixture not available: {name}")
    return path.read_bytes()


def _form(name: str):
    """Parses one fixture under yaml.1.2-core@1 with default limits."""
    raw = _fixture_bytes(name)
    return parse(raw, YamlProfile.YAML12_CORE_V1, ParseLimits()), raw


def test_real_project_yaml_fixtures_round_trip_byte_exact():
    # parse -> render must reproduce the source bytes exactly, formation
    # must be Complete, and the lossless index must cover every byte.
    for name, (document_count, alias_count) in FIXTURE_FACTS.items():
        document, raw = _form(name)
        assert document.formation_status().value == "Complete", name
        assert document.document_count() == document_count, name
        assert document.alias_count() == alias_count, name
        assert document.render() == raw, name
        pieces = document.lossless_structural_index().pieces
        assert sum(piece.span.len() for piece in pieces) == len(raw), name


def test_real_project_yaml_fixtures_graph_pgce_round_trip():
    # Graph projection -> PGCE encode -> decode must equal the projected
    # graph, with one root per document.
    for name, (document_count, _alias_count) in FIXTURE_FACTS.items():
        document, _raw = _form(name)
        graph = project_graph(document)
        assert len(graph.roots()) == document_count, name
        encoded = encode_pgce(graph)
        decoded = decode_pgce(encoded, PgceLimits())
        assert decoded == graph, name


def test_anchor_heavy_fixture_is_explicit_about_sharing():
    # The anchor-heavy fixture must reject implicit sharing and complete
    # under explicit acyclic duplication (consema-go/go/yaml fixture_test.go:147-166).
    document, _raw = _form("anchor-heavy.yaml")
    default = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert getattr(default, "code", None) == "yaml.projection.sharing@1"
    duplicated = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_sharing(SharingPolicy.DUPLICATE_ACYCLIC),
    )
    assert not hasattr(duplicated, "code")
    assert duplicated.fidelity.value == "Transformed"


def test_tree_shaped_yaml_fixtures_value_projection_closes():
    # The single-document tree-shaped fixtures close through PortableValue.
    for name in ("github-actions-ci.yaml", "compose-services.yaml"):
        document, _raw = _form(name)
        assert document.document_count() == 1, name
        projected = project_value(document, ValueProjectionRequest.best_exact_v1())
        assert not hasattr(projected, "code"), name
