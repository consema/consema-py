"""The plist-family format operation registry (6 frozen records per profile).

Authority: crates/consema-plist/src/operation_registry.rs:20-83 — the exact
operation ids, target roles, argument schemas, and support classifications;
the surface is frozen by the registry test (operation_registry.rs:104-132:
exactly six Supported structural operations per profile) and by RFC 0013
§11 (docs/rfcs/0013-plist-family-profiles-v1.md:685-695: both profiles
publish the same six snapshot-bound operations, independently typed per
profile).

Frozen records (operation_registry.rs:21-82):

1. plist.edit.set-value@1            plist.value      path(NodeRef), value(PortableValue)     Supported
2. plist.edit.insert-dict-entry@1   plist.value      path(NodeRef), key(String), value(PortableValue), placement(Placement)  Supported
3. plist.edit.remove-dict-entry@1   plist.dict-entry path(NodeRef), key(String), occurrence(NodeRef)  Supported
4. plist.edit.rename-dict-key@1     plist.dict-entry path(NodeRef), from(String), occurrence(NodeRef), to(String)  Supported
5. plist.edit.insert-array-element@1 plist.value     path(NodeRef), index(NodeRef), value(PortableValue)  Supported
6. plist.edit.remove-array-element@1 plist.array-element  path(NodeRef), index(NodeRef)  Supported

Sharing an operation name does not share XML edit vs. binary structural
edit behavior (RFC 0013 §11: XML edits replace text or elements only within
operation-owned spans; binary edits rewrite the target object's marker and
payload or the owning container's reference block, offset table, and
trailer when sizes change).

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId); the EditPlan operation-metadata
matching rule requires that form (edit_plan.rs:84-121).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.plist.kinds import PlistProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs:25-33, 71-78)."""

    SUPPORTED = "Supported"


class OperationArgumentKind(enum.Enum):
    """Argument value kinds (consema-document operation registry)."""

    NODE_REF = "NodeRef"
    PORTABLE_VALUE = "PortableValue"
    STRING = "String"
    PLACEMENT = "Placement"


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
class PlistFormatOperationRegistry:
    """Validated operation registry for one exact plist profile
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
    """The frozen six-record descriptor set (operation_registry.rs:20-83)."""
    return (
        _descriptor(
            "plist.edit.set-value@1",
            "plist.value",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "plist.edit.insert-dict-entry@1",
            "plist.value",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("key", OperationArgumentKind.STRING),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "plist.edit.remove-dict-entry@1",
            "plist.dict-entry",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("key", OperationArgumentKind.STRING),
                _argument("occurrence", OperationArgumentKind.NODE_REF),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "plist.edit.rename-dict-key@1",
            "plist.dict-entry",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("from", OperationArgumentKind.STRING),
                _argument("occurrence", OperationArgumentKind.NODE_REF),
                _argument("to", OperationArgumentKind.STRING),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "plist.edit.insert-array-element@1",
            "plist.value",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("index", OperationArgumentKind.NODE_REF),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "plist.edit.remove-array-element@1",
            "plist.array-element",
            [
                _argument("path", OperationArgumentKind.NODE_REF),
                _argument("index", OperationArgumentKind.NODE_REF),
            ],
            OperationSupport.SUPPORTED,
        ),
    )


def format_operation_registry(profile: PlistProfile) -> PlistFormatOperationRegistry:
    """Returns the validated operation registry for one exact plist profile
    (operation_registry.rs:9-14). The operations are ordered by canonical id
    (the frozen surface test, operation_registry.rs:104-132)."""
    return PlistFormatOperationRegistry(
        profile=profile.id(),
        operations=tuple(sorted(descriptors(), key=lambda d: d.to_string())),
    )
