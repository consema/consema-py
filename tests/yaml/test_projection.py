"""Projection golden transcriptions (conformance/vectors/yaml-v1.json cases).

Cases covered with the vector case ids cited:

- graph.shared-cycle (yaml-v1.json:44-48): node_count 2, root_count 1, and
  the byte-exact PGCE hex (the graph encoder is consema.graph, the shared
  L0 codec).
- projection.sharing-policy (yaml-v1.json:70-74): the default Reject code
  yaml.projection.sharing@1; explicit DuplicateAcyclic completes with
  fidelity Transformed and exactly three SharingDuplicated events.
- projection.cycle (yaml-v1.json:75-78): cycles never enter a PortableValue
  (yaml.projection.cycle@1) even under DuplicateAcyclic.
- projection.tag-policy (yaml-v1.json:80-84): default unsupported-tag@1;
  StripToNodeKind completes with fidelity Lossy and value "value".
- projection.mapping-policy (yaml-v1.json:85-89): RequireObject fails with
  mapping-not-object@1; RequireEntryMapping preserves both entries.
- projection.graph-provenance (yaml-v1.json:90-93): reference_origins 1,
  association_entries 2 for the shared-cycle source.
- resource.graph-provenance (yaml-v1.json:129-133): the provenance limit
  fails atomically with yaml.projection.provenance-limit@1.

Projection semantics: RFC 0007 s10 (lines 260-301); failure carries no
PortableGraph/PortableValue and no partial provenance.
"""

from __future__ import annotations

import pytest

from consema.graph import encode_pgce
from consema.yaml import (
    FailedValueProjection,
    Fidelity,
    MappingPolicy,
    ProjectionEventKind,
    SharingPolicy,
    TagPolicy,
    YamlGraphProjectionError,
    YamlGraphProjectionErrorKind,
    YamlProfile,
    YamlProjectionFailureKind,
    project_graph,
    project_graph_with_provenance,
    project_value,
)
from consema.yaml.projection import (
    GraphProjectionLimits,
    GraphProjectionRequest,
    ValueProjectionLimits,
    ValueProjectionRequest,
)
from tests.yaml.conftest import parse_source


def test_graph_shared_cycle():
    # Case graph.shared-cycle (yaml-v1.json:44-48). The PGCE hex is the
    # byte-exact golden: 50474345 01 01 02 00 40 15 "tag:yaml.org,2002:seq"
    # 02 01 00 20 15 "tag:yaml.org,2002:str" 03 "one".
    document = parse_source("&root [one, *root]\n", YamlProfile.YAML12_CORE_V1)
    graph = project_graph(document)
    assert graph.node_count() == 2
    assert len(graph.roots()) == 1
    encoded = encode_pgce(graph)
    assert encoded.hex() == (
        "504743450101020040157461673a79616d6c2e6f72672c323030323a736571"
        "02010020157461673a79616d6c2e6f72672c323030323a737472036f6e65"
    )


def test_projection_sharing_policy():
    # Case projection.sharing-policy (yaml-v1.json:70-74).
    document = parse_source("[&x {k: v}, *x]\n", YamlProfile.YAML12_CORE_V1)
    default = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert isinstance(default, FailedValueProjection)
    assert default.code == "yaml.projection.sharing@1"

    duplicated = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_sharing(SharingPolicy.DUPLICATE_ACYCLIC),
    )
    assert not isinstance(duplicated, FailedValueProjection)
    assert duplicated.fidelity is Fidelity.TRANSFORMED
    assert len(duplicated.report.events) == 3
    assert all(
        event.kind is ProjectionEventKind.SHARING_DUPLICATED
        for event in duplicated.report.events
    )


def test_projection_cycle():
    # Case projection.cycle (yaml-v1.json:75-78): a cycle always fails
    # value projection, even with explicit acyclic duplication.
    document = parse_source("&x [*x]\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_sharing(SharingPolicy.DUPLICATE_ACYCLIC),
    )
    assert isinstance(result, FailedValueProjection)
    assert result.kind is YamlProjectionFailureKind.CYCLE
    assert result.code == "yaml.projection.cycle@1"


