"""Canonical PortableValue materialization for exact Java Properties
profiles.

Authority (Rust arbitration for exact bytes):

- Entry and completion algebra: crates/consema-properties/src/
  materialization.rs:26-77 - profile resolution (materialization.rs:79-90),
  request validation (materialization.rs:92-122: style/newline/encoding
  contracts), bounded text output, reparse under the exact target profile,
  verify_closure (materialization.rs:348-395), and provenance
  (materialization.rs:397-468).
- Styles: java-properties.reader-canonical@1 and
  java-properties.latin1-canonical@1 (RFC 0010 section 12,
  docs/rfcs/0010-java-properties-profiles-v1.md:357-375). Both emit
  associations in input order as ``key=value`` with the selected newline,
  omit timestamp/comments, and escape backslash, control characters, key
  spaces, leading value spaces, ``#``, ``!``, ``=``, and ``:``
  deterministically; Unicode escape hex digits are uppercase and exactly
  four per UTF-16 code unit.
- Escaping and encoding: materialization.rs:308-346 (write_string,
  write_unicode_scalar), 520-534 (text_budget), 536-564 (encode_text with
  BOM emission), 566-631 (encode_fragment for UTF-8 / UTF-16LE / UTF-16BE
  / Latin-1 / Windows code pages), 633-652 (the frozen code-page
  registry).
- Failure names and codes: consema-document/src/materialization.rs:328-351,
  379-390 and RFC 0004 section 17 (docs/rfcs/0004-...:386-423) -
  UnsupportedProfile, UnsupportedStyle, UnsupportedEncoding,
  UnsupportedNewline, Unrepresentable, ResourceLimit, FormationFailed.
- Closure (RFC 0010 section 12, docs/rfcs/0010-...:377-381): every result
  reparses under the exact target profile and reprojects under the
  request's policy; output bytes, fidelity/report, and provenance are
  atomic and bounded. No source BOM is generated for Latin-1
  (docs/rfcs/0010-...:375).

Golden transcription targets: conformance/vectors/java-properties-v1.json
cases materialization.* (lines 91-104).
"""

from __future__ import annotations

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
from consema.document.source import SourceEncoding, SourceEncodingKind
from consema.document.structural import FormationStatus
from consema.properties.document import PropertiesDocument
from consema.properties.kinds import PropertiesProfile
from consema.properties.limits import (
    PropertiesEncodingSelection,
    PropertiesParseLimits,
)
from consema.properties.parser import parse
from consema.properties.projection import (
    AssociationLocation,
    AssociationRole,
    CompleteProjection,
    DuplicatePolicy,
    FailedProjectionAttempt,
    ProjectionLimits,
    ProjectionRequest,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    project,
)

# Frozen code-page registry (materialization.rs:633-652); bridged to the
# Python stdlib codecs by consema.document.source.
_CODEC_BY_CODE_PAGE = {
    874: "cp874",
    932: "cp932",
    936: "cp936",
    949: "cp949",
    950: "cp950",
    1250: "cp1250",
    1251: "cp1251",
    1252: "cp1252",
    1253: "cp1253",
    1254: "cp1254",
    1255: "cp1255",
    1256: "cp1256",
    1257: "cp1257",
    1258: "cp1258",
    65001: "utf-8",
}


class PropertiesStyle:
    """Resolved generation style (materialization.rs:96-110)."""

    READER_CANONICAL = "reader-canonical"
    LATIN1_CANONICAL = "latin1-canonical"


def requested_profile(request: MaterializationRequest) -> PropertiesProfile:
    """Resolves the target profile (materialization.rs:79-90)."""
    profile_id = request.target_profile.id
    version = request.target_profile.version
    if profile_id == "java-properties.reader" and version == 1:
        return PropertiesProfile.READER_V1
    if profile_id == "java-properties.latin1" and version == 1:
        return PropertiesProfile.LATIN1_V1
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_PROFILE)


def requested_style(
    request: MaterializationRequest, profile: PropertiesProfile
) -> str:
    """Resolves the generation style (materialization.rs:96-110)."""
    style_id = request.style.id
    version = request.style.version
    if (
        profile is PropertiesProfile.READER_V1
        and style_id == "java-properties.reader-canonical"
        and version == 1
    ):
        return PropertiesStyle.READER_CANONICAL
    if (
        profile is PropertiesProfile.LATIN1_V1
        and style_id == "java-properties.latin1-canonical"
        and version == 1
    ):
        return PropertiesStyle.LATIN1_CANONICAL
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_STYLE)


