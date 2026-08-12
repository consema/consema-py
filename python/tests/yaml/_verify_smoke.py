"""Ad-hoc verification driver (not a gate): exercises the critical vector
paths directly. Run with the repository python:
    python -m tests.yaml._verify_smoke
"""

from __future__ import annotations

import sys

sys.path.insert(0, "C:/Users/franck/Documents/consema/python/src")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def main() -> int:
    from consema.document.limits import ParseLimits
    from consema.graph import encode_pgce
    from consema.yaml import (
        EditTransactionBuilder,
        MappingPolicy,
        RepresentationPolicy,
        SharingPolicy,
        TagPolicy,
        YamlEditFailure,
        YamlEditFailureKind,
        YamlFormationFailure,
        YamlProfile,
        commit,
        format_operation_registry,
        materialize_graph,
        materialize_value,
        parse,
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
    from consema.core.value import PortableValue
    from consema.document.ids import MaterializationStyleId, ProfileId
    from consema.document.materialization import MaterializationRequest

    LIMITS = ParseLimits()

    def ps(source: str, profile=YamlProfile.YAML12_CORE_V1):
        return parse(source.encode("utf-8"), profile, LIMITS)

    # profile.yaml12-scalars
    doc = ps("[yes, 017, 0o17, 1:02:03, 2001-12-15]")
    root = doc.document(0).root()
    kinds = [root.sequence_item(i).node().scalar().kind().value for i in range(5)]
    canonical = [root.sequence_item(i).node().scalar().canonical() for i in range(5)]
    check("profile.yaml12-scalars kinds", kinds == ["String", "Integer", "Integer", "String", "String"], str(kinds))
    check("profile.yaml12-scalars canonical", canonical == ["yes", "17", "15", "1:02:03", "2001-12-15"], str(canonical))

    # profile.yaml11-scalars
    doc = ps("%YAML 1.1\n---\n[yes, 017, 0o17, 1:02:03, 2001-12-15]\n", YamlProfile.YAML11_COMPAT_V1)
    root = doc.document(0).root()
    kinds = [root.sequence_item(i).node().scalar().kind().value for i in range(5)]
    canonical = [root.sequence_item(i).node().scalar().canonical() for i in range(5)]
    check("profile.yaml11-scalars kinds", kinds == ["Boolean", "Integer", "String", "Integer", "Timestamp"], str(kinds))
    check("profile.yaml11-scalars canonical", canonical == ["true", "15", "0o17", "3723", "2001-12-15"], str(canonical))

    # source.utf16le-bom
    doc = parse(bytes.fromhex("fffe61003a00200031000a00"), YamlProfile.YAML12_CORE_V1, LIMITS)
    check("utf16le encoding", doc.source.encoding_facts().selected.kind.value == "utf-16le")
    check("utf16le doc count", doc.document_count() == 1)

    # stream.empty / multi-document
    doc = ps("")
    check("stream.empty", doc.document_count() == 0 and doc.alias_count() == 0)
    doc = ps("---\n&a [one, *a]\n---\n{k: v}\n")
    check("stream.multi-document", doc.document_count() == 2 and doc.alias_count() == 1)

    # syntax.styles-and-trivia
    source = (
        "--- # doc\nplain: text\nsingle: 'x'\ndouble: \"y\"\nliteral: |-\n  a\n"
        "folded: >+\n  b\nflow: [one, {k: v}]\n...\n"
    )
    doc = ps(source)
    check("syntax piece count", len(doc.lossless_structural_index().pieces) == 48,
          str(len(doc.lossless_structural_index().pieces)))

    # native.arbitrary-duplicate-mapping
    doc = ps("? [a, b]\n: one\nk: two\nk: three\n")
    root = doc.document(0).root()
    key_kinds = [root.mapping_entry(i).key().kind().value for i in range(3)]
    values = [root.mapping_entry(i).value().scalar().decoded() for i in range(3)]
    check("duplicate mapping", root.mapping_len() == 3 and key_kinds == ["Sequence", "Scalar", "Scalar"]
          and values == ["one", "two", "three"])

    # formation.undefined-alias
    try:
        ps("[*missing]\n")
        check("undefined alias", False, "no error")
    except YamlFormationFailure as error:
        check("undefined alias", error.code == "yaml.parse.syntax@1", error.code)

    # resource.parse-source-bytes
    try:
        parse(b"a: 1\n", YamlProfile.YAML12_CORE_V1, ParseLimits(max_source_bytes=4))
        check("parse source bytes", False, "no error")
    except YamlFormationFailure as error:
        check("parse source bytes", error.code == "core.parse.resource-limit@1", error.code)

    # regression.plain-property-characters
    doc = ps("---\nk:#foo\n &a !t s\n")
    scalar = doc.document(0).root().scalar()
    check("plain property characters", scalar.decoded() == "k:#foo &a !t s" and doc.alias_count() == 0,
          scalar.decoded())

    # graph.shared-cycle PGCE
    doc = ps("&root [one, *root]\n")
    graph = project_graph(doc)
    check("graph shared cycle counts", graph.node_count() == 2 and len(graph.roots()) == 1)
    check("pgce hex", encode_pgce(graph).hex() == (
        "504743450101020040157461673a79616d6c2e6f72672c323030323a736571"
        "02010020157461673a79616d6c2e6f72672c323030323a737472036f6e65"))

    # projection.sharing-policy
    doc = ps("[&x {k: v}, *x]\n")
    default = project_value(doc, ValueProjectionRequest.best_exact_v1())
    check("sharing default", default.code == "yaml.projection.sharing@1", default.code)
    duplicated = project_value(doc, ValueProjectionRequest.best_exact_v1().with_sharing(
        SharingPolicy.DUPLICATE_ACYCLIC))
    check("sharing duplicated", duplicated.fidelity.value == "Transformed"
          and len(duplicated.report.events) == 3, str(len(duplicated.report.events)))

    # projection.cycle
    doc = ps("&x [*x]\n")
    result = project_value(doc, ValueProjectionRequest.best_exact_v1().with_sharing(
        SharingPolicy.DUPLICATE_ACYCLIC))
    check("projection cycle", result.code == "yaml.projection.cycle@1", result.code)

    # projection.tag-policy
    doc = ps("!example value\n")
    default = project_value(doc, ValueProjectionRequest.best_exact_v1())
    check("tag default", default.code == "yaml.projection.unsupported-tag@1", default.code)
    stripped = project_value(doc, ValueProjectionRequest.best_exact_v1().with_tags(
        TagPolicy.STRIP_TO_NODE_KIND))
    check("tag stripped", stripped.value.as_string() == "value" and stripped.fidelity.value == "Lossy")

    # projection.mapping-policy
    doc = ps("{a: 1, a: 2}\n")
    object_result = project_value(doc, ValueProjectionRequest.best_exact_v1().with_mapping(
        MappingPolicy.REQUIRE_OBJECT))
    check("mapping not object", object_result.code == "yaml.projection.mapping-not-object@1",
          object_result.code)
    entries = project_value(doc, ValueProjectionRequest.best_exact_v1().with_mapping(
        MappingPolicy.REQUIRE_ENTRY_MAPPING))
    check("mapping entry count", len(entries.value.as_entry_mapping()) == 2)

    # projection.graph-provenance
    doc = ps("&root [one, *root]\n")
    result = project_graph_with_provenance(doc, GraphProjectionRequest.best_exact_v1())
    check("graph provenance", result.provenance.reference_origin_count() == 1
          and result.provenance.association_entry_count() == 2,
          f"{result.provenance.reference_origin_count()}/{result.provenance.association_entry_count()}")

    # resource.graph-provenance
    doc = ps("[one, two]\n")
    try:
        project_graph_with_provenance(doc, GraphProjectionRequest(
            limits=GraphProjectionLimits(max_provenance_entries=1)))
        check("provenance limit", False, "no error")
    except Exception as error:
        check("provenance limit", error.code == "yaml.projection.provenance-limit@1", error.code)

    # materialization.graph-cycle-flow
    doc = ps("&root [one, *root]\n")
    graph = project_graph(doc)
    request = MaterializationRequest.new(
        ProfileId.new("yaml.1.2-core", 1), MaterializationStyleId.new("yaml.canonical-flow", 1))
    result = materialize_graph(graph, request)
    check("graph-cycle-flow bytes", result.document.render() == b'--- &g0 !!seq [!!str "one", *g0]\n',
          result.document.render().decode())
    check("graph-cycle-flow reparse", project_graph(result.document) == graph)

    # materialization.value-flow
    doc = ps("{a: [1, true]}\n")
    projected = project_value(doc, ValueProjectionRequest.best_exact_v1())
    result = materialize_value(projected.value, request)
    check("value-flow bytes", result.document.render() ==
          b'--- !!map {? !!str "a" : !!seq [!!int "1", !!bool "true"]}\n',
          result.document.render().decode())

    # edit.scalar-atomic
    doc = ps("# keep\na: 1\nb: two\n")
    target = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.semantic_scalar(target.node_ref(), PortableValue.integer(2),
                            RepresentationPolicy.PRESERVE_COMPATIBLE)
    result = commit(doc, builder.build())
    check("edit.scalar-atomic", result.document.render() == b"# keep\na: 2\nb: two\n"
          and len(result.change_set.source_edits) == 1,
          result.document.render().decode())

    # edit.anchor-rename
    doc = ps("first: &x [one]\ncopy: *x\n")
    target = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.rename_anchor(target.anchor_node_ref(), "renamed")
    result = commit(doc, builder.build())
    check("edit.anchor-rename", result.document.render() == b"first: &renamed [one]\ncopy: *renamed\n",
          result.document.render().decode())

    # edit.structural-insert
    doc = ps("seq: [one, two]\nmap: {a: 1}\n")
    root = doc.document(0).root()
    sequence = root.mapping_entry(0).value()
    mapping = root.mapping_entry(1).value()
    builder = EditTransactionBuilder(doc)
    from consema.document.structural import AssociationPlacement
    builder.insert_sequence_element(
        sequence.node_ref(), PortableValue.boolean(True),
        AssociationPlacement(kind="Before", anchor=sequence.sequence_item(1).node_ref()))
    builder.insert_mapping_entry(
        mapping.node_ref(), PortableValue.string("b"), PortableValue.integer(2),
        AssociationPlacement(kind="End"))
    result = commit(doc, builder.build())
    check("edit.structural-insert", result.document.render() ==
          b'seq: [one, !!bool "true", two]\nmap: {a: 1, ? !!str "b" : !!int "2"}\n',
          result.document.render().decode())

    # edit.anchor-dependency
    doc = ps("seq:\n  - &x one\ncopy: *x\n")
    target = doc.document(0).root().mapping_entry(0).value().sequence_item(0)
    builder = EditTransactionBuilder(doc)
    builder.remove_sequence_element(target.node_ref())
    try:
        commit(doc, builder.build())
        check("edit.anchor-dependency", False, "no error")
    except YamlEditFailure as error:
        check("edit.anchor-dependency", error.code == "yaml.edit.anchor-dependency@1", error.code)

    # operation registry
    registry = format_operation_registry(YamlProfile.YAML12_CORE_V1)
    check("registry 8 records", len(registry.operations) == 8)
    ids = [op.to_string() for op in registry.operations]
    check("registry ids", ids == [
        "yaml.edit.insert-alias@1", "yaml.edit.insert-mapping-entry@1",
        "yaml.edit.insert-sequence-element@1", "yaml.edit.remove-mapping-entry@1",
        "yaml.edit.remove-sequence-element@1", "yaml.edit.rename-anchor@1",
        "yaml.edit.replace-scalar-literal@1", "yaml.edit.replace-scalar-semantic@1",
    ], str(ids))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
