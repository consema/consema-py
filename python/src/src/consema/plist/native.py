"""Native plist value model (RFC 0013 §6).

The value model is representation-independent: ``plist.xml@1`` and
``plist.binary@1`` share one native model, and cross-representation
conversion is exact whenever every native fact is expressible in the target
representation. The model is not a JSON Object tree and not an XML element
tree.

Authority (language-neutral first; Rust for arbitration):

- Value types and semantics: crates/consema-plist/src/native.rs:31-100
  (PLIST_EPOCH_OFFSET_UNIX / PlistStringStatus / PlistString), 135-195
  (PlistKey), 196-215 (PlistInteger), 219-293 (RealWidth / PlistReal),
  296-311 (PlistBoolean), 322-366 (PlistDate), 369-392 (PlistData),
  394-414 (PlistUid), 416-442 (PlistValueRef), 444-473 (PlistDictEntry),
  475-519 (PlistDict), 521-553 (PlistArray), 555-597 (PlistValueKind),
  599-734 (PlistValue), 736-812 (PlistArenaLimits / PlistArenaError).
- Arena and structural equality: native.rs:813-1004 (PlistDocument; the
  round-trip equality of RFC 0013 §10.3), 1006-1146
  (PlistDocumentBuilder; iterative Kahn validation).
- Freezing: RFC 0013 §6 (docs/rfcs/0013-plist-family-profiles-v1.md:
  462-510) — duplicate keys are ordered native facts, strings hold exact
  UTF-16 code units with a bounded validation result, integers are signed
  64-bit, reals keep the Float32 width fact, dates are exact double seconds
  since 2001-01-01T00:00:00Z, data is exact bytes, and UIDs are values
  whose reference meaning is never resolved.

Design: immutable frozen dataclasses with tuples, so values are hashable,
order-preserving, and shareable. The arena stores every node of one
document in binary object-table order (XML arena ordinals are close-tag
order); containers refer to children by ``PlistValueRef``, so shared
identity from the binary object table survives: one source object
referenced by several containers is one native node with multiple owners.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.plist.kinds import PlistStringStatus, RealWidth

# Seconds between the Unix epoch (1970-01-01T00:00:00Z) and the plist epoch
# (2001-01-01T00:00:00Z), the origin of every PlistDate value (RFC 0013
# §5.5; native.rs:31-35). The Unix epoch is exactly this many seconds after
# the plist epoch: 11,323 days x 86,400.
PLIST_EPOCH_OFFSET_UNIX = 978_307_200.0


def classify_string(units: tuple[int, ...]) -> PlistStringStatus:
    """Exact surrogate pairing status (native.rs:1154-1170)."""
    from consema.plist.kinds import PlistStringStatus

    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 < len(units) and 0xDC00 <= units[index + 1] <= 0xDFFF:
                index += 2
                continue
            return PlistStringStatus.UNPAIRED_SURROGATE
        if 0xDC00 <= unit <= 0xDFFF:
            return PlistStringStatus.UNPAIRED_SURROGATE
        index += 1
    return PlistStringStatus.WELL_FORMED_UNICODE


class PlistStringConversionError(Exception):
    """An exact plist string cannot enter a Unicode-only host string
    (native.rs:118-128)."""


@dataclass(frozen=True, slots=True)
class PlistString:
    """Exact plist string content as immutable UTF-16 code units
    (native.rs:46-115; RFC 0013 §6).

    A string holds exact UTF-16 code units with a bounded validation result
    (``WellFormedUnicode | UnpairedSurrogate``) following the
    ``core.java-utf16-string@1`` wire pattern. XML sources can only produce
    well-formed Unicode; binary sources may produce unpaired surrogates,
    which are preserved exactly and block conversion to XML and to ordinary
    Unicode projection (RFC 0013 §5.6, §7).
    """

    code_units: tuple[int, ...]
    _status: PlistStringStatus | None = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        from consema.plist.kinds import PlistStringStatus

        units = tuple(self.code_units)
        object.__setattr__(self, "code_units", units)
        object.__setattr__(
            self, "_status", classify_string(units)
        )

    @classmethod
    def from_unicode(cls, value: str) -> PlistString:
        """Converts one valid Unicode scalar string to its exact UTF-16
        units (native.rs:71-74)."""
        return cls(_utf16_units(value))

    def status(self) -> PlistStringStatus:
        """Exact surrogate pairing status."""
        from consema.plist.kinds import PlistStringStatus

        status = self._status
        assert status is not None
        return status

    def utf16be_bytes(self) -> bytes:
        """Canonical BOM-free big-endian UTF-16BE bytes (native.rs:83-87)."""
        return b"".join(unit.to_bytes(2, "big") for unit in self.code_units)

    def to_unicode(self) -> str:
        """Converts only well-formed content to a Unicode string
        (native.rs:97-101)."""
        if not _well_formed(self.code_units):
            raise PlistStringConversionError()
        # chr() round trip: surrogate pairs encode back to the astral
        # scalars through the surrogatepass codec.
        return "".join(chr(unit) for unit in self.code_units).encode(
            "utf-16", "surrogatepass"
        ).decode("utf-16")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlistString):
            return NotImplemented
        return self.code_units == other.code_units

    def __hash__(self) -> int:
        return hash(self.code_units)

    def __repr__(self) -> str:
        try:
            text = self.to_unicode()
        except PlistStringConversionError:
            text = f"<{len(self.code_units)} code units, unpaired>"
        return f"PlistString({text!r})"


def _utf16_units(value: str) -> tuple[int, ...]:
    """Exact UTF-16 code units of one Unicode string (native.rs:72-74)."""
    encoded = value.encode("utf-16-le")
    return tuple(
        encoded[index] | (encoded[index + 1] << 8) for index in range(0, len(encoded), 2)
    )


def _well_formed(units: tuple[int, ...]) -> bool:
    from consema.plist.kinds import PlistStringStatus

    return classify_string(units) is PlistStringStatus.WELL_FORMED_UNICODE


@dataclass(frozen=True, slots=True)
class PlistKey:
    """String key identity of one dictionary association (native.rs:135-194).

    Keys are strings in both profiles (RFC 0013 §4.4, §5.9); each physical
    association keeps its own key identity, and duplicate keys are preserved
    as ordered native facts rather than collapsed.
    """

    string: PlistString

    @classmethod
    def from_unicode(cls, value: str) -> PlistKey:
        return cls(PlistString.from_unicode(value))

    def code_units(self) -> tuple[int, ...]:
        return self.string.code_units

    def status(self) -> PlistStringStatus:
        return self.string.status()

    def to_unicode(self) -> str:
        return self.string.to_unicode()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlistKey):
            return NotImplemented
        return self.string == other.string

    def __hash__(self) -> int:
        return hash(self.string)


@dataclass(frozen=True, slots=True)
class PlistInteger:
    """Exact signed 64-bit plist integer (native.rs:196-215).

    Both profiles freeze the signed 64-bit range (RFC 0013 §4.5, §5.3, §6);
    wider source inputs are Recovered rather than widening this type.
    """

    value: int

    def __post_init__(self) -> None:
        if not -(1 << 63) <= self.value <= (1 << 63) - 1:
            raise ValueError("plist integer must fit signed 64 bits")


@dataclass(frozen=True, slots=True)
class PlistReal:
    """Exact IEEE 754 real with its source width fact (native.rs:226-293).

    The value is the exact bit pattern of the source width; NaN and the
    infinities are admitted values (RFC 0013 §4.6, §5.5). Equality and
    hashing follow the bit pattern, so distinct NaN payloads and signed zero
    are distinct values.
    """

    bits: int
    width: RealWidth

    @classmethod
    def double(cls, value: float) -> PlistReal:
        """Creates a Float64 real from an exact double (native.rs:241-247)."""
        return cls(_f64_bits(value), RealWidth.FLOAT64)

    @classmethod
    def single(cls, value: float) -> PlistReal:
        """Creates a Float32 real from an exact single (native.rs:250-256)."""
        return cls(_f32_bits(value), RealWidth.FLOAT32)

    @classmethod
    def from_bits(cls, width: RealWidth, bits: int) -> PlistReal:
        """Creates a real from the exact source-width bit pattern; for
        Float32 only the low 32 bits are retained (native.rs:262-271)."""
        if width is RealWidth.FLOAT32:
            bits = bits & 0xFFFF_FFFF
        return cls(bits, width)

    def as_f64(self) -> float:
        """Exact double-converted value (RFC 0013 §5.5; native.rs:286-292)."""
        if self.width is RealWidth.FLOAT64:
            return _f64_from_bits(self.bits)
        return _f32_from_bits(self.bits)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlistReal):
            return NotImplemented
        return self.bits == other.bits and self.width == other.width

    def __hash__(self) -> int:
        return hash((self.bits, self.width))


@dataclass(frozen=True, slots=True)
class PlistBoolean:
    """One plist boolean value (``<true/>``/``<false/>``, markers
    ``0x09``/``0x08``; native.rs:296-311)."""

    value: bool


@dataclass(frozen=True, slots=True)
class PlistDate:
    """Exact double seconds since the plist epoch (native.rs:321-366).

    Construction rejects non-finite payloads: ``plist.binary@1`` marks them
    Recovered (RFC 0013 §5.5) and XML calendar validation always yields a
    finite value. Equality is bit-exact, so signed zero is distinct from
    zero.
    """

    seconds: float

    @classmethod
    def from_seconds(cls, seconds: float) -> PlistDate:
        if not _finite(seconds):
            raise PlistDateError()
        return cls(seconds)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlistDate):
            return NotImplemented
        return _f64_bits(self.seconds) == _f64_bits(other.seconds)

    def __hash__(self) -> int:
        return hash(_f64_bits(self.seconds))


class PlistDateError(Exception):
    """A plist date value must be a finite double (native.rs:357-367)."""


@dataclass(frozen=True, slots=True)
class PlistData:
    """Exact plist data bytes (native.rs:369-392).

    Data is exact bytes in the native layer; base64 exists only as
    ``plist.xml@1`` representation text (RFC 0013 §6).
    """

    bytes: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "bytes", bytes(self.bytes))


@dataclass(frozen=True, slots=True)
class PlistUid:
    """Unsigned 32-bit UID value (binary profile only; native.rs:394-414).

    A UID is a value whose reference meaning belongs to an application layer
    such as NSKeyedArchiver; Consema preserves the value but never resolves
    it to an object, class name, or archive entry (RFC 0013 §5.8, §6).
    """

    value: int

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 0xFFFF_FFFF:
            raise ValueError("plist UID must fit unsigned 32 bits")


@dataclass(frozen=True, slots=True)
class PlistValueRef:
    """Arena reference to one native value node (native.rs:416-442).

    The same source object referenced several times is the same reference
    (shared identity); the arena is bound to one document snapshot at the
    document layer.
    """

    index: int


@dataclass(frozen=True, slots=True)
class PlistDictEntry:
    """One ordered dictionary association: key identity and value reference
    (native.rs:444-473)."""

    key: PlistKey
    value: PlistValueRef


@dataclass(frozen=True, slots=True)
class PlistDict:
    """Ordered plist dictionary value (native.rs:475-519).

    A dictionary preserves physical key/value association order and
    duplicate occurrences; there is no implicit first-wins or last-wins
    lookup (RFC 0013 §6).
    """

    entries: tuple[PlistDictEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))

    def positions_of_key(self, key: PlistKey) -> tuple[int, ...]:
        """Source-ordered positions of every association whose key equals
        ``key`` (native.rs:512-518)."""
        return tuple(
            position
            for position, entry in enumerate(self.entries)
            if entry.key == key
        )


@dataclass(frozen=True, slots=True)
class PlistArray:
    """Ordered plist array value (native.rs:521-553)."""

    elements: tuple[PlistValueRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "elements", tuple(self.elements))


class PlistValueKind(enum.Enum):
    """Closed native plist value kind (native.rs:555-597).

    The kind set is closed by RFC 0013 §6: both profiles share exactly
    these nine kinds. ``Uid`` values are binary-only.
    """

    DICT = "dict"
    ARRAY = "array"
    STRING = "string"
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "boolean"
    DATE = "date"
    DATA = "data"
    UID = "uid"

    def as_str(self) -> str:
        """Stable query/protocol name (native.rs:581-596)."""
        return self.value


@dataclass(frozen=True, slots=True)
class PlistValue:
    """One native plist value node (native.rs:599-734).

    The variant set is closed by RFC 0013 §6. ``Uid`` values are binary-only
    and are never reachable from an XML document.
    """

    kind: PlistValueKind
    payload: object

    @classmethod
    def dict(cls, dict_value: PlistDict) -> PlistValue:
        return cls(PlistValueKind.DICT, dict_value)

    @classmethod
    def array(cls, array_value: PlistArray) -> PlistValue:
        return cls(PlistValueKind.ARRAY, array_value)

    @classmethod
    def string(cls, string_value: PlistString) -> PlistValue:
        return cls(PlistValueKind.STRING, string_value)

    @classmethod
    def integer(cls, integer_value: PlistInteger) -> PlistValue:
        return cls(PlistValueKind.INTEGER, integer_value)

    @classmethod
    def real(cls, real_value: PlistReal) -> PlistValue:
        return cls(PlistValueKind.REAL, real_value)

    @classmethod
    def boolean(cls, boolean_value: PlistBoolean) -> PlistValue:
        return cls(PlistValueKind.BOOLEAN, boolean_value)

    @classmethod
    def date(cls, date_value: PlistDate) -> PlistValue:
        return cls(PlistValueKind.DATE, date_value)

    @classmethod
    def data(cls, data_value: PlistData) -> PlistValue:
        return cls(PlistValueKind.DATA, data_value)

    @classmethod
    def uid(cls, uid_value: PlistUid) -> PlistValue:
        return cls(PlistValueKind.UID, uid_value)

    def as_dict(self) -> PlistDict | None:
        return self.payload if self.kind is PlistValueKind.DICT else None

    def as_array(self) -> PlistArray | None:
        return self.payload if self.kind is PlistValueKind.ARRAY else None

    def as_string(self) -> PlistString | None:
        return self.payload if self.kind is PlistValueKind.STRING else None

    def as_integer(self) -> PlistInteger | None:
        return self.payload if self.kind is PlistValueKind.INTEGER else None

    def as_real(self) -> PlistReal | None:
        return self.payload if self.kind is PlistValueKind.REAL else None

    def as_boolean(self) -> PlistBoolean | None:
        return self.payload if self.kind is PlistValueKind.BOOLEAN else None

    def as_date(self) -> PlistDate | None:
        return self.payload if self.kind is PlistValueKind.DATE else None

    def as_data(self) -> PlistData | None:
        return self.payload if self.kind is PlistValueKind.DATA else None

    def as_uid(self) -> PlistUid | None:
        return self.payload if self.kind is PlistValueKind.UID else None

    def references(self) -> tuple[PlistValueRef, ...]:
        """Ordered direct child references: dictionary values, then array
        elements (native.rs:727-733)."""
        if self.kind is PlistValueKind.DICT:
            return tuple(entry.value for entry in self.payload.entries)
        if self.kind is PlistValueKind.ARRAY:
            return self.payload.elements
        return ()


class PlistArenaErrorKind(enum.Enum):
    """Native arena validation failures (native.rs:756-782)."""

    OBJECT_LIMIT_EXCEEDED = "ObjectLimitExceeded"
    REFERENCE_OUT_OF_BOUNDS = "ReferenceOutOfBounds"
    CYCLE_DETECTED = "CycleDetected"
    CONTAINER_DEPTH_LIMIT_EXCEEDED = "ContainerDepthLimitExceeded"


class PlistArenaError(Exception):
    """Native arena validation failure (native.rs:756-782)."""

    def __init__(
        self,
        kind: PlistArenaErrorKind,
        *,
        limit: int | None = None,
        reference: PlistValueRef | None = None,
        node_count: int | None = None,
        node: PlistValueRef | None = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.limit = limit
        self.reference = reference
        self.node_count = node_count
        self.node = node


@dataclass(frozen=True, slots=True)
class PlistArenaLimits:
    """Resource bounds for one native arena (native.rs:736-752)."""

    max_objects: int = 1_000_000
    max_container_depth: int = 256


class PlistDocument:
    """Immutable native plist value arena (native.rs:813-864).

    The arena owns every native value node of one document (in binary
    object-table order) and the root reference. Nodes may be referenced by
    several containers, preserving shared identity from the binary object
    table. Objects not reachable from the root may exist in the arena
    (binary object-table orphans); they remain structural facts of the
    binary representation and are excluded from structural equality.
    """

    __slots__ = ("_nodes", "_root", "_limits")

    def __init__(
        self, nodes: tuple[PlistValue, ...], root: PlistValueRef, limits: PlistArenaLimits
    ) -> None:
        self._nodes = tuple(nodes)
        self._root = root
        self._limits = limits

    def root(self) -> PlistValueRef:
        """Root value reference (native.rs:836-839)."""
        return self._root

    def root_value(self) -> PlistValue:
        """Root value; always in bounds because build validated the arena."""
        return self._nodes[self._root.index]

    def get(self, reference: PlistValueRef) -> PlistValue | None:
        """Resolves one reference within this arena (native.rs:848-852)."""
        if 0 <= reference.index < len(self._nodes):
            return self._nodes[reference.index]
        return None

    def node_count(self) -> int:
        """Number of nodes in the arena, including unreachable objects."""
        return len(self._nodes)

    def arena_limits(self) -> PlistArenaLimits:
        return self._limits

    def __eq__(self, other: object) -> bool:
        """Structural equality of the reachable value graphs
        (native.rs:866-947): content-based, ignoring sharing patterns,
        arena indices, and unreachable objects. This is the equality the
        reparse closure and round trips use (RFC 0013 §7, §10.3)."""
        if not isinstance(other, PlistDocument):
            return NotImplemented
        if self._root == other._root and self._nodes == other._nodes:
            return True
        memo: set[tuple[int, int]] = set()
        stack = [(self._root.index, other._root.index)]
        while stack:
            left, right = stack.pop()
            if (left, right) in memo:
                continue
            memo.add((left, right))
            left_value = self._nodes[left]
            right_value = other._nodes[right]
            if left_value.kind is not right_value.kind:
                return False
            if left_value.kind is PlistValueKind.DICT:
                left_dict = left_value.payload
                right_dict = right_value.payload
                if len(left_dict.entries) != len(right_dict.entries):
                    return False
                for left_entry, right_entry in zip(left_dict.entries, right_dict.entries):
                    if left_entry.key != right_entry.key:
                        return False
                    stack.append((left_entry.value.index, right_entry.value.index))
            elif left_value.kind is PlistValueKind.ARRAY:
                left_array = left_value.payload
                right_array = right_value.payload
                if len(left_array.elements) != len(right_array.elements):
                    return False
                stack.extend(
                    (left_ref.index, right_ref.index)
                    for left_ref, right_ref in zip(left_array.elements, right_array.elements)
                )
            elif left_value.payload != right_value.payload:
                return False
        return True

    def __hash__(self) -> int:
        """Content-based structural hash; shared nodes are hashed per
        occurrence (native.rs:951-1004)."""
        value = 0
        stack = [self._root.index]
        while stack:
            index = stack.pop()
            node = self._nodes[index]
            value = (value * 31 + hash(node.kind)) & 0xFFFFFFFFFFFFFFFF
            if node.kind is PlistValueKind.DICT:
                dict_value = node.payload
                value = (value * 31 + len(dict_value.entries)) & 0xFFFFFFFFFFFFFFFF
                for entry in dict_value.entries:
                    value = (value * 31 + hash(entry.key)) & 0xFFFFFFFFFFFFFFFF
                    stack.append(entry.value.index)
            elif node.kind is PlistValueKind.ARRAY:
                array_value = node.payload
                value = (value * 31 + len(array_value.elements)) & 0xFFFFFFFFFFFFFFFF
                stack.extend(ref.index for ref in array_value.elements)
            else:
                value = (value * 31 + hash(node.payload)) & 0xFFFFFFFFFFFFFFFF
        return value

    def __repr__(self) -> str:
        return f"PlistDocument({self._nodes!r}, root={self._root!r})"


class PlistDocumentBuilder:
    """Builds one immutable PlistDocument arena (native.rs:1014-1146).

    Nodes are added in object-table order so arena indices equal object
    indices; the same source object is added once and referenced many times,
    which yields shared identity. References may point forward.
    ``build`` validates the complete arena: reference bounds, acyclicity,
    and the container depth limit.
    """

    __slots__ = ("_nodes", "_limits")

    def __init__(self, limits: PlistArenaLimits | None = None) -> None:
        self._nodes: list[PlistValue] = []
        self._limits = limits if limits is not None else PlistArenaLimits()

    def node_count(self) -> int:
        return len(self._nodes)

    def add(self, value: PlistValue) -> PlistValueRef:
        """Adds one node and returns its arena reference
        (native.rs:1048-1057)."""
        if len(self._nodes) >= self._limits.max_objects:
            raise PlistArenaError(
                PlistArenaErrorKind.OBJECT_LIMIT_EXCEEDED, limit=self._limits.max_objects
            )
        self._nodes.append(value)
        return PlistValueRef(len(self._nodes) - 1)

    def build(self, root: PlistValueRef) -> PlistDocument:
        """Validates the arena and freezes it into one immutable document
        (native.rs:1065-1145). The root must be in bounds, every reference
        must index an existing node, the reference graph must be acyclic,
        and no container may be nested deeper than ``max_container_depth``.
        Validation is iterative (Kahn's algorithm plus a reversed
        topological depth pass)."""
        node_count = len(self._nodes)
        if not 0 <= root.index < node_count:
            raise PlistArenaError(
                PlistArenaErrorKind.REFERENCE_OUT_OF_BOUNDS,
                reference=root,
                node_count=node_count,
            )
        indegree = [0] * node_count
        for node in self._nodes:
            for reference in node.references():
                if not 0 <= reference.index < node_count:
                    raise PlistArenaError(
                        PlistArenaErrorKind.REFERENCE_OUT_OF_BOUNDS,
                        reference=reference,
                        node_count=node_count,
                    )
                indegree[reference.index] += 1
        queue = [index for index, degree in enumerate(indegree) if degree == 0]
        order: list[int] = []
        while queue:
            index = queue.pop(0)
            order.append(index)
            for reference in self._nodes[index].references():
                indegree[reference.index] -= 1
                if indegree[reference.index] == 0:
                    queue.append(reference.index)
        if len(order) != node_count:
            processed = set(order)
            node = 0
            while node < node_count and node in processed:
                node += 1
            raise PlistArenaError(
                PlistArenaErrorKind.CYCLE_DETECTED, node=PlistValueRef(node)
            )
        depth = [0] * node_count
        for index in reversed(order):
            node = self._nodes[index]
            if node.kind in (PlistValueKind.DICT, PlistValueKind.ARRAY):
                child_depth = max(
                    (depth[reference.index] for reference in node.references()),
                    default=0,
                )
                container_depth = child_depth + 1
                if container_depth > self._limits.max_container_depth:
                    raise PlistArenaError(
                        PlistArenaErrorKind.CONTAINER_DEPTH_LIMIT_EXCEEDED,
                        node=PlistValueRef(index),
                        limit=self._limits.max_container_depth,
                    )
                depth[index] = container_depth
        return PlistDocument(tuple(self._nodes), root, self._limits)


# -- bit helpers (Python floats are IEEE 754 doubles) -------------------------


def _f64_bits(value: float) -> int:
    import struct

    return struct.unpack(">Q", struct.pack(">d", value))[0]


def _f64_from_bits(bits: int) -> float:
    import struct

    return struct.unpack(">d", struct.pack(">Q", bits & 0xFFFFFFFFFFFFFFFF))[0]


def _f32_bits(value: float) -> int:
    import struct

    return struct.unpack(">I", struct.pack(">f", value))[0]


def _f32_from_bits(bits: int) -> float:
    import struct

    return struct.unpack(">f", struct.pack(">I", bits & 0xFFFFFFFF))[0]


def _finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")
