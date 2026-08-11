"""Normalized-result differential of the Python pipeline against the Rust
authority (roadmap 搂11.2 language-neutral behavior surface;
docs/five-language-ci-design.md 搂3.3; the Go twin is
go/conformance/differential/normalized/).

The Python implementation runs the same data-driven input set
(go/conformance/differential/normalized/cases.json) through its own
parse -> query/project/materialize/edit -> source pipeline and emits the
same line-oriented ``key=value`` fact vocabulary the Rust example
(crates/consema-conformance/examples/emit_normalized_results.rs) emits, so
the two sides can be compared field by field (case id + field + both values
on divergence; error text never participates).

The comparison is bidirectional (milestone 0.19.0 G5.2 shape, extended to
Python): the forward direction compares the Python facts with the Rust
evidence files (CONSEMA_DIFFERENTIAL_NORMALIZED_RUST_DIR, provisioned by
scripts/python-verify-normalized-differential.ps1), and the reverse
direction emits the Python evidence files
(CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR) which the Rust example's
``--consume`` mode recomputes and compares field by field. Without the
environment variables the tests skip (documented, never silent) and only
the case-file integrity checks run.

The fact vocabulary is the language-neutral surface: parse formation,
diagnostic code/order (never text), query count/identity/order, projection/
materialization reports, edit result bytes or failure codes, and
resource-limit completion semantics. Any divergence is a finding for the
roadmap 搂11.3 process, never a silent Python-side "fix".
"""

from __future__ import annotations

import dataclasses
import json
import os

from consema.differential import case_files
from consema.core.value import Decimal, EntryMappingBuilder, PortableValue
from consema.document.ids import MaterializationStyleId, ProfileId
from consema.document.limits import ParseLimits
from consema.document.materialization import (
    CompleteMaterialization,
    MaterializationLimits,
    MaterializationRequest,
    NewlinePolicy,
)
from consema.document.source import (
    BomPolicy,
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceLimits,
    SourceSnapshot,
    WindowsCodePage,
)
from consema.document.source_patch import (
    SourcePatch,
    SourcePatchError,
    SourcePatchLimits,
    SourceReplacement,
)
from consema.document.structural import AssociationPlacement, NodeRef
from consema.ini import edit as ini_edit
from consema.ini import errors as ini_errors
from consema.ini import kinds as ini_kinds
from consema.ini import materialization as ini_materialization
from consema.ini import parser as ini_parser
from consema.ini import projection as ini_projection
from consema.ini import query as ini_query
from consema.json import document as json_document
from consema.json import edit as json_edit
from consema.json import errors as json_errors
from consema.json import kinds as json_kinds
from consema.json import materialization as json_materialization
from consema.json import parser as json_parser
from consema.json import projection as json_projection
from consema.json import query as json_query
from consema.properties import edit as properties_edit
from consema.properties import errors as properties_errors
from consema.properties import kinds as properties_kinds
from consema.properties import limits as properties_limits
from consema.properties import materialization as properties_materialization
from consema.properties import parser as properties_parser
from consema.properties import projection as properties_projection
from consema.properties import query as properties_query
from consema.protocol import query as protocol_query
from consema.protocol.canonical import decode_json
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import CapabilityId, CapabilitySet
from consema.toml import edits as toml_edits
from consema.toml import errors as toml_errors
from consema.toml import document as toml_document
from consema.toml import materialization as toml_materialization
from consema.toml import parser as toml_parser
from consema.toml import projection as toml_projection
from consema.toml import query as toml_query
from consema.yaml import edit as yaml_edit
from consema.yaml import errors as yaml_errors
from consema.yaml import document as yaml_document
from consema.yaml import kinds as yaml_kinds
from consema.yaml import materialization as yaml_materialization
from consema.yaml import parser as yaml_parser
from consema.yaml import projection as yaml_projection
from consema.yaml import query as yaml_query

CASE_FILE = os.path.join("normalized", "cases.json")
MANIFEST = "consema.differential.normalized@1"

# The golden evidence directory (Rust emits, this module compares) and the
# Python evidence directory (this module emits, the Rust --consume mode
# compares): docs/five-language-ci-design.md 搂3.3.
RUST_DIR_ENV = "CONSEMA_DIFFERENTIAL_NORMALIZED_RUST_DIR"
PYTHON_DIR_ENV = "CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR"

# Query domain mapping (the Go runner's match, runner.go:794).
_NATIVE_DOMAINS = {
    ("json.native-semantic-query", 1): protocol_query.domain_json_native_v1,
    ("json.native-semantic-query", 2): protocol_query.domain_json_native_v2,
    ("toml.native-semantic-query", 1): protocol_query.domain_toml_native_v1,
    ("yaml.native-semantic-query", 1): protocol_query.domain_yaml_native_v1,
    ("ini.native-semantic-query", 1): protocol_query.domain_ini_native_v1,
    ("java-properties.native-semantic-query", 1): protocol_query.domain_java_properties_native_v1,
}
_SYNTAX_DOMAINS = {
    ("json.lossless-syntax-query", 1): protocol_query.domain_json_lossless_syntax_v1,
    ("json.lossless-syntax-query", 2): protocol_query.domain_json_lossless_syntax_v2,
    ("toml.lossless-syntax-query", 1): protocol_query.domain_toml_lossless_syntax_v1,
    ("yaml.lossless-syntax-query", 1): protocol_query.domain_yaml_lossless_syntax_v1,
    ("ini.lossless-syntax-query", 1): protocol_query.domain_ini_lossless_syntax_v1,
    ("java-properties.lossless-syntax-query", 1): (
        protocol_query.domain_java_properties_lossless_syntax_v1
    ),
}

_JSON_PROFILES = {
    "json.strict@1": json_kinds.JsonProfile.STRICT_V1,
    "jsonc.bounded@1": json_kinds.JsonProfile.JSONC_BOUNDED_V1,
    "json5.standard@1": json_kinds.JsonProfile.JSON5_STANDARD_V1,
}
_YAML_PROFILES = {
    "yaml.1.2-core@1": yaml_kinds.YamlProfile.YAML12_CORE_V1,
    "yaml.1.1-compat@1": yaml_kinds.YamlProfile.YAML11_COMPAT_V1,
}
_INI_PROFILES = {
    "ini.portable@1": ini_kinds.IniProfile.PORTABLE_V1,
    "ini.windows@1": ini_kinds.IniProfile.WINDOWS_V1,
    "ini.python-configparser@1": ini_kinds.IniProfile.PYTHON_CONFIGPARSER_V1,
}
_PROPERTIES_PROFILES = {
    "java-properties.reader@1": properties_kinds.PropertiesProfile.READER_V1,
    "java-properties.latin1@1": properties_kinds.PropertiesProfile.LATIN1_V1,
}


@dataclasses.dataclass(frozen=True)
class NormalizedResult:
    """The forward differential run outcome."""

    passed: int = 0
    failed: int = 0
    failures: tuple[str, ...] = ()
    emitted: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed


# ---------------------------------------------------------------------------
# Case file loading (mirrors normalized_test.go loadCaseFile)
# ---------------------------------------------------------------------------


def load_case_file() -> list[dict]:
    """Loads and validates the checked-in case set: manifest id, exact count,
    unique ids, and per-kind schema validity."""
    cases = case_files.load_case_file(CASE_FILE, MANIFEST, case_files.NORMALIZED_EXACT)
    for case in cases:
        case_id = case.get("id", "")
        kind = case.get("kind")
        if kind == "document":
            format_name = case.get("format", "")
            if format_name not in ("json", "toml", "yaml", "ini", "properties"):
                raise case_files.CaseFileError(
                    f"case {case_id}: unknown format {format_name!r}"
                )
            parse_document_profile(case)
            steps = case.get("steps")
            if not isinstance(steps, list) or not steps:
                raise case_files.CaseFileError(f"case {case_id}: document case without steps")
            for step in steps:
                op = step.get("op")
                if op not in ("parse", "query-native", "query-syntax", "project", "materialize", "edit"):
                    raise case_files.CaseFileError(f"case {case_id}: unknown step op {op!r}")
                if op in ("query-native", "query-syntax") and not step.get("domain"):
                    raise case_files.CaseFileError(f"case {case_id}: query step without a domain")
                if op == "project" and not step.get("target"):
                    raise case_files.CaseFileError(f"case {case_id}: project step without a target")
                if op == "materialize" and (not step.get("target_profile") or not step.get("style")):
                    raise case_files.CaseFileError(
                        f"case {case_id}: materialize step without target_profile/style"
                    )
                if op == "edit":
                    operations = step.get("operations")
                    if not isinstance(operations, list) or not operations:
                        raise case_files.CaseFileError(f"case {case_id}: edit step without operations")
                    for operation in operations:
                        if not operation.get("operation"):
                            raise case_files.CaseFileError(
                                f"case {case_id}: edit operation without a name"
                            )
                        target = operation.get("target")
                        if not isinstance(target, dict) or not target.get("kind"):
                            raise case_files.CaseFileError(
                                f"case {case_id}: edit operation {operation.get('operation')} without a target"
                            )
        elif kind == "source":
            if not isinstance(case.get("input"), dict):
                raise case_files.CaseFileError(f"case {case_id}: source case without input")
            request = case.get("request")
            if not isinstance(request, dict) or not request.get("profile_default"):
                raise case_files.CaseFileError(
                    f"case {case_id}: source case without request.profile_default"
                )
        else:
            raise case_files.CaseFileError(f"case {case_id}: unknown kind {kind!r}")
    return cases


def parse_document_profile(case: dict) -> object:
    """Resolves the case profile (runner.go parseDocumentProfile)."""
    format_name = case.get("format", "")
    profile_name = case.get("profile", "")
    if format_name == "json":
        profile = _JSON_PROFILES.get(profile_name)
        if profile is not None:
            return profile
    if format_name == "toml":
        if profile_name == "toml.1.0@1":
            return toml_document.TomlProfile.TOML10_V1
    if format_name == "yaml":
        profile = _YAML_PROFILES.get(profile_name)
        if profile is not None:
            return profile
    if format_name == "ini":
        profile = _INI_PROFILES.get(profile_name)
        if profile is not None:
            return profile
    if format_name == "properties":
        profile = _PROPERTIES_PROFILES.get(profile_name)
        if profile is not None:
            return profile
    raise case_files.CaseFileError(
        f"case {case.get('id', '?')}: unknown format/profile {format_name!r}/{profile_name!r}"
    )


# ---------------------------------------------------------------------------
# Fact emission helpers (the vocabulary is defined here and mirrored by the
# Rust example; no Python internal type names appear)
# ---------------------------------------------------------------------------


