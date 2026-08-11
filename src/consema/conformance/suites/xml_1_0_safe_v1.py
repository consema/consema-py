"""Suite ``consema.xml-1-0-safe.conformance@1`` (xml-1-0-safe-v1.json, 34
cases): XML 1.0 safe-profile formation with recovery, lossless syntax query,
native semantic query, element-tree projection, canonical materialization,
and the eight structural edits. Dispatch is by the ``capability`` field,
mirroring go/conformance/xml_1_0_safe_v1.go.
"""

from __future__ import annotations

from dataclasses import replace

from consema.conformance import compare
from consema.conformance import runner
from consema.core.value import Kind, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import MaterializationRequest
from consema.document.structural import NodeRole
from consema.protocol import query as protocol_query
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.xml import edit as xml_edit
from consema.xml import materialization as xml_materialization
from consema.xml import parser as xml_parser
from consema.xml import projection as xml_projection
from consema.xml import query as xml_query
from consema.xml.document import XmlContentKind, XmlProfile
from consema.xml.errors import XmlFormationFailure
from consema.xml.kinds import XmlSyntaxKind
from consema.xml.parser import XmlEncodingSelection, XmlParseLimits


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    for vector in data.cases:
        capability = vector.capability
        if capability in ("xml.formation@1", "xml.limit@1"):
            message = _formation_case(vector)
        elif capability == "xml.syntax-query@1":
            message = _syntax_query_case(vector)
        elif capability == "xml.native-query@1":
            message = _native_query_case(vector)
        elif capability == "xml.projection@1":
            message = _projection_case(vector)
        elif capability == "xml.materialization@1":
            message = _materialization_case(vector)
        elif capability == "xml.edit@1":
            message = _edit_case(vector)
        else:
            message = "runner does not recognize published capability " + capability
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# Shared formation
# ---------------------------------------------------------------------------


def _form_document(vector: runner.Case):
    """Forms the case document under the frozen profile and the case-specific
    limits; returns (document, None) or (None, failure message)."""
    source = compare.string_field(vector.input, "source")
    if source is None:
        return None, "missing input.source"
    if compare.string_field(vector.input, "encoding") == "utf16le-bom":
        raw = b"\xff\xfe" + source.encode("utf-16-le")
    else:
        raw = source.encode("utf-8")
    limits = XmlParseLimits()
    amplification = compare.integer_field(vector.input, "amplification_ratio")
    if amplification is not None:
        limits = replace(limits, max_entity_amplification_ratio=amplification)
    mixed = compare.integer_field(vector.input, "max_mixed_content_items")
    if mixed is not None:
        limits = replace(limits, max_mixed_content_items=mixed)
    try:
        document = xml_parser.parse(raw, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), limits)
    except XmlFormationFailure as failure:
        return None, "formation: " + failure.code
    return document, None


def _assert_formation_facts(document, expected: PortableValue) -> str | None:
    status = compare.string_field(expected, "status")
    if status is None:
        return "missing expected.status"
    actual_status = document.formation_status.value
    if actual_status != status:
        return f"status {actual_status} != {status}"
    if status == "Complete":
        render = compare.string_field(expected, "render")
        if render is not None and document.render() != render.encode("utf-8"):
            return f"render {document.render()!r} != {render!r}"
        hex_text = compare.string_field(expected, "render_hex")
        if hex_text is not None and document.render().hex() != hex_text:
            return f"render_hex {document.render().hex()} != {hex_text}"
    diagnostic = compare.string_field(expected, "diagnostic")
    if diagnostic is not None:
        codes = [item.code for item in document.diagnostics()]
        if diagnostic not in codes:
            return f"diagnostic {diagnostic} not found in {codes!r}"
    return None


def _formation_case(vector: runner.Case) -> str | None:
    document, message = _form_document(vector)
    if message:
        return message
    return _assert_formation_facts(document, vector.expected)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def _xml_filters(vector: runner.Case):
    """Builds the operator chain from the vector filters."""
    filters = compare.sequence_field(vector.input, "filters")
    if filters is None:
        return None, "missing input.filters"
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    for filter_value in filters:
        operator = compare.string_field(filter_value, "operator")
        if operator is None:
            return None, "missing filter.operator"
        call = protocol_query.OperatorCall(operator, 1)
        argument = compare.string_field(filter_value, "argument")
        if argument is not None:
            if operator == "xml.syntax-kind-is":
                call = call.with_argument("kind", PortableValue.string(argument))
            elif operator == "xml.syntax-text-equals":
                call = call.with_argument("text", PortableValue.string(argument))
            else:
                call = call.with_argument("argument", PortableValue.string(argument))
        expression = expression.then(call)
    return expression, None


