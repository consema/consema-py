"""JSON/JSONC/JSON5 materialization: deterministic canonical output.

Authority (Rust arbitration for exact bytes):

- Entry and completion algebra: crates/consema-json/src/materialization.rs:
  19-32 (materialize), 54-93 (materialize_complete) — profile/style
  resolution (materialization.rs:113-142), UTF-8-only encoding
  (materialization.rs:61-63), pretty requires an explicit newline
  (materialization.rs:64-66), the requested newline is appended exactly
  once (materialization.rs:70-72), output reparses and reprojects before
  completion (materialization.rs:74-78), fidelity Exact and an empty
  report (materialization.rs:87-92), provenance covers every emitted
  value/association (materialization.rs:80-86, 507-747).
- Style ids: json.canonical-compact@1 / json.canonical-pretty@1 (RFC 0004
  §4, docs/rfcs/0004-...:98-105) and json5.canonical-compact@1 /
  json5.canonical-pretty@1 (RFC 0005 §9, docs/rfcs/0005-...:195-212).
- Literal spellings: write_integer (materialization.rs:249-255), the
  canonical decimal "CeE" form (materialization.rs:257-268), string
  escaping with lowercase \\uXXXX (materialization.rs:270-297; U+2028/
  U+2029 escaped only under JSON5 styles), the four frozen non-finite
  spellings (materialization.rs:299-316; RFC 0005 §9), compact layout
  (materialization.rs:319-416), pretty layout with two ASCII spaces per
  level (materialization.rs:447-453).
- Failures: MaterializationFailure names and codes
  (consema-document/src/materialization.rs:328-351, 379-390; RFC 0004
  §17) — UnsupportedProfile, UnsupportedStyle, UnsupportedEncoding,
  UnsupportedNewline, Unrepresentable, ResourceLimit, FormationFailed.
- Provenance: input Value paths and Object/EntryMapping associations map
  to target origins with relation Direct/Generated (materialization.rs:
  507-747); every emitted value and supported association is covered.
- Mapping policy: an exactly representable EntryMapping is not collapsed;
  mapping_policy is irrelevant to it (RFC 0004 §5, docs/rfcs/0004-...:
  131-143; the v1 JSON materializer never converts under
  UniqueStringEntriesToObject — materialization.rs:379-416).

Closure: canonical output reparses under the exact requested profile and
reprojects to the identical PortableValue before completion (RFC 0005 §9,
docs/rfcs/0005-...:210-213).
"""

from __future__ import annotations

import enum

from consema.core.value import PortableValue, Kind
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationFailure,
    MaterializationFailureKind,
    MaterializationFidelity,
    MaterializationInputLocation,
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
from consema.document.structural import FormationStatus, NodeRef, Span
from consema.json.document import JsonDocument, JsonValue
from consema.json.kinds import JsonProfile, JsonValueKind
from consema.json.parser import parse
from consema.json.projection import (
    AssociationLocation,
    AssociationRole,
    CompleteProjection,
    ProjectionRequestBuilder,
    ProjectionTarget,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    project,
)


class JsonStyle(enum.Enum):
    """Resolved generation style (materialization.rs:95-111)."""

    COMPACT = "compact"
    PRETTY = "pretty"
    JSON5_COMPACT = "json5-compact"
    JSON5_PRETTY = "json5-pretty"

    def is_pretty(self) -> bool:
        return self in (JsonStyle.PRETTY, JsonStyle.JSON5_PRETTY)

    def is_json5(self) -> bool:
        return self in (JsonStyle.JSON5_COMPACT, JsonStyle.JSON5_PRETTY)


def requested_profile(
    request: MaterializationRequest,
) -> JsonProfile:
    """Resolves the target profile (materialization.rs:113-125)."""
    profile_id = request.target_profile.id
    version = request.target_profile.version
    if profile_id == "json.strict" and version == 1:
        return JsonProfile.STRICT_V1
    if profile_id == "jsonc.bounded" and version == 1:
        return JsonProfile.JSONC_BOUNDED_V1
    if profile_id == "json5.standard" and version == 1:
        return JsonProfile.JSON5_STANDARD_V1
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_PROFILE)