class Facts:
    """The ordered key=value fact set of one case. The key set is fixed:
    every document case emits exactly the same keys in the same order, so a
    missing or extra key is itself a differential failure."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def set(self, key: str, value: str) -> None:
        self.lines.append(key + "=" + value)


def escape(text: str) -> str:
    """JSON string escaping for the evidence files (the Go runner's
    ``escape``, runner.go:233-295, mirrored verbatim): short escapes for the
    JSON whitespace set, \\u00xx lowercase hex for the other control
    characters, everything else passed through as UTF-8."""
    output: list[str] = []
    for character in text:
        if character == '"':
            output.append('\\"')
        elif character == "\\":
            output.append("\\\\")
        elif character == "\b":
            output.append("\\b")
        elif character == "\f":
            output.append("\\f")
        elif character == "\n":
            output.append("\\n")
        elif character == "\r":
            output.append("\\r")
        elif character == "\t":
            output.append("\\t")
        elif ord(character) < 0x20:
            output.append(f"\\u{ord(character):04x}")
        else:
            output.append(character)
    return "".join(output)


def escape_bytes(data: bytes) -> str:
    """The Go ``escape(string(bytes))`` behavior: UTF-8 lossy conversion
    (one U+FFFD per invalid run, Rust from_utf8_lossy semantics) followed by
    the escaping above."""
    return escape(data.decode("utf-8", "replace"))


def join(items: list[str]) -> str:
    """Renders one ordered list into the ``|``-joined fact vocabulary."""
    return "|".join(items)


# ---------------------------------------------------------------------------
# Document face
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DocState:
    """The execution state of one document case."""

    format_name: str = ""
    profile_name: str = ""
    profile: object = None
    foreign_source: str = ""
    foreign_source_hex: str = ""
    parse_limits: ParseLimits = dataclasses.field(default_factory=ParseLimits)

    doc: object = None  # JsonDocument
    toml_doc: object = None
    yaml_doc: object = None
    ini_doc: object = None
    properties_doc: object = None
    foreign: object = None
    foreign_toml: object = None
    foreign_yaml: object = None
    foreign_ini: object = None
    foreign_properties: object = None

    # parse facts
    formation: str = ""
    diagnostic_codes: str = ""
    root_kind: str = ""
    native: str = ""

    # step run latches (each key set is emitted exactly once)
    query_native_run: bool = False
    query_syntax_run: bool = False
    project_run: bool = False
    materialize_run: bool = False
    edit_run: bool = False

    # projection result
    value: PortableValue | None = None
    projected: bool = False

    def document_parsed(self) -> bool:
        return (
            self.doc is not None
            or self.toml_doc is not None
            or self.yaml_doc is not None
            or self.ini_doc is not None
            or self.properties_doc is not None
        )


class FatalFormation(Exception):
    """One fatal parse failure carrying its stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parse_limits(case: dict) -> ParseLimits:
    limits = ParseLimits()
    desc = case.get("parse_limits")
    if isinstance(desc, dict):
        if isinstance(desc.get("max_source_bytes"), int):
            limits = dataclasses.replace(limits, max_source_bytes=desc["max_source_bytes"])
        if isinstance(desc.get("max_nesting_depth"), int):
            limits = dataclasses.replace(limits, max_nesting_depth=desc["max_nesting_depth"])
        if isinstance(desc.get("max_token_count"), int):
            limits = dataclasses.replace(limits, max_token_count=desc["max_token_count"])
        if isinstance(desc.get("max_node_count"), int):
            limits = dataclasses.replace(limits, max_node_count=desc["max_node_count"])
        if isinstance(desc.get("max_diagnostics"), int):
            limits = dataclasses.replace(limits, max_diagnostics=desc["max_diagnostics"])
    return limits


def run_document_case(case: dict) -> list[str]:
    """Executes one document-face case and returns its ordered facts
    (runner.go runDocumentCase)."""
    profile = parse_document_profile(case)
    state = DocState(
        format_name=case.get("format", ""),
        profile_name=case.get("profile", ""),
        profile=profile,
        foreign_source=case.get("foreign_source", ""),
        foreign_source_hex=case.get("foreign_source_hex", ""),
        parse_limits=_parse_limits(case),
    )
    facts = Facts()
    try:
        parse_into_state(state, case.get("source", ""))
    except FatalFormation as failure:
        facts.set("parse.formation", "Fatal")
        facts.set("parse.fatal_code", failure.code)
        facts.set("parse.diagnostic_codes", "")
        facts.set("parse.root_kind", "")
        facts.set("parse.native", "")
        emit_step_facts(facts, state, None)
        return facts.lines
    facts.set("parse.formation", state.formation)
    facts.set("parse.fatal_code", "")
    facts.set("parse.diagnostic_codes", state.diagnostic_codes)
    facts.set("parse.root_kind", state.root_kind)
    facts.set("parse.native", state.native)
    for step in case.get("steps", []):
        op = step.get("op")
        if op == "parse":
            continue
        if op in ("query-native", "query-syntax", "project", "materialize", "edit"):
            emit_step_facts(facts, state, step)
        else:
            raise case_files.CaseFileError(f"case {case.get('id')}: unknown step op {op!r}")
    # Every group's key set is emitted exactly once: groups whose step is
    # absent from the case report Blocked here, in the fixed order.
    emit_step_facts(facts, state, None)
    return facts.lines


def parse_into_state(state: DocState, source: str) -> None:
    """Parses the case source and fills the parse facts; raises
    FatalFormation for a fatal failure (runner.go parseIntoState)."""
    raw = source.encode("utf-8")
    format_name = state.format_name
    if format_name == "json":
        try:
            document = json_parser.parse(raw, state.profile, state.parse_limits)
        except json_errors.JsonFormationFailure as failure:
            raise FatalFormation(failure.code) from None
        state.doc = document
        state.formation = document.formation_status().value
        state.diagnostic_codes = join(
            [diagnostic.code for diagnostic in document.diagnostic_records()]
        )
        availability = document.root().kind()
        if availability.is_available:
            state.root_kind = availability.value.value
        else:
            state.root_kind = "Unavailable:" + _semantic_unavailable_name(availability.reason)
        state.native = json_native(document.root(), 0)
        return
    if format_name == "toml":
        try:
            document = toml_parser.parse(raw, state.profile, state.parse_limits)
        except toml_errors.TomlFormationFailure as failure:
            raise FatalFormation(failure.code) from None
        state.toml_doc = document
        state.formation = document.formation_status().value
        state.diagnostic_codes = join([diagnostic.code for diagnostic in document.diagnostics()])
        state.root_kind = document.root().kind().value
        state.native = toml_native(document.root(), 0)
        return
    if format_name == "yaml":
        try:
            document = yaml_parser.parse(raw, state.profile, state.parse_limits)
        except yaml_errors.YamlFormationFailure as failure:
            raise FatalFormation(failure.code) from None
        state.yaml_doc = document
        state.formation = document.formation_status().value
        state.diagnostic_codes = join([diagnostic.code for diagnostic in document.diagnostics()])
        state.root_kind = yaml_root_kind(document)
        state.native = f"docs={document.document_count()} aliases={document.alias_count()}"
        return
    if format_name == "ini":
        ini_limits = ini_kinds.IniParseLimits(common=state.parse_limits)
        try:
            document = ini_parser.parse(
                raw,
                state.profile,
                ini_kinds.IniEncodingSelection.profile_default(),
                ini_limits,
            )
        except ini_errors.IniFormationFailure as failure:
            raise FatalFormation(failure.code) from None
        state.ini_doc = document
        state.formation = document.formation_status().value
        state.diagnostic_codes = join(
            [diagnostic.code for diagnostic in document.diagnostic_records()]
        )
        state.root_kind = "Document"
        state.native = f"sections={len(document.sections)} entries={len(document.entries)}"
        return
    if format_name == "properties":
        family_limits = properties_limits.PropertiesParseLimits(common=state.parse_limits)
        try:
            document = properties_parser.parse_reader(raw, SourceEncoding.utf8(), family_limits)
        except properties_errors.PropertiesFormationFailure as failure:
            raise FatalFormation(failure.code) from None
        state.properties_doc = document
        state.formation = document.formation_status().value
        state.diagnostic_codes = join(
            [diagnostic.code for diagnostic in document.diagnostic_records()]
        )
        state.root_kind = "Document"
        state.native = (
            f"properties={len(document.properties)} comments={len(document.comments)}"
        )
        return
    raise case_files.CaseFileError(f"unknown case format {format_name!r}")


def _semantic_unavailable_name(reason) -> str:
    return reason.value


def yaml_root_kind(document) -> str:
    """Renders the document-0 root node kind fact of a YAML stream."""
    yaml_doc = document.document(0)
    if yaml_doc is None:
        return "EmptyStream"
    return yaml_node_kind_name(yaml_doc.root().kind())


def yaml_node_kind_name(kind) -> str:
    return kind.value


# ---------------------------------------------------------------------------
# Native value summaries (mirrored by the Rust example)
# ---------------------------------------------------------------------------


def json_native(value, depth: int) -> str:
    """Renders one JSON native value in the canonical summary vocabulary
    (runner.go jsonNativeValue / emit_normalized_results.rs json_native)."""
    if depth > 64:
        return "..."
    availability = value.kind()
    if not availability.is_available:
        return "Unavailable:" + _semantic_unavailable_name(availability.reason)
    kind = availability.value
    if kind is json_kinds.JsonValueKind.NULL:
        return "null"
    if kind is json_kinds.JsonValueKind.BOOLEAN:
        boolean = value.as_boolean()
        if not boolean.is_available or boolean.value is None:
            return "?"
        return "true" if boolean.value else "false"
    if kind is json_kinds.JsonValueKind.INTEGER:
        integer = value.as_integer()
        if not integer.is_available or integer.value is None:
            return "?"
        return str(integer.value)
    if kind is json_kinds.JsonValueKind.DECIMAL:
        decimal = value.as_decimal()
        if not decimal.is_available or decimal.value is None:
            return "?"
        return f"{decimal.value.coefficient}e{decimal.value.exponent}"
    if kind is json_kinds.JsonValueKind.BINARY_FLOAT64:
        number = value.as_binary_float64()
        if not number.is_available or number.value is None:
            return "?"
        return f"0x{number.value:016x}"
    if kind is json_kinds.JsonValueKind.STRING:
        text = value.as_string()
        if not text.is_available or text.value is None:
            return "?"
        return '"' + escape(text.value) + '"'
    if kind is json_kinds.JsonValueKind.ARRAY:
        elements = value.array_elements()
        if not elements.is_available:
            return "Unavailable:" + _semantic_unavailable_name(elements.reason)
        if elements.value is None:
            return "?"
        parts = [json_native(element.value(), depth + 1) for element in elements.value]
        return "[" + ",".join(parts) + "]"
    if kind is json_kinds.JsonValueKind.OBJECT:
        members = value.object_members()
        if not members.is_available:
            return "Unavailable:" + _semantic_unavailable_name(members.reason)
        if members.value is None:
            return "?"
        parts = []
        for member in members.value:
            name = member.name()
            rendered_name = "?" if not name.is_available else escape(name.value)
            parts.append('"' + rendered_name + '":' + json_native(member.value(), depth + 1))
        return "{" + ",".join(parts) + "}"
    return "?"


