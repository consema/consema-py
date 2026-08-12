"""The closed fifteen-kind PortableValue model.

Authority: RFC 0016 §4.1 and the kind registry of
crates/consema-core/src/value.rs:622-653 (PortableValueKind) — the
language-neutral list is: Null, Boolean, Integer, Decimal, BinaryFloat32,
BinaryFloat64, String, Bytes, Date, Time, LocalDateTime, OffsetDateTime,
Sequence, Object, EntryMapping. The kind *names* are the language-neutral
spellings used by the tagged JSON transport
(crates/consema-protocol/src/value_transport.rs). Decimal normalization and
temporal validation follow crates/consema-core/src/value.rs:277-292,
337-352, 420-576; go/core is a cross-reference.

Design: a single immutable class (Python has no closed interface types; a
closed *kind* is enforced by the enum and the private constructor), with
tuple payloads so values are hashable, order-preserving, and shareable.
Object entries are ordered unique-key (str, value) pairs; EntryMapping
associations are ordered arbitrary-key pairs with duplicates allowed.
"""

from __future__ import annotations

import enum
from typing import Sequence

from consema.core.errors import DuplicateKeyError, PVCEError, PVCEErrorKind


class Kind(enum.Enum):
    """The fifteen closed PortableValue kinds (RFC 0016 §4.1)."""

    NULL = "Null"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    BINARY_FLOAT32 = "BinaryFloat32"
    BINARY_FLOAT64 = "BinaryFloat64"
    STRING = "String"
    BYTES = "Bytes"
    DATE = "Date"
    TIME = "Time"
    LOCAL_DATE_TIME = "LocalDateTime"
    OFFSET_DATE_TIME = "OffsetDateTime"
    SEQUENCE = "Sequence"
    OBJECT = "Object"
    ENTRY_MAPPING = "EntryMapping"


