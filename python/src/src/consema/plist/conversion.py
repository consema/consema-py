"""Cross-representation conversion between ``plist.xml@1`` and
``plist.binary@1`` (RFC 0013 §7).

Authority (Rust arbitration for the exact semantics):

- Conversion algebra: crates/consema-plist/src/document.rs:224-312
  (Document::convert_to: same-representation, recovered, and missing-native
  gates), 494-552 (convert_xml_to_binary: node/key/report budget checks,
  serialization, reparse closure with native-model equality, and the
  value-mapped event mapping), 553-593 (convert_binary_to_xml:
  reachable-graph analysis, expressibility validation, serialization,
  reparse closure).
- Expressibility boundary: document.rs:619-741 (analyze — one
  ``plist.conversion.inexpressible@1`` diagnostic per violating node in
  source arena order with the ``fact`` argument naming the binary-only
  fact: shared-identity, uid, float32-width, real-nan-payload,
  unpaired-surrogate, non-xml-character, fractional-seconds,
  date-year-range), document.rs:931-946 (real_expressible), 958-1000
  (whole_second_date / civil_from_days), 1028-1057 (is_xml_char /
  is_xml_text).
- Serializers: serialize_xml document.rs:767-890 (Apple header spelling,
  four-space indentation, LF line endings, a trailing newline; the root
  value element is written at depth 0), serialize_binary document.rs:1080-
  1123 (document-ordered object table, one target object per source node
  so shared identity survives, key objects before their dictionary, minimal
  offset/ref sizes, ``sortVersion = 0x00``).
- Report events: document.rs:314-434 (one RepresentationChange event
  followed by one ValueMapped event per reachable native node, in source
  arena order; hard gate 2).
- Failure codes: document.rs:264 (same-representation@1), 270-276
  (formation@1), 718 (inexpressible@1), 1297 (internal@1), 1303
  (reparse@1).

Conversion is atomic (hard gate 3): a native fact the target representation
cannot express fails the whole conversion and returns no target document.
XML-sourced documents never contain binary-only facts, so conversion to
binary is always expressible.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.structural import FormationStatus
from consema.plist.document import PlistDocument, PlistRepresentation
from consema.plist.errors import (
    PlistConversionFailure,
    PlistConversionFailureKind,
    PlistFormationFailure,
)
from consema.plist.kinds import (
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
    PlistStringStatus,
    RealWidth,
)
from consema.plist.native import (
    PLIST_EPOCH_OFFSET_UNIX,
    PlistReal,
    PlistString,
    PlistValue,
    PlistValueRef,
)

_EXACT_UNIX_SECONDS_BOUND = 9_007_199_254_740_992.0
_NEGATIVE_ZERO_BITS = 0x8000_0000_0000_0000


class ConversionEventKind(enum.Enum):
    """Event kinds of one conversion report (document.rs:425-434)."""

    REPRESENTATION_CHANGE = "RepresentationChange"
    VALUE_MAPPED = "ValueMapped"


@dataclass(frozen=True, slots=True)
class ConversionReportEvent:
    """One conversion report event (document.rs:379-423)."""

    kind: ConversionEventKind
    source: PlistValueRef | None = None
    target: int | None = None


@dataclass(frozen=True, slots=True)
class ConversionReport:
    """Conversion report of one cross-representation conversion
    (document.rs:345-377)."""

    events: tuple[ConversionReportEvent, ...]

    def representation_changed(self) -> bool:
        """Whether the conversion changed representation (always true for a
        successful cross-representation conversion)."""
        return any(
            event.kind is ConversionEventKind.REPRESENTATION_CHANGE
            for event in self.events
        )


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    """One successful cross-representation conversion (document.rs:314-343)."""

    document: PlistDocument
    report: ConversionReport

    def into_document(self) -> PlistDocument:
        return self.document


# ---------------------------------------------------------------------------
# Expressibility and reachable graph (document.rs:619-741)
# ---------------------------------------------------------------------------


def _is_xml_char(character: str) -> bool:
    codepoint = ord(character)
    return (
        character in ("\t", "\n", "\r")
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _is_xml_text(units: tuple[int, ...]) -> bool:
    """Whether every scalar of one well-formed UTF-16 sequence is an XML
    1.0 character; an unpaired surrogate is not (document.rs:1038-1057)."""
    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 < len(units) and 0xDC00 <= units[index + 1] <= 0xDFFF:
                scalar = 0x10000 + ((unit - 0xD800) << 10) + (units[index + 1] - 0xDC00)
                index += 2
            else:
                return False
        elif 0xDC00 <= unit <= 0xDFFF:
            return False
        else:
            scalar = unit
            index += 1
        if not _is_xml_char(chr(scalar)):
            return False
    return True


def _real_expressible(real: PlistReal) -> bool:
    """Whether the exact bits of one real survive the XML spelling
    (document.rs:931-946)."""
    value = real.as_f64()
    if value != value:  # NaN
        import struct

        return struct.unpack(">Q", struct.pack(">d", value))[0] == _NAN_BITS
    if abs(value) == float("inf"):
        return True
    rendered = _render_real(real)
    try:
        parsed = float(rendered)
    except ValueError:
        return False
    import struct

    return struct.unpack(">Q", struct.pack(">d", parsed))[0] == struct.unpack(
        ">Q", struct.pack(">d", value)
    )[0]


_NAN_BITS = 0x7FF8_0000_0000_0000


def _render_real(real: PlistReal) -> str:
    """Deterministic shortest-round-trip decimal spelling of one real
    (RFC 0013 §4.6, §10.1; document.rs:914-929)."""
    value = real.as_f64()
    if value != value:
        return "nan"
    if abs(value) == float("inf"):
        return "-inf" if value < 0 else "inf"
    return repr(value)


def _civil_from_days(days: int) -> tuple[int, int, int]:
    """Proleptic Gregorian calendar date of ``days`` since the Unix epoch
    (document.rs:985-1000)."""
    z = days + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    day_of_era = z - era * 146_097
    year_of_era = (
        day_of_era - day_of_era // 1_460 + day_of_era // 36_524 - day_of_era // 146_096
    ) // 365
    year = year_of_era + era * 400
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    month_prime = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * month_prime + 2) // 5 + 1
    month = month_prime + (3 if month_prime < 10 else -9)
    year = year + 1 if month <= 2 else year
    return (year, month, day)


def _whole_second_date(seconds: float):
    """Decomposes exact plist-epoch seconds into XML calendar fields
    (document.rs:958-983). Returns (year, month, day, hour, minute,
    second) or raises PlistConversionFailure(INEXPRESSIBLE)."""
    if seconds != int(seconds):
        return None
    unix = seconds + PLIST_EPOCH_OFFSET_UNIX
    if abs(unix) >= _EXACT_UNIX_SECONDS_BOUND:
        return None
    unix_int = int(unix)
    days = unix_int // 86_400
    seconds_of_day = unix_int % 86_400
    year, month, day = _civil_from_days(days)
    if abs(year) > 0xFFFFFFFF:
        return None
    hour = seconds_of_day // 3_600
    minute = (seconds_of_day % 3_600) // 60
    second = seconds_of_day % 60
    return (year, month, day, hour, minute, second)


@dataclass(slots=True)
class _ReachableGraph:
    children: list[list[PlistValueRef]]
    ranks: list[int]
    reachable: list[int]


def _children_of(native: PlistDocument, node: PlistValueRef) -> list[PlistValueRef]:
    value = native.get(node)
    if value is None:
        return []
    if value.kind.value == "dict":
        return [entry.value for entry in value.payload.entries]
    if value.kind.value == "array":
        return list(value.payload.elements)
    return []


def _analyze(
    native: PlistDocument, limits: PlistParseLimits
) -> _ReachableGraph:
    """Validates one native document against the XML expressibility
    boundary (RFC 0013 §7, hard gate 3) and computes the reachable-graph
    facts (document.rs:626-741)."""
    node_count = native.node_count()
    if node_count > limits.max_conversion_nodes:
        raise PlistConversionFailure(
            PlistConversionFailureKind.INTERNAL,
            detail="conversion-nodes limit exceeded",
        )
    children = [_children_of(native, PlistValueRef(index)) for index in range(node_count)]
    visited = [False] * node_count
    indegree = [0] * node_count
    postorder: list[int] = []
    root = native.root()
    visited[root.index] = True
    stack: list[tuple[PlistValueRef, int]] = [(root, 0)]
    while stack:
        node, next_child = stack.pop()
        node_children = children[node.index]
        if next_child < len(node_children):
            stack.append((node, next_child + 1))
            child = node_children[next_child]
            indegree[child.index] += 1
            if not visited[child.index]:
                visited[child.index] = True
                stack.append((child, 0))
        else:
            postorder.append(node.index)
    ranks = [10**18] * node_count
    for rank, index in enumerate(postorder):
        ranks[index] = rank
    violations: list[tuple[int, str]] = []
    for index in range(node_count):
        if not visited[index]:
            continue
        if indegree[index] > 1:
            violations.append((index, "shared-identity"))
        value = native.get(PlistValueRef(index))
        if value is None:
            continue
        kind = value.kind
        if kind.value == "uid":
            violations.append((index, "uid"))
        elif kind.value == "real":
            real = value.payload
            if real.width is RealWidth.FLOAT32:
                violations.append((index, "float32-width"))
            elif not _real_expressible(real):
                violations.append((index, "real-nan-payload"))
        elif kind.value == "string":
            string = value.payload
            if string.status() is PlistStringStatus.UNPAIRED_SURROGATE:
                violations.append((index, "unpaired-surrogate"))
            elif not _is_xml_text(string.code_units):
                violations.append((index, "non-xml-character"))
        elif kind.value == "date":
            seconds = value.payload.seconds
            if _whole_second_date(seconds) is None:
                if seconds != int(seconds):
                    violations.append((index, "fractional-seconds"))
                else:
                    violations.append((index, "date-year-range"))
        elif kind.value == "dict":
            for entry in value.payload.entries:
                key = entry.key
                if key.status() is PlistStringStatus.UNPAIRED_SURROGATE:
                    violations.append((index, "unpaired-surrogate"))
                elif not _is_xml_text(key.code_units()):
                    violations.append((index, "non-xml-character"))
    if violations:
        diagnostics = [
            PlistConversionFailure(
                PlistConversionFailureKind.INEXPRESSIBLE,
                detail=f"fact={fact},node={node}",
            )
            for node, fact in violations[: limits.common.max_diagnostics]
        ]
        raise diagnostics[0]
    return _ReachableGraph(
        children=children,
        ranks=ranks,
        reachable=[index for index in range(node_count) if visited[index]],
    )


# ---------------------------------------------------------------------------
# Serializers (document.rs:767-890, 1080-1123)
# ---------------------------------------------------------------------------


def _write_indent(out: list[str], depth: int) -> None:
    for _ in range(depth):
        out.append("    ")


def _escape_xml_text(text: str) -> str:
    """Escapes XML text content (RFC 0013 §4.9, §10.1): ``&``, ``<``, ``>``,
    and a literal CR (document.rs:899-912)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", "&#13;")
    )


