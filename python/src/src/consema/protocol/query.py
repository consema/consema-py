"""Versioned typed query definitions, validation, and binding.

Authority: RFC 0016 §5.4 and the operator validation table of
crates/consema-core/src/query.rs:899-1897 (transcribed verbatim, including
the closed kind-name vocabularies at query.rs:1900-2209); the match-role
spellings are the language-neutral MatchRole names (query.rs:169-316). Go
(go/protocol/query.go, query_validate.go) is a cross-reference only.

The table maps ``(domain, operator)`` to the expected input role, the
output role, and the required argument value kinds. The generic rows
``core.take`` and ``core.distinct-by-identity`` are domain-agnostic. The
input-dependent rows (ini.duplicate-group, the XML parent/kind unions, the
plist value-operator and binary-structure unions, the HCL attribute/block
unions) are typed by the input role at validation time.
"""

from __future__ import annotations

import enum

from consema.core.value import Kind, PortableValue
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet


# --------------------------------------------------------------------------
# MatchRole
# --------------------------------------------------------------------------

class MatchRole(enum.Enum):
    """One typed match role of the query model (query.rs:169-316)."""

    VALUE = "Value"
    OBJECT_ENTRY = "ObjectEntry"
    ENTRY_MAPPING_ENTRY = "EntryMappingEntry"
    JSON_VALUE = "JsonValue"
    JSON_OBJECT_MEMBER = "JsonObjectMember"
    JSON_ARRAY_ELEMENT = "JsonArrayElement"
    TOML_ITEM = "TomlItem"
    TOML_ENTRY = "TomlEntry"
    TOML_ARRAY_ELEMENT = "TomlArrayElement"
    YAML_STREAM = "YamlStream"
    YAML_DOCUMENT = "YamlDocument"
    YAML_NODE = "YamlNode"
    YAML_MAPPING_ENTRY = "YamlMappingEntry"
    YAML_SEQUENCE_ELEMENT = "YamlSequenceElement"
    YAML_ANCHOR_DEFINITION = "YamlAnchorDefinition"
    YAML_ALIAS_OCCURRENCE = "YamlAliasOccurrence"
    JSON_SYNTAX_PIECE = "JsonSyntaxPiece"
    TOML_SYNTAX_PIECE = "TomlSyntaxPiece"
    YAML_SYNTAX_PIECE = "YamlSyntaxPiece"
    INI_DOCUMENT = "IniDocument"
    INI_SECTION = "IniSection"
    INI_DEFAULT_SECTION = "IniDefaultSection"
    INI_ENTRY = "IniEntry"
    INI_PHYSICAL_LINE = "IniPhysicalLine"
    INI_LOGICAL_LINE = "IniLogicalLine"
    INI_ERROR_LINE = "IniErrorLine"
    INI_SYNTAX_PIECE = "IniSyntaxPiece"
    PROPERTIES_DOCUMENT = "PropertiesDocument"
    PROPERTIES_NATURAL_LINE = "PropertiesNaturalLine"
    PROPERTIES_LOGICAL_LINE = "PropertiesLogicalLine"
    PROPERTIES_PROPERTY = "PropertiesProperty"
    PROPERTIES_COMMENT = "PropertiesComment"
    PROPERTIES_ESCAPE = "PropertiesEscape"
    PROPERTIES_ERROR_LINE = "PropertiesErrorLine"
    PROPERTIES_SYNTAX_PIECE = "PropertiesSyntaxPiece"
    GRAPH_NODE = "GraphNode"
    GRAPH_SEQUENCE_ELEMENT = "GraphSequenceElement"
    GRAPH_MAPPING_ENTRY = "GraphMappingEntry"
    XML_DOCUMENT = "XmlDocument"
    XML_DECLARATION = "XmlDeclaration"
    XML_DOCTYPE = "XmlDoctype"
    XML_PROLOG_ITEM = "XmlPrologItem"
    XML_ELEMENT = "XmlElement"
    XML_CONTENT_ITEM = "XmlContentItem"
    XML_ATTRIBUTE = "XmlAttribute"
    XML_NAMESPACE_BINDING = "XmlNamespaceBinding"
    XML_TEXT = "XmlText"
    XML_CDATA = "XmlCdata"
    XML_COMMENT = "XmlComment"
    XML_PROCESSING_INSTRUCTION = "XmlProcessingInstruction"
    XML_REFERENCE = "XmlReference"
    XML_ERROR_REGION = "XmlErrorRegion"
    XML_SYNTAX_PIECE = "XmlSyntaxPiece"
    PLIST_VALUE = "PlistValue"
    PLIST_DICT_ENTRY = "PlistDictEntry"
    PLIST_KEY = "PlistKey"
    PLIST_ARRAY_ELEMENT = "PlistArrayElement"
    PLIST_SYNTAX_PIECE = "PlistSyntaxPiece"
    PLIST_BINARY_STRUCTURE = "PlistBinaryStructure"
    PLIST_BINARY_OBJECT = "PlistBinaryObject"
    PLIST_BINARY_OFFSET = "PlistBinaryOffset"
    PLIST_BINARY_REF = "PlistBinaryRef"
    PLIST_BINARY_TRAILER = "PlistBinaryTrailer"
    HCL_BODY = "HclBody"
    HCL_ATTRIBUTE = "HclAttribute"
    HCL_BLOCK = "HclBlock"
    HCL_BLOCK_LABEL = "HclBlockLabel"
    HCL_EXPRESSION = "HclExpression"
    HCL_TEMPLATE_PART = "HclTemplatePart"
    HCL_ERROR_REGION = "HclErrorRegion"
    HCL_SYNTAX_PIECE = "HclSyntaxPiece"


ROLE_ANY = None  # table placeholder for input-dependent rows


# --------------------------------------------------------------------------
# QueryDomain / OperatorCall / expression model
# --------------------------------------------------------------------------

class QueryDomain:
    """A versioned query domain (query.rs:12-166)."""

    __slots__ = ("id", "version")

    def __init__(self, id: str, version: int):
        self.id = id
        self.version = version

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, QueryDomain):
            return NotImplemented
        return self.id == other.id and self.version == other.version

    def __hash__(self) -> int:
        return hash((self.id, self.version))

    def __repr__(self) -> str:
        return f"QueryDomain({self.id!r}@{self.version})"


def domain_portable_value_v1() -> QueryDomain:
    return QueryDomain("core.portable-value-query", 1)


def domain_portable_graph_v1() -> QueryDomain:
    return QueryDomain("core.portable-graph-query", 1)


def domain_json_native_v1() -> QueryDomain:
    return QueryDomain("json.native-semantic-query", 1)


def domain_json_native_v2() -> QueryDomain:
    return QueryDomain("json.native-semantic-query", 2)


def domain_toml_native_v1() -> QueryDomain:
    return QueryDomain("toml.native-semantic-query", 1)


def domain_yaml_native_v1() -> QueryDomain:
    return QueryDomain("yaml.native-semantic-query", 1)


def domain_ini_native_v1() -> QueryDomain:
    return QueryDomain("ini.native-semantic-query", 1)


def domain_java_properties_native_v1() -> QueryDomain:
    return QueryDomain("java-properties.native-semantic-query", 1)


def domain_xml_native_v1() -> QueryDomain:
    return QueryDomain("xml.native-semantic-query", 1)


def domain_json_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("json.lossless-syntax-query", 1)


def domain_json_lossless_syntax_v2() -> QueryDomain:
    return QueryDomain("json.lossless-syntax-query", 2)


def domain_toml_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("toml.lossless-syntax-query", 1)


def domain_yaml_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("yaml.lossless-syntax-query", 1)


def domain_ini_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("ini.lossless-syntax-query", 1)


def domain_java_properties_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("java-properties.lossless-syntax-query", 1)


def domain_xml_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("xml.lossless-syntax-query", 1)


def domain_plist_native_v1() -> QueryDomain:
    return QueryDomain("plist.native-semantic-query", 1)


def domain_plist_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("plist.lossless-syntax-query", 1)


def domain_plist_binary_structure_v1() -> QueryDomain:
    return QueryDomain("plist.binary-structure-query", 1)


