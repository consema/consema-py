"""Suite ``consema.semantic-model-v5.conformance@1`` (semantic-model-v5.json,
22 cases): the v5 registry facts, the portable-graph record and its PGCE/1
bytes, the graph query/provenance/projection records, the YAML query-result
record, and the v5 protocol envelope rejections. Dispatch is by case id,
mirroring go/conformance/semantic_model_v5.go.

The v5 record codecs that the Python protocol package does not implement
(portable-graph, graph-query-result, graph-provenance-map,
graph-projection-result, yaml-query-result) are transcribed locally from
go/protocol/records_graph.go and records_line_query.go, with the graph
value/byte machinery from the Python ``consema.graph`` package.
"""

from __future__ import annotations

import hashlib

from consema.conformance import compare
from consema.conformance import protocol_records as records
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.graph.errors import GraphBuildError, PgceDecodeError
from consema.graph.graph import GraphBuilder, GraphLimits, GraphMappingEntry
from consema.graph.pgce import PgceLimits, decode_pgce, encode_pgce, encode_pgce_bounded
from consema.protocol.contract import ContractId, ContractRegistry, ProtocolMessage
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.errors import (
    ProtocolError,
    ProtocolErrorKind,
    invalid,
    protocol_error,
    resource,
)
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import RegistryManifest
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    integer_value,
    nullable_string,
    optional_string,
    schema_fields,
    sequence_of,
    string_of,
    unsigned32,
    unsigned64,
)
from consema.protocol.query import MatchRole, QueryDomain


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "registry.v5-manifest": _registry_manifest,
        "registry.v1-v4-frozen": _registry_frozen,
        "registry.v5-additive-contracts": _registry_additions,
        "registry.v5-error-codes": _registry_error_codes,
        "portable-graph.dual-transport": _graph_transport,
        "portable-graph.reject-disagreement": _graph_disagreement,
        "portable-graph.reject-node-limit": _graph_limit,
        "graph-query.node-roundtrip": _graph_query,
        "graph-query.sequence-roundtrip": _graph_query,
        "graph-query.mapping-roundtrip": _graph_query,
        "graph-query.reject-dangling-association": _graph_query,
        "graph-provenance.reject-order": _graph_provenance_order,
        "graph-projection.roundtrip": _graph_projection,
        "graph-projection.reject-out-of-range": _graph_projection,
        "yaml-query.native-roles": _yaml_query_roundtrip,
        "yaml-query.syntax-roundtrip": _yaml_query_roundtrip,
        "yaml-query.reject-domain-role": _yaml_domain_rejection,
        "yaml-query.reject-process-local": _yaml_process_local,
        "protocol.v4-reject-v5-contract": _protocol_v4_rejection,
        "protocol.v5-nested-error-code": _protocol_nested_error,
        "protocol.reject-truncated-pvce": _protocol_truncated_pvce,
        "protocol.reject-unknown-payload-field": _protocol_unknown_field,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published v5 case")
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# registry facts
# ---------------------------------------------------------------------------


def _registry_manifest(vector: runner.Case) -> str | None:
    manifest = RegistryManifest.build(5, ContractRegistry(5), ErrorCodeRegistry(5))
    value = manifest.to_value()
    decoded = RegistryManifest.from_value(value)
    roundtrip_value = decoded.to_value()
    if not core_equal(roundtrip_value, value):
        return "manifest round-trip changed the record"
    semantic_model = compare.string_field(vector.expected, "semantic_model")
    contract_count = compare.integer_field(vector.expected, "contract_count")
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    if (
        decoded.semantic_model.schema() != semantic_model
        or len(decoded.contracts) != contract_count
        or len(decoded.error_codes) != error_code_count
    ):
        return "v5 manifest facts differ"
    return None


def _registry_frozen(vector: runner.Case) -> str | None:
    contract_counts = compare.integer_sequence(vector.expected, "contract_counts")
    error_counts = compare.integer_sequence(vector.expected, "error_code_counts")
    if contract_counts is None or error_counts is None:
        return "unexpected expectation facts"
    if len(contract_counts) != 4 or len(error_counts) != 4:
        return "unexpected expectation facts"
    current = RegistryManifest.build(5, ContractRegistry(5), ErrorCodeRegistry(5)).to_value()
    for index in range(4):
        version = index + 1
        manifest = RegistryManifest.build(
            version, ContractRegistry(version), ErrorCodeRegistry(version)
        )
        value = manifest.to_value()
        decoded = RegistryManifest.from_value(value)
        decoded.to_value()
        if (
            len(manifest.contracts) != contract_counts[index]
            or len(manifest.error_codes) != error_counts[index]
            or core_equal(value, current)
        ):
            return "a frozen registry changed"
    return None


def _registry_additions(vector: runner.Case) -> str | None:
    v4 = ContractRegistry(4)
    v5 = ContractRegistry(5)
    expected = compare.string_sequence(vector.expected, "contracts")
    if expected is None:
        return "missing expected.contracts"
    actual: list[str] = []
    for record in v5.contracts():
        if not any(old[0] == record[0] and old[1] == record[1] for old in v4.contracts()):
            actual.append(f"{record[0]}@{record[1]}")
    return compare.require_ordered(actual, expected, "v5 additions")


def _registry_error_codes(vector: runner.Case) -> str | None:
    v4 = ErrorCodeRegistry(4)
    v5 = ErrorCodeRegistry(5)
    expected = compare.string_sequence(vector.expected, "new_codes")
    if expected is None:
        return "missing expected.new_codes"
    actual = [descriptor.code for descriptor in v5.codes() if not v4.contains(descriptor.code)]
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    if len(v5.codes()) != error_code_count:
        return f"v5 error-code count {len(v5.codes())} != {error_code_count}"
    return compare.require_ordered(actual, expected, "v5 error additions")