def _validate_request(
    request: MaterializationRequest, profile: PropertiesProfile
) -> None:
    """Request contract validation (materialization.rs:92-122)."""
    if request.newline not in (NewlinePolicy.LF, NewlinePolicy.CRLF):
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_NEWLINE)
    if profile is PropertiesProfile.READER_V1:
        if request.encoding.kind is SourceEncodingKind.BINARY:
            raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    elif request.encoding != SourceEncoding.latin1():
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)


class _BoundedText:
    """Byte-budgeted output accumulation (materialization.rs:470-518)."""

    def __init__(self, max_bytes: int) -> None:
        self._text = ""
        self._max = max_bytes

    def push_str(self, value: str) -> None:
        if len(self._text) + len(value) > self._max:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        self._text += value

    def push_hex_unit(self, value: int) -> None:
        self.push_str(f"\\u{value:04X}")

    def finish(self) -> str:
        return self._text


class _InputEntry:
    """Input locations of one emitted association (materialization.rs:152-157)."""

    __slots__ = ("association", "key", "value")

    def __init__(self, association, key, value) -> None:
        self.association = association
        self.key = key
        self.value = value


class _MappingItem:
    __slots__ = ("key", "value", "association", "key_location", "value_path")

    def __init__(self, key, value, association, key_location, value_path) -> None:
        self.key = key
        self.value = value
        self.association = association
        self.key_location = key_location
        self.value_path = value_path


class _Writer:
    """One canonical document writer (materialization.rs:167-346)."""

    def __init__(
        self,
        style: str,
        newline: NewlinePolicy,
        limits,
        analyzed: list[ValuePath],
    ) -> None:
        self.style = style
        self.newline = newline
        self.limits = limits
        self.input_nodes = 0
        self.output = _BoundedText(limits.max_output_bytes)
        self.analyzed = analyzed

    def document(self, value: PortableValue, path: ValuePath, depth: int) -> list:
        """Emits one canonical record per association
        (materialization.rs:177-211)."""
        items = self.mapping_items(value, path, depth)
        input_entries: list[_InputEntry] = []
        for item in items:
            self.analyze(item.value_path, depth + 1)
            if item.value.kind is not Kind.STRING:
                raise MaterializationFailure(
                    MaterializationFailureKind.UNREPRESENTABLE,
                    name=item.value.kind.value,
                    detail=str(item.value_path),
                )
            self.write_string(item.key, True)
            self.output.push_str("=")
            self.write_string(item.value.as_string(), False)
            self.output.push_str(self.newline.bytes.decode("ascii"))
            input_entries.append(
                _InputEntry(
                    association=item.association,
                    key=item.key_location,
                    value=MaterializationInputLocation.value(item.value_path),
                )
            )
        return input_entries

    def mapping_items(self, value: PortableValue, path: ValuePath, depth: int) -> list:
        """Flattens one Object/EntryMapping into ordered mapping items
        (materialization.rs:213-288)."""
        self.analyze(path, depth)
        if value.kind is Kind.OBJECT:
            entries = value.as_object()
        elif value.kind is Kind.ENTRY_MAPPING:
            entries = value.as_entry_mapping()
        else:
            raise MaterializationFailure(
                MaterializationFailureKind.UNREPRESENTABLE,
                name=value.kind.value,
                detail=str(path),
            )
        if len(entries) > self.limits.max_input_nodes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes"
            )
        items: list[_MappingItem] = []
        if value.kind is Kind.OBJECT:
            for index, (key, item) in enumerate(entries):
                items.append(
                    _MappingItem(
                        key=key,
                        value=item,
                        association=AssociationLocation(
                            path, index, AssociationRole.OBJECT_ENTRY
                        ),
                        key_location=MaterializationInputLocation.association(
                            AssociationLocation(path, index, AssociationRole.OBJECT_KEY)
                        ),
                        value_path=path.child(
                            ValuePathSegment(ValuePathSegmentKind.OBJECT_VALUE, key)
                        ),
                    )
                )
        else:
            for index, (key, item) in enumerate(entries):
                key_path = path.child(
                    ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, index)
                )
                self.analyze(key_path, depth + 1)
                if key.kind is not Kind.STRING:
                    raise MaterializationFailure(
                        MaterializationFailureKind.UNREPRESENTABLE,
                        name=key.kind.value,
                        detail=str(key_path),
                    )
                items.append(
                    _MappingItem(
                        key=key.as_string(),
                        value=item,
                        association=AssociationLocation(
                            path, index, AssociationRole.ENTRY_MAPPING_ENTRY
                        ),
                        key_location=MaterializationInputLocation.value(key_path),
                        value_path=path.child(
                            ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, index)
                        ),
                    )
                )
        return items

    def analyze(self, path: ValuePath, depth: int) -> None:
        """Input-node accounting (materialization.rs:290-306)."""
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

    def write_string(self, value: str, is_key: bool) -> None:
        """Deterministic canonical escaping (materialization.rs:308-336;
        RFC 0010 section 12)."""
        leading_value_space = not is_key
        for character in value:
            leading_value_space = self.write_scalar(
                character, is_key, leading_value_space
            )

    def write_scalar(self, character: str, is_key: bool, leading_value_space: bool) -> bool:
        """Emits one scalar under the canonical rules and returns the
        updated leading-value-space state (materialization.rs:308-336)."""
        if character == " " and (is_key or leading_value_space):
            self.output.push_str("\\ ")
        elif character == "\t":
            self.output.push_str("\\t")
        elif character == "\n":
            self.output.push_str("\\n")
        elif character == "\r":
            self.output.push_str("\\r")
        elif character == "\f":
            self.output.push_str("\\f")
        elif character == "\\":
            self.output.push_str("\\\\")
        elif character in ("#", "!", "=", ":"):
            self.output.push_str("\\" + character)
        elif _is_control(character):
            self.write_unicode_scalar(character)
        elif (
            self.style == PropertiesStyle.LATIN1_CANONICAL
            and not 0x20 <= ord(character) <= 0x7E
        ):
            self.write_unicode_scalar(character)
        else:
            self.output.push_str(character)
        if character != " ":
            return False
        return leading_value_space

    def write_unicode_scalar(self, value: str) -> None:
        """Uppercase exactly-four-hex-digit escapes per UTF-16 code unit
        (materialization.rs:338-346)."""
        for unit in _utf16_units(value):
            self.output.push_hex_unit(unit)


