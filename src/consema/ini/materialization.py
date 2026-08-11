"""INI canonical materialization: deterministic profile-native output.

Authority (Rust arbitration for exact bytes):

- Entry and completion algebra: crates/consema-ini/src/materialization.rs:
  27-75 — profile/style resolution (materialization.rs:77-127: the exact
  style ids ini.portable-canonical@1 / ini.windows-canonical@1 /
  ini.python-configparser-canonical@1; Windows requires CRLF, Portable and
  Python require LF; Portable accepts UTF-8 only, Windows accepts UTF-16LE
  or one explicit registered Windows code page, Python accepts any non-
  Binary registered text encoding), parse-back and Complete gate
  (materialization.rs:60-66), closure verification (materialization.rs:
  489-535), provenance (materialization.rs:537-677).
- Encoding: text_budget materialization.rs:724-738; encode_text with the
  matching UTF-16 BOM materialization.rs:740-768; encode_fragment
  materialization.rs:770-829 (UTF-8, UTF-16LE/BE, Latin-1, Windows code
  pages; strict — an unrepresentable scalar fails the whole operation,
  RFC 0009 §11, docs/rfcs/0009-ini-family-profiles-v1.md:401-406).
- Writer: materialization.rs:191-461 — the canonical per-profile entry
  representation: portable ``key=value`` ASCII (RFC 0009 §11, lines 415-
  417), Windows ``key=value`` with deterministic quoting only when needed
  to preserve leading/trailing value whitespace (lines 419-424;
  windows_value_needs_quotes materialization.rs:874-888), Python
  ``key = value`` with deterministic four-space continuation and literal
  interpolation markers (lines 426-430; write_python_entry
  materialization.rs:423-453); section/key/value representability gates
  materialization.rs:348-382.
- Failure names and codes: the common materialization algebra of
  consema.document (RFC 0004 §7/§17) plus the INI closure code
  ini.materialization.round-trip-mismatch@1 (error_registry.rs:1021); a
  failed attempt contains no Document and no partial output bytes.

Closure: canonical output reparses under the exact target profile as a
Complete document and reprojects to the identical PortableValue before
completion (RFC 0009 §11, docs/rfcs/0009-...:432-435).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.core.value import Kind, PortableValue
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
from consema.document.structural import FormationStatus, NodeRef, Span
from consema.ini.document import IniDocument
from consema.ini.kinds import (
    IniProfile,
    IniSyntaxKind,
    is_portable_name,
    is_portable_value,
    is_windows_name,
    windows_value_needs_quotes,
)
from consema.ini.parser import parse
from consema.ini.projection import (
    AssociationLocation,
    AssociationRole,
    CollisionPolicy,
    CompleteProjection,
    FailedProjectionAttempt,
    NameComparison,
    ProjectionRequest,
    ProjectionTarget,
    ValuePath,
    ValuePathSegment,
    ValuePathSegmentKind,
    project,
)

# Python stdlib codec bridge for the frozen Windows code-page registry
# (mirror of consema.document.source._CODEC_BY_CODE_PAGE; byte-exactness of
# the DBCS tables against encoding_rs is a differential verification item).
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


def materialize(
    value: PortableValue, request: MaterializationRequest
) -> MaterializationResult:
    """Materializes one complete nested String mapping into a new immutable
    INI document (materialization.rs:27-41)."""
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
    validate_request(request, profile)
    utf8_budget = text_budget(request.encoding, request.limits.max_output_bytes)
    writer = _Writer(
        profile=profile,
        limits=request.limits,
        output=_BoundedText(utf8_budget),
        analyzed=analyzed,
    )
    sections = writer.document(value, ValuePath.root(), 0)
    text = writer.output.finish()
    bytes_ = encode_text(text, request.encoding, request.limits.max_output_bytes)
    selection = parse_encoding_selection(profile, request.encoding)
    try:
        document = parse(bytes_, profile, selection, parse_limits(request.limits))
    except Exception:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED) from None
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
    verify_closure(value, request, document)
    provenance = build_provenance(value, sections, document, request.limits)
    return CompleteMaterialization(
        document=document,
        fidelity=MaterializationFidelity.EXACT,
        report=MaterializationReport(),
        provenance=provenance,
    )


def requested_profile(request: MaterializationRequest) -> IniProfile:
    """Resolves the target profile (materialization.rs:77-89)."""
    profile_id = request.target_profile.id
    version = request.target_profile.version
    if profile_id == "ini.portable" and version == 1:
        return IniProfile.PORTABLE_V1
    if profile_id == "ini.windows" and version == 1:
        return IniProfile.WINDOWS_V1
    if profile_id == "ini.python-configparser" and version == 1:
        return IniProfile.PYTHON_CONFIGPARSER_V1
    raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_PROFILE)


def validate_request(request: MaterializationRequest, profile: IniProfile) -> None:
    """Style, newline, and encoding closure (materialization.rs:91-127;
    RFC 0009 §11, docs/rfcs/0009-...:399-406)."""
    style_matches = (
        (profile is IniProfile.PORTABLE_V1 and request.style.id == "ini.portable-canonical" and request.style.version == 1)
        or (profile is IniProfile.WINDOWS_V1 and request.style.id == "ini.windows-canonical" and request.style.version == 1)
        or (
            profile is IniProfile.PYTHON_CONFIGPARSER_V1
            and request.style.id == "ini.python-configparser-canonical"
            and request.style.version == 1
        )
    )
    if not style_matches:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_STYLE)
    expected_newline = (
        NewlinePolicy.CRLF if profile is IniProfile.WINDOWS_V1 else NewlinePolicy.LF
    )
    if request.newline is not expected_newline:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_NEWLINE)
    encoding_valid = (
        (profile is IniProfile.PORTABLE_V1 and request.encoding == SourceEncoding.utf8())
        or (
            profile is IniProfile.WINDOWS_V1
            and request.encoding.kind
            in (SourceEncodingKind.UTF16LE, SourceEncodingKind.WINDOWS_CODE_PAGE)
        )
        or (
            profile is IniProfile.PYTHON_CONFIGPARSER_V1
            and request.encoding.kind is not SourceEncodingKind.BINARY
        )
    )
    if not encoding_valid:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)


def parse_encoding_selection(
    profile: IniProfile, encoding: SourceEncoding
) -> object:
    """Reparse encoding selection (materialization.rs:129-135)."""
    from consema.ini.kinds import IniEncodingSelection

    if (
        profile in (IniProfile.PORTABLE_V1, IniProfile.PYTHON_CONFIGPARSER_V1)
        and encoding == SourceEncoding.utf8()
    ) or (profile is IniProfile.WINDOWS_V1 and encoding.kind is SourceEncodingKind.UTF16LE):
        return IniEncodingSelection.profile_default()
    return IniEncodingSelection.explicit(encoding)


def parse_limits(limits) -> object:
    """Parse limits derived from the materialization limits
    (materialization.rs:137-160)."""
    from consema.document.limits import ParseLimits
    from consema.ini.kinds import IniParseLimits

    return IniParseLimits(
        common=ParseLimits(
            max_source_bytes=limits.max_output_bytes,
            max_nesting_depth=limits.max_depth,
            max_token_count=limits.max_output_bytes,
            max_node_count=limits.max_output_bytes,
            max_diagnostics=limits.max_report_entries,
        ),
        max_decoded_utf8_bytes=limits.max_output_bytes * 3,
        max_decoded_scalars=limits.max_output_bytes,
        max_physical_lines=limits.max_output_bytes,
        max_physical_line_bytes=limits.max_output_bytes,
        max_physical_line_scalars=limits.max_output_bytes,
        max_logical_lines=limits.max_input_nodes,
        max_logical_line_bytes=limits.max_output_bytes,
        max_logical_line_scalars=limits.max_output_bytes,
        max_continuation_lines=limits.max_output_bytes,
        max_sections=limits.max_input_nodes,
        max_entries=limits.max_input_nodes,
        max_duplicate_group_members=limits.max_input_nodes,
        max_recovery_regions=limits.max_report_entries,
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class _MappingShape(enum.Enum):
    """Input mapping shape (materialization.rs:162-166)."""

    OBJECT = "Object"
    ENTRY_MAPPING = "EntryMapping"


@dataclass(frozen=True, slots=True)
class _InputEntry:
    """Portable input facts of one entry occurrence."""

    association: MaterializationInputLocation
    key: MaterializationInputLocation
    value: MaterializationInputLocation


@dataclass(frozen=True, slots=True)
class _InputSection:
    """Portable input facts of one section occurrence."""

    association: MaterializationInputLocation
    key: MaterializationInputLocation
    value: MaterializationInputLocation
    entries: tuple[_InputEntry, ...]


@dataclass(frozen=True, slots=True)
class _MappingItem:
    """One decoded mapping item with its portable locations."""

    key: str
    value: PortableValue
    association: MaterializationInputLocation
    key_location: MaterializationInputLocation
    value_path: ValuePath


class _Writer:
    def __init__(self, profile, limits, output, analyzed: list[ValuePath]) -> None:
        self.profile = profile
        self.limits = limits
        self.output = output
        self.analyzed = analyzed
        self.input_nodes = 0

    def document(
        self, value: PortableValue, path: ValuePath, depth: int
    ) -> list[_InputSection]:
        """One complete canonical document (materialization.rs:199-253)."""
        shape, outer = self.mapping_items(value, path, depth)
        if shape is _MappingShape.OBJECT and self.profile is IniProfile.WINDOWS_V1:
            _reject_case_equivalent_object_names(outer)
        sections: list[_InputSection] = []
        for section in outer:
            self.validate_section_name(section.key)
            self.output.push_char("[")
            self.output.push_str(section.key)
            self.output.push_char("]")
            self.newline()
            entry_shape, entries = self.mapping_items(
                section.value, section.value_path, depth + 1
            )
            if entry_shape is _MappingShape.OBJECT and self.profile is IniProfile.WINDOWS_V1:
                _reject_case_equivalent_object_names(entries)
            input_entries: list[_InputEntry] = []
            for entry in entries:
                self.validate_key(entry.key)
                self.analyze(entry.value_path, depth + 2)
                value = entry.value.as_string() if entry.value.kind is Kind.STRING else None
                if value is None:
                    raise MaterializationFailure(
                        MaterializationFailureKind.UNREPRESENTABLE,
                        name=entry.value.kind.value,
                        detail=str(entry.value_path),
                    )
                self.write_entry(entry.key, value)
                input_entries.append(
                    _InputEntry(
                        association=entry.association,
                        key=entry.key_location,
                        value=MaterializationInputLocation.value(entry.value_path),
                    )
                )
            sections.append(
                _InputSection(
                    association=section.association,
                    key=section.key_location,
                    value=MaterializationInputLocation.value(section.value_path),
                    entries=tuple(input_entries),
                )
            )
        return sections

    def mapping_items(
        self, value: PortableValue, path: ValuePath, depth: int
    ) -> tuple[str, list[_MappingItem]]:
        """One mapping level with its portable locations
        (materialization.rs:255-331)."""
        self.analyze(path, depth)
        if value.kind is Kind.OBJECT:
            entries = value.as_object()
            shape = _MappingShape.OBJECT
        elif value.kind is Kind.ENTRY_MAPPING:
            entries = value.as_entry_mapping()
            shape = _MappingShape.ENTRY_MAPPING
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
        if shape is _MappingShape.OBJECT:
            for index, (key, item) in enumerate(entries):
                items.append(
                    _MappingItem(
                        key=key,
                        value=item,
                        association=MaterializationInputLocation.association(
                            AssociationLocation(path, index, AssociationRole.OBJECT_ENTRY)
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
                key_path = path.child(ValuePathSegment(ValuePathSegmentKind.ENTRY_KEY, index))
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
                        association=MaterializationInputLocation.association(
                            AssociationLocation(path, index, AssociationRole.ENTRY_MAPPING_ENTRY)
                        ),
                        key_location=MaterializationInputLocation.value(key_path),
                        value_path=path.child(
                            ValuePathSegment(ValuePathSegmentKind.ENTRY_VALUE, index)
                        ),
                    )
                )
        return shape, items

    def analyze(self, path: ValuePath, depth: int) -> None:
        """Input node and depth accounting (materialization.rs:333-346)."""
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

    def validate_section_name(self, value: str) -> None:
        """Section-name representability (materialization.rs:348-363)."""
        valid = _section_name_valid(self.profile, value)
        if not valid:
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST,
                detail="section name is not representable",
            )

    def validate_key(self, value: str) -> None:
        """Entry-key representability (materialization.rs:365-382)."""
        valid = _entry_key_valid(self.profile, value)
        if not valid:
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST,
                detail="entry key is not representable",
            )

    def write_entry(self, key: str, value: str) -> None:
        """One canonical entry (materialization.rs:384-421)."""
        if self.profile is IniProfile.PORTABLE_V1:
            if not all(is_portable_value(byte) for byte in value.encode("utf-8")):
                raise MaterializationFailure(
                    MaterializationFailureKind.INVALID_REQUEST,
                    detail="portable value is not representable",
                )
            self.output.push_str(key)
            self.output.push_char("=")
            self.output.push_str(value)
            self.newline()
        elif self.profile is IniProfile.WINDOWS_V1:
            if "\0" in value or "\r" in value or "\n" in value:
                raise MaterializationFailure(
                    MaterializationFailureKind.INVALID_REQUEST,
                    detail="Windows value is not representable",
                )
            self.output.push_str(key)
            self.output.push_char("=")
            if windows_value_needs_quotes(value):
                quote = "'" if value.startswith('"') and value.endswith('"') else '"'
                self.output.push_char(quote)
                self.output.push_str(value)
                self.output.push_char(quote)
            else:
                self.output.push_str(value)
            self.newline()
        else:
            self.write_python_entry(key, value)

    def write_python_entry(self, key: str, value: str) -> None:
        """Python canonical entry with four-space continuation
        (materialization.rs:423-453)."""
        if "\0" in value or "\r" in value:
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST,
                detail="Python value is not representable",
            )
        if value.endswith("\n"):
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST,
                detail="trailing empty Python value line is not representable",
            )
        lines = value.split("\n")
        first = lines[0]
        validate_python_value_line(first)
        self.output.push_str(key)
        self.output.push_str(" =")
        if first:
            self.output.push_char(" ")
            self.output.push_str(first)
        self.newline()
        for line in lines[1:]:
            validate_python_value_line(line)
            if line:
                self.output.push_str("    ")
                self.output.push_str(line)
            self.newline()

    def newline(self) -> None:
        """Profile-canonical newline (materialization.rs:455-461)."""
        self.output.push_str("\r\n" if self.profile is IniProfile.WINDOWS_V1 else "\n")


def _section_name_valid(profile: IniProfile, value: str) -> bool:
    if profile is IniProfile.PORTABLE_V1:
        return bool(value) and all(is_portable_name(byte) for byte in value.encode("utf-8"))
    if profile is IniProfile.WINDOWS_V1:
        return bool(value) and all(is_windows_name(byte) for byte in value.encode("utf-8"))
    return bool(value) and not any(character in value for character in "\0\r\n")


def _entry_key_valid(profile: IniProfile, value: str) -> bool:
    if profile is IniProfile.PORTABLE_V1:
        return bool(value) and all(is_portable_name(byte) for byte in value.encode("utf-8"))
    if profile is IniProfile.WINDOWS_V1:
        return bool(value) and all(is_windows_name(byte) for byte in value.encode("utf-8"))
    return (
        bool(value)
        and not any(character in value for character in "\0\r\n=:")
        and _trim_horizontal(value) == value
    )


def _trim_horizontal(value: str) -> str:
    return value.strip(" \t")


def validate_python_value_line(line: str) -> None:
    """Edge-whitespace representability of one stored Python value line
    (materialization.rs:463-471)."""
    if _trim_horizontal(line) != line:
        raise MaterializationFailure(
            MaterializationFailureKind.INVALID_REQUEST,
            detail="Python value line edge whitespace is not representable",
        )


def _reject_case_equivalent_object_names(items: list[_MappingItem]) -> None:
    """Object input cannot fabricate Windows case-equivalent collisions
    (materialization.rs:473-487; RFC 0009 §11, docs/rfcs/0009-...:409-411)."""
    seen = set()
    for item in items:
        lowered = item.key.lower()
        if lowered in seen:
            raise MaterializationFailure(
                MaterializationFailureKind.INVALID_REQUEST,
                detail="Object cannot fabricate Windows case-equivalent collisions",
            )
        seen.add(lowered)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


class _BoundedText:
    def __init__(self, max_bytes: int) -> None:
        self.text: list[str] = []
        self.length = 0
        self.max_bytes = max_bytes

    def push_str(self, value: str) -> None:
        new_len = self.length + len(value.encode("utf-8"))
        if new_len > self.max_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        self.text.append(value)
        self.length = new_len

    def push_char(self, character: str) -> None:
        self.push_str(character)

    def finish(self) -> str:
        return "".join(self.text)


def text_budget(encoding: SourceEncoding, max_output_bytes: int) -> int:
    """Decoded-text byte budget per target encoding (materialization.rs:
    724-738)."""
    if encoding.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE, SourceEncodingKind.LATIN1):
        return max_output_bytes * 2
    if (
        encoding.kind is SourceEncodingKind.WINDOWS_CODE_PAGE
        and encoding.code_page is not None
        and encoding.code_page.number != 65001
    ):
        return max_output_bytes * 3
    if encoding.kind is SourceEncodingKind.BINARY:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    return max_output_bytes


def encode_text(text: str, encoding: SourceEncoding, max_output_bytes: int) -> bytes:
    """Exact output bytes including the matching UTF-16 BOM
    (materialization.rs:740-768)."""
    bom_bytes = 2 if encoding.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE) else 0
    fragment_limit = max_output_bytes - bom_bytes
    if fragment_limit < 0:
        raise MaterializationFailure(MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes")
    fragment = encode_fragment(text, encoding, fragment_limit)
    if bom_bytes == 0:
        return fragment
    bom = b"\xff\xfe" if encoding.kind is SourceEncodingKind.UTF16LE else b"\xfe\xff"
    return bom + fragment


def encode_fragment(text: str, encoding: SourceEncoding, max_output_bytes: int) -> bytes:
    """Strict encoding of one decoded fragment (materialization.rs:770-829).

    Encoding is strict: an unrepresentable scalar fails the whole operation
    (RFC 0009 §11, docs/rfcs/0009-...:404-406).
    """
    kind = encoding.kind
    if kind is SourceEncodingKind.UTF8:
        output = text.encode("utf-8")
    elif kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        units = text.encode("utf-16-le" if kind is SourceEncodingKind.UTF16LE else "utf-16-be")
        if len(units) > max_output_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        output = units
    elif kind is SourceEncodingKind.LATIN1:
        if len(text) > max_output_bytes:
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes"
            )
        try:
            output = text.encode("latin-1")
        except UnicodeEncodeError:
            raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING) from None
    elif kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
        assert encoding.code_page is not None
        codec_name = _CODEC_BY_CODE_PAGE[encoding.code_page.number]
        try:
            output = text.encode(codec_name)
        except UnicodeEncodeError:
            raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING) from None
    else:
        raise MaterializationFailure(MaterializationFailureKind.UNSUPPORTED_ENCODING)
    if len(output) > max_output_bytes:
        raise MaterializationFailure(MaterializationFailureKind.RESOURCE_LIMIT, name="output-bytes")
    return output


# ---------------------------------------------------------------------------
# Closure and provenance
# ---------------------------------------------------------------------------


def verify_closure(
    input_value: PortableValue,
    request: MaterializationRequest,
    document: IniDocument,
) -> None:
    """Reparse-and-reproject closure (materialization.rs:489-535)."""
    projection_limits = _ProjectionLimitsLike(
        max_source_associations=request.limits.max_input_nodes,
        max_value_nodes=request.limits.max_input_nodes,
        max_report_entries=request.limits.max_report_entries,
        max_provenance_units=request.limits.max_provenance_entries,
    )
    if input_value.kind is Kind.OBJECT:
        projection_request = ProjectionRequest.require_object(
            NameComparison.ORIGINAL_EXACT, CollisionPolicy.REJECT
        ).with_limits(projection_limits)
    else:
        projection_request = ProjectionRequest.best_exact_entry_mapping().with_limits(
            projection_limits
        )
    result = project(document, projection_request)
    if isinstance(result, CompleteProjection) and result.value == input_value:
        return
    if isinstance(result, FailedProjectionAttempt) and result.diagnostics:
        diagnostic = result.diagnostics[0]
        if diagnostic.code == "core.projection.resource-limit@1":
            limit = diagnostic.arguments.get("limit")
            if limit in ("max_source_associations", "max_value_nodes"):
                raise MaterializationFailure(
                    MaterializationFailureKind.RESOURCE_LIMIT, name="input-nodes"
                )
            if limit == "max_report_entries":
                raise MaterializationFailure(
                    MaterializationFailureKind.RESOURCE_LIMIT, name="report-entries"
                )
            if limit == "max_provenance_units":
                raise MaterializationFailure(
                    MaterializationFailureKind.RESOURCE_LIMIT, name="provenance-entries"
                )
            raise MaterializationFailure(
                MaterializationFailureKind.RESOURCE_LIMIT, name="projection"
            )
    raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)


class _ProjectionLimitsLike:
    """Structural stand-in for the projection limits during closure
    (the projection module accepts any object with these fields)."""

    def __init__(
        self,
        max_source_associations: int,
        max_value_nodes: int,
        max_report_entries: int,
        max_provenance_units: int,
    ) -> None:
        self.max_source_associations = max_source_associations
        self.max_value_nodes = max_value_nodes
        self.max_report_entries = max_report_entries
        self.max_provenance_units = max_provenance_units


def build_provenance(
    input_value: PortableValue,
    sections: list[_InputSection],
    document: IniDocument,
    limits,
) -> MaterializationProvenanceMap:
    """Complete input-to-output provenance (materialization.rs:537-677)."""
    entries: list[MaterializationProvenanceEntry] = []
    root_span = document.authority.span(0, document.source.len())
    entries.append(
        _provenance_entry(
            MaterializationInputLocation.value(ValuePath.root()),
            document.node_ref(),
            root_span,
            document,
            MaterializationRelation.REENCODED,
        )
    )
    entry_offset = 0
    for section_index, input_section in enumerate(sections):
        if section_index >= len(document.sections):
            raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
        section = document.sections[section_index]
        entries.append(
            _provenance_entry(
                input_section.association,
                section.node,
                section.span,
                document,
                MaterializationRelation.REENCODED,
            )
        )
        entries.append(
            _provenance_entry(
                input_section.key,
                section.node,
                section.name_span,
                document,
                MaterializationRelation.REENCODED,
            )
        )
        entries.append(
            _provenance_entry(
                input_section.value,
                section.node,
                section.span,
                document,
                MaterializationRelation.GENERATED,
            )
        )
        for input_entry in input_section.entries:
            if entry_offset >= len(document.entries):
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            entry = document.entries[entry_offset]
            if entry.section != section.node:
                raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
            entries.append(
                _provenance_entry(
                    input_entry.association,
                    entry.node,
                    entry.span,
                    document,
                    MaterializationRelation.REENCODED,
                )
            )
            entries.append(
                _provenance_entry(
                    input_entry.key,
                    entry.node,
                    entry.key_span,
                    document,
                    MaterializationRelation.REENCODED,
                )
            )
            value_outputs = [
                MaterializedOrigin(
                    snapshot=document.snapshot_identity(),
                    node=entry.node,
                    span=entry.value_span,
                    relation=MaterializationRelation.REENCODED,
                )
            ]
            value_outputs.extend(_continuation_outputs(document, entry))
            entries.append(
                MaterializationProvenanceEntry(
                    input=input_entry.value,
                    outputs=tuple(value_outputs),
                )
            )
            entry_offset += 1
    if entry_offset != len(document.entries) or input_value.kind not in (
        Kind.OBJECT,
        Kind.ENTRY_MAPPING,
    ):
        raise MaterializationFailure(MaterializationFailureKind.FORMATION_FAILED)
    return MaterializationProvenanceMap.new(
        entries, document.snapshot_identity(), limits
    )


def _continuation_outputs(document: IniDocument, entry) -> list[MaterializedOrigin]:
    """Every EntryValue piece of the continuation physical lines
    (materialization.rs:629-659)."""
    outputs: list[MaterializedOrigin] = []
    logical = document.resolve_logical_line(entry.logical_line)
    pieces = document.structural_index.pieces
    kinds = document.syntax_kinds
    for physical_node in logical.physical_nodes[1:]:
        physical = document.resolve_physical_line(physical_node)
        for piece, kind in zip(pieces, kinds):
            if piece.span.start_byte >= physical.content_span.end_byte:
                break
            if (
                piece.span.end_byte > physical.content_span.start_byte
                and kind is IniSyntaxKind.ENTRY_VALUE
            ):
                outputs.append(
                    MaterializedOrigin(
                        snapshot=document.snapshot_identity(),
                        node=entry.node,
                        span=piece.span,
                        relation=MaterializationRelation.REENCODED,
                    )
                )
    return outputs


def _provenance_entry(
    input_location: MaterializationInputLocation,
    node: NodeRef,
    span: Span,
    document: IniDocument,
    relation: MaterializationRelation,
) -> MaterializationProvenanceEntry:
    return MaterializationProvenanceEntry(
        input=input_location,
        outputs=(
            MaterializedOrigin(
                snapshot=document.snapshot_identity(),
                node=node,
                span=span,
                relation=relation,
            ),
        ),
    )
