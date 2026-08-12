"""Transferable Profile / Capability registry records and the registry manifest.

Authority: crates/consema-protocol/src/registry.rs (ProfileReference,
ProfileDescriptor, CapabilityDeclaration) and registry_manifest.rs
(RegistryManifest); Go (go/protocol/registry_descriptor.go) is a
cross-reference only.

Records:
- `core.profile-descriptor@1`: schema, format_family_id, format_family_version,
  profile_id, profile_version, base_profile, differences, required_capabilities;
- `core.capability-declaration@1`: schema, capability_id, capability_version,
  support, preconditions, verification, suite_id;
- `core.registry-manifest@1`: schema, semantic_model, contracts, error_codes.
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.protocol.contract import (
    ContractId,
    ContractRegistry,
    ContractStability,
    validate_namespace,
)
from consema.protocol.error_registry import (
    DiagnosticCategory,
    ErrorCodeRegistry,
    parse_category,
    validate_versioned_code,
)
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    integer_value,
    nullable_string,
    optional_string,
    schema_fields,
    sequence_of,
    string_map_from_object,
    string_map_object,
    string_of,
    unsigned32,
)


# --------------------------------------------------------------------------
# ProfileReference / ProfileDescriptor
# --------------------------------------------------------------------------

class ProfileReference:
    """A versioned reference to a Profile (registry.rs:14-46).

    Unlike contract IDs, profile IDs may contain numeric segments
    (validate_namespace with require_dot=True).
    """

    __slots__ = ("id", "version")

    def __init__(self, id: str, version: int):
        validate_namespace(id, require_dot=True, path="$.profile.id")
        if version == 0:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.profile.version",
                "version must be non-zero",
            )
        self.id = id
        self.version = version

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProfileReference):
            return NotImplemented
        return self.id == other.id and self.version == other.version

    def __hash__(self) -> int:
        return hash((self.id, self.version))

    def __repr__(self) -> str:
        return f"ProfileReference({self.id!r}@{self.version})"


class ProfileDescriptor:
    """An immutable language profile registry descriptor (registry.rs:48-250)."""

    __slots__ = (
        "format_family_id",
        "format_family_version",
        "profile_id",
        "profile_version",
        "base_profile",
        "differences",
        "required_capabilities",
    )

    def __init__(
        self,
        format_family_id: str,
        format_family_version: int,
        profile_id: str,
        profile_version: int,
        base_profile: ProfileReference | None,
        differences: list[str],
        required_capabilities: list["CapabilityId"],
    ):
        validate_namespace(format_family_id, require_dot=False, path="$.format_family_id")
        validate_namespace(profile_id, require_dot=True, path="$.profile_id")
        if format_family_version == 0 or profile_version == 0:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$",
                "family and profile versions must be non-zero",
            )
        for difference in differences:
            validate_namespace(difference, require_dot=True, path="$.differences")
        for capability in required_capabilities:
            ContractId(capability.namespace, capability.version)
        sorted_differences = sorted(differences)
        for left, right in zip(sorted_differences, sorted_differences[1:]):
            if left == right:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    "$.differences",
                    "difference IDs must be unique",
                )
        sorted_capabilities = sorted(
            required_capabilities,
            key=lambda capability: (capability.namespace, capability.version),
        )
        for left, right in zip(sorted_capabilities, sorted_capabilities[1:]):
            if left.namespace == right.namespace and left.version == right.version:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    "$.required_capabilities",
                    "capability IDs must be unique",
                )
        self.format_family_id = format_family_id
        self.format_family_version = format_family_version
        self.profile_id = profile_id
        self.profile_version = profile_version
        self.base_profile = base_profile
        self.differences = sorted_differences
        self.required_capabilities = sorted_capabilities

    def to_value(self) -> PortableValue:
        differences = [PortableValue.string(item) for item in self.differences]
        capabilities = [
            _reference_value(capability.namespace, capability.version)
            for capability in self.required_capabilities
        ]
        base_profile = PortableValue.null()
        if self.base_profile is not None:
            base_profile = _reference_value(self.base_profile.id, self.base_profile.version)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.profile-descriptor@1")),
                ("format_family_id", PortableValue.string(self.format_family_id)),
                ("format_family_version", integer_value(self.format_family_version)),
                ("profile_id", PortableValue.string(self.profile_id)),
                ("profile_version", integer_value(self.profile_version)),
                ("base_profile", base_profile),
                ("differences", PortableValue.sequence(differences)),
                ("required_capabilities", PortableValue.sequence(capabilities)),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue) -> "ProfileDescriptor":
        fields = schema_fields(
            value,
            "core.profile-descriptor@1",
            ["schema", "format_family_id", "format_family_version", "profile_id",
             "profile_version", "base_profile", "differences", "required_capabilities"],
            "$",
        )
        format_family_id = string_of(fields[1], "$.format_family_id")
        format_family_version = unsigned32(fields[2], "$.format_family_version")
        profile_id = string_of(fields[3], "$.profile_id")
        profile_version = unsigned32(fields[4], "$.profile_version")
        base_profile = None
        if fields[5].kind is not Kind.NULL:
            base_profile = _parse_profile_reference(fields[5], "$.base_profile")
        differences = []
        for index, item in enumerate(sequence_of(fields[6], "$.differences")):
            differences.append(string_of(item, f"$.differences[{index}]"))
        capabilities = []
        for index, item in enumerate(sequence_of(fields[7], "$.required_capabilities")):
            path = f"$.required_capabilities[{index}]"
            contract = _parse_contract_reference(item, path)
            capabilities.append(CapabilityId(contract.id, contract.version))
        return ProfileDescriptor(
            format_family_id, format_family_version, profile_id, profile_version,
            base_profile, differences, capabilities,
        )


# --------------------------------------------------------------------------
# CapabilityId / CapabilitySet / CapabilityDeclaration
# --------------------------------------------------------------------------

class CapabilityId:
    """A stable namespaced capability contract."""

    __slots__ = ("namespace", "version")

    def __init__(self, namespace: str, version: int):
        self.namespace = namespace
        self.version = version

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CapabilityId):
            return NotImplemented
        return self.namespace == other.namespace and self.version == other.version

    def __hash__(self) -> int:
        return hash((self.namespace, self.version))

    def __lt__(self, other: "CapabilityId") -> bool:
        return (self.namespace, self.version) < (other.namespace, other.version)

    def __repr__(self) -> str:
        return f"CapabilityId({self.namespace!r}@{self.version})"


class CapabilitySet:
    """A deterministic set of capabilities available to an operation."""

    def __init__(self):
        self._capabilities: dict[str, CapabilityId] = {}

    def insert(self, capability: CapabilityId) -> bool:
        key = f"{capability.namespace}@{capability.version}"
        if key in self._capabilities:
            return False
        self._capabilities[key] = capability
        return True

    def contains(self, capability: CapabilityId) -> bool:
        key = f"{capability.namespace}@{capability.version}"
        return key in self._capabilities

    def iterate(self) -> list[CapabilityId]:
        """Visits the capabilities in stable identifier order."""
        return sorted(self._capabilities.values())


class SupportKind(enum.Enum):
    CONFORMANT = "Conformant"
    CONDITIONAL = "Conditional"
    UNSUPPORTED = "Unsupported"


class Precondition:
    """One machine-readable conditional-support precondition."""

    __slots__ = ("key", "value")

    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Precondition):
            return NotImplemented
        return self.key == other.key and self.value == other.value

    def __hash__(self) -> int:
        return hash((self.key, self.value))


class ImplementationSupport:
    """The declared support state of one capability."""

    __slots__ = ("kind", "preconditions")

    def __init__(self, kind: SupportKind, preconditions: list[Precondition]):
        self.kind = kind
        self.preconditions = preconditions


class VerificationStatus(enum.Enum):
    VERIFIED = "Verified"
    SELF_DECLARED = "SelfDeclared"
    UNVERIFIED = "Unverified"


def parse_verification_status(name: str) -> VerificationStatus:
    try:
        return VerificationStatus(name)
    except ValueError:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, "$.verification", "unknown verification status"
        ) from None


class CapabilityDeclaration:
    """One implementation's support and verification claim for a capability
    (registry.rs:252-439)."""

    __slots__ = ("capability", "support", "verification", "suite_id")

    def __init__(
        self,
        capability: CapabilityId,
        support: ImplementationSupport,
        verification: VerificationStatus,
        suite_id: str | None,
    ):
        ContractId(capability.namespace, capability.version)
        if support.kind is SupportKind.CONDITIONAL and not support.preconditions:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.preconditions",
                "Conditional support requires preconditions",
            )
        if support.kind is not SupportKind.CONDITIONAL and support.preconditions:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.preconditions",
                "only Conditional support may carry preconditions",
            )
        seen: set[str] = set()
        for precondition in support.preconditions:
            if precondition.key in seen:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    "$.preconditions",
                    "precondition keys must be unique",
                )
            seen.add(precondition.key)
        if verification is VerificationStatus.VERIFIED:
            if suite_id is None:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE, "$.suite_id", "Verified requires a suite ID"
                )
            validate_namespace(suite_id, require_dot=True, path="$.suite_id")
        elif suite_id is not None:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.suite_id",
                "only Verified may name a suite",
            )
        self.capability = capability
        self.support = support
        self.verification = verification
        self.suite_id = suite_id

    def to_value(self) -> PortableValue:
        support_name = "Conformant"
        preconditions: dict[str, str] = {}
        if self.support.kind is SupportKind.CONDITIONAL:
            support_name = "Conditional"
            preconditions = {p.key: p.value for p in self.support.preconditions}
        elif self.support.kind is SupportKind.UNSUPPORTED:
            support_name = "Unsupported"
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.capability-declaration@1")),
                ("capability_id", PortableValue.string(self.capability.namespace)),
                ("capability_version", integer_value(self.capability.version)),
                ("support", PortableValue.string(support_name)),
                ("preconditions", string_map_object(preconditions)),
                ("verification", PortableValue.string(self.verification.value)),
                ("suite_id", nullable_string(self.suite_id)),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue) -> "CapabilityDeclaration":
        fields = schema_fields(
            value,
            "core.capability-declaration@1",
            ["schema", "capability_id", "capability_version", "support",
             "preconditions", "verification", "suite_id"],
            "$",
        )
        namespace = string_of(fields[1], "$.capability_id")
        version = unsigned32(fields[2], "$.capability_version")
        precondition_map = string_map_from_object(fields[4], "$.preconditions")
        preconditions = [
            Precondition(key, precondition_map[key]) for key in sorted(precondition_map)
        ]
        support_name = string_of(fields[3], "$.support")
        if support_name == "Conformant" and not preconditions:
            support = ImplementationSupport(SupportKind.CONFORMANT, [])
        elif support_name == "Conditional":
            support = ImplementationSupport(SupportKind.CONDITIONAL, preconditions)
        elif support_name == "Unsupported" and not preconditions:
            support = ImplementationSupport(SupportKind.UNSUPPORTED, [])
        else:
            raise protocol_error(
                ProtocolErrorKind.INVALID_VALUE,
                "$.support",
                "invalid support/preconditions combination",
            )
        verification = parse_verification_status(string_of(fields[5], "$.verification"))
        suite_id = optional_string(fields[6], "$.suite_id")
        return CapabilityDeclaration(
            CapabilityId(namespace, version), support, verification, suite_id
        )


# --------------------------------------------------------------------------
# RegistryManifest
# --------------------------------------------------------------------------

class ContractManifestEntry:
    """One owned contract entry of a registry manifest."""

    __slots__ = ("contract", "stability")

    def __init__(self, contract: ContractId, stability: ContractStability):
        self.contract = contract
        self.stability = stability


class ErrorCodeManifestEntry:
    """One owned error-code entry of a registry manifest."""

    __slots__ = ("code", "category", "introduced", "description")

    def __init__(
        self,
        code: str,
        category: DiagnosticCategory,
        introduced: str,
        description: str,
    ):
        self.code = code
        self.category = category
        self.introduced = introduced
        self.description = description


class RegistryManifest:
    """The `core.registry-manifest@1` record of one semantic-model contract set."""

    __slots__ = ("semantic_model", "contracts", "error_codes")

    def __init__(
        self,
        semantic_model: ContractId,
        contracts: list[ContractManifestEntry],
        error_codes: list[ErrorCodeManifestEntry],
    ):
        for left, right in zip(contracts, contracts[1:]):
            if (left.contract.id, left.contract.version) >= (
                right.contract.id,
                right.contract.version,
            ):
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE, "$", "manifest records must be sorted and unique"
                )
        for left, right in zip(error_codes, error_codes[1:]):
            if left.code >= right.code:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE, "$", "manifest records must be sorted and unique"
                )
        for entry in error_codes:
            validate_versioned_code(entry.code, "$.error_codes.code")
            if not entry.introduced or not entry.description:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    "$.error_codes",
                    "error-code metadata cannot be empty",
                )
        self.semantic_model = semantic_model
        self.contracts = contracts
        self.error_codes = error_codes

    @staticmethod
    def build(
        semantic_model_version: int,
        contract_registry: ContractRegistry,
        error_code_registry: ErrorCodeRegistry,
    ) -> "RegistryManifest":
        contracts = [
            ContractManifestEntry(ContractId(record[0], record[1]), record[2])
            for record in contract_registry.contracts()
        ]
        codes = [
            ErrorCodeManifestEntry(
                descriptor.code, descriptor.category, descriptor.introduced, descriptor.description
            )
            for descriptor in error_code_registry.codes()
        ]
        return RegistryManifest(
            ContractId("core.semantic-model", semantic_model_version), contracts, codes
        )

    def to_value(self) -> PortableValue:
        contracts = [
            PortableValue.object(
                [
                    ("id", PortableValue.string(entry.contract.id)),
                    ("version", integer_value(entry.contract.version)),
                    ("stability", PortableValue.string(entry.stability.value)),
                ]
            )
            for entry in self.contracts
        ]
        error_code_values = [
            PortableValue.object(
                [
                    ("code", PortableValue.string(entry.code)),
                    ("category", PortableValue.string(entry.category.value)),
                    ("introduced", PortableValue.string(entry.introduced)),
                    ("stability", PortableValue.string("Stable")),
                    ("description", PortableValue.string(entry.description)),
                ]
            )
            for entry in self.error_codes
        ]
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.registry-manifest@1")),
                (
                    "semantic_model",
                    _reference_value(self.semantic_model.id, self.semantic_model.version),
                ),
                ("contracts", PortableValue.sequence(contracts)),
                ("error_codes", PortableValue.sequence(error_code_values)),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue) -> "RegistryManifest":
        fields = schema_fields(
            value,
            "core.registry-manifest@1",
            ["schema", "semantic_model", "contracts", "error_codes"],
            "$",
        )
        semantic_model = _parse_contract_reference(fields[1], "$.semantic_model")
        contracts = []
        for index, item in enumerate(sequence_of(fields[2], "$.contracts")):
            path = f"$.contracts[{index}]"
            entry = exact_fields(item, ["id", "version", "stability"], path)
            id = string_of(entry[0], f"{path}.id")
            version = unsigned32(entry[1], f"{path}.version")
            contract = ContractId(id, version)
            stability_name = string_of(entry[2], f"{path}.stability")
            try:
                stability = ContractStability(stability_name)
            except ValueError:
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE, f"{path}.stability", "unknown contract stability"
                ) from None
            contracts.append(ContractManifestEntry(contract, stability))
        codes = []
        for index, item in enumerate(sequence_of(fields[3], "$.error_codes")):
            path = f"$.error_codes[{index}]"
            entry = exact_fields(
                item, ["code", "category", "introduced", "stability", "description"], path
            )
            code = string_of(entry[0], f"{path}.code")
            category = parse_category(string_of(entry[1], f"{path}.category"), f"{path}.category")
            introduced = string_of(entry[2], f"{path}.introduced")
            stability = string_of(entry[3], f"{path}.stability")
            if stability != "Stable":
                raise protocol_error(
                    ProtocolErrorKind.INVALID_VALUE,
                    f"{path}.stability",
                    "unknown error-code stability",
                )
            description = string_of(entry[4], f"{path}.description")
            codes.append(
                ErrorCodeManifestEntry(code, category, introduced, description)
            )
        return RegistryManifest(semantic_model, contracts, codes)


# --------------------------------------------------------------------------
# reference records
# --------------------------------------------------------------------------

def _reference_value(id: str, version: int) -> PortableValue:
    return PortableValue.object(
        [
            ("id", PortableValue.string(id)),
            ("version", integer_value(version)),
        ]
    )


def _parse_contract_reference(value: PortableValue, path: str) -> ContractId:
    fields = exact_fields(value, ["id", "version"], path)
    id = string_of(fields[0], f"{path}.id")
    version = unsigned32(fields[1], f"{path}.version")
    return ContractId(id, version)


def _parse_profile_reference(value: PortableValue, path: str) -> ProfileReference:
    fields = exact_fields(value, ["id", "version"], path)
    id = string_of(fields[0], f"{path}.id")
    version = unsigned32(fields[1], f"{path}.version")
    return ProfileReference(id, version)