def _bind(domain: protocol_query.QueryDomain, expression) -> protocol_query.ExecutableQuery:
    definition = (
        protocol_query.QueryDefinition(domain).with_expression(expression).validate()
    )
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return definition.bind(capabilities)


def _syntax_query_case(vector: runner.Case) -> str | None:
    document, message = _form_document(vector)
    if message:
        return "syntax-query: " + message
    if document.formation_status.value != "Complete":
        return "syntax-query input must form completely"
    expression, message = _xml_filters(vector)
    if message:
        return message
    executable = _bind(protocol_query.domain_xml_lossless_syntax_v1(), expression)
    matches = xml_query.execute_xml_syntax_query(
        executable, document, xml_query.QueryLimits(), xml_query.CancellationToken()
    )
    expected_matches = compare.sequence_field(vector.expected, "matches")
    if expected_matches is None:
        return "missing expected.matches"
    if len(matches) != len(expected_matches):
        return f"match count {len(matches)} != {len(expected_matches)}"
    decoded = document.source().decoded_text()
    if decoded is None:
        decoded = document.source().bytes().decode("utf-8", errors="replace")
    for index, match in enumerate(matches):
        expected = expected_matches[index]
        kind = compare.string_field(expected, "kind")
        if kind is None:
            return "missing expected match kind"
        if match.kind.value != kind:
            return f"kind {match.kind.value} != {kind}"
        text = compare.string_field(expected, "text")
        if text is not None:
            actual = decoded[match.span.start_byte : match.span.end_byte]
            if actual != text:
                return f"text {actual!r} != {text!r}"
    return None


def _native_query_case(vector: runner.Case) -> str | None:
    document, message = _form_document(vector)
    if message:
        return "native-query: " + message
    if document.formation_status.value != "Complete":
        return "native-query input must form completely"
    expression, message = _xml_filters(vector)
    if message:
        return message
    executable = _bind(protocol_query.domain_xml_native_v1(), expression)
    matches = xml_query.execute_xml_query(
        executable, document, xml_query.QueryLimits(), xml_query.CancellationToken()
    )
    expected_matches = compare.sequence_field(vector.expected, "matches")
    if expected_matches is None:
        return "missing expected.matches"
    if len(matches) != len(expected_matches):
        return f"match count {len(matches)} != {len(expected_matches)}"
    for index, match in enumerate(matches):
        expected = expected_matches[index]
        local = compare.string_field(expected, "local")
        if local is not None:
            if match.kind is not xml_query.XmlMatchKind.ELEMENT and match.kind is not xml_query.XmlMatchKind.ATTRIBUTE:
                return "unexpected match kind"
            if match.local != local:
                return f"local {match.local} != {local}"
        value = compare.string_field(expected, "value")
        if value is not None:
            if match.kind is not xml_query.XmlMatchKind.ATTRIBUTE:
                return "expected attribute match"
            if match.value != value:
                return f"value {match.value} != {value}"
    return None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _object_string(value: PortableValue, name: str) -> str | None:
    field = compare.object_field(value, name)
    if field is None or field.kind is not Kind.STRING:
        return None
    return field.as_string()


