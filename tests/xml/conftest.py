"""Shared fixtures for the xml family tests.

The vector suite ``consema.xml-1-0-safe.conformance@1``
(conformance/vectors/xml-1-0-safe-v1.json) is the language-neutral
authority: golden sources, statuses, renders, diagnostics, query matches,
projection records, materialization renders, and edit renders are
transcribed from its cases. Test ids cite the exact case ids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from consema.xml import (
    XmlEncodingSelection,
    XmlParseLimits,
    XmlProfile,
    parse,
)

VECTOR_PATH = (
    Path(__file__).resolve().parents[3]
    / "conformance"
    / "vectors"
    / "xml-1-0-safe-v1.json"
)


@pytest.fixture(scope="session")
def xml_vectors():
    """The full machine-readable suite (conformance/vectors/
    xml-1-0-safe-v1.json:1-594)."""
    with open(VECTOR_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def find_case(xml_vectors, case_id: str) -> dict:
    """One vector case by its frozen id."""
    for case in xml_vectors["cases"]:
        if case["id"] == case_id:
            return case
    raise AssertionError(f"case {case_id} not found in the xml vector suite")


def form_document(case: dict) -> object:
    """Forms the document exactly as the shared runners do
    (crates/consema-conformance/src/xml_v1.rs:133-170): the
    ``utf16le-bom`` input encoding selects a BOM-prefixed UTF-16LE source,
    and the ``amplification_ratio`` / ``max_mixed_content_items`` inputs
    override the parse limits."""
    source = case["input"]["source"]
    limits = XmlParseLimits()
    if "amplification_ratio" in case["input"]:
        limits = XmlParseLimits(
            max_entity_amplification_ratio=case["input"]["amplification_ratio"]
        )
    if "max_mixed_content_items" in case["input"]:
        limits = XmlParseLimits(
            max_mixed_content_items=case["input"]["max_mixed_content_items"]
        )
    encoding = case["input"].get("encoding")
    if encoding == "utf16le-bom":
        raw = b"\xff\xfe" + source.encode("utf-16-le")
    else:
        raw = source.encode("utf-8")
    return parse(raw, XmlProfile.SAFE_V1, XmlEncodingSelection.profile_default(), limits)
