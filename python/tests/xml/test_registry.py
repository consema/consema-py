"""Operation-registry intent tests: the frozen eight-operation XML surface.

Authority: https://github.com/consema/consema-rs/blob/main/consema-xml/src/operation_registry.rs (the exact
descriptors) and its surface test operation_registry.rs (the
frozen eight-operation list); RFC 0012 §11 (https://github.com/consema/consema/blob/main/docs/rfcs/0012-xml-1.0-safe-
profile-v1.md).

These tests are delivered: the full suite runs in CI (ci-python.yml
python-gates job) against the provisioned conformance data.
"""

from __future__ import annotations

import pytest

from consema.document.ids import ProfileId

from consema.xml import (
    OperationArgumentKind,
    OperationSupport,
    format_operation_registry,
)

# The frozen eight-operation surface, operation_registry.rs.
EXPECTED_OPERATIONS = (
    "xml.edit.insert-attribute@1",
    "xml.edit.insert-element@1",
    "xml.edit.remove-attribute@1",
    "xml.edit.remove-element@1",
    "xml.edit.rename-attribute@1",
    "xml.edit.rename-element@1",
    "xml.edit.replace-text@1",
    "xml.edit.set-attribute-value@1",
)


def test_frozen_eight_operation_surface():
    """The XML profile publishes exactly the frozen eight-operation surface
    (operation_registry.rs)."""
    registry = format_operation_registry(ProfileId.new("xml.1.0-safe", 1))
    operations = tuple(
        descriptor.id.to_string() for descriptor in registry.operations()
    )
    assert operations == EXPECTED_OPERATIONS
    assert all(
        descriptor.support is OperationSupport.SUPPORTED
        for descriptor in registry.operations()
    )


def test_descriptor_target_roles_and_arguments():
    """Each descriptor carries its frozen target role and argument schema
    (operation_registry.rs)."""
    registry = format_operation_registry(ProfileId.new("xml.1.0-safe", 1))
    by_id = {descriptor.id.to_string(): descriptor for descriptor in registry.operations()}
    replace_text = by_id["xml.edit.replace-text@1"]
    assert replace_text.target_role == "xml.text@1"
    assert [(a.name, a.kind) for a in replace_text.arguments] == [
        ("text", OperationArgumentKind.STRING)
    ]
    insert_attribute = by_id["xml.edit.insert-attribute@1"]
    assert insert_attribute.target_role == "xml.element@1"
    assert [(a.name, a.kind) for a in insert_attribute.arguments] == [
        ("name", OperationArgumentKind.STRING),
        ("value", OperationArgumentKind.STRING),
        ("placement", OperationArgumentKind.PLACEMENT),
    ]
    remove_attribute = by_id["xml.edit.remove-attribute@1"]
    assert remove_attribute.target_role == "xml.attribute@1"
    assert remove_attribute.arguments == ()
    insert_element = by_id["xml.edit.insert-element@1"]
    assert insert_element.target_role == "xml.element@1"
    assert [(a.name, a.kind) for a in insert_element.arguments] == [
        ("name", OperationArgumentKind.STRING),
        ("content", OperationArgumentKind.STRING),
        ("placement", OperationArgumentKind.PLACEMENT),
    ]


def test_foreign_profile_is_rejected():
    """The registry binds exactly one profile: ``xml.1.0-safe@1``
    (operation_registry.rs)."""
    with pytest.raises(ValueError):
        format_operation_registry(ProfileId.new("toml.1.0", 1))