def _is_control(character: str) -> bool:
    """The Cc category (Rust ``char::is_control``): U+0000..U+001F and
    U+007F..U+009F."""
    code = ord(character)
    return code <= 0x1F or 0x7F <= code <= 0x9F


def _utf16_units(value: str) -> tuple[int, ...]:
    """Exact UTF-16 code units of one Unicode scalar."""
    encoded = value.encode("utf-16-be")
    return tuple(
        int.from_bytes(encoded[index : index + 2], "big")
        for index in range(0, len(encoded), 2)
    )


def materialize(
    value: PortableValue, request: MaterializationRequest
) -> MaterializationResult:
    """Materializes one complete PortableValue into a new canonical
    Java Properties document (materialization.rs:26-39)."""
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
    _validate_request(request, profile)
    text_limit = _text_budget(request.encoding, request.limits.max_output_bytes)
    writer = _Writer(style, request.newline, request.limits, analyzed)
    input_entries = writer.document(value, ValuePath.root(), 0)
    text = writer.output.finish()
    bytes_ = _encode_text(text, request.encoding, request.limits.max_output_bytes)
    selection = (
        PropertiesEncodingSelection.reader(request.encoding)
        if profile is PropertiesProfile.READER_V1
        else PropertiesEncodingSelection.latin1()
    )
    try:
        document = parse(bytes_, profile, selection, _parse_limits(request.limits))
    except Exception:
        raise MaterializationFailure(
            MaterializationFailureKind.FORMATION_FAILED
        ) from None
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
    _verify_closure(value, request, document)
    provenance = _build_provenance(input_entries, document, request.limits)
    return CompleteMaterialization(
        document=document,
        fidelity=MaterializationFidelity.EXACT,
        report=MaterializationReport(),
        provenance=provenance,
    )


