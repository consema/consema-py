"""consema.registry — the additive facade registry surface and the opaque
Document union.

Authority: crates/consema/src/lib.rs ``registry`` module and ``Document``
(RFC 0015 §6.2; the Rust crate is the registry/byte arbitration source);
go/registry.go and go/document.go are cross-references only, never a
template.

The capability inventory is the declared Feature-Complete Manifest
capability set (fc-manifest-0.13.0.json:30-34: 8 families / 16 profiles /
21 query domains / 16 operation registries / 187 error codes). Everything
this module can derive from the backend packages is derived from them:

- the family and profile ids of the eight families come from the family
  packages' profile enums (``JsonProfile.id()``, ``TomlProfile.profile_id()``,
  ...) and the parsed documents' ``format_family()``/``profile_id()`` facts
  (drift-guard tests assert the equality);
- the query domains come from the protocol package's frozen domain
  constructors (``consema.protocol.query.domain_*_v1``);
- the per-profile operation registries come from the family registries
  (``format_operation_registry`` of each family package).

The module also implements the single parse entry by profile id
(``parse_document``) over the opaque :class:`Document` union
(crates/consema/src/lib.rs:512-820): the concrete representation is
private and format access is only possible through the typed adapters.
All returned facts are immutable snapshot facts.
"""

from __future__ import annotations

from consema.document.ids import FormatFamilyId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.source import SourceEncoding
from consema.document.structural import FormationStatus
from consema.hcl.document import HclDocument, HclEncodingSelection, parse as parse_hcl_document
from consema.hcl.kinds import HclProfile
from consema.hcl.limits import HclParseLimits
from consema.ini.document import IniDocument
from consema.ini.kinds import IniEncodingSelection, IniParseLimits, IniProfile
from consema.ini.parser import parse as parse_ini_document
from consema.json.document import JsonDocument
from consema.json.kinds import JsonProfile
from consema.json.operation_registry import JsonFormatOperationRegistry
from consema.json.parser import parse as parse_json_document
from consema.plist.document import PlistDocument, parse as parse_plist_document
from consema.plist.kinds import PlistEncodingSelection, PlistParseLimits, PlistProfile
from consema.properties.document import PropertiesDocument
from consema.properties.kinds import PropertiesProfile
from consema.properties.limits import PropertiesEncodingSelection, PropertiesParseLimits
from consema.properties.parser import parse as parse_properties_document
from consema.protocol.query import (
    QueryDomain,
    domain_hcl_lossless_syntax_v1,
    domain_hcl_native_v1,
    domain_ini_lossless_syntax_v1,
    domain_ini_native_v1,
    domain_java_properties_lossless_syntax_v1,
    domain_java_properties_native_v1,
    domain_json_lossless_syntax_v1,
    domain_json_lossless_syntax_v2,
    domain_json_native_v1,
    domain_json_native_v2,
    domain_plist_binary_structure_v1,
    domain_plist_lossless_syntax_v1,
    domain_plist_native_v1,
    domain_portable_graph_v1,
    domain_portable_value_v1,
    domain_toml_lossless_syntax_v1,
    domain_toml_native_v1,
    domain_xml_lossless_syntax_v1,
    domain_xml_native_v1,
    domain_yaml_lossless_syntax_v1,
    domain_yaml_native_v1,
)
from consema.toml.document import TomlProfile
from consema.toml.parser import parse as parse_toml_document
from consema.xml.document import Document as XmlDocument
from consema.xml.document import XmlProfile
from consema.xml.parser import XmlEncodingSelection, XmlParseLimits, parse as parse_xml_document
from consema.yaml.document import Document as YamlDocument
from consema.yaml.kinds import YamlProfile
from consema.yaml.parser import parse as parse_yaml_document

__all__ = [
    "Document",
    "FormatMismatch",
    "FormatProfile",
    "OperationArgumentDescriptor",
    "OperationDescriptor",
    "OperationRegistry",
    "ProfileError",
    "format_families",
    "operation_registry",
    "parse_document",
    "profiles",
    "query_domains",
]

# ---------------------------------------------------------------------------
# the family/profile enumeration
# ---------------------------------------------------------------------------


