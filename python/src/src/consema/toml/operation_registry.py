"""The frozen TOML format operation registry.

Authority:

- RFC 0004 §10 (docs/rfcs/0004-materialization-conversion-and-structural-
  edit-v1.md:244-269) freezes the five structural operation ids
  (toml.edit.insert-entry@1, remove-entry@1, rename-entry@1,
  insert-array-element@1, remove-array-element@1) and declares the two
  existing scalar capabilities (replace-scalar-semantic@1,
  replace-scalar-literal@1) through the registry as
  ExistingTypedCapability.
- The exact seven descriptors transcribe
  crates/consema-toml/src/operation_registry.rs:16-74 (ids, target roles
  toml.table-item@1 / toml.entry@1 / toml.array-item@1 /
  toml.array-element@1 / toml.scalar-item@1, argument kinds String /
  PortableValue / Placement / RepresentationPolicy / ExactBytes, and the
  support classification Supported / ExistingTypedCapability); the
  structural surface test operation_registry.rs:94-119 pins the five
  Supported operations and the total count of seven.
- The argument-kind spellings follow consema-document operation_registry.rs
  (OperationArgumentKind) and are cross-checked by go/toml
  operation_registry.go:24-37.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from consema.document.ids import FormatOperationId, ProfileId


class OperationSupport(enum.Enum):
    """Closed operation support classification
    (consema-document operation_registry.rs)."""

    SUPPORTED = "Supported"
    EXISTING_TYPED_CAPABILITY = "ExistingTypedCapability"


class OperationArgumentKind(enum.Enum):
    """Closed operation argument kind."""

    STRING = "String"
    PORTABLE_VALUE = "PortableValue"
    PLACEMENT = "Placement"
    REPRESENTATION_POLICY = "RepresentationPolicy"
    EXACT_BYTES = "ExactBytes"


@dataclass(frozen=True, slots=True)
class OperationArgumentDescriptor:
    """One required operation argument."""

    name: str
    kind: OperationArgumentKind

    @classmethod
    def new(cls, name: str, kind: OperationArgumentKind) -> OperationArgumentDescriptor:
        return cls(name=name, kind=kind)


@dataclass(frozen=True, slots=True)
class OperationDescriptor:
    """One validated format operation descriptor
    (consema-toml/src/operation_registry.rs:76-92)."""

    id: FormatOperationId
    target_role: str
    arguments: tuple[OperationArgumentDescriptor, ...] = field(default_factory=tuple)
    support: OperationSupport = OperationSupport.SUPPORTED

    @classmethod
    def new(
        cls,
        id: FormatOperationId,
        target_role: str,
        arguments: list[OperationArgumentDescriptor],
        support: OperationSupport,
    ) -> OperationDescriptor:
        return cls(id=id, target_role=target_role, arguments=tuple(arguments), support=support)


@dataclass(frozen=True, slots=True)
class FormatOperationRegistry:
    """The validated operation registry for one exact TOML profile
    (operation_registry.rs:9-14)."""

    profile: ProfileId
    _operations: tuple[OperationDescriptor, ...] = field(default_factory=tuple)

    def profile_id(self) -> ProfileId:
        return self.profile

    def operations(self) -> tuple[OperationDescriptor, ...]:
        return self._operations


def format_operation_registry(profile_id: ProfileId) -> FormatOperationRegistry:
    """Returns the validated operation registry for the frozen TOML
    profile (operation_registry.rs:11-14); the profile must be
    ``toml.1.0@1``."""
    if (profile_id.id, profile_id.version) != ("toml.1.0", 1):
        raise ValueError("unsupported TOML profile for the frozen operation registry")
    return FormatOperationRegistry(
        profile=profile_id,
        _operations=_DESCRIPTORS,
    )


def _argument(name: str, kind: OperationArgumentKind) -> OperationArgumentDescriptor:
    return OperationArgumentDescriptor.new(name, kind)


def _descriptor(
    id: str,
    target_role: str,
    arguments: list[OperationArgumentDescriptor],
    support: OperationSupport,
) -> OperationDescriptor:
    return OperationDescriptor.new(
        FormatOperationId.new(id, 1), target_role, arguments, support
    )


# The frozen seven descriptors, operation_registry.rs:16-74, in the
# canonical emission order: FormatOperationRegistry sorts by operation id
# (crates/consema-document/src/operation_registry.rs:234), so the
# published surface is alphabetical (insert-array-element, insert-entry,
# remove-array-element, remove-entry, rename-entry, replace-scalar-literal,
# replace-scalar-semantic).
_DESCRIPTORS: tuple[OperationDescriptor, ...] = (
    _descriptor(
        "toml.edit.insert-array-element",
        "toml.array-item@1",
        [
            _argument("value", OperationArgumentKind.PORTABLE_VALUE),
            _argument("placement", OperationArgumentKind.PLACEMENT),
        ],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "toml.edit.insert-entry",
        "toml.table-item@1",
        [
            _argument("key", OperationArgumentKind.STRING),
            _argument("value", OperationArgumentKind.PORTABLE_VALUE),
            _argument("placement", OperationArgumentKind.PLACEMENT),
        ],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "toml.edit.remove-array-element",
        "toml.array-element@1",
        [],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "toml.edit.remove-entry",
        "toml.entry@1",
        [],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "toml.edit.rename-entry",
        "toml.entry@1",
        [_argument("key", OperationArgumentKind.STRING)],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "toml.edit.replace-scalar-literal",
        "toml.scalar-item@1",
        [_argument("literal", OperationArgumentKind.EXACT_BYTES)],
        OperationSupport.EXISTING_TYPED_CAPABILITY,
    ),
    _descriptor(
        "toml.edit.replace-scalar-semantic",
        "toml.scalar-item@1",
        [
            _argument("value", OperationArgumentKind.PORTABLE_VALUE),
            _argument("representation_policy", OperationArgumentKind.REPRESENTATION_POLICY),
        ],
        OperationSupport.EXISTING_TYPED_CAPABILITY,
    ),
)