def toml_native(item, depth: int) -> str:
    """Renders one TOML native item in the canonical summary vocabulary
    (runner.go tomlNativeItem)."""
    if depth > 64:
        return "..."
    kind = item.kind()
    if kind is toml_document.TomlItemKind.STRING:
        text = item.as_string()
        return '"' + escape(text) + '"' if text is not None else "?"
    if kind is toml_document.TomlItemKind.INTEGER:
        number = item.as_integer()
        return str(number) if number is not None else "?"
    if kind is toml_document.TomlItemKind.FLOAT:
        bits = item.as_float_bits()
        return f"0x{bits:016x}" if bits is not None else "?"
    if kind is toml_document.TomlItemKind.BOOLEAN:
        boolean = item.as_boolean()
        return "true" if boolean else "false" if boolean is not None else "?"
    if kind in (
        toml_document.TomlItemKind.OFFSET_DATE_TIME,
        toml_document.TomlItemKind.LOCAL_DATE_TIME,
        toml_document.TomlItemKind.LOCAL_DATE,
        toml_document.TomlItemKind.LOCAL_TIME,
    ):
        date_time = item.as_date_time()
        return toml_datetime_summary(date_time) if date_time is not None else "?"
    if kind in (
        toml_document.TomlItemKind.ARRAY,
        toml_document.TomlItemKind.ARRAY_OF_TABLES,
    ):
        elements = item.array_elements()
        if elements is None:
            return "?"
        parts = [toml_native(element.item(), depth + 1) for element in elements]
        return "[" + ",".join(parts) + "]"
    if kind in (
        toml_document.TomlItemKind.INLINE_TABLE,
        toml_document.TomlItemKind.ROOT_TABLE,
        toml_document.TomlItemKind.STANDARD_TABLE,
        toml_document.TomlItemKind.IMPLICIT_TABLE,
        toml_document.TomlItemKind.DOTTED_TABLE,
    ):
        entries = item.table_entries()
        if entries is None:
            return "?"
        parts = [
            '"' + escape(entry.name()) + '":' + toml_native(entry.item(), depth + 1)
            for entry in entries
        ]
        return "{" + ",".join(parts) + "}"
    return "?"


def toml_datetime_summary(date_time) -> str:
    """Renders one TOML date/time datum canonically
    (runner.go tomlDateTimeSummary)."""
    parts: list[str] = []
    if date_time.date is not None:
        date = date_time.date
        parts.append(f"date={date.year:04d}-{date.month:02d}-{date.day:02d}")
    if date_time.time is not None:
        time = date_time.time
        text = f"time={time.hour:02d}:{time.minute:02d}:{time.second:02d}"
        if time.nanosecond != 0:
            text += f".{time.nanosecond:09d}"
        parts.append(text)
    if date_time.offset_minutes is not None:
        minutes = date_time.offset_minutes
        if minutes == 0:
            parts.append("offset=Z")
        else:
            sign = "-" if minutes < 0 else "+"
            magnitude = -minutes if minutes < 0 else minutes
            parts.append(f"offset={sign}{magnitude // 60:02d}:{magnitude % 60:02d}")
    return "datetime(" + ",".join(parts) + ")"


# ---------------------------------------------------------------------------
# Query steps
# ---------------------------------------------------------------------------


def _ordered_results() -> CapabilitySet:
    capabilities = CapabilitySet()
    capabilities.insert(CapabilityId("core.query.ordered-results", 1))
    return capabilities


def _build_query_definition(step: dict, domain: protocol_query.QueryDomain):
    """Builds the executable from the declarative filters (runner.go
    buildQueryDefinition); raises QueryFailure."""
    format_name = "json"
    domain_id = step.get("domain", "")
    if domain_id.startswith("toml."):
        format_name = "toml"
    elif domain_id.startswith("yaml."):
        format_name = "yaml"
    elif domain_id.startswith("ini."):
        format_name = "ini"
    elif domain_id.startswith("java-properties."):
        format_name = "properties"

    def string_argument(argument: object, operator: str, name: str):
        if isinstance(argument, str):
            return protocol_query.OperatorCall(operator, 1).with_argument(
                name, PortableValue.string(argument)
            )
        return _verbatim_argument(argument, operator, name)

    def integer_argument(argument: object, operator: str, name: str):
        if isinstance(argument, int) and not isinstance(argument, bool):
            return protocol_query.OperatorCall(operator, 1).with_argument(
                name, PortableValue.integer(argument)
            )
        return _verbatim_argument(argument, operator, name)

    calls = []
    for filter_desc in step.get("filters", []):
        operator = filter_desc.get("operator", "")
        argument = filter_desc.get("argument")
        if operator in ("kind-is", "text-equals"):
            suffix = "syntax-kind-is" if operator == "kind-is" else "syntax-text-equals"
            calls.append(string_argument(argument, f"{format_name}.{suffix}", "kind" if operator == "kind-is" else "text"))
        elif operator == "take":
            calls.append(integer_argument(argument, "core.take", "count"))
        elif operator in ("json.member-name-equals", "toml.entry-name-equals"):
            calls.append(string_argument(argument, operator, "name"))
        elif operator in (
            "yaml.where-node-kind",
            "yaml.where-tag",
            "yaml.scalar-canonical-equals",
            "ini.entry-value-state-is",
            "properties.property-value-state-is",
        ):
            argument_name = {
                "yaml.where-node-kind": "kind",
                "yaml.where-tag": "tag",
                "yaml.scalar-canonical-equals": "canonical",
                "ini.entry-value-state-is": "state",
                "properties.property-value-state-is": "state",
            }[operator]
            calls.append(string_argument(argument, operator, argument_name))
        else:
            calls.append(protocol_query.OperatorCall(operator, 1))
    combine = step.get("combine", "Single")
    expression = protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT)
    if combine in ("Single", ""):
        for call in calls:
            expression = expression.then(call)
    elif combine == "StructureOrderMerge":
        expression = protocol_query.QueryExpression(
            protocol_query.ExpressionKind.STRUCTURE_ORDER_MERGE,
            branches=[protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(call) for call in calls],
        )
    elif combine == "Concat":
        expression = protocol_query.QueryExpression(
            protocol_query.ExpressionKind.CONCAT,
            branches=[protocol_query.QueryExpression(protocol_query.ExpressionKind.INPUT).then(call) for call in calls],
        )
    else:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.INVALID_ARGUMENT,
            operator="vector",
            argument=combine,
        )
    selection = step.get("selection", "All")
    selection_map = {
        "All": protocol_query.QuerySelection.ALL,
        "": protocol_query.QuerySelection.ALL,
        "First": protocol_query.QuerySelection.FIRST,
        "Last": protocol_query.QuerySelection.LAST,
        "ZeroOrOne": protocol_query.QuerySelection.ZERO_OR_ONE,
        "RequireOne": protocol_query.QuerySelection.REQUIRE_ONE,
    }
    if selection not in selection_map:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.INVALID_ARGUMENT,
            operator="vector",
            argument=selection,
        )
    return (
        protocol_query.QueryDefinition(domain)
        .with_expression(expression)
        .with_selection(selection_map[selection])
        .validate()
        .bind(_ordered_results())
    )


def _verbatim_argument(argument: object, operator: str, name: str):
    """Binds a present but wrong-typed argument verbatim so the definition
    validation reports the wrong argument kind (runner.go argumentCall:
    missing argument is an invalid-argument failure, present-but-wrong is
    bound and reported by validation)."""
    if argument is None:
        raise protocol_query.QueryFailure(
            protocol_query.QueryFailureKind.INVALID_ARGUMENT,
            operator=operator,
            argument=name,
        )
    value = decode_json(
        json.dumps(argument, ensure_ascii=False).encode("utf-8"), ProtocolLimits()
    )
    return protocol_query.OperatorCall(operator, 1).with_argument(name, value)


def _apply_query_limits(step: dict, limits) -> None:
    desc = step.get("query_limits")
    if isinstance(desc, dict):
        if isinstance(desc.get("max_results"), int):
            limits.max_results = desc["max_results"]
        if isinstance(desc.get("max_steps"), int):
            limits.max_steps = desc["max_steps"]


def _query_limits(step: dict):
    limits = json_query.JsonQueryLimits()
    _apply_query_limits(step, limits)
    return limits


def emit_step_facts(facts: Facts, state: DocState, step: dict | None) -> None:
    """Dispatches one step (or the absence of a step) and emits exactly one
    group's key set in the fixed order (runner.go emitStepFacts)."""
    op = (step or {}).get("op", "")
    if op == "query-native":
        emit_native_query(facts, state, step)
    elif op == "query-syntax":
        emit_syntax_query(facts, state, step)
    elif op == "project":
        emit_project(facts, state, step)
    elif op == "materialize":
        emit_materialize(facts, state, step)
    elif op == "edit":
        emit_edit(facts, state, step)
    else:
        emit_native_query(facts, state, None)
        emit_syntax_query(facts, state, None)
        emit_project(facts, state, None)
        emit_materialize(facts, state, None)
        emit_edit(facts, state, None)


def _query_failed(facts: Facts, prefix: str, code: str) -> None:
    facts.set(f"{prefix}.status", "Failed")
    facts.set(f"{prefix}.failure", code)
    facts.set(f"{prefix}.count", "")
    facts.set(f"{prefix}.matches", "")


def _query_blocked(facts: Facts, prefix: str) -> None:
    facts.set(f"{prefix}.status", "Blocked")
    facts.set(f"{prefix}.failure", "")
    facts.set(f"{prefix}.count", "")
    facts.set(f"{prefix}.matches", "")


def emit_native_query(facts: Facts, state: DocState, step: dict | None) -> None:
    if state.query_native_run:
        return
    state.query_native_run = True
    if step is None or step.get("op") != "query-native" or not state.document_parsed():
        _query_blocked(facts, "query.native")
        return
    domain = _resolve_domain(step, _NATIVE_DOMAINS)
    if domain is None:
        _query_failed(facts, "query.native", "core.query.domain-mismatch@1")
        return
    try:
        executable = _build_query_definition(step, domain)
    except protocol_query.QueryFailure as failure:
        _query_failed(facts, "query.native", failure.code)
        return
    limits = _query_limits(step)
    token = json_query.JsonCancellationToken()
    try:
        if state.doc is not None:
            matches = json_query.execute_json_query(executable, state.doc, limits, token).matches
            items = [_json_native_match(match, state.doc) for match in matches]
        elif state.toml_doc is not None:
            matches = toml_query.execute_toml_query(
                executable, state.toml_doc, limits, token
            )
            items = [_toml_native_match(match) for match in matches]
        elif state.yaml_doc is not None:
            execution = yaml_query.execute_yaml_query(
                executable, state.yaml_doc, yaml_query.YamlQueryLimits(limits.max_steps, limits.max_results), yaml_query.YamlCancellationToken()
            )
            items = [_yaml_native_match(match) for match in execution.matches]
        elif state.ini_doc is not None:
            execution = ini_query.execute_ini_query(
                executable, state.ini_doc, ini_query.IniQueryLimits(limits.max_steps, limits.max_results), ini_query.IniCancellationToken()
            )
            items = [_ini_native_match(match) for match in execution.matches]
        elif state.properties_doc is not None:
            execution = properties_query.execute_properties_query(
                executable, state.properties_doc, properties_query.PropertiesQueryLimits(limits.max_steps, limits.max_results), properties_query.PropertiesCancellationToken()
            )
            items = [_properties_native_match(match) for match in execution.matches]
        else:
            _query_blocked(facts, "query.native")
            return
    except protocol_query.QueryFailure as failure:
        _query_failed(facts, "query.native", failure.code)
        return
    facts.set("query.native.status", "Completed")
    facts.set("query.native.failure", "")
    facts.set("query.native.count", str(len(items)))
    facts.set("query.native.matches", join(items))