# ---------------------------------------------------------------------------
# graph construction from the vector descriptor
# ---------------------------------------------------------------------------


def _graph_from_vector(graph_value) -> PortableGraph:
    roots_value = compare.sequence_field(graph_value, "roots")
    nodes_value = compare.sequence_field(graph_value, "nodes")
    if roots_value is None or nodes_value is None:
        raise ValueError("graph descriptor requires roots and nodes")
    builder = GraphBuilder()
    ids = [builder.reserve_node() for _ in nodes_value]
    for index, node_value in enumerate(nodes_value):
        kind = compare.string_field(node_value, "kind")
        tag = compare.string_field(node_value, "tag")
        if kind is None or tag is None:
            raise ValueError(f"graph node {index} requires kind and tag")
        if kind == "Scalar":
            content = compare.string_field(node_value, "content")
            if content is None:
                raise ValueError(f"scalar {index} content missing")
            builder.define_scalar(ids[index], tag, content)
        elif kind == "Sequence":
            items = compare.sequence_field(node_value, "items")
            if items is None:
                raise ValueError(f"sequence {index} items missing")
            builder.define_sequence(ids[index], tag, [ids[item.as_integer()] for item in items])
        elif kind == "Mapping":
            entries_value = compare.sequence_field(node_value, "entries")
            if entries_value is None:
                raise ValueError(f"mapping {index} entries missing")
            entries = []
            for entry_value in entries_value:
                key = compare.integer_field(entry_value, "key")
                value = compare.integer_field(entry_value, "value")
                if key is None or value is None:
                    raise ValueError(f"mapping {index} entry requires key and value")
                entries.append(GraphMappingEntry(ids[key], ids[value]))
            builder.define_mapping(ids[index], tag, entries)
        else:
            raise ValueError(f"unknown graph node kind {kind!r}")
    for root_value in roots_value:
        builder.push_root(ids[root_value.as_integer()])
    return builder.build()


def _pgce_limits(**overrides) -> PgceLimits:
    return PgceLimits(**overrides)


# ---------------------------------------------------------------------------
# core.portable-graph@1 codec (records_portable_graph.go)
# ---------------------------------------------------------------------------


def _graph_message_value(graph, pgce: bytes) -> PortableValue:
    """The exact readable graph plus PGCE/1 wire record (portable_graph.rs:44-126)."""
    order, canonical_ids = graph._canonical_layout()
    roots = [PortableValue.integer(canonical_ids[root.index]) for root in graph.roots()]
    nodes = []
    for wire_id, original in enumerate(order):
        node = graph._nodes[original]
        record = [
            ("id", PortableValue.integer(wire_id)),
            ("kind", PortableValue.string(node.kind.value)),
            ("tag", PortableValue.string(node.tag)),
        ]
        if node.content[0] == "scalar":
            record.append(("canonical_content", PortableValue.string(node.content[1])))
        elif node.content[0] == "sequence":
            items = [PortableValue.integer(canonical_ids[item.index]) for item in node.content[1]]
            record.append(("items", PortableValue.sequence(tuple(items))))
        else:
            entries = [
                PortableValue.object(
                    [
                        ("key", PortableValue.integer(canonical_ids[entry.key.index])),
                        ("value", PortableValue.integer(canonical_ids[entry.value.index])),
                    ]
                )
                for entry in node.content[1]
            ]
            record.append(("entries", PortableValue.sequence(tuple(entries))))
        nodes.append(PortableValue.object(record))
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.portable-graph@1")),
            ("encoding", PortableValue.string("PGCE/1")),
            ("roots", PortableValue.sequence(tuple(roots))),
            ("nodes", PortableValue.sequence(tuple(nodes))),
            ("pgce", PortableValue.bytes_value(pgce)),
        ]
    )


def _graph_message_from_value(value: PortableValue, limits: PgceLimits) -> PortableGraph:
    """Strictly decodes and cross-validates the readable graph and PGCE forms
    (portable_graph.rs:127-203)."""
    fields = schema_fields(
        value,
        "core.portable-graph@1",
        ["schema", "encoding", "roots", "nodes", "pgce"],
        "$",
    )
    encoding = string_of(fields[1], "$.encoding")
    if encoding != "PGCE/1":
        raise invalid("$.encoding", "expected PGCE/1")
    root_values = sequence_of(fields[2], "$.roots")
    node_values = sequence_of(fields[3], "$.nodes")
    _check_graph_count("$.roots", len(root_values), limits.max_roots)
    _check_graph_count("$.nodes", len(node_values), limits.max_nodes)
    pgce_value = fields[4]
    if pgce_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.pgce", "expected Bytes")
    pgce_bytes = pgce_value.as_bytes()
    _check_graph_count("$.pgce", len(pgce_bytes), limits.max_stream_bytes)
    builder = GraphBuilder(limits.graph_limits())
    ids = [builder.reserve_node() for _ in node_values]
    for index, record_value in enumerate(node_values):
        path = f"$.nodes[{index}]"
        record = _node_record(record_value, index, path)
        kind = record[1]
        if kind == "Scalar":
            fields_s = exact_fields(
                record_value, ["id", "kind", "tag", "canonical_content"], path
            )
            try:
                builder.define_scalar(ids[index], string_of(fields_s[2], path + ".tag"), string_of(fields_s[3], path + ".canonical_content"))
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
        elif kind == "Sequence":
            fields_s = exact_fields(record_value, ["id", "kind", "tag", "items"], path)
            items = []
            for item_index, item_value in enumerate(sequence_of(fields_s[3], path + ".items")):
                items.append(_resolve_graph_id(ids, item_value, path + f".items[{item_index}]"))
            try:
                builder.define_sequence(ids[index], string_of(fields_s[2], path + ".tag"), items)
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
        else:
            fields_s = exact_fields(record_value, ["id", "kind", "tag", "entries"], path)
            entries = []
            for entry_index, entry_value in enumerate(sequence_of(fields_s[3], path + ".entries")):
                entry_path = path + f".entries[{entry_index}]"
                entry_fields = exact_fields(entry_value, ["key", "value"], entry_path)
                entries.append(
                    GraphMappingEntry(
                        _resolve_graph_id(ids, entry_fields[0], entry_path + ".key"),
                        _resolve_graph_id(ids, entry_fields[1], entry_path + ".value"),
                    )
                )
            try:
                builder.define_mapping(ids[index], string_of(fields_s[2], path + ".tag"), entries)
            except GraphBuildError as error:
                raise _map_graph_build_error(error) from None
    for index, root_value in enumerate(root_values):
        root_id = _resolve_graph_id(ids, root_value, f"$.roots[{index}]")
        try:
            builder.push_root(root_id)
        except GraphBuildError as error:
            raise _map_graph_build_error(error) from None
    try:
        built = builder.build()
    except GraphBuildError as error:
        raise _map_graph_build_error(error) from None
    order, _ = built._canonical_layout()
    if order != list(range(len(node_values))):
        raise invalid("$.nodes", "node records are not in canonical first-discovery order")
    try:
        decoded = decode_pgce(pgce_bytes, limits)
    except PgceDecodeError as error:
        raise invalid("$.pgce", str(error)) from None
    if built != decoded:
        raise invalid("$", "readable graph and PGCE graph are not strictly equal")
    try:
        canonical = encode_pgce_bounded(built, limits)
    except Exception as error:  # noqa: BLE001 — mapped to the pgce path
        raise invalid("$.pgce", str(error)) from None
    if canonical != pgce_bytes:
        raise invalid("$.pgce", "PGCE bytes disagree with readable graph")
    return built


