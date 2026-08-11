"""Materialization golden transcriptions (conformance/vectors/yaml-v1.json
cases) and the RFC 0007 s11 closure.

Cases covered with the vector case ids cited:

- materialization.graph-cycle-flow (yaml-v1.json:95-98): the byte-exact
  canonical flow output ``--- &g0 !!seq [!!str "one", *g0]\\n`` for the
  shared-cycle graph; the materialized document reprojects to the exact
  input graph and the fidelity is Exact.
- materialization.value-flow (yaml-v1.json:99-103): the byte-exact output
  ``--- !!map {? !!str "a" : !!seq [!!int "1", !!bool "true"]}\\n``; the
  reprojection equals the input value.

Contract: RFC 0007 s11 (lines 303-353) — canonical graph numbering,
deterministic anchors ``&g0``... for nodes whose topology requires an
alias, explicit standard tags, target reparse before a Complete result,
and no partial output bytes on failure.
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    MaterializationRequest,
    MaterializationFidelity,
)
from consema.yaml import (
    FailedGraphMaterializationAttempt,
    YamlGraphMaterializationFailureKind,
    YamlProfile,
    materialize_graph,
    materialize_value,
    project_graph,
    project_value,
)
from consema.yaml.projection import ValueProjectionRequest
from tests.yaml.conftest import parse_source


def _flow_request(profile: str = "yaml.1.2-core") -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new(profile, 1),
        MaterializationStyleId.new("yaml.canonical-flow", 1),
    )


def test_materialization_graph_cycle_flow():
    # Case materialization.graph-cycle-flow (yaml-v1.json:95-98).
    document = parse_source("&root [one, *root]\n", YamlProfile.YAML12_CORE_V1)
    graph = project_graph(document)
    result = materialize_graph(graph, _flow_request())
    assert not isinstance(result, FailedGraphMaterializationAttempt)
    assert result.document.render() == b'--- &g0 !!seq [!!str "one", *g0]\n'
    assert result.fidelity is MaterializationFidelity.EXACT
    assert project_graph(result.document) == graph


def test_materialization_value_flow():
    # Case materialization.value-flow (yaml-v1.json:99-103).
    document = parse_source("{a: [1, true]}\n", YamlProfile.YAML12_CORE_V1)
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert not isinstance(projected, FailedGraphMaterializationAttempt)
    result = materialize_value(projected.value, _flow_request())
    assert result.document.render() == (
        b'--- !!map {? !!str "a" : !!seq [!!int "1", !!bool "true"]}\n'
    )
    assert result.fidelity is MaterializationFidelity.EXACT
    reprojected = project_value(result.document, ValueProjectionRequest.best_exact_v1())
    assert reprojected.value == projected.value


def test_materialization_block_style():
    # RFC 0007 s11: yaml.canonical-block@1 emits explicit document starts,
    # block collections, and the explicit standard tags.
    document = parse_source("a: [1]\n", YamlProfile.YAML12_CORE_V1)
    graph = project_graph(document)
    request = MaterializationRequest.new(
        ProfileId.new("yaml.1.2-core", 1),
        MaterializationStyleId.new("yaml.canonical-block", 1),
    )
    result = materialize_graph(graph, request)
    assert not isinstance(result, FailedGraphMaterializationAttempt)
    assert result.document.render() == (
        b"--- !!map\n? !!str \"a\"\n: !!seq\n  - !!int \"1\"\n"
    )
    assert project_graph(result.document) == graph


def test_materialization_utf16_output_carries_bom():
    # RFC 0007 s11 (lines 348-351): UTF-16LE/BE output always carries the
    # matching BOM and raw encoded bytes are charged to max_output_bytes.
    document = parse_source("[one]\n", YamlProfile.YAML12_CORE_V1)
    graph = project_graph(document)
    request = _flow_request().with_encoding(_utf16le())
    result = materialize_graph(graph, request)
    assert not isinstance(result, FailedGraphMaterializationAttempt)
    raw = result.document.render()
    assert raw.startswith(b"\xff\xfe")
    assert raw[2:].decode("utf-16-le") == '--- !!seq [!!str "one"]\n'


def test_materialization_cross_document_sharing_fails():
    # materialization.rs:353-369: YAML anchors are document-scoped, so a
    # graph node reachable from more than one root fails with
    # yaml.materialization.cross-document-sharing@1.
    from consema.graph import GraphBuilder, GraphMappingEntry, GraphLimits

    builder = GraphBuilder(GraphLimits())
    shared = builder.reserve_node()
    builder.define_scalar(shared, "tag:yaml.org,2002:str", "x")
    root_a = builder.reserve_node()
    builder.define_sequence(root_a, "tag:yaml.org,2002:seq", [shared])
    root_b = builder.reserve_node()
    builder.define_sequence(root_b, "tag:yaml.org,2002:seq", [shared])
    graph = builder.push_root(root_a).push_root(root_b).build()
    result = materialize_graph(graph, _flow_request())
    assert isinstance(result, FailedGraphMaterializationAttempt)
    assert result.failure.kind is YamlGraphMaterializationFailureKind.CROSS_DOCUMENT_SHARING
    assert result.failure.code == "yaml.materialization.cross-document-sharing@1"


def test_materialization_custom_tag_fails():
    # RFC 0007 s11: custom graph tags fail until a versioned extension
    # constructor contract is selected (yaml.materialization.unsupported-tag@1).
    from consema.graph import GraphBuilder, GraphLimits

    builder = GraphBuilder(GraphLimits())
    node = builder.reserve_node()
    builder.define_scalar(node, "!application/thing", "value")
    graph = builder.push_root(node).build()
    result = materialize_graph(graph, _flow_request())
    assert isinstance(result, FailedGraphMaterializationAttempt)
    assert result.failure.code == "yaml.materialization.unsupported-tag@1"


def test_materialization_unrepresentable_value_fails():
    # RFC 0007 s11: BinaryFloat32 is not representable (no guessing); the
    # failed attempt carries no Document and no partial output bytes.
    from consema.document.materialization import FailedMaterializationAttempt

    value = PortableValue.binary_float32(0x3F800000)
    result = materialize_value(value, _flow_request())
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.code == "core.materialization.unrepresentable@1"


def test_materialization_float_canonical_e0():
    # materialization.rs:719-728: a float canonical without "."/"e"/"E"
    # gains "e0" so the tag and content reparse exactly.
    document = parse_source("1e3\n", YamlProfile.YAML12_CORE_V1)
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert projected.value.as_decimal().coefficient == 1
    assert projected.value.as_decimal().exponent == 3
    result = materialize_value(projected.value, _flow_request())
    assert result.document.render() == b'--- !!float "1e3"\n'


def _utf16le():
    from consema.document.source import SourceEncoding

    return SourceEncoding.utf16le()
