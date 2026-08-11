"""The exact Java UTF-16 string value and its wire identity.

Java ``String`` is an ordered sequence of UTF-16 code units, not a guarantee
of well-formed Unicode scalar values. Properties escape processing can
produce an unpaired surrogate such as ``\\uD800``; rejecting it during a
valid parse or replacing it with U+FFFD would be silent corruption
(RFC 0010 §4, docs/rfcs/0010-java-properties-profiles-v1.md:108-131).

Authority:

- RFC 0010 §4 (docs/rfcs/0010-...:108-131) — immutable code-unit sequence,
  strict equality/hash over exact code units, bounded validation result
  ``WellFormedUnicode | UnpairedSurrogate``, conversion to a Unicode String
  only when well formed, and canonical BOM-free big-endian ``UTF16BE/1``
  bytes (an even-length byte sequence containing each code unit in
  big-endian order; no BOM, no normalization).
- crates/consema-properties/src/lib.rs:124-206 — arbitration:
  ``JavaStringStatus`` (lib.rs:125-131), ``JavaString`` (lib.rs:134-194,
  equality lib.rs:182-194), ``JavaStringConversionError`` (lib.rs:197-206),
  and the surrogate-pair walk ``classify_java_string`` (lib.rs:814-830).
- Error codes: ``java-properties.java-string.invalid-wire@1`` and
  ``java-properties.java-string.non-canonical-wire@1``
  (crates/consema-protocol/src/error_registry.rs:1111,1117) belong to the
  protocol-layer wire payloads, not to this in-SDK value type.

Design: the value stores an immutable tuple of code units (Python ints in
``0..=0xFFFF``); equality and hashing are over exact code units. The
well-formedness status is computed once at construction with the same
high/low surrogate pairing walk as the Rust classification. ``to_unicode``
raises ``JavaStringConversionError`` for unpaired content; a Unicode
scalar string converts through its exact UTF-16 encoding.
"""

from __future__ import annotations

import enum
from typing import Iterable


class JavaStringStatus(enum.Enum):
    """Whether exact Java UTF-16 units form Unicode scalar text
    (lib.rs:125-131)."""

    WELL_FORMED_UNICODE = "WellFormedUnicode"
    UNPAIRED_SURROGATE = "UnpairedSurrogate"


class JavaStringConversionError(Exception):
    """An exact Java string cannot enter a Unicode-only host string
    (lib.rs:197-206)."""

    def __str__(self) -> str:
        return "Java UTF-16 string contains an unpaired surrogate"


def _classify(units: tuple[int, ...]) -> JavaStringStatus:
    """Walks the exact code-unit sequence pairing adjacent high/low
    surrogates (lib.rs:814-830)."""
    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 < len(units) and 0xDC00 <= units[index + 1] <= 0xDFFF:
                index += 2
                continue
            return JavaStringStatus.UNPAIRED_SURROGATE
        if 0xDC00 <= unit <= 0xDFFF:
            return JavaStringStatus.UNPAIRED_SURROGATE
        index += 1
    return JavaStringStatus.WELL_FORMED_UNICODE


class JavaString:
    """Exact Java string content as immutable UTF-16 code units
    (RFC 0010 §4; lib.rs:134-194).

    Instances are immutable and hashable; equality is over exact code
    units. An unpaired surrogate is valid native content; it blocks
    ``to_unicode`` and ordinary PortableValue String projection
    (RFC 0010 §7, docs/rfcs/0010-...:199-206).
    """

    __slots__ = ("_units", "_status")

    def __init__(self, units: tuple[int, ...]) -> None:
        object.__setattr__(self, "_units", units)
        object.__setattr__(self, "_status", _classify(units))

    @classmethod
    def from_code_units(cls, units: Iterable[int]) -> JavaString:
        """Creates exact Java content and computes surrogate
        well-formedness (lib.rs:143-147)."""
        return cls(tuple(int(unit) for unit in units))

    @classmethod
    def from_unicode(cls, value: str) -> JavaString:
        """Converts one valid Unicode scalar string to its exact UTF-16
        units (lib.rs:151-153)."""
        encoded = value.encode("utf-16-be")
        return cls(
            tuple(
                int.from_bytes(encoded[index : index + 2], "big")
                for index in range(0, len(encoded), 2)
            )
        )

    @property
    def code_units(self) -> tuple[int, ...]:
        """Exact ordered Java UTF-16 code units (lib.rs:156-159)."""
        return self._units

    def code_units(self) -> tuple[int, ...]:
        """Exact ordered Java UTF-16 code units (lib.rs:156-159)."""
        return self._units

    def utf16be_bytes(self) -> bytes:
        """Canonical BOM-free big-endian ``UTF16BE/1`` bytes
        (lib.rs:162-168; RFC 0010 §4)."""
        return b"".join(unit.to_bytes(2, "big") for unit in self._units)

    def status(self) -> JavaStringStatus:
        """Exact surrogate pairing status (lib.rs:172-174)."""
        return self._status

    def is_well_formed(self) -> bool:
        """Whether every surrogate participates in one adjacent pair."""
        return self._status is JavaStringStatus.WELL_FORMED_UNICODE

    def to_unicode(self) -> str:
        """Converts only well-formed Java content to a Python Unicode
        string (lib.rs:177-179)."""
        try:
            return b"".join(
                unit.to_bytes(2, "big") for unit in self._units
            ).decode("utf-16-be")
        except UnicodeDecodeError:
            raise JavaStringConversionError() from None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, JavaString):
            return NotImplemented
        return self._units == other._units

    def __hash__(self) -> int:
        return hash(self._units)

    def __repr__(self) -> str:
        hex_units = "".join(f"{unit:04X}" for unit in self._units)
        return f"JavaString(code_units={hex_units!r}, status={self._status.value})"
