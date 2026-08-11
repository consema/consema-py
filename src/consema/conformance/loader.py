"""Vector loading and aggregate digest verification.

The vector files are plain strict JSON documents (never
``core.portable-value-json@1`` transport envelopes; conformance/README.md
rule 3); the loader converts them into the core value model with the Go
runner's exact conventions (go/conformance/conformance.go:490-577):

- object member keys are sorted lexicographically at load time;
- exact-integer number spellings become ``Integer``; non-integral
  spellings become the exact canonical ``Decimal``;
- duplicate object keys are tolerated (last wins), mirroring Go's
  encoding/json behavior (the ini-v1 vector relies on this);
- trailing content and non-finite constants are rejected.

The aggregate digest algorithm is frozen at
fc-manifest-0.13.0.json:40 (and mirrored by the Go runner at
conformance.go:437-484): file-name byte-order sort, per-file sha256
lowercase hex, lines ``{basename}:{digest}`` joined with ``\\n`` and no
trailing newline, then sha256 of that UTF-8 string.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from consema.core.value import Decimal as ConsemaDecimal
from consema.core.value import PortableValue

# A number spelling captured exactly; converted by the tree walker.
_INTEGER_SPELLING = re.compile(r"^-?\d+$")


class _NumberToken:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def _parse_float_hook(text: str) -> _NumberToken:
    return _NumberToken(text)


def _parse_constant_hook(text: str) -> None:
    raise ValueError(f"non-finite constant {text!r} is not strict JSON")


def _decimal_from_text(text: str) -> ConsemaDecimal:
    """Exact canonical Decimal from a JSON number spelling: coefficient x
    10^exponent, canonicalized by the value model."""
    exponent_text = ""
    coefficient_text = text
    if "e" in text or "E" in text:
        for marker in ("e", "E"):
            if marker in text:
                coefficient_text, exponent_text = text.split(marker, 1)
                break
    scale = 0
    if "." in coefficient_text:
        integer_part, fraction_part = coefficient_text.split(".", 1)
        coefficient_text = integer_part + fraction_part
        scale = -len(fraction_part)
    coefficient = int(coefficient_text)
    exponent = int(exponent_text) + scale if exponent_text else scale
    return ConsemaDecimal(coefficient, exponent)


def _convert_value(raw: Any) -> PortableValue:
    if raw is None:
        return PortableValue.null()
    if isinstance(raw, bool):
        return PortableValue.boolean(raw)
    if isinstance(raw, str):
        return PortableValue.string(raw)
    if isinstance(raw, int):
        return PortableValue.integer(raw)
    if isinstance(raw, _NumberToken):
        if _INTEGER_SPELLING.match(raw.text):
            return PortableValue.integer(int(raw.text))
        return PortableValue.decimal(_decimal_from_text(raw.text))
    if isinstance(raw, list):
        return PortableValue.sequence(tuple(_convert_value(item) for item in raw))
    if isinstance(raw, dict):
        # Keys were already sorted by object_pairs_hook.
        return PortableValue.object(
            tuple((key, _convert_value(value)) for key, value in raw.items())
        )
    raise ValueError(f"unsupported JSON value {raw!r}")


def parse_vector_json(data: bytes) -> PortableValue:
    """Parses one vector file as strict JSON into the core value model."""
    decoder = json.JSONDecoder(
        object_pairs_hook=_sorted_pairs_hook,
        parse_float=_parse_float_hook,
        parse_constant=_parse_constant_hook,
    )
    text = data.decode("utf-8")
    try:
        raw, end = decoder.raw_decode(text)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"vector file is not strict JSON: {error}") from error
    if text[end:].strip():
        raise ValueError("trailing content after the root document")
    return _convert_value(raw)


def _sorted_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Last-wins duplicate tolerance plus deterministic key order."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        result[key] = value
    return dict(sorted(result.items()))


def load_vector_root(data: bytes) -> PortableValue:
    """Loads one vector file and returns its root Object value."""
    root = parse_vector_json(data)
    if root.kind.value != "Object":
        raise ValueError("vector root must be an Object")
    return root


def count_cases(data: bytes) -> int:
    """Counts the ``cases`` array of one vector file."""
    root = load_vector_root(data)
    cases = _object_field(root, "cases")
    if cases is None or cases.kind.value != "Sequence":
        raise ValueError("cases field must be a Sequence")
    return len(cases.as_sequence())


def _object_field(value: PortableValue, name: str) -> PortableValue | None:
    if value.kind.value != "Object":
        return None
    for key, item in value.as_object():
        if key == name:
            return item
    return None


def read_vector_file(vectors_dir: str, name: str) -> bytes:
    """Reads one vector file as its canonical LF bytes.

    The frozen aggregate digest is defined over the canonical checkout
    bytes (``.gitattributes`` eol=lf, fc-manifest-0.13.0.json:40); a CRLF
    working tree (core.autocrlf=true) produces different per-file digests
    for the affected files, which the manifest documents as expected. The
    runner therefore normalizes CRLF line endings to LF before both hashing
    and parsing (the affected vectors carry the CR bytes only as formatting
    whitespace, never inside string content).
    """
    with open(os.path.join(vectors_dir, name), "rb") as handle:
        return handle.read().replace(b"\r\n", b"\n")


def compute_vectors_digest(vectors_dir: str) -> tuple[str, int, int]:
    """Computes the aggregate sha256 of the vector files plus the inventory
    (file count and total case count)."""
    names = sorted(
        name
        for name in os.listdir(vectors_dir)
        if name.endswith(".json") and os.path.isfile(os.path.join(vectors_dir, name))
    )
    lines: list[str] = []
    total_cases = 0
    for name in names:
        data = read_vector_file(vectors_dir, name)
        total_cases += count_cases(data)
        lines.append(f"{name}:{hashlib.sha256(data).hexdigest()}")
    aggregate = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return aggregate, len(names), total_cases
