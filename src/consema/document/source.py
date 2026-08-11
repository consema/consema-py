"""Raw source ownership, encoding facts, content identity, and decoded locations.

Authority (language-neutral first; Rust only for byte/registry arbitration):

- conformance/vectors/source-v1.json — the machine-readable case suite
  "consema.source.conformance@1": digest cases lines 4-22, encoding
  round-trips lines 23-52, encoding-conflict / unsupported-bom /
  invalid-sequence cases lines 53-82, decoded-location cases lines 83-100,
  limit cases lines 155-172.
- RFC 0003 (docs/rfcs/0003-source-syntax-query-and-patch-v1.md): content
  digest §3 lines 45-62; closed v1 encoding IDs §4.1 lines 66-77; resolution
  inputs and priority §4.2 lines 79-107; decoding rejections §4.3 lines
  109-122; raw spans and decoded boundaries §5 lines 124-141;
  core.source-snapshot@1 exact fields §6 lines 143-160; resource behavior
  §12 lines 311-317.
- crates/consema-document/src/source.rs — byte/registry arbitration only:
  SourceEncoding wire ids source.rs:141-150; WindowsCodePage registry
  source.rs:57-119; BOM detection source.rs:784-804; resolution priority
  source.rs:727-782; UTF-16 decode source.rs:806-869; Latin-1 decode
  source.rs:880-894; code-page decode source.rs:901-992; SourceLimits
  defaults source.rs:401-409; decoded-boundary conversion source.rs:622-665,
  1090-1157.
- Error codes: crates/consema-protocol/src/error_registry.rs
  (core.source.invalid-utf8@1:207, core.source.encoding-conflict@1:366,
  core.source.invalid-sequence@1:372, core.source.unsupported-bom@1:405,
  core.source.resource-limit@1:399, and the v6 additions
  core.source.code-page-required@1:967, core.source.unsupported-code-page@1:973).

go/document is a cross-reference only; no code structure is copied.
"""

from __future__ import annotations

import bisect
import codecs
import enum
import sys
from dataclasses import dataclass, field, replace

from consema.document.ids import ContentDigest
from consema.document.structural import LocationError, LocationErrorKind

# CHECKPOINT_STRIDE, crates/consema-document/src/source.rs:13
_CHECKPOINT_STRIDE = 256

# Source limits defaults, crates/consema-document/src/source.rs:401-409
_DEFAULT_MAX_RAW_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_DECODED_UTF8_BYTES = 128 * 1024 * 1024
_DEFAULT_MAX_DECODED_SCALARS = 64 * 1024 * 1024

# Frozen Windows code-page registry, crates/consema-document/src/source.rs:63-68
_FROZEN_CODE_PAGES = frozenset((874, 932, 936, 949, 950) + tuple(range(1250, 1259)) + (65001,))

# Python stdlib codec name per frozen code page (zero-dependency bridge; the
# byte-exactness of the DBCS tables against encoding_rs is a differential
# verification item, not a claimed gate).
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


class SourceEncodingKind(enum.Enum):
    """Closed source encoding kinds (source.rs:121-155; RFC 0003 §4.1)."""

    BINARY = "binary"
    UTF8 = "utf-8"
    UTF16LE = "utf-16le"
    UTF16BE = "utf-16be"
    LATIN1 = "latin-1"
    WINDOWS_CODE_PAGE = "windows-code-page"


@dataclass(frozen=True, slots=True)
class WindowsCodePage:
    """One deterministic Windows code page admitted by source contract v2
    (crates/consema-document/src/source.rs:57-119).

    Only the frozen set {874, 932, 936, 949, 950, 1250-1258, 65001} is
    published (source.rs:63-68). Windows code pages are never resolved from
    the host locale. The v6 codes core.source.code-page-required@1 and
    core.source.unsupported-code-page@1 (error_registry.rs:967,973) belong to
    the declaring format Profile layer, not to this resolver.
    """

    number: int

    def __post_init__(self) -> None:
        if self.number not in _FROZEN_CODE_PAGES:
            raise ValueError(
                f"code page {self.number} is not in the frozen portable registry"
            )

    @classmethod
    def from_number(cls, number: int) -> WindowsCodePage | None:
        """Resolves one numeric code page only when source v2 publishes it."""
        if number not in _FROZEN_CODE_PAGES:
            return None
        return cls(number=number)

    @property
    def name(self) -> str:
        """Canonical ``windows-{number}`` spelling (source.rs:76-96)."""
        return f"windows-{self.number}"

    @property
    def codec_name(self) -> str:
        """Python stdlib codec used to decode this page."""
        return _CODEC_BY_CODE_PAGE[self.number]