def _leap_year(year: int) -> bool:
    # Proleptic Gregorian leap rule over the absolute magnitude of the year
    # (value.rs:433-434): year -400 is a leap year, year -100 is not.
    magnitude = abs(year)
    return magnitude % 4 == 0 and (magnitude % 100 != 0 or magnitude % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if _leap_year(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _valid_date(year: int, month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= _days_in_month(year, month)


def _decimal_digits(value: int) -> int:
    return len(str(abs(value)))


class Decimal:
    """A canonical exact finite decimal, coefficient × 10^exponent.

    The canonical form (value.rs:277-292): a zero coefficient always has
    exponent zero, and trailing decimal zeros of the coefficient are
    stripped into the exponent (10 × 10^0 → 1 × 10^1). Instances are
    immutable and hashable.
    """

    __slots__ = ("coefficient", "exponent")

    def __init__(self, coefficient: int, exponent: int):
        if coefficient == 0:
            coefficient, exponent = 0, 0
        else:
            while coefficient % 10 == 0:
                coefficient //= 10
                exponent += 1
        self.coefficient = coefficient
        self.exponent = exponent

    def is_fraction(self) -> bool:
        """True when the value is an exact decimal in [0, 1) (value.rs:337-352)."""
        if self.coefficient < 0:
            return False
        if self.coefficient == 0:
            return True
        if self.exponent >= 0:
            return False
        return _decimal_digits(self.coefficient) + self.exponent <= 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Decimal):
            return NotImplemented
        return self.coefficient == other.coefficient and self.exponent == other.exponent

    def __hash__(self) -> int:
        return hash((self.coefficient, self.exponent))

    def __repr__(self) -> str:
        return f"Decimal(coefficient={self.coefficient}, exponent={self.exponent})"


class PortableValue:
    """One immutable value of one of the fifteen closed kinds.

    Construct via the kind-named classmethods (``PortableValue.null()``,
    ``PortableValue.integer(3)``, ...) or the module-level builders below.
    Strict equality and the deterministic hash are defined in
    :mod:`consema.core.equal`; ``==`` and ``hash()`` delegate to them.
    """

    __slots__ = ("_kind", "_payload")

    def __init__(self, kind: Kind, payload: object):
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_payload", payload)

    # -- closed kind -------------------------------------------------------

    @property
    def kind(self) -> Kind:
        return self._kind

    # -- constructors ------------------------------------------------------

    @staticmethod
    def null() -> "PortableValue":
        return PortableValue(Kind.NULL, None)

    @staticmethod
    def boolean(value: bool) -> "PortableValue":
        return PortableValue(Kind.BOOLEAN, bool(value))

    @staticmethod
    def integer(value: int) -> "PortableValue":
        if not isinstance(value, int):
            raise TypeError("integer value must be an int")
        return PortableValue(Kind.INTEGER, value)

    @staticmethod
    def decimal(value: Decimal) -> "PortableValue":
        if not isinstance(value, Decimal):
            raise TypeError("decimal value must be a Decimal")
        return PortableValue(Kind.DECIMAL, value)

    @staticmethod
    def binary_float32(bits: int) -> "PortableValue":
        if not 0 <= bits <= 0xFFFFFFFF:
            raise ValueError("binary32 bits must fit 32 bits")
        return PortableValue(Kind.BINARY_FLOAT32, bits)

    @staticmethod
    def binary_float64(bits: int) -> "PortableValue":
        if not 0 <= bits <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("binary64 bits must fit 64 bits")
        return PortableValue(Kind.BINARY_FLOAT64, bits)

    @staticmethod
    def string(value: str) -> "PortableValue":
        if not isinstance(value, str):
            raise TypeError("string value must be a str")
        return PortableValue(Kind.STRING, value)

    @staticmethod
    def bytes_value(value: bytes) -> "PortableValue":
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("bytes value must be bytes")
        return PortableValue(Kind.BYTES, bytes(value))

    @staticmethod
    def date(year: int, month: int, day: int) -> "PortableValue":
        if not _valid_date(year, month, day):
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL)
        return PortableValue(Kind.DATE, (year, month, day))

    @staticmethod
    def time(hour: int, minute: int, second: int, fraction: Decimal) -> "PortableValue":
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL)
        if not isinstance(fraction, Decimal) or not fraction.is_fraction():
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL)
        return PortableValue(Kind.TIME, (hour, minute, second, fraction))

    @staticmethod
    def local_date_time(date: "PortableValue", time: "PortableValue") -> "PortableValue":
        if date.kind is not Kind.DATE or time.kind is not Kind.TIME:
            raise TypeError("local_date_time requires a Date and a Time")
        return PortableValue(Kind.LOCAL_DATE_TIME, (date, time))

    @staticmethod
    def offset_date_time(
        local: "PortableValue", offset_seconds: int
    ) -> "PortableValue":
        if local.kind is not Kind.LOCAL_DATE_TIME:
            raise TypeError("offset_date_time requires a LocalDateTime")
        if not -24 * 60 * 60 < offset_seconds < 24 * 60 * 60:
            raise PVCEError(PVCEErrorKind.INVALID_TEMPORAL)
        return PortableValue(Kind.OFFSET_DATE_TIME, (local, offset_seconds))

    @staticmethod
    def sequence(items: Sequence["PortableValue"]) -> "PortableValue":
        return PortableValue(Kind.SEQUENCE, tuple(items))

    @staticmethod
    def object(entries: Sequence[tuple[str, "PortableValue"]]) -> "PortableValue":
        """Builds an ordered object, rejecting duplicate keys at construction."""
        pairs: list[tuple[str, PortableValue]] = []
        seen: set[str] = set()
        for key, value in entries:
            if key in seen:
                raise DuplicateKeyError(key)
            seen.add(key)
            pairs.append((key, value))
        return PortableValue(Kind.OBJECT, tuple(pairs))

    @staticmethod
    def entry_mapping(
        entries: Sequence[tuple["PortableValue", "PortableValue"]]
    ) -> "PortableValue":
        # Arbitrary keys; duplicates and association order are value semantics
        # (EntryMappingBuilder::push, value.rs:973-978).
        return PortableValue(Kind.ENTRY_MAPPING, tuple(entries))

    # -- typed accessors ---------------------------------------------------

    def as_boolean(self) -> bool:
        if self._kind is not Kind.BOOLEAN:
            raise TypeError(f"value kind is {self._kind.value}, not Boolean")
        return self._payload

    def as_integer(self) -> int:
        if self._kind is not Kind.INTEGER:
            raise TypeError(f"value kind is {self._kind.value}, not Integer")
        return self._payload

    def as_decimal(self) -> Decimal:
        if self._kind is not Kind.DECIMAL:
            raise TypeError(f"value kind is {self._kind.value}, not Decimal")
        return self._payload

    def as_binary_float32(self) -> int:
        if self._kind is not Kind.BINARY_FLOAT32:
            raise TypeError(f"value kind is {self._kind.value}, not BinaryFloat32")
        return self._payload

    def as_binary_float64(self) -> int:
        if self._kind is not Kind.BINARY_FLOAT64:
            raise TypeError(f"value kind is {self._kind.value}, not BinaryFloat64")
        return self._payload

    def as_string(self) -> str:
        if self._kind is not Kind.STRING:
            raise TypeError(f"value kind is {self._kind.value}, not String")
        return self._payload

    def as_bytes(self) -> bytes:
        if self._kind is not Kind.BYTES:
            raise TypeError(f"value kind is {self._kind.value}, not Bytes")
        return self._payload

    def as_date(self) -> tuple[int, int, int]:
        if self._kind is not Kind.DATE:
            raise TypeError(f"value kind is {self._kind.value}, not Date")
        return self._payload

    def as_time(self) -> tuple[int, int, int, Decimal]:
        if self._kind is not Kind.TIME:
            raise TypeError(f"value kind is {self._kind.value}, not Time")
        return self._payload

    def as_local_date_time(self) -> tuple["PortableValue", "PortableValue"]:
        if self._kind is not Kind.LOCAL_DATE_TIME:
            raise TypeError(f"value kind is {self._kind.value}, not LocalDateTime")
        return self._payload

    def as_offset_date_time(self) -> tuple["PortableValue", int]:
        if self._kind is not Kind.OFFSET_DATE_TIME:
            raise TypeError(f"value kind is {self._kind.value}, not OffsetDateTime")
        return self._payload

    def as_sequence(self) -> tuple["PortableValue", ...]:
        if self._kind is not Kind.SEQUENCE:
            raise TypeError(f"value kind is {self._kind.value}, not Sequence")
        return self._payload

    def as_object(self) -> tuple[tuple[str, "PortableValue"], ...]:
        if self._kind is not Kind.OBJECT:
            raise TypeError(f"value kind is {self._kind.value}, not Object")
        return self._payload

    def as_entry_mapping(self) -> tuple[tuple["PortableValue", "PortableValue"], ...]:
        if self._kind is not Kind.ENTRY_MAPPING:
            raise TypeError(f"value kind is {self._kind.value}, not EntryMapping")
        return self._payload

    # -- strict equality / deterministic hash ------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PortableValue):
            return NotImplemented
        return equal(self, other)

    def __hash__(self) -> int:
        return hash_value(self)

    def __repr__(self) -> str:
        if self._kind is Kind.NULL:
            return "PortableValue.null()"
        if self._kind is Kind.OBJECT:
            items = ", ".join(f"{k!r}: {v!r}" for k, v in self._payload)
            return f"PortableValue.object({{{items}}})"
        if self._kind is Kind.SEQUENCE:
            return f"PortableValue.sequence({list(self._payload)!r})"
        return f"PortableValue.{self._kind.value.lower()}({self._payload!r})"