def domain_hcl_native_v1() -> QueryDomain:
    return QueryDomain("hcl.native-semantic-query", 1)


def domain_hcl_lossless_syntax_v1() -> QueryDomain:
    return QueryDomain("hcl.lossless-syntax-query", 1)


class OperatorCall:
    """One versioned operator call with deterministic arguments."""

    __slots__ = ("id", "version", "arguments")

    def __init__(self, id: str, version: int):
        self.id = id
        self.version = version
        self.arguments: dict[str, PortableValue] = {}

    def with_argument(self, name: str, value: PortableValue) -> "OperatorCall":
        self.arguments[name] = value
        return self

    def __repr__(self) -> str:
        return f"OperatorCall({self.id!r}@{self.version})"


class ExpressionKind(enum.Enum):
    INPUT = "Input"
    APPLY = "Apply"
    CONCAT = "Concat"
    STRUCTURE_ORDER_MERGE = "StructureOrderMerge"


class QueryExpression:
    """The declarative operator tree (query.rs:363-390)."""

    __slots__ = ("kind", "input", "operator", "branches")

    def __init__(
        self,
        kind: ExpressionKind,
        input: "QueryExpression | None" = None,
        operator: OperatorCall | None = None,
        branches: list["QueryExpression"] | None = None,
    ):
        self.kind = kind
        self.input = input
        self.operator = operator
        self.branches = branches

    def then(self, operator: OperatorCall) -> "QueryExpression":
        return QueryExpression(ExpressionKind.APPLY, input=self, operator=operator)


class QuerySelection(enum.Enum):
    ALL = "All"
    FIRST = "First"
    LAST = "Last"
    ZERO_OR_ONE = "ZeroOrOne"
    REQUIRE_ONE = "RequireOne"


class QueryFailureKind(enum.Enum):
    DOMAIN_MISMATCH = "domain-mismatch"
    UNKNOWN_OPERATOR = "unknown-operator"
    WRONG_ARGUMENT_TYPE = "wrong-argument-type"
    INVALID_ARGUMENT = "invalid-argument"
    INVALID_OPERATOR_COMPOSITION = "invalid-composition"
    MISSING_CAPABILITY = "missing-capability"
    REQUIRED_TYPE_MISMATCH = "required-type-mismatch"
    CARDINALITY_VIOLATION = "cardinality-violation"
    RESOURCE_LIMIT = "resource-limit"
    CANCELLED = "cancelled"
    TARGET_UNAVAILABLE = "target-unavailable"


_QUERY_FAILURE_CODES = {
    QueryFailureKind.DOMAIN_MISMATCH: "core.query.domain-mismatch@1",
    QueryFailureKind.UNKNOWN_OPERATOR: "core.query.unknown-operator@1",
    QueryFailureKind.WRONG_ARGUMENT_TYPE: "core.query.wrong-argument-type@1",
    QueryFailureKind.INVALID_ARGUMENT: "core.query.invalid-argument@1",
    QueryFailureKind.INVALID_OPERATOR_COMPOSITION: "core.query.invalid-composition@1",
    QueryFailureKind.MISSING_CAPABILITY: "core.query.missing-capability@1",
    QueryFailureKind.REQUIRED_TYPE_MISMATCH: "core.query.required-type-mismatch@1",
    QueryFailureKind.CARDINALITY_VIOLATION: "core.query.cardinality-violation@1",
    QueryFailureKind.RESOURCE_LIMIT: "core.query.resource-limit@1",
    QueryFailureKind.CANCELLED: "core.query.cancelled@1",
    QueryFailureKind.TARGET_UNAVAILABLE: "core.query.target-unavailable@1",
}


class QueryFailure(Exception):
    """The typed query failure (RFC 0016 §6 code contract)."""

    def __init__(
        self,
        kind: QueryFailureKind,
        domain: QueryDomain | None = None,
        operator: str | None = None,
        version: int | None = None,
        argument: str | None = None,
        expected_kind: str | None = None,
        expected_role: MatchRole | None = None,
        actual_role: MatchRole | None = None,
        capability: CapabilityId | None = None,
    ):
        super().__init__(kind.value, operator, argument)
        self.kind = kind
        self.domain = domain
        self.operator = operator
        self.version = version
        self.argument = argument
        self.expected_kind = expected_kind
        self.expected_role = expected_role
        self.actual_role = actual_role
        self.capability = capability

    @property
    def code(self) -> str:
        return _QUERY_FAILURE_CODES[self.kind]

    def __str__(self) -> str:
        if self.kind is QueryFailureKind.DOMAIN_MISMATCH:
            return f"{self.code}: domain mismatch for {self.domain}"
        if self.kind is QueryFailureKind.UNKNOWN_OPERATOR:
            return f"{self.code}: unknown operator {self.operator}@{self.version}"
        if self.kind is QueryFailureKind.WRONG_ARGUMENT_TYPE:
            return (
                f"{self.code}: operator {self.operator} argument {self.argument} "
                f"wants {self.expected_kind}"
            )
        if self.kind is QueryFailureKind.INVALID_ARGUMENT:
            return f"{self.code}: operator {self.operator} argument {self.argument} is invalid"
        if self.kind is QueryFailureKind.INVALID_OPERATOR_COMPOSITION:
            return (
                f"{self.code}: operator {self.operator} wants "
                f"{self.expected_role.value if self.expected_role else None} but input is "
                f"{self.actual_role.value if self.actual_role else None}"
            )
        if self.kind is QueryFailureKind.MISSING_CAPABILITY:
            return f"{self.code}: missing capability {self.capability}"
        return f"{self.code}: {self.kind.value}"


# --------------------------------------------------------------------------
# QueryDefinition and validation
# --------------------------------------------------------------------------

class QueryDefinition:
    """A transferable, not-yet-validated query definition."""

    __slots__ = ("domain", "expression", "selection")

    def __init__(self, domain: QueryDomain):
        self.domain = domain
        self.expression = QueryExpression(ExpressionKind.INPUT)
        self.selection = QuerySelection.ALL

    def with_expression(self, expression: QueryExpression) -> "QueryDefinition":
        self.expression = expression
        return self

    def with_selection(self, selection: QuerySelection) -> "QueryDefinition":
        self.selection = selection
        return self

    def validate(self) -> "ValidatedQuery":
        """Validates the domain, argument schemas, composition, and role
        typing (query.rs:500-530). The required capability set of a
        validated query is always [core.query.ordered-results@1]."""
        input_role = _domain_input_role(self.domain.id, self.domain.version)
        if input_role is None:
            raise QueryFailure(QueryFailureKind.DOMAIN_MISMATCH, domain=self.domain)
        output_role = _validate_expression(self.domain, self.expression, input_role)
        ordered_results = CapabilityId("core.query.ordered-results", 1)
        return ValidatedQuery(self, output_role, [ordered_results])


class ValidatedQuery:
    """A definition proven structurally valid for its domain."""

    __slots__ = ("definition", "output_role", "required_capabilities")

    def __init__(
        self,
        definition: QueryDefinition,
        output_role: MatchRole,
        required_capabilities: list[CapabilityId],
    ):
        self.definition = definition
        self.output_role = output_role
        self.required_capabilities = required_capabilities

    def bind(self, capabilities: CapabilitySet) -> "ExecutableQuery":
        for capability in self.required_capabilities:
            if not capabilities.contains(capability):
                raise QueryFailure(
                    QueryFailureKind.MISSING_CAPABILITY, capability=capability
                )
        return ExecutableQuery(self)


class ExecutableQuery:
    """A fully validated and capability-bound query."""

    __slots__ = ("validated",)

    def __init__(self, validated: ValidatedQuery):
        self.validated = validated

    @property
    def definition(self) -> QueryDefinition:
        return self.validated.definition

    @property
    def output_role(self) -> MatchRole:
        return self.validated.output_role