def emit_syntax_query(facts: Facts, state: DocState, step: dict | None) -> None:
    if state.query_syntax_run:
        return
    state.query_syntax_run = True
    if step is None or step.get("op") != "query-syntax" or not state.document_parsed():
        _query_blocked(facts, "query.syntax")
        return
    domain = _resolve_domain(step, _SYNTAX_DOMAINS)
    if domain is None:
        _query_failed(facts, "query.syntax", "core.query.domain-mismatch@1")
        return
    try:
        executable = _build_query_definition(step, domain)
    except protocol_query.QueryFailure as failure:
        _query_failed(facts, "query.syntax", failure.code)
        return
    limits = _query_limits(step)
    token = json_query.JsonCancellationToken()
    try:
        if state.doc is not None:
            execution = json_query.execute_json_syntax_query(executable, state.doc, limits, token)
            items = [f"{_syntax_kind_name(match.kind)}@{match.ordinal}" for match in execution.matches]
        elif state.toml_doc is not None:
            matches = toml_query.execute_toml_syntax_query(
                executable, state.toml_doc, limits, token
            )
            items = [f"{_syntax_kind_name(match.kind)}@{match.ordinal}" for match in matches]
        elif state.yaml_doc is not None:
            execution = yaml_query.execute_yaml_syntax_query(
                executable, state.yaml_doc, yaml_query.YamlQueryLimits(limits.max_steps, limits.max_results), yaml_query.YamlCancellationToken()
            )
            items = [f"{_syntax_kind_name(match.kind)}@{match.ordinal}" for match in execution.matches]
        elif state.ini_doc is not None:
            execution = ini_query.execute_ini_syntax_query(
                executable, state.ini_doc, ini_query.IniQueryLimits(limits.max_steps, limits.max_results), ini_query.IniCancellationToken()
            )
            items = [f"{_syntax_kind_name(match.kind)}@{match.ordinal}" for match in execution.matches]
        elif state.properties_doc is not None:
            execution = properties_query.execute_properties_syntax_query(
                executable, state.properties_doc, properties_query.PropertiesQueryLimits(limits.max_steps, limits.max_results), properties_query.PropertiesCancellationToken()
            )
            items = [f"{_syntax_kind_name(match.kind)}@{match.ordinal}" for match in execution.matches]
        else:
            _query_blocked(facts, "query.syntax")
            return
    except protocol_query.QueryFailure as failure:
        _query_failed(facts, "query.syntax", failure.code)
        return
    facts.set("query.syntax.status", "Completed")
    facts.set("query.syntax.failure", "")
    facts.set("query.syntax.count", str(len(items)))
    facts.set("query.syntax.matches", join(items))


def _syntax_kind_name(kind) -> str:
    """One syntax match kind's stable wire name (the ``KIND@ordinal``
    vocabulary of runner.go emitSyntaxQuery)."""
    as_str = getattr(kind, "as_str", None)
    if as_str is not None:
        return as_str()
    return kind.value


def _resolve_domain(step: dict, table: dict):
    domain_id = step.get("domain", "")
    domain_version = step.get("domain_version", 1)
    factory = table.get((domain_id, domain_version))
    if factory is None:
        return None
    return factory()


def _json_native_match(match, document) -> str:
    """Renders one JSON native match identity fact (runner.go
    jsonNativeMatch): V:{kind} / M:{ordinal}:{escaped name} / E:{ordinal}."""
    if match.kind is json_query.JsonMatchKind.VALUE:
        kind = "?"
        value = json_document.JsonValue(document, match.node.index)
        availability = value.kind()
        if availability.is_available:
            kind = availability.value.value
        return "V:" + kind
    if match.kind is json_query.JsonMatchKind.OBJECT_MEMBER:
        name = "?" if match.name is None else escape(match.name)
        return f"M:{match.ordinal}:{name}"
    if match.kind is json_query.JsonMatchKind.ARRAY_ELEMENT:
        return f"E:{match.ordinal}"
    return "?"


def _toml_native_match(match) -> str:
    if match.kind is toml_query.TomlMatchKind.ITEM:
        kind_name = "?" if match.kind_name is None else match.kind_name.value
        return "I:" + kind_name
    if match.kind is toml_query.TomlMatchKind.ENTRY:
        return f"M:{match.ordinal}:{escape(match.name or '')}"
    if match.kind is toml_query.TomlMatchKind.ARRAY_ELEMENT:
        return f"E:{match.ordinal}"
    return "?"


def _yaml_native_match(match) -> str:
    if match.kind is yaml_query.YamlMatchKind.STREAM:
        return "Stream:0"
    if match.kind is yaml_query.YamlMatchKind.DOCUMENT:
        return f"Document:{match.ordinal or 0}"
    if match.kind is yaml_query.YamlMatchKind.NODE:
        return "Node:" + (match.kind_name or "?")
    if match.kind is yaml_query.YamlMatchKind.MAPPING_ENTRY:
        return f"MappingEntry:{match.ordinal or 0}"
    if match.kind is yaml_query.YamlMatchKind.SEQUENCE_ELEMENT:
        return f"SequenceElement:{match.ordinal or 0}"
    if match.kind is yaml_query.YamlMatchKind.ANCHOR_DEFINITION:
        return "AnchorDefinition:" + escape(match.anchor or "")
    if match.kind is yaml_query.YamlMatchKind.ALIAS_OCCURRENCE:
        return f"AliasOccurrence:{match.ordinal or 0}"
    return "?"


def _ini_native_match(match) -> str:
    return f"{match.kind.value}:{match.ordinal or 0}"


def _properties_native_match(match) -> str:
    return f"{match.kind.value}:{match.ordinal or 0}"


# ---------------------------------------------------------------------------
# Projection / materialization / edit steps
# ---------------------------------------------------------------------------


def _project_blocked(facts: Facts) -> None:
    facts.set("project.status", "Blocked")
    facts.set("project.failure", "")
    facts.set("project.fidelity", "")
    facts.set("project.value_kind", "")
    facts.set("project.report", "")
    facts.set("project.provenance_entries", "")


def _project_failed(facts: Facts, code: str, report: str = "") -> None:
    facts.set("project.status", "Failed")
    facts.set("project.failure", code)
    facts.set("project.fidelity", "")
    facts.set("project.value_kind", "")
    facts.set("project.report", report)
    facts.set("project.provenance_entries", "")


def _project_completed(facts: Facts, fidelity: str, value: PortableValue, report: str, provenance_entries: int) -> None:
    facts.set("project.status", "Completed")
    facts.set("project.failure", "")
    facts.set("project.fidelity", fidelity)
    facts.set("project.value_kind", value.kind.value)
    facts.set("project.report", report)
    facts.set("project.provenance_entries", str(provenance_entries))


def emit_project(facts: Facts, state: DocState, step: dict | None) -> None:
    if state.project_run:
        return
    state.project_run = True
    if step is None or step.get("op") != "project" or not state.document_parsed():
        _project_blocked(facts)
        return
    if state.doc is not None:
        try:
            request = _build_json_projection_request(step)
        except json_errors.JsonProjectionFailure as failure:
            _project_failed(facts, failure.code)
            return
        result = json_projection.project(state.doc, request)
        if isinstance(result, json_projection.FailedProjectionAttempt):
            code = result.diagnostics[0].code if result.diagnostics else ""
            _project_failed(facts, code, json_event_summary(result.report))
            return
        state.value = result.value
        state.projected = True
        _project_completed(
            facts,
            result.fidelity.value,
            result.value,
            json_event_summary(result.report),
            len(result.provenance.entries),
        )
        return
    if state.toml_doc is not None:
        request = toml_projection.ProjectionRequest.new(
            toml_projection.ProjectionTarget.BEST_EXACT_CORE_V1
        )
        result = toml_projection.project_document(state.toml_doc, request)
        if isinstance(result, toml_projection.FailedProjectionAttempt):
            code = result.diagnostics[0].code if result.diagnostics else ""
            _project_failed(facts, code, toml_report_summary(result.report))
            return
        state.value = result.value
        state.projected = True
        _project_completed(
            facts,
            result.fidelity.value,
            result.value,
            toml_report_summary(result.report),
            len(result.provenance.entries),
        )
        return
    if state.yaml_doc is not None:
        result = yaml_projection.project_value(
            state.yaml_doc, yaml_projection.ValueProjectionRequest.best_exact_v1()
        )
        if isinstance(result, yaml_projection.FailedValueProjection):
            _project_failed(facts, result.code)
            return
        state.value = result.value
        state.projected = True
        _project_completed(
            facts,
            result.fidelity.value,
            result.value,
            yaml_event_summary(result.report),
            len(result.provenance.entries),
        )
        return
    if state.ini_doc is not None:
        request = ini_projection.ProjectionRequest.best_exact_entry_mapping()
        result = ini_projection.project(state.ini_doc, request)
        if isinstance(result, ini_projection.FailedProjectionAttempt):
            code = result.diagnostics[0].code if result.diagnostics else ""
            _project_failed(facts, code, ini_event_summary(result.report))
            return
        state.value = result.value
        state.projected = True
        _project_completed(
            facts,
            result.fidelity.value,
            result.value,
            ini_event_summary(result.report),
            len(result.provenance.entries),
        )
        return
    if state.properties_doc is not None:
        request = properties_projection.ProjectionRequest.best_exact_entry_mapping()
        result = properties_projection.project(state.properties_doc, request)
        if isinstance(result, properties_projection.FailedProjectionAttempt):
            code = result.diagnostics[0].code if result.diagnostics else ""
            _project_failed(facts, code, properties_event_summary(result.report))
            return
        state.value = result.value
        state.projected = True
        _project_completed(
            facts,
            result.fidelity.value,
            result.value,
            properties_event_summary(result.report),
            len(result.provenance.entries),
        )
        return
    _project_blocked(facts)


