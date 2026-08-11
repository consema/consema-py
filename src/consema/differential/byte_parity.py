"""PVCE/PGCE byte parity of the Python encoders against the Rust authority
(roadmap §16.1 hard gate: "Rust 与 Go 的 PVCE/PGCE bytes 完全一致", extended
to Python per docs/five-language-ci-design.md §3.2).

Rust is the byte authority (crates/consema-pvce, crates/consema-graph). The
checked-in case set (go/conformance/differential/cases.json) is encoded by
both sides; the Rust encoder example
(crates/consema-conformance/examples/emit_parity_bytes.rs) emits one
``<case-id>.hex`` golden file per case, and this module compares the Python
encoder's bytes byte for byte and checks the bidirectional direction (golden
bytes -> Python decode -> Python re-encode), mirroring the Go test
(go/conformance/differential/differential_test.go). Orchestration:
scripts/python-verify-byte-parity.ps1 provisions the golden directory
(``CONSEMA_DIFFERENTIAL_RUST_DIR``); without it the test skips (documented,
never silent) and only the case-file integrity checks run.

A divergence here is a Python encoder bug (the encoders are in scope:
python/src/consema/core/pvce.py, python/src/consema/graph/pgce.py).
"""

from __future__ import annotations

import dataclasses
import os

from consema.differential import case_files
from consema.core import pvce
from consema.core.errors import PVCEError
from consema.graph import PgceDecodeError, PortableGraph, decode_pgce, encode_pgce
from consema.graph.errors import GraphBuildError
from consema.graph.graph import GraphBuilder, GraphMappingEntry, GraphNodeId
from consema.protocol.canonical import decode_json
from consema.protocol.limits import ProtocolLimits

CASE_FILE = "cases.json"
MANIFEST = "consema.differential.byte-parity@1"

# The directory of the Rust encoder's golden hex files
# (scripts/python-verify-byte-parity.ps1 provisions it; the Go harness uses
# the same variable name, docs/five-language-ci-design.md §3.2).
RUST_DIR_ENV = "CONSEMA_DIFFERENTIAL_RUST_DIR"

# The closed fifteen-kind vocabulary of the case file's "kinds" metadata
# (RFC 0016 §4.1 core Kind names, differential_test.go allKindNames).
ALL_KIND_NAMES = [
    "Null", "Boolean", "String", "Integer", "Decimal",
    "BinaryFloat32", "BinaryFloat64", "Bytes", "Date", "Time",
    "LocalDateTime", "OffsetDateTime", "Array", "Object", "EntryMapping",
]

# The neutral graph descriptor of one pgce case (the same shape as the
# portable-graph-v1 vector inputs).
GRAPH_DESCRIPTOR_FIELDS = {"roots", "nodes"}
NODE_KINDS = {"Scalar", "Sequence", "Mapping"}


@dataclasses.dataclass(frozen=True)
class ByteCase:
    """One loaded parity case."""

    id: str
    codec: str  # "pvce" or "pgce"
    value: str | None  # pvce transport JSON text
    graph: dict | None  # pgce neutral descriptor
    kinds: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ParityResult:
    """The byte-parity run outcome."""

    passed: int = 0
    failed: int = 0
    pvce: int = 0
    pgce: int = 0
    failures: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.passed + self.failed


def load_case_file() -> list[ByteCase]:
    """Loads and validates the checked-in case set: manifest id, exact count,
    unique ids, known codecs, decodable PVCE values, buildable PGCE graphs,
    and fifteen-kind coverage (differential_test.go loadCaseFile)."""
    cases = case_files.load_case_file(CASE_FILE, MANIFEST, case_files.BYTE_PARITY_EXACT)
    parsed: list[ByteCase] = []
    seen: set[str] = set()
    kinds: set[str] = set()
    for raw in cases:
        case_id = raw["id"]
        if case_id in seen:
            raise case_files.CaseFileError(f"duplicate case id {case_id!r}")
        seen.add(case_id)
        codec = raw.get("codec")
        if codec == "pvce":
            value = raw.get("value")
            if not isinstance(value, str) or not value:
                raise case_files.CaseFileError(f"case {case_id}: pvce case without a value")
            # The strict canonicality check (parse + re-encode) keeps the
            # file's transport JSON honest; the Rust side must accept the
            # same text.
            decode_json(value.encode("utf-8"), ProtocolLimits())
            parsed.append(ByteCase(case_id, "pvce", value, None, tuple(raw.get("kinds", []))))
        elif codec == "pgce":
            graph_desc = raw.get("graph")
            if not isinstance(graph_desc, dict):
                raise case_files.CaseFileError(f"case {case_id}: pgce case without a graph")
            build_graph(graph_desc)  # must build
            parsed.append(ByteCase(case_id, "pgce", None, graph_desc, tuple(raw.get("kinds", []))))
        else:
            raise case_files.CaseFileError(f"case {case_id}: unknown codec {codec!r}")
        for kind in raw.get("kinds", []):
            kinds.add(kind)
    for kind in ALL_KIND_NAMES:
        if kind not in kinds:
            raise case_files.CaseFileError(f"case set does not cover kind {kind!r} (kinds metadata)")
    return parsed


