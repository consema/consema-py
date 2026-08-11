"""The YAML-family format operation registry (8 frozen records).

Authority: crates/consema-yaml/src/operation_registry.rs:16-83 — the exact
operation ids, target roles, argument schemas, and support classifications;
the structural surface is frozen by the registry test
(operation_registry.rs:107-135: exactly six Supported structural operations
for every profile and eight total records, with the insert-alias anchor
argument on role yaml.sequence) and RFC 0007 s12
(docs/rfcs/0007-yaml-family-profiles-and-safety-v1.md:357-398).

Frozen records (operation_registry.rs:17-82):

1. yaml.edit.insert-alias@1           yaml.sequence         anchor(NodeRef), placement(Placement)              Supported
2. yaml.edit.insert-mapping-entry@1   yaml.mapping          key(PortableValue), value(PortableValue), placement(Placement)  Supported
3. yaml.edit.insert-sequence-element@1 yaml.sequence        value(PortableValue), placement(Placement)          Supported
4. yaml.edit.remove-mapping-entry@1   yaml.mapping-entry    (no arguments)                                      Supported
5. yaml.edit.remove-sequence-element@1 yaml.sequence-element (no arguments)                                    Supported
6. yaml.edit.rename-anchor@1          yaml.anchor-definition name(String)                                      Supported
7. yaml.edit.replace-scalar-literal@1  yaml.scalar          literal(ExactBytes)                                 ExistingTypedCapability
8. yaml.edit.replace-scalar-semantic@1 yaml.scalar          value(PortableValue), representation_policy(RepresentationPolicy)  ExistingTypedCapability

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId, operation_registry.rs:10-42); the
EditPlan operation-metadata matching rule requires that form
(edit_plan.rs:84-121).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.yaml.kinds import YamlProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs:25-27)."""

    SUPPORTED = "Supported"
    EXISTING_TYPED_CAPABILITY = "ExistingTypedCapability"


class OperationArgumentKind(enum.Enum):
    """Argument value kinds (consema-document operation registry)."""

    NODE_REF = "NodeRef"
    PORTABLE_VALUE = "PortableValue"
    PLACEMENT = "Placement"
    STRING = "String"
    EXACT_BYTES = "ExactBytes"
    REPRESENTATION_POLICY = "RepresentationPolicy"


@dataclass(frozen=True, slots=True)
class OperationArgumentDescriptor:
    """One operation argument schema (operation_registry.rs:99-101)."""

    name: str
    kind: OperationArgumentKind
    required: bool = True


@dataclass(frozen=True, slots=True)
class FormatOperationDescriptor:
    """One validated immutable operation record (operation_registry.rs:90-97)."""

    id: FormatOperationId
    target_role: str
    arguments: tuple[OperationArgumentDescriptor, ...]
    support: OperationSupport

    def to_string(self) -> str:
        """Canonical ``id@version`` spelling."""
        return self.id.to_string()


@dataclass(frozen=True, slots=True)
class YamlFormatOperationRegistry:
    """Validated operation registry for one exact YAML profile
    (operation_registry.rs:9-14)."""

    profile: ProfileId
    operations: tuple[FormatOperationDescriptor, ...]

    def operation_ids(self) -> tuple[str, ...]:
        return tuple(operation.to_string() for operation in self.operations)

    def find(self, id_string: str) -> FormatOperationDescriptor | None:
        for operation in self.operations:
            if operation.to_string() == id_string:
                return operation
        return None


def _descriptor(
    id_string: str,
    target_role: str,
    arguments: list[tuple[str, OperationArgumentKind]],
    support: OperationSupport,
) -> FormatOperationDescriptor:
    operation_id, version = id_string.rsplit("@", 1)
    return FormatOperationDescriptor(
        id=FormatOperationId.new(operation_id, int(version)),
        target_role=target_role,
        arguments=tuple(
            OperationArgumentDescriptor(name, kind) for name, kind in arguments
        ),
        support=support,
    )


def _argument(name: str, kind: OperationArgumentKind) -> tuple[str, OperationArgumentKind]:
    return (name, kind)


def descriptors() -> tuple[FormatOperationDescriptor, ...]:
    """The frozen eight-record descriptor set (operation_registry.rs:16-82)."""
    return (
        _descriptor(
            "yaml.edit.insert-alias@1",
            "yaml.sequence",
            [
                _argument("anchor", OperationArgumentKind.NODE_REF),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.insert-mapping-entry@1",
            "yaml.mapping",
            [
                _argument("key", OperationArgumentKind.PORTABLE_VALUE),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.insert-sequence-element@1",
            "yaml.sequence",
            [
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.remove-mapping-entry@1",
            "yaml.mapping-entry",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.remove-sequence-element@1",
            "yaml.sequence-element",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.rename-anchor@1",
            "yaml.anchor-definition",
            [_argument("name", OperationArgumentKind.STRING)],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "yaml.edit.replace-scalar-literal@1",
            "yaml.scalar",
            [_argument("literal", OperationArgumentKind.EXACT_BYTES)],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
        _descriptor(
            "yaml.edit.replace-scalar-semantic@1",
            "yaml.scalar",
            [
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("representation_policy", OperationArgumentKind.REPRESENTATION_POLICY),
            ],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
    )


def format_operation_registry(profile: YamlProfile) -> YamlFormatOperationRegistry:
    """Returns the validated operation registry for one exact YAML profile
    (operation_registry.rs:9-14)."""
    name, version = profile.id()
    return YamlFormatOperationRegistry(
        profile=ProfileId.new(name, version),
        operations=descriptors(),
    )