def _build_json_projection_request(step: dict):
    target_map = {
        "ProjectAsObject": json_projection.ProjectionTarget.PROJECT_AS_OBJECT_V1,
        "ProjectAsEntryMapping": json_projection.ProjectionTarget.PROJECT_AS_ENTRY_MAPPING_V1,
        "Json5BestExactCore": json_projection.ProjectionTarget.JSON5_BEST_EXACT_CORE_V1,
    }
    target = target_map.get(step.get("target", ""), json_projection.ProjectionTarget.BEST_EXACT_CORE_V1)
    builder = json_projection.ProjectionRequestBuilder(target)
    policy_map = {
        "FirstWins": json_projection.DuplicateKeyPolicy.FIRST_WINS,
        "LastWins": json_projection.DuplicateKeyPolicy.LAST_WINS,
    }
    if step.get("duplicate_policy") in policy_map:
        builder = builder.global_duplicate_policy(policy_map[step["duplicate_policy"]])
    return builder.build()


def json_event_summary(report) -> str:
    """Renders the JSON projection report as ordered EventKind:count pairs
    (runner.go jsonEventSummary)."""
    order: list[str] = []
    counts: dict[str, int] = {}
    for event in report.events:
        name = event.kind.value
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1
    return join([f"{name}:{counts[name]}" for name in order])


def toml_report_summary(report) -> str:
    """Renders the TOML projection report as ordered diagnostic codes."""
    return join([diagnostic.code for diagnostic in report.events])


def yaml_event_summary(report) -> str:
    order: list[str] = []
    counts: dict[str, int] = {}
    for event in report.events:
        name = event.kind.value
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1
    return join([f"{name}:{counts[name]}" for name in order])


def ini_event_summary(report) -> str:
    order: list[str] = []
    counts: dict[str, int] = {}
    for event in report.events:
        name = event.kind.value
        if name not in counts:
            order.append(name)
        counts[name] = counts.get(name, 0) + 1
    return join([f"{name}:{counts[name]}" for name in order])


def properties_event_summary(report) -> str:
    order: list[str] = []
    counts: dict[str, int] = {}
    for event in report.events:
        if event.code not in counts:
            order.append(event.code)
        counts[event.code] = counts.get(event.code, 0) + 1
    return join([f"{name}:{counts[name]}" for name in order])


def _materialize_blocked(facts: Facts) -> None:
    facts.set("materialize.status", "Blocked")
    facts.set("materialize.failure", "")
    facts.set("materialize.output", "")
    facts.set("materialize.fidelity", "")


def _materialize_failed(facts: Facts, code: str) -> None:
    facts.set("materialize.status", "Failed")
    facts.set("materialize.failure", code)
    facts.set("materialize.output", "")
    facts.set("materialize.fidelity", "")


def emit_materialize(facts: Facts, state: DocState, step: dict | None) -> None:
    if state.materialize_run:
        return
    state.materialize_run = True
    if step is None or step.get("op") != "materialize" or not state.document_parsed():
        _materialize_blocked(facts)
        return
    input_kind = step.get("input", "project")
    if input_kind in ("", "project"):
        if not state.projected:
            _materialize_blocked(facts)
            return
        value = state.value
    elif input_kind == "value":
        value = decode_materialize_value(step)
        if value is None:
            _materialize_failed(facts, "core.protocol.invalid-value@1")
            return
    else:
        _materialize_failed(facts, "core.protocol.invalid-value@1")
        return
    request = _build_materialization_request(step)
    if request is None:
        _materialize_failed(facts, "core.materialization.invalid-request@1")
        return
    if state.doc is not None:
        result = json_materialization.materialize(value, request)
    elif state.toml_doc is not None:
        result = toml_materialization.materialize(value, request)
    elif state.yaml_doc is not None:
        result = yaml_materialization.materialize_value(value, request)
    elif state.ini_doc is not None:
        result = ini_materialization.materialize(value, request)
    elif state.properties_doc is not None:
        result = properties_materialization.materialize(value, request)
    else:
        _materialize_blocked(facts)
        return
    if isinstance(result, CompleteMaterialization):
        facts.set("materialize.status", "Completed")
        facts.set("materialize.failure", "")
        facts.set("materialize.output", escape_bytes(result.document.render()))
        facts.set("materialize.fidelity", result.fidelity.value)
        return
    failure = result.failure
    code = failure.code if hasattr(failure, "code") else str(failure)
    _materialize_failed(facts, code)


def decode_materialize_value(step: dict) -> PortableValue | None:
    """Decodes the materialize input descriptor through the canonical
    transport JSON decoder (runner.go decodeMaterializeValue)."""
    entry_mapping = step.get("entry_mapping")
    if isinstance(entry_mapping, dict):
        try:
            key = decode_json(entry_mapping["key_json"].encode("utf-8"), ProtocolLimits())
            value = decode_json(entry_mapping["value_json"].encode("utf-8"), ProtocolLimits())
        except Exception:
            return None
        builder = EntryMappingBuilder()
        try:
            builder.push(key, value)
        except Exception:
            return None
        return builder.build()
    value_json = step.get("value_json")
    if not isinstance(value_json, str):
        return None
    try:
        return decode_json(value_json.encode("utf-8"), ProtocolLimits())
    except Exception:
        return None


def _build_materialization_request(step: dict) -> MaterializationRequest | None:
    """Builds the request from the descriptor (runner.go
    buildMaterializationRequest); None means an invalid request."""
    target_profile = step.get("target_profile", "")
    style = step.get("style", "")
    if not target_profile or not style:
        return None
    request = MaterializationRequest.new(
        ProfileId.new(target_profile.split("@", 1)[0], 1),
        MaterializationStyleId.new(style.split("@", 1)[0], 1),
    )
    newline_map = {
        "None": NewlinePolicy.NONE,
        "CrLf": NewlinePolicy.CRLF,
    }
    request = request.with_newline(newline_map.get(step.get("newline", ""), NewlinePolicy.LF))
    mat_limits = step.get("limits")
    if isinstance(mat_limits, dict):
        limits = MaterializationLimits()
        if isinstance(mat_limits.get("max_output_bytes"), int):
            limits = dataclasses.replace(limits, max_output_bytes=mat_limits["max_output_bytes"])
        if isinstance(mat_limits.get("max_input_nodes"), int):
            limits = dataclasses.replace(limits, max_input_nodes=mat_limits["max_input_nodes"])
        if isinstance(mat_limits.get("max_depth"), int):
            limits = dataclasses.replace(limits, max_depth=mat_limits["max_depth"])
        if isinstance(mat_limits.get("max_provenance_entries"), int):
            limits = dataclasses.replace(
                limits, max_provenance_entries=mat_limits["max_provenance_entries"]
            )
        request = request.with_limits(limits)
    return request


# ---------------------------------------------------------------------------
# Edit steps
# ---------------------------------------------------------------------------


def _edit_blocked(facts: Facts) -> None:
    facts.set("edit.status", "Blocked")
    facts.set("edit.failure", "")
    facts.set("edit.output", "")
    facts.set("edit.source_edit_count", "")


def _edit_failed(facts: Facts, code: str) -> None:
    facts.set("edit.status", "Failed")
    facts.set("edit.failure", code)
    facts.set("edit.output", "")
    facts.set("edit.source_edit_count", "")


def emit_edit(facts: Facts, state: DocState, step: dict | None) -> None:
    if state.edit_run:
        return
    state.edit_run = True
    if step is None or step.get("op") != "edit" or not state.document_parsed():
        _edit_blocked(facts)
        return
    if state.doc is not None:
        if not ensure_foreign(state):
            _edit_failed(facts, "core.source.invalid-sequence@1")
            return
        builder = json_edit.EditTransactionBuilder(state.doc)
        if not apply_json_edit_operations(builder, state, step):
            _edit_failed(facts, "core.edit.target-not-found@1")
            return
        try:
            commit = json_edit.commit(state.doc, builder.build())
        except json_errors.JsonEditFailure as failure:
            _edit_failed(facts, failure.code)
            return
        facts.set("edit.status", "Completed")
        facts.set("edit.failure", "")
        facts.set("edit.output", escape_bytes(commit.document.render()))
        facts.set("edit.source_edit_count", str(len(commit.change_set.source_edits)))
        return
    if state.toml_doc is not None:
        builder = toml_edits.EditTransactionBuilder(state.toml_doc)
        if not apply_toml_edit_operations(builder, state, step):
            _edit_failed(facts, "core.edit.target-not-found@1")
            return
        try:
            commit = toml_edits.commit_document(state.toml_doc, builder.build())
        except toml_errors.TomlEditFailure as failure:
            _edit_failed(facts, failure.code)
            return
        facts.set("edit.status", "Completed")
        facts.set("edit.failure", "")
        facts.set("edit.output", escape_bytes(commit.document.render()))
        facts.set("edit.source_edit_count", str(len(commit.change_set.source_edits)))
        return
    if state.yaml_doc is not None:
        builder = yaml_edit.EditTransactionBuilder(state.yaml_doc)
        if not apply_yaml_edit_operations(builder, state, step):
            _edit_failed(facts, "core.edit.target-not-found@1")
            return
        try:
            commit = yaml_edit.commit(state.yaml_doc, builder.build())
        except yaml_errors.YamlEditFailure as failure:
            _edit_failed(facts, failure.code)
            return
        facts.set("edit.status", "Completed")
        facts.set("edit.failure", "")
        facts.set("edit.output", escape_bytes(commit.document.render()))
        facts.set("edit.source_edit_count", str(len(commit.change_set.source_edits)))
        return
    if state.ini_doc is not None:
        builder = ini_edit.EditTransactionBuilder(state.ini_doc)
        if not apply_ini_edit_operations(builder, state, step):
            _edit_failed(facts, "core.edit.target-not-found@1")
            return
        try:
            commit = ini_edit.commit(state.ini_doc, builder.build())
        except ini_errors.IniEditFailure as failure:
            _edit_failed(facts, failure.code)
            return
        facts.set("edit.status", "Completed")
        facts.set("edit.failure", "")
        facts.set("edit.output", escape_bytes(commit.document.render()))
        facts.set("edit.source_edit_count", str(len(commit.change_set.source_edits)))
        return
    if state.properties_doc is not None:
        builder = properties_edit.EditTransactionBuilder(state.properties_doc)
        if not apply_properties_edit_operations(builder, state, step):
            _edit_failed(facts, "core.edit.target-not-found@1")
            return
        try:
            commit = properties_edit.commit(state.properties_doc, builder.build())
        except properties_errors.PropertiesEditFailure as failure:
            _edit_failed(facts, failure.code)
            return
        facts.set("edit.status", "Completed")
        facts.set("edit.failure", "")
        facts.set("edit.output", escape_bytes(commit.document.render()))
        facts.set("edit.source_edit_count", str(len(commit.change_set.source_edits)))
        return
    _edit_blocked(facts)