def _render_date(year: int, month: int, day: int, hour: int, minute: int, second: int) -> str:
    sign = "-" if year < 0 else ""
    return f"{sign}{abs(year):04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z"


def _encode_base64(bytes_: bytes) -> str:
    import base64

    return base64.b64encode(bytes_).decode("ascii")


def _write_string_object(out: bytearray, string: PlistString) -> None:
    """Writes one string object: the ASCII marker when every code unit is
    below 0x80, else the UTF-16BE marker (RFC 0013 §5.6)."""
    units = string.code_units
    if all(unit < 0x80 for unit in units):
        _write_sized(out, 0x50, len(units))
        for unit in units:
            out.append(unit)
    else:
        _write_sized(out, 0x60, len(units))
        for unit in units:
            out.extend(unit.to_bytes(2, "big"))


def _write_sized(out: bytearray, marker: int, count: int) -> None:
    """Writes one sized marker: counts below 0x0F fit the low nibble, while
    the nibble 0x0F itself is the extended-size sentinel (RFC 0013 §5.4)."""
    if count < 0x0F:
        out.append(marker | count)
        return
    out.append(marker | 0x0F)
    width = _unsigned_width(count)
    out.append(0x10 | {1: 0, 2: 1, 4: 2, 8: 3}[width])
    out.extend(count.to_bytes(width, "big"))


