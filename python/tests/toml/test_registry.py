"""The frozen TOML operation registry (seven operations).

The exact ids, target roles, argument kinds, and support classification
are transcribed from crates/consema-toml/src/operation_registry.rs:16-74;
the structural surface (five Supported operations) and the total count
are pinned by the Rust registry test operation_registry.rs:94-119.
"""

from __future__ import annotations

from consema.document.ids import ProfileId
from consema.toml import (
    OperationArgumentKind,
    OperationSupport,
    format_operation_registry,
)


def test_registry_has_exactly_seven_frozen_operations():
    registry = format_operation_registry(ProfileId.new("toml.1.0", 1))
    operations = registry.operations()
    assert len(operations) == 7
    assert [operation.id.to_string() for operation in operations] == [
        "toml.edit.insert-array-element@1",
        "toml.edit.insert-entry@1",
        "toml.edit.remove-array-element@1",
        "toml.edit.remove-entry@1",
        "toml.edit.rename-entry@1",
        "toml.edit.replace-scalar-literal@1",
        "toml.edit.replace-scalar-semantic@1",
    ]


def test_structural_surface_is_the_five_supported_operations():
    """operation_registry.rs:99-118: the mandatory structural surface is
    exactly the five Supported operations."""
    registry = format_operation_registry(ProfileId.new("toml.1.0", 1))
    structural = [
        operation.id.to_string()
        for operation in registry.operations()
        if operation.support is OperationSupport.SUPPORTED
    ]
    assert structural == [
        "toml.edit.insert-array-element@1",
        "toml.edit.insert-entry@1",
        "toml.edit.remove-array-element@1",
        "toml.edit.remove-entry@1",
        "toml.edit.rename-entry@1",
    ]


def test_descriptor_shapes_are_frozen():
    registry = format_operation_registry(ProfileId.new("toml.1.0", 1))
    by_id = {operation.id.id: operation for operation in registry.operations()}

    insert = by_id["toml.edit.insert-entry"]
    assert insert.target_role == "toml.table-item@1"
    assert [(a.name, a.kind) for a in insert.arguments] == [
        ("key", OperationArgumentKind.STRING),
        ("value", OperationArgumentKind.PORTABLE_VALUE),
        ("placement", OperationArgumentKind.PLACEMENT),
    ]

    remove = by_id["toml.edit.remove-entry"]
    assert remove.target_role == "toml.entry@1"
    assert remove.arguments == ()

    rename = by_id["toml.edit.rename-entry"]
    assert rename.target_role == "toml.entry@1"
    assert [(a.name, a.kind) for a in rename.arguments] == [
        ("key", OperationArgumentKind.STRING)
    ]

    insert_element = by_id["toml.edit.insert-array-element"]
    assert insert_element.target_role == "toml.array-item@1"
    assert [(a.name, a.kind) for a in insert_element.arguments] == [
        ("value", OperationArgumentKind.PORTABLE_VALUE),
        ("placement", OperationArgumentKind.PLACEMENT),
    ]

    remove_element = by_id["toml.edit.remove-array-element"]
    assert remove_element.target_role == "toml.array-element@1"
    assert remove_element.arguments == ()

    semantic = by_id["toml.edit.replace-scalar-semantic"]
    assert semantic.target_role == "toml.scalar-item@1"
    assert semantic.support is OperationSupport.EXISTING_TYPED_CAPABILITY
    assert [(a.name, a.kind) for a in semantic.arguments] == [
        ("value", OperationArgumentKind.PORTABLE_VALUE),
        ("representation_policy", OperationArgumentKind.REPRESENTATION_POLICY),
    ]

    literal = by_id["toml.edit.replace-scalar-literal"]
    assert literal.target_role == "toml.scalar-item@1"
    assert literal.support is OperationSupport.EXISTING_TYPED_CAPABILITY
    assert [(a.name, a.kind) for a in literal.arguments] == [
        ("literal", OperationArgumentKind.EXACT_BYTES)
    ]


def test_registry_binds_the_toml_profile():
    registry = format_operation_registry(ProfileId.new("toml.1.0", 1))
    assert registry.profile_id() == ProfileId.new("toml.1.0", 1)
