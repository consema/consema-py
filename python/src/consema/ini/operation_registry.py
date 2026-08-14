"""The INI-family format operation registry (8 frozen records per profile).

Authority: https://github.com/consema/consema-rs/blob/main/consema-ini/src/operation_registry.rs — the exact
operation ids, target roles, argument schemas, and support classifications;
the surface is frozen by the registry test
(operation_registry.rs: exactly six Supported structural operations
per profile and eight total records) and by RFC 0009 §12
(https://github.com/consema/consema/blob/main/docs/rfcs/0009-ini-family-profiles-v1.md: all three profiles
publish the same frozen eight-operation surface, independently typed per
profile) and the vector case registry.frozen-eight-operation-surface
(conformance/vectors/ini-v1.json:136-139: direct_structural 6).

Frozen records (operation_registry.rs):

1. ini.edit.insert-section@1          ini.document   name(String), placement(Placement)   Supported
2. ini.edit.remove-section@1          ini.section    (no arguments)                        Supported
3. ini.edit.rename-section@1          ini.section    name(String)                          Supported
4. ini.edit.insert-entry@1            ini.section    key(String), value(String), placement(Placement)  Supported
5. ini.edit.remove-entry@1            ini.entry      (no arguments)                        Supported
6. ini.edit.rename-entry@1            ini.entry      key(String)                           Supported
7. ini.edit.replace-semantic-value@1  ini.entry      value(String), representation_policy(RepresentationPolicy)  ExistingTypedCapability
8. ini.edit.replace-literal-value@1   ini.entry      literal(ExactBytes)                   ExistingTypedCapability

Sharing an operation name does not share delimiter, quote, continuation,
comment ownership, case-collision, or encoding behavior between profiles
(RFC 0009 §12, https://github.com/consema/consema/blob/main/docs/rfcs/0009-ini-family-profiles-v1.md).

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId); the EditPlan operation-metadata
matching rule requires that form (edit_plan.rs).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.ini.kinds import IniProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs)."""

    SUPPORTED = "Supported"
    EXISTING_TYPED_CAPABILITY = "ExistingTypedCapability"


class OperationArgumentKind(enum.Enum):
    """Argument value kinds (consema-document operation registry)."""

    STRING = "String"
    PLACEMENT = "Placement"
    REPRESENTATION_POLICY = "RepresentationPolicy"
    EXACT_BYTES = "ExactBytes"


@dataclass(frozen=True, slots=True)
class OperationArgumentDescriptor:
    """One operation argument schema (operation_registry.rs)."""

    name: str
    kind: OperationArgumentKind
    required: bool = True


@dataclass(frozen=True, slots=True)
class FormatOperationDescriptor:
    """One validated immutable operation record (operation_registry.rs)."""

    id: FormatOperationId
    target_role: str
    arguments: tuple[OperationArgumentDescriptor, ...]
    support: OperationSupport

    def to_string(self) -> str:
        """Canonical ``id@version`` spelling."""
        return self.id.to_string()


@dataclass(frozen=True, slots=True)
class IniFormatOperationRegistry:
    """Validated operation registry for one exact INI-family profile
    (operation_registry.rs)."""

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
    """The frozen eight-record descriptor set (operation_registry.rs)."""
    return (
        _descriptor(
            "ini.edit.insert-section@1",
            "ini.document",
            [
                _argument("name", OperationArgumentKind.STRING),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.remove-section@1",
            "ini.section",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.rename-section@1",
            "ini.section",
            [_argument("name", OperationArgumentKind.STRING)],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.insert-entry@1",
            "ini.section",
            [
                _argument("key", OperationArgumentKind.STRING),
                _argument("value", OperationArgumentKind.STRING),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.remove-entry@1",
            "ini.entry",
            [],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.rename-entry@1",
            "ini.entry",
            [_argument("key", OperationArgumentKind.STRING)],
            OperationSupport.SUPPORTED,
        ),
        _descriptor(
            "ini.edit.replace-semantic-value@1",
            "ini.entry",
            [
                _argument("value", OperationArgumentKind.STRING),
                _argument(
                    "representation_policy", OperationArgumentKind.REPRESENTATION_POLICY
                ),
            ],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
        _descriptor(
            "ini.edit.replace-literal-value@1",
            "ini.entry",
            [_argument("literal", OperationArgumentKind.EXACT_BYTES)],
            OperationSupport.EXISTING_TYPED_CAPABILITY,
        ),
    )


def format_operation_registry(profile: IniProfile) -> IniFormatOperationRegistry:
    """Returns the validated operation registry for one exact INI-family
    profile (operation_registry.rs).

    Operations are presented in frozen sorted-id order — the vector case
    registry.frozen-eight-operation-surface (ini-v1.json:138) and the
    registry test (operation_registry.rs) pin the sorted list.
    """
    return IniFormatOperationRegistry(
        profile=profile.id(),
        operations=tuple(sorted(descriptors(), key=lambda descriptor: descriptor.to_string())),
    )