def _write_be(out: bytearray, value: int, width: int) -> None:
    out.extend(value.to_bytes(width, "big"))


def _ref_size_for(max_index: int) -> int:
    """Smallest width in bytes whose capacity (``2^(8 * width)``) exceeds
    ``max_index`` (RFC 0013 §5.11 sufficiency checks)."""
    size = 1
    capacity = 256
    while max_index >= capacity and size < 8:
        size += 1
        capacity *= 256
    return size


def _unsigned_width(value: int) -> int:
    if value <= 0xFF:
        return 1
    if value <= 0xFFFF:
        return 2
    if value <= 0xFFFF_FFFF:
        return 4
    return 8


def _integer_width(value: int) -> int:
    """Minimal marker width for one signed 64-bit integer: negatives always
    use the signed 8-byte form (RFC 0013 §5.3, §10.2)."""
    if value >= 0:
        return _unsigned_width(value)
    return 8


def serialize_xml(native: PlistDocument, graph: _ReachableGraph) -> bytes:
    """Serializes one native value graph as a ``plist.xml@1`` source
    (RFC 0013 §4, §7; document.rs:767-890). The document uses the Apple
    header spelling, four-space indentation, LF line endings, and a
    trailing newline; the root value element is written at depth 0."""
    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.append(
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    )
    out.append('<plist version="1.0">\n')
    root = native.root()
    root_value = native.get(root)
    if root_value is not None and root_value.kind.value in ("dict", "array"):
        frames: list[tuple[PlistValueRef, int, int]] = [(root, 0, 0)]
        while frames:
            node, depth, next_child = frames.pop()
            value = native.get(node)
            if value is None:
                raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
            children = graph.children[node.index]
            if next_child == 0:
                _write_indent(out, depth)
                if value.kind.value == "dict":
                    if not children:
                        out.append("<dict></dict>\n")
                        continue
                    out.append("<dict>\n")
                elif value.kind.value == "array":
                    if not children:
                        out.append("<array></array>\n")
                        continue
                    out.append("<array>\n")
                else:
                    raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
            if next_child < len(children):
                frames.append((node, depth, next_child + 1))
                child = children[next_child]
                if value.kind.value == "dict":
                    key_text = value.payload.entries[next_child].key.to_unicode()
                    _write_indent(out, depth + 1)
                    out.append("<key>")
                    out.append(_escape_xml_text(key_text))
                    out.append("</key>\n")
                child_value = native.get(child)
                if (
                    child_value is not None
                    and child_value.kind.value in ("dict", "array")
                ):
                    frames.append((child, depth + 1, 0))
                else:
                    _emit_scalar_xml(out, native, child, depth + 1)
            else:
                _write_indent(out, depth)
                if value.kind.value == "dict":
                    out.append("</dict>\n")
                elif value.kind.value == "array":
                    out.append("</array>\n")
                else:
                    raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
    else:
        _emit_scalar_xml(out, native, root, 0)
    out.append("</plist>\n")
    return "".join(out).encode("utf-8")


