"""The JSON-family format operation registry (8 frozen records).

Authority: crates/consema-json/src/operation_registry.rs:16-98 — the exact
operation ids, target roles, argument schemas, and support classifications;
the structural surface is frozen by the registry test
(operation_registry.rs:105-129: exactly six Supported structural operations
for every profile and eight total records) and RFC 0005 §10
(docs/rfcs/0005-...:220-241: move-member@1 raises the registry to eight).

Frozen records (operation_registry.rs:18-79):

1. json.edit.insert-member@1         json.object        name(String), value(PortableValue), placement(Placement)   Supported
2. json.edit.remove-member@1         json.object-member (no arguments)                                              Supported
3. json.edit.move-member@1           json.object-member placement(Placement)                                        Supported
4. json.edit.rename-member@1         json.object-member name(String)                                                Supported
5. json.edit.insert-array-element@1  json.array         value(PortableValue), placement(Placement)                  Supported
6. json.edit.remove-array-element@1  json.array-element (no arguments)                                              Supported
7. json.edit.replace-scalar-semantic@1  json.scalar     value(PortableValue), representation_policy(RepresentationPolicy)  ExistingTypedCapability
8. json.edit.replace-scalar-literal@1   json.scalar     literal(ExactBytes)                                         ExistingTypedCapability

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId, operation_registry.rs:10-42); the
EditPlan operation-metadata matching rule requires that form
(edit_plan.rs:84-121).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.json.kinds import JsonProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs:26-28, 71-78)."""

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
    """One operation argument schema (operation_registry.rs:96-98)."""

    name: str
    kind: OperationArgumentKind
    required: bool = True


@dataclass(frozen=True, slots=True)
class FormatOperationDescriptor:
    """One validated immutable operation record (operation_registry.rs:82-94)."""

    id: FormatOperationId
    target_role: str
    arguments: tuple[OperationArgumentDescriptor, ...]
    support: OperationSupport

    def to_string(self) -> str:
        """Canonical ``id@version`` spelling."""
        return self.id.to_string()


@dataclass(frozen=True, slots=True)
class JsonFormatOperationRegistry:
    """Validated operation registry for one exact JSON-family profile
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
    """The frozen eight-record descriptor set (operation_registry.rs:16-80)."""
    return (
        _descriptor(
            "json.edit.insert-member@1",
            "json.object",
            [
                _argument("name", OperationArgumentKind.STRING),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.remove-member@1",
            "json.object-member",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.move-member@1",
            "json.object-member",
            [_argument("placement", OperationArgumentKind.PLACEMENT)],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.rename-member@1",
            "json.object-member",
            [_argument("name", OperationArgumentKind.STRING)],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.insert-array-element@1",
            "json.array",
            [
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.remove-array-element@1",
            "json.array-element",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "json.edit.replace-scalar-semantic@1",
            "json.scalar",
            [
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("representation_policy", OperationArgumentKind.REPRESENTATION_POLICY),
            ],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
        _descriptor(
            "json.edit.replace-scalar-literal@1",
            "json.scalar",
            [_argument("literal", OperationArgumentKind.EXACT_BYTES)],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
    )


def format_operation_registry(profile: JsonProfile) -> JsonFormatOperationRegistry:
    """Returns the validated operation registry for one exact JSON-family
    profile (operation_registry.rs:9-14)."""
    return JsonFormatOperationRegistry(
        profile=profile.id(),
        operations=descriptors(),
    )