def _node_record(value: PortableValue, index: int, path: str) -> tuple:
    if value.kind is not Kind.OBJECT or not value.as_object():
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected node Object")
    entries = value.as_object()
    if entries[0][0] != "id":
        raise invalid(path, "id must be the first field")
    canonical_id = unsigned64(entries[0][1], path + ".id")
    if canonical_id != index:
        raise invalid(path + ".id", "node records must carry canonical wire IDs")
    for key, item in entries[1:]:
        if key == "kind":
            return (canonical_id, string_of(item, path + ".kind"))
    raise invalid(path, "kind field is absent")


def _resolve_graph_id(ids, value: PortableValue, path: str):
    canonical = unsigned64(value, path)
    if canonical >= len(ids):
        raise invalid(path, "canonical node ID out of range")
    return ids[canonical]


def _check_graph_count(name: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise resource(name, "count exceeds configured limit")


def _map_graph_build_error(error: GraphBuildError) -> ProtocolError:
    return invalid("$.nodes", str(error))


def _graph_from_vector_case(vector: runner.Case):
    graph_value = compare.object_field(vector.input, "graph")
    if graph_value is None:
        raise ValueError("missing input.graph")
    return _graph_from_vector(graph_value)


# ---------------------------------------------------------------------------
# graph transport cases
# ---------------------------------------------------------------------------


def _dual_roundtrip(schema: str, payload: PortableValue, version: int) -> str | None:
    """Proves JSON/PVCE transport identity under one registry version."""
    contract_id, contract_version = schema.rsplit("@", 1)
    contract = ContractId(contract_id, int(contract_version))
    registry = ContractRegistry(version)
    envelope = ProtocolMessage(contract, payload, registry)
    limits = ProtocolLimits()
    json_bytes = envelope.to_json(limits)
    pvce_bytes = envelope.to_pvce(limits)
    decoded_json = ProtocolMessage.from_json(json_bytes, limits, registry)
    decoded_pvce = ProtocolMessage.from_pvce(pvce_bytes, limits, registry)
    if not core_equal(decoded_json.payload, envelope.payload):
        return "JSON transport did not close"
    if not core_equal(decoded_pvce.payload, envelope.payload):
        return "PVCE transport did not close"
    return None


def _expect_code(vector: runner.Case, error: BaseException | None) -> str | None:
    if error is None:
        return "record must be rejected"
    if not isinstance(error, ProtocolError):
        return f"unexpected error type: {error!r}"
    expected = compare.string_field(vector.expected, "code")
    if error.code != expected:
        return f"rejection {error.code} != {expected}"
    return None


def _graph_transport(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    pgce = encode_pgce(built)
    pgce_hex = compare.string_field(vector.expected, "pgce_hex")
    if pgce.hex() != pgce_hex:
        return f"pgce hex {pgce.hex()} != {pgce_hex}"
    payload = _graph_message_value(built, pgce)
    envelope = ProtocolMessage(
        ContractId("core.portable-graph", 1), payload, ContractRegistry(5)
    )
    limits = ProtocolLimits()
    json_bytes = envelope.to_json(limits)
    pvce_bytes = envelope.to_pvce(limits)
    registry = ContractRegistry(5)
    decoded_json = ProtocolMessage.from_json(json_bytes, limits, registry)
    decoded_pvce = ProtocolMessage.from_pvce(pvce_bytes, limits, registry)
    if not core_equal(decoded_json.payload, envelope.payload):
        return "transport identity differed"
    if not core_equal(decoded_pvce.payload, envelope.payload):
        return "transport identity differed"
    json_sha256 = compare.string_field(vector.expected, "json_sha256")
    pvce_sha256 = compare.string_field(vector.expected, "pvce_sha256")
    json_digest = hashlib.sha256(json_bytes).hexdigest()
    pvce_digest = hashlib.sha256(pvce_bytes).hexdigest()
    if json_digest != json_sha256:
        return f"json digest {json_digest} != {json_sha256}"
    if pvce_digest != pvce_sha256:
        return f"pvce digest {pvce_digest} != {pvce_sha256}"
    return None


def _replace_object_field(value: PortableValue, name: str, replacement: PortableValue) -> PortableValue:
    """Replaces one existing field or appends a new trailing field
    (Go replaceObjectField / appendObjectField)."""
    if value.kind is not Kind.OBJECT:
        raise ValueError("value must be Object")
    entries = []
    found = False
    for key, item in value.as_object():
        if key == name:
            entries.append((key, replacement))
            found = True
        else:
            entries.append((key, item))
    if not found:
        entries.append((name, replacement))
    return PortableValue.object(entries)


def _graph_disagreement(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    payload = _graph_message_value(built, encode_pgce(built))
    nodes = compare.sequence_field(payload, "nodes")
    if nodes is None:
        return "nodes field missing"
    index = compare.integer_field(vector.input, "node_index")
    replacement = compare.string_field(vector.input, "replacement")
    if index is None or replacement is None:
        return "missing input.node_index/replacement"
    changed_nodes = []
    for ordinal, node_value in enumerate(nodes):
        if ordinal == index:
            changed_nodes.append(_replace_object_field(node_value, "canonical_content", PortableValue.string(replacement)))
        else:
            changed_nodes.append(node_value)
    changed = _replace_object_field(payload, "nodes", PortableValue.sequence(tuple(changed_nodes)))
    try:
        _graph_message_from_value(changed, _pgce_limits())
    except ProtocolError as error:
        expected = compare.string_field(vector.expected, "code")
        if error.code != expected:
            return f"rejection {error.code} != {expected}"
        return None
    return "readable and PGCE forms unexpectedly agreed"


def _graph_limit(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    payload = _graph_message_value(built, encode_pgce(built))
    limit = compare.integer_field(vector.input, "max_nodes")
    if limit is None:
        return "missing input.max_nodes"
    try:
        _graph_message_from_value(payload, _pgce_limits(max_nodes=limit))
    except ProtocolError as error:
        expected = compare.string_field(vector.expected, "code")
        if error.code != expected:
            return f"rejection {error.code} != {expected}"
        return None
    return "record must be rejected"


# ---------------------------------------------------------------------------
# core.graph-query-result@1 codec (records_graph.go)
# ---------------------------------------------------------------------------


def _parse_graph_role(text: str) -> MatchRole | None:
    return records.parse_match_role(text)


def _graph_match_role(kind: str) -> MatchRole:
    if kind == "Node":
        return MatchRole.GRAPH_NODE
    if kind == "SequenceElement":
        return MatchRole.GRAPH_SEQUENCE_ELEMENT
    if kind == "MappingEntry":
        return MatchRole.GRAPH_MAPPING_ENTRY
    raise invalid("$", "unknown graph query match kind")


def _graph_match_value(match) -> PortableValue:
    kind = match["kind"]
    if kind == "Node":
        return PortableValue.object(
            [("kind", PortableValue.string("Node")), ("node", PortableValue.integer(match["node"]))]
        )
    if kind == "SequenceElement":
        return PortableValue.object(
            [
                ("kind", PortableValue.string("SequenceElement")),
                ("parent", PortableValue.integer(match["parent"])),
                ("ordinal", PortableValue.integer(match["ordinal"])),
                ("node", PortableValue.integer(match["node"])),
            ]
        )
    return PortableValue.object(
        [
            ("kind", PortableValue.string("MappingEntry")),
            ("parent", PortableValue.integer(match["parent"])),
            ("ordinal", PortableValue.integer(match["ordinal"])),
            ("key", PortableValue.integer(match["key"])),
            ("value", PortableValue.integer(match["value"])),
        ]
    )


def _graph_match_from_vector(value: PortableValue) -> dict:
    if value.kind is not Kind.OBJECT:
        raise invalid("$.match", "match must be Object")
    kind = compare.string_field(value, "kind")
    if kind is None:
        raise invalid("$.match", "match kind missing")

    def read(name: str) -> int:
        number = compare.integer_field(value, name)
        if number is None:
            raise invalid(f"$.match", f"match {name} missing")
        return number

    if kind == "Node":
        return {"kind": "Node", "node": read("node")}
    if kind == "SequenceElement":
        return {
            "kind": "SequenceElement",
            "parent": read("parent"),
            "ordinal": read("ordinal"),
            "node": read("node"),
        }
    if kind == "MappingEntry":
        return {
            "kind": "MappingEntry",
            "parent": read("parent"),
            "ordinal": read("ordinal"),
            "key": read("key"),
            "value": read("value"),
        }
    raise invalid("$.match", f"unknown graph match kind {kind!r}")


def _validate_graph_matches(graph, matches: list[dict]) -> None:
    """Resolves every match against the exact graph (graph_query.rs)."""
    order, _ = graph._canonical_layout()

    def resolve(canonical: int, path: str) -> int:
        if canonical >= len(order):
            raise invalid(path, "canonical node ID out of range")
        return order[canonical]

    for index, match in enumerate(matches):
        path = f"$.matches[{index}]"
        kind = match["kind"]
        if kind == "Node":
            resolve(match["node"], path + ".node")
        elif kind == "SequenceElement":
            parent = resolve(match["parent"], path + ".parent")
            child = resolve(match["node"], path + ".node")
            node = graph._nodes[parent]
            if node.content[0] != "sequence":
                raise invalid(path, "sequence element parent is not a sequence")
            items = node.content[1]
            if match["ordinal"] >= len(items):
                raise invalid(path, "sequence element ordinal out of range")
            if items[match["ordinal"]].index != child:
                raise invalid(path, "sequence element does not reference the child node")
        else:
            parent = resolve(match["parent"], path + ".parent")
            key = resolve(match["key"], path + ".key")
            value = resolve(match["value"], path + ".value")
            node = graph._nodes[parent]
            if node.content[0] != "mapping":
                raise invalid(path, "mapping entry parent is not a mapping")
            entries = node.content[1]
            if match["ordinal"] >= len(entries):
                raise invalid(path, "mapping entry ordinal out of range")
            entry = entries[match["ordinal"]]
            if entry.key.index != key or entry.value.index != value:
                raise invalid(path, "mapping entry does not reference the key/value nodes")


def _graph_query_result_value(
    domain: QueryDomain, role: MatchRole, graph, matches: list[dict], completion_value: PortableValue
) -> PortableValue:
    """Validates and encodes ``core.graph-query-result@1`` (graph_query.rs:64-100)."""
    if domain.id != "core.portable-graph-query" or domain.version != 1 or not _is_graph_role(role):
        raise invalid("$", "graph result requires core.portable-graph-query@1 and a graph role")
    produced = _completion_produced(completion_value)
    if produced != len(matches):
        raise invalid("$", "completion count or graph match role is inconsistent")
    for match in matches:
        if _graph_match_role(match["kind"]) is not role:
            raise invalid("$", "completion count or graph match role is inconsistent")
    _validate_graph_matches(graph, matches)
    pgce = encode_pgce(graph)
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.graph-query-result@1")),
            ("domain_id", PortableValue.string(domain.id)),
            ("domain_version", PortableValue.integer(domain.version)),
            ("role", PortableValue.string(role.value)),
            ("graph", _graph_message_value(graph, pgce)),
            ("matches", PortableValue.sequence(tuple(_graph_match_value(m) for m in matches))),
            ("completion", completion_value),
            ("diagnostics", PortableValue.sequence(())),
        ]
    )


def _is_graph_role(role: MatchRole) -> bool:
    return role in (MatchRole.GRAPH_NODE, MatchRole.GRAPH_SEQUENCE_ELEMENT, MatchRole.GRAPH_MAPPING_ENTRY)


def _completion_produced(value: PortableValue) -> int:
    fields = exact_fields(
        value, ["schema", "status", "processed", "produced", "limit_name", "failure_code"], "$"
    )
    return unsigned64(fields[3], "$.produced")


def _completion_success(processed: int, produced: int) -> PortableValue:
    completion = records.Completion.new(records.CompletionStatus.SUCCESS, processed, produced)
    return completion.to_value()


def _graph_query(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    role_text = compare.string_field(vector.input, "role")
    if role_text is None:
        return "missing input.role"
    role = _parse_graph_role(role_text)
    if role is None:
        return f"unknown match role {role_text!r}"
    match_value = compare.object_field(vector.input, "match")
    if match_value is None:
        return "missing input.match"
    match = _graph_match_from_vector(match_value)
    accepted = compare.boolean_field(vector.expected, "accepted")
    try:
        value = _graph_query_result_value(
            QueryDomain("core.portable-graph-query", 1), role, built, [match],
            _completion_success(1, 1),
        )
    except ProtocolError as error:
        if accepted:
            return f"unexpected rejection: {error}"
        return _expect_code(vector, error)
    if not accepted:
        return "expected rejection"
    return _dual_roundtrip("core.graph-query-result@1", value, 5)


# ---------------------------------------------------------------------------
# core.graph-provenance-map@1 and core.graph-projection-result@1
# (records_graph.go)
# ---------------------------------------------------------------------------

_GRAPH_LOCATION_RANK = {"Root": 0, "Node": 1, "SequenceElement": 2, "MappingKey": 3, "MappingValue": 4}


def _graph_location_less(left: dict, right: dict) -> bool:
    if left["kind"] != right["kind"]:
        return _GRAPH_LOCATION_RANK[left["kind"]] < _GRAPH_LOCATION_RANK[right["kind"]]
    kind = left["kind"]
    if kind == "Root":
        return left["ordinal"] < right["ordinal"]
    if kind == "Node":
        return left["node"] < right["node"]
    if left["parent"] != right["parent"]:
        return left["parent"] < right["parent"]
    return left["ordinal"] < right["ordinal"]


def _graph_provenance_value(entries: list[dict]) -> PortableValue:
    """Validates and encodes ``core.graph-provenance-map@1``
    (graph_projection.rs:121-141)."""
    for entry in entries:
        if not entry["origins"]:
            raise invalid("$.entries", "graph provenance locations must be sorted, unique, and have origins")
    for index in range(1, len(entries)):
        if not _graph_location_less(entries[index - 1]["projected"], entries[index]["projected"]):
            raise invalid("$.entries", "graph provenance locations must be sorted, unique, and have origins")
    entry_values = []
    for entry in entries:
        entry_values.append(
            PortableValue.object(
                [
                    ("projected", _graph_location_value(entry["projected"])),
                    ("origins", PortableValue.sequence(tuple(origin for origin in entry["origins"]))),
                ]
            )
        )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.graph-provenance-map@1")),
            ("entries", PortableValue.sequence(tuple(entry_values))),
        ]
    )


def _graph_location_value(location: dict) -> PortableValue:
    kind = location["kind"]
    if kind == "Root":
        return PortableValue.object(
            [("kind", PortableValue.string("Root")), ("ordinal", PortableValue.integer(location["ordinal"]))]
        )
    if kind == "Node":
        return PortableValue.object(
            [("kind", PortableValue.string("Node")), ("node", PortableValue.integer(location["node"]))]
        )
    return PortableValue.object(
        [
            ("kind", PortableValue.string(kind)),
            ("parent", PortableValue.integer(location["parent"])),
            ("ordinal", PortableValue.integer(location["ordinal"])),
        ]
    )


def _graph_origin_value(
    source_id: str, node_locator: str | None, start_byte: int, end_byte: int, relation: str
) -> PortableValue:
    if (
        not source_id
        or len(source_id) > 1024
        or start_byte > end_byte
        or (node_locator is not None and (node_locator == "" or len(node_locator) > 4096))
    ):
        raise invalid("$.origin", "invalid source identity, locator, or half-open range")
    if relation not in ("Direct", "Reference"):
        raise invalid("$.origin", "unknown graph provenance relation")
    return PortableValue.object(
        [
            ("source_id", PortableValue.string(source_id)),
            ("node_locator", nullable_string(node_locator)),
            ("start_byte", PortableValue.integer(start_byte)),
            ("end_byte", PortableValue.integer(end_byte)),
            ("relation", PortableValue.string(relation)),
        ]
    )


def _provenance_entries_from_vector(vector: runner.Case) -> list[dict]:
    locations = compare.sequence_field(vector.input, "locations")
    if locations is None:
        raise ValueError("missing input.locations")
    source_id = compare.string_field(vector.input, "source_id") or ""
    node_locator = compare.string_field(vector.input, "node_locator")
    start_byte = compare.integer_field(vector.input, "start_byte")
    end_byte = compare.integer_field(vector.input, "end_byte")
    relation_name = compare.string_field(vector.input, "relation")
    if start_byte is None or end_byte is None or relation_name is None:
        raise ValueError("missing input.start_byte/end_byte/relation")
    entries = []
    for location_value in locations:
        kind = compare.string_field(location_value, "kind")
        if kind is None:
            raise ValueError("location kind missing")
        if kind == "Root":
            ordinal = compare.integer_field(location_value, "ordinal")
            if ordinal is None:
                raise ValueError("root ordinal missing")
            projected = {"kind": "Root", "ordinal": ordinal}
        elif kind == "Node":
            node = compare.integer_field(location_value, "node")
            if node is None:
                raise ValueError("node id missing")
            projected = {"kind": "Node", "node": node}
        elif kind in ("SequenceElement", "MappingKey", "MappingValue"):
            parent = compare.integer_field(location_value, "parent")
            ordinal = compare.integer_field(location_value, "ordinal")
            if parent is None or ordinal is None:
                raise ValueError("parent id or ordinal missing")
            projected = {"kind": kind, "parent": parent, "ordinal": ordinal}
        else:
            raise ValueError(f"unknown projected graph location {kind!r}")
        entries.append(
            {
                "projected": projected,
                "origins": [
                    _graph_origin_value(source_id, node_locator, start_byte, end_byte, relation_name)
                ],
            }
        )
    return entries


def _validate_graph_locations(graph, entries: list[dict]) -> None:
    """Validates every projected location against the exact graph
    (graph_projection.rs:143-158)."""
    order, _ = graph._canonical_layout()

    def resolve(canonical: int, name: str, path: str) -> int:
        if canonical >= len(order):
            raise invalid(path + "." + name, "canonical node ID out of range")
        return order[canonical]

    for index, entry in enumerate(entries):
        path = f"$.entries[{index}].projected"
        location = entry["projected"]
        kind = location["kind"]
        if kind == "Root":
            if location["ordinal"] >= len(graph.roots()):
                raise invalid(path, "root ordinal out of range")
        elif kind == "Node":
            resolve(location["node"], "node", path)
        elif kind == "SequenceElement":
            parent = resolve(location["parent"], "parent", path)
            node = graph._nodes[parent]
            if node.content[0] != "sequence":
                raise invalid(path, "sequence element parent is not a sequence")
            if location["ordinal"] >= len(node.content[1]):
                raise invalid(path, "sequence element ordinal out of range")
        elif kind in ("MappingKey", "MappingValue"):
            parent = resolve(location["parent"], "parent", path)
            node = graph._nodes[parent]
            if node.content[0] != "mapping":
                raise invalid(path, "mapping location parent is not a mapping")
            if location["ordinal"] >= len(node.content[1]):
                raise invalid(path, "mapping location ordinal out of range")
        else:
            raise invalid(path, "unknown projected graph location")


def _graph_projection_result_value(
    completion_value: PortableValue, graph, has_graph: bool, provenance: PortableValue
) -> PortableValue:
    """Validates and encodes ``core.graph-projection-result@1``
    (graph_projection.rs:245-...)."""
    success = _completion_status(completion_value) == "Success"
    if success != has_graph:
        raise invalid("$", "only a successful graph projection carries a graph")
    if success:
        if graph is None:
            raise invalid("$", "successful projection requires a graph")
        entries = _provenance_entries_of(provenance)
        try:
            _validate_graph_locations(graph, entries)
        except ProtocolError as error:
            raise error
    else:
        entries = _provenance_entries_of(provenance)
        if entries:
            raise invalid("$.provenance", "failed projection cannot claim complete provenance")
    graph_value: PortableValue = PortableValue.null()
    if has_graph:
        graph_value = PortableValue.object([("portable_graph", _graph_message_value(graph, encode_pgce(graph)))])
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.graph-projection-result@1")),
            ("completion", completion_value),
            ("graph", graph_value),
            ("provenance", provenance),
            ("diagnostics", PortableValue.sequence(())),
        ]
    )