def requested_style(request: MaterializationRequest, profile: JsonProfile) -> JsonStyle:
    """Resolves the generation style (materialization.rs:127-142)."""
    style_id = request.style.id
    version = request.style.version
    if profile in (JsonProfile.STRICT_V1, JsonProfile.JSONC_BOUNDED_V1):
        if style_id == "json.canonical-compact" and version == 1:
            return JsonStyle.COMPACT
        if style_id == "json.canonical-pretty" and version == 1:
            return JsonStyle.PRETTY
    if profile is JsonProfile.JSON5_STANDARD_V1:
        if style_id == "json5.canonical-compact" and version == 1:
            return JsonStyle.JSON5_COMPACT
        if style_id == "json5.canonical-pretty" and version == 1:
            return JsonStyle.JSON5_PRETTY
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_STYLE)


class _BoundedOutput:
    def __init__(self, max_bytes: int) -> None:
        self._bytes = bytearray()
        self._max = max_bytes

    def push_byte(self, byte: int) -> None:
        if len(self._bytes) + 1 > self._max:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        self._bytes.append(byte)

    def push_bytes(self, bytes_: bytes) -> None:
        if len(self._bytes) + len(bytes_) > self._max:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        self._bytes.extend(bytes_)

    def finish(self) -> bytes:
        return bytes(self._bytes)


class _JsonWriter:
    def __init__(
        self,
        style: JsonStyle,
        newline: NewlinePolicy,
        limits,
        analyzed: list[ValuePath],
    ) -> None:
        self.style = style
        self.newline = newline
        self.limits = limits
        self.input_nodes = 0
        self.output = _BoundedOutput(limits.max_output_bytes)
        self.analyzed = analyzed

    def value(self, value: PortableValue, path: ValuePath, depth: int) -> None:
        self.analyze(path, depth)
        if value.kind is Kind.NULL:
            self.output.push_bytes(b"null")
        elif value.kind is Kind.BOOLEAN:
            self.output.push_bytes(b"true" if value.as_boolean() else b"false")
        elif value.kind is Kind.INTEGER:
            self.write_integer(value.as_integer())
        elif value.kind is Kind.DECIMAL:
            self.write_decimal(value.as_decimal())
        elif value.kind is Kind.BINARY_FLOAT64 and self.style.is_json5():
            self.write_binary_float64(value.as_binary_float64(), path)
        elif value.kind is Kind.STRING:
            self.write_string(value.as_string())
        elif value.kind is Kind.SEQUENCE:
            self.write_sequence(value.as_sequence(), path, depth)
        elif value.kind is Kind.OBJECT:
            self.write_object(value.as_object(), path, depth)
        elif value.kind is Kind.ENTRY_MAPPING:
            self.write_entry_mapping(value.as_entry_mapping(), path, depth)
        else:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE,
                name=value.kind.value,
                detail=str(path),
            )

    def write_integer(self, value: int) -> None:
        self.output.push_bytes(str(value).encode("ascii"))

    def write_decimal(self, value) -> None:
        self.output.push_bytes(f"{value.coefficient}e{value.exponent}".encode("ascii"))

    def write_string(self, value: str) -> None:
        self.output.push_byte(ord('"'))
        for character in value:
            if character == '"':
                self.output.push_bytes(b'\\"')
            elif character == "\\":
                self.output.push_bytes(b"\\\\")
            elif character == "\b":
                self.output.push_bytes(b"\\b")
            elif character == "\f":
                self.output.push_bytes(b"\\f")
            elif character == "\n":
                self.output.push_bytes(b"\\n")
            elif character == "\r":
                self.output.push_bytes(b"\\r")
            elif character == "\t":
                self.output.push_bytes(b"\\t")
            elif "\u0000" <= character <= "\u001f":
                self.output.push_bytes(f"\\u{ord(character):04x}".encode("ascii"))
            elif character in ("\u2028", "\u2029") and self.style.is_json5():
                self.output.push_bytes(f"\\u{ord(character):04x}".encode("ascii"))
            else:
                self.output.push_bytes(character.encode("utf-8"))
        self.output.push_byte(ord('"'))

    def write_binary_float64(self, bits: int, path: ValuePath) -> None:
        spelling = _NON_FINITE_SPELLINGS.get(bits)
        if spelling is None:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE,
                name="BinaryFloat64",
                detail=str(path),
            )
        self.output.push_bytes(spelling)

    def write_sequence(self, values, path: ValuePath, depth: int) -> None:
        self.output.push_byte(ord("["))
        if values and self.style.is_pretty():
            self.layout_newline(depth + 1)
        for index, value in enumerate(values):
            if index != 0:
                self.output.push_byte(ord(","))
                if self.style.is_pretty():
                    self.layout_newline(depth + 1)
            self.value(
                value,
                path.child(ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, index)),
                depth + 1,
            )
        if values and self.style.is_pretty():
            self.layout_newline(depth)
        self.output.push_byte(ord("]"))

    def write_object(self, entries, path: ValuePath, depth: int) -> None:
        self.output.push_byte(ord("{"))
        if entries and self.style.is_pretty():
            self.layout_newline(depth + 1)
        for index, (key, value) in enumerate(entries):
            self.member_separator(index, depth)
            self.write_string(key)
            self.output.push_byte(ord(":"))
            if self.style.is_pretty():
                self.output.push_byte(ord(" "))
            self.value(
                value,
                path.child(ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, key)),
                depth + 1,
            )
        if entries and self.style.is_pretty():
            self.layout_newline(depth)
        self.output.push_byte(ord("}"))

    def write_entry_mapping(self, entries, path: ValuePath, depth: int) -> None:
        self.output.push_byte(ord("{"))
        if entries and self.style.is_pretty():
            self.layout_newline(depth + 1)
        for index, (key, value) in enumerate(entries):
            self.member_separator(index, depth)
            key_path = path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, index))
            self.analyze(key_path, depth + 1)
            if key.kind is not Kind.STRING:
                raise MaterializationFailure(
                    MaterializationFailureKind.UNREPRESENTABLE,
                    name=key.kind.value,
                    detail=str(key_path),
                )
            self.write_string(key.as_string())
            self.output.push_byte(ord(":"))
            if self.style.is_pretty():
                self.output.push_byte(ord(" "))
            self.value(
                value,
                path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, index)),
                depth + 1,
            )
        if entries and self.style.is_pretty():
            self.layout_newline(depth)
        self.output.push_byte(ord("}"))

    def analyze(self, path: ValuePath, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-depth"
            )
        self.input_nodes += 1
        if self.input_nodes > self.limits.max_input_nodes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes"
            )
        self.analyzed.append(path)

    def member_separator(self, index: int, depth: int) -> None:
        if index != 0:
            self.output.push_byte(ord(","))
            if self.style.is_pretty():
                self.layout_newline(depth + 1)

    def layout_newline(self, depth: int) -> None:
        self.output.push_bytes(self.newline.bytes)
        for _ in range(depth):
            self.output.push_bytes(b"  ")


