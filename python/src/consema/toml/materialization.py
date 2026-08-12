"""Deterministic PortableValue materialization into TOML 1.0 documents.

Authority:

- RFC 0004 §3/§4/§6 (docs/rfcs/0004-materialization-conversion-and-
  structural-edit-v1.md:56-94, 98-127, 150-168): the common
  MaterializationRequest; the frozen style ``toml.canonical-document@1``;
  TOML requires a root Object (or an explicit
  UniqueStringEntriesToObject EntryMapping conversion), accepts
  Boolean/Integer/BinaryFloat64/String/Date/Time/LocalDateTime/
  OffsetDateTime/Sequence/Object recursively, requires signed 64-bit
  integers, rejects non-canonical NaN payloads, and never interprets
  duplicate keys.
- The writer transcribes crates/consema-toml/src/materialization.rs:
  request validation 81-99; canonical string/key escaping 353-380 (same
  escape set as the canonical literal, edit.rs:1516-1537); float
  canonicalization 382-407 (canonical NaN payloads ``nan``/``-nan``,
  ``inf``/``-inf``, decimal with ``.0`` appended when integral); temporal
  canonicalization 409-486 (four-digit year 0-9999, nanosecond fraction
  with trailing zeros stripped, ``Z`` for zero offset, whole-minute
  offsets only); sequence ``[a, b]`` and inline object ``{ k = v }``
  layouts 488-536; one assignment per root entry and one final newline
  211-259; parse limits derived from materialization limits 178-186;
  provenance collection 613-864 (Direct for Object values/associations,
  Reencoded for EntryMapping, Generated for array element associations).
- The completion algebra reuses consema.document.materialization
  (CompleteMaterialization/FailedMaterializationAttempt/fidelity/report/
  provenance), whose failure-to-code mapping is frozen there.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from consema.core.value import Decimal, PortableValue
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MappingPolicy,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationInputLocation,
    MaterializationLimits,
    MaterializationProvenanceEntry,
    MaterializationProvenanceMap,
    MaterializationRelation,
    MaterializationReport,
    MaterializationRequest,
    MaterializationResult,
    MaterializedOrigin,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding

from consema.toml.document import Document, TomlItem, TomlItemKind, TomlProfile
from consema.toml.paths import (
    AssociationLocation,
    AssociationRole,
    ValuePath,
    ValuePathSegment,
)
from consema.toml.parser import parse


@dataclass(frozen=True, slots=True)
class MaterializationEvent:
    """One stable ordered report event of a TOML materialization.

    Exact TOML materializations emit no events; the explicit
    UniqueStringEntriesToObject conversion emits exactly one
    ``core.materialization.mapping-transformed@1`` event with the from/
    policy/to arguments (RFC 0004 §3; error_registry.rs:568). The event
    record shape follows core.materialization-report@1; the protocol agent
    owns the wire record.
    """

    code: str
    arguments: dict[str, str] = field(default_factory=dict)


def materialize(
    value: PortableValue, request: MaterializationRequest
) -> MaterializationResult:
    """Materializes one complete PortableValue into a new immutable TOML
    document (materialization.rs:19-34)."""
    try:
        complete = _materialize_complete(value, request)
        return complete
    except MaterializationFailure as failure:
        return FailedMaterializationAttempt(
            failure=failure,
            report=MaterializationReport(),
            analyzed_input_paths=(),
        )


def canonical_fragment(
    value: PortableValue, limits: MaterializationLimits
) -> bytes:
    """Renders one canonical TOML value fragment for structural editing
    (materialization.rs:36-45). Raises MaterializationFailure."""
    writer = _TomlWriter(NewlinePolicy.LF, limits)
    writer.value(value, ValuePath.root(), 0)
    return writer.output


def _materialize_complete(
    value: PortableValue, request: MaterializationRequest
) -> CompleteMaterialization:
    _requested_contract(request)
    root_entries, transformed = _prepare_root(value, request)
    writer = _TomlWriter(request.newline, request.limits)
    writer.root(root_entries, transformed, request.newline)
    bytes_output = writer.output
    from consema.document.limits import ParseLimits

    parse_limits = ParseLimits(
        max_source_bytes=request.limits.max_output_bytes,
        max_nesting_depth=request.limits.max_depth,
        max_token_count=request.limits.max_output_bytes,
        max_node_count=request.limits.max_input_nodes * 4,
        max_diagnostics=request.limits.max_report_entries,
    )
    try:
        document = parse(bytes_output, TomlProfile.TOML10_V1, parse_limits)
    except Exception:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED) from None

    provenance = _collect_provenance(value, document, request.limits)
    fidelity = (
        MaterializationFidelity.EXACT
        if not transformed
        else MaterializationFidelity.TRANSFORMED
    )
    report = MaterializationReport()
    if transformed:
        report = MaterializationReport.new(
            [
                MaterializationEvent(
                    code="core.materialization.mapping-transformed@1",
                    arguments={
                        "from": "EntryMapping",
                        "policy": "UniqueStringEntriesToObject",
                        "to": "Object",
                    },
                )
            ],
            request.limits,
        )
    return CompleteMaterialization(
        document=document,
        fidelity=fidelity,
        report=report,
        provenance=provenance,
    )


def _requested_contract(request: MaterializationRequest) -> None:
    """materialization.rs:81-99."""
    if (request.target_profile.id, request.target_profile.version) != ("toml.1.0", 1):
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_PROFILE)
    if (request.style.id, request.style.version) != ("toml.canonical-document", 1):
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_STYLE)
    if request.encoding != SourceEncoding.utf8():
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    if request.newline not in (NewlinePolicy.LF, NewlinePolicy.CRLF):
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_NEWLINE)


def _prepare_root(
    value: PortableValue, request: MaterializationRequest
) -> tuple[list[tuple[str, PortableValue]], bool]:
    """materialization.rs:101-176: a root Object is exact; an EntryMapping
    needs the explicit UniqueStringEntriesToObject policy and unique
    String keys, reported as Transformed (the mapping-transformed event
    carries code core.materialization.mapping-transformed@1 with
    from/policy/to arguments, error_registry.rs:568)."""
    if value.kind.value == "Object":
        return list(value.as_object()), False
    if value.kind.value != "EntryMapping":
        raise MaterializationFailure(
            MaterializationFailureKind.UNREPRESENTABLE, name="root-kind"
        )
    if request.mapping_policy is not MappingPolicy.UNIQUE_STRING_ENTRIES_TO_OBJECT:
        raise MaterializationFailure(
            MaterializationFailureKind.UNREPRESENTABLE, name="root-mapping"
        )
    if len(value.as_entry_mapping()) > request.limits.max_input_nodes:
        raise MaterializationFailure(
            MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes"
        )
    seen: set[str] = set()
    entries: list[tuple[str, PortableValue]] = []
    for key, entry_value in value.as_entry_mapping():
        if key.kind.value != "String" or key.as_string() in seen:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="mapping-key"
            )
        seen.add(key.as_string())
        entries.append((key.as_string(), entry_value))
    return entries, True


class _TomlWriter:
    """The canonical TOML writer with resource limits
    (materialization.rs:188-607)."""

    __slots__ = ("newline", "limits", "input_nodes", "output")

    def __init__(self, newline: NewlinePolicy, limits: MaterializationLimits) -> None:
        self.newline = newline
        self.limits = limits
        self.input_nodes = 0
        self.output = bytearray()

    def _check_output(self, extra: int) -> None:
        if len(self.output) + extra > self.limits.max_output_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )

    def push(self, text: str) -> None:
        encoded = text.encode("utf-8")
        self._check_output(len(encoded))
        self.output.extend(encoded)

    def push_bytes(self, data: bytes) -> None:
        self._check_output(len(data))
        self.output.extend(data)

    def analyze(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-depth"
            )
        self.input_nodes += 1
        if self.input_nodes > self.limits.max_input_nodes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes"
            )

    def root(
        self,
        entries: list[tuple[str, PortableValue]],
        transformed: bool,
        newline: NewlinePolicy,
    ) -> None:
        """One assignment per root entry, one final newline
        (materialization.rs:211-259)."""
        self.analyze(0)
        newline_bytes = newline.bytes
        for key, entry_value in entries:
            self.write_string(key)
            self.push(" = ")
            self.value(entry_value, ValuePath.root().child(ValuePathSegment.object_value(key)), 1)
            self.push_bytes(newline_bytes)
        if not entries:
            self.push_bytes(newline_bytes)

    def value(self, value: PortableValue, path: ValuePath, depth: int) -> None:
        self.analyze(depth)
        kind = value.kind
        if kind.value == "Boolean":
            self.push("true" if value.as_boolean() else "false")
        elif kind.value == "Integer":
            integer = value.as_integer()
            if not -(2**63) <= integer <= 2**63 - 1:
                raise MaterializationFailure(
                    MaterializationFailureKind.UNREPRESENTABLE, name="integer-range"
                )
            self.push(str(integer))
        elif kind.value == "BinaryFloat64":
            self.write_float(value.as_binary_float64())
        elif kind.value == "String":
            self.write_string(value.as_string())
        elif kind.value == "Date":
            self.write_date(value.as_date())
        elif kind.value == "Time":
            self.write_time(value.as_time())
        elif kind.value == "LocalDateTime":
            local, time = value.as_local_date_time()
            self.write_date(local.as_date())
            self.push("T")
            self.write_time(time.as_time())
        elif kind.value == "OffsetDateTime":
            local, offset_seconds = value.as_offset_date_time()
            date_value, time_value = local.as_local_date_time()
            self.write_date(date_value.as_date())
            self.push("T")
            self.write_time(time_value.as_time())
            self.write_offset(offset_seconds)
        elif kind.value == "Sequence":
            self.write_sequence(value.as_sequence(), path, depth)
        elif kind.value == "Object":
            self.write_inline_object(value.as_object(), path, depth)
        else:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE,
                name=f"kind:{kind.value}",
            )

    def write_string(self, value: str) -> None:
        """Canonical basic-string escaping (materialization.rs:353-380)."""
        self.push('"')
        for character in value:
            code = ord(character)
            if character == "\b":
                self.push("\\b")
            elif character == "\t":
                self.push("\\t")
            elif character == "\n":
                self.push("\\n")
            elif character == "\f":
                self.push("\\f")
            elif character == "\r":
                self.push("\\r")
            elif character == '"':
                self.push('\\"')
            elif character == "\\":
                self.push("\\\\")
            elif code <= 0x1F or code == 0x7F:
                self.push(f"\\u{code:04X}")
            else:
                self.push(character)
        self.push('"')

    def write_float(self, bits: int) -> None:
        """materialization.rs:382-407: canonical NaN payloads ``nan`` and
        ``-nan``, ``inf``/``-inf``, otherwise the shortest decimal with
        ``.0`` appended when the spelling has no fraction/exponent."""
        if bits == 0x7FF8000000000000:
            self.push("nan")
            return
        if bits == 0xFFF8000000000000:
            self.push("-nan")
            return
        if bits == 0x7FF0000000000000:
            self.push("inf")
            return
        if bits == 0xFFF0000000000000:
            self.push("-inf")
            return
        if bits & 0x7FF0000000000000 == 0x7FF0000000000000:
            # every other NaN payload fails (RFC 0004 §6: non-canonical
            # NaN payloads fail)
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="binary-float64"
            )
        value = struct.unpack(">d", struct.pack(">Q", bits))[0]
        text = repr(value)
        if not any(character in text for character in ".eE"):
            text += ".0"
        self.push(text)

    def write_date(self, date: tuple[int, int, int]) -> None:
        year, month, day = date
        if not 0 <= year <= 9999:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="date-year"
            )
        self.push(f"{year:04d}-{month:02d}-{day:02d}")

    def write_time(self, time: tuple[int, int, int, Decimal]) -> None:
        hour, minute, second, fraction = time
        nanoseconds = _exact_nanoseconds(fraction)
        if nanoseconds is None:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="time-fraction"
            )
        self.push(f"{hour:02d}:{minute:02d}:{second:02d}")
        if nanoseconds != 0:
            width = 9
            while nanoseconds % 10 == 0:
                nanoseconds //= 10
                width -= 1
            self.push(f".{nanoseconds:0{width}d}")

    def write_offset(self, offset_seconds: int) -> None:
        """materialization.rs:460-486."""
        if offset_seconds == 0:
            self.push("Z")
            return
        if offset_seconds % 60 != 0:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="offset-seconds"
            )
        minutes = offset_seconds // 60
        if abs(minutes) >= 24 * 60:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE, name="offset-range"
            )
        sign = "-" if minutes < 0 else "+"
        magnitude = abs(minutes)
        self.push(f"{sign}{magnitude // 60:02d}:{magnitude % 60:02d}")

    def write_sequence(self, values: tuple, path: ValuePath, depth: int) -> None:
        self.push("[")
        for index, item in enumerate(values):
            if index != 0:
                self.push(", ")
            child = path.child(ValuePathSegment.sequence_element(index))
            self.value(item, child, depth + 1)
        self.push("]")

    def write_inline_object(self, entries: tuple, path: ValuePath, depth: int) -> None:
        self.push("{")
        if entries:
            self.push(" ")
        for index, (key, entry_value) in enumerate(entries):
            if index != 0:
                self.push(", ")
            self.write_string(key)
            self.push(" = ")
            child = path.child(ValuePathSegment.object_value(key))
            self.value(entry_value, child, depth + 1)
        if entries:
            self.push(" ")
        self.push("}")


def _exact_nanoseconds(fraction: Decimal) -> int | None:
    """materialization.rs:549-567: the fraction must be an exact
    nanosecond count in [0, 10^9)."""
    if fraction.coefficient == 0:
        return 0
    exponent = fraction.exponent
    if not -9 <= exponent < 0:
        return None
    nanoseconds = fraction.coefficient
    if nanoseconds < 0:
        return None
    for _ in range(exponent + 9):
        nanoseconds *= 10
    if nanoseconds >= 1_000_000_000:
        return None
    return nanoseconds


def _collect_provenance(
    value: PortableValue,
    document: Document,
    limits: MaterializationLimits,
) -> MaterializationProvenanceMap:
    """materialization.rs:613-864."""
    entries: list[MaterializationProvenanceEntry] = []
    units = 0

    def origin(node, span, relation: MaterializationRelation) -> MaterializedOrigin:
        return MaterializedOrigin(
            snapshot=document.snapshot_identity(),
            node=node,
            span=span,
            relation=relation,
        )

    def push(input_location, output: MaterializedOrigin) -> None:
        nonlocal units
        units += 2
        if units > limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        entries.append(MaterializationProvenanceEntry(input=input_location, outputs=(output,)))

    def add_output(input_location, output: MaterializedOrigin) -> None:
        nonlocal units
        units += 1
        if units > limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        for index, entry in enumerate(entries):
            if entry.input == input_location:
                entries[index] = MaterializationProvenanceEntry(
                    input=input_location, outputs=entry.outputs + (output,)
                )
                return
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)

    def collect(input_value: PortableValue, path: ValuePath, output: TomlItem) -> None:
        relation = (
            MaterializationRelation.REENCODED
            if input_value.kind.value == "EntryMapping"
            else MaterializationRelation.DIRECT
        )
        push(
            MaterializationInputLocation.value(path),
            origin(output.node_ref(), output.span(), relation),
        )
        kind = input_value.kind.value
        if kind == "Sequence":
            values = input_value.as_sequence()
            elements = output.array_elements()
            if elements is None or len(values) != len(elements):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, (item, element) in enumerate(zip(values, elements)):
                child_path = path.child(ValuePathSegment.sequence_element(index))
                collect(item, child_path, element.item())
                add_output(
                    MaterializationInputLocation.value(child_path),
                    origin(
                        element.node_ref(),
                        element.span(),
                        MaterializationRelation.GENERATED,
                    ),
                )
        elif kind == "Object":
            inputs = input_value.as_object()
            table_entries = output.table_entries()
            if table_entries is None or len(inputs) != len(table_entries):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, (input_entry, entry) in enumerate(zip(inputs, table_entries)):
                if input_entry[0] != entry.name():
                    raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
                push(
                    MaterializationInputLocation.association(
                        AssociationLocation.new(path, index, AssociationRole.OBJECT_ENTRY)
                    ),
                    origin(entry.node_ref(), entry.span(), MaterializationRelation.DIRECT),
                )
                push(
                    MaterializationInputLocation.association(
                        AssociationLocation.new(path, index, AssociationRole.OBJECT_KEY)
                    ),
                    origin(
                        entry.key_node_ref(),
                        entry.key_span(),
                        MaterializationRelation.DIRECT,
                    ),
                )
                child_path = path.child(ValuePathSegment.object_value(input_entry[0]))
                collect(input_entry[1], child_path, entry.item())
        elif kind == "EntryMapping":
            inputs = input_value.as_entry_mapping()
            table_entries = output.table_entries()
            if table_entries is None or len(inputs) != len(table_entries):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, (input_entry, entry) in enumerate(zip(inputs, table_entries)):
                if input_entry[0].as_string() != entry.name():
                    raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
                push(
                    MaterializationInputLocation.association(
                        AssociationLocation.new(
                            path, index, AssociationRole.ENTRY_MAPPING_ENTRY
                        )
                    ),
                    origin(entry.node_ref(), entry.span(), MaterializationRelation.REENCODED),
                )
                push(
                    MaterializationInputLocation.value(
                        path.child(ValuePathSegment.entry_key(index))
                    ),
                    origin(
                        entry.key_node_ref(),
                        entry.key_span(),
                        MaterializationRelation.REENCODED,
                    ),
                )
                child_path = path.child(ValuePathSegment.entry_value(index))
                collect(input_entry[1], child_path, entry.item())
        elif not _scalar_kind_matches(kind, output.kind()):
            raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)

    collect(value, ValuePath.root(), document.root())
    return MaterializationProvenanceMap.new(
        entries, document.snapshot_identity(), limits
    )


def _scalar_kind_matches(input_kind: str, output_kind: TomlItemKind) -> bool:
    """materialization.rs:866-884."""
    return (
        (input_kind == "String" and output_kind is TomlItemKind.STRING)
        or (input_kind == "Integer" and output_kind is TomlItemKind.INTEGER)
        or (input_kind == "BinaryFloat64" and output_kind is TomlItemKind.FLOAT)
        or (input_kind == "Boolean" and output_kind is TomlItemKind.BOOLEAN)
        or (input_kind == "Date" and output_kind is TomlItemKind.LOCAL_DATE)
        or (input_kind == "Time" and output_kind is TomlItemKind.LOCAL_TIME)
        or (input_kind == "LocalDateTime" and output_kind is TomlItemKind.LOCAL_DATE_TIME)
        or (
            input_kind == "OffsetDateTime"
            and output_kind is TomlItemKind.OFFSET_DATE_TIME
        )
    )