def build_graph(desc: dict) -> PortableGraph:
    """Constructs the graph of a neutral descriptor (the Python mirror of the
    Go buildGraph / Rust graph_from_value)."""
    roots = desc.get("roots")
    nodes = desc.get("nodes")
    if not isinstance(roots, list) or not isinstance(nodes, list):
        raise case_files.CaseFileError("graph descriptor must have roots and nodes arrays")
    builder = GraphBuilder()
    ids: list[GraphNodeId] = []
    for _ in nodes:
        ids.append(builder.reserve_node())

    def reference(index: object) -> GraphNodeId:
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(ids)):
            raise case_files.CaseFileError(
                f"node reference {index!r} out of range (0..{len(ids) - 1})"
            )
        return ids[index]

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise case_files.CaseFileError("graph node must be an Object")
        kind = node.get("kind")
        tag = node.get("tag", "")
        if not isinstance(tag, str):
            raise case_files.CaseFileError(f"node {index}: tag must be a String")
        if kind == "Scalar":
            content = node.get("content", "")
            if not isinstance(content, str):
                raise case_files.CaseFileError(f"node {index}: content must be a String")
            builder.define_scalar(ids[index], tag, content)
        elif kind == "Sequence":
            items = node.get("items")
            if not isinstance(items, list):
                raise case_files.CaseFileError(f"node {index}: sequence items must be an array")
            builder.define_sequence(ids[index], tag, [reference(item) for item in items])
        elif kind == "Mapping":
            entries = node.get("entries")
            if not isinstance(entries, list):
                raise case_files.CaseFileError(f"node {index}: mapping entries must be an array")
            builder.define_mapping(
                ids[index],
                tag,
                [
                    GraphMappingEntry(
                        reference(entry.get("key")),
                        reference(entry.get("value")),
                    )
                    for entry in entries
                ],
            )
        else:
            raise case_files.CaseFileError(f"node {index}: unknown node kind {kind!r}")
    for root in roots:
        builder.push_root(reference(root))
    try:
        return builder.build()
    except GraphBuildError as error:
        raise case_files.CaseFileError(f"graph build failed: {error}") from error


def _first_diff(id: str, direction: str, py_bytes: bytes, rust_bytes: bytes) -> str:
    """Reports a byte-level difference with the first differing offset and
    the full hex of both sides (differential_test.go firstDiff)."""
    index = 0
    while index < len(py_bytes) and index < len(rust_bytes) and py_bytes[index] == rust_bytes[index]:
        index += 1
    return (
        f"case {id} ({direction}): Python {len(py_bytes)} bytes, Rust {len(rust_bytes)} bytes, "
        f"first difference at offset {index}\n  Python: {py_bytes.hex()}\n  Rust:   {rust_bytes.hex()}"
    )


def run_parity(rust_dir: str | None = None) -> ParityResult:
    """Encodes every case with the Python encoders and compares byte for byte
    with the Rust golden bytes; checks the bidirectional direction (Rust
    bytes decode under the Python decoders and re-encode byte-identically).

    ``rust_dir`` is the directory of ``<case-id>.hex`` golden files produced
    by scripts/python-verify-byte-parity.ps1. Every file in it must
    correspond to a known case (drift check)."""
    cases = load_case_file()
    if rust_dir is None:
        rust_dir = os.environ.get(RUST_DIR_ENV)
    known_ids = {case.id for case in cases}
    if rust_dir is not None:
        for name in os.listdir(rust_dir):
            if name.endswith(".hex") and name[: -len(".hex")] not in known_ids:
                raise case_files.CaseFileError(
                    f"rust byte file {name!r} does not correspond to any case (case file drift?)"
                )

    failures: list[str] = []
    passed = pvce_count = pgce_count = 0
    for case in cases:
        rust_bytes = case_files.read_hex_file(rust_dir, case.id) if rust_dir else b""
        if case.codec == "pvce":
            pvce_count += 1
            value = decode_json(case.value.encode("utf-8"), ProtocolLimits())
            py_bytes = pvce.encode(value)
            if rust_dir is None:
                failures.append(f"case {case.id}: no Rust golden directory provisioned")
                continue
            if py_bytes != rust_bytes:
                failures.append(_first_diff(case.id, "pvce", py_bytes, rust_bytes))
                continue
            # Bidirectional: Rust bytes decode under the Python decoder and
            # re-encode byte-identically.
            try:
                decoded = pvce.decode(rust_bytes)
                re_encoded = pvce.encode(decoded)
            except PVCEError as error:
                failures.append(f"case {case.id}: Python cannot decode the Rust PVCE bytes: {error}")
                continue
            if re_encoded != rust_bytes:
                failures.append(_first_diff(case.id, "pvce-rust->python->re-encode", re_encoded, rust_bytes))
                continue
            passed += 1
        else:  # pgce
            pgce_count += 1
            py_graph = build_graph(case.graph)
            py_bytes = encode_pgce(py_graph)
            if rust_dir is None:
                failures.append(f"case {case.id}: no Rust golden directory provisioned")
                continue
            if py_bytes != rust_bytes:
                failures.append(_first_diff(case.id, "pgce", py_bytes, rust_bytes))
                continue
            # Bidirectional: Rust bytes decode under the Python decoder and
            # re-encode byte-identically (the canonical PGCE encode also
            # proves graph equality, portable_graph_v1.go _graph_equality).
            try:
                decoded = decode_pgce(rust_bytes)
                re_encoded = encode_pgce(decoded)
            except PgceDecodeError as error:
                failures.append(f"case {case.id}: Python cannot decode the Rust PGCE bytes: {error}")
                continue
            if re_encoded != rust_bytes:
                failures.append(_first_diff(case.id, "pgce-rust->python->re-encode", re_encoded, rust_bytes))
                continue
            passed += 1
    return ParityResult(
        passed=passed,
        failed=len(failures),
        pvce=pvce_count,
        pgce=pgce_count,
        failures=tuple(failures),
    )


def parity_summary(result: ParityResult) -> str:
    """The human summary line (the shape the driver script greps)."""
    return f"byte parity: {result.passed}/{result.total} equal ({result.pvce} pvce, {result.pgce} pgce)"