class FormatProfile:
    """One profile together with the format family that publishes it
    (lib.rs registry FormatProfile; registry.rs:50-69)."""

    __slots__ = ("family", "profile")

    def __init__(self, family: FormatFamilyId, profile: ProfileId):
        self.family = family
        self.profile = profile

    def family_id(self) -> FormatFamilyId:
        """The format family of the profile."""
        return self.family

    def profile_id(self) -> ProfileId:
        """The profile itself."""
        return self.profile

    def __repr__(self) -> str:
        return (
            f"FormatProfile(family={self.family.id!r}@{self.family.version}, "
            f"profile={self.profile.id!r}@{self.profile.version})"
        )


def _family_profile(family_id: str, profile: ProfileId) -> FormatProfile:
    return FormatProfile(FormatFamilyId.new(family_id, 1), profile)


def format_families() -> list[FormatFamilyId]:
    """The eight format families (RFC 0015 §6.2 ``families``), sorted by id.

    The ids are the declared Manifest capability facts, drift-guarded by the
    facade tests against the parsed documents' ``format_family()`` facts.
    """
    families = [
        FormatFamilyId.new("hcl", 1),
        FormatFamilyId.new("ini", 1),
        FormatFamilyId.new("java-properties", 1),
        FormatFamilyId.new("json", 1),
        FormatFamilyId.new("plist", 1),
        FormatFamilyId.new("toml", 1),
        FormatFamilyId.new("xml", 1),
        FormatFamilyId.new("yaml", 1),
    ]
    families.sort(key=lambda family: family.id)
    return families


def profiles() -> list[FormatProfile]:
    """All sixteen profiles with their owning family (RFC 0015 §6.2
    ``profiles``), sorted by profile id then version."""
    entries = [
        _family_profile("hcl", HclProfile.NATIVE_V1.id()),
        _family_profile("hcl", HclProfile.TFVARS_V1.id()),
        _family_profile("ini", IniProfile.PORTABLE_V1.id()),
        _family_profile("ini", IniProfile.WINDOWS_V1.id()),
        _family_profile("ini", IniProfile.PYTHON_CONFIGPARSER_V1.id()),
        _family_profile("java-properties", PropertiesProfile.READER_V1.id()),
        _family_profile("java-properties", PropertiesProfile.LATIN1_V1.id()),
        _family_profile("json", JsonProfile.STRICT_V1.id()),
        _family_profile("json", JsonProfile.JSONC_BOUNDED_V1.id()),
        _family_profile("json", JsonProfile.JSON5_STANDARD_V1.id()),
        _family_profile("plist", PlistProfile.XML_V1.id()),
        _family_profile("plist", PlistProfile.BINARY_V1.id()),
        _family_profile("toml", TomlProfile.TOML10_V1.profile_id()),
        _family_profile("xml", XmlProfile.SAFE_V1.profile_id()),
        _family_profile("yaml", _yaml_profile_id(YamlProfile.YAML12_CORE_V1)),
        _family_profile("yaml", _yaml_profile_id(YamlProfile.YAML11_COMPAT_V1)),
    ]
    entries.sort(key=lambda entry: (entry.profile.id, entry.profile.version))
    return entries


def _yaml_profile_id(profile: YamlProfile) -> ProfileId:
    name, version = profile.id()
    return ProfileId.new(name, version)


def query_domains() -> list[QueryDomain]:
    """The query-domain constructor inventory (RFC 0015 §6.2
    ``query_domains``), sorted by (id, version). Every domain comes from
    the protocol package's frozen domain constructors; this module only
    aggregates and sorts them."""
    domains = [
        domain_portable_value_v1(),
        domain_portable_graph_v1(),
        domain_json_native_v1(),
        domain_json_native_v2(),
        domain_toml_native_v1(),
        domain_yaml_native_v1(),
        domain_ini_native_v1(),
        domain_java_properties_native_v1(),
        domain_xml_native_v1(),
        domain_json_lossless_syntax_v1(),
        domain_json_lossless_syntax_v2(),
        domain_toml_lossless_syntax_v1(),
        domain_yaml_lossless_syntax_v1(),
        domain_ini_lossless_syntax_v1(),
        domain_java_properties_lossless_syntax_v1(),
        domain_xml_lossless_syntax_v1(),
        domain_plist_native_v1(),
        domain_plist_lossless_syntax_v1(),
        domain_plist_binary_structure_v1(),
        domain_hcl_native_v1(),
        domain_hcl_lossless_syntax_v1(),
    ]
    domains.sort(key=lambda domain: (domain.id, domain.version))
    return domains


# ---------------------------------------------------------------------------
# the per-profile operation registries
# ---------------------------------------------------------------------------