class ExtendedValue:
    """A formally versioned PVCE extension root (record tag 0x7f).

    Extensions remain separate from the closed core tree (lib.rs:45-52):
    they may appear only as the stream root and are never nested inside a
    PortableValue. ``encode_value``/``decode_value`` handle them; the
    core-only ``encode``/``decode`` reject them.
    """

    __slots__ = ("type_id", "semantic_version", "payload_codec_id", "canonical_payload")

    def __init__(
        self,
        type_id: str,
        semantic_version: int,
        payload_codec_id: str,
        canonical_payload: bytes,
    ):
        self.type_id = type_id
        self.semantic_version = semantic_version
        self.payload_codec_id = payload_codec_id
        self.canonical_payload = bytes(canonical_payload)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtendedValue):
            return NotImplemented
        return (
            self.type_id == other.type_id
            and self.semantic_version == other.semantic_version
            and self.payload_codec_id == other.payload_codec_id
            and self.canonical_payload == other.canonical_payload
        )

    def __hash__(self) -> int:
        return hash(
            (self.type_id, self.semantic_version, self.payload_codec_id, self.canonical_payload)
        )

    def __repr__(self) -> str:
        return (
            f"ExtendedValue(type_id={self.type_id!r}, semantic_version={self.semantic_version}, "
            f"payload_codec_id={self.payload_codec_id!r}, canonical_payload={self.canonical_payload!r})"
        )


# -- module-level builders (Go core.NewX / Rust PortableValue::x analogs) --

def decimal(coefficient: int, exponent: int) -> Decimal:
    """Builds one canonical Decimal."""
    return Decimal(coefficient, exponent)


# -- incremental builders ---------------------------------------------------


class ObjectBuilder:
    """Incrementally constructs an ordered unique-key Object.

    Insert rejects duplicate keys with :class:`DuplicateKeyError` (the
    RFC 0002 object contract; value.rs ObjectBuilder uniqueness invariant).
    """

    def __init__(self):
        self._entries: list[tuple[str, PortableValue]] = []
        self._keys: set[str] = set()

    def insert(self, key: str, value: PortableValue) -> None:
        if key in self._keys:
            raise DuplicateKeyError(key)
        self._keys.add(key)
        self._entries.append((key, value))

    def __len__(self) -> int:
        return len(self._entries)

    def build(self) -> PortableValue:
        return PortableValue(Kind.OBJECT, tuple(self._entries))


class EntryMappingBuilder:
    """Incrementally constructs an ordered arbitrary-key EntryMapping.

    No deduplication: arbitrary keys may repeat (value.rs:973-978).
    """

    def __init__(self):
        self._entries: list[tuple[PortableValue, PortableValue]] = []

    def push(self, key: PortableValue, value: PortableValue) -> None:
        self._entries.append((key, value))

    def __len__(self) -> int:
        return len(self._entries)

    def build(self) -> PortableValue:
        return PortableValue(Kind.ENTRY_MAPPING, tuple(self._entries))


# -- strict equality and hash (defined here; implemented in consema.core.equal)

def equal(left: "PortableValue", right: "PortableValue") -> bool:
    """Strict PortableValue equality (RFC 0016 §4.1)."""
    from consema.core.equal import equal as _equal

    return _equal(left, right)


def hash_value(value: "PortableValue") -> int:
    """Deterministic 64-bit hash (FNV-1a over the canonical PVCE/1 bytes)."""
    from consema.core.equal import hash_value as _hash_value

    return _hash_value(value)