def ensure_foreign(state: DocState) -> bool:
    """Parses the foreign source when the case declares one (the
    wrong-snapshot edit cases; runner.go ensureForeign). A declared source
    that fails to decode or parse reports edit.failure =
    core.source.invalid-sequence@1."""
    if (
        state.foreign is not None
        or state.foreign_toml is not None
        or state.foreign_yaml is not None
        or state.foreign_ini is not None
        or state.foreign_properties is not None
        or (state.foreign_source == "" and state.foreign_source_hex == "")
    ):
        return True
    foreign_bytes = state.foreign_source.encode("utf-8")
    if state.foreign_source_hex:
        try:
            foreign_bytes = bytes.fromhex(state.foreign_source_hex)
        except ValueError:
            return False
    format_name = state.format_name
    try:
        if format_name == "json":
            document = json_parser.parse(foreign_bytes, state.profile, state.parse_limits)
            state.foreign = document
        elif format_name == "toml":
            document = toml_parser.parse(foreign_bytes, state.profile, state.parse_limits)
            state.foreign_toml = document
        elif format_name == "yaml":
            document = yaml_parser.parse(foreign_bytes, state.profile, state.parse_limits)
            state.foreign_yaml = document
        elif format_name == "ini":
            document = ini_parser.parse(
                foreign_bytes,
                state.profile,
                ini_kinds.IniEncodingSelection.profile_default(),
                ini_kinds.IniParseLimits(common=state.parse_limits),
            )
            state.foreign_ini = document
        elif format_name == "properties":
            document = properties_parser.parse_reader(
                foreign_bytes,
                SourceEncoding.utf8(),
                properties_limits.PropertiesParseLimits(common=state.parse_limits),
            )
            state.foreign_properties = document
        else:
            return False
    except Exception:
        return False
    return True


def _edit_value(value_desc: dict) -> tuple[PortableValue | None, bool]:
    """Builds one core value from a scalar descriptor (runner.go
    valueDesc.coreValue)."""
    if value_desc is None:
        return None, False
    if "null" in value_desc and value_desc["null"] is not None:
        return PortableValue.null(), True
    if "boolean" in value_desc and value_desc["boolean"] is not None:
        return PortableValue.boolean(value_desc["boolean"]), True
    if value_desc.get("integer"):
        try:
            return PortableValue.integer(int(value_desc["integer"])), True
        except ValueError:
            return None, False
    if value_desc.get("decimal"):
        decimal = parse_decimal_number(value_desc["decimal"])
        if decimal is None:
            return None, False
        return PortableValue.decimal(decimal), True
    if value_desc.get("string"):
        return PortableValue.string(value_desc["string"]), True
    if value_desc.get("binary64"):
        try:
            bits = int(value_desc["binary64"].removeprefix("0x"), 16)
        except ValueError:
            return None, False
        return PortableValue.binary_float64(bits), True
    return None, False


def _edit_string(value_desc: dict) -> tuple[str | None, bool]:
    if value_desc is None or not value_desc.get("string"):
        return None, False
    return value_desc["string"], True


def parse_decimal_number(source: str) -> Decimal | None:
    """Parses one JSON-number spelling ("1.00", "10e-1") into its canonical
    coefficient x 10^exponent decimal (runner.go parseDecimalNumber)."""
    coefficient_text = source
    exponent = 0
    index = source.find("e")
    if index < 0:
        index = source.find("E")
    if index >= 0:
        exponent_text = source[index + 1 :]
        coefficient_text = source[:index]
        try:
            exponent = int(exponent_text)
        except ValueError:
            return None
    scale = 0
    index = coefficient_text.find(".")
    if index >= 0:
        fraction = coefficient_text[index + 1 :]
        coefficient_text = coefficient_text[:index] + fraction
        scale = -len(fraction)
    if coefficient_text in ("", "-", "+"):
        return None
    try:
        coefficient = int(coefficient_text)
    except ValueError:
        return None
    return Decimal(coefficient, exponent + scale)


def _policy(policy_name: str, enum_type) -> object | None:
    for member in enum_type:
        if member.value == policy_name:
            return member
    return None


def apply_json_edit_operations(builder, state: DocState, step: dict) -> bool:
    for op in step.get("operations", []):
        operation = op.get("operation")
        if operation == "semantic-scalar":
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            target, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            policy = _policy(op.get("policy", ""), json_edit.RepresentationPolicy)
            if policy is None:
                return False
            builder.semantic_scalar(target, value, policy)
        elif operation == "literal-scalar":
            target, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            try:
                literal = bytes.fromhex(op.get("literal_hex", ""))
            except ValueError:
                return False
            builder.literal_scalar(target, literal)
        elif operation == "insert-member":
            container, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_json_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_member(container, op.get("name", ""), value, placement)
        elif operation == "remove-member":
            target, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_member(target)
        elif operation == "rename-member":
            target, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_member(target, op.get("name", ""))
        elif operation == "insert-array-element":
            container, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_json_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_array_element(container, value, placement)
        elif operation == "remove-array-element":
            target, ok = resolve_json_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_array_element(target)
        else:
            return False
    return True


# The TOML family's policy enum uses lowercase wire spellings
# ("preserve-compatible") while the case file uses the shared vocabulary
# ("PreserveCompatible"); map explicitly (the Go tomlRepresentationPolicy
# mapping, runner.go:2750-2763).
_TOML_POLICIES = {
    "PreserveCompatible": toml_edits.RepresentationPolicy.PRESERVE_COMPATIBLE,
    "CanonicalForProfile": toml_edits.RepresentationPolicy.CANONICAL_FOR_PROFILE,
    "PreserveElseCanonical": toml_edits.RepresentationPolicy.PRESERVE_ELSE_CANONICAL,
    "ExactLiteral": toml_edits.RepresentationPolicy.EXACT_LITERAL,
}


def apply_toml_edit_operations(builder, state: DocState, step: dict) -> bool:
    for op in step.get("operations", []):
        operation = op.get("operation")
        if operation == "semantic-scalar":
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            target, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            policy = _TOML_POLICIES.get(op.get("policy", ""))
            if policy is None:
                return False
            builder.semantic_scalar(target, value, policy)
        elif operation == "literal-scalar":
            target, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            try:
                literal = bytes.fromhex(op.get("literal_hex", ""))
            except ValueError:
                return False
            builder.literal_scalar(target, literal)
        elif operation == "insert-entry":
            container, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_toml_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_entry(container, op.get("name", ""), value, placement)
        elif operation == "remove-entry":
            target, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_entry(target)
        elif operation == "rename-entry":
            target, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_entry(target, op.get("name", ""))
        elif operation == "insert-array-element":
            container, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_toml_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_array_element(container, value, placement)
        elif operation == "remove-array-element":
            target, ok = resolve_toml_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_array_element(target)
        else:
            return False
    return True