def _verify_closure(
    input_value: PortableValue,
    request: MaterializationRequest,
    document: PropertiesDocument,
) -> None:
    """Exact reparse-and-reproject closure (materialization.rs:348-395)."""
    projection_limits = ProjectionLimits(
        max_source_associations=request.limits.max_input_nodes,
        max_value_nodes=request.limits.max_input_nodes * 2 + 1,
        max_report_entries=request.limits.max_report_entries,
        max_provenance_units=request.limits.max_provenance_entries,
    )
    if input_value.kind is Kind.OBJECT:
        projection_request = ProjectionRequest.require_object(
            DuplicatePolicy.REQUIRE_UNIQUE
        ).with_limits(projection_limits)
    else:
        projection_request = ProjectionRequest.best_exact_entry_mapping().with_limits(
            projection_limits
        )
    result = project(document, projection_request)
    if isinstance(result, CompleteProjection) and result.value == input_value:
        return
    if isinstance(result, FailedProjectionAttempt):
        diagnostic = result.diagnostics[0] if result.diagnostics else None
        if (
            diagnostic is not None
            and diagnostic.code == "core.projection.resource-limit@1"
        ):
            limit = diagnostic.arguments.get("limit")
            name = {
                "max_source_associations": "input-nodes",
                "max_value_nodes": "input-nodes",
                "max_report_entries": "report-entries",
                "max_provenance_units": "provenance-entries",
            }.get(limit, "projection")
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name=name
            )
    raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)


def _build_provenance(
    input_entries: list[_InputEntry],
    document: PropertiesDocument,
    limits,
) -> MaterializationProvenanceMap:
    """Input-to-output provenance with Reencoded relations
    (materialization.rs:397-468)."""
    if len(input_entries) != len(document.properties):
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
    entries: list[MaterializationProvenanceEntry] = []
    root_span = document.authority.span(0, len(document.render()))
    entries.append(
        MaterializationProvenanceEntry(
            input=MaterializationInputLocation.value(ValuePath.root()),
            outputs=(
                MaterializedOrigin(
                    snapshot=document.snapshot_identity(),
                    node=document.node_ref(),
                    span=root_span,
                    relation=MaterializationRelation.REENCODED,
                ),
            ),
        )
    )
    for input_entry, property in zip(input_entries, document.properties):
        entries.append(
            MaterializationProvenanceEntry(
                input=input_entry.association,
                outputs=(
                    MaterializedOrigin(
                        snapshot=document.snapshot_identity(),
                        node=property.node,
                        span=property.span,
                        relation=MaterializationRelation.REENCODED,
                    ),
                ),
            )
        )
        for input_location, spans in (
            (
                input_entry.key,
                property.key_fragments or (property.key_anchor,),
            ),
            (
                input_entry.value,
                property.value_fragments or (property.value_anchor,),
            ),
        ):
            entries.append(
                MaterializationProvenanceEntry(
                    input=input_location,
                    outputs=tuple(
                        MaterializedOrigin(
                            snapshot=document.snapshot_identity(),
                            node=property.node,
                            span=span,
                            relation=MaterializationRelation.REENCODED,
                        )
                        for span in spans
                    ),
                )
            )
    return MaterializationProvenanceMap.new(
        entries, document.snapshot_identity(), limits
    )


def _parse_limits(limits) -> PropertiesParseLimits:
    """Reparse limits derived from the materialization budget
    (materialization.rs:124-150)."""
    from consema.document.limits import ParseLimits

    common = ParseLimits(
        max_source_bytes=limits.max_output_bytes,
        max_nesting_depth=limits.max_depth,
        max_token_count=limits.max_output_bytes,
        max_node_count=limits.max_output_bytes * 2 + 1,
        max_diagnostics=limits.max_report_entries,
    )
    return PropertiesParseLimits(
        common=common,
        max_decoded_utf8_bytes=limits.max_output_bytes * 3,
        max_decoded_scalars=limits.max_output_bytes * 2,
        max_natural_lines=limits.max_input_nodes,
        max_natural_line_bytes=limits.max_output_bytes,
        max_natural_line_scalars=limits.max_output_bytes,
        max_logical_lines=limits.max_input_nodes,
        max_logical_line_natural_lines=1,
        max_logical_line_scalars=limits.max_output_bytes,
        max_properties=limits.max_input_nodes,
        max_comments=0,
        max_escapes=limits.max_output_bytes,
        max_unicode_escapes=limits.max_output_bytes,
        max_java_code_units_per_string=limits.max_output_bytes,
        max_total_java_code_units=limits.max_output_bytes * 2,
        max_duplicate_group_members=limits.max_input_nodes,
        max_recovery_regions=limits.max_report_entries,
    )