def _domain_input_role(id: str, version: int) -> MatchRole | None:
    """Maps a domain to its root match role (query.rs:502-523)."""
    table = {
        ("core.portable-value-query", 1): MatchRole.VALUE,
        ("core.portable-graph-query", 1): MatchRole.GRAPH_NODE,
        ("json.native-semantic-query", 1): MatchRole.JSON_VALUE,
        ("json.native-semantic-query", 2): MatchRole.JSON_VALUE,
        ("toml.native-semantic-query", 1): MatchRole.TOML_ITEM,
        ("yaml.native-semantic-query", 1): MatchRole.YAML_STREAM,
        ("ini.native-semantic-query", 1): MatchRole.INI_DOCUMENT,
        ("java-properties.native-semantic-query", 1): MatchRole.PROPERTIES_DOCUMENT,
        ("xml.native-semantic-query", 1): MatchRole.XML_DOCUMENT,
        ("json.lossless-syntax-query", 1): MatchRole.JSON_SYNTAX_PIECE,
        ("json.lossless-syntax-query", 2): MatchRole.JSON_SYNTAX_PIECE,
        ("toml.lossless-syntax-query", 1): MatchRole.TOML_SYNTAX_PIECE,
        ("yaml.lossless-syntax-query", 1): MatchRole.YAML_SYNTAX_PIECE,
        ("ini.lossless-syntax-query", 1): MatchRole.INI_SYNTAX_PIECE,
        ("java-properties.lossless-syntax-query", 1): MatchRole.PROPERTIES_SYNTAX_PIECE,
        ("xml.lossless-syntax-query", 1): MatchRole.XML_SYNTAX_PIECE,
        ("plist.native-semantic-query", 1): MatchRole.PLIST_VALUE,
        ("plist.lossless-syntax-query", 1): MatchRole.PLIST_SYNTAX_PIECE,
        ("plist.binary-structure-query", 1): MatchRole.PLIST_BINARY_STRUCTURE,
        ("hcl.native-semantic-query", 1): MatchRole.HCL_BODY,
        ("hcl.lossless-syntax-query", 1): MatchRole.HCL_SYNTAX_PIECE,
    }
    return table.get((id, version))


def _validate_expression(
    domain: QueryDomain, expression: QueryExpression, input_role: MatchRole
) -> MatchRole:
    if expression.kind is ExpressionKind.INPUT:
        return input_role
    if expression.kind is ExpressionKind.APPLY:
        actual_input = _validate_expression(domain, expression.input, input_role)
        return _validate_operator(domain, expression.operator, actual_input)
    if expression.kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
        output: MatchRole | None = None
        for branch in expression.branches:
            branch_output = _validate_expression(domain, branch, input_role)
            if output is not None and output is not branch_output:
                raise QueryFailure(
                    QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                    operator="composition.concat",
                    expected_role=output,
                    actual_role=branch_output,
                )
            output = branch_output
        if output is None:
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator="composition.concat",
                argument="branches",
            )
        return output
    raise QueryFailure(
        QueryFailureKind.INVALID_ARGUMENT, operator="expression", argument="kind"
    )


# --------------------------------------------------------------------------
# the operator validation table (query.rs:899-1897)
# --------------------------------------------------------------------------

def _row(
    expected: MatchRole | None, output: MatchRole, arguments: list[tuple[str, str]] | None = None
):
    return (expected, output, arguments)