class OperationArgumentDescriptor:
    """One operation argument contract (RFC 0015 §6.2 ``operations``)."""

    __slots__ = ("name", "kind", "required")

    def __init__(self, name: str, kind: str, required: bool = True):
        self.name = name
        self.kind = kind
        self.required = required

    def __repr__(self) -> str:
        return f"OperationArgumentDescriptor({self.name!r}, {self.kind!r}, required={self.required})"


class OperationDescriptor:
    """One versioned format operation descriptor."""

    __slots__ = ("id", "target_role", "arguments", "support")

    def __init__(
        self,
        id: str,
        target_role: str,
        arguments: tuple[OperationArgumentDescriptor, ...],
        support: str,
    ):
        self.id = id
        self.target_role = target_role
        self.arguments = arguments
        self.support = support

    def __repr__(self) -> str:
        return f"OperationDescriptor({self.id!r}, {self.target_role!r}, {self.support!r})"


class OperationRegistry:
    """The validated operation registry of one exact profile (RFC 0015 §6.2
    ``operations``), derived from the family registries of the implementing
    packages and never re-declared here."""

    __slots__ = ("profile", "_operations")

    def __init__(self, profile: ProfileId, operations: tuple[OperationDescriptor, ...]):
        self.profile = profile
        self._operations = operations

    def profile_id(self) -> ProfileId:
        """The owning profile."""
        return self.profile

    def operations(self) -> tuple[OperationDescriptor, ...]:
        """The ordered operation descriptors (a copy)."""
        return self._operations

    def __repr__(self) -> str:
        return f"OperationRegistry({self.profile.id!r}@{self.profile.version}, {len(self._operations)} operations)"


def operation_registry(profile: ProfileId) -> OperationRegistry | None:
    """The per-profile operation registry of one exact profile (RFC 0015
    §6.2 ``operations``); ``None`` for profile ids outside the sixteen-profile
    facade surface. Every registry is derived from the family registries."""
    try:
        family = _family_for_profile(profile.id)
    except KeyError:
        return None
    if family == "json":
        return _json_registry(_registry_for_json(profile.id))
    if family == "toml":
        return _generic_registry(toml_registry(profile))
    if family == "yaml":
        return _generic_registry(yaml_registry(_yaml_profile(profile.id)))
    if family == "ini":
        return _generic_registry(ini_registry(_ini_profile(profile.id)))
    if family == "properties":
        return _generic_registry(properties_registry(_properties_profile(profile.id)))
    if family == "xml":
        return _generic_registry(xml_registry(profile))
    if family == "plist":
        return _generic_registry(plist_registry(_plist_profile(profile.id)))
    return _generic_registry(hcl_registry(_hcl_profile(profile.id)))


def _registry_for_json(profile_id: str) -> JsonFormatOperationRegistry:
    from consema.json.operation_registry import format_operation_registry as json_registry

    return json_registry(_json_profile(profile_id))


def _json_registry(registry: JsonFormatOperationRegistry) -> OperationRegistry:
    descriptors = []
    for operation in registry.operations:
        arguments = tuple(
            OperationArgumentDescriptor(
                argument.name,
                argument.kind.value if hasattr(argument.kind, "value") else str(argument.kind),
                argument.required,
            )
            for argument in operation.arguments
        )
        descriptors.append(
            OperationDescriptor(
                operation.to_string(),
                operation.target_role,
                arguments,
                operation.support.value,
            )
        )
    return OperationRegistry(registry.profile, tuple(descriptors))


def _generic_registry(registry) -> OperationRegistry:
    """Derives the root registry view from a family registry whose
    descriptors carry the (id, target_role, arguments, support) shape with a
    ``profile_id``/``profile`` fact. The family argument descriptors carry no
    required flag; every argument of the frozen surfaces is required."""
    operations = _registry_operations(registry)
    descriptors = []
    for operation in operations:
        arguments = tuple(
            OperationArgumentDescriptor(argument.name, _kind_name(argument.kind), True)
            for argument in operation.arguments
        )
        descriptors.append(
            OperationDescriptor(
                operation.id.to_string(),
                operation.target_role,
                arguments,
                _kind_name(operation.support),
            )
        )
    return OperationRegistry(_registry_profile(registry), tuple(descriptors))


def _registry_operations(registry) -> tuple:
    """The ordered operation descriptors of one family registry; the family
    registries expose the surface either as an ``operations`` attribute or as
    an ``operations()`` method."""
    operations = getattr(registry, "operations", None)
    if callable(operations):
        return operations()
    return operations