_NON_FINITE_SPELLINGS = {
    0x7FF0000000000000: b"Infinity",
    0xFFF0000000000000: b"-Infinity",
    0x7FF8000000000000: b"NaN",
    0xFFF8000000000000: b"-NaN",
}


def materialize(
    value: PortableValue, request: MaterializationRequest
) -> MaterializationResult:
    """Materializes one complete PortableValue into a new immutable
    JSON/JSONC/JSON5 document (materialization.rs:19-32)."""
    analyzed: list[ValuePath] = []
    try:
        complete = _materialize_complete(value, request, analyzed)
    except MaterializationFailure as failure:
        return FailedMaterializationAttempt(
            failure=failure,
            report=MaterializationReport(),
            analyzed_input_paths=tuple(analyzed),
        )
    return complete


def _materialize_complete(
    value: PortableValue,
    request: MaterializationRequest,
    analyzed: list[ValuePath],
) -> CompleteMaterialization:
    profile = requested_profile(request)
    style = requested_style(request, profile)
    if request.encoding != SourceEncoding.utf8():
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    if style.is_pretty() and request.newline is NewlinePolicy.NONE:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_NEWLINE)

    writer = _JsonWriter(style, request.newline, request.limits, analyzed)
    writer.value(value, ValuePath.root(), 0)
    if request.newline is not NewlinePolicy.NONE:
        writer.output.push_bytes(request.newline.bytes)
    bytes_ = writer.output.finish()

    parse_limits = _parse_limits(request.limits)
    try:
        document = parse(bytes_, profile, parse_limits)
    except Exception:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED) from None
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)

    # Reparse-and-reproject closure: canonical output reprojects to the
    # identical PortableValue before completion (RFC 0005 §9).
    target = (
        ProjectionTarget.JSON5_BEST_EXACT_CORE_V1
        if profile.is_json5()
        else ProjectionTarget.BEST_EXACT_CORE_V1
    )
    projection_result = project(
        document,
        ProjectionRequestBuilder(target).build(),
    )
    if not isinstance(projection_result, CompleteProjection) or projection_result.value != value:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)

    builder = _ProvenanceBuilder(document, request.limits)
    builder.collect(value, ValuePath.root(), document.root())
    provenance = MaterializationProvenanceMap.new(
        builder.entries, document.snapshot_identity(), request.limits
    )
    return CompleteMaterialization(
        document=document,
        fidelity=MaterializationFidelity.EXACT,
        report=MaterializationReport(),
        provenance=provenance,
    )


