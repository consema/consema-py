"""The frozen XML format operation registry.

Authority:

- RFC 0012 §11 (docs/rfcs/0012-xml-1.0-safe-profile-v1.md:376-387) freezes
  the eight operation ids (xml.edit.replace-text@1, insert-attribute@1,
  remove-attribute@1, rename-attribute@1, set-attribute-value@1,
  insert-element@1, remove-element@1, rename-element@1).
- The exact descriptors transcribe crates/consema-xml/src/
  operation_registry.rs:16-89 (ids, target roles xml.text@1 /
  xml.element@1 / xml.attribute@1, argument kinds String and Placement,
  and the Support classification); the structural surface test
  operation_registry.rs:100-124 pins the frozen eight-operation surface.
- The argument-kind spellings follow consema-document
  operation_registry.rs (OperationArgumentKind) and are cross-checked by
  go/xml operation_test.go.
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
    (operation_registry.rs:77-89)."""

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
    """The validated operation registry for one exact XML profile
    (operation_registry.rs:11-14)."""

    profile: ProfileId
    _operations: tuple[OperationDescriptor, ...] = field(default_factory=tuple)

    def profile_id(self) -> ProfileId:
        return self.profile

    def operations(self) -> tuple[OperationDescriptor, ...]:
        return self._operations


def format_operation_registry(profile_id: ProfileId) -> FormatOperationRegistry:
    """Returns the validated operation registry for the frozen XML profile
    (operation_registry.rs:9-14); the profile must be ``xml.1.0-safe@1``."""
    if (profile_id.id, profile_id.version) != ("xml.1.0-safe", 1):
        raise ValueError("unsupported XML profile for the frozen operation registry")
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


# The frozen eight descriptors in the registry's published order,
# operation_registry.rs:16-74 (the surface test at 100-124 pins the
# sorted id list).
_DESCRIPTORS: tuple[OperationDescriptor, ...] = (
    _descriptor(
        "xml.edit.insert-attribute",
        "xml.element@1",
        [
            _argument("name", OperationArgumentKind.STRING),
            _argument("value", OperationArgumentKind.STRING),
            _argument("placement", OperationArgumentKind.PLACEMENT),
        ],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.insert-element",
        "xml.element@1",
        [
            _argument("name", OperationArgumentKind.STRING),
            _argument("content", OperationArgumentKind.STRING),
            _argument("placement", OperationArgumentKind.PLACEMENT),
        ],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.remove-attribute",
        "xml.attribute@1",
        [],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.remove-element",
        "xml.element@1",
        [],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.rename-attribute",
        "xml.attribute@1",
        [_argument("name", OperationArgumentKind.STRING)],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.rename-element",
        "xml.element@1",
        [_argument("name", OperationArgumentKind.STRING)],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.replace-text",
        "xml.text@1",
        [_argument("text", OperationArgumentKind.STRING)],
        OperationSupport.SUPPORTED,
    ),
    _descriptor(
        "xml.edit.set-attribute-value",
        "xml.attribute@1",
        [_argument("value", OperationArgumentKind.STRING)],
        OperationSupport.SUPPORTED,
    ),
)
