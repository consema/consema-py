"""The frozen eight-operation INI registry surface.

Case covered here (conformance/vectors/ini-v1.json, suite
"consema.ini.conformance@1"): registry.frozen-eight-operation-surface
(lines 136-139) — every profile publishes the same eight operation ids in
the same order, with exactly six direct structural (Supported)
operations.

Authority: crates/consema-ini/src/operation_registry.rs:16-80 (descriptors)
and the registry test operation_registry.rs:105-137; RFC 0009 §12
(docs/rfcs/0009-ini-family-profiles-v1.md:439-455).
"""

from __future__ import annotations

from consema.ini import (
    IniProfile,
    OperationArgumentKind,
    OperationSupport,
    descriptors,
    format_operation_registry,
)

EXPECTED_OPERATIONS = [
    "ini.edit.insert-entry@1",
    "ini.edit.insert-section@1",
    "ini.edit.remove-entry@1",
    "ini.edit.remove-section@1",
    "ini.edit.rename-entry@1",
    "ini.edit.rename-section@1",
    "ini.edit.replace-literal-value@1",
    "ini.edit.replace-semantic-value@1",
]


def test_frozen_eight_operation_surface():
    # Case registry.frozen-eight-operation-surface (ini-v1.json:136-139).
    for profile in (
        IniProfile.PORTABLE_V1,
        IniProfile.WINDOWS_V1,
        IniProfile.PYTHON_CONFIGPARSER_V1,
    ):
        registry = format_operation_registry(profile)
        assert list(registry.operation_ids()) == EXPECTED_OPERATIONS
        supported = [
            operation
            for operation in registry.operations
            if operation.support is OperationSupport.SUPPORTED
        ]
        assert len(supported) == 6


def test_descriptor_target_roles_and_arguments():
    # operation_registry.rs:18-79: exact target roles and argument schemas.
    by_id = {descriptor.to_string(): descriptor for descriptor in descriptors()}
    assert by_id["ini.edit.insert-section@1"].target_role == "ini.document"
    assert [argument.name for argument in by_id["ini.edit.insert-section@1"].arguments] == [
        "name",
        "placement",
    ]
    assert by_id["ini.edit.remove-section@1"].target_role == "ini.section"
    assert by_id["ini.edit.rename-section@1"].target_role == "ini.section"
    assert [argument.name for argument in by_id["ini.edit.rename-section@1"].arguments] == ["name"]
    assert by_id["ini.edit.insert-entry@1"].target_role == "ini.section"
    assert [argument.name for argument in by_id["ini.edit.insert-entry@1"].arguments] == [
        "key",
        "value",
        "placement",
    ]
    assert by_id["ini.edit.remove-entry@1"].target_role == "ini.entry"
    assert by_id["ini.edit.rename-entry@1"].target_role == "ini.entry"
    assert [argument.name for argument in by_id["ini.edit.rename-entry@1"].arguments] == ["key"]

    semantic = by_id["ini.edit.replace-semantic-value@1"]
    assert semantic.target_role == "ini.entry"
    assert semantic.support is OperationSupport.EXISTING_TYPED_CAPABILITY
    assert [argument.name for argument in semantic.arguments] == [
        "value",
        "representation_policy",
    ]
    assert semantic.arguments[1].kind is OperationArgumentKind.REPRESENTATION_POLICY

    literal = by_id["ini.edit.replace-literal-value@1"]
    assert literal.target_role == "ini.entry"
    assert literal.support is OperationSupport.EXISTING_TYPED_CAPABILITY
    assert literal.arguments[0].kind is OperationArgumentKind.EXACT_BYTES


def test_registry_profile_is_bound_to_the_exact_profile():
    registry = format_operation_registry(IniProfile.WINDOWS_V1)
    assert registry.profile.id == "ini.windows"
    assert registry.profile.version == 1
    assert registry.find("ini.edit.remove-entry@1") is not None
    assert registry.find("json.edit.remove-member@1") is None
