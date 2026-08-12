"""Projection golden transcriptions for the plist family (RFC 0013 §9).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json;
each test cites the vector case id.

Cases covered here:

- plist-v1.json: plist.projection.value-tree-record (lines 1090-1148),
  plist.projection.require-object-policies (1149-1199),
  plist.projection.atomic-failures (1200-1221).
"""

from __future__ import annotations

import pytest

from consema.plist import (
    CollisionPolicy,
    PlistEncodingSelection,
    PlistParseLimits,
    PlistProfile,
    ProjectionRequest,
    ProjectionTarget,
    UidPolicy,
    parse,
    project,
)

DEFAULT_LIMITS = PlistParseLimits()


def xml_document(source: str):
    return parse(
        source.encode("utf-8"),
        PlistProfile.XML_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


def binary_document(hex_string: str):
    return parse(
        bytes.fromhex(hex_string),
        PlistProfile.BINARY_V1,
        PlistEncodingSelection.profile_default(),
        DEFAULT_LIMITS,
    )


# ---------------------------------------------------------------------------
# plist.projection.value-tree-record (plist-v1.json:1090-1148)
# ---------------------------------------------------------------------------


def test_projection_value_tree_record():
    # Case plist.projection.value-tree-record (plist-v1.json:1091-1147).
    source = (
        '<plist version="1.0"><dict>'
        "<key>name</key><string>text</string>"
        "<key>count</key><integer>42</integer>"
        "<key>ratio</key><real>1.5</real>"
        "<key>enabled</key><true/>"
        "<key>disabled</key><false/>"
        "<key>payload</key><data>AQID</data>"
        "<key>created</key><date>2023-01-01T00:00:00Z</date>"
        "<key>tags</key><array><string>a</string><string>b</string></array>"
        "</dict></plist>"
    )
    document = xml_document(source)
    result = project(document, ProjectionRequest.value_tree())
    assert result.complete is not None
    completion = result.complete
    assert completion.fidelity.value == "Exact"
    record = completion.value.as_object()
    fields = dict(record)
    assert fields["record"].as_string() == "plist.value-tree@1"
    root = fields["root"]
    # Ordered dictionary associations project as an EntryMapping (RFC 0013
    # §9: "dict EntryMapping of [key string, value] associations").
    assert root.kind.value == "EntryMapping"
    entries = root.as_entry_mapping()
    keys = [key.as_string() for key, _ in entries]
    assert keys == [
        "name",
        "count",
        "ratio",
        "enabled",
        "disabled",
        "payload",
        "created",
        "tags",
    ]
    values = dict((key.as_string(), value) for key, value in entries)
    assert values["name"].as_string() == "text"
    assert values["count"].as_integer() == 42
    assert values["ratio"].kind.value == "BinaryFloat64"
    assert values["enabled"].as_boolean() is True
    assert values["disabled"].as_boolean() is False
    payload = values["payload"].as_bytes()
    assert payload.hex() == "010203"
    date_fields = dict(values["created"].as_object())
    assert date_fields["epoch"].as_string() == "2001-01-01T00:00:00Z"
    assert date_fields["seconds"].kind.value == "BinaryFloat64"
    tags = values["tags"].as_sequence()
    assert [item.as_string() for item in tags] == ["a", "b"]


# ---------------------------------------------------------------------------
# plist.projection.require-object-policies (plist-v1.json:1149-1199)
# ---------------------------------------------------------------------------


def test_projection_require_object_policies():
    # Case plist.projection.require-object-policies (plist-v1.json:1150-1198).
    duplicate_source = (
        '<plist version="1.0"><dict>'
        "<key>a</key><string>one</string>"
        "<key>a</key><string>last</string>"
        "<key>b</key><string>two</string>"
        "</dict></plist>"
    )
    date_source = (
        '<plist version="1.0"><dict>'
        "<key>d</key><date>2023-01-01T00:00:00Z</date>"
        "<key>s</key><string>x</string>"
        "</dict></plist>"
    )
    data_source = (
        '<plist version="1.0"><dict><key>p</key><data>AQID</data></dict></plist>'
    )
    reject = project(
        xml_document(duplicate_source),
        ProjectionRequest.require_object(CollisionPolicy.REJECT),
    )
    assert reject.failed is not None
    assert reject.failed.diagnostics[0].code == "plist.projection.collision@1"
    first = project(
        xml_document(duplicate_source),
        ProjectionRequest.require_object(CollisionPolicy.FIRST),
    )
    assert first.complete is not None
    # The require-object projection value is the plain unique-key Object
    # itself, not a value-tree record wrapper (go/plist/projection.go
    # projectRequireObject; go/conformance/plist_v1.go:2636-2641).
    entries = first.complete.value.as_object()
    assert [key for key, _ in entries] == ["a", "b"]
    assert [value.as_string() for _, value in entries] == ["one", "two"]
    assert first.complete.fidelity.value == "Transformed"
    assert len(first.complete.report.events) == 1
    date_reject = project(
        xml_document(date_source),
        ProjectionRequest.require_object(CollisionPolicy.REJECT),
    )
    assert date_reject.failed is not None
    assert date_reject.failed.diagnostics[0].code == "plist.projection.unrepresentable@1"
    data_reject = project(
        xml_document(data_source),
        ProjectionRequest.require_object(CollisionPolicy.REJECT),
    )
    assert data_reject.failed is not None
    assert data_reject.failed.diagnostics[0].code == "plist.projection.unrepresentable@1"


# ---------------------------------------------------------------------------
# plist.projection.atomic-failures (plist-v1.json:1200-1221)
# ---------------------------------------------------------------------------


def test_projection_atomic_failures():
    # Case plist.projection.atomic-failures (plist-v1.json:1201-1220).
    incomplete = xml_document('<plist version="1.0"><dict><key>a</key></dict></plist>')
    result = project(incomplete, ProjectionRequest.value_tree())
    assert result.failed is not None
    assert result.failed.diagnostics[0].code == "plist.projection.incomplete-document@1"

    unpaired = binary_document(
        "62706c697374303062d800004108000000000000010100000000000000010000000000000000000000000000000d"
    )
    result = project(unpaired, ProjectionRequest.value_tree())
    assert result.failed is not None
    assert result.failed.diagnostics[0].code == "plist.projection.unpaired-surrogate@1"


def test_projection_uid_policy():
    # UIDs project only under an explicit Include policy into a typed UID
    # member and are never disguised as integers (RFC 0013 §9;
    # projection.rs:63-71).
    document = binary_document(
        "62706c6973743030800508000000000000010100000000000000010000000000000000000000000000000a"
    )
    excluded = project(document, ProjectionRequest.value_tree())
    assert excluded.failed is not None
    assert excluded.failed.diagnostics[0].code == "plist.projection.unrepresentable@1"
    included = project(document, ProjectionRequest.value_tree_with_uid(UidPolicy.INCLUDE))
    assert included.complete is not None
    root = dict(included.complete.value.as_object())["root"]
    uid_fields = dict(root.as_object())
    assert uid_fields["uid"].as_integer() == 5