def _parse_limits(limits) -> object:
    from consema.document.limits import ParseLimits

    return ParseLimits(
        max_source_bytes=limits.max_output_bytes,
        max_nesting_depth=limits.max_depth,
        max_token_count=limits.max_output_bytes,
        max_node_count=limits.max_input_nodes * 3,
        max_diagnostics=limits.max_report_entries,
    )


class _ProvenanceBuilder:
    def __init__(self, document: JsonDocument, limits) -> None:
        self.document = document
        self.limits = limits
        self.units = 0
        self.entries: list[MaterializationProvenanceEntry] = []

    def collect(
        self, input_value: PortableValue, path: ValuePath, output: JsonValue
    ) -> None:
        expected = _EXPECTED_KIND_BY_INPUT.get(input_value.kind)
        if expected is None:
            raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
        availability = output.kind()
        if (
            not availability.is_available
            or availability.value is not expected
        ):
            raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
        if input_value.kind is Kind.BINARY_FLOAT64:
            bits_availability = output.as_binary_float64()
            if (
                not bits_availability.is_available
                or bits_availability.value != input_value.as_binary_float64()
            ):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
        self.push_origin(
            MaterializationInputLocation.value(path),
            self.origin(output.node_ref(), output.span(), MaterializationRelation.DIRECT),
        )
        if input_value.kind is Kind.SEQUENCE:
            values = input_value.as_sequence()
            elements_availability = output.array_elements()
            if (
                not elements_availability.is_available
                or elements_availability.value is None
                or len(elements_availability.value) != len(values)
            ):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, (item, element) in enumerate(
                zip(values, elements_availability.value)
            ):
                child_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.SEQUENCE_ELEMENT, index)
                )
                self.collect(item, child_path, element.value())
                self.add_output(
                    MaterializationInputLocation.value(child_path),
                    self.origin(
                        element.node_ref(),
                        element.span(),
                        MaterializationRelation.GENERATED,
                    ),
                )
        elif input_value.kind is Kind.OBJECT:
            entries = input_value.as_object()
            members_availability = output.object_members()
            if (
                not members_availability.is_available
                or members_availability.value is None
                or len(members_availability.value) != len(entries)
            ):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, ((key, item), member) in enumerate(
                zip(entries, members_availability.value)
            ):
                name_availability = member.name()
                if not name_availability.is_available or name_availability.value != key:
                    raise MaterializationFailure(
                        MaterializationFailureKind.FORMATION_FAILED
                    )
                self.push_origin(
                    MaterializationInputLocation.association(
                        AssociationLocation(
                            path, index, AssociationRole.OBJECT_ENTRY
                        )
                    ),
                    self.origin(
                        member.node_ref(),
                        member.span(),
                        MaterializationRelation.DIRECT,
                    ),
                )
                self.push_origin(
                    MaterializationInputLocation.association(
                        AssociationLocation(path, index, AssociationRole.OBJECT_KEY)
                    ),
                    self.origin(
                        member.key_node_ref(),
                        self.document.span(member.entity().key),
                        MaterializationRelation.DIRECT,
                    ),
                )
                self.collect(
                    item,
                    path.child(
                        ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, key)
                    ),
                    member.value(),
                )
        elif input_value.kind is Kind.ENTRY_MAPPING:
            entries = input_value.as_entry_mapping()
            members_availability = output.object_members()
            if (
                not members_availability.is_available
                or members_availability.value is None
                or len(members_availability.value) != len(entries)
            ):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            for index, ((key, item), member) in enumerate(
                zip(entries, members_availability.value)
            ):
                if key.kind is not Kind.STRING:
                    raise MaterializationFailure(
                        MaterializationFailureKind.FORMATION_FAILED
                    )
                name_availability = member.name()
                if not name_availability.is_available or name_availability.value != key.as_string():
                    raise MaterializationFailure(
                        MaterializationFailureKind.FORMATION_FAILED
                    )
                self.push_origin(
                    MaterializationInputLocation.association(
                        AssociationLocation(
                            path, index, AssociationRole.ENTRY_MAPPING_ENTRY
                        )
                    ),
                    self.origin(
                        member.node_ref(),
                        member.span(),
                        MaterializationRelation.DIRECT,
                    ),
                )
                self.collect(
                    item,
                    path.child(
                        ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, index)
                    ),
                    member.value(),
                )
        else:
            # Scalar kinds: only the value origin above applies.
            return

    def origin(
        self, node: NodeRef, span: Span, relation: MaterializationRelation
    ) -> MaterializedOrigin:
        return MaterializedOrigin(
            snapshot=self.document.snapshot_identity(),
            node=node,
            span=span,
            relation=relation,
        )

    def push_origin(
        self, input_location: MaterializationInputLocation, origin: MaterializedOrigin
    ) -> None:
        self.units += 1
        if self.units > self.limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        self.entries.append(
            MaterializationProvenanceEntry(input=input_location, outputs=(origin,))
        )

    def add_output(
        self, input_location: MaterializationInputLocation, origin: MaterializedOrigin
    ) -> None:
        self.units += 1
        if self.units > self.limits.max_provenance_entries:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
            )
        for index, entry in enumerate(self.entries):
            if entry.input == input_location:
                self.entries[index] = MaterializationProvenanceEntry(
                    input=entry.input, outputs=entry.outputs + (origin,)
                )
                return
        self.entries.append(
            MaterializationProvenanceEntry(input=input_location, outputs=(origin,))
        )