@dataclass(frozen=True, slots=True)
class SourceEncoding:
    """Closed source encoding set supported by source contracts v1 and v2
    (crates/consema-document/src/source.rs:121-155).

    Wire identifiers match the vector suite's ``selected`` values exactly
    (conformance/vectors/source-v1.json lines 27, 33, 39, 45, 51):
    "binary", "utf-8", "utf-16le", "utf-16be", "latin-1".
    """

    kind: SourceEncodingKind
    code_page: WindowsCodePage | None = None

    def __post_init__(self) -> None:
        if self.kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
            if self.code_page is None:
                raise ValueError("windows-code-page encoding requires a code page")
        elif self.code_page is not None:
            raise ValueError("code page is only valid for windows-code-page encoding")

    @classmethod
    def binary(cls) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.BINARY)

    @classmethod
    def utf8(cls) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.UTF8)

    @classmethod
    def utf16le(cls) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.UTF16LE)

    @classmethod
    def utf16be(cls) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.UTF16BE)

    @classmethod
    def latin1(cls) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.LATIN1)

    @classmethod
    def windows_code_page(cls, code_page: WindowsCodePage) -> SourceEncoding:
        return cls(kind=SourceEncodingKind.WINDOWS_CODE_PAGE, code_page=code_page)

    @property
    def as_str(self) -> str:
        """Stable wire identifier (source.rs:141-150)."""
        if self.kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
            return self.code_page.name
        return self.kind.value

    @property
    def is_text(self) -> bool:
        return self.kind is not SourceEncodingKind.BINARY


class BomPolicy(enum.Enum):
    """Whether marker-shaped leading bytes participate in BOM resolution
    (crates/consema-document/src/source.rs:158-164).

    Wire spellings "DetectUnicode"/"TreatAsContent" are frozen by
    crates/consema-protocol/src/source.rs:606-609.
    """

    DETECT_UNICODE = "DetectUnicode"
    TREAT_AS_CONTENT = "TreatAsContent"


class BomKind(enum.Enum):
    """Recognized Unicode byte-order mark (source.rs:167-187)."""

    UTF8 = "Utf8"
    UTF16LE = "Utf16Le"
    UTF16BE = "Utf16Be"

    @property
    def encoding(self) -> SourceEncoding:
        return {
            BomKind.UTF8: SourceEncoding.utf8(),
            BomKind.UTF16LE: SourceEncoding.utf16le(),
            BomKind.UTF16BE: SourceEncoding.utf16be(),
        }[self]


class UnsupportedBomKind(enum.Enum):
    """Recognized but unsupported Unicode marker (source.rs:719-725)."""

    UTF32LE = "Utf32Le"
    UTF32BE = "Utf32Be"


@dataclass(frozen=True, slots=True)
class EncodingRequest:
    """Caller inputs to deterministic encoding resolution
    (crates/consema-document/src/source.rs:190-260; RFC 0003 §4.2).

    Resolution priority is caller_override -> declaration -> bom ->
    profile_default (RFC 0003 §4.2, docs/rfcs/0003-...:95-104); priority
    chooses only when higher evidence is absent. Any two present BOM,
    declaration, and caller facts that disagree produce an EncodingConflict;
    the resolver never guesses or silently lets priority hide a contradiction.
    """

    profile_default: SourceEncoding
    bom_policy: BomPolicy = BomPolicy.DETECT_UNICODE
    declaration: SourceEncoding | None = None
    caller_override: SourceEncoding | None = None

    @classmethod
    def new(cls, profile_default: SourceEncoding) -> EncodingRequest:
        """Starts with the required profile default and no higher-priority facts."""
        return cls(profile_default=profile_default)

    @classmethod
    def binary(cls) -> EncodingRequest:
        """Opaque-binary request (source.rs:211-214)."""
        return cls(profile_default=SourceEncoding.binary())

    def with_declaration(self, declaration: SourceEncoding) -> EncodingRequest:
        return replace(self, declaration=declaration)

    def with_caller_override(self, caller_override: SourceEncoding) -> EncodingRequest:
        return replace(self, caller_override=caller_override)

    def with_bom_policy(self, bom_policy: BomPolicy) -> EncodingRequest:
        return replace(self, bom_policy=bom_policy)


