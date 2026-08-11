"""Materialization golden transcriptions for the plist family (RFC 0013
§10).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json;
each test cites the vector case id. Materialization consumes the
``plist.value-tree@1`` record shape; the golden records are transcribed
from the vector JSON spellings.

Cases covered here:

- plist-v1.json: plist.materialization.xml-canonical-text (lines 1222-
  1254), plist.materialization.binary-canonical-hex (1255-1287),
  plist.materialization.fractional-date-policy (1313-1355),
  plist.materialization.old-record-shape-rejected (1356-1378).
"""

from __future__ import annotations

import pytest

from consema.core.value import PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.materialization import (
    CompleteMaterialization,
    FailedMaterializationAttempt,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source import SourceEncoding
from consema.plist import materialize

DEFAULT_LIMITS = MaterializationLimits()


def xml_request() -> MaterializationRequest:
    return (
        MaterializationRequest.new(
            ProfileId.new("plist.xml", 1),
            MaterializationStyleId.new("plist.xml-canonical", 1),
        )
        .with_encoding(SourceEncoding.utf8())
        .with_newline(NewlinePolicy.LF)
        .with_limits(DEFAULT_LIMITS)
    )


def binary_request() -> MaterializationRequest:
    return (
        MaterializationRequest.new(
            ProfileId.new("plist.binary", 1),
            MaterializationStyleId.new("plist.binary-canonical", 1),
        )
        .with_encoding(SourceEncoding.binary())
        .with_newline(NewlinePolicy.NONE)
        .with_limits(DEFAULT_LIMITS)
    )


def value_tree_record(root: PortableValue, truncate_policy: str | None = None) -> PortableValue:
    entries = [
        ("record", PortableValue.string("plist.value-tree@1")),
        ("root", root),
    ]
    if truncate_policy is not None:
        entries.append(("truncate_policy", PortableValue.string(truncate_policy)))
    return PortableValue.object(entries)


def date_leaf(seconds: float) -> PortableValue:
    return PortableValue.object(
        (
            ("epoch", PortableValue.string("2001-01-01T00:00:00Z")),
            ("seconds", PortableValue.binary_float64(_f64_bits(seconds))),
        )
    )


def data_leaf(hex_string: str) -> PortableValue:
    return PortableValue.object((("hex", PortableValue.string(hex_string)),))


def _f64_bits(value: float) -> int:
    import struct

    return struct.unpack(">Q", struct.pack(">d", value))[0]


# ---------------------------------------------------------------------------
# plist.materialization.xml-canonical-text (plist-v1.json:1222-1254)
# ---------------------------------------------------------------------------


def test_xml_canonical_text_golden():
    # Case plist.materialization.xml-canonical-text (plist-v1.json:1223-1253).
    root = PortableValue.object(
        (
            ("name", PortableValue.string("value")),
            ("count", PortableValue.integer(42)),
            ("ratio", PortableValue.binary_float64(_f64_bits(1.5))),
            ("enabled", PortableValue.boolean(True)),
            ("disabled", PortableValue.boolean(False)),
            ("payload", data_leaf("010203")),
            ("created", date_leaf(694224000.0)),
            ("title", PortableValue.string("a & b < c")),
            ("tags", PortableValue.sequence([PortableValue.string("a"), PortableValue.string("b")])),
        )
    )
    result = materialize(value_tree_record(root), xml_request())
    assert isinstance(result, CompleteMaterialization)
    document = result.document
    expected = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "    <dict>\n"
        "        <key>name</key>\n"
        "        <string>value</string>\n"
        "        <key>count</key>\n"
        "        <integer>42</integer>\n"
        "        <key>ratio</key>\n"
        "        <real>1.5</real>\n"
        "        <key>enabled</key>\n"
        "        <true/>\n"
        "        <key>disabled</key>\n"
        "        <false/>\n"
        "        <key>payload</key>\n"
        "        <data>AQID</data>\n"
        "        <key>created</key>\n"
        "        <date>2023-01-01T00:00:00Z</date>\n"
        "        <key>title</key>\n"
        "        <string>a &amp; b &lt; c</string>\n"
        "        <key>tags</key>\n"
        "        <array>\n"
        "            <string>a</string>\n"
        "            <string>b</string>\n"
        "        </array>\n"
        "    </dict>\n"
        "</plist>\n"
    )
    assert document.render() == expected.encode("utf-8")
    # Reparse closure: the materialized document reparses Complete.
    assert document.formation_status().value == "Complete"