def _registry_profile(registry) -> ProfileId:
    for attribute in ("profile", "profile_id"):
        value = getattr(registry, attribute, None)
        if value is None:
            continue
        if isinstance(value, ProfileId):
            return value
    raise TypeError(f"registry {type(registry).__name__} has no ProfileId profile fact")


def _kind_name(kind) -> str:
    if isinstance(kind, str):
        return kind
    return kind.value if hasattr(kind, "value") else str(kind)


# ---------------------------------------------------------------------------
# profile-id resolution tables (frozen profile inventory; lib.rs
# registry::profiles)
# ---------------------------------------------------------------------------

_FAMILY_BY_PROFILE = {
    "hcl.native": "hcl",
    "hcl.tfvars": "hcl",
    "ini.portable": "ini",
    "ini.windows": "ini",
    "ini.python-configparser": "ini",
    "java-properties.reader": "properties",
    "java-properties.latin1": "properties",
    "json.strict": "json",
    "jsonc.bounded": "json",
    "json5.standard": "json",
    "plist.xml": "plist",
    "plist.binary": "plist",
    "toml.1.0": "toml",
    "xml.1.0-safe": "xml",
    "yaml.1.2-core": "yaml",
    "yaml.1.1-compat": "yaml",
}


def _family_for_profile(profile_id: str) -> str:
    return _FAMILY_BY_PROFILE[profile_id]


def _json_profile(profile_id: str) -> JsonProfile:
    return {
        "json.strict": JsonProfile.STRICT_V1,
        "jsonc.bounded": JsonProfile.JSONC_BOUNDED_V1,
        "json5.standard": JsonProfile.JSON5_STANDARD_V1,
    }[profile_id]


def _yaml_profile(profile_id: str) -> YamlProfile:
    return {
        "yaml.1.2-core": YamlProfile.YAML12_CORE_V1,
        "yaml.1.1-compat": YamlProfile.YAML11_COMPAT_V1,
    }[profile_id]


def _ini_profile(profile_id: str) -> IniProfile:
    return {
        "ini.portable": IniProfile.PORTABLE_V1,
        "ini.windows": IniProfile.WINDOWS_V1,
        "ini.python-configparser": IniProfile.PYTHON_CONFIGPARSER_V1,
    }[profile_id]


def _properties_profile(profile_id: str) -> PropertiesProfile:
    return {
        "java-properties.reader": PropertiesProfile.READER_V1,
        "java-properties.latin1": PropertiesProfile.LATIN1_V1,
    }[profile_id]


def _plist_profile(profile_id: str) -> PlistProfile:
    return {
        "plist.xml": PlistProfile.XML_V1,
        "plist.binary": PlistProfile.BINARY_V1,
    }[profile_id]


def _hcl_profile(profile_id: str) -> HclProfile:
    return {
        "hcl.native": HclProfile.NATIVE_V1,
        "hcl.tfvars": HclProfile.TFVARS_V1,
    }[profile_id]


# ---------------------------------------------------------------------------
# the opaque Document union
# ---------------------------------------------------------------------------


class FormatMismatch(Exception):
    """The snapshot is not a document of the requested format family."""

    def __init__(self, family: str):
        super().__init__(f"consema: the snapshot is not a {family} document")
        self.family = family


