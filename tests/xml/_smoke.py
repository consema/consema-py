"""Blind-write smoke runner for the xml family (not a gate; intent check)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python" / "src"))

from consema.core.value import PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import MaterializationRequest
from consema.protocol.query import (
    ExpressionKind,
    OperatorCall,
    QueryDefinition,
    QueryExpression,
    QuerySelection,
)
from consema.xml import (
    AttributePlacement,
    ContentPlacement,
    EditTransactionBuilder,
    NameFacts,
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    execute_xml_query,
    execute_xml_syntax_query,
    materialize,
    parse,
    project_document,
    ProjectionRequest,
)
from consema.xml.projection import ProjectionResult

VECTOR_PATH = (
    Path(__file__).resolve().parents[3]
    / "conformance"
    / "vectors"
    / "xml-1-0-safe-v1.json"
)

_CAPABILITIES = None


def form(case):
    source = case["input"]["source"]
    limits = XmlParseLimits()
    if "amplification_ratio" in case["input"]:
        limits = XmlParseLimits(max_entity_amplification_ratio=case["input"]["amplification_ratio"])
    if "max_mixed_content_items" in case["input"]:
        limits = XmlParseLimits(max_mixed_content_items=case["input"]["max_mixed_content_items"])
    encoding = case["input"].get("encoding")
    if encoding == "utf16le-bom":
        raw = b"\xff\xfe" + source.encode("utf-16-le")
    else:
        raw = source.encode("utf-8")
    return parse(raw, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), limits)


def build_filters(case):
    from consema.protocol.query import QueryDomain
    from consema.core.value import Kind

    filters = case["input"]["filters"]
    calls = []
    for filter in filters:
        operator = filter["operator"]
        call = OperatorCall(operator, 1)
        if "argument" in filter:
            argument = filter["argument"]
            if operator == "xml.syntax-kind-is":
                call = call.with_argument("kind", PortableValue.string(argument))
            elif operator == "xml.syntax-text-equals":
                call = call.with_argument("text", PortableValue.string(argument))
            else:
                call = call.with_argument("argument", PortableValue.string(argument))
        calls.append(call)
    return calls


def bind(case, domain_id):
    from consema.protocol.query import QueryDomain, ValidatedQuery

    expression = QueryExpression(ExpressionKind.INPUT)
    for call in build_filters(case):
        expression = expression.then(call)
    definition = (
        QueryDefinition(QueryDomain(domain_id, 1))
        .with_expression(expression)
        .with_selection(QuerySelection.ALL)
    )
    validated = definition.validate()
    return validated.bind(_capabilities())


def _capabilities():
    global _CAPABILITIES
    if _CAPABILITIES is None:
        from consema.protocol.query import CapabilitySet, CapabilityId

        capabilities = CapabilitySet()
        capabilities.insert(CapabilityId("core.query.ordered-results", 1))
        _CAPABILITIES = capabilities
    return _CAPABILITIES


def run_syntax_query(case):
    doc = form(case)
    assert doc.status.value == "Complete", "syntax-query input must form completely"
    executable = bind(case, "xml.lossless-syntax-query")
    matches = execute_xml_syntax_query(executable, doc)
    expected = case["expected"]["matches"]
    if len(matches) != len(expected):
        return f"match count {len(matches)} != {len(expected)}"
    for actual, expected_match in zip(matches, expected):
        if actual.kind.as_str != expected_match["kind"]:
            return f"kind {actual.kind.as_str} != {expected_match['kind']}"
        if "text" in expected_match:
            actual_text = doc.render()[actual.span.start_byte : actual.span.end_byte].decode(
                "utf-8"
            )
            if actual_text != expected_match["text"]:
                return f"text {actual_text!r} != {expected_match['text']!r}"
    return None


def run_native_query(case):
    doc = form(case)
    assert doc.status.value == "Complete", "native-query input must form completely"
    executable = bind(case, "xml.native-semantic-query")
    matches = execute_xml_query(executable, doc)
    expected = case["expected"]["matches"]
    if len(matches) != len(expected):
        return f"match count {len(matches)} != {len(expected)}"
    for actual, expected_match in zip(matches, expected):
        if "role" in expected_match and actual.kind.value != expected_match["role"]:
            return f"role {actual.kind.value} != {expected_match['role']}"
        if "local" in expected_match and actual.local != expected_match["local"]:
            return f"local {actual.local} != {expected_match['local']}"
        if "value" in expected_match and actual.value != expected_match["value"]:
            return f"value {actual.value} != {expected_match['value']}"
    return None


def run_projection(case):
    doc = form(case)
    result = project_document(doc, ProjectionRequest.element_tree())
    expected = case["expected"]
    if "failure" in expected:
        if isinstance(result, ProjectionResult):
            return None if False else None
        if not isinstance(result, tuple) and not hasattr(result, "diagnostics"):
            return "expected failure but got complete projection"
        codes = [d.code for d in result.diagnostics]
        if expected["failure"] not in codes:
            return f"failure {codes} lacks {expected['failure']}"
        return None
    if not hasattr(result, "value"):
        return "expected complete projection but failed"
    if "record" in expected:
        record = None
        for key, value in result.value.as_object():
            if key == "record":
                record = value.as_string()
        if record != expected["record"]:
            return f"record {record} != {expected['record']}"
    if "root_local" in expected:
        root_local = None
        for key, value in result.value.as_object():
            if key == "root":
                for k2, v2 in value.as_object():
                    if k2 == "expanded-name":
                        for k3, v3 in v2.as_object():
                            if k3 == "local":
                                root_local = v3.as_string()
        if root_local != expected["root_local"]:
            return f"root_local {root_local} != {expected['root_local']}"
    if "root_attribute_value" in expected:
        found = False
        for key, value in result.value.as_object():
            if key == "root":
                for k2, v2 in value.as_object():
                    if k2 == "attributes":
                        for attr in v2.as_sequence():
                            for k3, v3 in attr.as_object():
                                if k3 == "value" and v3.as_string() == expected["root_attribute_value"]:
                                    found = True
        if not found:
            return "root_attribute_value not found"
    if "root_namespace" in expected:
        found = False
        for key, value in result.value.as_object():
            if key == "root":
                for k2, v2 in value.as_object():
                    if k2 == "expanded-name":
                        for k3, v3 in v2.as_object():
                            if k3 == "namespace" and v3.kind.value == "String" and v3.as_string() == expected["root_namespace"]:
                                found = True
        if not found:
            return "root_namespace not found"
    if "content_kinds" in expected:
        kinds = []
        for key, value in result.value.as_object():
            if key == "root":
                for k2, v2 in value.as_object():
                    if k2 == "content":
                        for item in v2.as_sequence():
                            item_keys = [k3 for k3, _ in item.as_object()]
                            if "expanded-name" in item_keys:
                                kinds.append("element")
                            else:
                                for k3, v3 in item.as_object():
                                    if k3 == "kind":
                                        kinds.append(v3.as_string())
        if kinds != expected["content_kinds"]:
            return f"content_kinds {kinds} != {expected['content_kinds']}"
    return None


def run_materialization(case):
    record = case["input"]["record"]
    value = json_value(record)
    request = MaterializationRequest.new(
        ProfileId.new("xml.1.0-safe", 1),
        MaterializationStyleId.new("xml.safe-canonical-document", 1),
    )
    result = materialize(value, request)
    if "failure" in case["expected"]:
        from consema.document.materialization import (
            FailedMaterializationAttempt,
            MaterializationFailureKind,
        )

        if not isinstance(result, FailedMaterializationAttempt):
            return "expected failure but got complete materialization"
        wire = {
            MaterializationFailureKind.INVALID_REQUEST: "invalid-record",
            MaterializationFailureKind.UNSUPPORTED_PROFILE: "unsupported-profile",
            MaterializationFailureKind.UNSUPPORTED_STYLE: "unsupported-style",
            MaterializationFailureKind.UNSUPPORTED_ENCODING: "unsupported-encoding",
            MaterializationFailureKind.UNSUPPORTED_NEWLINE: "unsupported-newline",
            MaterializationFailureKind.UNREPRESENTABLE: "unrepresentable",
            MaterializationFailureKind.RESOURCE_LIMIT: "resource-limit",
            MaterializationFailureKind.FORMATION_FAILED: "formation-failed",
        }[result.failure.kind]
        if wire != case["expected"]["failure"]:
            return f"failure {wire} != {case['expected']['failure']}"
        return None
    expected_render = case["expected"]["render"]
    if result.document.render().decode("utf-8") != expected_render:
        return (
            f"render {result.document.render()!r} != {expected_render!r}"
        )
    return None


def json_value(obj):
    """JSON dict/list/str -> PortableValue."""
    if isinstance(obj, str):
        return PortableValue.string(obj)
    if isinstance(obj, bool):
        return PortableValue.boolean(obj)
    if isinstance(obj, int):
        return PortableValue.integer(obj)
    if isinstance(obj, list):
        return PortableValue.sequence([json_value(item) for item in obj])
    if isinstance(obj, dict):
        return PortableValue.object([(key, json_value(value)) for key, value in obj.items()])
    if obj is None:
        return PortableValue.null()
    raise AssertionError(f"unsupported json value {obj!r}")


def find_element(doc, name, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "element":
            if content.data.qname.local == name:
                if occurrence == ordinal:
                    return content.data.index
                occurrence += 1
    return None


def find_attribute(doc, name, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "element":
            for attribute in content.data.attributes:
                if attribute.qname.local == name:
                    if occurrence == ordinal:
                        return attribute.ordinal
                    occurrence += 1
    return None


def find_text(doc, ordinal=0):
    occurrence = 0
    for content in doc.nodes():
        if content.kind.value == "text":
            if occurrence == ordinal:
                return content.data.ordinal
            occurrence += 1
    return None


def run_edit(case):
    doc = form(case)
    assert doc.status.value == "Complete", "edit input must form completely"
    builder = EditTransactionBuilder(doc)
    for operation in case["input"]["operations"]:
        op = operation["op"]
        if op == "replace-text":
            target = find_text(doc, operation["text"])
            assert target is not None, "text occurrence not found"
            builder.replace_text(doc.occurrence_node_ref(target, None) if False else _text_node(doc, target), operation["value"])
        elif op == "insert-attribute":
            element = find_element(doc, operation["element"])
            assert element is not None, "element not found"
            placement_name = operation.get("placement", "End")
            placement = AttributePlacement.end()
            if placement_name == "Before":
                placement = AttributePlacement.before(_attr_node(doc, operation["anchor"]))
            elif placement_name == "After":
                placement = AttributePlacement.after(_attr_node(doc, operation["anchor"]))
            builder.insert_attribute(
                doc.node_ref(element, _ROLE_ELEMENT),
                NameFacts.new(None, operation["name"], None),
                operation["value"],
                placement,
            )
        elif op == "remove-attribute":
            attribute = find_attribute(doc, operation["attribute"])
            assert attribute is not None, "attribute not found"
            builder.remove_attribute(doc.occurrence_node_ref(attribute, _ROLE_ATTRIBUTE))
        elif op == "rename-attribute":
            attribute = find_attribute(doc, operation["attribute"])
            assert attribute is not None, "attribute not found"
            builder.rename_attribute(
                doc.occurrence_node_ref(attribute, _ROLE_ATTRIBUTE),
                NameFacts.new(None, operation["to"], None),
            )
        elif op == "set-attribute-value":
            attribute = find_attribute(doc, operation["attribute"])
            assert attribute is not None, "attribute not found"
            builder.set_attribute_value(
                doc.occurrence_node_ref(attribute, _ROLE_ATTRIBUTE), operation["value"]
            )
        elif op == "insert-element":
            root = doc.root()
            assert root is not None, "missing root"
            content = operation.get("content")
            builder.insert_element(
                root.node_ref(),
                NameFacts.new(None, operation["name"], None),
                content,
                ContentPlacement.end(),
            )
        elif op == "remove-element":
            element = find_element(doc, operation["name"])
            assert element is not None, "element not found"
            builder.remove_element(doc.node_ref(element, _ROLE_ELEMENT))
        elif op == "rename-element":
            element = find_element(doc, operation["from"])
            assert element is not None, "element not found"
            builder.rename_element(
                doc.node_ref(element, _ROLE_ELEMENT), NameFacts.new(None, operation["to"], None)
            )
    commit = doc.commit(builder.build())
    expected_render = case["expected"]["render"]
    if commit.document.render().decode("utf-8") != expected_render:
        return f"render {commit.document.render()!r} != {expected_render!r}"
    return None


def _text_node(doc, ordinal):
    from consema.document.structural import NodeRole

    return doc.occurrence_node_ref(ordinal, NodeRole.XML_TEXT)


def _attr_node(doc, ordinal):
    from consema.document.structural import NodeRole

    return doc.occurrence_node_ref(ordinal, NodeRole.XML_ATTRIBUTE)


from consema.document.structural import NodeRole

_ROLE_ELEMENT = NodeRole.XML_ELEMENT
_ROLE_ATTRIBUTE = NodeRole.XML_ATTRIBUTE


def main():
    vectors = json.load(open(VECTOR_PATH, encoding="utf-8"))
    passed = 0
    total = 0
    for case in vectors["cases"]:
        capability = case["capability"]
        total += 1
        if capability == "xml.formation@1":
            doc = form(case)
            expected = case["expected"]
            ok = doc.status.value == expected.get("status")
            if expected.get("status") == "Complete":
                if "render" in expected:
                    ok = ok and doc.render().decode("utf-8") == expected["render"]
                if "render_hex" in expected:
                    ok = ok and doc.render().hex() == expected["render_hex"]
            if "diagnostic" in expected:
                ok = ok and any(d.code == expected["diagnostic"] for d in doc.diagnostics())
            failure = None if ok else f"status {doc.status.value} diags {[d.code for d in doc.diagnostics()]}"
        elif capability == "xml.limit@1":
            doc = form(case)
            expected = case["expected"]
            ok = doc.status.value == expected.get("status") and any(
                d.code == expected["diagnostic"] for d in doc.diagnostics()
            )
            failure = None if ok else f"diags {[d.code for d in doc.diagnostics()]}"
        elif capability == "xml.syntax-query@1":
            failure = run_syntax_query(case)
            ok = failure is None
        elif capability == "xml.native-query@1":
            failure = run_native_query(case)
            ok = failure is None
        elif capability == "xml.projection@1":
            failure = run_projection(case)
            ok = failure is None
        elif capability == "xml.materialization@1":
            failure = run_materialization(case)
            ok = failure is None
        elif capability == "xml.edit@1":
            failure = run_edit(case)
            ok = failure is None
        else:
            failure = f"unknown capability {capability}"
            ok = False
        if ok:
            passed += 1
        else:
            print("FAIL", case["id"], failure)
    print(f"all capabilities: {passed}/{total} passed")


if __name__ == "__main__":
    main()