@dataclass(frozen=True, slots=True)
class EncodingFacts:
    """Complete, auditable result of encoding resolution
    (crates/consema-document/src/source.rs:263-379; RFC 0003 §4.2).

    ``selected`` is the first present value in the frozen priority order
    caller_override -> declaration -> bom -> profile_default, and only when
    no two present facts disagree.
    """

    profile_default: SourceEncoding
    bom_policy: BomPolicy
    bom: BomKind | None
    declaration: SourceEncoding | None
    caller_override: SourceEncoding | None
    selected: SourceEncoding

    @classmethod
    def from_claim(
        cls,
        profile_default: SourceEncoding,
        bom: BomKind | None,
        declaration: SourceEncoding | None,
        caller_override: SourceEncoding | None,
        selected: SourceEncoding,
    ) -> EncodingFacts:
        """Validates a structurally complete encoding-facts claim (source.rs:278-300).

        Proves resolution consistency only; the caller must still verify that
        the claimed BOM is present in the supplied raw bytes.
        """
        request = EncodingRequest.new(profile_default)
        if declaration is not None:
            request = request.with_declaration(declaration)
        if caller_override is not None:
            request = request.with_caller_override(caller_override)
        resolved = _resolve_assertions(request, bom)
        if resolved.selected != selected:
            raise SourceError(
                SourceErrorKind.ENCODING_CONFLICT,
                bom=bom.encoding if bom is not None else None,
                declaration=declaration,
                caller_override=caller_override,
            )
        return resolved

    @classmethod
    def from_claim_with_bom_policy(
        cls,
        profile_default: SourceEncoding,
        bom_policy: BomPolicy,
        bom: BomKind | None,
        declaration: SourceEncoding | None,
        caller_override: SourceEncoding | None,
        selected: SourceEncoding,
    ) -> EncodingFacts:
        """Validates a source-v2 claim including explicit BOM interpretation
        (source.rs:303-333)."""
        if bom_policy is BomPolicy.TREAT_AS_CONTENT and bom is not None:
            raise SourceError(
                SourceErrorKind.ENCODING_CONFLICT,
                bom=bom.encoding,
                declaration=declaration,
                caller_override=caller_override,
            )
        request = EncodingRequest.new(profile_default).with_bom_policy(bom_policy)
        if declaration is not None:
            request = request.with_declaration(declaration)
        if caller_override is not None:
            request = request.with_caller_override(caller_override)
        resolved = _resolve_assertions(request, bom)
        if resolved.selected != selected:
            raise SourceError(
                SourceErrorKind.ENCODING_CONFLICT,
                bom=bom.encoding if bom is not None else None,
                declaration=declaration,
                caller_override=caller_override,
            )
        return resolved


@dataclass(frozen=True, slots=True)
class DecodedPosition:
    """One exact boundary expressed in every supported coordinate system
    (crates/consema-document/src/source.rs:412-422; RFC 0003 §5).

    Only scalar boundaries are addressable; a raw offset inside a UTF-8 scalar
    or between a UTF-16 surrogate pair is rejected rather than rounded.
    """

    raw_byte: int
    decoded_utf8_byte: int
    unicode_scalar_offset: int
    utf16_code_unit_offset: int


class DecodedOffsetKind(enum.Enum):
    """Decoded coordinate system for raw-byte resolution (source.rs:425-433)."""

    UTF8_BYTE = "utf8-byte"
    UNICODE_SCALAR = "unicode-scalar"
    UTF16_CODE_UNIT = "utf16-code-unit"


@dataclass(frozen=True, slots=True)
class DecodedOffset:
    """A decoded coordinate to resolve back to an exact raw-byte boundary."""

    kind: DecodedOffsetKind
    value: int

    @classmethod
    def utf8_byte(cls, value: int) -> DecodedOffset:
        return cls(kind=DecodedOffsetKind.UTF8_BYTE, value=value)

    @classmethod
    def unicode_scalar(cls, value: int) -> DecodedOffset:
        return cls(kind=DecodedOffsetKind.UNICODE_SCALAR, value=value)

    @classmethod
    def utf16_code_unit(cls, value: int) -> DecodedOffset:
        return cls(kind=DecodedOffsetKind.UTF16_CODE_UNIT, value=value)


class SourceErrorKind(enum.Enum):
    """Closed set of source construction failures
    (crates/consema-document/src/source.rs:669-708)."""

    INVALID_UTF8 = "invalid-utf8"
    INVALID_SEQUENCE = "invalid-sequence"
    ENCODING_CONFLICT = "encoding-conflict"
    UNSUPPORTED_BOM = "unsupported-bom"
    RESOURCE_LIMIT = "resource-limit"
    OFFSET_OVERFLOW = "offset-overflow"


_CODE_BY_SOURCE_KIND = {
    SourceErrorKind.INVALID_UTF8: "core.source.invalid-utf8@1",
    SourceErrorKind.INVALID_SEQUENCE: "core.source.invalid-sequence@1",
    SourceErrorKind.ENCODING_CONFLICT: "core.source.encoding-conflict@1",
    SourceErrorKind.UNSUPPORTED_BOM: "core.source.unsupported-bom@1",
    SourceErrorKind.RESOURCE_LIMIT: "core.source.resource-limit@1",
    SourceErrorKind.OFFSET_OVERFLOW: "core.source.resource-limit@1",
}