def _emit_scalar_xml(
    out: list[str], native: PlistDocument, node: PlistValueRef, depth: int
) -> None:
    """Emits one scalar value element at the given depth
    (document.rs:843-890)."""
    _write_indent(out, depth)
    value = native.get(node)
    if value is None:
        raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
    kind = value.kind
    if kind.value == "string":
        out.append("<string>")
        out.append(_escape_xml_text(value.payload.to_unicode()))
        out.append("</string>\n")
    elif kind.value == "integer":
        out.append(f"<integer>{value.payload.value}</integer>\n")
    elif kind.value == "real":
        out.append(f"<real>{_render_real(value.payload)}</real>\n")
    elif kind.value == "boolean":
        out.append("<true/>\n" if value.payload.value else "<false/>\n")
    elif kind.value == "date":
        fields = _whole_second_date(value.payload.seconds)
        if fields is None:
            raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
        out.append(f"<date>{_render_date(*fields)}</date>\n")
    elif kind.value == "data":
        out.append("<data>")
        out.append(_encode_base64(value.payload.bytes))
        out.append("</data>\n")
    else:
        raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)


def serialize_binary(native: PlistDocument) -> bytes:
    """Serializes one native value graph as a ``plist.binary@1`` source
    (RFC 0013 §5, §7; document.rs:1080-1123).

    The object table is document-ordered with one target object per source
    node (shared identity survives), every dictionary is followed by its
    key objects and then its values, and the offset/ref sizes are the
    minimal widths satisfying the trailer sufficiency checks."""
    node_count = native.node_count()
    target_index = [0] * node_count
    keys_before = 0
    for index in range(node_count):
        node = native.get(PlistValueRef(index))
        dict_keys = node.payload.entries.__len__() if node is not None and node.kind.value == "dict" else 0
        target_index[index] = index + keys_before + dict_keys
        keys_before += dict_keys
    target_object_count = node_count + keys_before
    ref_size = _ref_size_for(target_object_count)
    out = bytearray(b"bplist00")
    offsets: list[int] = []
    for index in range(node_count):
        node = native.get(PlistValueRef(index))
        if node is None:
            raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)
        if node.kind.value == "dict":
            for entry in node.payload.entries:
                offsets.append(len(out))
                _write_string_object(out, entry.key.string)
        offsets.append(len(out))
        _write_binary_object(out, index, node, ref_size, target_index)
    offset_table_offset = len(out)
    offset_int_size = _ref_size_for(offset_table_offset)
    for offset in offsets:
        _write_be(out, offset, offset_int_size)
    out.extend(b"\x00\x00\x00\x00\x00")
    out.append(0)  # sortVersion
    out.append(offset_int_size)
    out.append(ref_size)
    out.extend(target_object_count.to_bytes(8, "big"))
    out.extend(target_index[native.root().index].to_bytes(8, "big"))
    out.extend(offset_table_offset.to_bytes(8, "big"))
    return bytes(out)