def _completion_status(value: PortableValue) -> str:
    fields = exact_fields(
        value, ["schema", "status", "processed", "produced", "limit_name", "failure_code"], "$"
    )
    return string_of(fields[1], "$.status")


def _provenance_entries_of(provenance: PortableValue) -> list[dict]:
    fields = schema_fields(provenance, "core.graph-provenance-map@1", ["schema", "entries"], "$")
    entries = []
    for index, entry_value in enumerate(sequence_of(fields[1], "$.entries")):
        entry_fields = exact_fields(entry_value, ["projected", "origins"], f"$.entries[{index}]")
        projected = _parse_graph_location(entry_fields[0], f"$.entries[{index}].projected")
        origins = [
            _parse_graph_origin(item, f"$.entries[{index}].origins[{origin_index}]")
            for origin_index, item in enumerate(sequence_of(entry_fields[1], f"$.entries[{index}].origins"))
        ]
        entries.append({"projected": projected, "origins": origins})
    return entries


def _parse_graph_location(value: PortableValue, path: str) -> dict:
    if value.kind is not Kind.OBJECT or not value.as_object():
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected location Object")
    entries = value.as_object()
    if entries[0][0] != "kind":
        raise invalid(path, "kind must be the first String field")
    kind = string_of(entries[0][1], path + ".kind")
    if kind == "Root":
        fields = exact_fields(value, ["kind", "ordinal"], path)
        return {"kind": "Root", "ordinal": unsigned64(fields[1], path + ".ordinal")}
    if kind == "Node":
        fields = exact_fields(value, ["kind", "node"], path)
        return {"kind": "Node", "node": unsigned64(fields[1], path + ".node")}
    if kind in ("SequenceElement", "MappingKey", "MappingValue"):
        fields = exact_fields(value, ["kind", "parent", "ordinal"], path)
        return {
            "kind": kind,
            "parent": unsigned64(fields[1], path + ".parent"),
            "ordinal": unsigned64(fields[2], path + ".ordinal"),
        }
    raise invalid(path, "unknown projected graph location")