class SourceError(Exception):
    """Stable source construction failure with a frozen registered code.

    Code mapping authority: crates/consema-protocol/src/error_registry.rs
    (core.source.invalid-utf8@1:207, core.source.invalid-sequence@1:372,
    core.source.encoding-conflict@1:366, core.source.unsupported-bom@1:405,
    core.source.resource-limit@1:399); variant semantics per
    crates/consema-document/src/source.rs:669-708. The OffsetOverflow variant
    shares the resource-limit code (FatalFormationFailure mapping,
    lib.rs:701-705). Error text is human presentation only (RFC 0016 §6).
    """

    def __init__(
        self,
        kind: SourceErrorKind,
        *,
        encoding: SourceEncoding | None = None,
        byte_offset: int | None = None,
        valid_up_to: int | None = None,
        bom: SourceEncoding | None = None,
        declaration: SourceEncoding | None = None,
        caller_override: SourceEncoding | None = None,
        unsupported_bom: UnsupportedBomKind | None = None,
        name: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.encoding = encoding
        self.byte_offset = byte_offset
        self.valid_up_to = valid_up_to
        self.bom = bom
        self.declaration = declaration
        self.caller_override = caller_override
        self.unsupported_bom = unsupported_bom
        self.name = name
        self.observed = observed
        self.limit = limit

    @property
    def code(self) -> str:
        """The frozen registered ``core.source.*@1`` code (RFC 0016 §6)."""
        return _CODE_BY_SOURCE_KIND[self.kind]

    def __str__(self) -> str:
        if self.kind is SourceErrorKind.INVALID_UTF8:
            return f"source bytes are not valid UTF-8 (valid up to byte {self.valid_up_to})"
        if self.kind is SourceErrorKind.INVALID_SEQUENCE:
            return (
                f"source bytes are invalid for {self.encoding.as_str} "
                f"at byte {self.byte_offset}"
            )
        if self.kind is SourceErrorKind.ENCODING_CONFLICT:
            return "source encoding facts conflict"
        if self.kind is SourceErrorKind.UNSUPPORTED_BOM:
            return (
                f"source begins with unsupported byte-order mark "
                f"{self.unsupported_bom.value}"
            )
        if self.kind is SourceErrorKind.RESOURCE_LIMIT:
            return f"source limit {self.name}: observed {self.observed} > limit {self.limit}"
        return f"source coordinate overflow ({self.kind.value})"


@dataclass(frozen=True, slots=True)
class SourceLimits:
    """Resource bounds applied while a source snapshot is constructed
    (crates/consema-document/src/source.rs:382-409; RFC 0003 §12).

    Limits apply before or during allocation; a limit failure returns no
    partial snapshot, mapping, or patch result.
    """

    max_raw_bytes: int = _DEFAULT_MAX_RAW_BYTES
    max_decoded_utf8_bytes: int = _DEFAULT_MAX_DECODED_UTF8_BYTES
    max_decoded_scalars: int = _DEFAULT_MAX_DECODED_SCALARS

    @classmethod
    def unbounded(cls) -> SourceLimits:
        """Compatibility limits for already-bounded format parsers
        (source.rs:394-399)."""
        return cls(
            max_raw_bytes=sys.maxsize,
            max_decoded_utf8_bytes=sys.maxsize,
            max_decoded_scalars=sys.maxsize,
        )


@dataclass(frozen=True, slots=True)
class _BoundaryStep:
    """Raw width and boundary exactness of one decoded scalar."""

    raw_advance: int
    exact_after: bool


@dataclass(frozen=True, slots=True)
class _DecodedIndex:
    """Boundary checkpoints at every _CHECKPOINT_STRIDE scalars plus terminal."""

    checkpoints: tuple[DecodedPosition, ...]
    terminal: DecodedPosition
    steps: tuple[_BoundaryStep, ...] | None  # per-scalar steps for variable-width sources


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Immutable ownership of exact raw bytes plus explicitly derived text facts
    (crates/consema-document/src/source.rs:477-666; RFC 0003 §3/§4/§6).

    The raw bytes, the content digest, and the encoding facts are exact and
    immutable; the decoded text is derived once at construction. Binary
    sources have no decoded text or decoded coordinate map. This payload is a
    complete immutable content fact, not a file path, URI, loader, owner,
    permission record, or live buffer (RFC 0003 §6).
    """

    raw: bytes = field(repr=False)
    _digest: ContentDigest
    encoding: EncodingFacts
    decoded: str | None = field(default=None, repr=False)
    _index: _DecodedIndex | None = field(default=None, repr=False)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_raw(
        cls, raw: bytes, request: EncodingRequest, limits: SourceLimits
    ) -> SourceSnapshot:
        """Constructs a source from raw bytes using explicit resolution inputs
        and limits (source.rs:488-550)."""
        _check_limit("raw-bytes", len(raw), limits.max_raw_bytes)
        encoding = _resolve_encoding(raw, request)
        digest = ContentDigest.of(raw)
        selected = encoding.selected
        if selected.kind is SourceEncodingKind.BINARY:
            decoded: str | None = None
            steps: tuple[_BoundaryStep, ...] | None = None
        elif selected.kind is SourceEncodingKind.UTF8:
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SourceError(
                    SourceErrorKind.INVALID_SEQUENCE,
                    encoding=selected,
                    byte_offset=error.start,
                ) from None
            steps = None
        elif selected.kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
            decoded = _decode_utf16(raw, selected, limits)
            steps = None
        elif selected.kind is SourceEncodingKind.LATIN1:
            decoded = _decode_latin1(raw, limits)
            steps = None
        else:
            decoded, steps = _decode_windows_code_page(raw, selected, limits)
        index = None
        if decoded is not None:
            index = _build_index(decoded, selected, len(raw), limits, steps)
        return cls(raw=raw, _digest=digest, encoding=encoding, decoded=decoded, _index=index)

    @classmethod
    def from_utf8(cls, raw: bytes) -> SourceSnapshot:
        """Compatibility constructor for exact UTF-8 sources (source.rs:553-568).

        Uses the caller-override form of the UTF-8 request with unbounded
        limits; an invalid UTF-8 sequence surfaces as InvalidUtf8 carrying the
        valid prefix length.
        """
        try:
            return cls.from_raw(
                raw,
                EncodingRequest.new(SourceEncoding.utf8()).with_caller_override(
                    SourceEncoding.utf8()
                ),
                SourceLimits.unbounded(),
            )
        except SourceError as error:
            if (
                error.kind is SourceErrorKind.INVALID_SEQUENCE
                and error.encoding == SourceEncoding.utf8()
            ):
                raise SourceError(
                    SourceErrorKind.INVALID_UTF8, valid_up_to=error.byte_offset
                ) from None
            raise

    @classmethod
    def from_binary(cls, raw: bytes, limits: SourceLimits) -> SourceSnapshot:
        """Constructs an opaque binary source without decoding or BOM
        interpretation (source.rs:571-576)."""
        return cls.from_raw(raw, EncodingRequest.binary(), limits)

    # -- accessors --------------------------------------------------------

    def bytes(self) -> bytes:
        """Exact retained source bytes."""
        return self.raw

    def digest(self) -> ContentDigest:
        """Stable SHA-256 identity of exact retained bytes."""
        return self._digest

    def encoding_facts(self) -> EncodingFacts:
        """Complete encoding-resolution facts."""
        return self.encoding

    def decoded_text(self) -> str | None:
        """Decoded text, or None for an opaque binary source."""
        return self.decoded

    def len(self) -> int:
        """Source byte length."""
        return len(self.raw)

    def is_empty(self) -> bool:
        """Whether the source is empty."""
        return not self.raw

    # -- decoded locations ------------------------------------------------

    def decoded_position(self, raw_byte: int) -> DecodedPosition:
        """Resolves one raw byte offset only when it is a decoded scalar
        boundary (source.rs:623-641; vector cases source.location.*,
        conformance/vectors/source-v1.json:83-100).

        The terminal raw offset (``raw_byte == len``) is the valid half-open
        end of the source and resolves to the terminal DecodedPosition, the
        same way the Rust decoder accepts ``raw_byte <= bytes.len()``
        (source.rs:624-626) and the Go decoder accepts ``rawByte <= len``
        (go/document/source.go:322-323); only offsets beyond the source are
        out of bounds. Raises LocationError(OutOfBounds) for offsets beyond
        the source, LocationError(NotDecodedBoundary) for offsets inside one
        encoded scalar, and LocationError(NoDecodedText) for binary sources
        (RFC 0003 §5: a raw offset inside a UTF-8 scalar or between a UTF-16
        surrogate pair is rejected rather than rounded).
        """
        if raw_byte > len(self.raw):
            raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)
        text = self.decoded
        if text is None:
            raise LocationError(LocationErrorKind.NO_DECODED_TEXT)
        index = self._index
        assert index is not None
        checkpoint = _last_checkpoint_at_most(
            index.checkpoints, raw_byte, key=lambda position: position.raw_byte
        )
        return _scan_to_raw(text, self.encoding.selected, index.steps, checkpoint, raw_byte)

    def raw_byte_at(self, offset: DecodedOffset) -> int:
        """Resolves one decoded offset only when it denotes a scalar boundary
        (source.rs:644-665)."""
        text = self.decoded
        if text is None:
            raise LocationError(LocationErrorKind.NO_DECODED_TEXT)
        index = self._index
        assert index is not None
        requested = offset.value
        if requested > _component(index.terminal, offset):
            raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)
        checkpoint = _last_checkpoint_at_most(
            index.checkpoints, requested, key=lambda position: _component(position, offset)
        )
        position = _scan_to_decoded(text, self.encoding.selected, index.steps, checkpoint, offset)
        return position.raw_byte


# ---------------------------------------------------------------------------
# Encoding resolution
# ---------------------------------------------------------------------------


def _resolve_encoding(raw: bytes, request: EncodingRequest) -> EncodingFacts:
    """Deterministic encoding resolution (source.rs:727-738)."""
    has_explicit_text = (request.declaration is not None and request.declaration.is_text) or (
        request.caller_override is not None and request.caller_override.is_text
    )
    interpret_bom = request.bom_policy is BomPolicy.DETECT_UNICODE and (
        request.profile_default.is_text or has_explicit_text
    )
    bom = _detect_bom(raw) if interpret_bom else None
    return _resolve_assertions(request, bom)


def _resolve_assertions(request: EncodingRequest, bom: BomKind | None) -> EncodingFacts:
    """Resolution core with the frozen priority and conflict rules
    (source.rs:740-782; RFC 0003 §4.2)."""
    if request.profile_default.kind is SourceEncodingKind.BINARY and (
        (request.declaration is not None and request.declaration.is_text)
        or (request.caller_override is not None and request.caller_override.is_text)
    ):
        raise SourceError(
            SourceErrorKind.ENCODING_CONFLICT,
            bom=bom.encoding if bom is not None else None,
            declaration=request.declaration,
            caller_override=request.caller_override,
        )
    bom_encoding = bom.encoding if bom is not None else None
    assertions = [
        value
        for value in (bom_encoding, request.declaration, request.caller_override)
        if value is not None
    ]
    if assertions and any(value != assertions[0] for value in assertions):
        raise SourceError(
            SourceErrorKind.ENCODING_CONFLICT,
            bom=bom_encoding,
            declaration=request.declaration,
            caller_override=request.caller_override,
        )
    selected = (
        request.caller_override
        or request.declaration
        or bom_encoding
        or request.profile_default
    )
    return EncodingFacts(
        profile_default=request.profile_default,
        bom_policy=request.bom_policy,
        bom=bom,
        declaration=request.declaration,
        caller_override=request.caller_override,
        selected=selected,
    )


def _detect_bom(raw: bytes) -> BomKind | None:
    """BOM detection; UTF-32 BOMs are recognized but unsupported
    (source.rs:784-804; RFC 0003 §4.2: "UTF-32 BOMs are explicitly
    unsupported in v1")."""
    if raw.startswith(b"\xff\xfe\x00\x00"):
        raise SourceError(
            SourceErrorKind.UNSUPPORTED_BOM, unsupported_bom=UnsupportedBomKind.UTF32LE
        )
    if raw.startswith(b"\x00\x00\xfe\xff"):
        raise SourceError(
            SourceErrorKind.UNSUPPORTED_BOM, unsupported_bom=UnsupportedBomKind.UTF32BE
        )
    if raw.startswith(b"\xef\xbb\xbf"):
        return BomKind.UTF8
    if raw.startswith(b"\xff\xfe"):
        return BomKind.UTF16LE
    if raw.startswith(b"\xfe\xff"):
        return BomKind.UTF16BE
    return None


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_utf16(raw: bytes, encoding: SourceEncoding, limits: SourceLimits) -> str:
    """Strict UTF-16 decode (source.rs:806-869; RFC 0003 §4.3).

    Rejects odd-length input and isolated or reversed surrogate pairs. The
    BOM code unit decodes to U+FEFF and remains part of the raw source and
    the decoded text (RFC 0003 §4.3, lines 119-122).
    """
    if len(raw) % 2 != 0:
        raise SourceError(
            SourceErrorKind.INVALID_SEQUENCE, encoding=encoding, byte_offset=len(raw) - 1
        )
    little_endian = encoding.kind is SourceEncodingKind.UTF16LE
    output: list[str] = []
    decoded_utf8_bytes = 0
    scalars = 0
    offset = 0
    while offset < len(raw):
        first = int.from_bytes(
            raw[offset : offset + 2], "little" if little_endian else "big"
        )
        if 0xD800 <= first <= 0xDBFF:
            if offset + 3 >= len(raw):
                raise SourceError(
                    SourceErrorKind.INVALID_SEQUENCE, encoding=encoding, byte_offset=offset
                )
            second = int.from_bytes(
                raw[offset + 2 : offset + 4], "little" if little_endian else "big"
            )
            if not (0xDC00 <= second <= 0xDFFF):
                raise SourceError(
                    SourceErrorKind.INVALID_SEQUENCE, encoding=encoding, byte_offset=offset
                )
            scalar = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
            consumed = 4
        elif 0xDC00 <= first <= 0xDFFF:
            raise SourceError(
                SourceErrorKind.INVALID_SEQUENCE, encoding=encoding, byte_offset=offset
            )
        else:
            scalar = first
            consumed = 2
        character = chr(scalar)
        scalars += 1
        _check_limit("decoded-scalars", scalars, limits.max_decoded_scalars)
        decoded_utf8_bytes += len(character.encode("utf-8"))
        _check_limit("decoded-utf8-bytes", decoded_utf8_bytes, limits.max_decoded_utf8_bytes)
        output.append(character)
        offset += consumed
    return "".join(output)


def _decode_latin1(raw: bytes, limits: SourceLimits) -> str:
    """ISO-8859-1 byte-to-U+0000..U+00FF decoding (source.rs:880-894).

    Latin-1 is not Windows-1252 (RFC 0003 §4.1, lines 76-77).
    """
    _check_limit("decoded-scalars", len(raw), limits.max_decoded_scalars)
    output: list[str] = []
    decoded_utf8_bytes = 0
    for byte in raw:
        character = chr(byte)
        decoded_utf8_bytes += len(character.encode("utf-8"))
        _check_limit("decoded-utf8-bytes", decoded_utf8_bytes, limits.max_decoded_utf8_bytes)
        output.append(character)
    return "".join(output)


def _decode_windows_code_page(
    raw: bytes, encoding: SourceEncoding, limits: SourceLimits
) -> tuple[str, tuple[_BoundaryStep, ...]]:
    """Strict code-page decode with exact per-scalar raw boundaries
    (source.rs:901-992).

    Bridges the frozen code-page registry to the Python stdlib incremental
    codecs (zero runtime dependencies). Malformed sequences fail with
    InvalidSequence at the first pending byte; a trailing incomplete sequence
    surfaces as the coordinate-overflow limit code (the InputEmpty-with-
    pending branch of source.rs:964-975). Byte-exactness of the DBCS pages
    against encoding_rs is a differential verification item.
    """
    assert encoding.code_page is not None
    decoder = codecs.getincrementaldecoder(encoding.code_page.codec_name)(errors="strict")
    output: list[str] = []
    steps: list[_BoundaryStep] = []
    decoded_utf8_bytes = 0
    pending = 0
    pending_start = 0
    for offset, byte in enumerate(raw):
        if pending == 0:
            pending_start = offset
        try:
            # Python 3 incremental decoders return only the decoded str, not
            # a (chunk, consumed) tuple; the consumed count (bytes absorbed
            # into the decoder's pending buffer, held or completed) must be
            # derived from getstate() instead.
            buffered = len(decoder.getstate()[0])
            chunk = decoder.decode(bytes((byte,)), final=False)
            pending += 1 + buffered - len(decoder.getstate()[0])
        except UnicodeDecodeError as error:
            raise SourceError(
                SourceErrorKind.INVALID_SEQUENCE,
                encoding=encoding,
                byte_offset=error.start,
            ) from None
        if chunk:
            decoded_utf8_bytes += _group_utf8_bytes(chunk)
            _check_limit("decoded-utf8-bytes", decoded_utf8_bytes, limits.max_decoded_utf8_bytes)
            _append_group_steps(chunk, steps, pending, limits)
            pending = 0
            output.append(chunk)
    try:
        consumed = len(decoder.getstate()[0])
        chunk = decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise SourceError(
            SourceErrorKind.INVALID_SEQUENCE, encoding=encoding, byte_offset=error.start
        ) from None
    pending += consumed
    if chunk:
        decoded_utf8_bytes += _group_utf8_bytes(chunk)
        _check_limit("decoded-utf8-bytes", decoded_utf8_bytes, limits.max_decoded_utf8_bytes)
        _append_group_steps(chunk, steps, pending, limits)
        pending = 0
        output.append(chunk)
    if pending != 0:
        raise SourceError(
            SourceErrorKind.OFFSET_OVERFLOW,
            name="pending-sequence",
            observed=pending,
            limit=0,
        )
    return "".join(output), tuple(steps)


def _group_utf8_bytes(chunk: str) -> int:
    return sum(len(character.encode("utf-8")) for character in chunk)


def _append_group_steps(
    chunk: str,
    steps: list[_BoundaryStep],
    pending: int,
    limits: SourceLimits,
) -> None:
    """One decoded scalar group from a raw-byte run: the last scalar is the
    exact boundary at the accumulated raw width (source.rs:994-1014)."""
    chars = list(chunk)
    if pending == 0:
        # A decoded scalar group with no consumed raw bytes cannot happen for
        # the frozen codec set; the Rust path reports OffsetOverflow
        # (source.rs:1005-1007).
        raise SourceError(
            SourceErrorKind.OFFSET_OVERFLOW,
            name="code-page-step",
            observed=0,
            limit=0,
        )
    for _ in range(1, len(chars)):
        steps.append(_BoundaryStep(raw_advance=0, exact_after=False))
    steps.append(_BoundaryStep(raw_advance=pending, exact_after=True))
    _check_limit("decoded-scalars", len(steps), limits.max_decoded_scalars)


# ---------------------------------------------------------------------------
# Decoded boundary index
# ---------------------------------------------------------------------------


def _build_index(
    text: str,
    encoding: SourceEncoding,
    raw_len: int,
    limits: SourceLimits,
    variable_steps: tuple[_BoundaryStep, ...] | None,
) -> _DecodedIndex:
    """Builds the checkpointed boundary index (source.rs:1016-1067)."""
    _check_limit("decoded-utf8-bytes", len(text.encode("utf-8")), limits.max_decoded_utf8_bytes)
    current = DecodedPosition(0, 0, 0, 0)
    checkpoints: list[DecodedPosition] = [current]
    if variable_steps is not None and len(variable_steps) != len(text):
        raise SourceError(SourceErrorKind.OFFSET_OVERFLOW)
    for scalar_offset, character in enumerate(text):
        step = _raw_step(encoding, variable_steps, scalar_offset, character)
        current = _advance(current, character, step.raw_advance)
        _check_limit(
            "decoded-scalars", current.unicode_scalar_offset, limits.max_decoded_scalars
        )
        if step.exact_after and current.unicode_scalar_offset % _CHECKPOINT_STRIDE == 0:
            checkpoints.append(current)
    if checkpoints[-1] != current:
        checkpoints.append(current)
    return _DecodedIndex(checkpoints=tuple(checkpoints), terminal=current, steps=variable_steps)


def _raw_step(
    encoding: SourceEncoding,
    variable_steps: tuple[_BoundaryStep, ...] | None,
    scalar_offset: int,
    character: str,
) -> _BoundaryStep:
    """Raw width of one scalar under the selected encoding (source.rs:1159-1181)."""
    if variable_steps is not None:
        return variable_steps[scalar_offset]
    kind = encoding.kind
    if kind is SourceEncodingKind.UTF8:
        width = len(character.encode("utf-8"))
    elif kind in (SourceEncodingKind.UTF16LE, SourceEncodingKind.UTF16BE):
        width = len(character.encode("utf-16-be")) // 2 * 2
    elif kind is SourceEncodingKind.LATIN1:
        width = 1
    else:
        raise AssertionError("binary sources have no decoded locations")
    return _BoundaryStep(raw_advance=width, exact_after=True)


def _advance(position: DecodedPosition, character: str, raw_width: int) -> DecodedPosition:
    """Advances one boundary by one decoded scalar (source.rs:1069-1080).

    Python integers are unbounded, so the checked-arithmetic overflow
    (SourceError::OffsetOverflow) cannot occur here; the variant remains part
    of the closed error set for the code-page degenerate cases.
    """
    return DecodedPosition(
        raw_byte=position.raw_byte + raw_width,
        decoded_utf8_byte=position.decoded_utf8_byte + len(character.encode("utf-8")),
        unicode_scalar_offset=position.unicode_scalar_offset + 1,
        utf16_code_unit_offset=position.utf16_code_unit_offset
        + len(character.encode("utf-16-be")) // 2,
    )


def _component(position: DecodedPosition, offset: DecodedOffset) -> int:
    if offset.kind is DecodedOffsetKind.UTF8_BYTE:
        return position.decoded_utf8_byte
    if offset.kind is DecodedOffsetKind.UNICODE_SCALAR:
        return position.unicode_scalar_offset
    return position.utf16_code_unit_offset


def _last_checkpoint_at_most(
    checkpoints: tuple[DecodedPosition, ...], value: int, key
) -> DecodedPosition:
    """Rightmost checkpoint whose key component is <= value (binary search)."""
    index = bisect.bisect_right(checkpoints, value, key=key)
    return checkpoints[index - 1]


def _scan_to_raw(
    text: str,
    encoding: SourceEncoding,
    variable_steps: tuple[_BoundaryStep, ...] | None,
    position: DecodedPosition,
    requested: int,
) -> DecodedPosition:
    """Scans from one checkpoint until the requested raw byte (source.rs:1090-1116)."""
    if position.raw_byte == requested:
        return position
    for scalar, character in enumerate(
        text[position.unicode_scalar_offset :], start=position.unicode_scalar_offset
    ):
        step = _raw_step(encoding, variable_steps, scalar, character)
        position = _advance(position, character, step.raw_advance)
        if step.exact_after and position.raw_byte == requested:
            return position
        if position.raw_byte > requested:
            raise LocationError(LocationErrorKind.NOT_DECODED_BOUNDARY)
    raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)


def _scan_to_decoded(
    text: str,
    encoding: SourceEncoding,
    variable_steps: tuple[_BoundaryStep, ...] | None,
    position: DecodedPosition,
    offset: DecodedOffset,
) -> DecodedPosition:
    """Scans from one checkpoint until the requested decoded coordinate
    (source.rs:1118-1150)."""
    target = offset.value
    if _component(position, offset) == target:
        return position
    for scalar, character in enumerate(
        text[position.unicode_scalar_offset :], start=position.unicode_scalar_offset
    ):
        step = _raw_step(encoding, variable_steps, scalar, character)
        position = _advance(position, character, step.raw_advance)
        observed = _component(position, offset)
        if observed == target:
            if step.exact_after:
                return position
            raise LocationError(LocationErrorKind.DECODED_OFFSET_NOT_BOUNDARY)
        if observed > target:
            raise LocationError(LocationErrorKind.DECODED_OFFSET_NOT_BOUNDARY)
    raise LocationError(LocationErrorKind.OUT_OF_BOUNDS)


def _check_limit(name: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise SourceError(
            SourceErrorKind.RESOURCE_LIMIT, name=name, observed=observed, limit=limit
        )