def _write_binary_object(
    out: bytearray,
    source_index: int,
    node: PlistValue,
    ref_size: int,
    target_index: list[int],
) -> None:
    """Writes one object: marker, size, payload, and references
    (RFC 0013 §5)."""
    kind = node.kind
    if kind.value == "dict":
        dict_value = node.payload
        count = len(dict_value.entries)
        _write_sized(out, 0xD0, count)
        key_start = target_index[source_index] - count
        for position in range(count):
            _write_be(out, key_start + position, ref_size)
        for entry in dict_value.entries:
            _write_be(out, target_index[entry.value.index], ref_size)
    elif kind.value == "array":
        array_value = node.payload
        count = len(array_value.elements)
        _write_sized(out, 0xA0, count)
        for element in array_value.elements:
            _write_be(out, target_index[element.index], ref_size)
    elif kind.value == "string":
        _write_string_object(out, node.payload)
    elif kind.value == "integer":
        value = node.payload.value
        width = _integer_width(value)
        out.append(0x10 | {1: 0, 2: 1, 4: 2, 8: 3}[width])
        _write_be(out, value & 0xFFFFFFFFFFFFFFFF, width)
    elif kind.value == "real":
        real = node.payload
        if real.width is RealWidth.FLOAT64:
            out.append(0x23)
            _write_be(out, real.bits, 8)
        else:
            out.append(0x22)
            _write_be(out, real.bits, 4)
    elif kind.value == "boolean":
        out.append(0x09 if node.payload.value else 0x08)
    elif kind.value == "date":
        out.append(0x33)
        _write_be(out, _f64_bits(node.payload.seconds), 8)
    elif kind.value == "data":
        data = node.payload.bytes
        _write_sized(out, 0x40, len(data))
        out.extend(data)
    elif kind.value == "uid":
        value = node.payload.value
        width = _uid_width(value)
        out.append(0x80 | (width - 1))
        _write_be(out, value, width)
    else:
        raise PlistConversionFailure(PlistConversionFailureKind.INTERNAL)


def _uid_width(value: int) -> int:
    """Minimal byte width of one unsigned 32-bit UID value (RFC 0013 §5.8)."""
    if value <= 0xFF:
        return 1
    if value <= 0xFFFF:
        return 2
    if value <= 0xFF_FFFF:
        return 3
    return 4


def _f64_bits(value: float) -> int:
    import struct

    return struct.unpack(">Q", struct.pack(">d", value))[0]


# ---------------------------------------------------------------------------
# Conversion entry
# ---------------------------------------------------------------------------


def convert(
    document: PlistDocument, target: PlistProfile, limits: PlistParseLimits
) -> ConvertedDocument:
    """Converts one document to the other representation (RFC 0013 §7;
    document.rs:252-289)."""
    source_representation = document.representation()
    target_representation = (
        PlistRepresentation.XML
        if target is PlistProfile.XML_V1
        else PlistRepresentation.BINARY
    )
    if source_representation is target_representation:
        raise PlistConversionFailure(PlistConversionFailureKind.SAME_REPRESENTATION)
    if document.formation_status() is not FormationStatus.COMPLETE:
        raise PlistConversionFailure(
            PlistConversionFailureKind.FORMATION,
            detail="status=recovered",
        )
    native = document.document()
    if native is None:
        raise PlistConversionFailure(
            PlistConversionFailureKind.FORMATION,
            detail="status=no-native-document",
        )
    if source_representation is PlistRepresentation.XML:
        return _convert_xml_to_binary(native, limits)
    return _convert_binary_to_xml(native, limits)