class Document:
    """Common opaque snapshot over the supported format documents
    (crates/consema/src/lib.rs:512-820).

    The concrete representation is private; format access is only possible
    through the typed adapters (``as_json``, ``as_toml``, ...). All returned
    facts are immutable snapshot facts. The union is additive: every family
    document is wrapped without changing this type or the adapter semantics.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: object):
        self._inner = inner

    # -- parse entries ------------------------------------------------------

    @classmethod
    def parse_json(
        cls, source: bytes, profile: JsonProfile, limits: ParseLimits
    ) -> Document:
        """Forms one JSON/JSONC/JSON5 snapshot under an exact profile."""
        return cls(parse_json_document(source, profile, limits))

    @classmethod
    def parse_toml(
        cls, source: bytes, profile: TomlProfile, limits: ParseLimits
    ) -> Document:
        """Forms one TOML 1.0 snapshot under the exact profile."""
        return cls(parse_toml_document(source, profile, limits))

    @classmethod
    def parse_yaml(
        cls, source: bytes, profile: YamlProfile, limits: ParseLimits
    ) -> Document:
        """Forms one YAML stream snapshot under the exact frozen profile."""
        return cls(parse_yaml_document(source, profile, limits))

    @classmethod
    def parse_ini(
        cls,
        source: bytes,
        profile: IniProfile,
        selection: IniEncodingSelection,
        limits: IniParseLimits,
    ) -> Document:
        """Forms one INI snapshot under the exact profile and explicit
        encoding selection."""
        return cls(parse_ini_document(source, profile, selection, limits))

    @classmethod
    def parse_properties(
        cls,
        source: bytes,
        profile: PropertiesProfile,
        selection: PropertiesEncodingSelection,
        limits: PropertiesParseLimits,
    ) -> Document:
        """Forms one Java Properties snapshot under the exact profile and
        source contract."""
        return cls(parse_properties_document(source, profile, selection, limits))

    @classmethod
    def parse_xml(
        cls,
        source: bytes,
        profile: XmlProfile,
        selection: XmlEncodingSelection,
        limits,
    ) -> Document:
        """Forms one XML 1.0 safe snapshot under the exact profile and
        explicit encoding selection."""
        return cls(parse_xml_document(source, profile, selection, limits))

    @classmethod
    def parse_plist(
        cls,
        source: bytes,
        profile: PlistProfile,
        selection: PlistEncodingSelection,
        limits: PlistParseLimits,
    ) -> Document:
        """Forms one Property List snapshot under the exact profile and
        explicit encoding selection."""
        return cls(parse_plist_document(source, profile, selection, limits))

    @classmethod
    def parse_hcl(
        cls,
        source: bytes,
        profile: HclProfile,
        selection: HclEncodingSelection = HclEncodingSelection.PROFILE_DEFAULT,
        limits: HclParseLimits | None = None,
    ) -> Document:
        """Forms one HCL snapshot under the exact profile and explicit
        encoding selection."""
        return cls(parse_hcl_document(source, profile, selection, limits))

    # -- common snapshot facts ---------------------------------------------

    def render(self) -> bytes:
        """Default rendering is byte-for-byte identical to the source."""
        return self._inner.render()

    def formation_status(self) -> FormationStatus:
        """Formation status of the underlying snapshot."""
        return self._inner.formation_status()

    def diagnostics(self) -> tuple:
        """Deterministically ordered document diagnostics."""
        if hasattr(self._inner, "diagnostic_records"):
            return self._inner.diagnostic_records()
        return self._inner.diagnostics()

    def snapshot_identity(self) -> object:
        """Snapshot identity to which every handle and span belongs."""
        return self._inner.snapshot_identity()

    def profile(self) -> ProfileId:
        """Exact source profile of the underlying format document."""
        if hasattr(self._inner, "profile_id"):
            return self._inner.profile_id()
        return self._inner.profile()

    def format_family(self) -> FormatFamilyId:
        """The format family contract of the underlying document."""
        return self._inner.format_family()

    # -- typed adapters -----------------------------------------------------

    def as_json(self) -> JsonDocument | None:
        """The typed JSON-family document; ``None`` only when the snapshot
        is not a JSON document."""
        return self._inner if isinstance(self._inner, JsonDocument) else None

    def as_toml(self) -> object | None:
        """The typed TOML document; ``None`` only when the snapshot is not
        a TOML document."""
        from consema.toml.document import Document as TomlDocument

        return self._inner if isinstance(self._inner, TomlDocument) else None

    def as_yaml(self) -> YamlDocument | None:
        """The typed YAML document; ``None`` only when the snapshot is not
        a YAML document."""
        return self._inner if isinstance(self._inner, YamlDocument) else None

    def as_ini(self) -> IniDocument | None:
        """The typed INI document; ``None`` only when the snapshot is not
        an INI document."""
        return self._inner if isinstance(self._inner, IniDocument) else None

    def as_properties(self) -> PropertiesDocument | None:
        """The typed Java Properties document; ``None`` only when the
        snapshot is not a Properties document."""
        return self._inner if isinstance(self._inner, PropertiesDocument) else None

    def as_xml(self) -> XmlDocument | None:
        """The typed XML document; ``None`` only when the snapshot is not
        an XML document."""
        return self._inner if isinstance(self._inner, XmlDocument) else None

    def as_plist(self) -> PlistDocument | None:
        """The typed Property List document; ``None`` only when the snapshot
        is not a plist document."""
        return self._inner if isinstance(self._inner, PlistDocument) else None

    def as_hcl(self) -> HclDocument | None:
        """The typed HCL document; ``None`` only when the snapshot is not
        an HCL document."""
        return self._inner if isinstance(self._inner, HclDocument) else None

    def __repr__(self) -> str:
        return f"Document({type(self._inner).__name__})"


class ProfileError(Exception):
    """The typed failure of :func:`parse_document`: the profile id is
    unknown or its family is not implemented.

    The text is human presentation only (RFC 0016 §6); the frozen code
    mirrors the Rust facade's unknown-profile failure diagnostic
    (crates/consema/src/lib.rs:298-307).
    """

    def __init__(self, profile: ProfileId):
        super().__init__(f"consema: unknown or unimplemented profile {profile.id}@{profile.version}")
        self.profile = profile

    def code(self) -> str:
        """The frozen registered code of the unknown-profile failure."""
        return "core.source.encoding-conflict@1"


# ---------------------------------------------------------------------------
# the single facade parse entry
# ---------------------------------------------------------------------------


def parse_document(source: bytes, profile: ProfileId) -> Document:
    """Parses one snapshot under an exact profile id through the single
    facade parse entry (crates/consema/src/lib.rs registry::parse_document;
    RFC 0015 §7.1 ``cli.parse-facts@1``).

    The per-format encoding selection and limits use the frozen profile
    defaults; the java-properties reader profile uses an explicit UTF-8
    selection because its contract has no profile default. Unknown profile
    ids raise :class:`ProfileError` with the same frozen code as the Rust
    facade's unknown-profile failure.
    """
    profile_id = profile.id
    if profile_id in ("json.strict", "jsonc.bounded", "json5.standard"):
        return Document(
            parse_json_document(source, _json_profile(profile_id), ParseLimits())
        )
    if profile_id == "toml.1.0":
        return Document(
            parse_toml_document(source, TomlProfile.TOML10_V1, ParseLimits())
        )
    if profile_id in ("yaml.1.2-core", "yaml.1.1-compat"):
        return Document(
            parse_yaml_document(source, _yaml_profile(profile_id), ParseLimits())
        )
    if profile_id in ("ini.portable", "ini.windows", "ini.python-configparser"):
        return Document(
            parse_ini_document(
                source,
                _ini_profile(profile_id),
                IniEncodingSelection.profile_default(),
                IniParseLimits(),
            )
        )
    if profile_id in ("java-properties.reader", "java-properties.latin1"):
        selection = (
            PropertiesEncodingSelection.reader(SourceEncoding.utf8())
            if profile_id == "java-properties.reader"
            else PropertiesEncodingSelection.latin1()
        )
        return Document(
            parse_properties_document(
                source,
                _properties_profile(profile_id),
                selection,
                PropertiesParseLimits(),
            )
        )
    if profile_id == "xml.1.0-safe":
        return Document(
            parse_xml_document(
                source,
                XmlProfile.SAFE_V1,
                XmlEncodingSelection.profile_default(),
                XmlParseLimits(),
            )
        )
    if profile_id in ("plist.xml", "plist.binary"):
        return Document(
            parse_plist_document(
                source,
                _plist_profile(profile_id),
                PlistEncodingSelection.profile_default(),
                PlistParseLimits(),
            )
        )
    if profile_id in ("hcl.native", "hcl.tfvars"):
        return Document(
            parse_hcl_document(
                source,
                _hcl_profile(profile_id),
                HclEncodingSelection.PROFILE_DEFAULT,
                HclParseLimits(),
            )
        )
    raise ProfileError(profile)


# ---------------------------------------------------------------------------
# family registry adapters (imported lazily to keep the module import graph
# acyclic and cheap; every adapter only reads registry facts)
# ---------------------------------------------------------------------------


def toml_registry(profile: ProfileId):
    from consema.toml.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def yaml_registry(profile: YamlProfile):
    from consema.yaml.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def ini_registry(profile: IniProfile):
    from consema.ini.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def properties_registry(profile: PropertiesProfile):
    from consema.properties.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def xml_registry(profile: ProfileId):
    from consema.xml.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def plist_registry(profile: PlistProfile):
    from consema.plist.operation_registry import format_operation_registry

    return format_operation_registry(profile)


def hcl_registry(profile: HclProfile):
    from consema.hcl.operation_registry import format_operation_registry

    return format_operation_registry(profile)