def _parse_graph_origin(value: PortableValue, path: str) -> PortableValue:
    fields = exact_fields(value, ["source_id", "node_locator", "start_byte", "end_byte", "relation"], path)
    source_id = string_of(fields[0], path + ".source_id")
    node_locator = optional_string(fields[1], path + ".node_locator")
    start_byte = unsigned64(fields[2], path + ".start_byte")
    end_byte = unsigned64(fields[3], path + ".end_byte")
    relation = string_of(fields[4], path + ".relation")
    if relation not in ("Direct", "Reference"):
        raise invalid(path + ".relation", "unknown graph provenance relation")
    return _graph_origin_value(source_id, node_locator, start_byte, end_byte, relation)


def _graph_provenance_order(vector: runner.Case) -> str | None:
    try:
        entries = _provenance_entries_from_vector(vector)
        _graph_provenance_value(entries)
    except ProtocolError as error:
        return _expect_code(vector, error)
    except ValueError as error:
        return str(error)
    return "record must be rejected"


def _graph_projection(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    try:
        entries = _provenance_entries_from_vector(vector)
        provenance = _graph_provenance_value(entries)
    except ProtocolError as error:
        return _expect_code(vector, error)
    except ValueError as error:
        return str(error)
    accepted = compare.boolean_field(vector.expected, "accepted")
    try:
        value = _graph_projection_result_value(
            _completion_success(1, 1), built, True, provenance
        )
    except ProtocolError as error:
        if accepted:
            return f"unexpected rejection: {error}"
        return _expect_code(vector, error)
    if not accepted:
        return "expected rejection"
    return _dual_roundtrip("core.graph-projection-result@1", value, 5)


# ---------------------------------------------------------------------------
# core.yaml-query-result@1 (records_line_query.go)
# ---------------------------------------------------------------------------

_YAML_ROLES = {
    MatchRole.YAML_STREAM,
    MatchRole.YAML_DOCUMENT,
    MatchRole.YAML_NODE,
    MatchRole.YAML_MAPPING_ENTRY,
    MatchRole.YAML_SEQUENCE_ELEMENT,
    MatchRole.YAML_ANCHOR_DEFINITION,
    MatchRole.YAML_ALIAS_OCCURRENCE,
    MatchRole.YAML_SYNTAX_PIECE,
}


def _yaml_locator(source_id: str, node_locator: str, role: MatchRole, ordinal: int) -> PortableValue:
    if (
        not source_id
        or len(source_id) > 1024
        or not node_locator
        or len(node_locator) > 4096
        or role not in _YAML_ROLES
    ):
        raise invalid("$.yaml_match", "invalid source, locator, or YAML role")
    return PortableValue.object(
        [
            ("source_id", PortableValue.string(source_id)),
            ("node_locator", PortableValue.string(node_locator)),
            ("role", PortableValue.string(role.value)),
            ("ordinal", PortableValue.integer(ordinal)),
        ]
    )


def _yaml_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "yaml.native-semantic-query" and domain.version == 1:
        return role in _YAML_ROLES and role is not MatchRole.YAML_SYNTAX_PIECE
    if domain.id == "yaml.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.YAML_SYNTAX_PIECE
    return False


def _yaml_query_result_value(
    domain: QueryDomain, role: MatchRole, locators: list[PortableValue], completion_value: PortableValue
) -> PortableValue:
    """Validates and encodes ``core.yaml-query-result@1`` (yaml_query.rs:85-120)."""
    if not _yaml_domain_accepts_role(domain, role):
        raise invalid("$", "YAML query domain and result role are inconsistent")
    if _completion_produced(completion_value) != len(locators):
        raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
    previous = 0
    for index, locator in enumerate(locators):
        locator_fields = exact_fields(locator, ["source_id", "node_locator", "role", "ordinal"], f"$.matches[{index}]")
        locator_role = records.parse_match_role(string_of(locator_fields[2], f"$.matches[{index}].role"))
        ordinal = unsigned64(locator_fields[3], f"$.matches[{index}].ordinal")
        if locator_role is not role:
            raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
        if index > 0 and ordinal <= previous:
            raise invalid("$", "completion count, role, or YAML match ordinals are inconsistent")
        previous = ordinal
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.yaml-query-result@1")),
            ("domain_id", PortableValue.string(domain.id)),
            ("domain_version", PortableValue.integer(domain.version)),
            ("role", PortableValue.string(role.value)),
            ("matches", PortableValue.sequence(tuple(locators))),
            ("completion", completion_value),
            ("diagnostics", PortableValue.sequence(())),
        ]
    )


