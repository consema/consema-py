"""The HCL format operation registry (RFC 0014 §10).

The frozen surface is pinned by the Rust registry tests
(crates/consema-hcl/src/operation_registry.rs:100-157): `hcl.native@1`
publishes exactly the six frozen operations in the frozen order;
`hcl.tfvars@1` publishes the four attribute operations only.
"""

from __future__ import annotations

from consema.hcl import HclProfile, format_operation_registry


def test_native_profile_publishes_the_frozen_six_operation_surface():
    # operation_registry.rs:105-127.
    registry = format_operation_registry(HclProfile.NATIVE_V1)
    assert registry.operation_ids() == (
        "hcl.edit.insert-attribute@1",
        "hcl.edit.insert-block@1",
        "hcl.edit.remove-attribute@1",
        "hcl.edit.remove-block@1",
        "hcl.edit.rename-attribute@1",
        "hcl.edit.set-attribute-value@1",
    )
    assert all(operation.support.value == "Supported" for operation in registry.operations)


def test_tfvars_profile_publishes_attribute_operations_only():
    # operation_registry.rs:129-156.
    registry = format_operation_registry(HclProfile.TFVARS_V1)
    assert registry.operation_ids() == (
        "hcl.edit.insert-attribute@1",
        "hcl.edit.remove-attribute@1",
        "hcl.edit.rename-attribute@1",
        "hcl.edit.set-attribute-value@1",
    )
    assert all("block" not in operation.to_string() for operation in registry.operations)


def test_argument_schemas():
    # operation_registry.rs:26-80: the exact argument schemas.
    registry = format_operation_registry(HclProfile.NATIVE_V1)
    insert_attribute = registry.find("hcl.edit.insert-attribute@1")
    assert [argument.name for argument in insert_attribute.arguments] == [
        "name",
        "value",
        "placement",
    ]
    set_value = registry.find("hcl.edit.set-attribute-value@1")
    assert [argument.name for argument in set_value.arguments] == ["value"]
    insert_block = registry.find("hcl.edit.insert-block@1")
    assert [argument.name for argument in insert_block.arguments] == [
        "type",
        "labels",
        "attributes",
        "placement",
    ]
    assert insert_block.target_role == "hcl.body"
    remove_block = registry.find("hcl.edit.remove-block@1")
    assert remove_block.target_role == "hcl.block"
    assert remove_block.arguments == ()


def test_operation_ids_are_frozen_spellings():
    # RFC 0014 §10: the six operation ids are frozen language-neutral
    # spellings (docs/rfcs/0014-...:630-642).
    from consema.hcl import (
        EditOperationKind,
    )

    assert EditOperationKind.SET_ATTRIBUTE_VALUE.value == "SetAttributeValue"
    assert EditOperationKind.INSERT_ATTRIBUTE.value == "InsertAttribute"
    assert EditOperationKind.REMOVE_ATTRIBUTE.value == "RemoveAttribute"
    assert EditOperationKind.RENAME_ATTRIBUTE.value == "RenameAttribute"
    assert EditOperationKind.INSERT_BLOCK.value == "InsertBlock"
    assert EditOperationKind.REMOVE_BLOCK.value == "RemoveBlock"