_FAILURE_NAME_BY_KIND = {
    MaterializationFailureKind.INVALID_REQUEST: "InvalidRequest",
    MaterializationFailureKind.UNSUPPORTED_PROFILE: "UnsupportedProfile",
    MaterializationFailureKind.UNSUPPORTED_STYLE: "UnsupportedStyle",
    MaterializationFailureKind.UNSUPPORTED_ENCODING: "UnsupportedEncoding",
    MaterializationFailureKind.UNSUPPORTED_NEWLINE: "UnsupportedNewline",
    MaterializationFailureKind.UNREPRESENTABLE: "Unrepresentable",
    MaterializationFailureKind.RESOURCE_LIMIT: "ResourceLimit",
    MaterializationFailureKind.FORMATION_FAILED: "FormationFailed",
}


def materialization_failure_name(failure: MaterializationFailure) -> str:
    """Exact Rust variant spelling referenced by the conformance vectors
    (json-family-v2.json:147 "Unrepresentable", :153 "UnsupportedStyle";
    consema-document/src/materialization.rs:328-351)."""
    return _FAILURE_NAME_BY_KIND[failure.kind]


_EXPECTED_KIND_BY_INPUT = {
    Kind.NULL: JsonValueKind.NULL,
    Kind.BOOLEAN: JsonValueKind.BOOLEAN,
    Kind.INTEGER: JsonValueKind.INTEGER,
    Kind.DECIMAL: JsonValueKind.DECIMAL,
    Kind.BINARY_FLOAT64: JsonValueKind.BINARY_FLOAT64,
    Kind.STRING: JsonValueKind.STRING,
    Kind.SEQUENCE: JsonValueKind.ARRAY,
    Kind.OBJECT: JsonValueKind.OBJECT,
    Kind.ENTRY_MAPPING: JsonValueKind.OBJECT,
}


def canonical_fragment(value: PortableValue, profile: JsonProfile, limits) -> bytes:
    """Canonical compact fragment for one PortableValue (edit insertion and
    rename; materialization.rs:34-52)."""
    analyzed: list[ValuePath] = []
    writer = _JsonWriter(
        JsonStyle.JSON5_COMPACT if profile.is_json5() else JsonStyle.COMPACT,
        NewlinePolicy.NONE,
        limits,
        analyzed,
    )
    writer.value(value, ValuePath.root(), 0)
    return writer.output.finish()