def _yaml_query_roundtrip(vector: runner.Case) -> str | None:
    roles_value = compare.sequence_field(vector.input, "roles")
    if roles_value is None:
        return "missing input.roles"
    source_id = compare.string_field(vector.input, "source_id") or ""
    count = 0
    for ordinal, role_value in enumerate(roles_value):
        text = role_value.as_string()
        role = _parse_graph_role(text)
        if role is None:
            return f"unknown match role {text!r}"
        domain = QueryDomain("yaml.lossless-syntax-query", 1)
        if role is not MatchRole.YAML_SYNTAX_PIECE:
            domain = QueryDomain("yaml.native-semantic-query", 1)
        locator = _yaml_locator(source_id, f"/nodes/{ordinal}", role, ordinal)
        try:
            value = _yaml_query_result_value(domain, role, [locator], _completion_success(1, 1))
        except ProtocolError as error:
            return f"unexpected rejection: {error}"
        message = _dual_roundtrip("core.yaml-query-result@1", value, 5)
        if message:
            return message
        count += 1
    role_count = compare.integer_field(vector.expected, "role_count")
    if count != role_count:
        return f"role count {count} != {role_count}"
    return None


def _yaml_domain_rejection(vector: runner.Case) -> str | None:
    role_text = compare.string_field(vector.input, "role")
    if role_text is None:
        return "missing input.role"
    role = _parse_graph_role(role_text)
    if role is None:
        return f"unknown match role {role_text!r}"
    try:
        locator = _yaml_locator("sha256:source", "/syntax/0", role, 0)
        _yaml_query_result_value(
            QueryDomain("yaml.native-semantic-query", 1), role, [locator], _completion_success(1, 1)
        )
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "record must be rejected"