_OPERATOR_TABLE = {
    # core.portable-value-query@1
    "core.portable-value-query/core.try-object-entries": _row(MatchRole.VALUE, MatchRole.OBJECT_ENTRY),
    "core.portable-value-query/core.object-entry-value": _row(MatchRole.OBJECT_ENTRY, MatchRole.VALUE),
    "core.portable-value-query/core.object-entry-name-equals": _row(
        MatchRole.OBJECT_ENTRY, MatchRole.OBJECT_ENTRY, [("name", "String")]
    ),
    "core.portable-value-query/core.try-entry-mapping-entries": _row(MatchRole.VALUE, MatchRole.ENTRY_MAPPING_ENTRY),
    "core.portable-value-query/core.entry-key": _row(MatchRole.ENTRY_MAPPING_ENTRY, MatchRole.VALUE),
    "core.portable-value-query/core.entry-value": _row(MatchRole.ENTRY_MAPPING_ENTRY, MatchRole.VALUE),
    "core.portable-value-query/core.try-sequence-elements": _row(MatchRole.VALUE, MatchRole.VALUE),
    "core.portable-value-query/core.where-type": _row(MatchRole.VALUE, MatchRole.VALUE, [("kind", "String")]),
    "core.portable-value-query/core.require-type": _row(MatchRole.VALUE, MatchRole.VALUE, [("kind", "String")]),

    # json.native-semantic-query@1|2
    "json.native-semantic-query/json.try-object-members": _row(MatchRole.JSON_VALUE, MatchRole.JSON_OBJECT_MEMBER),
    "json.native-semantic-query/json.member-name-equals": _row(
        MatchRole.JSON_OBJECT_MEMBER, MatchRole.JSON_OBJECT_MEMBER, [("name", "String")]
    ),
    "json.native-semantic-query/json.member-value": _row(MatchRole.JSON_OBJECT_MEMBER, MatchRole.JSON_VALUE),
    "json.native-semantic-query/json.try-array-elements": _row(MatchRole.JSON_VALUE, MatchRole.JSON_ARRAY_ELEMENT),
    "json.native-semantic-query/json.array-element-value": _row(MatchRole.JSON_ARRAY_ELEMENT, MatchRole.JSON_VALUE),

    # toml.native-semantic-query@1
    "toml.native-semantic-query/toml.try-table-entries": _row(MatchRole.TOML_ITEM, MatchRole.TOML_ENTRY),
    "toml.native-semantic-query/toml.entry-name-equals": _row(
        MatchRole.TOML_ENTRY, MatchRole.TOML_ENTRY, [("name", "String")]
    ),
    "toml.native-semantic-query/toml.entry-item": _row(MatchRole.TOML_ENTRY, MatchRole.TOML_ITEM),
    "toml.native-semantic-query/toml.try-array-elements": _row(MatchRole.TOML_ITEM, MatchRole.TOML_ARRAY_ELEMENT),
    "toml.native-semantic-query/toml.array-element-item": _row(MatchRole.TOML_ARRAY_ELEMENT, MatchRole.TOML_ITEM),

    # yaml.native-semantic-query@1
    "yaml.native-semantic-query/yaml.documents": _row(MatchRole.YAML_STREAM, MatchRole.YAML_DOCUMENT),
    "yaml.native-semantic-query/yaml.document-root": _row(MatchRole.YAML_DOCUMENT, MatchRole.YAML_NODE),
    "yaml.native-semantic-query/yaml.where-node-kind": _row(
        MatchRole.YAML_NODE, MatchRole.YAML_NODE, [("kind", "String")]
    ),
    "yaml.native-semantic-query/yaml.where-tag": _row(
        MatchRole.YAML_NODE, MatchRole.YAML_NODE, [("tag", "String")]
    ),
    "yaml.native-semantic-query/yaml.scalar-canonical-equals": _row(
        MatchRole.YAML_NODE, MatchRole.YAML_NODE, [("canonical", "String")]
    ),
    "yaml.native-semantic-query/yaml.try-sequence-elements": _row(MatchRole.YAML_NODE, MatchRole.YAML_SEQUENCE_ELEMENT),
    "yaml.native-semantic-query/yaml.sequence-element-node": _row(MatchRole.YAML_SEQUENCE_ELEMENT, MatchRole.YAML_NODE),
    "yaml.native-semantic-query/yaml.try-mapping-entries": _row(MatchRole.YAML_NODE, MatchRole.YAML_MAPPING_ENTRY),
    "yaml.native-semantic-query/yaml.mapping-entry-key": _row(MatchRole.YAML_MAPPING_ENTRY, MatchRole.YAML_NODE),
    "yaml.native-semantic-query/yaml.mapping-entry-value": _row(MatchRole.YAML_MAPPING_ENTRY, MatchRole.YAML_NODE),
    "yaml.native-semantic-query/yaml.anchor-definition": _row(MatchRole.YAML_NODE, MatchRole.YAML_ANCHOR_DEFINITION),
    "yaml.native-semantic-query/yaml.anchor-node": _row(MatchRole.YAML_ANCHOR_DEFINITION, MatchRole.YAML_NODE),
    "yaml.native-semantic-query/yaml.alias-occurrences": _row(MatchRole.YAML_STREAM, MatchRole.YAML_ALIAS_OCCURRENCE),
    "yaml.native-semantic-query/yaml.alias-target": _row(MatchRole.YAML_ALIAS_OCCURRENCE, MatchRole.YAML_NODE),

    # ini.native-semantic-query@1
    "ini.native-semantic-query/ini.document-sections": _row(MatchRole.INI_DOCUMENT, MatchRole.INI_SECTION),
    "ini.native-semantic-query/ini.section-entries": _row(MatchRole.INI_SECTION, MatchRole.INI_ENTRY),
    "ini.native-semantic-query/ini.all-entries": _row(MatchRole.INI_DOCUMENT, MatchRole.INI_ENTRY),
    "ini.native-semantic-query/ini.entry-section": _row(MatchRole.INI_ENTRY, MatchRole.INI_SECTION),
    "ini.native-semantic-query/ini.section-name-equals": _row(
        MatchRole.INI_SECTION, MatchRole.INI_SECTION,
        [("name", "String"), ("comparison", "String")],
    ),
    "ini.native-semantic-query/ini.entry-key-equals": _row(
        MatchRole.INI_ENTRY, MatchRole.INI_ENTRY, [("key", "String"), ("comparison", "String")]
    ),
    "ini.native-semantic-query/ini.entry-value-state-is": _row(
        MatchRole.INI_ENTRY, MatchRole.INI_ENTRY, [("state", "String")]
    ),
    # ini.duplicate-group is the input-dependent row (ROLE_ANY placeholder);
    # _check_input_dependent_roles types it by the input role.
    "ini.native-semantic-query/ini.duplicate-group": _row(ROLE_ANY, ROLE_ANY),
    "ini.native-semantic-query/ini.physical-lines": _row(MatchRole.INI_DOCUMENT, MatchRole.INI_PHYSICAL_LINE),
    "ini.native-semantic-query/ini.logical-lines": _row(MatchRole.INI_DOCUMENT, MatchRole.INI_LOGICAL_LINE),

    # java-properties.native-semantic-query@1
    "java-properties.native-semantic-query/properties.document-properties": _row(MatchRole.PROPERTIES_DOCUMENT, MatchRole.PROPERTIES_PROPERTY),
    "java-properties.native-semantic-query/properties.natural-lines": _row(MatchRole.PROPERTIES_DOCUMENT, MatchRole.PROPERTIES_NATURAL_LINE),
    "java-properties.native-semantic-query/properties.logical-lines": _row(MatchRole.PROPERTIES_DOCUMENT, MatchRole.PROPERTIES_LOGICAL_LINE),
    "java-properties.native-semantic-query/properties.logical-line-natural-lines": _row(MatchRole.PROPERTIES_LOGICAL_LINE, MatchRole.PROPERTIES_NATURAL_LINE),
    "java-properties.native-semantic-query/properties.property-key-equals": _row(
        MatchRole.PROPERTIES_PROPERTY, MatchRole.PROPERTIES_PROPERTY, [("key", "Bytes")]
    ),
    "java-properties.native-semantic-query/properties.property-value-state-is": _row(
        MatchRole.PROPERTIES_PROPERTY, MatchRole.PROPERTIES_PROPERTY, [("state", "String")]
    ),
    "java-properties.native-semantic-query/properties.property-escapes": _row(MatchRole.PROPERTIES_PROPERTY, MatchRole.PROPERTIES_ESCAPE),
    "java-properties.native-semantic-query/properties.duplicate-group": _row(MatchRole.PROPERTIES_PROPERTY, MatchRole.PROPERTIES_PROPERTY),

    # json.lossless-syntax-query@1|2
    "json.lossless-syntax-query/json.syntax-kind-is": _row(
        MatchRole.JSON_SYNTAX_PIECE, MatchRole.JSON_SYNTAX_PIECE, [("kind", "String")]
    ),
    "json.lossless-syntax-query/json.syntax-text-equals": _row(
        MatchRole.JSON_SYNTAX_PIECE, MatchRole.JSON_SYNTAX_PIECE, [("text", "String")]
    ),

    # toml.lossless-syntax-query@1
    "toml.lossless-syntax-query/toml.syntax-kind-is": _row(
        MatchRole.TOML_SYNTAX_PIECE, MatchRole.TOML_SYNTAX_PIECE, [("kind", "String")]
    ),
    "toml.lossless-syntax-query/toml.syntax-text-equals": _row(
        MatchRole.TOML_SYNTAX_PIECE, MatchRole.TOML_SYNTAX_PIECE, [("text", "String")]
    ),

    # yaml.lossless-syntax-query@1
    "yaml.lossless-syntax-query/yaml.syntax-kind-is": _row(
        MatchRole.YAML_SYNTAX_PIECE, MatchRole.YAML_SYNTAX_PIECE, [("kind", "String")]
    ),
    "yaml.lossless-syntax-query/yaml.syntax-text-equals": _row(
        MatchRole.YAML_SYNTAX_PIECE, MatchRole.YAML_SYNTAX_PIECE, [("text", "String")]
    ),

    # ini.lossless-syntax-query@1
    "ini.lossless-syntax-query/ini.syntax-kind-is": _row(
        MatchRole.INI_SYNTAX_PIECE, MatchRole.INI_SYNTAX_PIECE, [("kind", "String")]
    ),
    "ini.lossless-syntax-query/ini.syntax-text-equals": _row(
        MatchRole.INI_SYNTAX_PIECE, MatchRole.INI_SYNTAX_PIECE, [("text", "String")]
    ),

    # java-properties.lossless-syntax-query@1
    "java-properties.lossless-syntax-query/properties.syntax-kind-is": _row(
        MatchRole.PROPERTIES_SYNTAX_PIECE, MatchRole.PROPERTIES_SYNTAX_PIECE, [("kind", "String")]
    ),
    "java-properties.lossless-syntax-query/properties.syntax-text-equals": _row(
        MatchRole.PROPERTIES_SYNTAX_PIECE, MatchRole.PROPERTIES_SYNTAX_PIECE, [("text", "String")]
    ),
    "java-properties.lossless-syntax-query/properties.syntax-raw-bytes-equals": _row(
        MatchRole.PROPERTIES_SYNTAX_PIECE, MatchRole.PROPERTIES_SYNTAX_PIECE, [("bytes", "Bytes")]
    ),
    "java-properties.lossless-syntax-query/properties.syntax-utf16be-equals": _row(
        MatchRole.PROPERTIES_SYNTAX_PIECE, MatchRole.PROPERTIES_SYNTAX_PIECE, [("code_units", "Bytes")]
    ),

    # core.portable-graph-query@1
    "core.portable-graph-query/graph.reachable-nodes": _row(MatchRole.GRAPH_NODE, MatchRole.GRAPH_NODE),
    "core.portable-graph-query/graph.where-kind": _row(
        MatchRole.GRAPH_NODE, MatchRole.GRAPH_NODE, [("kind", "String")]
    ),
    "core.portable-graph-query/graph.where-tag": _row(
        MatchRole.GRAPH_NODE, MatchRole.GRAPH_NODE, [("tag", "String")]
    ),
    "core.portable-graph-query/graph.try-sequence-elements": _row(MatchRole.GRAPH_NODE, MatchRole.GRAPH_SEQUENCE_ELEMENT),
    "core.portable-graph-query/graph.sequence-element-node": _row(MatchRole.GRAPH_SEQUENCE_ELEMENT, MatchRole.GRAPH_NODE),
    "core.portable-graph-query/graph.try-mapping-entries": _row(MatchRole.GRAPH_NODE, MatchRole.GRAPH_MAPPING_ENTRY),
    "core.portable-graph-query/graph.mapping-entry-key": _row(MatchRole.GRAPH_MAPPING_ENTRY, MatchRole.GRAPH_NODE),
    "core.portable-graph-query/graph.mapping-entry-value": _row(MatchRole.GRAPH_MAPPING_ENTRY, MatchRole.GRAPH_NODE),

    # xml.native-semantic-query@1
    "xml.native-semantic-query/xml.document-root": _row(MatchRole.XML_DOCUMENT, MatchRole.XML_ELEMENT),
    "xml.native-semantic-query/xml.document-declaration": _row(MatchRole.XML_DOCUMENT, MatchRole.XML_DECLARATION),
    "xml.native-semantic-query/xml.document-doctype": _row(MatchRole.XML_DOCUMENT, MatchRole.XML_DOCTYPE),
    "xml.native-semantic-query/xml.document-prolog": _row(MatchRole.XML_DOCUMENT, MatchRole.XML_PROLOG_ITEM),
    "xml.native-semantic-query/xml.document-epilog": _row(MatchRole.XML_DOCUMENT, MatchRole.XML_PROLOG_ITEM),
    "xml.native-semantic-query/xml.element-children": _row(MatchRole.XML_ELEMENT, MatchRole.XML_CONTENT_ITEM),
    "xml.native-semantic-query/xml.element-child-elements": _row(MatchRole.XML_ELEMENT, MatchRole.XML_ELEMENT),
    "xml.native-semantic-query/xml.element-descendants": _row(MatchRole.XML_ELEMENT, MatchRole.XML_ELEMENT),
    "xml.native-semantic-query/xml.element-child-text": _row(MatchRole.XML_ELEMENT, MatchRole.XML_TEXT),
    "xml.native-semantic-query/xml.element-child-cdata": _row(MatchRole.XML_ELEMENT, MatchRole.XML_CDATA),
    "xml.native-semantic-query/xml.element-child-comments": _row(MatchRole.XML_ELEMENT, MatchRole.XML_COMMENT),
    "xml.native-semantic-query/xml.element-child-pi": _row(MatchRole.XML_ELEMENT, MatchRole.XML_PROCESSING_INSTRUCTION),
    "xml.native-semantic-query/xml.element-attributes": _row(MatchRole.XML_ELEMENT, MatchRole.XML_ATTRIBUTE),
    "xml.native-semantic-query/xml.element-namespace-bindings": _row(MatchRole.XML_ELEMENT, MatchRole.XML_NAMESPACE_BINDING),
    "xml.native-semantic-query/xml.element-in-scope-namespaces": _row(MatchRole.XML_ELEMENT, MatchRole.XML_NAMESPACE_BINDING),
    "xml.native-semantic-query/xml.text-references": _row(MatchRole.XML_TEXT, MatchRole.XML_REFERENCE),
    "xml.native-semantic-query/xml.content-parent": _row(ROLE_ANY, ROLE_ANY),
    "xml.native-semantic-query/xml.attribute-element": _row(ROLE_ANY, ROLE_ANY),
    "xml.native-semantic-query/xml.reference-text": _row(ROLE_ANY, ROLE_ANY),
    "xml.native-semantic-query/xml.name-equals": _row(
        ROLE_ANY, ROLE_ANY,
        [("prefix", "String"), ("local", "String"), ("namespace", "String"), ("comparison", "String")],
    ),
    "xml.native-semantic-query/xml.attribute-value-equals": _row(
        MatchRole.XML_ATTRIBUTE, MatchRole.XML_ATTRIBUTE, [("value", "String")]
    ),
    "xml.native-semantic-query/xml.pi-target-equals": _row(
        MatchRole.XML_PROCESSING_INSTRUCTION, MatchRole.XML_PROCESSING_INSTRUCTION, [("target", "String")]
    ),
    "xml.native-semantic-query/xml.reference-kind-is": _row(
        MatchRole.XML_REFERENCE, MatchRole.XML_REFERENCE, [("kind", "String")]
    ),
    "xml.native-semantic-query/xml.reference-name-equals": _row(
        MatchRole.XML_REFERENCE, MatchRole.XML_REFERENCE, [("name", "String")]
    ),
    "xml.native-semantic-query/xml.node-kind-is": _row(ROLE_ANY, ROLE_ANY, [("kind", "String")]),

    # xml.lossless-syntax-query@1
    "xml.lossless-syntax-query/xml.syntax-kind-is": _row(
        MatchRole.XML_SYNTAX_PIECE, MatchRole.XML_SYNTAX_PIECE, [("kind", "String")]
    ),
    "xml.lossless-syntax-query/xml.syntax-text-equals": _row(
        MatchRole.XML_SYNTAX_PIECE, MatchRole.XML_SYNTAX_PIECE, [("text", "String")]
    ),

    # plist.native-semantic-query@1
    "plist.native-semantic-query/plist.document-root": _row(MatchRole.PLIST_VALUE, MatchRole.PLIST_VALUE),
    "plist.native-semantic-query/plist.dict-entries": _row(MatchRole.PLIST_VALUE, MatchRole.PLIST_DICT_ENTRY),
    "plist.native-semantic-query/plist.dict-entry-key": _row(MatchRole.PLIST_DICT_ENTRY, MatchRole.PLIST_KEY),
    "plist.native-semantic-query/plist.dict-entry-value": _row(MatchRole.PLIST_DICT_ENTRY, MatchRole.PLIST_VALUE),
    "plist.native-semantic-query/plist.dict-key-equals": _row(
        MatchRole.PLIST_DICT_ENTRY, MatchRole.PLIST_DICT_ENTRY, [("key", "String")]
    ),
    "plist.native-semantic-query/plist.duplicate-key-group": _row(MatchRole.PLIST_DICT_ENTRY, MatchRole.PLIST_DICT_ENTRY),
    "plist.native-semantic-query/plist.array-elements": _row(MatchRole.PLIST_VALUE, MatchRole.PLIST_ARRAY_ELEMENT),
    "plist.native-semantic-query/plist.value-type-is": _row(ROLE_ANY, ROLE_ANY, [("kind", "String")]),
    "plist.native-semantic-query/plist.value-as-integer": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-real": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-string": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-data": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-date": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-uid": _row(ROLE_ANY, ROLE_ANY),
    "plist.native-semantic-query/plist.value-as-boolean-is": _row(ROLE_ANY, ROLE_ANY, [("value", "Boolean")]),

    # plist.lossless-syntax-query@1
    "plist.lossless-syntax-query/plist.syntax-kind-is": _row(
        MatchRole.PLIST_SYNTAX_PIECE, MatchRole.PLIST_SYNTAX_PIECE, [("kind", "String")]
    ),
    "plist.lossless-syntax-query/plist.syntax-text-equals": _row(
        MatchRole.PLIST_SYNTAX_PIECE, MatchRole.PLIST_SYNTAX_PIECE, [("text", "String")]
    ),

    # plist.binary-structure-query@1
    "plist.binary-structure-query/plist.object-table": _row(ROLE_ANY, MatchRole.PLIST_BINARY_OBJECT),
    "plist.binary-structure-query/plist.object-offset": _row(ROLE_ANY, MatchRole.PLIST_BINARY_OFFSET),
    "plist.binary-structure-query/plist.object-refs": _row(ROLE_ANY, MatchRole.PLIST_BINARY_REF),
    "plist.binary-structure-query/plist.offset-table": _row(ROLE_ANY, MatchRole.PLIST_BINARY_OFFSET),
    "plist.binary-structure-query/plist.trailer-facts": _row(ROLE_ANY, MatchRole.PLIST_BINARY_TRAILER),
    "plist.binary-structure-query/plist.top-object": _row(ROLE_ANY, MatchRole.PLIST_BINARY_OBJECT),

    # hcl.native-semantic-query@1
    "hcl.native-semantic-query/hcl.document-body": _row(MatchRole.HCL_BODY, MatchRole.HCL_BODY),
    "hcl.native-semantic-query/hcl.body-items": _row(MatchRole.HCL_BODY, MatchRole.HCL_ATTRIBUTE),
    "hcl.native-semantic-query/hcl.body-attributes": _row(MatchRole.HCL_BODY, MatchRole.HCL_ATTRIBUTE),
    "hcl.native-semantic-query/hcl.body-blocks": _row(MatchRole.HCL_BODY, MatchRole.HCL_BLOCK),
    "hcl.native-semantic-query/hcl.body-block-type-equals": _row(
        MatchRole.HCL_BODY, MatchRole.HCL_BLOCK, [("type", "String")]
    ),
    "hcl.native-semantic-query/hcl.attribute-name": _row(ROLE_ANY, ROLE_ANY),
    "hcl.native-semantic-query/hcl.attribute-name-equals": _row(ROLE_ANY, ROLE_ANY, [("name", "String")]),
    "hcl.native-semantic-query/hcl.attribute-expression": _row(ROLE_ANY, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.attribute-literal-value": _row(ROLE_ANY, ROLE_ANY, [("accessor", "String")]),
    "hcl.native-semantic-query/hcl.block-type": _row(ROLE_ANY, ROLE_ANY),
    "hcl.native-semantic-query/hcl.block-type-equals": _row(ROLE_ANY, ROLE_ANY, [("type", "String")]),
    "hcl.native-semantic-query/hcl.block-labels": _row(ROLE_ANY, MatchRole.HCL_BLOCK_LABEL),
    "hcl.native-semantic-query/hcl.block-nested-body": _row(ROLE_ANY, MatchRole.HCL_BODY),
    "hcl.native-semantic-query/hcl.block-label-equals": _row(
        MatchRole.HCL_BLOCK_LABEL, MatchRole.HCL_BLOCK_LABEL, [("label", "String")]
    ),
    "hcl.native-semantic-query/hcl.expression-kind-is": _row(
        MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION, [("kind", "String")]
    ),
    "hcl.native-semantic-query/hcl.expression-is-literal": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.expression-text": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.expression-children": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.template-parts": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_TEMPLATE_PART),
    "hcl.native-semantic-query/hcl.tuple-elements": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.object-entries": _row(MatchRole.HCL_EXPRESSION, MatchRole.HCL_EXPRESSION),
    "hcl.native-semantic-query/hcl.error-regions": _row(ROLE_ANY, MatchRole.HCL_ERROR_REGION),

    # hcl.lossless-syntax-query@1
    "hcl.lossless-syntax-query/hcl.syntax-kind-is": _row(
        MatchRole.HCL_SYNTAX_PIECE, MatchRole.HCL_SYNTAX_PIECE, [("kind", "String")]
    ),
    "hcl.lossless-syntax-query/hcl.syntax-text-equals": _row(
        MatchRole.HCL_SYNTAX_PIECE, MatchRole.HCL_SYNTAX_PIECE, [("text", "String")]
    ),
}


def _validate_operator(
    domain: QueryDomain, operator: OperatorCall, input_role: MatchRole
) -> MatchRole:
    if operator.version != 1:
        raise QueryFailure(
            QueryFailureKind.UNKNOWN_OPERATOR,
            operator=operator.id,
            version=operator.version,
        )
    key = f"{domain.id}/{operator.id}"
    row = _OPERATOR_TABLE.get(key)
    if row is None:
        # The domain-agnostic generic rows.
        if operator.id == "core.take":
            expected, output, arguments = input_role, input_role, [("count", "Integer")]
        elif operator.id == "core.distinct-by-identity":
            expected, output, arguments = input_role, input_role, None
        else:
            raise QueryFailure(
                QueryFailureKind.UNKNOWN_OPERATOR,
                operator=operator.id,
                version=operator.version,
            )
    else:
        expected, output, arguments = row
    if expected is not ROLE_ANY and input_role is not expected:
        raise QueryFailure(
            QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
            operator=operator.id,
            expected_role=expected,
            actual_role=input_role,
        )
    output = _check_input_dependent_roles(domain.id, operator.id, input_role, output, expected)
    if len(operator.arguments) != (len(arguments) if arguments else 0):
        raise QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT,
            operator=operator.id,
            argument="argument-set",
        )
    for name, kind_name in arguments or []:
        value = operator.arguments.get(name)
        if value is None or value.kind.value != kind_name:
            raise QueryFailure(
                QueryFailureKind.WRONG_ARGUMENT_TYPE,
                operator=operator.id,
                argument=name,
                expected_kind=kind_name,
            )
    _check_operator_arguments(domain, operator)
    return output


