"""Operation registry golden transcription (java-properties-v1.json case).

Case covered: registry.frozen-five-operation-surface
(java-properties-v1.json:147-149) — both profiles publish the same frozen
five Supported structural operations; the operation ids are exact
(operation_registry.rs:16-48).
"""

from __future__ import annotations

import pytest

from consema.properties import (
    OperationSupport,
    PropertiesProfile,
    format_operation_registry,
)

EXPECTED = [
    "java-properties.edit.insert-property@1",
    "java-properties.edit.remove-property@1",
    "java-properties.edit.rename-property@1",
    "java-properties.edit.replace-literal-value@1",
    "java-properties.edit.replace-semantic-value@1",
]


def test_both_profiles_publish_the_same_frozen_five_operation_surface():
    # Case registry.frozen-five-operation-surface
    # (java-properties-v1.json:147-149).
    for profile in (PropertiesProfile.READER_V1, PropertiesProfile.LATIN1_V1):
        registry = format_operation_registry(profile)
        assert list(registry.operation_ids()) == EXPECTED
        assert len(registry.operations) == 5
        assert all(
            operation.support is OperationSupport.SUPPORTED
            for operation in registry.operations
        )


def test_descriptor_argument_schemas():
    # Exact argument schemas (operation_registry.rs:17-47).
    registry = format_operation_registry(PropertiesProfile.READER_V1)
    by_id = {operation.to_string(): operation for operation in registry.operations}
    assert by_id["java-properties.edit.insert-property@1"].target_role == "java-properties.document"
    assert [
        (argument.name, argument.kind.value)
        for argument in by_id["java-properties.edit.insert-property@1"].arguments
    ] == [
        ("key", "PortableValue"),
        ("value", "PortableValue"),
        ("placement", "Placement"),
    ]
    assert by_id["java-properties.edit.remove-property@1"].arguments == ()
    assert by_id["java-properties.edit.rename-property@1"].target_role == "java-properties.property"
    assert [
        (argument.name, argument.kind.value)
        for argument in by_id["java-properties.edit.rename-property@1"].arguments
    ] == [("key", "PortableValue")]
    assert [
        (argument.name, argument.kind.value)
        for argument in by_id["java-properties.edit.replace-literal-value@1"].arguments
    ] == [("literal", "ExactBytes")]
    assert [
        (argument.name, argument.kind.value)
        for argument in by_id["java-properties.edit.replace-semantic-value@1"].arguments
    ] == [("value", "PortableValue")]