def apply_yaml_edit_operations(builder, state: DocState, step: dict) -> bool:
    for op in step.get("operations", []):
        operation = op.get("operation")
        if operation == "semantic-scalar":
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            target, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            policy = _policy(op.get("policy", ""), yaml_edit.RepresentationPolicy)
            if policy is None:
                return False
            builder.semantic_scalar(target, value, policy)
        elif operation == "literal-scalar":
            target, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            try:
                literal = bytes.fromhex(op.get("literal_hex", ""))
            except ValueError:
                return False
            builder.literal_scalar(target, literal)
        elif operation == "rename-anchor":
            target, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_anchor(target, op.get("name", ""))
        elif operation == "insert-mapping-entry":
            container, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_yaml_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_mapping_entry(container, PortableValue.string(op.get("name", "")), value, placement)
        elif operation == "remove-mapping-entry":
            target, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_mapping_entry(target)
        elif operation == "insert-sequence-element":
            container, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_value(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_yaml_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_sequence_element(container, value, placement)
        elif operation == "remove-sequence-element":
            target, ok = resolve_yaml_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_sequence_element(target)
        else:
            return False
    return True


def apply_ini_edit_operations(builder, state: DocState, step: dict) -> bool:
    for op in step.get("operations", []):
        operation = op.get("operation")
        if operation == "semantic-value":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_string(op.get("value"))
            if not ok:
                return False
            policy = _policy(op.get("policy", ""), ini_edit.RepresentationPolicy)
            if policy is None:
                return False
            builder.semantic_value(target, value, policy)
        elif operation == "literal-value":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            try:
                literal = bytes.fromhex(op.get("literal_hex", ""))
            except ValueError:
                return False
            builder.literal_value(target, literal)
        elif operation == "insert-section":
            container, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            placement, ok = resolve_ini_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_section(container, op.get("name", ""), placement)
        elif operation == "remove-section":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_section(target)
        elif operation == "rename-section":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_section(target, op.get("name", ""))
        elif operation == "insert-entry":
            container, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_string(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_ini_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_entry(container, op.get("name", ""), value, placement)
        elif operation == "remove-entry":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_entry(target)
        elif operation == "rename-entry":
            target, ok = resolve_ini_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_entry(target, op.get("name", ""))
        else:
            return False
    return True


def apply_properties_edit_operations(builder, state: DocState, step: dict) -> bool:
    for op in step.get("operations", []):
        operation = op.get("operation")
        if operation == "semantic-value":
            target, ok = resolve_properties_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_string(op.get("value"))
            if not ok:
                return False
            builder.semantic_value(target, properties_edit.JavaString.from_unicode(value))
        elif operation == "literal-value":
            target, ok = resolve_properties_target(state, op.get("target"))
            if not ok:
                return False
            try:
                literal = bytes.fromhex(op.get("literal_hex", ""))
            except ValueError:
                return False
            builder.literal_value(target, literal)
        elif operation == "insert-property":
            container, ok = resolve_properties_target(state, op.get("target"))
            if not ok:
                return False
            value, ok = _edit_string(op.get("value"))
            if not ok:
                return False
            placement, ok = resolve_properties_placement(state, op.get("placement"))
            if not ok:
                return False
            builder.insert_property(
                container,
                properties_edit.JavaString.from_unicode(op.get("name", "")),
                properties_edit.JavaString.from_unicode(value),
                placement,
            )
        elif operation == "remove-property":
            target, ok = resolve_properties_target(state, op.get("target"))
            if not ok:
                return False
            builder.remove_property(target)
        elif operation == "rename-property":
            target, ok = resolve_properties_target(state, op.get("target"))
            if not ok:
                return False
            builder.rename_property(
                target, properties_edit.JavaString.from_unicode(op.get("name", ""))
            )
        else:
            return False
    return True


def resolve_json_target(state: DocState, target: dict | None) -> tuple[NodeRef | None, bool]:
    if target is None:
        return None, False
    doc = state.foreign if target.get("foreign") else state.doc
    if doc is None:
        return None, False
    root = doc.root()
    kind = target.get("kind")
    if kind == "root":
        return root.node_ref(), True
    if kind == "member":
        members = root.object_members()
        if not members.is_available or members.value is None or target.get("ordinal", 0) >= len(members.value):
            return None, False
        return members.value[target["ordinal"]].node_ref(), True
    if kind == "member-value":
        members = root.object_members()
        if not members.is_available or members.value is None or target.get("ordinal", 0) >= len(members.value):
            return None, False
        return members.value[target["ordinal"]].value_node_ref(), True
    if kind == "member-key":
        members = root.object_members()
        if not members.is_available or members.value is None or target.get("ordinal", 0) >= len(members.value):
            return None, False
        return members.value[target["ordinal"]].key_node_ref(), True
    if kind == "array-element":
        elements = root.array_elements()
        if not elements.is_available or elements.value is None or target.get("ordinal", 0) >= len(elements.value):
            return None, False
        return elements.value[target["ordinal"]].node_ref(), True
    if kind == "array-element-value":
        elements = root.array_elements()
        if not elements.is_available or elements.value is None or target.get("ordinal", 0) >= len(elements.value):
            return None, False
        return elements.value[target["ordinal"]].value_node_ref(), True
    return None, False


def resolve_toml_target(state: DocState, target: dict | None) -> tuple[NodeRef | None, bool]:
    if target is None:
        return None, False
    doc = state.foreign_toml if target.get("foreign") else state.toml_doc
    if doc is None:
        return None, False
    root = doc.root()
    kind = target.get("kind")
    if kind == "root":
        return root.node_ref(), True
    if kind == "entry":
        entries = root.table_entries()
        if entries is None or target.get("ordinal", 0) >= len(entries):
            return None, False
        return entries[target["ordinal"]].node_ref(), True
    if kind == "entry-item":
        entries = root.table_entries()
        if entries is None or target.get("ordinal", 0) >= len(entries):
            return None, False
        return entries[target["ordinal"]].item_node_ref(), True
    if kind == "entry-key":
        entries = root.table_entries()
        if entries is None or target.get("ordinal", 0) >= len(entries):
            return None, False
        return entries[target["ordinal"]].key_node_ref(), True
    if kind == "array-element":
        elements = root.array_elements()
        if elements is None or target.get("ordinal", 0) >= len(elements):
            return None, False
        return elements[target["ordinal"]].node_ref(), True
    if kind == "array-element-item":
        elements = root.array_elements()
        if elements is None or target.get("ordinal", 0) >= len(elements):
            return None, False
        return elements[target["ordinal"]].item_node_ref(), True
    return None, False


def resolve_yaml_target(state: DocState, target: dict | None) -> tuple[NodeRef | None, bool]:
    if target is None:
        return None, False
    doc = state.foreign_yaml if target.get("foreign") else state.yaml_doc
    if doc is None:
        return None, False
    yaml_doc = doc.document(0)
    if yaml_doc is None:
        return None, False
    root = yaml_doc.root()
    kind = target.get("kind")
    if kind == "document-root":
        return root.node_ref(), True
    if kind == "mapping-entry":
        entry = root.mapping_entry(target.get("ordinal", 0))
        return (entry.node_ref(), True) if entry is not None else (None, False)
    if kind == "mapping-value":
        entry = root.mapping_entry(target.get("ordinal", 0))
        return (entry.value().node_ref(), True) if entry is not None else (None, False)
    if kind == "mapping-key":
        entry = root.mapping_entry(target.get("ordinal", 0))
        return (entry.key().node_ref(), True) if entry is not None else (None, False)
    if kind in ("sequence-element", "sequence-element-node"):
        item = root.sequence_item(target.get("ordinal", 0))
        if item is not None:
            node = item.node_ref() if kind == "sequence-element" else item.node().node_ref()
            return node, True
        entry = root.mapping_entry(0)
        if entry is not None:
            item = entry.value().sequence_item(target.get("ordinal", 0))
            if item is not None:
                node = item.node_ref() if kind == "sequence-element" else item.node().node_ref()
                return node, True
        return None, False
    if kind == "anchor-value":
        entry = root.mapping_entry(target.get("ordinal", 0))
        if entry is None:
            return None, False
        anchor = entry.value().anchor_node_ref()
        return (anchor, True) if anchor is not None else (None, False)
    return None, False


def resolve_ini_target(state: DocState, target: dict | None) -> tuple[NodeRef | None, bool]:
    if target is None:
        return None, False
    doc = state.foreign_ini if target.get("foreign") else state.ini_doc
    if doc is None:
        return None, False
    kind = target.get("kind")
    if kind == "document":
        return doc.node_ref(), True
    if kind == "section":
        sections = doc.sections
        if target.get("ordinal", 0) >= len(sections):
            return None, False
        return sections[target["ordinal"]].node, True
    if kind == "entry":
        entries = doc.entries
        if target.get("ordinal", 0) >= len(entries):
            return None, False
        return entries[target["ordinal"]].node, True
    return None, False


def resolve_properties_target(state: DocState, target: dict | None) -> tuple[NodeRef | None, bool]:
    if target is None:
        return None, False
    doc = state.foreign_properties if target.get("foreign") else state.properties_doc
    if doc is None:
        return None, False
    kind = target.get("kind")
    if kind == "document":
        return doc.node_ref(), True
    if kind == "property":
        properties = doc.properties
        if target.get("ordinal", 0) >= len(properties):
            return None, False
        return properties[target["ordinal"]].node, True
    return None, False


def resolve_json_placement(state: DocState, placement: dict | None) -> tuple[AssociationPlacement | None, bool]:
    if placement is None:
        return AssociationPlacement(kind="End"), True
    at = placement.get("at")
    if at == "start":
        return AssociationPlacement(kind="Start"), True
    if at == "end":
        return AssociationPlacement(kind="End"), True
    doc = state.doc
    root = doc.root()
    if placement.get("before_ordinal") is not None:
        anchor = _json_ordinal_anchor(root, placement["before_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="Before", anchor=anchor), True
    if placement.get("after_ordinal") is not None:
        anchor = _json_ordinal_anchor(root, placement["after_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="After", anchor=anchor), True
    return AssociationPlacement(kind="End"), True


def _json_ordinal_anchor(root, ordinal: int) -> NodeRef | None:
    members = root.object_members()
    if members.is_available and members.value is not None and ordinal < len(members.value):
        return members.value[ordinal].node_ref()
    elements = root.array_elements()
    if elements.is_available and elements.value is not None and ordinal < len(elements.value):
        return elements.value[ordinal].node_ref()
    return None


def resolve_toml_placement(state: DocState, placement: dict | None) -> tuple[AssociationPlacement | None, bool]:
    if placement is None:
        return AssociationPlacement(kind="End"), True
    at = placement.get("at")
    if at == "start":
        return AssociationPlacement(kind="Start"), True
    if at == "end":
        return AssociationPlacement(kind="End"), True
    root = state.toml_doc.root()
    if placement.get("before_ordinal") is not None:
        anchor = _toml_ordinal_anchor(root, placement["before_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="Before", anchor=anchor), True
    if placement.get("after_ordinal") is not None:
        anchor = _toml_ordinal_anchor(root, placement["after_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="After", anchor=anchor), True
    return AssociationPlacement(kind="End"), True


def _toml_ordinal_anchor(root, ordinal: int) -> NodeRef | None:
    entries = root.table_entries()
    if entries is not None and ordinal < len(entries):
        return entries[ordinal].node_ref()
    elements = root.array_elements()
    if elements is not None and ordinal < len(elements):
        return elements[ordinal].node_ref()
    return None


def resolve_yaml_placement(state: DocState, placement: dict | None) -> tuple[AssociationPlacement | None, bool]:
    if placement is None:
        return AssociationPlacement(kind="End"), True
    at = placement.get("at")
    if at == "start":
        return AssociationPlacement(kind="Start"), True
    if at == "end":
        return AssociationPlacement(kind="End"), True
    if placement.get("before_ordinal") is not None:
        anchor = _yaml_ordinal_anchor(state, placement["before_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="Before", anchor=anchor), True
    if placement.get("after_ordinal") is not None:
        anchor = _yaml_ordinal_anchor(state, placement["after_ordinal"])
        if anchor is None:
            return None, False
        return AssociationPlacement(kind="After", anchor=anchor), True
    return AssociationPlacement(kind="End"), True


def _yaml_ordinal_anchor(state: DocState, ordinal: int) -> NodeRef | None:
    yaml_doc = state.yaml_doc.document(0)
    if yaml_doc is None:
        return None
    root = yaml_doc.root()
    entry = root.mapping_entry(ordinal)
    if entry is not None:
        return entry.node_ref()
    item = root.sequence_item(ordinal)
    if item is not None:
        return item.node_ref()
    return None


def resolve_ini_placement(state: DocState, placement: dict | None) -> tuple[AssociationPlacement | None, bool]:
    if placement is None:
        return AssociationPlacement(kind="End"), True
    at = placement.get("at")
    if at == "start":
        return AssociationPlacement(kind="Start"), True
    if at == "end":
        return AssociationPlacement(kind="End"), True
    return AssociationPlacement(kind="End"), True


def resolve_properties_placement(state: DocState, placement: dict | None) -> tuple[AssociationPlacement | None, bool]:
    if placement is None:
        return AssociationPlacement(kind="End"), True
    at = placement.get("at")
    if at == "start":
        return AssociationPlacement(kind="Start"), True
    if at == "end":
        return AssociationPlacement(kind="End"), True
    return AssociationPlacement(kind="End"), True


# ---------------------------------------------------------------------------
# Source face (runner.go source.go)
# ---------------------------------------------------------------------------


def run_source_case(case: dict) -> list[str]:
    """Executes one source-face case and returns its ordered facts."""
    facts = Facts()
    input_desc = case.get("input", {})
    raw = _source_raw_bytes(input_desc)
    if raw is None:
        raise case_files.CaseFileError(f"case {case.get('id')}: source case without input")
    request = _build_encoding_request(case.get("request", {}))
    if request is None:
        raise case_files.CaseFileError(f"case {case.get('id')}: unknown request")
    limits = SourceLimits()
    try:
        snapshot = SourceSnapshot.from_raw(raw, request, limits)
    except SourceError as error:
        facts.set("source.status", "Failed")
        facts.set("source.failure", _source_code(error))
        facts.set("source.encoding", "")
        facts.set("source.bom", "")
        facts.set("source.declared", "")
        facts.set("source.digest", "")
        facts.set("source.len", "")
        facts.set("source.text", "")
        _emit_position_facts(facts, case.get("positions", []), raw, None)
        _emit_patch_facts(facts, case, raw, None, request)
        return facts.lines
    facts.set("source.status", "Ok")
    facts.set("source.failure", "")
    facts.set("source.encoding", snapshot.encoding_facts().selected.as_str)
    facts.set("source.bom", _bom_name(snapshot.encoding_facts().bom))
    facts.set("source.declared", _encoding_name(snapshot.encoding_facts().declaration))
    facts.set("source.digest", snapshot.digest().hex)
    facts.set("source.len", str(snapshot.len()))
    text = snapshot.decoded_text()
    facts.set("source.text", escape(text) if text is not None else "binary")
    _emit_position_facts(facts, case.get("positions", []), raw, snapshot)
    _emit_patch_facts(facts, case, raw, snapshot, request)
    return facts.lines


def _source_raw_bytes(input_desc: dict) -> bytes | None:
    if input_desc is None:
        return None
    raw_hex = input_desc.get("raw_hex", "")
    if raw_hex:
        try:
            return bytes.fromhex(raw_hex)
        except ValueError as error:
            raise case_files.CaseFileError(f"invalid raw_hex: {error}") from error
    return input_desc.get("source", "").encode("utf-8")


def _build_encoding_request(desc: dict) -> EncodingRequest | None:
    if not isinstance(desc, dict):
        return None
    default_encoding, ok = _encoding_by_name(desc.get("profile_default", ""))
    if not ok:
        return None
    request = EncodingRequest.new(default_encoding)
    declaration_name = desc.get("declaration", "")
    if declaration_name:
        declaration, ok = _encoding_by_name(declaration_name)
        if not ok:
            return None
        request = request.with_declaration(declaration)
    override_name = desc.get("caller_override", "")
    if override_name:
        override, ok = _encoding_by_name(override_name)
        if not ok:
            return None
        request = request.with_caller_override(override)
    bom_policy = desc.get("bom_policy", "")
    if bom_policy == "TreatAsContent":
        request = request.with_bom_policy(BomPolicy.TREAT_AS_CONTENT)
    elif bom_policy not in ("", "DetectUnicode"):
        return None
    return request


def _encoding_by_name(name: str) -> tuple[SourceEncoding | None, bool]:
    factories = {
        "binary": SourceEncoding.binary,
        "utf-8": SourceEncoding.utf8,
        "utf-16le": SourceEncoding.utf16le,
        "utf-16be": SourceEncoding.utf16be,
        "latin-1": SourceEncoding.latin1,
    }
    if name in factories:
        return factories[name](), True
    if name == "windows-1252":
        page = WindowsCodePage.from_number(1252)
        if page is None:
            return None, False
        return SourceEncoding.windows_code_page(page), True
    return None, False


def _bom_name(bom) -> str:
    if bom is None:
        return ""
    return bom.encoding.as_str


def _encoding_name(encoding) -> str:
    if encoding is None:
        return ""
    return encoding.as_str


def _source_code(error: SourceError) -> str:
    return error.code


def _emit_position_facts(facts: Facts, positions: list, raw: bytes, snapshot) -> None:
    for index, raw_byte in enumerate(positions):
        key = f"source.position.{index}."
        if snapshot is None:
            facts.set(key + "raw_byte", str(raw_byte))
            facts.set(key + "decoded_utf8", "")
            facts.set(key + "scalars", "")
            facts.set(key + "utf16", "")
            continue
        try:
            position = snapshot.decoded_position(raw_byte)
        except Exception:
            facts.set(key + "raw_byte", str(raw_byte))
            facts.set(key + "decoded_utf8", "")
            facts.set(key + "scalars", "")
            facts.set(key + "utf16", "")
            continue
        facts.set(key + "raw_byte", str(position.raw_byte))
        facts.set(key + "decoded_utf8", str(position.decoded_utf8_byte))
        facts.set(key + "scalars", str(position.unicode_scalar_offset))
        facts.set(key + "utf16", str(position.utf16_code_unit_offset))


def _emit_patch_facts(facts: Facts, case: dict, raw: bytes, snapshot, request: EncodingRequest) -> None:
    key = "patch."
    patch_desc = case.get("patch")
    if patch_desc is None or snapshot is None:
        facts.set(key + "status", "Skipped")
        facts.set(key + "failure", "")
        facts.set(key + "output", "")
        facts.set(key + "replacement_count", "")
        return
    replacements = _build_source_replacements(snapshot, patch_desc.get("replacements", []))
    if replacements is None:
        facts.set(key + "status", "Failed")
        facts.set(key + "failure", "core.protocol.invalid-value@1")
        facts.set(key + "output", "")
        facts.set(key + "replacement_count", "")
        return
    limits = SourcePatchLimits()
    try:
        patch = SourcePatch.create(snapshot, replacements, {}, limits)
    except (SourcePatchError, SourceError) as error:
        facts.set(key + "status", "Failed")
        facts.set(key + "failure", _source_patch_code(error))
        facts.set(key + "output", "")
        facts.set(key + "replacement_count", "")
        return
    base = snapshot
    if patch_desc.get("apply_to") == "tampered":
        tampered = bytearray(raw)
        if len(tampered) > 0:
            tampered[-1] ^= 0x01
        try:
            tampered_snapshot = SourceSnapshot.from_raw(bytes(tampered), request, SourceLimits())
        except SourceError as error:
            facts.set(key + "status", "Failed")
            facts.set(key + "failure", _source_patch_code(error))
            facts.set(key + "output", "")
            facts.set(key + "replacement_count", "")
            return
        base = tampered_snapshot
    try:
        target = patch.apply(base, limits)
    except (SourcePatchError, SourceError) as error:
        facts.set(key + "status", "Failed")
        facts.set(key + "failure", _source_patch_code(error))
        facts.set(key + "output", "")
        facts.set(key + "replacement_count", "")
        return
    facts.set(key + "status", "Applied")
    facts.set(key + "failure", "")
    facts.set(key + "output", escape_bytes(target.bytes()))
    facts.set(key + "replacement_count", str(len(replacements)))


def _build_source_replacements(snapshot, descriptions: list) -> list[SourceReplacement] | None:
    base = snapshot.bytes()
    replacements = []
    for desc in descriptions:
        old_start = desc.get("old_start", -1)
        old_end = desc.get("old_end", -1)
        if old_start < 0 or old_end < old_start or old_end > len(base):
            return None
        try:
            replacement = bytes.fromhex(desc.get("replacement_hex", ""))
        except ValueError:
            return None
        original = base[old_start:old_end]
        replacements.append(SourceReplacement.new(old_start, old_end, original, replacement))
    return replacements


def _source_patch_code(error) -> str:
    if hasattr(error, "code"):
        return error.code
    return "core.protocol.invalid-value@1"


# ---------------------------------------------------------------------------
# Case dispatch and comparison (mirrors normalized_test.go)
# ---------------------------------------------------------------------------


def run_case(case: dict) -> list[str]:
    """Dispatches one case to its face runner."""
    kind = case.get("kind")
    if kind == "document":
        return run_document_case(case)
    if kind == "source":
        return run_source_case(case)
    raise case_files.CaseFileError(f"unknown case kind {kind!r}")


def compare_facts(case_id: str, own_lines: list[str], evidence_lines: list[str]) -> list[str]:
    """Compares the two fact line sets field by field (the Go test's
    ``compareFacts``, normalized_test.go:191-231). Every key must exist on
    both sides with an equal value; a missing or extra key is itself a
    differential failure."""
    own_facts: dict[str, str] = {}
    for line in own_lines:
        key, value, ok = _split_fact(line)
        if not ok:
            return [f"case {case_id}: Python side emitted malformed fact line {line!r}"]
        if key in own_facts:
            return [f"case {case_id}: Python side emitted duplicate fact key {key!r}"]
        own_facts[key] = value
    evidence_facts: dict[str, str] = {}
    for line in evidence_lines:
        key, value, ok = _split_fact(line)
        if not ok:
            return [f"case {case_id}: Rust side emitted malformed fact line {line!r}"]
        if key in evidence_facts:
            return [f"case {case_id}: Rust side emitted duplicate fact key {key!r}"]
        evidence_facts[key] = value
    failures: list[str] = []
    for key, own_value in own_facts.items():
        rust_value = evidence_facts.get(key)
        if rust_value is None:
            failures.append(
                f"case {case_id}: field {key}: Rust side has no such field (Python value {own_value!r})"
            )
        elif own_value != rust_value:
            failures.append(
                f"case {case_id}: field {key} differs\n  Python: {own_value!r}\n  Rust:   {rust_value!r}"
            )
    for key, rust_value in evidence_facts.items():
        if key not in own_facts:
            failures.append(
                f"case {case_id}: field {key}: Python side has no such field (Rust value {rust_value!r})"
            )
    return failures


def _split_fact(line: str) -> tuple[str, str, bool]:
    index = line.find("=")
    if index < 0:
        return "", "", False
    return line[:index], line[index + 1 :], True


def run_differential(rust_dir: str | None = None, python_evidence_dir: str | None = None) -> NormalizedResult:
    """Runs the forward differential over the whole input set and emits the
    Python evidence files into ``python_evidence_dir`` when given (the
    reverse direction's input)."""
    cases = load_case_file()
    if rust_dir is None:
        rust_dir = os.environ.get(RUST_DIR_ENV)
    if python_evidence_dir is not None:
        os.makedirs(python_evidence_dir, exist_ok=True)
    known_ids = {case["id"] for case in cases}
    if rust_dir is not None:
        for name in os.listdir(rust_dir):
            if name.endswith(".txt") and name[: -len(".txt")] not in known_ids:
                raise case_files.CaseFileError(
                    f"rust evidence file {name!r} does not correspond to any case (case file drift?)"
                )
    passed = 0
    failures: list[str] = []
    for case in cases:
        own_lines = run_case(case)
        if python_evidence_dir is not None:
            _write_evidence_file(python_evidence_dir, case["id"], own_lines)
        if rust_dir is None:
            continue
        evidence_lines = case_files.read_evidence_file(rust_dir, case["id"])
        field_failures = compare_facts(case["id"], own_lines, evidence_lines)
        if not field_failures:
            passed += 1
            continue
        failures.extend(field_failures)
    emitted = len(cases) if python_evidence_dir is not None else 0
    return NormalizedResult(passed=passed, failed=len(failures), failures=tuple(failures), emitted=emitted)


def emit_evidence_to_dir(cases: list[dict], directory: str) -> int:
    """Writes the Python-side evidence files: one ``<case-id>.txt`` per case,
    every fact line as ``key=value\\n`` (byte-identical in shape to the Rust
    emitter's files, so the Rust consume mode reads them with the same
    reader)."""
    os.makedirs(directory, exist_ok=True)
    emitted = 0
    for case in cases:
        lines = run_case(case)
        _write_evidence_file(directory, case["id"], lines)
        emitted += 1
    return emitted


def _write_evidence_file(directory: str, case_id: str, lines: list[str]) -> None:
    content = "".join(line + "\n" for line in lines)
    with open(os.path.join(directory, case_id + ".txt"), "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def normalized_summary(result: NormalizedResult) -> str:
    """The human summary line (the shape the driver script greps)."""
    return f"normalized-result differential: {result.passed}/{result.total} equal"