def test_projection_tag_policy():
    # Case projection.tag-policy (yaml-v1.json:80-84).
    document = parse_source("!example value\n", YamlProfile.YAML12_CORE_V1)
    default = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert isinstance(default, FailedValueProjection)
    assert default.code == "yaml.projection.unsupported-tag@1"
    assert default.tag == "!example"

    stripped = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_tags(TagPolicy.STRIP_TO_NODE_KIND),
    )
    assert not isinstance(stripped, FailedValueProjection)
    assert stripped.fidelity is Fidelity.LOSSY
    assert stripped.value.as_string() == "value"
    assert len(stripped.report.events) == 1
    assert stripped.report.events[0].kind is ProjectionEventKind.TAG_STRIPPED


def test_projection_mapping_policy():
    # Case projection.mapping-policy (yaml-v1.json:85-89).
    document = parse_source("{a: 1, a: 2}\n", YamlProfile.YAML12_CORE_V1)
    object_result = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_mapping(MappingPolicy.REQUIRE_OBJECT),
    )
    assert isinstance(object_result, FailedValueProjection)
    assert object_result.code == "yaml.projection.mapping-not-object@1"

    entries = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_mapping(MappingPolicy.REQUIRE_ENTRY_MAPPING),
    )
    assert not isinstance(entries, FailedValueProjection)
    assert len(entries.value.as_entry_mapping()) == 2


def test_projection_graph_provenance():
    # Case projection.graph-provenance (yaml-v1.json:90-93): the alias edge
    # is an additional Reference origin; both sequence associations are
    # association entries.
    document = parse_source("&root [one, *root]\n", YamlProfile.YAML12_CORE_V1)
    result = project_graph_with_provenance(document, GraphProjectionRequest.best_exact_v1())
    assert result.provenance.reference_origin_count() == 1
    assert result.provenance.association_entry_count() == 2


def test_resource_graph_provenance():
    # Case resource.graph-provenance (yaml-v1.json:129-133): the
    # provenance limit fails atomically with yaml.projection.provenance-limit@1.
    document = parse_source("[one, two]\n", YamlProfile.YAML12_CORE_V1)
    request = GraphProjectionRequest(
        limits=GraphProjectionLimits(max_provenance_entries=1)
    )
    with pytest.raises(YamlGraphProjectionError) as caught:
        project_graph_with_provenance(document, request)
    assert caught.value.kind is YamlGraphProjectionErrorKind.PROVENANCE_LIMIT
    assert caught.value.code == "yaml.projection.provenance-limit@1"


def test_projection_custom_tag_fails_graph():
    # native.rs:1360-1373: unknown/custom tags are preserved in the native
    # view but never admitted to PortableGraph.
    document = parse_source("!application/object payload\n", YamlProfile.YAML12_CORE_V1)
    root = document.document(0).root()
    assert root.tag() == "!application/object"
    with pytest.raises(YamlGraphProjectionError) as caught:
        project_graph(document)
    assert caught.value.kind is YamlGraphProjectionErrorKind.UNSUPPORTED_TAG
    assert caught.value.tag == "!application/object"


def test_projection_value_limits_fail_atomically():
    # projection.rs:780-785: a value-node limit fails with the resource
    # code and never returns a partial value.
    document = parse_source("[one, two]\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(
        document,
        ValueProjectionRequest.best_exact_v1().with_limits(
            ValueProjectionLimits(max_value_nodes=1)
        ),
    )
    assert isinstance(result, FailedValueProjection)
    assert result.code == "yaml.projection.resource-limit@1"
    assert result.resource_name == "max_value_nodes"


def test_projection_multidocument_cardinality():
    # projection.rs:558-563: a multi-document stream never satisfies a
    # single-value projection.
    document = parse_source("---\na\n---\nb\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert isinstance(result, FailedValueProjection)
    assert result.code == "yaml.projection.document-cardinality@1"


def test_projection_exact_object_and_non_finite_bits():
    # projection.rs:994-1003: the four frozen non-finite bit patterns;
    # RFC 0007 s5: YAML has no negative-NaN spelling, so the fourth pattern
    # is unreachable from implicit resolution.
    document = parse_source(".inf\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert result.value.as_binary_float64() == 0x7FF0000000000000
    document = parse_source("-.inf\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert result.value.as_binary_float64() == 0xFFF0000000000000
    document = parse_source(".nan\n", YamlProfile.YAML12_CORE_V1)
    result = project_value(document, ValueProjectionRequest.best_exact_v1())
    assert result.value.as_binary_float64() == 0x7FF8000000000000
