"""Ad-hoc extended verification (not a gate): the remaining intent-document
scenarios from the pytest suite."""

from __future__ import annotations

import sys

sys.path.insert(0, "C:/Users/franck/Documents/consema/python/src")
sys.path.insert(0, "C:/Users/franck/Documents/consema/python")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def main() -> int:
    from consema.document.limits import ParseLimits
    from consema.document.ids import MaterializationStyleId, ProfileId
    from consema.document.materialization import (
        FailedMaterializationAttempt,
        MaterializationRequest,
    )
    from consema.document.source import SourceEncoding
    from consema.document.structural import AssociationPlacement
    from consema.document.edit_plan import EditPlanSourceId
    from consema.graph import GraphBuilder, GraphLimits
    from consema.yaml import (
        EditTransactionBuilder,
        RepresentationPolicy,
        YamlEditFailure,
        YamlFormationFailure,
        YamlGraphMaterializationFailureKind,
        YamlProfile,
        commit,
        dry_run,
        materialize_graph,
        materialize_value,
        parse,
        project_graph,
        project_value,
    )
    from consema.yaml.projection import ValueProjectionRequest
    from consema.core.value import PortableValue

    LIMITS = ParseLimits()

    def ps(source: str, profile=YamlProfile.YAML12_CORE_V1):
        return parse(source.encode("utf-8"), profile, LIMITS)

    # Block materialization
    doc = ps("a: [1]\n")
    graph = project_graph(doc)
    request = MaterializationRequest.new(
        ProfileId.new("yaml.1.2-core", 1), MaterializationStyleId.new("yaml.canonical-block", 1))
    result = materialize_graph(graph, request)
    check("block style bytes", result.document.render() ==
          b'--- !!map\n? !!str "a"\n: !!seq\n  - !!int "1"\n',
          result.document.render().decode())

    # UTF-16 output carries BOM
    doc = ps("[one]\n")
    graph = project_graph(doc)
    request = MaterializationRequest.new(
        ProfileId.new("yaml.1.2-core", 1), MaterializationStyleId.new("yaml.canonical-flow", 1))
    result = materialize_graph(graph, request.with_encoding(SourceEncoding.utf16le()))
    check("utf16 bom", result.document.render().startswith(b"\xff\xfe"))
    check("utf16 text", result.document.render()[2:].decode("utf-16-le") ==
          '--- !!seq [!!str "one"]\n')

    # Cross-document sharing fails
    builder = GraphBuilder(GraphLimits())
    shared = builder.reserve_node()
    builder.define_scalar(shared, "tag:yaml.org,2002:str", "x")
    root_a = builder.reserve_node()
    builder.define_sequence(root_a, "tag:yaml.org,2002:seq", [shared])
    root_b = builder.reserve_node()
    builder.define_sequence(root_b, "tag:yaml.org,2002:seq", [shared])
    graph = builder.push_root(root_a).push_root(root_b).build()
    request = MaterializationRequest.new(
        ProfileId.new("yaml.1.2-core", 1), MaterializationStyleId.new("yaml.canonical-flow", 1))
    result = materialize_graph(graph, request)
    check("cross-document sharing", result.failure.kind is
          YamlGraphMaterializationFailureKind.CROSS_DOCUMENT_SHARING
          and result.failure.code == "yaml.materialization.cross-document-sharing@1",
          getattr(result, "failure", None).code if hasattr(result, "failure") else "ok")

    # Custom tag materialization fails
    builder = GraphBuilder(GraphLimits())
    node = builder.reserve_node()
    builder.define_scalar(node, "!application/thing", "value")
    graph = builder.push_root(node).build()
    result = materialize_graph(graph, request)
    check("custom tag materialization", result.failure.code ==
          "yaml.materialization.unsupported-tag@1")

    # Unrepresentable value fails
    result = materialize_value(PortableValue.binary_float32(0x3F800000), request)
    check("unrepresentable value", isinstance(result, FailedMaterializationAttempt)
          and result.failure.code == "core.materialization.unrepresentable@1")

    # Float canonical e0
    doc = ps("1e3\n")
    projected = project_value(doc, ValueProjectionRequest.best_exact_v1())
    result = materialize_value(projected.value, request)
    check("float e0", result.document.render() == b'--- !!float "1e3"\n',
          result.document.render().decode())

    # Alias bomb (anchors are document-scoped, so the copies live in the
    # same document as the definition).
    source = "bomb: &bomb {}\n" + "copy: *bomb\n" * 2000
    doc = ps(source)
    check("alias bomb count", doc.alias_count() == 2000)
    check("alias bomb nodes", len(doc.native.nodes) == 1 + 1 + 1 + 2000,
          str(len(doc.native.nodes)))

    # Block scalar keywords
    doc = ps("a: |\n  ~\nb: >\n  null\n")
    root = doc.document(0).root()
    tilde = root.mapping_entry(0).value().scalar()
    check("literal tilde", tilde.kind().value == "String" and tilde.decoded() == "~\n",
          f"{tilde.kind().value} {tilde.decoded()!r}")
    null_text = root.mapping_entry(1).value().scalar()
    check("folded null", null_text.kind().value == "String" and null_text.decoded() == "null\n")

    # Explicit tag validation
    try:
        ps("!!int nope\n")
        check("invalid explicit tag", False, "no error")
    except YamlFormationFailure as error:
        check("invalid explicit tag", error.code == "yaml.scalar.invalid-explicit-tag@1",
              error.code)
    try:
        ps("!!seq {a: b}\n")
        check("tag kind mismatch", False, "no error")
    except YamlFormationFailure as error:
        check("tag kind mismatch", error.code == "yaml.tag.kind-mismatch@1", error.code)

    # Quoted keywords are strings
    ok = True
    for keyword in ("~", "null", "true", "0o17", "2001-12-15"):
        for quote in ('"', "'"):
            doc = ps(f"{quote}{keyword}{quote}\n")
            scalar = doc.document(0).root().scalar()
            if scalar.kind().value != "String" or scalar.decoded() != keyword:
                ok = False
    check("quoted keywords", ok)

    # Null spellings
    ok = True
    for spelling in ("~", "null", "Null", "NULL", ""):
        source = f"a: {spelling}\n" if spelling else "a:\n"
        doc = ps(source)
        scalar = doc.document(0).root().mapping_entry(0).value().scalar()
        if scalar.kind().value != "Null" or scalar.canonical() != "":
            ok = False
    check("null spellings", ok)

    # Nesting depth limit
    try:
        parse(b"[[x]]", YamlProfile.YAML12_CORE_V1, ParseLimits(max_nesting_depth=1))
        check("nesting depth", False, "no error")
    except YamlFormationFailure as error:
        check("nesting depth", error.code == "core.parse.resource-limit@1"
              and error.name == "nesting-depth", error.code)

    # Version directive conflict
    try:
        ps("%YAML 1.1\n---\nyes\n", YamlProfile.YAML12_CORE_V1)
        check("version directive", False, "no error")
    except YamlFormationFailure as error:
        check("version directive", error.code == "yaml.profile.version-directive@1", error.code)

    # Timestamp and binary projection (1.1)
    doc = ps("bytes: !!binary SGVsbG8=\ntime: !!timestamp 2001-12-15T02:59:43Z\n",
             YamlProfile.YAML11_COMPAT_V1)
    projected = project_value(doc, ValueProjectionRequest.best_exact_v1())
    root = projected.value.as_object()
    check("binary projection", root[0][1].as_bytes() == b"Hello")
    check("timestamp projection", root[1][1].as_offset_date_time() is not None)

    # Multi-document cardinality
    doc = ps("---\na\n---\nb\n")
    result = project_value(doc, ValueProjectionRequest.best_exact_v1())
    check("document cardinality", result.code == "yaml.projection.document-cardinality@1")

    # Value projection limit
    doc = ps("[one, two]\n")
    from consema.yaml.projection import ValueProjectionLimits
    result = project_value(doc, ValueProjectionRequest.best_exact_v1().with_limits(
        ValueProjectionLimits(max_value_nodes=1)))
    check("value node limit", result.code == "yaml.projection.resource-limit@1"
          and result.resource_name == "max_value_nodes")

    # Edit: removal without dependency
    doc = ps("seq:\n  - one\n  - two\n")
    target = doc.document(0).root().mapping_entry(0).value().sequence_item(0)
    builder = EditTransactionBuilder(doc)
    builder.remove_sequence_element(target.node_ref())
    result = commit(doc, builder.build())
    check("removal without dependency", result.document.render() == b"seq:\n  - two\n",
          result.document.render().decode())

    # Edit: rename updates only dependent aliases
    doc = ps("first: &x [one]\ncopy: *x\nother: &x [two]\ncopy2: *x\n")
    target = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.rename_anchor(target.anchor_node_ref(), "renamed")
    result = commit(doc, builder.build())
    check("rename dependent aliases", result.document.render() ==
          b"first: &renamed [one]\ncopy: *renamed\nother: &x [two]\ncopy2: *x\n"
          and result.document.alias(0).name() == "renamed"
          and result.document.alias(1).name() == "x",
          result.document.render().decode())

    # Edit: dry-run digest matches commit
    doc = ps("first: &x [one]\ncopy: *x\n")
    target = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.rename_anchor(target.anchor_node_ref(), "renamed")
    transaction = builder.build()
    result = commit(doc, transaction)
    plan = dry_run(doc, transaction, EditPlanSourceId.new("config.yaml"))
    check("dry run digest", plan.target_digest() == result.source_patch.target_digest)

    # Edit: wrong snapshot
    first = ps("a: 1\n")
    second = ps("a: 1\n")
    builder = EditTransactionBuilder(first)
    builder.semantic_scalar(
        second.document(0).root().mapping_entry(0).value().node_ref(),
        PortableValue.integer(2), RepresentationPolicy.PRESERVE_COMPATIBLE)
    try:
        commit(first, builder.build())
        check("wrong snapshot", False, "no error")
    except YamlEditFailure as error:
        check("wrong snapshot", error.code == "core.edit.wrong-snapshot@1", error.code)

    # Edit: duplicate target (two inserts on the same container share the
    # same target, edit.rs:1974-2014 duplicate-target check).
    doc = ps("seq: [one, two]\n")
    sequence = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.insert_sequence_element(sequence.node_ref(), PortableValue.boolean(True),
                                    AssociationPlacement(kind="End"))
    builder.insert_sequence_element(sequence.node_ref(), PortableValue.boolean(False),
                                    AssociationPlacement(kind="Start"))
    try:
        commit(doc, builder.build())
        check("duplicate target", False, "no error")
    except YamlEditFailure as error:
        check("duplicate target", error.code == "core.edit.conflicting-edits@1", error.code)

    # Edit: structural container conflict (distinct targets, one container).
    doc = ps("seq: [one, two]\n")
    sequence = doc.document(0).root().mapping_entry(0).value()
    builder = EditTransactionBuilder(doc)
    builder.insert_sequence_element(sequence.node_ref(), PortableValue.boolean(True),
                                    AssociationPlacement(kind="End"))
    builder.remove_sequence_element(sequence.sequence_item(0).node_ref())
    try:
        commit(doc, builder.build())
        check("container conflict", False, "no error")
    except YamlEditFailure as error:
        check("container conflict", error.code == "yaml.edit.structural-container-conflict@1",
              error.code)

    # Query: mapping entries roles
    from consema.protocol.query import (
        CapabilityId,
        CapabilitySet,
        ExpressionKind,
        OperatorCall,
        QueryDefinition,
        QueryDomain,
        QueryExpression,
    )
    from consema.yaml import execute_yaml_query
    from consema.yaml import YamlQueryLimits
    from consema.yaml import YamlCancellationToken

    def pipeline_executable(domain_id: str, pipeline: list[str]):
        operators = []
        for entry in pipeline:
            name, version = entry.rsplit("@", 1)
            operators.append(OperatorCall(name, int(version)))
        definition = QueryDefinition(QueryDomain(domain_id, 1))
        expression = QueryExpression(ExpressionKind.INPUT)
        for operator in operators:
            expression = expression.then(operator)
        capabilities = CapabilitySet()
        capabilities.insert(CapabilityId("core.query.ordered-results", 1))
        return definition.with_expression(expression).validate().bind(capabilities)

    doc = ps("{a: 1, b: 2}\n")
    executable = pipeline_executable(
        "yaml.native-semantic-query",
        ["yaml.documents@1", "yaml.document-root@1", "yaml.try-mapping-entries@1"])
    execution = execute_yaml_query(
        executable, doc, YamlQueryLimits(), YamlCancellationToken())
    check("query mapping entries", [m.kind.value for m in execution.matches] ==
          ["MappingEntry", "MappingEntry"])

    # Query: alias target
    doc = ps("[&x {k: v}, *x]\n")
    executable = pipeline_executable(
        "yaml.native-semantic-query",
        ["yaml.alias-occurrences@1", "yaml.alias-target@1"])
    execution = execute_yaml_query(executable, doc, YamlQueryLimits(), YamlCancellationToken())
    anchored = doc.document(0).root().sequence_item(0).node()
    check("query alias target", len(execution.matches) == 1
          and execution.matches[0].node == anchored.node_ref())

    # Query: resource limit
    from consema.protocol.query import QueryFailure, QueryFailureKind
    doc = ps("[a, b, c]\n")
    executable = pipeline_executable(
        "yaml.native-semantic-query",
        ["yaml.documents@1", "yaml.document-root@1", "yaml.try-sequence-elements@1"])
    try:
        execute_yaml_query(executable, doc, YamlQueryLimits(max_results=2),
                           YamlCancellationToken())
        check("query resource limit", False, "no error")
    except QueryFailure as error:
        check("query resource limit", error.kind is QueryFailureKind.RESOURCE_LIMIT
              and error.code == "core.query.resource-limit@1", error.code)

    # Query: syntax comments (ordinals 5, 12)
    from consema.yaml import execute_yaml_syntax_query
    doc = ps("a: 1 # first\nb: 2 # second\n")
    definition = QueryDefinition(QueryDomain("yaml.lossless-syntax-query", 1))
    expression = (
        QueryExpression(ExpressionKind.INPUT)
        .then(OperatorCall("yaml.syntax-kind-is", 1).with_argument(
            "kind", PortableValue.string("Comment")))
    )
    executable = definition.with_expression(expression).validate().bind(
        _capabilities())
    execution = execute_yaml_syntax_query(
        executable, doc, YamlQueryLimits(), YamlCancellationToken())
    check("query comment ordinals", [m.ordinal for m in execution.matches] == [5, 12],
          str([m.ordinal for m in execution.matches]))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("ALL EXTENDED CHECKS PASSED")
    return 0


def _capabilities():
    from consema.protocol.query import CapabilityId, CapabilitySet

    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


if __name__ == "__main__":
    sys.exit(main())
