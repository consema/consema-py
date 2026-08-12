"""The frozen plist format operation registry (6 records per profile).

Authority: crates/consema-plist/src/operation_registry.rs:104-132 (the
frozen surface test: exactly six Supported structural operations per
profile, sorted) and RFC 0013 §11 (docs/rfcs/0013-plist-family-profiles-
v1.md:685-695).
"""

from __future__ import annotations

from consema.plist import PlistProfile, format_operation_registry


def test_every_plist_profile_publishes_the_frozen_six_operation_surface():
    # operation_registry.rs:104-132.
    expected = [
        "plist.edit.insert-array-element@1",
        "plist.edit.insert-dict-entry@1",
        "plist.edit.remove-array-element@1",
        "plist.edit.remove-dict-entry@1",
        "plist.edit.rename-dict-key@1",
        "plist.edit.set-value@1",
    ]
    for profile in (PlistProfile.XML_V1, PlistProfile.BINARY_V1):
        registry = format_operation_registry(profile)
        assert list(registry.operation_ids()) == expected
        assert all(
            operation.support.value == "Supported"
            for operation in registry.operations
        )
        assert registry.profile == profile.id()


def test_descriptor_argument_schemas_are_frozen():
    # operation_registry.rs:21-82.
    registry = format_operation_registry(PlistProfile.XML_V1)
    set_value = registry.find("plist.edit.set-value@1")
    assert set_value is not None
    assert set_value.target_role == "plist.value"
    assert [argument.name for argument in set_value.arguments] == ["path", "value"]
    insert_dict = registry.find("plist.edit.insert-dict-entry@1")
    assert insert_dict is not None
    assert [argument.name for argument in insert_dict.arguments] == [
        "path",
        "key",
        "value",
        "placement",
    ]
    remove_dict = registry.find("plist.edit.remove-dict-entry@1")
    assert remove_dict is not None
    assert remove_dict.target_role == "plist.dict-entry"
    remove_array = registry.find("plist.edit.remove-array-element@1")
    assert remove_array is not None
    assert remove_array.target_role == "plist.array-element"