def _convert_xml_to_binary(
    native: PlistDocument, limits: PlistParseLimits
) -> ConvertedDocument:
    """Converts one ``plist.xml@1`` document to ``plist.binary@1``
    (RFC 0013 §7; document.rs:494-552)."""
    node_count = native.node_count()
    if node_count > limits.max_conversion_nodes:
        raise PlistConversionFailure(
            PlistConversionFailureKind.INTERNAL,
            detail="conversion-nodes limit exceeded",
        )
    total_keys = 0
    for index in range(node_count):
        node = native.get(PlistValueRef(index))
        if node is not None and node.kind.value == "dict":
            total_keys += len(node.payload.entries)
    target_object_count = node_count + total_keys
    if target_object_count > limits.max_object_count:
        raise PlistConversionFailure(
            PlistConversionFailureKind.INTERNAL,
            detail="object-count limit exceeded",
        )
    event_count = 1 + node_count
    if event_count > limits.max_report_events:
        raise PlistConversionFailure(
            PlistConversionFailureKind.INTERNAL,
            detail="report-events limit exceeded",
        )
    bytes_ = serialize_binary(native)
    from consema.plist.document import PlistDocument as _Document
    from consema.plist.parser_binary import parse_binary

    try:
        formed = parse_binary(bytes_, PlistEncodingSelection.profile_default(), limits)
    except PlistFormationFailure as error:
        raise PlistConversionFailure(
            PlistConversionFailureKind.REPARSE,
            detail=error.code,
        ) from None
    if (
        formed.status is not FormationStatus.COMPLETE
        or formed.document != native
    ):
        raise PlistConversionFailure(PlistConversionFailureKind.REPARSE)
    keys_before = 0
    events: list[ConversionReportEvent] = [ConversionReportEvent(ConversionEventKind.REPRESENTATION_CHANGE)]
    for index in range(node_count):
        node = native.get(PlistValueRef(index))
        dict_keys = len(node.payload.entries) if node is not None and node.kind.value == "dict" else 0
        events.append(
            ConversionReportEvent(
                ConversionEventKind.VALUE_MAPPED,
                source=PlistValueRef(index),
                target=index + keys_before + dict_keys,
            )
        )
        keys_before += dict_keys
    document = _Document(
        authority=formed.authority,
        source=formed.source,
        profile=PlistProfile.BINARY_V1,
        _representation=PlistRepresentation.BINARY,
        _formation_status=formed.status,
        diagnostics=formed.diagnostics,
        native=formed.document,
        formed_binary=formed,
        root_node=formed.root_node,
    )
    return ConvertedDocument(
        document=document,
        report=ConversionReport(tuple(events)),
    )


def _convert_binary_to_xml(
    native: PlistDocument, limits: PlistParseLimits
) -> ConvertedDocument:
    """Converts one ``plist.binary@1`` document to ``plist.xml@1``
    (RFC 0013 §7; document.rs:553-593)."""
    graph = _analyze(native, limits)
    reachable_count = len(graph.reachable)
    event_count = 1 + reachable_count
    if event_count > limits.max_report_events:
        raise PlistConversionFailure(
            PlistConversionFailureKind.INTERNAL,
            detail="report-events limit exceeded",
        )
    bytes_ = serialize_xml(native, graph)
    from consema.plist.document import PlistDocument as _Document
    from consema.plist.parser_xml import parse_xml

    try:
        formed = parse_xml(bytes_, PlistEncodingSelection.profile_default(), limits)
    except PlistFormationFailure as error:
        raise PlistConversionFailure(
            PlistConversionFailureKind.REPARSE,
            detail=error.code,
        ) from None
    if (
        formed.status is not FormationStatus.COMPLETE
        or formed.document != native
    ):
        raise PlistConversionFailure(PlistConversionFailureKind.REPARSE)
    events: list[ConversionReportEvent] = [ConversionReportEvent(ConversionEventKind.REPRESENTATION_CHANGE)]
    for index in graph.reachable:
        events.append(
            ConversionReportEvent(
                ConversionEventKind.VALUE_MAPPED,
                source=PlistValueRef(index),
                target=graph.ranks[index],
            )
        )
    document = _Document(
        authority=formed.authority,
        source=formed.source,
        profile=PlistProfile.XML_V1,
        _representation=PlistRepresentation.XML,
        _formation_status=formed.status,
        diagnostics=formed.diagnostics,
        native=formed.document,
        formed_xml=formed,
        root_node=formed.root_node,
    )
    return ConvertedDocument(
        document=document,
        report=ConversionReport(tuple(events)),
    )
