"""``plist.binary@1`` formation (RFC 0013 §2.2, §3, §5, §12).

Authority (Rust arbitration for exact byte semantics and recovery):

- Parser flow: crates/consema-plist/src/parser_binary.rs:496-730 — the
  header check (RFC 0013 §5.1, parser_binary.rs:543-552), trailer facts
  and mandatory integrity checks (parser_binary.rs:554-601, 778-917,
  RFC 0013 §5.11), the offset table (parser_binary.rs:919-1010), the
  object-table scan with prefix recovery (parser_binary.rs:1012-1252),
  extended sizes (parser_binary.rs:1254-1324, RFC 0013 §5.4), dictionary
  key verification (parser_binary.rs:1326-1354), native-document
  eligibility (unproven top object / unproven references / cycles,
  parser_binary.rs:614-671), and exhaustive region coverage
  (parser_binary.rs:703-727).
- Facts: BinaryObjectFact / BinaryOffsetFact / BinaryObjectRefFact /
  BinaryTrailerFacts / BinaryFacts (parser_binary.rs:53-251, RFC 0013
  §8.3).
- Recovery is prefix-based: the first object that fails any structural or
  value check cuts the proven prefix; every proven object keeps its facts
  and native value, and all bytes from the end of the last proven object to
  the offset table form one error region (parser_binary.rs:1-26). The
  native arena adds nodes in object-table order so arena indices equal
  object indices; shared references and forward references resolve through
  PlistValueRef, and cycle/container-depth validation happens in the arena
  build (RFC 0013 §5.11).
- Limits: enforced at the point each claim is read, before any allocation;
  every size arithmetic is checked (hard gate 4, RFC 0013 §12). The
  minimum admissible source length is 42 bytes (RFC 0013 §2.2).

The parser works on raw bytes with source encoding kind Binary: every span
is a half-open raw-byte range over the header, object table, offset table,
and trailer (RFC 0013 §2.2).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.source import SourceLimits, SourceSnapshot
from consema.document.structural import (
    BinaryRegion,
    BinaryStructuralIndex,
    DocumentAuthority,
    FormationStatus,
    NodeRef,
    NodeRole,
    Span,
)
from consema.plist.errors import (
    PlistDiagnostic,
    PlistFormationFailure,
    PlistFormationFailureKind,
    PlistSeverity,
    sort_diagnostics,
)
from consema.plist.kinds import PlistParseLimits, RealWidth
from consema.plist.native import (
    PlistArenaError,
    PlistArenaErrorKind,
    PlistArenaLimits,
    PlistArray,
    PlistBoolean,
    PlistData,
    PlistDate,
    PlistDict,
    PlistDictEntry,
    PlistDocument,
    PlistDocumentBuilder,
    PlistInteger,
    PlistKey,
    PlistReal,
    PlistString,
    PlistUid,
    PlistValue,
    PlistValueRef,
)
from consema.protocol.error_registry import DiagnosticCategory

HEADER = b"bplist00"
MIN_SOURCE_BYTES = 42
TRAILER_BYTES = 32
MAX_FIELD_WIDTH = 8


# ---------------------------------------------------------------------------
# Facts (RFC 0013 §8.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BinaryObjectFact:
    """One proven object-table entry fact (parser_binary.rs:53-89)."""

    index: int
    offset: int
    marker: int
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryOffsetFact:
    """One validated offset-table entry fact (parser_binary.rs:91-117)."""

    index: int
    offset: int
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryObjectRefFact:
    """One decoded object reference of a proven container
    (parser_binary.rs:119-156). For dictionaries, keys occupy positions
    ``0..count`` and values ``count..2*count``."""

    owner: int
    position: int
    target: int
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryTrailerFacts:
    """Trailer field facts (parser_binary.rs:158-216)."""

    sort_version: int
    offset_int_size: int
    object_ref_size: int
    num_objects: int
    top_object: int
    offset_table_offset: int
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryFacts:
    """Complete binary structure facts of one parse (parser_binary.rs:218-
    251)."""

    objects: tuple[BinaryObjectFact, ...]
    offsets: tuple[BinaryOffsetFact, ...]
    refs: tuple[BinaryObjectRefFact, ...]
    trailer: BinaryTrailerFacts


# ---------------------------------------------------------------------------
# Object shapes
# ---------------------------------------------------------------------------


class _ShapeKind(enum.Enum):
    FALSE = "false"
    TRUE = "true"
    INTEGER = "integer"
    REAL = "real"
    DATE = "date"
    DATA = "data"
    ASCII_STRING = "ascii-string"
    UTF16_STRING = "utf16-string"
    UID = "uid"
    ARRAY = "array"
    DICT = "dict"

    def is_string(self) -> bool:
        return self in (_ShapeKind.ASCII_STRING, _ShapeKind.UTF16_STRING)


@dataclass(slots=True)
class _Shape:
    kind: _ShapeKind
    offset: int
    marker: int
    extent: int
    count: int = 0
    key_count: int = 0
    payload_start: int = 0
    refs: list[tuple[int, int, Span]] = None  # type: ignore[assignment]  # (position, target, span)

    def __post_init__(self) -> None:
        self.refs = []


@dataclass(slots=True)
class _RawTrailer:
    unused: bytes
    sort_version: int
    offset_int_size: int
    object_ref_size: int
    num_objects: int
    top_object: int
    offset_table_offset: int


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, source: SourceSnapshot, limits: PlistParseLimits) -> None:
        self.source = source
        self.limits = limits
        self.authority = DocumentAuthority.fresh()
        self.recovered = False
        self.diagnostics: list[PlistDiagnostic] = []
        self.occurrence = 0
        self.uid_count = 0
        self.extended_integers = 0
        self.facts = 0
        # Exact raw span of every arena node (the object marker-through-
        # payload range); consumed by projection provenance (RFC 0013 §9).
        self.value_spans: dict[int, object] = {}

    # -- diagnostics ---------------------------------------------------------

    def recover(
        self,
        code: str,
        primary: object,
        arguments: dict[str, str] | None = None,
    ) -> None:
        """Records one recovery diagnostic and marks the parse Recovered."""
        self.recovered = True
        diagnostic = PlistDiagnostic(
            code=code,
            category=DiagnosticCategory.SYNTAX,
            severity=PlistSeverity.ERROR,
            primary=primary,
            occurrence=self.occurrence,
            arguments=dict(arguments or {}),
        )
        self.occurrence += 1
        self.diagnostics.append(diagnostic)

    def record_fact(self) -> None:
        """One structural-fact budget unit (parser_binary.rs limits)."""
        self.facts += 1
        if self.facts > self.limits.max_binary_facts:
            raise PlistFormationFailure(
                PlistFormationFailureKind.BINARY_FACTS,
                resource_name="binary-facts",
                observed=self.facts,
                limit=self.limits.max_binary_facts,
            )

    def span(self, start: int, end: int) -> Span:
        return self.authority.span(start, end)

    def loc(self, start: int, end: int) -> Span:
        return self.span(start, end)

    def fatal_limit(self, name: str, observed: int, limit: int) -> PlistFormationFailure:
        kind = {
            "object-count": PlistFormationFailureKind.OBJECT_COUNT,
            "container-depth": PlistFormationFailureKind.CONTAINER_DEPTH,
            "dict-entries": PlistFormationFailureKind.DICT_ENTRIES,
            "array-elements": PlistFormationFailureKind.ARRAY_ELEMENTS,
            "duplicate-key-group": PlistFormationFailureKind.DUPLICATE_KEY_GROUP,
            "string-code-units": PlistFormationFailureKind.STRING_CODE_UNITS,
            "data-bytes": PlistFormationFailureKind.DATA_BYTES,
            "uid-count": PlistFormationFailureKind.UID_COUNT,
            "extended-size-value": PlistFormationFailureKind.EXTENDED_SIZE_VALUE,
            "extended-size-integers": PlistFormationFailureKind.EXTENDED_SIZE_INTEGERS,
            "offset-int-size": PlistFormationFailureKind.OFFSET_INT_SIZE,
            "object-ref-size": PlistFormationFailureKind.OBJECT_REF_SIZE,
            "offset-table-bytes": PlistFormationFailureKind.OFFSET_TABLE_BYTES,
            "recovery-regions": PlistFormationFailureKind.RECOVERY_REGIONS,
            "binary-facts": PlistFormationFailureKind.BINARY_FACTS,
            "nesting-depth": PlistFormationFailureKind.NESTING_DEPTH,
        }[name]
        return PlistFormationFailure(
            kind, resource_name=name, observed=observed, limit=limit
        )

    # -- entry ---------------------------------------------------------------

    def parse(self) -> PlistFormedBinary:
        bytes_ = self.source.bytes()
        length = len(bytes_)
        if length < MIN_SOURCE_BYTES:
            raise PlistFormationFailure(
                PlistFormationFailureKind.BINARY_MINIMUM_SIZE,
                resource_name="minimum-size",
                observed=length,
                limit=MIN_SOURCE_BYTES,
            )
        trailer_start = length - TRAILER_BYTES

        header_ok = bytes_[:8] == HEADER
        if not header_ok:
            self.recover(
                "plist.binary.header@1",
                self.loc(0, 8),
                {"expected": "bplist00"},
            )

        raw = _read_raw_trailer(bytes_, trailer_start)
        self.record_fact()
        trailer_facts = BinaryTrailerFacts(
            sort_version=raw.sort_version,
            offset_int_size=raw.offset_int_size,
            object_ref_size=raw.object_ref_size,
            num_objects=raw.num_objects,
            top_object=raw.top_object,
            offset_table_offset=raw.offset_table_offset,
            span=self.span(trailer_start, length),
        )

        trailer_ok = self.validate_trailer(raw, trailer_start, length)
        if not trailer_ok:
            regions = [
                self.region(0, "header" if header_ok else "error-region", 0, 8),
                self.region(1, "error-region", 8, trailer_start),
                self.region(2, "error-region", trailer_start, length),
            ]
            return self.finish(
                None,
                BinaryFacts((), (), (), trailer_facts),
                regions,
            )

        offset_table_offset = raw.offset_table_offset
        num_objects = raw.num_objects
        offset_int_size = raw.offset_int_size
        object_ref_size = raw.object_ref_size
        table_bytes = num_objects * offset_int_size
        if table_bytes > self.limits.max_offset_table_bytes:
            raise self.fatal_limit(
                "offset-table-bytes", table_bytes, self.limits.max_offset_table_bytes
            )

        offset_facts, object_offsets, entry_cut = self.read_offset_table(
            offset_table_offset, num_objects, offset_int_size
        )
        shapes, shape_cut = self.scan_objects(
            object_offsets,
            entry_cut,
            offset_table_offset,
            object_ref_size,
            num_objects,
        )
        cut = self.verify_dict_keys(shapes, shape_cut)

        top_object = raw.top_object
        native_unproven = False
        if top_object >= cut:
            self.recover(
                "plist.binary.unproven-top-object@1",
                self.loc(trailer_start + 16, trailer_start + 24),
                {"top-object": str(top_object)},
            )
            native_unproven = True
        for owner in range(cut):
            for reference in shapes[owner].refs:
                if reference[1] >= cut:
                    self.recover(
                        "plist.binary.unproven-reference@1",
                        reference[2],
                        {"owner": str(owner), "target": str(reference[1])},
                    )
                    native_unproven = True
                    break
            if native_unproven:
                break

        document = None
        if not native_unproven:
            values = self.build_values(shapes, cut)
            builder = PlistDocumentBuilder(
                PlistArenaLimits(
                    max_objects=self.limits.max_object_count,
                    max_container_depth=self.limits.max_container_depth,
                )
            )
            for value in values:
                try:
                    builder.add(value)
                except PlistArenaError as error:
                    if error.kind is PlistArenaErrorKind.OBJECT_LIMIT_EXCEEDED:
                        raise self.fatal_limit("object-count", cut, error.limit) from None
                    raise PlistFormationFailure(
                        PlistFormationFailureKind.BINARY_INTERNAL
                    ) from None
            try:
                document = builder.build(PlistValueRef(top_object))
            except PlistArenaError as error:
                if error.kind is PlistArenaErrorKind.CYCLE_DETECTED:
                    self.recover("plist.binary.cycle@1", None)
                elif error.kind is PlistArenaErrorKind.CONTAINER_DEPTH_LIMIT_EXCEEDED:
                    raise self.fatal_limit(
                        "container-depth", error.node.index, error.limit
                    ) from None
                else:
                    raise PlistFormationFailure(
                        PlistFormationFailureKind.BINARY_INTERNAL
                    ) from None

        objects: list[BinaryObjectFact] = []
        for index in range(cut):
            self.record_fact()
            shape = shapes[index]
            objects.append(
                BinaryObjectFact(
                    index=index,
                    offset=shape.offset,
                    marker=shape.marker,
                    span=self.span(shape.offset, shape.offset + shape.extent),
                )
            )
        refs: list[BinaryObjectRefFact] = []
        for owner in range(cut):
            for position, reference in enumerate(shapes[owner].refs):
                self.record_fact()
                refs.append(
                    BinaryObjectRefFact(
                        owner=owner,
                        position=position,
                        target=reference[1],
                        span=reference[2],
                    )
                )
        facts = BinaryFacts(
            objects=tuple(objects),
            offsets=tuple(offset_facts),
            refs=tuple(refs),
            trailer=trailer_facts,
        )

        regions: list[BinaryRegion] = []
        regions.append(self.region(0, "header" if header_ok else "error-region", 0, 8))
        if cut > 0:
            last_end = shapes[cut - 1].offset + shapes[cut - 1].extent
            regions.append(self.region(1, "object-table", 8, last_end))
            if cut < num_objects:
                if last_end < offset_table_offset:
                    regions.append(
                        self.region(2, "error-region", last_end, offset_table_offset)
                    )
            elif last_end < offset_table_offset:
                regions.append(self.region(2, "padding", last_end, offset_table_offset))
        elif 8 < offset_table_offset:
            regions.append(self.region(1, "error-region", 8, offset_table_offset))
        regions.append(
            self.region(
                len(regions),
                "offset-table",
                offset_table_offset,
                offset_table_offset + table_bytes,
            )
        )
        regions.append(self.region(len(regions), "trailer", trailer_start, length))

        return self.finish(document, facts, regions)

    def region(self, index: int, kind: str, start: int, end: int) -> BinaryRegion:
        return BinaryRegion(
            node=self.authority.node_ref(index, NodeRole.BINARY_REGION),
            span=self.span(start, end),
            kind=kind,
        )

    def finish(
        self,
        document: PlistDocument | None,
        facts: BinaryFacts,
        regions: list[BinaryRegion],
    ) -> PlistFormedBinary:
        error_regions = sum(1 for region in regions if region.kind == "error-region")
        if error_regions > self.limits.max_recovery_regions:
            raise self.fatal_limit(
                "recovery-regions", error_regions, self.limits.max_recovery_regions
            )
        try:
            structural = BinaryStructuralIndex.new(
                self.authority.identity, self.source.len(), regions
            )
        except Exception:
            raise PlistFormationFailure(
                PlistFormationFailureKind.BINARY_COVERAGE
            ) from None
        sort_diagnostics(self.diagnostics)
        return PlistFormedBinary(
            source=self.source,
            authority=self.authority,
            status=FormationStatus.RECOVERED if self.recovered else FormationStatus.COMPLETE,
            diagnostics=tuple(self.diagnostics),
            document=document,
            facts=facts,
            structural=structural,
            limits=self.limits,
            root_node=self.authority.node_ref(0, NodeRole.PLIST_DOCUMENT),
            value_spans=dict(self.value_spans),
        )

    # -- trailer -------------------------------------------------------------

    def validate_trailer(
        self, raw: _RawTrailer, start: int, length: int
    ) -> bool:
        """Mandatory integrity checks before any object is decoded
        (RFC 0013 §5.11; parser_binary.rs:778-917)."""
        ok = True
        if raw.unused != b"\x00\x00\x00\x00\x00":
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start, start + 5),
                {"check": "unused-bytes"},
            )
            ok = False
        if raw.sort_version not in (0, 1):
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 5, start + 6),
                {"check": "sort-version", "sort-version": f"{raw.sort_version:#04x}"},
            )
            ok = False
        if not 1 <= raw.offset_int_size <= MAX_FIELD_WIDTH:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 6, start + 7),
                {
                    "check": "offset-int-size",
                    "offset-int-size": str(raw.offset_int_size),
                },
            )
            ok = False
        elif raw.offset_int_size > self.limits.max_offset_int_size:
            raise self.fatal_limit(
                "offset-int-size",
                raw.offset_int_size,
                self.limits.max_offset_int_size,
            )
        if not 1 <= raw.object_ref_size <= MAX_FIELD_WIDTH:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 7, start + 8),
                {
                    "check": "object-ref-size",
                    "object-ref-size": str(raw.object_ref_size),
                },
            )
            ok = False
        elif raw.object_ref_size > self.limits.max_object_ref_size:
            raise self.fatal_limit(
                "object-ref-size",
                raw.object_ref_size,
                self.limits.max_object_ref_size,
            )
        if raw.num_objects == 0:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 8, start + 16),
                {"check": "num-objects"},
            )
            ok = False
        elif raw.num_objects > self.limits.max_object_count:
            raise self.fatal_limit(
                "object-count", raw.num_objects, self.limits.max_object_count
            )
        if raw.top_object >= raw.num_objects:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 16, start + 24),
                {"check": "top-object", "top-object": str(raw.top_object)},
            )
            ok = False
        max_table_offset = length - TRAILER_BYTES
        if not 9 <= raw.offset_table_offset < max_table_offset:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start + 24, start + 32),
                {
                    "check": "offset-table-offset",
                    "offset-table-offset": str(raw.offset_table_offset),
                },
            )
            ok = False
        if 1 <= raw.offset_int_size < MAX_FIELD_WIDTH:
            capacity = 1 << (8 * raw.offset_int_size)
            if capacity <= raw.offset_table_offset:
                self.recover(
                    "plist.binary.trailer@1",
                    self.loc(start + 24, start + 32),
                    {"check": "offset-int-size-sufficiency"},
                )
                ok = False
        if 1 <= raw.object_ref_size < MAX_FIELD_WIDTH:
            capacity = 1 << (8 * raw.object_ref_size)
            if capacity <= raw.num_objects:
                self.recover(
                    "plist.binary.trailer@1",
                    self.loc(start + 7, start + 8),
                    {"check": "object-ref-size-sufficiency"},
                )
                ok = False
        table_bytes = raw.num_objects * raw.offset_int_size
        expected = raw.offset_table_offset + table_bytes + TRAILER_BYTES
        if expected != length:
            self.recover(
                "plist.binary.trailer@1",
                self.loc(start, length),
                {
                    "check": "total-length",
                    "expected": str(expected),
                    "observed": str(length),
                },
            )
            ok = False
        return ok

    # -- offset table --------------------------------------------------------

    def read_offset_table(
        self,
        offset_table_offset: int,
        num_objects: int,
        offset_int_size: int,
    ) -> tuple[tuple[BinaryOffsetFact, ...], list[int], int]:
        """Reads and validates the offset table in entry order
        (parser_binary.rs:919-1010). The first invalid entry cuts the
        proven prefix."""
        bytes_ = self.source.bytes()
        facts: list[BinaryOffsetFact] = []
        offsets: list[int] = []
        cut = num_objects
        for index in range(num_objects):
            start = offset_table_offset + index * offset_int_size
            end = start + offset_int_size
            if end > len(bytes_):
                self.recover(
                    "plist.binary.offset-table@1",
                    self.loc(min(start, len(bytes_) - 1), len(bytes_)),
                    {"index": str(index), "end": str(end)},
                )
                cut = index
                break
            value = _read_be_u64(bytes_, start, offset_int_size)
            if value < 8 or value >= offset_table_offset:
                self.recover(
                    "plist.binary.offset-table@1",
                    self.loc(start, end),
                    {
                        "index": str(index),
                        "value": f"{value:#x}",
                    },
                )
                cut = index
                break
            self.record_fact()
            facts.append(
                BinaryOffsetFact(
                    index=index,
                    offset=value,
                    span=self.span(start, end),
                )
            )
            offsets.append(value)
        return (tuple(facts), offsets, cut)

    # -- object scan ---------------------------------------------------------

    def scan_objects(
        self,
        object_offsets: list[int],
        entry_cut: int,
        offset_table_offset: int,
        object_ref_size: int,
        num_objects: int,
    ) -> tuple[list[_Shape], int]:
        """Scans the object table in index order; the first fault cuts the
        proven prefix (parser_binary.rs:1012-1252)."""
        shapes: list[_Shape] = []
        cut = entry_cut
        for index in range(entry_cut):
            shape = self.scan_object(
                object_offsets[index],
                offset_table_offset,
                index,
                object_ref_size,
                num_objects,
            )
            if shape is None:
                cut = index
                break
            shapes.append(shape)
        return (shapes, cut)

    def scan_object(
        self,
        offset: int,
        table_end: int,
        index: int,
        object_ref_size: int,
        num_objects: int,
    ) -> _Shape | None:
        """Decodes one object shape at one proven offset (parser_binary.rs:
        1012-1252). None is a fault that cuts the proven prefix."""
        bytes_ = self.source.bytes()
        if offset >= len(bytes_):
            self.recover(
                "plist.binary.offset-table@1",
                self.loc(len(bytes_) - 1, len(bytes_)),
                {"index": str(index), "value": f"{offset:#x}"},
            )
            return None
        marker = bytes_[offset]
        marker_span = self.loc(offset, offset + 1)
        ext_bytes = 0
        kind: _ShapeKind | None = None
        count = 0

        if marker == 0x08:
            kind, count = _ShapeKind.FALSE, 0
        elif marker == 0x09:
            kind, count = _ShapeKind.TRUE, 0
        elif 0x10 <= marker <= 0x13:
            kind, count = _ShapeKind.INTEGER, 1 << (marker & 0x0F)
        elif marker == 0x22:
            kind, count = _ShapeKind.REAL, 4
        elif marker == 0x23:
            kind, count = _ShapeKind.REAL, 8
        elif marker == 0x33:
            kind, count = _ShapeKind.DATE, 8
        elif 0x40 <= marker <= 0x4F:
            sized = self.sized_count(marker, offset, index)
            if sized is None:
                return None
            count, ext_bytes = sized
            if count > self.limits.max_data_bytes:
                raise self.fatal_limit("data-bytes", count, self.limits.max_data_bytes)
            kind = _ShapeKind.DATA
        elif 0x50 <= marker <= 0x5F:
            sized = self.sized_count(marker, offset, index)
            if sized is None:
                return None
            count, ext_bytes = sized
            if count > self.limits.max_string_code_units:
                raise self.fatal_limit(
                    "string-code-units", count, self.limits.max_string_code_units
                )
            kind = _ShapeKind.ASCII_STRING
        elif 0x60 <= marker <= 0x6F:
            sized = self.sized_count(marker, offset, index)
            if sized is None:
                return None
            count, ext_bytes = sized
            if count > self.limits.max_string_code_units:
                raise self.fatal_limit(
                    "string-code-units", count, self.limits.max_string_code_units
                )
            kind = _ShapeKind.UTF16_STRING
        elif 0x80 <= marker <= 0x8F:
            kind, count = _ShapeKind.UID, (marker & 0x0F) + 1
        elif 0xA0 <= marker <= 0xAF:
            sized = self.sized_count(marker, offset, index)
            if sized is None:
                return None
            count, ext_bytes = sized
            if count > self.limits.max_array_elements:
                raise self.fatal_limit(
                    "array-elements", count, self.limits.max_array_elements
                )
            kind = _ShapeKind.ARRAY
        elif 0xD0 <= marker <= 0xDF:
            sized = self.sized_count(marker, offset, index)
            if sized is None:
                return None
            count, ext_bytes = sized
            if count > self.limits.max_dict_entries:
                raise self.fatal_limit(
                    "dict-entries", count, self.limits.max_dict_entries
                )
            kind = _ShapeKind.DICT
        else:
            self.recover(
                "plist.binary.marker@1",
                marker_span,
                {"marker": f"{marker:#04x}", "object": str(index)},
            )
            return None

        assert kind is not None
        payload_start = offset + 1 + ext_bytes
        payload_len = {
            _ShapeKind.UID: count,
            _ShapeKind.DATA: count,
            _ShapeKind.ASCII_STRING: count,
            _ShapeKind.FALSE: 0,
            _ShapeKind.TRUE: 0,
            _ShapeKind.INTEGER: count,
            _ShapeKind.REAL: count,
            _ShapeKind.DATE: 8,
            _ShapeKind.UTF16_STRING: count * 2,
            _ShapeKind.ARRAY: count * object_ref_size,
            _ShapeKind.DICT: count * 2 * object_ref_size,
        }[kind]
        extent = 1 + ext_bytes + payload_len
        end = offset + extent
        if end > table_end:
            self.recover(
                "plist.binary.extent@1",
                marker_span,
                {"object": str(index), "end": str(end), "table-end": str(table_end)},
            )
            return None

        # Value-validity checks that cut the prefix here (RFC 0013 §5.5-5.8).
        if kind is _ShapeKind.ASCII_STRING:
            for at in range(payload_start, end):
                if bytes_[at] >= 0x80:
                    self.recover(
                        "plist.binary.string@1",
                        self.loc(at, at + 1),
                        {"byte": f"{bytes_[at]:#04x}", "object": str(index)},
                    )
                    return None
        elif kind is _ShapeKind.DATE:
            seconds = _f64_from_bits(_read_be_u64(bytes_, payload_start, 8))
            if not (seconds == seconds and abs(seconds) != float("inf")):
                self.recover(
                    "plist.binary.date@1",
                    self.loc(payload_start, payload_start + 8),
                    {"object": str(index)},
                )
                return None
        elif kind is _ShapeKind.UID:
            value = _read_be_u64(bytes_, payload_start, count)
            if value > 0xFFFFFFFF:
                self.recover(
                    "plist.binary.uid@1",
                    self.loc(payload_start, payload_start + count),
                    {"value": f"{value:#x}", "object": str(index)},
                )
                return None
            self.uid_count += 1
            if self.uid_count > self.limits.max_uid_count:
                raise self.fatal_limit(
                    "uid-count", self.uid_count, self.limits.max_uid_count
                )

        shape = _Shape(
            kind=kind,
            offset=offset,
            marker=marker,
            extent=extent,
            count=count,
            key_count=count if kind is _ShapeKind.DICT else 0,
            payload_start=payload_start,
        )
        if kind in (_ShapeKind.ARRAY, _ShapeKind.DICT):
            total = count * 2 if kind is _ShapeKind.DICT else count
            for position in range(total):
                ref_start = payload_start + position * object_ref_size
                ref_end = ref_start + object_ref_size
                ref_span = self.span(ref_start, ref_end)
                target = _read_be_u64(bytes_, ref_start, object_ref_size)
                if target >= num_objects:
                    self.recover(
                        "plist.binary.reference@1",
                        ref_span,
                        {"owner": str(index), "target": str(target)},
                    )
                    return None
                shape.refs.append((position, target, ref_span))
        return shape

    def sized_count(
        self, marker: int, object_offset: int, index: int
    ) -> tuple[int, int] | None:
        """Reads a sized construct's count, honoring the extended-size
        integer rule (RFC 0013 §5.4; parser_binary.rs:1254-1267)."""
        nibble = marker & 0x0F
        if nibble != 0x0F:
            return (nibble, 0)
        return self.read_count(object_offset, index)

    def read_count(
        self, object_offset: int, index: int
    ) -> tuple[int, int] | None:
        """Reads one extended-size integer and enforces its limits
        (parser_binary.rs:1269-1324)."""
        bytes_ = self.source.bytes()
        if object_offset + 1 >= len(bytes_):
            self.recover(
                "plist.binary.offset-table@1",
                self.loc(len(bytes_) - 1, len(bytes_)),
                {"index": str(index), "value": f"{object_offset:#x}"},
            )
            return None
        marker = bytes_[object_offset + 1]
        if not 0x10 <= marker <= 0x13:
            self.recover(
                "plist.binary.extended-size@1",
                self.loc(object_offset + 1, object_offset + 2),
                {"marker": f"{marker:#04x}", "object": str(index)},
            )
            return None
        width = 1 << (marker & 0x0F)
        value = _read_be_u64(bytes_, object_offset + 2, width)
        if value > self.limits.max_extended_size_value:
            raise self.fatal_limit(
                "extended-size-value", value, self.limits.max_extended_size_value
            )
        self.extended_integers += 1
        if self.extended_integers > self.limits.max_extended_size_integers:
            raise self.fatal_limit(
                "extended-size-integers",
                self.extended_integers,
                self.limits.max_extended_size_integers,
            )
        return (value, 1 + width)

    # -- dictionary keys and values ------------------------------------------

    def verify_dict_keys(self, shapes: list[_Shape], cut: int) -> int:
        """Verifies that every dictionary key target is a string object
        (RFC 0013 §5.9; parser_binary.rs:1326-1354). The first violating
        dictionary cuts the proven prefix."""
        for index in range(cut):
            shape = shapes[index]
            if shape.kind is not _ShapeKind.DICT:
                continue
            for key_ref in shape.refs[: shape.key_count]:
                if key_ref[1] >= cut:
                    continue
                if not shapes[key_ref[1]].kind.is_string():
                    self.recover(
                        "plist.binary.non-string-key@1",
                        key_ref[2],
                        {"key-object": str(key_ref[1]), "object": str(index)},
                    )
                    return index
        return cut

    def build_values(
        self, shapes: list[_Shape], cut: int
    ) -> list[PlistValue]:
        """Builds native values in object-table order so arena indices equal
        object indices (parser_binary.rs:1356-1440)."""
        bytes_ = self.source.bytes()
        values: list[PlistValue] = []
        for index, shape in enumerate(shapes[:cut]):
            self.value_spans[index] = self.span(shape.offset, shape.offset + shape.extent)
            if shape.kind is _ShapeKind.FALSE:
                value = PlistValue.boolean(PlistBoolean(False))
            elif shape.kind is _ShapeKind.TRUE:
                value = PlistValue.boolean(PlistBoolean(True))
            elif shape.kind is _ShapeKind.INTEGER:
                value = PlistValue.integer(
                    PlistInteger(self.read_integer(shape.payload_start, shape.count))
                )
            elif shape.kind is _ShapeKind.REAL:
                value = PlistValue.real(self.read_real(shape.payload_start, shape.count))
            elif shape.kind is _ShapeKind.DATE:
                seconds = _f64_from_bits(_read_be_u64(bytes_, shape.payload_start, 8))
                value = PlistValue.date(PlistDate.from_seconds(seconds))
            elif shape.kind is _ShapeKind.DATA:
                value = PlistValue.data(
                    PlistData(bytes(bytes_[shape.payload_start : shape.payload_start + shape.count]))
                )
            elif shape.kind is _ShapeKind.ASCII_STRING:
                units = tuple(
                    int(byte) for byte in bytes_[shape.payload_start : shape.payload_start + shape.count]
                )
                value = PlistValue.string(PlistString(units))
            elif shape.kind is _ShapeKind.UTF16_STRING:
                units = []
                at = shape.payload_start
                for _ in range(shape.count):
                    units.append(
                        int.from_bytes(bytes_[at : at + 2], "big")
                    )
                    at += 2
                value = PlistValue.string(PlistString(tuple(units)))
            elif shape.kind is _ShapeKind.UID:
                value = PlistValue.uid(
                    PlistUid(_read_be_u64(bytes_, shape.payload_start, shape.count))
                )
            elif shape.kind is _ShapeKind.ARRAY:
                value = PlistValue.array(
                    PlistArray(
                        tuple(PlistValueRef(target) for _, target, _ in shape.refs)
                    )
                )
            else:
                value = PlistValue.dict(PlistDict(()))
            values.append(value)

        # Dictionary entries need the key target's string content, which is
        # only complete after every node exists; forward key references are
        # therefore materialized in a second pass.
        for index in range(cut):
            shape = shapes[index]
            if shape.kind is not _ShapeKind.DICT:
                continue
            entries = []
            for position in range(shape.count):
                key_ref = shape.refs[position]
                value_ref = shape.refs[position + shape.count]
                key_value = values[key_ref[1]]
                key_string = key_value.as_string()
                assert key_string is not None
                entries.append(
                    PlistDictEntry(PlistKey(key_string), PlistValueRef(value_ref[1]))
                )
            values[index] = PlistValue.dict(PlistDict(tuple(entries)))
        return values

    def read_integer(self, payload_start: int, width: int) -> int:
        """Widths below 8 are unsigned; 8-byte integers are signed
        two's-complement (RFC 0013 §5.3)."""
        bytes_ = self.source.bytes()
        value = _read_be_u64(bytes_, payload_start, width)
        if width == 8:
            if value >= 1 << 63:
                return value - (1 << 64)
            return value
        return value

    def read_real(self, payload_start: int, width: int) -> PlistReal:
        bytes_ = self.source.bytes()
        if width == 4:
            return PlistReal.from_bits(RealWidth.FLOAT32, _read_be_u64(bytes_, payload_start, 4))
        return PlistReal.from_bits(RealWidth.FLOAT64, _read_be_u64(bytes_, payload_start, 8))


