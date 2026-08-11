"""Edit golden transcriptions for the plist family (RFC 0013 §11).

Golden cases transcribed verbatim from conformance/vectors/plist-v1.json;
each test cites the vector case id.

Cases covered here:

- plist-v1.json: plist.edit.xml-six-operations (lines 1379-1450),
  plist.edit.binary-structural (1451-1496),
  plist.edit.conflicts (1497-1564).
"""

from __future__ import annotations

import pytest

from consema.document.edit_plan import EditPlanSourceId
from consema.document.source_patch import SourcePatchLimits
from consema.plist import (
    DictEntryPlacement,
    EditPath,
    EditPathStep,
    EditTransactionBuilder,
    EditValue,
    PlistBoolean,
    PlistEncodingSelection,
    PlistInteger,
    PlistKey,
    PlistParseLimits,
    PlistProfile,
    PlistString,
    commit,
    dry_run,
    parse,
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


def key(text: str) -> PlistKey:
    return PlistKey.from_unicode(text)


def path(*steps: EditPathStep) -> EditPath:
    return EditPath.new(tuple(steps))


# ---------------------------------------------------------------------------
# plist.edit.xml-six-operations (plist-v1.json:1379-1450)
# ---------------------------------------------------------------------------


def test_xml_six_operations():
    # Case plist.edit.xml-six-operations (plist-v1.json:1380-1449).
    source = (
        '<plist version="1.0"><dict>'
        "<key>a</key><dict><key>b</key><string>old</string></dict>"
        "<key>arr</key><array><integer>1</integer><integer>2</integer></array>"
        "</dict></plist>"
    )
    document = xml_document(source)
    builder = EditTransactionBuilder(document)
    builder.set_value(
        path(EditPathStep.dict_key(key("a")), EditPathStep.dict_key(key("b"))),
        EditValue.string(PlistString.from_unicode("new")),
    )
    builder.insert_dict_entry(
        path(EditPathStep.dict_key(key("a"))),
        key("c"),
        EditValue.integer(PlistInteger(3)),
        DictEntryPlacement.end(),
    )
    builder.insert_array_element(
        path(EditPathStep.dict_key(key("arr"))),
        0,
        EditValue.string(PlistString.from_unicode("z")),
    )
    builder.remove_array_element(path(EditPathStep.dict_key(key("arr"))), 2)
    builder.rename_dict_key(
        path(EditPathStep.dict_key(key("a"))),
        key("c"),
        0,
        key("c2"),
    )
    builder.remove_dict_entry(path(EditPathStep.dict_key(key("a"))), key("b"))
    result = commit(document, builder.build())
    assert result.document.formation_status().value == "Complete"
    native = result.document.document()
    root = native.root_value()
    dict_value = root.as_dict()
    values = {entry.key.to_unicode(): native.get(entry.value) for entry in dict_value.entries}
    dict_a = values["a"].as_dict()
    assert [entry.key.to_unicode() for entry in dict_a.entries] == ["c2"]
    assert [native.get(entry.value).as_integer().value for entry in dict_a.entries] == [3]
    arr = values["arr"].as_array()
    arr_values = [native.get(ref) for ref in arr.elements]
    assert arr_values[0].as_string().to_unicode() == "z"
    assert arr_values[1].as_integer().value == 1
    # Reparse closure: the committed bytes reparse Complete.
    reparsed = xml_document(result.document.render().decode("utf-8"))
    assert reparsed.formation_status().value == "Complete"
    assert reparsed.document() == result.document.document()
    # Untouched-byte proof verifies and the patch replays.
    result.untouched_proof.verify(
        document.source, result.document.source, list(result.source_patch.replacements)
    )
    replayed = result.source_patch.apply(document.source, SourcePatchLimits())
    assert replayed.bytes() == result.document.render()
    assert replayed.digest() == result.document.source.digest()


# ---------------------------------------------------------------------------
# plist.edit.binary-structural (plist-v1.json:1451-1496)
# ---------------------------------------------------------------------------


def test_binary_structural_edit():
    # Case plist.edit.binary-structural (plist-v1.json:1452-1495).
    document = binary_document(
        "62706c6973743030a2010210015162080b0d000000000000010100000000000000030000000000000000000000000000000f"
    )
    builder = EditTransactionBuilder(document)
    builder.set_value(
        path(EditPathStep.array_index(1)),
        EditValue.integer(PlistInteger(42)),
    )
    builder.insert_array_element(
        path(),
        0,
        EditValue.boolean(PlistBoolean(True)),
    )
    result = commit(document, builder.build())
    assert result.document.formation_status().value == "Complete"
    native = result.document.document()
    root = native.root_value()
    array = root.as_array()
    assert [native.get(ref).kind.value for ref in array.elements] == [
        "boolean",
        "integer",
        "integer",
    ]
    values = [native.get(ref) for ref in array.elements]
    assert values[0].as_boolean().value is True
    assert values[1].as_integer().value == 1
    assert values[2].as_integer().value == 42
    # Untouched objects keep their exact bytes.
    for region in result.untouched_proof.regions:
        base_bytes = document.source.bytes()[region.old_start : region.old_end]
        target_bytes = result.document.source.bytes()[region.new_start : region.new_end]
        assert base_bytes == target_bytes
    # The patch replays to the exact committed bytes and digest.
    replayed = result.source_patch.apply(document.source, SourcePatchLimits())
    assert replayed.bytes() == result.document.render()
    assert replayed.digest() == result.document.source.digest()


# ---------------------------------------------------------------------------
# plist.edit.conflicts (plist-v1.json:1497-1564)
# ---------------------------------------------------------------------------


def test_edit_conflicts():
    # Case plist.edit.conflicts (plist-v1.json:1498-1563).
    from consema.plist import PlistEditFailure, PlistUid

    uid_in_xml = xml_document('<plist version="1.0"><dict><key>a</key><string>x</string></dict></plist>')
    builder = EditTransactionBuilder(uid_in_xml)
    builder.set_value(
        path(EditPathStep.dict_key(key("a"))),
        EditValue.uid(PlistUid(5)),
    )
    with pytest.raises(PlistEditFailure) as excinfo:
        commit(uid_in_xml, builder.build())
    assert excinfo.value.code == "plist.edit.uid-in-xml@1"

    incomplete = xml_document('<plist version="1.0"><dict><key>a</key></dict></plist>')
    builder = EditTransactionBuilder(incomplete)
    builder.set_value(
        path(EditPathStep.dict_key(key("a"))),
        EditValue.integer(PlistInteger(1)),
    )
    with pytest.raises(PlistEditFailure) as excinfo:
        commit(incomplete, builder.build())
    assert excinfo.value.code == "core.edit.incomplete-target@1"

    base = binary_document(
        "62706c6973743030a2010210015162080b0d000000000000010100000000000000030000000000000000000000000000000f"
    )
    wrong = binary_document(
        "62706c697374303050080000000000000101000000000000000100000000000000000000000000000009"
    )
    builder = EditTransactionBuilder(wrong)
    builder.set_value(
        path(EditPathStep.array_index(1)),
        EditValue.integer(PlistInteger(42)),
    )
    with pytest.raises(PlistEditFailure) as excinfo:
        commit(base, builder.build())
    assert excinfo.value.code == "core.edit.wrong-snapshot@1"


def test_dry_run_matches_commit():
    # RFC 0004 §14: dry-run and commit produce the same replacement set and
    # target digest.
    source = '<plist version="1.0"><dict><key>a</key><integer>1</integer></dict></plist>'
    document = xml_document(source)
    builder = EditTransactionBuilder(document)
    builder.set_value(
        path(EditPathStep.dict_key(key("a"))),
        EditValue.integer(PlistInteger(7)),
    )
    transaction = builder.build()
    result = commit(document, transaction)
    plan = dry_run(document, transaction, EditPlanSourceId.new("memory:plist-test"))
    assert plan.base_digest() == document.source.digest()
    assert plan.target_digest() == result.document.source.digest()
    assert plan.replacements() == result.source_patch.replacements