def _yaml_process_local(vector: runner.Case) -> str | None:
    return _expect_code(vector, records.process_local_error("$.yaml_match.node"))


# ---------------------------------------------------------------------------
# protocol envelope cases
# ---------------------------------------------------------------------------


def _protocol_v4_rejection(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    payload = _graph_message_value(built, encode_pgce(built))
    try:
        ProtocolMessage(ContractId("core.portable-graph", 1), payload, ContractRegistry(4))
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "envelope must be rejected"


def _completion_value_with_registry(
    status, processed: int, produced: int, failure_code: str | None, registry: ErrorCodeRegistry
) -> PortableValue:
    """Builds one completion value validating the failure code against one
    explicit semantic-model registry (execution.rs NewCompletionWithRegistry)."""
    if failure_code is not None:
        registry.validate(failure_code, "$.failure_code")
        valid = status in (
            records.CompletionStatus.FAILED,
            records.CompletionStatus.UNSUPPORTED,
            records.CompletionStatus.NOT_APPLICABLE,
        ) and failure_code != ""
    else:
        valid = status in (records.CompletionStatus.SUCCESS, records.CompletionStatus.CANCELLED)
    if not valid:
        raise invalid("$", "completion status contradicts limit/failure fields")
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.completion@1")),
            ("status", PortableValue.string(status.value)),
            ("processed", PortableValue.integer(processed)),
            ("produced", PortableValue.integer(produced)),
            ("limit_name", PortableValue.null()),
            ("failure_code", nullable_string(failure_code)),
        ]
    )