def _projection_case(vector: runner.Case) -> str | None:
    document, message = _form_document(vector)
    if message:
        return "projection: " + message
    result = xml_projection.project_document(
        document, xml_projection.ProjectionRequest.element_tree()
    )
    expected_failure = compare.string_field(vector.expected, "failure")
    if expected_failure is not None:
        if not isinstance(result, xml_projection.FailedProjectionAttempt):
            return "projection must fail"
        code = result.diagnostics[0].code if result.diagnostics else ""
        if code != expected_failure:
            return f"failure code {code} != {expected_failure}"
        return None
    if isinstance(result, xml_projection.FailedProjectionAttempt):
        return "projection must complete"
    projection = result
    record = _object_string(projection.value, "record")
    expected_record = compare.string_field(vector.expected, "record")
    if expected_record is not None and record != expected_record:
        return f"record {record} != {expected_record}"
    root_value = compare.object_field(projection.value, "root")
    if root_value is None:
        return "missing root"
    root_local = compare.string_field(vector.expected, "root_local")
    if root_local is not None:
        expanded = compare.object_field(root_value, "expanded-name")
        if expanded is None:
            return "missing expanded-name"
        local = _object_string(expanded, "local")
        if local != root_local:
            return f"root_local {local} != {root_local}"
    root_namespace = compare.string_field(vector.expected, "root_namespace")
    if root_namespace is not None:
        expanded = compare.object_field(root_value, "expanded-name")
        if expanded is None:
            return "missing expanded-name"
        namespace = compare.object_field(expanded, "namespace")
        if namespace is None or namespace.kind is not Kind.STRING:
            return "missing expanded-name.namespace"
        if namespace.as_string() != root_namespace:
            return f"root_namespace {namespace.as_string()} != {root_namespace}"
    attribute_value = compare.string_field(vector.expected, "root_attribute_value")
    if attribute_value is not None:
        attributes = compare.sequence_field(root_value, "attributes")
        if not attributes:
            return "missing attributes sequence"
        first = attributes[0]
        value = _object_string(first, "value")
        if value != attribute_value:
            return f"attribute value {value} != {attribute_value}"
    content_kinds = compare.sequence_field(vector.expected, "content_kinds")
    if content_kinds is not None:
        content = compare.sequence_field(root_value, "content")
        if content is None:
            return "missing content sequence"
        if len(content) != len(content_kinds):
            return f"content count {len(content)} != {len(content_kinds)}"
        for index, item in enumerate(content):
            expected_kind = content_kinds[index].as_string()
            if compare.object_field(item, "expanded-name") is not None:
                actual_kind = "element"
            else:
                actual_kind = _object_string(item, "kind")
            if actual_kind != expected_kind:
                return f"kind {actual_kind} != {expected_kind}"
    return None


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


_XML_FAILURE_SPELLING = {
    "invalid-request": "invalid-record",
    "unsupported-profile": "unsupported-profile",
    "unsupported-style": "unsupported-style",
    "unsupported-encoding": "unsupported-encoding",
    "unsupported-newline": "unsupported-newline",
    "unrepresentable": "unrepresentable",
    "resource-limit": "resource-limit",
    "formation-failed": "formation-failed",
}


def _materialization_request() -> MaterializationRequest:
    return MaterializationRequest.new(
        ProfileId.new("xml.1.0-safe", 1),
        MaterializationStyleId.new("xml.safe-canonical-document", 1),
    )


def _materialization_case(vector: runner.Case) -> str | None:
    record = compare.object_field(vector.input, "record")
    if record is None:
        return "missing input.record"
    result = xml_materialization.materialize(record, _materialization_request())
    expected_failure = compare.string_field(vector.expected, "failure")
    if expected_failure is not None:
        if not isinstance(result, xml_materialization.FailedMaterializationAttempt):
            return "materialization must fail"
        actual = _XML_FAILURE_SPELLING.get(result.failure.kind.value, result.failure.kind.value)
        if actual != expected_failure:
            return f"failure {actual} != {expected_failure}"
        if len(result.analyzed_input_paths) > _materialization_request().limits.max_input_nodes:
            return "analyzed_input_paths exceeds max_input_nodes"
        return None
    if not isinstance(result, xml_materialization.CompleteMaterialization):
        return "materialization must complete"
    render = compare.string_field(vector.expected, "render")
    if render is None:
        return "missing expected.render"
    actual = result.document.render().decode("utf-8")
    if actual != render:
        return f"render {actual!r} != {render!r}"
    return None


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def _occurrence(operation: PortableValue, name: str) -> int:
    ordinal = compare.integer_field(operation, name)
    return ordinal or 0


def _find_attribute(document, name: str, ordinal: int):
    occurrence = 0
    for content in document.nodes():
        if content.kind is not XmlContentKind.ELEMENT:
            continue
        for attribute in content.data.attributes:
            if attribute.qname.local == name:
                if occurrence == ordinal:
                    return document.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE)
                occurrence += 1
    return None


def _find_element(document, name: str, ordinal: int):
    occurrence = 0
    for index, content in enumerate(document.nodes()):
        if content.kind is not XmlContentKind.ELEMENT:
            continue
        if content.data.qname.local == name:
            if occurrence == ordinal:
                return document.occurrence_node_ref(index, NodeRole.XML_ELEMENT)
            occurrence += 1
    return None


def _find_text(document, ordinal: int):
    occurrence = 0
    for content in document.nodes():
        if content.kind is not XmlContentKind.TEXT:
            continue
        if occurrence == ordinal:
            return document.occurrence_node_ref(content.data.ordinal, NodeRole.XML_TEXT)
        occurrence += 1
    return None


def _find_anchor_attribute(document, element_ref, name: str):
    index = element_ref.index
    nodes = document.nodes()
    if index >= len(nodes) or nodes[index].kind is not XmlContentKind.ELEMENT:
        return None
    for attribute in nodes[index].data.attributes:
        if attribute.qname.local == name:
            return document.occurrence_node_ref(attribute.ordinal, NodeRole.XML_ATTRIBUTE)
    return None