def _check_input_dependent_roles(
    domain_id: str,
    operator_id: str,
    input_role: MatchRole,
    output: MatchRole,
    expected: MatchRole | None,
) -> MatchRole:
    """Applies the role-union rows that accept several input roles."""
    if domain_id == "ini.native-semantic-query" and operator_id == "ini.duplicate-group":
        if input_role not in (MatchRole.INI_SECTION, MatchRole.INI_ENTRY):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.INI_SECTION,
                actual_role=input_role,
            )
        return input_role
    if domain_id == "xml.native-semantic-query" and operator_id in (
        "xml.content-parent", "xml.attribute-element", "xml.reference-text"
    ):
        if not _xml_content_input_roles(input_role):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.XML_CONTENT_ITEM,
                actual_role=input_role,
            )
        return MatchRole.XML_ELEMENT
    if domain_id == "xml.native-semantic-query" and operator_id == "xml.name-equals":
        return input_role
    if domain_id == "xml.native-semantic-query" and operator_id == "xml.node-kind-is":
        if not _xml_node_kind_roles(input_role):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.XML_DOCUMENT,
                actual_role=input_role,
            )
        return input_role
    if domain_id == "plist.native-semantic-query" and operator_id in (
        "plist.value-type-is", "plist.value-as-integer", "plist.value-as-real",
        "plist.value-as-string", "plist.value-as-data", "plist.value-as-date",
        "plist.value-as-uid", "plist.value-as-boolean-is",
    ):
        if input_role not in (MatchRole.PLIST_VALUE, MatchRole.PLIST_ARRAY_ELEMENT):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.PLIST_VALUE,
                actual_role=input_role,
            )
        return input_role
    if domain_id == "plist.binary-structure-query":
        if not _plist_binary_input_roles(input_role):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.PLIST_BINARY_STRUCTURE,
                actual_role=input_role,
            )
        return output
    if domain_id == "hcl.native-semantic-query" and operator_id in (
        "hcl.attribute-name", "hcl.attribute-name-equals", "hcl.block-type",
        "hcl.block-type-equals",
    ):
        if input_role not in (MatchRole.HCL_ATTRIBUTE, MatchRole.HCL_BLOCK):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.HCL_ATTRIBUTE,
                actual_role=input_role,
            )
        return input_role
    if domain_id == "hcl.native-semantic-query" and operator_id == "hcl.attribute-literal-value":
        if input_role not in (MatchRole.HCL_EXPRESSION, MatchRole.HCL_ATTRIBUTE):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.HCL_EXPRESSION,
                actual_role=input_role,
            )
        return input_role
    if domain_id == "hcl.native-semantic-query" and operator_id in (
        "hcl.attribute-expression", "hcl.block-labels", "hcl.block-nested-body"
    ):
        if input_role not in (MatchRole.HCL_ATTRIBUTE, MatchRole.HCL_BLOCK):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.HCL_ATTRIBUTE,
                actual_role=input_role,
            )
        return output
    if domain_id == "hcl.native-semantic-query" and operator_id == "hcl.error-regions":
        if not _hcl_error_region_input_roles(input_role):
            raise QueryFailure(
                QueryFailureKind.INVALID_OPERATOR_COMPOSITION,
                operator=operator_id,
                expected_role=MatchRole.HCL_BODY,
                actual_role=input_role,
            )
        return MatchRole.HCL_ERROR_REGION
    return output


