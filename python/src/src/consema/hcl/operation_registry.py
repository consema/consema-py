"""The HCL-family format operation registry (RFC 0014 §10).

Authority: crates/consema-hcl/src/operation_registry.rs — the exact
operation ids, target roles, argument schemas, and support classifications.
`hcl.native@1` publishes all six structural operations; `hcl.tfvars@1`
publishes the four attribute operations only, because the tfvars
restriction admits no block (RFC 0014 §5, §10). The frozen surface is
pinned by the registry tests (operation_registry.rs:100-157).

Frozen records (operation_registry.rs:26-80):

1. hcl.edit.insert-attribute@1     hcl.body      name(String), value(PortableValue), placement(Placement)   Supported
2. hcl.edit.remove-attribute@1     hcl.attribute (no arguments)                                              Supported
3. hcl.edit.rename-attribute@1     hcl.attribute name(String)                                                Supported
4. hcl.edit.set-attribute-value@1  hcl.attribute value(PortableValue)                                        Supported
5. hcl.edit.insert-block@1         hcl.body      type(String), labels(String), attributes(PortableValue), placement(Placement)  Supported (native only)
6. hcl.edit.remove-block@1         hcl.block     (no arguments)                                              Supported (native only)

Operation ids/versions and the ``id@version`` display form are frozen by
consema-document (FormatOperationId); the EditPlan operation-metadata
matching rule requires that form (edit_plan.py:84-121).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from consema.document.ids import FormatOperationId, ProfileId
from consema.hcl.kinds import HclProfile


class OperationSupport(enum.Enum):
    """Support classification (operation_registry.rs:26-28)."""

    SUPPORTED = "Supported"


class OperationArgumentKind(enum.Enum):
    """Argument value kinds (consema-document operation registry)."""

    STRING = "String"
    PORTABLE_VALUE = "PortableValue"
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
class HclFormatOperationRegistry:
    """Validated operation registry for one exact HCL profile
    (operation_registry.rs:16-23)."""

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


def _attribute_descriptors() -> tuple[FormatOperationDescriptor, ...]:
    """The attribute-only surface of `hcl.tfvars@1`
    (operation_registry.rs:49-80)."""
    return (
        _descriptor(
            "hcl.edit.insert-attribute@1",
            "hcl.body",
            [
                _argument("name", OperationArgumentKind.STRING),
                _argument("value", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
        ),
        _descriptor("hcl.edit.remove-attribute@1", "hcl.attribute", []),
        _descriptor(
            "hcl.edit.rename-attribute@1",
            "hcl.attribute",
            [_argument("name", OperationArgumentKind.STRING)],
        ),
        _descriptor(
            "hcl.edit.set-attribute-value@1",
            "hcl.attribute",
            [_argument("value", OperationArgumentKind.PORTABLE_VALUE)],
        ),
    )


def _native_descriptors() -> tuple[FormatOperationDescriptor, ...]:
    """The full six-operation surface of `hcl.native@1`
    (operation_registry.rs:26-46)."""
    return _attribute_descriptors() + (
        _descriptor(
            "hcl.edit.insert-block@1",
            "hcl.body",
            [
                _argument("type", OperationArgumentKind.STRING),
                _argument("labels", OperationArgumentKind.STRING),
                _argument("attributes", OperationArgumentKind.PORTABLE_VALUE),
                _argument("placement", OperationArgumentKind.PLACEMENT),
            ],
        ),
        _descriptor("hcl.edit.remove-block@1", "hcl.block", []),
    )


def format_operation_registry(profile: HclProfile) -> HclFormatOperationRegistry:
    """Returns the validated operation registry for one exact HCL profile
    (operation_registry.rs:16-23).

    The registry presents its operations in the frozen sorted order pinned
    by the Rust registry test (operation_registry.rs:106-113):
    insert-attribute, insert-block, remove-attribute, remove-block,
    rename-attribute, set-attribute-value for `hcl.native@1`.
    """
    if profile is HclProfile.NATIVE_V1:
        operations = _native_descriptors()
    else:
        operations = _attribute_descriptors()
    return HclFormatOperationRegistry(
        profile=profile.id(),
        operations=tuple(sorted(operations, key=lambda operation: operation.to_string())),
    )