# ---------------------------------------------------------------------------
# Byte helpers
# ---------------------------------------------------------------------------


def _read_raw_trailer(bytes_: bytes, start: int) -> _RawTrailer:
    return _RawTrailer(
        unused=bytes_[start : start + 5],
        sort_version=bytes_[start + 5],
        offset_int_size=bytes_[start + 6],
        object_ref_size=bytes_[start + 7],
        num_objects=int.from_bytes(bytes_[start + 8 : start + 16], "big"),
        top_object=int.from_bytes(bytes_[start + 16 : start + 24], "big"),
        offset_table_offset=int.from_bytes(bytes_[start + 24 : start + 32], "big"),
    )


def _read_be_u64(bytes_: bytes, start: int, width: int) -> int:
    if width > 8 or start + width > len(bytes_):
        raise PlistFormationFailure(PlistFormationFailureKind.BINARY_OVERFLOW)
    return int.from_bytes(bytes_[start : start + width], "big")


def _f64_from_bits(bits: int) -> float:
    import struct

    return struct.unpack(">d", struct.pack(">Q", bits & 0xFFFFFFFFFFFFFFFF))[0]


# ---------------------------------------------------------------------------
# Formed binary document
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlistFormedBinary:
    """One formed ``plist.binary@1`` document (parser_binary.rs:253-362).

    ``Complete`` requires exhaustive byte coverage under the Profile's
    grammar and every configured limit. ``Recovered`` retains the immutable
    source, exhaustive region coverage, ordered diagnostics, every
    independently proven construct, and — when the native value graph is
    provable — the native document; ``document`` is None when the top
    object or a proven reference reaches an unproven object, when the
    object table cannot be located, or when the arena contains a reference
    cycle.
    """

    source: object  # consema.document.source.SourceSnapshot
    authority: DocumentAuthority
    status: FormationStatus
    diagnostics: tuple[PlistDiagnostic, ...]
    document: PlistDocument | None
    facts: BinaryFacts
    structural: BinaryStructuralIndex
    limits: PlistParseLimits
    root_node: NodeRef
    value_spans: dict = field(default_factory=dict, repr=False)

    def render(self) -> bytes:
        """Exact original bytes; unmodified rendering is byte-exact."""
        return self.source.bytes()

    def binary_facts(self) -> BinaryFacts:
        return self.facts

    def binary_structural_index(self) -> BinaryStructuralIndex:
        return self.structural


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_binary(
    raw: bytes,
    selection: PlistEncodingSelection,
    limits: PlistParseLimits,
) -> PlistFormedBinary:
    """Forms one ``plist.binary@1`` document from raw bytes (RFC 0013 §2.2,
    §3, §5).

    The source is an opaque Binary snapshot; only ProfileDefault and
    Explicit(Binary) are consistent with the profile. Any other selection
    is a fatal ``plist.binary.encoding@1`` failure (lib.rs:241-260)."""
    from consema.document.source import SourceEncodingKind

    if selection.kind == "Explicit":
        encoding = selection.encoding
        assert encoding is not None
        if encoding.kind is not SourceEncodingKind.BINARY:
            raise PlistFormationFailure(PlistFormationFailureKind.BINARY_ENCODING)
    try:
        source = SourceSnapshot.from_binary(
            raw,
            SourceLimits(
                max_raw_bytes=limits.common.max_source_bytes,
                max_decoded_utf8_bytes=limits.max_decoded_utf8_bytes,
                max_decoded_scalars=limits.max_decoded_scalars,
            ),
        )
    except Exception as error:
        raise PlistFormationFailure(
            PlistFormationFailureKind.SOURCE, source=error
        ) from None
    return _Parser(source, limits).parse()