def _edit_case(vector: runner.Case) -> str | None:
    document, message = _form_document(vector)
    if message:
        return "edit: " + message
    if document.formation_status.value != "Complete":
        return "edit input must form completely"
    operations = compare.sequence_field(vector.input, "operations")
    if operations is None:
        return "missing input.operations"
    builder = xml_edit.EditTransactionBuilder(document)
    for operation in operations:
        op = compare.string_field(operation, "op")
        if op is None:
            return "missing op"
        if op == "replace-text":
            value = compare.string_field(operation, "value")
            if value is None:
                return "missing value"
            target = _find_text(document, _occurrence(operation, "text"))
            if target is None:
                return "text occurrence not found"
            builder.replace_text(target, value)
        elif op == "insert-attribute":
            element_name = compare.string_field(operation, "element")
            name = compare.string_field(operation, "name")
            value = compare.string_field(operation, "value")
            if element_name is None or name is None or value is None:
                return "missing element/name/value"
            target = _find_element(document, element_name, _occurrence(operation, "ordinal"))
            if target is None:
                return "element not found"
            placement_name = compare.string_field(operation, "placement") or "End"
            if placement_name == "End":
                placement = xml_edit.AttributePlacement.end()
            elif placement_name == "Before":
                anchor = _find_anchor_attribute(document, target, compare.string_field(operation, "anchor") or "")
                if anchor is None:
                    return "anchor attribute not found"
                placement = xml_edit.AttributePlacement.before(anchor)
            elif placement_name == "After":
                anchor = _find_anchor_attribute(document, target, compare.string_field(operation, "anchor") or "")
                if anchor is None:
                    return "anchor attribute not found"
                placement = xml_edit.AttributePlacement.after(anchor)
            else:
                return "unknown placement " + placement_name
            builder.insert_attribute(target, xml_edit.NameFacts.new(None, name, None), value, placement)
        elif op == "remove-attribute":
            name = compare.string_field(operation, "attribute")
            if name is None:
                return "missing attribute"
            target = _find_attribute(document, name, _occurrence(operation, "ordinal"))
            if target is None:
                return "attribute not found"
            builder.remove_attribute(target)
        elif op == "rename-attribute":
            from_name = compare.string_field(operation, "attribute")
            to_name = compare.string_field(operation, "to")
            if from_name is None or to_name is None:
                return "missing attribute/to"
            target = _find_attribute(document, from_name, _occurrence(operation, "ordinal"))
            if target is None:
                return "attribute not found"
            builder.rename_attribute(target, xml_edit.NameFacts.new(None, to_name, None))
        elif op == "set-attribute-value":
            name = compare.string_field(operation, "attribute")
            value = compare.string_field(operation, "value")
            if name is None or value is None:
                return "missing attribute/value"
            target = _find_attribute(document, name, _occurrence(operation, "ordinal"))
            if target is None:
                return "attribute not found"
            builder.set_attribute_value(target, value)
        elif op == "insert-element":
            root = document.root()
            if root is None:
                return "missing root"
            name = compare.string_field(operation, "name")
            if name is None:
                return "missing name"
            content = compare.string_field(operation, "content")
            builder.insert_element(
                root.node_ref(),
                xml_edit.NameFacts.new(None, name, None),
                content,
                xml_edit.ContentPlacement.end(),
            )
        elif op == "remove-element":
            name = compare.string_field(operation, "name")
            if name is None:
                return "missing name"
            target = _find_element(document, name, _occurrence(operation, "ordinal"))
            if target is None:
                return "element not found"
            builder.remove_element(target)
        elif op == "rename-element":
            from_name = compare.string_field(operation, "from")
            to_name = compare.string_field(operation, "to")
            if from_name is None or to_name is None:
                return "missing from/to"
            target = _find_element(document, from_name, _occurrence(operation, "ordinal"))
            if target is None:
                return "element not found"
            builder.rename_element(target, xml_edit.NameFacts.new(None, to_name, None))
        else:
            return "unknown edit op " + op
    commit = document.commit(builder.build())
    render = compare.string_field(vector.expected, "render")
    if render is None:
        return "missing expected.render"
    actual = commit.document.render().decode("utf-8")
    if actual != render:
        return f"render {actual!r} != {render!r}"
    return None


runner.register_suite("xml-1-0-safe-v1.json", "consema.xml-1-0-safe.conformance@1", "", 34, run)