def _protocol_nested_error(vector: runner.Case) -> str | None:
    code_text = compare.string_field(vector.input, "failure_code")
    if code_text is None:
        return "missing input.failure_code"
    v4_code = compare.string_field(vector.expected, "v4_code")
    try:
        _completion_value_with_registry(
            records.CompletionStatus.FAILED, 1, 0, code_text, ErrorCodeRegistry(4)
        )
    except ProtocolError as error:
        if error.code != v4_code:
            return f"v4 nested rejection {error.code} != {v4_code}"
    else:
        return "v4 accepted a v5 diagnostic code"
    try:
        payload = _completion_value_with_registry(
            records.CompletionStatus.FAILED, 1, 0, code_text, ErrorCodeRegistry(5)
        )
    except ProtocolError as error:
        return f"v5 rejected its own diagnostic code: {error}"
    return _dual_roundtrip("core.completion@1", payload, 5)


def _protocol_truncated_pvce(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    payload = _graph_message_value(built, encode_pgce(built))
    envelope = ProtocolMessage(ContractId("core.portable-graph", 1), payload, ContractRegistry(5))
    limits = ProtocolLimits()
    bytes_encoded = envelope.to_pvce(limits)
    truncate = compare.integer_field(vector.input, "truncate_bytes")
    if truncate is None:
        return "missing input.truncate_bytes"
    cut = len(bytes_encoded) - truncate
    if cut < 0:
        cut = 0
    try:
        ProtocolMessage.from_pvce(bytes_encoded[:cut], limits, ContractRegistry(5))
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "decode must be rejected"


def _protocol_unknown_field(vector: runner.Case) -> str | None:
    built = _graph_from_vector_case(vector)
    payload = _graph_message_value(built, encode_pgce(built))
    changed = _replace_object_field(payload, "unknown", PortableValue.null())
    try:
        _graph_message_from_value(changed, _pgce_limits())
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "record must be rejected"


runner.register_suite(
    "semantic-model-v5.json", "consema.semantic-model-v5.conformance@1", "core.semantic-model@5", 22, run
)