def _xml_content_input_roles(role: MatchRole) -> bool:
    return role in (
        MatchRole.XML_CONTENT_ITEM, MatchRole.XML_ATTRIBUTE, MatchRole.XML_NAMESPACE_BINDING,
        MatchRole.XML_REFERENCE, MatchRole.XML_ELEMENT, MatchRole.XML_TEXT, MatchRole.XML_CDATA,
        MatchRole.XML_COMMENT, MatchRole.XML_PROCESSING_INSTRUCTION,
    )


def _xml_node_kind_roles(role: MatchRole) -> bool:
    return role in (
        MatchRole.XML_DOCUMENT, MatchRole.XML_DECLARATION, MatchRole.XML_DOCTYPE,
        MatchRole.XML_PROLOG_ITEM, MatchRole.XML_ELEMENT, MatchRole.XML_CONTENT_ITEM,
        MatchRole.XML_ATTRIBUTE, MatchRole.XML_NAMESPACE_BINDING, MatchRole.XML_TEXT,
        MatchRole.XML_CDATA, MatchRole.XML_COMMENT, MatchRole.XML_PROCESSING_INSTRUCTION,
        MatchRole.XML_REFERENCE, MatchRole.XML_ERROR_REGION,
    )


def _plist_binary_input_roles(role: MatchRole) -> bool:
    return role in (
        MatchRole.PLIST_BINARY_STRUCTURE, MatchRole.PLIST_BINARY_OBJECT,
        MatchRole.PLIST_BINARY_OFFSET, MatchRole.PLIST_BINARY_REF,
        MatchRole.PLIST_BINARY_TRAILER,
    )


