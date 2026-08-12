"""The Java Properties format operation registry (5 frozen records).

Authority: crates/consema-properties/src/operation_registry.rs:16-48 — the
exact operation ids, target roles, argument schemas, and support
classifications; the surface is frozen by the registry test
(operation_registry.rs:67-95: exactly five Supported structural operations
for every profile) and RFC 0010 section 13 (docs/rfcs/0010-java-properties-
profiles-v1.md:385-393). The vector suite pins the exact five-operation
surface (conformance/vectors/java-properties-v1.json:147-149,
"registry.frozen-five-operation-surface").

Frozen records (operation_registry.rs:17-47):

1. java-properties.edit.insert-property@1         java-properties.document   key(PortableValue), value(PortableValue), placement(Placement)   Supported
2. java-properties.edit.remove-property@1         java-properties.property   (no arguments)                                                  Supported
3. java-properties.edit.rename-property@1         java-properties.property   key(PortableValue)                                              Supported
4. java-properties.edit.replace-literal-value@1   java-properties.property   literal(ExactBytes)                                             Supported
5. java-properties.edit.replace-semantic-value@1  java-properties.property   value(PortableValue)                                            Supported

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId, operation_registry.rs:10-42); the
EditPlan operation-metadata matching rule requires that form
(edit_plan.rs:84-121).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.properties.kinds import PropertiesProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs:56-60, 71-78)."""

    SUPPORTED = "Supported"
    EXISTING_TYPED_CAPABILITY = "ExistingTypedCapability"


class OperationArgumentKind(enum.Enum):
    """Argument value kinds (consema-document operation registry)."""

    STRING = "String"
    PORTABLE_VALUE = "PortableValue"
    PLACEMENT = "Placement"
    REPRESENTATION_POLICY = "RepresentationPolicy"
    EXACT_BYTES = "ExactBytes"


@dataclass(frozen=True, slots=True)
class OperationArgumentDescriptor:
    """One operation argument schema (operation_registry.rs:63-65)."""

    name: str
    kind: OperationArgumentKind
    required: bool = True


@dataclass(frozen=True, slots=True)
class FormatOperationDescriptor:
    """One validated immutable operation record (operation_registry.rs:50-61)."""

    id: FormatOperationId
    target_role: str
    arguments: tuple[OperationArgumentDescriptor, ...]
    support: OperationSupport

    def to_string(self) -> str:
        """Canonical ``id@version`` spelling."""
        return self.id.to_string()


@dataclass(frozen=True, slots=True)
class PropertiesFormatOperationRegistry:
    """Validated operation registry for one exact Java Properties profile
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
) -> FormatOperationDescriptor:
    operation_id, version = id_string.rsplit("@", 1)
    return FormatOperationDescriptor(
        id=FormatOperationId.new(operation_id, int(version)),
        target_role=target_role,
        arguments=tuple(
            OperationArgumentDescriptor(name, kind) for name, kind in arguments
        ),
        support=OperationSupport.SUPPORTED,
    )


def _argument(name: str, kind: OperationArgumentKind) -> tuple[str, OperationArgumentKind]:
    return (name, kind)


def descriptors() -> tuple[FormatOperationDescriptor, ...]:
    """The frozen five-record descriptor set
    (operation_registry.rs:16-48)."""
    return (
        _descriptor(
            "java-properties.edit.insert-property@1",
            "java-properties.document",
            [
                _argument("key", OperationArgumentKind.PORTABLE_VALUE),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
        ),
        _descriptor(
            "java-properties.edit.remove-property@1",
            "java-properties.property",
            [],
        ),
        _descriptor(
            "java-properties.edit.rename-property@1",
            "java-properties.property",
            [_argument("key", OperationArgumentKind.PORTABLE_VALUE)],
        ),
        _descriptor(
            "java-properties.edit.replace-literal-value@1",
            "java-properties.property",
            [_argument("literal", OperationArgumentKind.EXACT_BYTES)],
        ),
        _descriptor(
            "java-properties.edit.replace-semantic-value@1",
            "java-properties.property",
            [_argument("value", OperationArgumentKind.PORTABLE_VALUE)],
        ),
    )


def format_operation_registry(
    profile: PropertiesProfile,
) -> PropertiesFormatOperationRegistry:
    """Returns the validated operation registry for one exact Java
    Properties profile (operation_registry.rs:9-14)."""
    return PropertiesFormatOperationRegistry(
        profile=profile.id(),
        operations=descriptors(),
    )
