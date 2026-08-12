"""Canonical PortableGraph and PortableValue materialization for YAML.

Authority (language-neutral first; Rust only for byte arbitration):

- RFC 0007 s11 (docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md:303-353):
  yaml.canonical-block@1 and yaml.canonical-flow@1; canonical graph
  numbering; deterministic anchors ``&g0``, ``&g1``, ... for nodes whose
  topology requires an alias; the first serialization occurrence defines the
  anchor before child edges; cross-document sharing fails; v1 styles emit
  every retained standard repository tag explicitly; newline and
  UTF-8/UTF-16 target encoding policies; output is reparsed under the target
  profile before a Complete result.
- The writer grammar is crates/consema-yaml/src/materialization.rs:207-238
  (analyze/write/reparse/provenance), 292-401 (GraphLayout: anchor names for
  nodes with more than one occurrence), 430-717 (GraphWriter block/flow
  rendering), 719-728 (scalar presentation: float canonical ``e0``), and
  775-819 (encode_output: UTF-16 output always carries the matching BOM).
- Value materialization: materialization.rs:1058-1144 (prepare_value,
  UniqueStringEntriesToObject event, value_graph, exact reprojection),
  1146-1335 (prepare/prepare_mapping), 1337-1454 (value_graph with the
  standard tags).
- Vector surface: conformance/vectors/yaml-v1.json cases
  materialization.graph-cycle-flow (``--- &g0 !!seq [!!str "one", *g0]\\n``)
  and materialization.value-flow (``--- !!map {? !!str "a" : !!seq
  [!!int "1", !!bool "true"]}\\n``), both byte-exact.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace

from consema.core.value import Decimal, PortableValue, Kind
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationInputLocation,
    MaterializationInputLocationKind,
    MaterializationLimits,
    MaterializationProvenanceEntry,
    MaterializationProvenanceMap,
    MaterializationRelation,
    MaterializationReport,
    MaterializationRequest,
    MaterializedOrigin,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding, SourceEncodingKind
from consema.graph import (
    GraphBuilder,
    GraphLimits,
    GraphMappingEntry,
    GraphNodeId,
    GraphNodeKind,
)
from consema.yaml.errors import (
    YamlDiagnostic,
    YamlGraphMaterializationFailure,
    YamlGraphMaterializationFailureKind,
    YamlSeverity,
)
from consema.yaml.kinds import YamlProfile
from consema.yaml.parser import (
    TAG_BINARY,
    TAG_BOOL,
    TAG_FLOAT,
    TAG_INT,
    TAG_MAP,
    TAG_MERGE,
    TAG_NULL,
    TAG_OMAP,
    TAG_PAIRS,
    TAG_SEQ,
    TAG_SET,
    TAG_STR,
    TAG_TIMESTAMP,
    TAG_VALUE,
    TAG_YAML,
    parse,
)
from consema.yaml.projection import (
    CompleteValueProjection,
    Fidelity,
    ValueProjectionRequest,
    project_graph,
    project_value,
)
from consema.protocol.error_registry import DiagnosticCategory

# Frozen materialization style ids (RFC 0007 s11).
YAML_CANONICAL_BLOCK_STYLE = "yaml.canonical-block@1"
YAML_CANONICAL_FLOW_STYLE = "yaml.canonical-flow@1"

BITS_POSITIVE_INFINITY = 0x7FF0000000000000
BITS_NEGATIVE_INFINITY = 0xFFF0000000000000
BITS_NAN = 0x7FF8000000000000


class YamlStyle(enum.Enum):
    """Canonical presentation style (materialization.rs:240-244)."""

    BLOCK = "block"
    FLOW = "flow"


def requested_profile(request: MaterializationRequest) -> YamlProfile:
    """Resolves the target profile (materialization.rs:246-257)."""
    profile = request.target_profile
    if profile.id == "yaml.1.2-core" and profile.version == 1:
        return YamlProfile.YAML12_CORE_V1
    if profile.id == "yaml.1.1-compat" and profile.version == 1:
        return YamlProfile.YAML11_COMPAT_V1
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_PROFILE)


def requested_style(request: MaterializationRequest) -> YamlStyle:
    """Resolves the target style (materialization.rs:259-265)."""
    style = request.style
    if style.id == "yaml.canonical-block" and style.version == 1:
        return YamlStyle.BLOCK
    if style.id == "yaml.canonical-flow" and style.version == 1:
        return YamlStyle.FLOW
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_STYLE)


def _requested_output_contract(request: MaterializationRequest) -> None:
    """Encoding/newline validation (materialization.rs:267-280)."""
    encoding = request.encoding
    if encoding.kind not in (
        SourceEncodingKind.UTF8,
        SourceEncodingKind.UTF16LE,
        SourceEncodingKind.UTF16BE,
    ):
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    if request.newline is NewlinePolicy.NONE:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_NEWLINE)


def _parse_limits(limits: MaterializationLimits) -> ParseLimits:
    return ParseLimits(
        max_source_bytes=limits.max_output_bytes,
        max_nesting_depth=limits.max_depth,
        max_token_count=limits.max_output_bytes,
        max_node_count=limits.max_input_nodes * 4,
        max_diagnostics=limits.max_report_entries,
    )


class _GraphLayout:
    """Anchor names for nodes whose topology requires an alias
    (materialization.rs:292-401)."""

    def __init__(self, anchor_names: dict[GraphNodeId, int]) -> None:
        self.anchor_names = anchor_names


def _analyze_layout(graph, limits: MaterializationLimits) -> _GraphLayout:
    canonical: list[GraphNodeId] = []
    canonical_ids: dict[GraphNodeId, int] = {}
    stack: list[tuple[GraphNodeId, int]] = [
        (root, 0) for root in reversed(graph.roots())
    ]
    while stack:
        node_id, depth = stack.pop()
        if node_id in canonical_ids:
            continue
        if depth > limits.max_depth:
            raise MaterializationFailure(MaterializationFailureKind.RESOURCE_LIMIT, name="input-depth")
        if len(canonical) >= limits.max_input_nodes:
            raise MaterializationFailure(MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes")
        node = graph.node(node_id)
        if node is None:
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST, detail="foreign graph node"
            )
        _validate_tag_kind(node_id, node.tag, node.kind)
        canonical_ids[node_id] = len(canonical)
        canonical.append(node_id)
        child_depth = depth + 1
        if node.kind is GraphNodeKind.SEQUENCE:
            stack.extend(
                (child, child_depth) for child in reversed(node.sequence_items() or ())
            )
        elif node.kind is GraphNodeKind.MAPPING:
            for entry in reversed(node.mapping_entries() or ()):
                stack.append((entry.value, child_depth))
                stack.append((entry.key, child_depth))
    document_owner: dict[GraphNodeId, int] = {}
    occurrences: dict[GraphNodeId, int] = {}
    for root_ordinal, root in enumerate(graph.roots()):
        seen: set[GraphNodeId] = set()
        pending = [root]
        occurrences[root] = occurrences.get(root, 0) + 1
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id in document_owner:
                raise YamlGraphMaterializationFailure(
                    YamlGraphMaterializationFailureKind.CROSS_DOCUMENT_SHARING, node=node_id
                )
            document_owner[node_id] = root_ordinal
            node = graph.node(node_id)
            if node is None:
                raise YamlGraphMaterializationFailure(
                    YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
                )
            if node.kind is GraphNodeKind.SEQUENCE:
                for child in node.sequence_items() or ():
                    occurrences[child] = occurrences.get(child, 0) + 1
                    pending.append(child)
            elif node.kind is GraphNodeKind.MAPPING:
                for entry in node.mapping_entries() or ():
                    for child in (entry.key, entry.value):
                        occurrences[child] = occurrences.get(child, 0) + 1
                        pending.append(child)
    anchor_names = {
        node_id: anchor
        for anchor, node_id in enumerate(
            node_id for node_id in canonical if occurrences.get(node_id, 0) > 1
        )
    }
    return _GraphLayout(anchor_names)


def _validate_tag_kind(node_id, tag: str, kind: GraphNodeKind) -> None:
    """Standard tag/kind compatibility (materialization.rs:403-428)."""
    scalar_tags = (TAG_NULL, TAG_BOOL, TAG_INT, TAG_FLOAT, TAG_STR, TAG_TIMESTAMP, TAG_BINARY,
                   TAG_MERGE, TAG_VALUE, TAG_YAML)
    sequence_tags = (TAG_SEQ, TAG_OMAP, TAG_PAIRS)
    mapping_tags = (TAG_MAP, TAG_SET)
    if tag in scalar_tags:
        compatible = kind is GraphNodeKind.SCALAR
    elif tag in sequence_tags:
        compatible = kind is GraphNodeKind.SEQUENCE
    elif tag in mapping_tags:
        compatible = kind is GraphNodeKind.MAPPING
    else:
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.UNSUPPORTED_TAG, node=node_id, tag=tag
        )
    if not compatible:
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.TAG_KIND_MISMATCH, node=node_id, tag=tag
        )


class _BoundedText:
    """Output buffer with the configured byte limit (materialization.rs:730-773)."""

    def __init__(self, max_bytes: int) -> None:
        self.text: list[str] = []
        self.length = 0
        self.max = max_bytes

    def push(self, value: str) -> None:
        length = self.length + len(value)
        if length > self.max:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        self.text.append(value)
        self.length = length

    def finish(self) -> str:
        return "".join(self.text)


def _scalar_presentation(tag: str, canonical: str) -> str:
    """The frozen scalar presentation (materialization.rs:719-728): a float
    canonical without ``.``/``e``/``E`` gains ``e0``."""
    if (
        tag == TAG_FLOAT
        and canonical not in (".inf", "-.inf", ".nan")
        and not any(marker in canonical for marker in (".", "e", "E"))
    ):
        return f"{canonical}e0"
    return canonical


class _GraphWriter:
    """Deterministic canonical writer (materialization.rs:430-717)."""

    def __init__(
        self,
        graph,
        layout: _GraphLayout,
        style: YamlStyle,
        request: MaterializationRequest,
    ) -> None:
        self.graph = graph
        self.layout = layout
        self.style = style
        self.newline = request.newline.bytes.decode("utf-8")
        self.limits = request.limits
        self.output = _BoundedText(request.limits.max_output_bytes)
        self.emitted: set[GraphNodeId] = set()

    def stream(self) -> str:
        for ordinal, root in enumerate(self.graph.roots()):
            if ordinal != 0:
                self.output.push(self.newline)
            self.emitted = set()
            self.output.push("---")
            if self.style is YamlStyle.BLOCK:
                self._block_after_indicator(root, 0, 0)
            else:
                self.output.push(" ")
                self._flow_node(root, 0)
            self.output.push(self.newline)
        return self.output.finish()

    def _flow_node(self, node_id: GraphNodeId, depth: int) -> None:
        if self._write_alias_if_emitted(node_id):
            return
        self._begin_definition(node_id, depth)
        self._write_properties(node_id)
        node = self.graph.node(node_id)
        if node.kind is GraphNodeKind.SCALAR:
            self.output.push(" ")
            self._write_quoted(_scalar_presentation(node.tag, node.scalar_content()))
            return
        if node.kind is GraphNodeKind.SEQUENCE:
            self.output.push(" [")
            for index, child in enumerate(node.sequence_items() or ()):
                if index != 0:
                    self.output.push(", ")
                self._flow_node(child, depth + 1)
            self.output.push("]")
            return
        self.output.push(" {")
        for index, entry in enumerate(node.mapping_entries() or ()):
            if index != 0:
                self.output.push(", ")
            self.output.push("? ")
            self._flow_node(entry.key, depth + 1)
            self.output.push(" : ")
            self._flow_node(entry.value, depth + 1)
        self.output.push("}")

    def _block_after_indicator(self, node_id: GraphNodeId, child_indent: int, depth: int) -> None:
        if node_id in self.emitted:
            self.output.push(" ")
            self._write_alias(node_id)
            return
        node = self.graph.node(node_id)
        if node.kind is GraphNodeKind.SEQUENCE:
            block = bool(node.sequence_items())
        elif node.kind is GraphNodeKind.MAPPING:
            block = bool(node.mapping_entries())
        else:
            block = False
        self._begin_definition(node_id, depth)
        self.output.push(" ")
        self._write_properties(node_id)
        if block:
            self.output.push(self.newline)
            self._block_content(node_id, child_indent, depth)
            return
        if node.kind is GraphNodeKind.SCALAR:
            self.output.push(" ")
            self._write_quoted(_scalar_presentation(node.tag, node.scalar_content()))
            return
        if node.kind is GraphNodeKind.SEQUENCE:
            self.output.push(" []")
            return
        self.output.push(" {}")

    def _block_content(self, node_id: GraphNodeId, indent: int, depth: int) -> None:
        node = self.graph.node(node_id)
        if node.kind is GraphNodeKind.SEQUENCE:
            for index, child in enumerate(node.sequence_items() or ()):
                if index != 0:
                    self.output.push(self.newline)
                self._indent(indent)
                self.output.push("-")
                self._block_after_indicator(child, indent + 2, depth + 1)
            return
        for index, entry in enumerate(node.mapping_entries() or ()):
            if index != 0:
                self.output.push(self.newline)
            self._indent(indent)
            self.output.push("?")
            self._block_after_indicator(entry.key, indent + 2, depth + 1)
            self.output.push(self.newline)
            self._indent(indent)
            self.output.push(":")
            self._block_after_indicator(entry.value, indent + 2, depth + 1)

    def _begin_definition(self, node_id: GraphNodeId, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-depth"
            )
        self.emitted.add(node_id)

    def _write_alias_if_emitted(self, node_id: GraphNodeId) -> bool:
        if node_id in self.emitted:
            self._write_alias(node_id)
            return True
        return False

    def _write_alias(self, node_id: GraphNodeId) -> None:
        anchor = self.layout.anchor_names.get(node_id)
        if anchor is None:
            raise YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
            )
        self.output.push(f"*g{anchor}")

    def _write_properties(self, node_id: GraphNodeId) -> None:
        anchor = self.layout.anchor_names.get(node_id)
        if anchor is not None:
            self.output.push(f"&g{anchor} ")
        tag = self.graph.node(node_id).tag
        if not tag.startswith("tag:yaml.org,2002:"):
            raise YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.UNSUPPORTED_TAG, node=node_id, tag=tag
            )
        self.output.push(f"!!{tag[len('tag:yaml.org,2002:'):]}")

    def _write_quoted(self, value: str) -> None:
        self.output.push('"')
        for character in value:
            if character == '"':
                self.output.push('\\"')
            elif character == "\\":
                self.output.push("\\\\")
            elif character == "\b":
                self.output.push("\\b")
            elif character == "\t":
                self.output.push("\\t")
            elif character == "\n":
                self.output.push("\\n")
            elif character == "\f":
                self.output.push("\\f")
            elif character == "\r":
                self.output.push("\\r")
            elif ord(character) <= 0x1F or ord(character) == 0x7F:
                self.output.push(f"\\u{ord(character):04x}")
            else:
                self.output.push(character)
        self.output.push('"')

    def _indent(self, spaces: int) -> None:
        self.output.push(" " * spaces)


def _encode_output(text: str, encoding: SourceEncoding, max_bytes: int) -> bytes:
    """Encodes the canonical text; UTF-16 always carries the matching BOM
    (materialization.rs:775-819)."""
    if encoding.kind is SourceEncodingKind.UTF8:
        if len(text) > max_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        return text.encode("utf-8")
    if encoding.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        length = len(text.encode("utf-16-be")) + 2
        if length > max_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        bom = b"\xff\xfe" if encoding.kind is SourceEncodingKind.UTF16LE else b"\xfe\xff"
        return bom + text.encode("utf-16-le" if encoding.kind is SourceEncodingKind.UTF16LE else "utf-16-be")
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)


def materialize_graph(graph, request: MaterializationRequest):
    """Materializes one complete PortableGraph as a canonical YAML stream
    (materialization.rs:191-238)."""
    analyzed: list[GraphNodeId] = []
    try:
        complete = _materialize_graph_complete(graph, request, analyzed)
        return complete
    except MaterializationFailure as failure:
        return FailedGraphMaterializationAttempt(
            failure=YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.MATERIALIZATION,
                materialization_code=failure.code,
            ),
            analyzed_input_nodes=tuple(analyzed),
        )
    except YamlGraphMaterializationFailure as failure:
        return FailedGraphMaterializationAttempt(failure=failure, analyzed_input_nodes=tuple(analyzed))


@dataclass(frozen=True, slots=True)
class CompleteGraphMaterialization:
    """Complete exact PortableGraph-to-YAML materialization
    (materialization.rs:169-180)."""

    document: object
    fidelity: MaterializationFidelity
    report: MaterializationReport
    provenance: object


@dataclass(frozen=True, slots=True)
class FailedGraphMaterializationAttempt:
    """Failed graph attempt without a Document or partial output bytes
    (materialization.rs:160-167)."""

    failure: YamlGraphMaterializationFailure
    analyzed_input_nodes: tuple = ()


def _materialize_graph_complete(graph, request: MaterializationRequest, analyzed: list):
    profile = requested_profile(request)
    style = requested_style(request)
    _requested_output_contract(request)
    layout = _analyze_layout(graph, request.limits)
    writer = _GraphWriter(graph, layout, style, request)
    text = writer.stream()
    raw = _encode_output(text, request.encoding, request.limits.max_output_bytes)
    try:
        document = parse(raw, profile, _parse_limits(request.limits))
    except Exception:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED) from None
    try:
        reparsed = project_graph(document)
    except Exception:
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
        ) from None
    if reparsed != graph:
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
        )
    provenance = _collect_graph_provenance(graph, document, request.limits)
    return CompleteGraphMaterialization(
        document=document,
        fidelity=MaterializationFidelity.EXACT,
        report=MaterializationReport(),
        provenance=provenance,
    )


def _collect_graph_provenance(graph, document, limits: MaterializationLimits):
    """Graph input locations mapped to generated YAML origins
    (materialization.rs:821-1056)."""
    entries: list[list] = []  # [input_location, [origins...]]
    units = 0
    seen: set[GraphNodeId] = set()

    def push(input_location, origin: MaterializedOrigin) -> None:
        nonlocal units
        units += 2
        if units > limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        entries.append([input_location, [origin]])

    def add(input_location, origin: MaterializedOrigin) -> None:
        nonlocal units
        units += 1
        if units > limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        for entry in entries:
            if entry[0] == input_location:
                entry[1].append(origin)
                return
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
        )

    def origin(node, span, relation: MaterializationRelation) -> MaterializedOrigin:
        return MaterializedOrigin(
            snapshot=document.snapshot_identity(), node=node, span=span, relation=relation
        )

    if len(graph.roots()) != document.document_count():
        raise YamlGraphMaterializationFailure(
            YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
        )
    for index, input_root in enumerate(graph.roots()):
        output_document = document.document(index)
        if output_document is None:
            raise YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
            )
        push(
            MaterializationInputLocation.value(("root", index)),
            origin(output_document.node_ref(), output_document.span(), MaterializationRelation.GENERATED),
        )
        _collect_graph_node(graph, input_root, output_document.root(), seen, push, add, origin)
    return tuple(
        MaterializationProvenanceEntry(input=entry[0], outputs=tuple(entry[1]))
        for entry in entries
    )


def _collect_graph_node(graph, input, output, seen, push, add, origin) -> None:
    if input in seen:
        return
    seen.add(input)
    node = graph.node(input)
    output_node = output
    if node.kind is GraphNodeKind.SEQUENCE:
        if output_node.sequence_len() != len(node.sequence_items() or ()):
            raise YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
            )
        for index, child in enumerate(node.sequence_items() or ()):
            edge = output_node.sequence_item(index)
            if edge is None:
                raise YamlGraphMaterializationFailure(
                    YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
                )
            location = MaterializationInputLocation.value(("sequence", input.as_u64(), index))
            push(location, origin(edge.node_ref(), edge.span(), MaterializationRelation.DIRECT))
            alias = edge.alias()
            if alias is not None:
                add(location, origin(alias.node_ref(), alias.span(), MaterializationRelation.REENCODED))
            _collect_graph_node(graph, child, edge.node(), seen, push, add, origin)
        return
    if node.kind is GraphNodeKind.MAPPING:
        if output_node.mapping_len() != len(node.mapping_entries() or ()):
            raise YamlGraphMaterializationFailure(
                YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
            )
        for index, entry in enumerate(node.mapping_entries() or ()):
            output_entry = output_node.mapping_entry(index)
            if output_entry is None:
                raise YamlGraphMaterializationFailure(
                    YamlGraphMaterializationFailureKind.ROUND_TRIP_MISMATCH
                )
            for role, alias in (("key", output_entry.key_alias()), ("value", output_entry.value_alias())):
                location = MaterializationInputLocation.value(
                    (role, input.as_u64(), index)
                )
                push(
                    location,
                    origin(output_entry.node_ref(), output_entry.span(), MaterializationRelation.DIRECT),
                )
                if alias is not None:
                    add(
                        location,
                        origin(alias.node_ref(), alias.span(), MaterializationRelation.REENCODED),
                    )
            _collect_graph_node(graph, entry.key, output_entry.key(), seen, push, add, origin)
            _collect_graph_node(graph, entry.value, output_entry.value(), seen, push, add, origin)
        return


# -- value materialization ---------------------------------------------------


def materialize_value(value: PortableValue, request: MaterializationRequest):
    """Materializes one complete PortableValue into a canonical YAML document
    (materialization.rs:1058-1144)."""
    try:
        return _materialize_value_complete(value, request)
    except MaterializationFailure as failure:
        return FailedMaterializationAttempt(
            failure=failure,
            report=MaterializationReport(),
            analyzed_input_paths=(),
        )


def _materialize_value_complete(value: PortableValue, request: MaterializationRequest):
    requested_profile(request)
    requested_style(request)
    _requested_output_contract(request)
    prepared = _prepare_value(value, 0, request)
    graph = _value_graph(prepared, request.limits)
    graph_limits = request.limits
    graph_request = replace(
        request,
        limits=MaterializationLimits(
            max_input_nodes=graph_limits.max_input_nodes * 2 + 1,
            max_output_bytes=graph_limits.max_output_bytes,
            max_depth=graph_limits.max_depth,
            max_report_entries=graph_limits.max_report_entries,
            max_provenance_entries=graph_limits.max_provenance_entries,
        ),
    )
    try:
        graph_complete = _materialize_graph_complete(graph, graph_request, [])
    except (YamlGraphMaterializationFailure, MaterializationFailure):
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED) from None
    document = graph_complete.document
    projected = project_value(document, ValueProjectionRequest.best_exact_v1())
    if isinstance(projected, CompleteValueProjection):
        if projected.fidelity is Fidelity.EXACT and projected.value == prepared:
            return CompleteMaterialization(
                document=document,
                fidelity=MaterializationFidelity.EXACT,
                report=MaterializationReport(),
                provenance=MaterializationProvenanceMap(),
            )
    raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)


def _prepare_value(value: PortableValue, depth: int, request: MaterializationRequest) -> PortableValue:
    if depth > request.limits.max_depth:
        raise MaterializationFailure(
            MaterializationFailureKind.RESOURCE_LIMIT, name="input-depth"
        )
    kind = value.kind
    if kind in (
        Kind.NULL, Kind.BOOLEAN, Kind.INTEGER, Kind.DECIMAL, Kind.STRING, Kind.BYTES,
    ):
        return value
    if kind is Kind.BINARY_FLOAT64 and value.as_binary_float64() in (
        BITS_POSITIVE_INFINITY, BITS_NEGATIVE_INFINITY, BITS_NAN,
    ):
        return value
    if kind is Kind.DATE:
        return value
    if kind is Kind.OFFSET_DATE_TIME:
        return value
    if kind is Kind.SEQUENCE:
        return PortableValue.sequence(
            [_prepare_value(child, depth + 1, request) for child in value.as_sequence()]
        )
    if kind is Kind.OBJECT:
        return PortableValue.object(
            [
                (name, _prepare_value(child, depth + 1, request))
                for name, child in value.as_object()
            ]
        )
    if kind is Kind.ENTRY_MAPPING:
        return _prepare_mapping(value.as_entry_mapping(), depth, request)
    raise MaterializationFailure(
        MaterializationFailureKind.UNREPRESENTABLE, detail=kind.value
    )


def _prepare_mapping(entries, depth: int, request: MaterializationRequest) -> PortableValue:
    names: set[str] = set()
    object_ = True
    for key, _ in entries:
        if key.kind is Kind.STRING and key.as_string() not in names:
            names.add(key.as_string())
        else:
            object_ = False
            break
    if object_ and request.mapping_policy is not MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT:
        raise MaterializationFailure(
            MaterializationFailureKind.UNREPRESENTABLE, kind=Kind.ENTRY_MAPPING.value
        )
    prepared = [
        (
            _prepare_value(key, depth + 1, request),
            _prepare_value(value, depth + 1, request),
        )
        for key, value in entries
    ]
    if object_:
        return PortableValue.object(
            [(key.as_string(), value) for key, value in prepared]
        )
    return PortableValue.entry_mapping(prepared)


def _value_graph(value: PortableValue, limits: MaterializationLimits) -> object:
    max_nodes = limits.max_input_nodes * 2 + 1
    builder = GraphBuilder(
        GraphLimits(
            max_roots=1,
            max_nodes=max_nodes,
            max_edges=max_nodes * 2,
            max_container_entries=limits.max_input_nodes,
            max_tag_bytes=64,
            max_scalar_bytes=limits.max_output_bytes,
            max_traversal_depth=limits.max_depth,
        )
    )
    root = _define_value_node(builder, value)
    builder.push_root(root)
    return builder.build()


def _define_value_node(builder: GraphBuilder, value: PortableValue) -> GraphNodeId:
    node_id = builder.reserve_node()
    kind = value.kind
    if kind is Kind.NULL:
        builder.define_scalar(node_id, TAG_NULL, "")
    elif kind is Kind.BOOLEAN:
        builder.define_scalar(
            node_id, TAG_BOOL, "true" if value.as_boolean() else "false"
        )
    elif kind is Kind.INTEGER:
        builder.define_scalar(node_id, TAG_INT, str(value.as_integer()))
    elif kind is Kind.DECIMAL:
        decimal = value.as_decimal()
        canonical = (
            str(decimal.coefficient)
            if decimal.exponent == 0
            else f"{decimal.coefficient}e{decimal.exponent}"
        )
        builder.define_scalar(node_id, TAG_FLOAT, canonical)
    elif kind is Kind.BINARY_FLOAT64:
        canonical = {
            BITS_POSITIVE_INFINITY: ".inf",
            BITS_NEGATIVE_INFINITY: "-.inf",
            BITS_NAN: ".nan",
        }.get(value.as_binary_float64())
        if canonical is None:
            raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
        builder.define_scalar(node_id, TAG_FLOAT, canonical)
    elif kind is Kind.STRING:
        builder.define_scalar(node_id, TAG_STR, value.as_string())
    elif kind is Kind.BYTES:
        builder.define_scalar(node_id, TAG_BINARY, _encode_base64(value.as_bytes()))
    elif kind is Kind.DATE:
        year, month, day = value.as_date()
        builder.define_scalar(
            node_id, TAG_TIMESTAMP, f"{year:04d}-{month:02d}-{day:02d}"
        )
    elif kind is Kind.OFFSET_DATE_TIME:
        local, offset = value.as_offset_date_time()
        date, time = local.as_local_date_time()
        year, month, day = date.as_date()
        hour, minute, second, fraction = time.as_time()
        fraction_text = _fraction_text(fraction)
        zone = _offset_text(offset)
        builder.define_scalar(
            node_id,
            TAG_TIMESTAMP,
            f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
            f"{fraction_text}{zone}",
        )
    elif kind is Kind.SEQUENCE:
        children = [
            _define_value_node(builder, child) for child in value.as_sequence()
        ]
        builder.define_sequence(node_id, TAG_SEQ, children)
    elif kind is Kind.OBJECT:
        entries = [
            GraphMappingEntry(
                _define_value_node(builder, PortableValue.string(name)),
                _define_value_node(builder, child),
            )
            for name, child in value.as_object()
        ]
        builder.define_mapping(node_id, TAG_MAP, entries)
    else:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
    return node_id


def _fraction_text(fraction: Decimal) -> str:
    if fraction.coefficient == 0:
        return ""
    digits = str(abs(fraction.coefficient))
    return "." + digits


def _offset_text(offset: int) -> str:
    sign = "+" if offset >= 0 else "-"
    absolute = abs(offset)
    hours, minutes = divmod(absolute, 3600)
    minutes //= 60
    return f"{sign}{hours:02d}:{minutes:02d}"


_BASE64_TABLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _encode_base64(data: bytes) -> str:
    output: list[str] = []
    for start in range(0, len(data), 3):
        chunk = data[start : start + 3]
        combined = int.from_bytes(chunk, "big")
        if len(chunk) == 3:
            output.append(_BASE64_TABLE[(combined >> 18) & 0x3F])
            output.append(_BASE64_TABLE[(combined >> 12) & 0x3F])
            output.append(_BASE64_TABLE[(combined >> 6) & 0x3F])
            output.append(_BASE64_TABLE[combined & 0x3F])
        elif len(chunk) == 2:
            output.append(_BASE64_TABLE[(combined >> 12) & 0x3F])
            output.append(_BASE64_TABLE[(combined >> 6) & 0x3F])
            output.append(_BASE64_TABLE[combined & 0x3F])
            output.append("=")
        else:
            output.append(_BASE64_TABLE[(combined >> 6) & 0x3F])
            output.append(_BASE64_TABLE[combined & 0x3F])
            output.append("==")
    return "".join(output)