def _hcl_error_region_input_roles(role: MatchRole) -> bool:
    return role in (
        MatchRole.HCL_BODY, MatchRole.HCL_ATTRIBUTE, MatchRole.HCL_BLOCK,
        MatchRole.HCL_BLOCK_LABEL, MatchRole.HCL_EXPRESSION, MatchRole.HCL_TEMPLATE_PART,
        MatchRole.HCL_ERROR_REGION,
    )


# --------------------------------------------------------------------------
# semantic argument-value checks (query.rs:1634-1897)
# --------------------------------------------------------------------------

def _check_operator_arguments(domain: QueryDomain, operator: OperatorCall) -> None:
    def string_arg(name: str) -> str | None:
        value = operator.arguments.get(name)
        if value is None:
            return None
        if value.kind is not Kind.STRING:
            return None
        return value.as_string()

    def fail(argument: str) -> QueryFailure:
        return QueryFailure(
            QueryFailureKind.INVALID_ARGUMENT, operator=operator.id, argument=argument
        )

    operator_id = operator.id
    if operator_id == "core.take":
        number = operator.arguments["count"].as_integer()
        if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
            raise fail("count")
    elif operator_id in ("core.where-type", "core.require-type"):
        kind = string_arg("kind")
        if not _is_value_kind_name(kind or ""):
            # The Go/Rust row names the operator "value-kind" here.
            raise QueryFailure(
                QueryFailureKind.INVALID_ARGUMENT,
                operator="value-kind",
                argument=kind or "",
            )
    elif operator_id == "json.syntax-kind-is":
        if not _is_json_syntax_kind(domain.version, string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "toml.syntax-kind-is":
        if not _is_toml_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "yaml.syntax-kind-is":
        if not _is_yaml_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "ini.syntax-kind-is":
        if not _is_ini_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "properties.syntax-kind-is":
        if not _is_properties_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "xml.syntax-kind-is":
        if not _is_xml_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "plist.value-type-is":
        if not _is_plist_value_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "plist.syntax-kind-is":
        if not _is_plist_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "hcl.expression-kind-is":
        if not _is_hcl_expression_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "hcl.syntax-kind-is":
        if not _is_hcl_syntax_kind(string_arg("kind") or ""):
            raise fail("kind")
    elif operator_id == "hcl.attribute-literal-value":
        if not _is_hcl_literal_accessor(string_arg("accessor") or ""):
            raise fail("accessor")
    elif operator_id in ("properties.property-key-equals", "properties.syntax-utf16be-equals"):
        # The Bytes-typed arguments are validated against the kind
        # vocabulary; the even-length check is verbatim (a UTF16BE/1
        # argument must carry a whole number of code units).
        name = "key" if operator_id == "properties.property-key-equals" else "code_units"
        value = operator.arguments.get(name)
        if value is None or value.kind is not Kind.BYTES or len(value.as_bytes()) % 2 != 0:
            raise fail(name)
    elif operator_id == "properties.property-value-state-is":
        if string_arg("state") not in ("ImplicitEmpty", "ExplicitEmpty", "Present"):
            raise fail("state")
    elif operator_id in ("ini.section-name-equals", "ini.entry-key-equals"):
        if string_arg("comparison") not in ("OriginalExact", "ProfileEquivalent"):
            raise fail("comparison")
    elif operator_id == "ini.entry-value-state-is":
        if string_arg("state") not in ("Missing", "Empty", "Present"):
            raise fail("state")
    elif operator_id == "yaml.where-node-kind":
        if string_arg("kind") not in ("Scalar", "Sequence", "Mapping"):
            raise fail("kind")
    elif operator_id == "yaml.where-tag":
        if not string_arg("tag"):
            raise fail("tag")
    elif operator_id == "graph.where-kind":
        if string_arg("kind") not in ("Scalar", "Sequence", "Mapping"):
            raise fail("kind")
    elif operator_id == "graph.where-tag":
        if not string_arg("tag"):
            raise fail("tag")


def _is_value_kind_name(kind: str) -> bool:
    """The frozen fifteen-kind vocabulary of the value-kind arguments."""
    return kind in (
        "Null", "Boolean", "Integer", "Decimal", "BinaryFloat32", "BinaryFloat64",
        "String", "Bytes", "Date", "Time", "LocalDateTime", "OffsetDateTime",
        "Sequence", "Object", "EntryMapping",
    )


def _is_json_syntax_kind(domain_version: int, kind: str) -> bool:
    if kind in (
        "Bom", "Whitespace", "LineComment", "BlockComment", "LeftBrace", "RightBrace",
        "LeftBracket", "RightBracket", "Colon", "Comma", "String", "Number",
        "True", "False", "Null", "ErrorRegion",
    ):
        return True
    return domain_version == 2 and kind == "Identifier"


def _is_toml_syntax_kind(kind: str) -> bool:
    return kind in (
        "Whitespace", "Newline", "Comment", "String", "Bare", "Equals",
        "LeftBracket", "RightBracket", "LeftBrace", "RightBrace", "Comma", "Dot",
    )


def _is_yaml_syntax_kind(kind: str) -> bool:
    return kind in (
        "Bom", "Whitespace", "Newline", "Comment", "Directive", "DocumentStart",
        "DocumentEnd", "FlowSequenceStart", "FlowSequenceEnd", "FlowMappingStart",
        "FlowMappingEnd", "FlowEntry", "SequenceEntry", "ExplicitKey", "MappingValue",
        "Anchor", "Alias", "Tag", "PlainScalar", "SingleQuotedScalar",
        "DoubleQuotedScalar", "LiteralBlockHeader", "FoldedBlockHeader",
        "BlockScalarContent", "ErrorRegion",
    )


def _is_ini_syntax_kind(kind: str) -> bool:
    return kind in (
        "Bom", "Whitespace", "LineBreak", "CommentMarker", "CommentText",
        "SectionOpen", "SectionName", "SectionClose", "EntryKey", "Delimiter",
        "Quote", "EntryValue", "ContinuationMarker", "ErrorRegion",
    )


def _is_properties_syntax_kind(kind: str) -> bool:
    return kind in (
        "Bom", "Whitespace", "LineBreak", "CommentMarker", "CommentText",
        "Key", "Separator", "Value", "EscapeMarker", "EscapeBody",
        "ContinuationMarker", "ErrorRegion",
    )


def _is_xml_syntax_kind(kind: str) -> bool:
    return kind in (
        "bom", "whitespace", "line-break", "declaration-open", "declaration-name",
        "declaration-value", "declaration-close", "doctype-open", "doctype-name",
        "dtd-markup", "doctype-close", "tag-open", "tag-close",
        "empty-element-close", "end-tag-open", "prefix", "local-name", "colon",
        "attribute-name", "equals", "quote", "attribute-value",
        "namespace-declaration", "text", "entity-reference", "character-reference",
        "cdata-open", "cdata-text", "cdata-close", "comment-open", "comment-text",
        "comment-close", "processing-instruction-open",
        "processing-instruction-target", "processing-instruction-content",
        "processing-instruction-close", "error-region",
    )


def _is_plist_value_kind(kind: str) -> bool:
    return kind in ("dict", "array", "string", "integer", "real", "boolean", "date", "data", "uid")


def _is_plist_syntax_kind(kind: str) -> bool:
    return kind in (
        "bom", "whitespace", "line-break", "declaration-open", "declaration-name",
        "declaration-value", "declaration-close", "doctype-open", "doctype-body",
        "doctype-close", "plist-open", "plist-version-name", "plist-version-value",
        "plist-close", "dict-open", "dict-close", "key-open", "key-close",
        "array-open", "array-close", "string-open", "string-close", "integer-open",
        "integer-close", "real-open", "real-close", "date-open", "date-close",
        "data-open", "data-close", "true", "false", "text", "entity-reference",
        "character-reference", "cdata-open", "cdata-text", "cdata-close",
        "comment-open", "comment-text", "comment-close",
        "processing-instruction-open", "processing-instruction-target",
        "processing-instruction-content", "processing-instruction-close",
        "error-region",
    )


def _is_hcl_expression_kind(kind: str) -> bool:
    return kind in (
        "number", "boolean", "null", "template", "function-call", "variable-ref",
        "traversal", "unary", "binary", "conditional", "for-tuple", "for-object",
        "tuple", "object", "parenthesized",
    )


def _is_hcl_syntax_kind(kind: str) -> bool:
    return kind in (
        "Whitespace", "LineBreak", "LineComment", "InlineComment", "Identifier",
        "Equals", "Number", "StringOpen", "StringContent", "StringClose",
        "InterpolationOpen", "InterpolationContent", "InterpolationClose",
        "DirectiveOpen", "DirectiveContent", "DirectiveClose", "HeredocOpen",
        "HeredocContent", "HeredocClose", "BraceOpen", "BraceClose", "BracketOpen",
        "BracketClose", "ParenOpen", "ParenClose", "Comma", "Colon", "QuestionMark",
        "Operator", "ErrorRegion",
    )


def _is_hcl_literal_accessor(accessor: str) -> bool:
    return accessor in ("as-string", "as-integer", "as-real", "as-boolean-is", "as-null-is")


# --------------------------------------------------------------------------
# the core.query-definition@1 protocol codec
# --------------------------------------------------------------------------

def _query_protocol_error(field: str) -> QueryFailure:
    return QueryFailure(
        QueryFailureKind.INVALID_ARGUMENT, operator="core.query-definition@1", argument=field
    )


def _query_unsigned32(value: PortableValue, name: str) -> int:
    if value.kind is not Kind.INTEGER:
        raise _query_protocol_error(name)
    number = value.as_integer()
    if number < 0 or number > 0xFFFFFFFF:
        raise _query_protocol_error(name)
    return number


def _exact_object_fields(value: PortableValue, names: list[str], context: str) -> list[PortableValue]:
    if value.kind is not Kind.OBJECT:
        raise _query_protocol_error(context)
    entries = value.as_object()
    if len(entries) != len(names):
        raise _query_protocol_error(context)
    values = []
    for index, (key, entry_value) in enumerate(entries):
        if key != names[index]:
            raise _query_protocol_error(context)
        values.append(entry_value)
    return values


class QueryDefinitionCodec:
    """The fixed-field `core.query-definition@1` wire codec (query.rs:532-598)."""

    @staticmethod
    def to_value(definition: QueryDefinition) -> PortableValue:
        expression = QueryDefinitionCodec._encode_expression(definition.expression, 0)
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.query-definition@1")),
                ("domain_id", PortableValue.string(definition.domain.id)),
                ("domain_version", PortableValue.integer(definition.domain.version)),
                ("selection", PortableValue.string(definition.selection.value)),
                ("expression", expression),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue) -> QueryDefinition:
        fields = _exact_object_fields(
            value,
            ["schema", "domain_id", "domain_version", "selection", "expression"],
            "core.query-definition@1",
        )
        if fields[0].kind is not Kind.STRING or fields[0].as_string() != "core.query-definition@1":
            raise _query_protocol_error("schema")
        if fields[1].kind is not Kind.STRING:
            raise _query_protocol_error("domain_id")
        domain_version = _query_unsigned32(fields[2], "domain_version")
        if fields[3].kind is not Kind.STRING:
            raise _query_protocol_error("selection")
        try:
            selection = QuerySelection(fields[3].as_string())
        except ValueError:
            raise _query_protocol_error("selection") from None
        expression = QueryDefinitionCodec._decode_expression(fields[4], 0)
        return (
            QueryDefinition(QueryDomain(fields[1].as_string(), domain_version))
            .with_expression(expression)
            .with_selection(selection)
        )

    @staticmethod
    def _encode_expression(expression: QueryExpression, depth: int) -> PortableValue:
        if depth > 256:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        if expression.kind is ExpressionKind.INPUT:
            return PortableValue.object([("kind", PortableValue.string("Input"))])
        if expression.kind is ExpressionKind.APPLY:
            input_value = QueryDefinitionCodec._encode_expression(expression.input, depth + 1)
            operator = QueryDefinitionCodec._encode_operator(expression.operator)
            return PortableValue.object(
                [
                    ("kind", PortableValue.string("Apply")),
                    ("input", input_value),
                    ("operator", operator),
                ]
            )
        if expression.kind in (ExpressionKind.CONCAT, ExpressionKind.STRUCTURE_ORDER_MERGE):
            kind = "Concat"
            if expression.kind is ExpressionKind.STRUCTURE_ORDER_MERGE:
                kind = "StructureOrderMerge"
            branches = [
                QueryDefinitionCodec._encode_expression(branch, depth + 1)
                for branch in expression.branches
            ]
            return PortableValue.object(
                [
                    ("kind", PortableValue.string(kind)),
                    ("branches", PortableValue.sequence(branches)),
                ]
            )
        raise _query_protocol_error("expression.kind")

    @staticmethod
    def _encode_operator(operator: OperatorCall) -> PortableValue:
        arguments = [
            (name, operator.arguments[name]) for name in sorted(operator.arguments)
        ]
        return PortableValue.object(
            [
                ("id", PortableValue.string(operator.id)),
                ("version", PortableValue.integer(operator.version)),
                ("arguments", PortableValue.object(arguments)),
            ]
        )

    @staticmethod
    def _decode_expression(value: PortableValue, depth: int) -> QueryExpression:
        if depth > 256:
            raise QueryFailure(QueryFailureKind.RESOURCE_LIMIT)
        if value.kind is not Kind.OBJECT:
            raise _query_protocol_error("expression")
        entries = value.as_object()
        if not entries or entries[0][0] != "kind":
            raise _query_protocol_error("expression.kind")
        kind_node = entries[0][1]
        if kind_node.kind is not Kind.STRING:
            raise _query_protocol_error("expression.kind")
        kind_name = kind_node.as_string()
        if kind_name == "Input":
            if len(entries) != 1:
                raise _query_protocol_error("expression.kind")
            return QueryExpression(ExpressionKind.INPUT)
        if kind_name == "Apply":
            fields = _exact_object_fields(value, ["kind", "input", "operator"], "Apply")
            input_expression = QueryDefinitionCodec._decode_expression(fields[1], depth + 1)
            operator = QueryDefinitionCodec._decode_operator(fields[2])
            return QueryExpression(
                ExpressionKind.APPLY, input=input_expression, operator=operator
            )
        if kind_name in ("Concat", "StructureOrderMerge"):
            fields = _exact_object_fields(value, ["kind", "branches"], kind_name)
            if fields[1].kind is not Kind.SEQUENCE:
                raise _query_protocol_error("expression.branches")
            branches = [
                QueryDefinitionCodec._decode_expression(branch, depth + 1)
                for branch in fields[1].as_sequence()
            ]
            expression_kind = (
                ExpressionKind.CONCAT
                if kind_name == "Concat"
                else ExpressionKind.STRUCTURE_ORDER_MERGE
            )
            return QueryExpression(expression_kind, branches=branches)
        raise _query_protocol_error("expression.kind")

    @staticmethod
    def _decode_operator(value: PortableValue) -> OperatorCall:
        fields = _exact_object_fields(value, ["id", "version", "arguments"], "operator")
        if fields[0].kind is not Kind.STRING:
            raise _query_protocol_error("operator.id")
        version = _query_unsigned32(fields[1], "operator.version")
        if fields[2].kind is not Kind.OBJECT:
            raise _query_protocol_error("operator.arguments")
        operator = OperatorCall(fields[0].as_string(), version)
        for key, argument_value in fields[2].as_object():
            operator.with_argument(key, argument_value)
        return operator