# ---------------------------------------------------------------------------
# plist.materialization.binary-canonical-hex (plist-v1.json:1255-1287)
# ---------------------------------------------------------------------------


def test_binary_canonical_hex_golden():
    # Case plist.materialization.binary-canonical-hex (plist-v1.json:1256-1286).
    root = PortableValue.object(
        (
            ("name", PortableValue.string("value")),
            ("count", PortableValue.integer(42)),
            ("ratio", PortableValue.binary_float64(_f64_bits(1.5))),
            ("enabled", PortableValue.boolean(True)),
            ("disabled", PortableValue.boolean(False)),
            ("payload", data_leaf("010203")),
            ("created", date_leaf(694224000.0)),
            ("title", PortableValue.string("a & b < c")),
            ("tags", PortableValue.sequence([PortableValue.string("a"), PortableValue.string("b")])),
        )
    )
    result = materialize(value_tree_record(root), binary_request())
    assert isinstance(result, CompleteMaterialization)
    document = result.document
    expected_hex = (
        "62706c6973743030d90102030405060708090a0b0c0d0e0f101112546e616d6555636f756e"
        "7455726174696f57656e61626c65645864697361626c6564577061796c6f6164576372656174"
        "6564557469746c6554746167735576616c7565102a233ff80000000000000908430102033341"
        "c4b08240000000596120262062203c2063a2131451615162081b20262c343d454d53585e6069"
        "6a6b6f7882858700000000000001010000000000000015000000000000000000000000000000"
        "89"
    )
    assert document.render().hex() == expected_hex
    assert document.formation_status().value == "Complete"


# ---------------------------------------------------------------------------
# plist.materialization.fractional-date-policy (plist-v1.json:1313-1355)
# ---------------------------------------------------------------------------


def test_fractional_date_policy():
    # Case plist.materialization.fractional-date-policy (plist-v1.json:1314-
    # 1354).
    root = PortableValue.object((("t", date_leaf(1.5)),))
    truncated = materialize(
        value_tree_record(root, "TruncateWithReport"), xml_request()
    )
    assert isinstance(truncated, CompleteMaterialization)
    assert truncated.fidelity.value == "Transformed"
    expected = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "    <dict>\n"
        "        <key>t</key>\n"
        "        <date>2001-01-01T00:00:01Z</date>\n"
        "    </dict>\n"
        "</plist>\n"
    )
    assert truncated.document.render() == expected.encode("utf-8")
    assert len(truncated.report.events) == 1
    refused = materialize(value_tree_record(root), xml_request())
    assert isinstance(refused, FailedMaterializationAttempt)
    # The plist family surfaces the plist-owned code directly
    # (go/plist/materialization.go:125-140; RFC 0013 §12).
    assert refused.failure.kind.value == "unrepresentable"
    assert refused.failure.name == "date"
    assert refused.failure.code == "plist.materialization.fractional-date@1"


# ---------------------------------------------------------------------------
# plist.materialization.old-record-shape-rejected (plist-v1.json:1356-1378)
# ---------------------------------------------------------------------------


def test_old_record_shape_rejected():
    # Case plist.materialization.old-record-shape-rejected (plist-v1.json:
    # 1357-1377): an Object carrying a `kind` member is not the
    # plist.value-tree@1 record.
    old_shape = PortableValue.object(
        (
            ("record", PortableValue.string("plist.value-tree@1")),
            ("value", PortableValue.object((("kind", PortableValue.string("string")), ("text", PortableValue.string("x"))))),
        )
    )
    result = materialize(old_shape, xml_request())
    assert isinstance(result, FailedMaterializationAttempt)
    assert result.failure.code == "core.materialization.invalid-request@1"


def test_binary_materialization_deduplicates_scalars():
    # RFC 0013 §10.2: identical scalar objects are deduplicated at first
    # occurrence; the vector normalization-and-conversion case pins
    # deduplicated_scalars: 2 (plist-v1.json:1289-1312).
    root = PortableValue.object(
        (
            ("a", PortableValue.integer(5)),
            ("b", PortableValue.integer(5)),
            ("c", PortableValue.string("x")),
        )
    )
    result = materialize(value_tree_record(root), binary_request())
    assert isinstance(result, CompleteMaterialization)
    facts = result.document.binary_facts()
    # 1 dict + 3 keys + 2 unique scalars (the duplicated integer collapses).
    assert facts.trailer.num_objects == 6