def _text_budget(encoding: SourceEncoding, max_output_bytes: int) -> int:
    """Decoded text budget per source encoding (materialization.rs:520-534)."""
    kind = encoding.kind
    if kind in (
        SourceEncodingKind.UTF16LE,
        SourceEncodingKind.UTF16BE,
        SourceEncodingKind.LATIN1,
    ):
        return max_output_bytes * 2
    if (
        kind is SourceEncodingKind.WINDOWS_CODE_PAGE
        and encoding.code_page is not None
        and encoding.code_page.number != 65001
    ):
        return max_output_bytes * 3
    if kind is SourceEncodingKind.BINARY:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    return max_output_bytes


def _encode_text(text: str, encoding: SourceEncoding, max_output_bytes: int) -> bytes:
    """Exact output encoding with optional BOM (materialization.rs:536-564)."""
    kind = encoding.kind
    bom_bytes = 2 if kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE) else 0
    fragment_limit = max_output_bytes - bom_bytes
    if fragment_limit < 0:
        raise MaterializationFailure(
            MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
        )
    fragment = _encode_fragment(text, encoding, fragment_limit)
    if bom_bytes == 0:
        return fragment
    if kind is SourceEncodingKind.UTF16LE:
        return b"\xff\xfe" + fragment
    return b"\xfe\xff" + fragment


def _encode_fragment(text: str, encoding: SourceEncoding, max_output_bytes: int) -> bytes:
    """Encodes one text fragment under one published source encoding
    (materialization.rs:566-631)."""
    kind = encoding.kind
    if kind is SourceEncodingKind.UTF8:
        output = text.encode("utf-8")
    elif kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        output = text.encode(
            "utf-16-le" if kind is SourceEncodingKind.UTF16LE else "utf-16-be"
        )
        if len(output) > max_output_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        return output
    elif kind is SourceEncodingKind.LATIN1:
        if len(text) > max_output_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        try:
            return text.encode("latin-1")
        except UnicodeEncodeError:
            raise MaterializationFailure(
                MaterializationFailureKind.UNSUPPORTED_ENCODING
            ) from None
    elif kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
        assert encoding.code_page is not None
        try:
            output = text.encode(_CODEC_BY_CODE_PAGE[encoding.code_page.number])
        except UnicodeEncodeError:
            raise MaterializationFailure(
                MaterializationFailureKind.UNSUPPORTED_ENCODING
            ) from None
    else:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    if len(output) > max_output_bytes:
        raise MaterializationFailure(
            MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
        )
    return output


def canonical_fragment(
    value: JavaString,
    profile: PropertiesProfile,
    is_key: bool,
    limit: int,
) -> str:
    """Canonical escaped text for one exact Java string (edit insertion,
    rename, and canonical fallback; edit.rs:601-613, canonical_java_string
    edit.rs:925-1027).

    Surrogate pairs combine into one Unicode scalar before profile escaping;
    every unpaired code unit emits an uppercase ``\\uXXXX`` escape and
    contributes no other code unit (edit.rs:936-951).
    """
    style = (
        PropertiesStyle.LATIN1_CANONICAL
        if profile is PropertiesProfile.LATIN1_V1
        else PropertiesStyle.READER_CANONICAL
    )
    writer = _Writer(style, NewlinePolicy.LF, _FragmentLimits(limit), [])
    units = value.code_units()
    index = 0
    leading_value_space = not is_key
    while index < len(units):
        unit = units[index]
        if (
            0xD800 <= unit <= 0xDBFF
            and index + 1 < len(units)
            and 0xDC00 <= units[index + 1] <= 0xDFFF
        ):
            high = unit - 0xD800
            low = units[index + 1] - 0xDC00
            scalar = chr(0x10000 + (high << 10) + low)
            index += 2
        elif 0xD800 <= unit <= 0xDFFF:
            writer.output.push_hex_unit(unit)
            index += 1
            leading_value_space = False
            continue
        else:
            scalar = chr(unit)
            index += 1
        leading_value_space = writer.write_scalar(scalar, is_key, leading_value_space)
    return writer.output.finish()


class _FragmentLimits:
    """Bounded budget for canonical fragments (edit.rs push_bounded)."""

    def __init__(self, max_bytes: int) -> None:
        self.max_output_bytes = max_bytes
        self.max_input_nodes = 1_000_000
        self.max_depth = 1
        self.max_report_entries = 0
        self.max_provenance_entries = 0
