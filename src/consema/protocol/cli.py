"""CLI machine-protocol payloads: RFC 0015 §4/§8/§9.

Authority: RFC 0015 §4 (the `core.cli-output@1` envelope), §8 (the
`core.batch-plan@1` manifest), §9 (the `core.batch-result@1` manifest), and
the record validation in crates/consema-protocol/src/cli.rs. Go
(go/protocol/cli.go, cli_json.go) is a cross-reference only.

Every decoder re-validates the cross constraints (closed command and
exit-class sets, payload-schema/command consistency, redaction consistency,
digest equality, per-status presence rules) instead of trusting the schema
discriminator.

The batch-plan manifest is the only record whose JSON transport carries
Bytes leaves inside nested source-patch records; its JSON codec therefore
operates on the canonical tagged-JSON parse tree with record paths relative
to "$" (cli.rs), while the other messages round-trip through the value
model.
"""

from __future__ import annotations

import enum
import hashlib

from consema.core.value import Kind, PortableValue
from consema.protocol.canonical import (
    PortableValueJSONSchema,
    decode_json,
    decode_pvce,
    encode_json,
    encode_pvce,
    json_is_tagged_null,
    json_record_fields,
    json_string_map,
    json_tagged_array,
    json_tagged_boolean,
    json_tagged_bytes,
    json_tagged_string,
    json_tagged_uint32,
    json_tagged_uint64,
    parse_json_document,
    ensure_canonical,
    tagged_array,
    tagged_boolean,
    tagged_bytes,
    tagged_integer,
    tagged_null,
    tagged_object,
    tagged_string,
)
from consema.protocol.diagnostic import (
    Diagnostic,
    parse_diagnostic_node,
    diagnostic_node,
)
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.errors import invalid, resource
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.exit_class import ExitClass, parse_exit_class
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import ProfileReference, _parse_profile_reference
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    integer_value,
    schema_fields,
    sequence_of,
    string_map_from_object,
    string_map_object,
    string_of,
    unsigned32,
    unsigned64,
)


# --------------------------------------------------------------------------
# CliCommand
# --------------------------------------------------------------------------

class CliCommand(enum.Enum):
    """One of the eleven formal CLI commands (RFC 0015 §6.1)."""

    INSPECT = "inspect"
    CAPABILITIES = "capabilities"
    QUERY = "query"
    PROJECT = "project"
    MATERIALIZE = "materialize"
    CONVERT = "convert"
    EDIT = "edit"
    PLAN = "plan"
    APPLY = "apply"
    CONFORMANCE = "conformance"
    EXPLAIN = "explain"


def parse_cli_command(name: str) -> CliCommand | None:
    try:
        return CliCommand(name)
    except ValueError:
        return None


def payload_schemas(command: CliCommand) -> list[str]:
    """The payload schemas the command may carry (RFC 0015 §6.1 table)."""
    return {
        CliCommand.INSPECT: ["cli.inspect@1"],
        CliCommand.CAPABILITIES: ["cli.capabilities@1"],
        CliCommand.QUERY: [
            "core.query-result@1",
            "core.ini-query-result@1",
            "core.java-properties-query-result@1",
            "core.yaml-query-result@1",
            "core.graph-query-result@1",
        ],
        CliCommand.PROJECT: ["core.projection-result@1"],
        CliCommand.MATERIALIZE: ["core.materialization-result@2"],
        CliCommand.CONVERT: ["cli.convert@1"],
        CliCommand.EDIT: ["cli.edit@1"],
        CliCommand.PLAN: ["core.batch-plan@1"],
        CliCommand.APPLY: ["core.batch-result@1"],
        CliCommand.CONFORMANCE: ["cli.conformance@1"],
        CliCommand.EXPLAIN: ["cli.explain@1"],
    }[command]


# --------------------------------------------------------------------------
# Redaction / ContentDigest / FormatOperationId / EditOperationSummary
# --------------------------------------------------------------------------

class Redaction:
    """The envelope redaction facts (RFC 0015 §11.3): redacted == (count > 0)."""

    __slots__ = ("redacted", "count")

    def __init__(self, redacted: bool, count: int):
        if redacted != (count > 0):
            raise invalid("$.redaction", "redacted must equal (count > 0)")
        self.redacted = redacted
        self.count = count

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Redaction):
            return NotImplemented
        return self.redacted == other.redacted and self.count == other.count


class ContentDigest:
    """The stable SHA-256 identity of exact raw source bytes.

    The document milestone (L1) owns the full source model; this is the
    wire-form digest used by the CLI records (sha256 algorithm, lowercase
    hex spelling).
    """

    __slots__ = ("_bytes",)

    def __init__(self, digest_bytes: bytes):
        if len(digest_bytes) != 32:
            raise ValueError("sha256 digest must be 32 bytes")
        self._bytes = bytes(digest_bytes)

    @staticmethod
    def of(data: bytes) -> "ContentDigest":
        return ContentDigest(hashlib.sha256(data).digest())

    @property
    def algorithm(self) -> str:
        return "sha256"

    def bytes(self) -> bytes:
        return self._bytes

    def hex(self) -> str:
        return self._bytes.hex()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ContentDigest):
            return NotImplemented
        return self._bytes == other._bytes

    def __hash__(self) -> int:
        return hash(self._bytes)


class FormatOperationId:
    """A stable format-operation contract identity."""

    __slots__ = ("id", "version")

    def __init__(self, id: str, version: int):
        self.id = id
        self.version = version

    def __repr__(self) -> str:
        return f"FormatOperationId({self.id!r}@{self.version})"


class EditOperationSummary:
    """One safe, content-free summary of a declared edit operation.

    Summary values must not contain raw edited values.
    """

    __slots__ = ("operation", "summary")

    def __init__(self, operation: FormatOperationId, summary: dict[str, str]):
        if len(summary) > 64:
            raise invalid("$.files[].operations", "invalid operation summary")
        for name, value in summary.items():
            if not _valid_summary_name(name) or not value or len(value) > 1024:
                raise invalid("$.files[].operations", "invalid operation summary")
        self.operation = operation
        self.summary = dict(summary)


def _valid_summary_name(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    return all(
        ("a" <= character <= "z") or ("0" <= character <= "9") or character == "_"
        for character in name
    )


# --------------------------------------------------------------------------
# SourceEncoding / EncodingFacts / SourceReplacement / SourcePatch
# --------------------------------------------------------------------------

_WINDOWS_CODE_PAGES = (874, 932, 936, 949, 950, 1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257, 1258, 65001)


def windows_code_page_from_number(number: int) -> int | None:
    """Resolves one numeric code page only when source contract v2 publishes
    it (the portable registry of crates/consema-document/src/source.rs:58-76)."""
    if number in _WINDOWS_CODE_PAGES:
        return number
    return None


class SourceEncoding:
    """The wire form of one core.source-encoding@1 record."""

    __slots__ = ("kind", "windows_code_page")

    def __init__(self, kind: str, windows_code_page: int | None):
        self.kind = kind
        self.windows_code_page = windows_code_page

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceEncoding):
            return NotImplemented
        return self.kind == other.kind and self.windows_code_page == other.windows_code_page


class EncodingFacts:
    """The source-patch@2 encoding facts record.

    The semantic consistency checks of the facts (BOM policy, code-page
    registration, selected-encoding reconciliation) belong to the document
    milestone; this module validates the record structure and carries the
    facts.
    """

    __slots__ = (
        "profile_default",
        "bom_policy",
        "bom",
        "declaration",
        "caller_override",
        "selected",
    )

    def __init__(
        self,
        profile_default: SourceEncoding | None,
        bom_policy: str,
        bom: str | None,
        declaration: SourceEncoding | None,
        caller_override: SourceEncoding | None,
        selected: SourceEncoding | None,
    ):
        self.profile_default = profile_default
        self.bom_policy = bom_policy
        self.bom = bom
        self.declaration = declaration
        self.caller_override = caller_override
        self.selected = selected


class SourceReplacement:
    """One structural replacement of a wire source patch."""

    __slots__ = (
        "old_start",
        "old_end",
        "original",
        "replacement",
        "redact_original",
        "redact_replacement",
    )

    def __init__(
        self,
        old_start: int,
        old_end: int,
        original: bytes,
        replacement: bytes,
        redact_original: bool,
        redact_replacement: bool,
    ):
        self.old_start = old_start
        self.old_end = old_end
        self.original = bytes(original)
        self.replacement = bytes(replacement)
        self.redact_original = redact_original
        self.redact_replacement = redact_replacement


class SourcePatch:
    """The wire form of a source patch (core.source-patch@2 record)."""

    __slots__ = ("base_digest", "target_digest", "encoding", "replacements", "metadata")

    def __init__(
        self,
        base_digest: ContentDigest,
        target_digest: ContentDigest,
        encoding: EncodingFacts,
        replacements: list[SourceReplacement],
        metadata: dict[str, str],
    ):
        self.base_digest = base_digest
        self.target_digest = target_digest
        self.encoding = encoding
        self.replacements = replacements
        self.metadata = dict(metadata)


class SourcePatchLimits:
    """Resource bounds applied while decoding a source-patch record.

    Mirrors the document source-patch limits (RFC 0003 §12;
    crates/consema-protocol/src/cli.rs from_value_with_registry patch_limits
    parameter; go/protocol/records_source.go DefaultSourcePatchLimits):
    ``max_replacements`` bounds the ordered replacement count and
    ``max_patch_bytes`` bounds the sum of original and replacement bytes.
    """

    __slots__ = ("max_replacements", "max_patch_bytes")

    def __init__(
        self,
        max_replacements: int = 100_000,
        max_patch_bytes: int = 128 * 1024 * 1024,
    ):
        self.max_replacements = max_replacements
        self.max_patch_bytes = max_patch_bytes


# --------------------------------------------------------------------------
# core.batch-plan@1
# --------------------------------------------------------------------------

class BatchPlanFileStatus(enum.Enum):
    """One file-level status in a core.batch-plan@1 manifest (RFC 0015 §8.2)."""

    PLANNED = "planned"
    FAILED = "failed"


class BatchPlanFileEntry:
    """One file entry of a core.batch-plan@1 manifest (cli.rs:376-541)."""

    __slots__ = (
        "path",
        "status",
        "profile",
        "source_digest",
        "operations",
        "source_patch",
        "failure_code",
        "diagnostics",
    )

    def __init__(
        self,
        path: str,
        status: BatchPlanFileStatus,
        profile: ProfileReference | None,
        source_digest: ContentDigest | None,
        operations: list[EditOperationSummary] | None,
        source_patch: SourcePatch | None,
        failure_code: str | None,
        diagnostics: list[Diagnostic] | None,
        registry: ErrorCodeRegistry | None = None,
    ):
        registry = registry or ErrorCodeRegistry(7)
        if not path or len(path.encode("utf-8")) > 1024:
            raise invalid("$.files[].path", "invalid path")
        for index, operation in enumerate(operations or []):
            try:
                EditOperationSummary(operation.operation, operation.summary)
            except ProtocolError as error:
                raise protocol_error(
                    error.kind, f"$.files[].operations[{index}]", error.detail
                ) from None
        if status is BatchPlanFileStatus.PLANNED:
            if (
                profile is None
                or source_digest is None
                or operations is None
                or source_patch is None
            ):
                raise invalid(
                    "$.files[]",
                    "planned entries require profile, source_digest, operations, and source_patch",
                )
            if failure_code is not None or diagnostics is not None:
                raise invalid(
                    "$.files[]",
                    "planned entries cannot carry failure_code or diagnostics",
                )
            if source_digest != source_patch.base_digest:
                raise invalid(
                    "$.files[].source_digest",
                    "source_digest must equal source_patch.base_digest",
                )
        else:
            if (
                profile is not None
                or source_digest is not None
                or operations is not None
                or source_patch is not None
            ):
                raise invalid("$.files[]", "failed entries cannot carry planning facts")
            if not failure_code:
                raise invalid(
                    "$.files[].failure_code", "failed entries require a failure_code"
                )
            if diagnostics is None:
                raise invalid(
                    "$.files[].diagnostics", "failed entries require a diagnostics sequence"
                )
        for index, diagnostic in enumerate(diagnostics or []):
            from consema.protocol.diagnostic import validate_diagnostic_code

            try:
                validate_diagnostic_code(diagnostic.code, diagnostic.category, registry)
            except ProtocolError as error:
                raise protocol_error(
                    error.kind,
                    f"$.files[].diagnostics[{index}]",
                    error.detail,
                ) from None
        self.path = path
        self.status = status
        self.profile = profile
        self.source_digest = source_digest
        self.operations = operations
        self.source_patch = source_patch
        self.failure_code = failure_code
        self.diagnostics = diagnostics


class BatchPlanMessage:
    """The full core.batch-plan@1 manifest (RFC 0015 §8; cli.rs:543-641)."""

    __slots__ = ("product_version", "files")

    def __init__(self, product_version: str, files: list[BatchPlanFileEntry]):
        self._validate(product_version, files)
        self.product_version = product_version
        self.files = files

    @staticmethod
    def _validate(product_version: str, files: list[BatchPlanFileEntry]) -> None:
        if not product_version:
            raise invalid("$.product_version", "product_version cannot be empty")
        for index, entry in enumerate(files):
            _revalidate_plan_entry(entry, index)

    # -- value-level codec -------------------------------------------------

    def to_value(self) -> PortableValue:
        files = [
            _plan_entry_value(entry, index) for index, entry in enumerate(self.files)
        ]
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.batch-plan@1")),
                ("product_version", PortableValue.string(self.product_version)),
                ("command", PortableValue.string("plan")),
                ("files", PortableValue.sequence(files)),
            ]
        )

    @staticmethod
    def from_value(
        value: PortableValue, registry: ErrorCodeRegistry | None = None
    ) -> "BatchPlanMessage":
        """Strictly decodes ``core.batch-plan@1`` under the semantic-model v7
        error registry and default source-patch limits (cli.rs:610-614)."""
        return BatchPlanMessage.from_value_with_registry_and_patch_limits(
            value, registry or ErrorCodeRegistry(7), SourcePatchLimits()
        )

    @staticmethod
    def from_value_with_registry_and_patch_limits(
        value: PortableValue, registry: ErrorCodeRegistry, patch_limits: SourcePatchLimits
    ) -> "BatchPlanMessage":
        """Strictly decodes the manifest and re-verifies every cross
        constraint under one explicit registry and one explicit source-patch
        replacement budget (cli.rs from_value_with_registry patch_limits
        parameter; go/protocol/cli.go:480-486)."""
        fields = schema_fields(
            value,
            "core.batch-plan@1",
            ["schema", "product_version", "command", "files"],
            "$",
        )
        command = string_of(fields[2], "$.command")
        if command != "plan":
            raise invalid("$.command", 'expected command "plan"')
        files = []
        for index, item in enumerate(sequence_of(fields[3], "$.files")):
            files.append(_parse_plan_entry(item, index, registry, patch_limits))
        product_version = string_of(fields[1], "$.product_version")
        return BatchPlanMessage(product_version, files)

    # -- JSON tree codec (Bytes leaves; record paths relative to "$") ------

    def to_json(self, limits: ProtocolLimits) -> bytes:
        from consema.protocol.canonical import _encode_transport

        return _encode_transport(_plan_message_node(self), limits)

    @staticmethod
    def from_json(data: bytes, limits: ProtocolLimits) -> "BatchPlanMessage":
        document = parse_json_document(data, limits)
        ensure_canonical(document, data, limits)
        # The transport envelope is a plain JSON object (not a tagged value);
        # the record paths restart at "$" for the plan value (cli.rs).
        from consema.protocol.canonical import json_object_exact, json_string_of

        fields = json_object_exact(document, ["schema", "value"], "$")
        schema = json_string_of(fields[0], "$.schema")
        if schema != PortableValueJSONSchema:
            raise protocol_error(
                ProtocolErrorKind.SCHEMA_MISMATCH, "$.schema", "unexpected transport schema"
            )
        return _parse_plan_message_node(fields[1], ErrorCodeRegistry(7))

    # -- PVCE codec ---------------------------------------------------------

    def to_pvce(self, limits: ProtocolLimits) -> bytes:
        return encode_pvce(self.to_value(), limits)

    @staticmethod
    def from_pvce(data: bytes, limits: ProtocolLimits) -> "BatchPlanMessage":
        return BatchPlanMessage.from_value(decode_pvce(data, limits))


class BatchResultFileStatus(enum.Enum):
    """One file-level status in a core.batch-result@1 manifest (RFC 0015 §9.2)."""

    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED_STALE = "skipped-stale"


class BatchResultFileEntry:
    """One result entry of a core.batch-result@1 manifest (cli.rs:656-743)."""

    __slots__ = ("path", "status", "failure_code", "target_digest", "redacted")

    def __init__(
        self,
        path: str,
        status: BatchResultFileStatus,
        failure_code: str | None,
        target_digest: ContentDigest | None,
        redacted: bool,
    ):
        if not path or len(path.encode("utf-8")) > 1024:
            raise invalid("$.files[].path", "invalid path")
        if status is BatchResultFileStatus.COMPLETED:
            if failure_code is not None or target_digest is None:
                raise invalid(
                    "$.files[]",
                    "completed entries require a target_digest and no failure_code",
                )
        elif status in (BatchResultFileStatus.FAILED, BatchResultFileStatus.SKIPPED_STALE):
            if not failure_code or target_digest is not None:
                raise invalid(
                    "$.files[]",
                    "failed or skipped-stale entries require a failure_code and no target_digest",
                )
        elif status is BatchResultFileStatus.PENDING:
            if failure_code is not None or target_digest is not None:
                raise invalid(
                    "$.files[]",
                    "pending entries cannot carry failure_code or target_digest",
                )
        else:
            raise invalid("$.files[].status", "unknown result file status")
        self.path = path
        self.status = status
        self.failure_code = failure_code
        self.target_digest = target_digest
        self.redacted = redacted


class BatchResultMessage:
    """The full core.batch-result@1 manifest (RFC 0015 §9; cli.rs:745-822)."""

    __slots__ = ("product_version", "files")

    def __init__(self, product_version: str, files: list[BatchResultFileEntry]):
        if not product_version:
            raise invalid("$.product_version", "product_version cannot be empty")
        self.product_version = product_version
        self.files = files

    def to_value(self) -> PortableValue:
        files = [_result_entry_value(entry) for entry in self.files]
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.batch-result@1")),
                ("product_version", PortableValue.string(self.product_version)),
                ("command", PortableValue.string("apply")),
                ("files", PortableValue.sequence(files)),
            ]
        )

    @staticmethod
    def from_value(value: PortableValue) -> "BatchResultMessage":
        fields = schema_fields(
            value,
            "core.batch-result@1",
            ["schema", "product_version", "command", "files"],
            "$",
        )
        command = string_of(fields[2], "$.command")
        if command != "apply":
            raise invalid("$.command", 'expected command "apply"')
        files = []
        for index, item in enumerate(sequence_of(fields[3], "$.files")):
            files.append(_parse_result_entry(item, f"$.files[{index}]"))
        product_version = string_of(fields[1], "$.product_version")
        return BatchResultMessage(product_version, files)

    def to_json(self, limits: ProtocolLimits) -> bytes:
        return encode_json(self.to_value(), limits)

    @staticmethod
    def from_json(data: bytes, limits: ProtocolLimits) -> "BatchResultMessage":
        return BatchResultMessage.from_value(decode_json(data, limits))

    def to_pvce(self, limits: ProtocolLimits) -> bytes:
        return encode_pvce(self.to_value(), limits)

    @staticmethod
    def from_pvce(data: bytes, limits: ProtocolLimits) -> "BatchResultMessage":
        return BatchResultMessage.from_value(decode_pvce(data, limits))


# --------------------------------------------------------------------------
# core.cli-output@1
# --------------------------------------------------------------------------

class CliOutputMessage:
    """The full core.cli-output@1 machine envelope (RFC 0015 §4; cli.rs:149-364)."""

    __slots__ = ("command", "exit_class", "product_version", "payload", "diagnostics", "redaction")

    def __init__(
        self,
        command: CliCommand,
        exit_class: ExitClass,
        product_version: str,
        payload: PortableValue,
        diagnostics: list[Diagnostic],
        redaction: Redaction,
        registry: ErrorCodeRegistry | None = None,
    ):
        registry = registry or ErrorCodeRegistry(7)
        if not _is_semantic_version(product_version):
            raise invalid(
                "$.product_version",
                "expected MAJOR.MINOR.PATCH[-prerelease] without leading zeros or build metadata",
            )
        _validate_payload_schema(payload, command)
        for index, diagnostic in enumerate(diagnostics):
            from consema.protocol.diagnostic import validate_diagnostic_code

            try:
                validate_diagnostic_code(diagnostic.code, diagnostic.category, registry)
            except ProtocolError as error:
                raise protocol_error(
                    error.kind, f"$.diagnostics[{index}]", error.detail
                ) from None
        self.command = command
        self.exit_class = exit_class
        self.product_version = product_version
        self.payload = payload
        self.diagnostics = diagnostics
        self.redaction = redaction

    def to_value(self) -> PortableValue:
        diagnostics = [diagnostic.to_value() for diagnostic in self.diagnostics]
        redaction = PortableValue.object(
            [
                ("redacted", PortableValue.boolean(self.redaction.redacted)),
                ("count", integer_value(self.redaction.count)),
            ]
        )
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.cli-output@1")),
                ("command", PortableValue.string(self.command.value)),
                ("exit_class", PortableValue.string(self.exit_class.value)),
                ("product_version", PortableValue.string(self.product_version)),
                ("payload", self.payload),
                ("diagnostics", PortableValue.sequence(diagnostics)),
                ("redaction", redaction),
            ]
        )

    @staticmethod
    def from_value(
        value: PortableValue, registry: ErrorCodeRegistry | None = None
    ) -> "CliOutputMessage":
        registry = registry or ErrorCodeRegistry(7)
        fields = schema_fields(
            value,
            "core.cli-output@1",
            ["schema", "command", "exit_class", "product_version", "payload",
             "diagnostics", "redaction"],
            "$",
        )
        command_name = string_of(fields[1], "$.command")
        command = parse_cli_command(command_name)
        if command is None:
            raise invalid("$.command", "unknown command")
        exit_class_name = string_of(fields[2], "$.exit_class")
        exit_class = parse_exit_class(exit_class_name)
        if exit_class is None:
            raise invalid("$.exit_class", "unknown exit class")
        product_version = string_of(fields[3], "$.product_version")
        if not _is_semantic_version(product_version):
            raise invalid(
                "$.product_version",
                "expected MAJOR.MINOR.PATCH[-prerelease] without leading zeros or build metadata",
            )
        _validate_payload_schema(fields[4], command)
        diagnostics = [
            Diagnostic.from_value(item, registry)
            for item in sequence_of(fields[5], "$.diagnostics")
        ]
        redaction_fields = exact_fields(fields[6], ["redacted", "count"], "$.redaction")
        redacted = boolean_of(redaction_fields[0], "$.redaction.redacted")
        count = unsigned64(redaction_fields[1], "$.redaction.count")
        redaction = Redaction(redacted, count)
        return CliOutputMessage(
            command, exit_class, product_version, fields[4], diagnostics, redaction, registry
        )

    def to_json(self, limits: ProtocolLimits) -> bytes:
        return encode_json(self.to_value(), limits)

    @staticmethod
    def from_json(data: bytes, limits: ProtocolLimits) -> "CliOutputMessage":
        return CliOutputMessage.from_value(decode_json(data, limits))

    def to_pvce(self, limits: ProtocolLimits) -> bytes:
        return encode_pvce(self.to_value(), limits)

    @staticmethod
    def from_pvce(data: bytes, limits: ProtocolLimits) -> "CliOutputMessage":
        return CliOutputMessage.from_value(decode_pvce(data, limits))


# --------------------------------------------------------------------------
# shared validation helpers
# --------------------------------------------------------------------------

def _validate_payload_schema(payload: PortableValue, command: CliCommand) -> None:
    """The payload must be an Object whose first field is ``schema`` carrying
    one of the command's published schemas (cli.rs:824-868)."""
    if payload.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.payload", "payload must be an Object")
    entries = payload.as_object()
    if not entries:
        raise protocol_error(
            ProtocolErrorKind.MISSING_FIELD, "$.payload.schema", "payload schema is absent"
        )
    if entries[0][0] != "schema":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, "$.payload", "schema must be the first field"
        )
    schema = string_of(entries[0][1], "$.payload.schema")
    if schema not in payload_schemas(command):
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH,
            "$.payload.schema",
            f"payload schema {schema} is not published by {command.value}",
        )


def _is_semantic_version(version: str) -> bool:
    """Validates the SemVer 2.0 core shape of a product version (RFC 0015
    §3.3): MAJOR.MINOR.PATCH with an optional dot-separated -prerelease
    suffix; numeric segments and numeric prerelease identifiers carry no
    leading zeros; build metadata ('+' suffix) is rejected."""
    if "+" in version:
        return False
    core = version
    prerelease = ""
    has_prerelease = False
    if "-" in version:
        core, prerelease = version.split("-", 1)
        has_prerelease = True
    if not _numeric_core(core):
        return False
    if not has_prerelease:
        return True
    if not prerelease:
        return False
    return all(_prerelease_identifier(item) for item in prerelease.split("."))


def _numeric_core(text: str) -> bool:
    segments = text.split(".")
    return len(segments) == 3 and all(_numeric_segment(segment) for segment in segments)


def _numeric_segment(segment: str) -> bool:
    if not segment or not segment.isdigit():
        return False
    return len(segment) == 1 or segment[0] != "0"


def _prerelease_identifier(identifier: str) -> bool:
    if not identifier:
        return False
    numeric = True
    for character in identifier:
        if character < "0" or character > "9":
            numeric = False
            if not (
                ("a" <= character <= "z")
                or ("A" <= character <= "Z")
                or character == "-"
            ):
                return False
    if numeric and len(identifier) > 1 and identifier[0] == "0":
        return False
    return True


def _revalidate_plan_entry(entry: BatchPlanFileEntry, index: int) -> None:
    """Re-verifies the entry-level cross constraints of a manifest."""
    path = f"$.files[{index}]"
    if entry.status is BatchPlanFileStatus.PLANNED:
        if (
            entry.profile is None
            or entry.source_digest is None
            or entry.operations is None
            or entry.source_patch is None
        ):
            raise invalid(path, "planned entries require all planning facts")
        if entry.source_digest != entry.source_patch.base_digest:
            raise invalid(
                f"{path}.source_digest", "source_digest must equal source_patch.base_digest"
            )
    else:
        if not entry.failure_code or entry.diagnostics is None:
            raise invalid(path, "failed entries require failure_code and diagnostics")


# --------------------------------------------------------------------------
# plan/result entry codecs
# --------------------------------------------------------------------------

def _plan_entry_value(entry: BatchPlanFileEntry, index: int) -> PortableValue:
    profile = PortableValue.null()
    if entry.profile is not None:
        profile = _reference_value(entry.profile.id, entry.profile.version)
    source_digest = PortableValue.null()
    if entry.source_digest is not None:
        source_digest = _digest_value(entry.source_digest)
    operations = PortableValue.null()
    if entry.operations is not None:
        items = []
        for operation in entry.operations:
            items.append(
                PortableValue.object(
                    [
                        (
                            "operation",
                            _reference_value(operation.operation.id, operation.operation.version),
                        ),
                        ("summary", string_map_object(operation.summary)),
                    ]
                )
            )
        operations = PortableValue.sequence(items)
    source_patch = PortableValue.null()
    if entry.source_patch is not None:
        source_patch = _source_patch_value(entry.source_patch)
    failure_code = PortableValue.null()
    if entry.failure_code is not None:
        failure_code = PortableValue.string(entry.failure_code)
    diagnostics = PortableValue.null()
    if entry.diagnostics is not None:
        diagnostics = PortableValue.sequence(
            [diagnostic.to_value() for diagnostic in entry.diagnostics]
        )
    return PortableValue.object(
        [
            ("path", PortableValue.string(entry.path)),
            ("status", PortableValue.string(entry.status.value)),
            ("profile", profile),
            ("source_digest", source_digest),
            ("operations", operations),
            ("source_patch", source_patch),
            ("failure_code", failure_code),
            ("diagnostics", diagnostics),
        ]
    )


def _parse_plan_entry(
    value: PortableValue,
    index: int,
    registry: ErrorCodeRegistry,
    patch_limits: SourcePatchLimits | None = None,
) -> BatchPlanFileEntry:
    path = f"$.files[{index}]"
    fields = exact_fields(
        value,
        ["path", "status", "profile", "source_digest", "operations",
         "source_patch", "failure_code", "diagnostics"],
        path,
    )
    status_name = string_of(fields[1], f"{path}.status")
    try:
        status = BatchPlanFileStatus(status_name)
    except ValueError:
        raise invalid(f"{path}.status", "unknown plan file status") from None
    profile = None
    source_digest = None
    operations = None
    source_patch = None
    failure_code = None
    diagnostics = None
    if status is BatchPlanFileStatus.PLANNED:
        profile = _parse_profile_reference(fields[2], f"{path}.profile")
        source_digest = _parse_digest(fields[3], f"{path}.source_digest")
        operation_values = sequence_of(fields[4], f"{path}.operations")
        operations = [
            _parse_operation_summary(item, f"{path}.operations[{operation_index}]")
            for operation_index, item in enumerate(operation_values)
        ]
        source_patch = _parse_source_patch_value(
            fields[5], f"{path}.source_patch", patch_limits or SourcePatchLimits()
        )
        if fields[6].kind is not Kind.NULL or fields[7].kind is not Kind.NULL:
            raise invalid(path, "planned entries cannot carry failure_code or diagnostics")
    else:
        for field_index in (2, 3, 4, 5):
            if fields[field_index].kind is not Kind.NULL:
                raise invalid(path, "failed entries cannot carry planning facts")
        code = string_of(fields[6], f"{path}.failure_code")
        if not code:
            raise invalid(f"{path}.failure_code", "failure_code cannot be empty")
        failure_code = code
        diagnostic_values = sequence_of(fields[7], f"{path}.diagnostics")
        diagnostics = [
            Diagnostic.from_value(item, registry) for item in diagnostic_values
        ]
    path_text = string_of(fields[0], f"{path}.path")
    return BatchPlanFileEntry(
        path_text, status, profile, source_digest, operations,
        source_patch, failure_code, diagnostics, registry,
    )


def _result_entry_value(entry: BatchResultFileEntry) -> PortableValue:
    failure_code = PortableValue.null()
    if entry.failure_code is not None:
        failure_code = PortableValue.string(entry.failure_code)
    target_digest = PortableValue.null()
    if entry.target_digest is not None:
        target_digest = _digest_value(entry.target_digest)
    return PortableValue.object(
        [
            ("path", PortableValue.string(entry.path)),
            ("status", PortableValue.string(entry.status.value)),
            ("failure_code", failure_code),
            ("target_digest", target_digest),
            ("redacted", PortableValue.boolean(entry.redacted)),
        ]
    )


def _parse_result_entry(value: PortableValue, path: str) -> BatchResultFileEntry:
    fields = exact_fields(
        value, ["path", "status", "failure_code", "target_digest", "redacted"], path
    )
    status_name = string_of(fields[1], f"{path}.status")
    try:
        status = BatchResultFileStatus(status_name)
    except ValueError:
        raise invalid(f"{path}.status", "unknown result file status") from None
    failure_code = None
    if fields[2].kind is not Kind.NULL:
        failure_code = string_of(fields[2], f"{path}.failure_code")
    target_digest = None
    if fields[3].kind is not Kind.NULL:
        target_digest = _parse_digest(fields[3], f"{path}.target_digest")
    redacted = boolean_of(fields[4], f"{path}.redacted")
    path_text = string_of(fields[0], f"{path}.path")
    return BatchResultFileEntry(path_text, status, failure_code, target_digest, redacted)


def _digest_value(digest: ContentDigest) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string(digest.algorithm)),
            ("hex", PortableValue.string(digest.hex())),
        ]
    )


def _parse_digest(value: PortableValue, path: str) -> ContentDigest:
    fields = exact_fields(value, ["algorithm", "hex"], path)
    algorithm = string_of(fields[0], f"{path}.algorithm")
    if algorithm != "sha256":
        raise invalid(path, "expected sha256")
    hex_text = string_of(fields[1], f"{path}.hex")
    if len(hex_text) != 64 or not _is_lowercase_hex(hex_text):
        raise invalid(path, "invalid lowercase sha256")
    return ContentDigest(bytes.fromhex(hex_text))


def _is_lowercase_hex(text: str) -> bool:
    return all(
        ("0" <= character <= "9") or ("a" <= character <= "f") for character in text
    )


def _parse_operation_summary(value: PortableValue, path: str) -> EditOperationSummary:
    fields = exact_fields(value, ["operation", "summary"], path)
    reference = exact_fields(fields[0], ["id", "version"], f"{path}.operation")
    id = string_of(reference[0], f"{path}.operation.id")
    version = unsigned32(reference[1], f"{path}.operation.version")
    summary = string_map_from_object(fields[1], f"{path}.summary")
    return EditOperationSummary(FormatOperationId(id, version), summary)


def _source_patch_value(patch: SourcePatch) -> PortableValue:
    encoding = _encoding_facts_value(patch.encoding)
    replacements = []
    for replacement in patch.replacements:
        replacements.append(
            PortableValue.object(
                [
                    ("old_start", integer_value(replacement.old_start)),
                    ("old_end", integer_value(replacement.old_end)),
                    ("original", PortableValue.bytes_value(replacement.original)),
                    ("replacement", PortableValue.bytes_value(replacement.replacement)),
                    ("redact_original", PortableValue.boolean(replacement.redact_original)),
                    ("redact_replacement", PortableValue.boolean(replacement.redact_replacement)),
                ]
            )
        )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-patch@2")),
            ("base_digest", _digest_value(patch.base_digest)),
            ("target_digest", _digest_value(patch.target_digest)),
            ("encoding", encoding),
            ("replacements", PortableValue.sequence(replacements)),
            ("metadata", string_map_object(patch.metadata)),
        ]
    )


def _parse_source_patch_value(
    value: PortableValue, path: str, patch_limits: SourcePatchLimits | None = None
) -> SourcePatch:
    patch_limits = patch_limits or SourcePatchLimits()
    fields = schema_fields(
        value,
        "core.source-patch@2",
        ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"],
        path,
    )
    base_digest = _parse_digest(fields[1], f"{path}.base_digest")
    target_digest = _parse_digest(fields[2], f"{path}.target_digest")
    encoding = _parse_encoding_facts_value(fields[3], f"{path}.encoding")
    replacement_values = sequence_of(fields[4], f"{path}.replacements")
    if len(replacement_values) > patch_limits.max_replacements:
        raise resource(
            f"{path}.replacements", "replacement count exceeds configured limit"
        )
    patch_bytes = 0
    replacements = []
    for index, replacement_value in enumerate(replacement_values):
        replacement_path = f"{path}.replacements[{index}]"
        replacement_fields = exact_fields(
            replacement_value,
            ["old_start", "old_end", "original", "replacement",
             "redact_original", "redact_replacement"],
            replacement_path,
        )
        old_start = unsigned64(replacement_fields[0], f"{replacement_path}.old_start")
        old_end = unsigned64(replacement_fields[1], f"{replacement_path}.old_end")
        if replacement_fields[2].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, f"{replacement_path}.original", "expected Bytes"
            )
        if replacement_fields[3].kind is not Kind.BYTES:
            raise protocol_error(
                ProtocolErrorKind.WRONG_TYPE, f"{replacement_path}.replacement", "expected Bytes"
            )
        original = replacement_fields[2].as_bytes()
        replacement = replacement_fields[3].as_bytes()
        redact_original = boolean_of(replacement_fields[4], f"{replacement_path}.redact_original")
        redact_replacement = boolean_of(replacement_fields[5], f"{replacement_path}.redact_replacement")
        if old_start > old_end or len(original) != old_end - old_start:
            raise invalid(replacement_path, "invalid replacement range or original length")
        patch_bytes += len(original) + len(replacement)
        if patch_bytes > patch_limits.max_patch_bytes:
            raise resource(
                f"{path}.replacements", "patch bytes exceed the configured limit"
            )
        replacements.append(
            SourceReplacement(
                old_start, old_end, original, replacement,
                redact_original, redact_replacement,
            )
        )
    metadata = string_map_from_object(fields[5], f"{path}.metadata")
    return SourcePatch(base_digest, target_digest, encoding, replacements, metadata)


def _encoding_facts_value(facts: EncodingFacts) -> PortableValue:
    profile_default = PortableValue.null()
    if facts.profile_default is not None:
        profile_default = _source_encoding_value(facts.profile_default)
    bom = PortableValue.null()
    if facts.bom is not None:
        bom = PortableValue.string(facts.bom)
    declaration = PortableValue.null()
    if facts.declaration is not None:
        declaration = _source_encoding_value(facts.declaration)
    caller_override = PortableValue.null()
    if facts.caller_override is not None:
        caller_override = _source_encoding_value(facts.caller_override)
    selected = PortableValue.null()
    if facts.selected is not None:
        selected = _source_encoding_value(facts.selected)
    return PortableValue.object(
        [
            ("profile_default", profile_default),
            ("bom_policy", PortableValue.string(facts.bom_policy)),
            ("bom", bom),
            ("declaration", declaration),
            ("caller_override", caller_override),
            ("selected", selected),
        ]
    )


def _source_encoding_value(encoding: SourceEncoding) -> PortableValue:
    code_page = PortableValue.null()
    if encoding.windows_code_page is not None:
        code_page = integer_value(encoding.windows_code_page)
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-encoding@1")),
            ("kind", PortableValue.string(encoding.kind)),
            ("windows_code_page", code_page),
        ]
    )


def _parse_encoding_facts_value(value: PortableValue, path: str) -> EncodingFacts:
    fields = exact_fields(
        value,
        ["profile_default", "bom_policy", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    profile_default = _parse_source_encoding_value(fields[0], f"{path}.profile_default")
    bom_policy = string_of(fields[1], f"{path}.bom_policy")
    bom = None
    if fields[2].kind is not Kind.NULL:
        bom = string_of(fields[2], f"{path}.bom")
    declaration = None
    if fields[3].kind is not Kind.NULL:
        declaration = _parse_source_encoding_value(fields[3], f"{path}.declaration")
    caller_override = None
    if fields[4].kind is not Kind.NULL:
        caller_override = _parse_source_encoding_value(fields[4], f"{path}.caller_override")
    selected = _parse_source_encoding_value(fields[5], f"{path}.selected")
    return EncodingFacts(
        profile_default, bom_policy, bom, declaration, caller_override, selected
    )


def _parse_source_encoding_value(value: PortableValue, path: str) -> SourceEncoding:
    fields = schema_fields(
        value,
        "core.source-encoding@1",
        ["schema", "kind", "windows_code_page"],
        path,
    )
    kind = string_of(fields[1], f"{path}.kind")
    code_page = None
    if fields[2].kind is not Kind.NULL:
        code_page = unsigned32(fields[2], f"{path}.windows_code_page")
    if kind in ("Binary", "Utf8", "Utf16Le", "Utf16Be", "Latin1"):
        if code_page is not None:
            raise invalid(f"{path}.windows_code_page", "non-Windows encoding requires null")
    elif kind == "WindowsCodePage":
        if code_page is None:
            raise invalid(f"{path}.windows_code_page", "Windows code page requires a number")
        if windows_code_page_from_number(code_page) is None:
            raise invalid(f"{path}.windows_code_page", "unsupported Windows code page")
    else:
        raise invalid(f"{path}.kind", "unknown source encoding kind")
    return SourceEncoding(kind, code_page)


def _reference_value(id: str, version: int) -> PortableValue:
    return PortableValue.object(
        [
            ("id", PortableValue.string(id)),
            ("version", integer_value(version)),
        ]
    )


# --------------------------------------------------------------------------
# JSON tree codec for core.batch-plan@1 (Bytes leaves)
# --------------------------------------------------------------------------

def _plan_message_node(message: BatchPlanMessage) -> tuple:
    files = [
        _plan_entry_node(entry, index) for index, entry in enumerate(message.files)
    ]
    return tagged_object(
        [
            ("schema", tagged_string("core.batch-plan@1")),
            ("product_version", tagged_string(message.product_version)),
            ("command", tagged_string("plan")),
            ("files", tagged_array(files)),
        ]
    )


def _parse_plan_message_node(
    node: tuple,
    registry: ErrorCodeRegistry,
    patch_limits: SourcePatchLimits | None = None,
) -> BatchPlanMessage:
    patch_limits = patch_limits or SourcePatchLimits()
    fields = json_record_fields(
        node, ["schema", "product_version", "command", "files"], "$"
    )
    schema = json_tagged_string(fields[0], "$.schema")
    if schema != "core.batch-plan@1":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, "$.schema", "expected core.batch-plan@1"
        )
    product_version = json_tagged_string(fields[1], "$.product_version")
    command = json_tagged_string(fields[2], "$.command")
    if command != "plan":
        raise invalid("$.command", 'expected command "plan"')
    files = [
        _parse_plan_entry_node(item, f"$.files[{index}]", registry, patch_limits)
        for index, item in enumerate(json_tagged_array(fields[3], "$.files"))
    ]
    return BatchPlanMessage(product_version, files)


def _plan_entry_node(entry: BatchPlanFileEntry, index: int) -> tuple:
    profile = tagged_null()
    if entry.profile is not None:
        profile = tagged_object(
            [
                ("id", tagged_string(entry.profile.id)),
                ("version", tagged_integer(entry.profile.version)),
            ]
        )
    source_digest = tagged_null()
    if entry.source_digest is not None:
        source_digest = _digest_node(entry.source_digest)
    operations = tagged_null()
    if entry.operations is not None:
        items = []
        for operation in entry.operations:
            summary_fields = [
                (name, tagged_string(value))
                for name, value in sorted(operation.summary.items())
            ]
            items.append(
                tagged_object(
                    [
                        (
                            "operation",
                            tagged_object(
                                [
                                    ("id", tagged_string(operation.operation.id)),
                                    ("version", tagged_integer(operation.operation.version)),
                                ]
                            ),
                        ),
                        ("summary", tagged_object(summary_fields)),
                    ]
                )
            )
        operations = tagged_array(items)
    source_patch = tagged_null()
    if entry.source_patch is not None:
        source_patch = _source_patch_node(entry.source_patch)
    failure_code = tagged_null()
    if entry.failure_code is not None:
        failure_code = tagged_string(entry.failure_code)
    diagnostics = tagged_null()
    if entry.diagnostics is not None:
        diagnostics = tagged_array([diagnostic_node(d) for d in entry.diagnostics])
    return tagged_object(
        [
            ("path", tagged_string(entry.path)),
            ("status", tagged_string(entry.status.value)),
            ("profile", profile),
            ("source_digest", source_digest),
            ("operations", operations),
            ("source_patch", source_patch),
            ("failure_code", failure_code),
            ("diagnostics", diagnostics),
        ]
    )


def _parse_plan_entry_node(
    node: tuple,
    path: str,
    registry: ErrorCodeRegistry,
    patch_limits: SourcePatchLimits | None = None,
) -> BatchPlanFileEntry:
    patch_limits = patch_limits or SourcePatchLimits()
    fields = json_record_fields(
        node,
        ["path", "status", "profile", "source_digest", "operations",
         "source_patch", "failure_code", "diagnostics"],
        path,
    )
    path_text = json_tagged_string(fields[0], f"{path}.path")
    status_name = json_tagged_string(fields[1], f"{path}.status")
    try:
        status = BatchPlanFileStatus(status_name)
    except ValueError:
        raise invalid(f"{path}.status", "unknown plan file status") from None
    profile = None
    source_digest = None
    operations = None
    source_patch = None
    failure_code = None
    diagnostics = None
    if status is BatchPlanFileStatus.PLANNED:
        profile = _parse_profile_node(fields[2], f"{path}.profile")
        source_digest = _parse_digest_node(fields[3], f"{path}.source_digest")
        operation_nodes = json_tagged_array(fields[4], f"{path}.operations")
        operations = [
            _parse_operation_summary_node(item, f"{path}.operations[{operation_index}]")
            for operation_index, item in enumerate(operation_nodes)
        ]
        source_patch = _parse_source_patch_node(
            fields[5], f"{path}.source_patch", patch_limits
        )
        if not json_is_tagged_null(fields[6]) or not json_is_tagged_null(fields[7]):
            raise invalid(path, "planned entries cannot carry failure_code or diagnostics")
    else:
        for field_index in (2, 3, 4, 5):
            if not json_is_tagged_null(fields[field_index]):
                raise invalid(path, "failed entries cannot carry planning facts")
        code = json_tagged_string(fields[6], f"{path}.failure_code")
        if not code:
            raise invalid(f"{path}.failure_code", "failure_code cannot be empty")
        failure_code = code
        diagnostic_nodes = json_tagged_array(fields[7], f"{path}.diagnostics")
        diagnostics = [
            parse_diagnostic_node(item, f"{path}.diagnostics[{diagnostic_index}]", registry)
            for diagnostic_index, item in enumerate(diagnostic_nodes)
        ]
    return BatchPlanFileEntry(
        path_text, status, profile, source_digest, operations,
        source_patch, failure_code, diagnostics, registry,
    )


def _digest_node(digest: ContentDigest) -> tuple:
    return tagged_object(
        [
            ("algorithm", tagged_string(digest.algorithm)),
            ("hex", tagged_string(digest.hex())),
        ]
    )


def _parse_digest_node(node: tuple, path: str) -> ContentDigest:
    fields = json_record_fields(node, ["algorithm", "hex"], path)
    algorithm = json_tagged_string(fields[0], f"{path}.algorithm")
    if algorithm != "sha256":
        raise invalid(path, "expected sha256")
    hex_text = json_tagged_string(fields[1], f"{path}.hex")
    if len(hex_text) != 64 or not _is_lowercase_hex(hex_text):
        raise invalid(path, "invalid lowercase sha256")
    return ContentDigest(bytes.fromhex(hex_text))


def _parse_profile_node(node: tuple, path: str) -> ProfileReference:
    fields = json_record_fields(node, ["id", "version"], path)
    id = json_tagged_string(fields[0], f"{path}.id")
    version = json_tagged_uint32(fields[1], f"{path}.version")
    return ProfileReference(id, version)


def _parse_operation_summary_node(node: tuple, path: str) -> EditOperationSummary:
    fields = json_record_fields(node, ["operation", "summary"], path)
    reference_fields = json_record_fields(fields[0], ["id", "version"], f"{path}.operation")
    id = json_tagged_string(reference_fields[0], f"{path}.operation.id")
    version = json_tagged_uint32(reference_fields[1], f"{path}.operation.version")
    summary = json_string_map(fields[1], f"{path}.summary")
    return EditOperationSummary(FormatOperationId(id, version), summary)


def _source_patch_node(patch: SourcePatch) -> tuple:
    replacements = []
    for replacement in patch.replacements:
        replacements.append(
            tagged_object(
                [
                    ("old_start", tagged_integer(replacement.old_start)),
                    ("old_end", tagged_integer(replacement.old_end)),
                    ("original", tagged_bytes(replacement.original)),
                    ("replacement", tagged_bytes(replacement.replacement)),
                    ("redact_original", tagged_boolean(replacement.redact_original)),
                    ("redact_replacement", tagged_boolean(replacement.redact_replacement)),
                ]
            )
        )
    metadata_fields = [
        (name, tagged_string(value)) for name, value in sorted(patch.metadata.items())
    ]
    return tagged_object(
        [
            ("schema", tagged_string("core.source-patch@2")),
            ("base_digest", _digest_node(patch.base_digest)),
            ("target_digest", _digest_node(patch.target_digest)),
            ("encoding", _encoding_facts_node(patch.encoding)),
            ("replacements", tagged_array(replacements)),
            ("metadata", tagged_object(metadata_fields)),
        ]
    )


def _parse_source_patch_node(
    node: tuple, path: str, patch_limits: SourcePatchLimits | None = None
) -> SourcePatch:
    patch_limits = patch_limits or SourcePatchLimits()
    fields = json_record_fields(
        node, ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"], path
    )
    schema = json_tagged_string(fields[0], f"{path}.schema")
    if schema != "core.source-patch@2":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, f"{path}.schema", "expected core.source-patch@2"
        )
    base_digest = _parse_digest_node(fields[1], f"{path}.base_digest")
    target_digest = _parse_digest_node(fields[2], f"{path}.target_digest")
    encoding = _parse_encoding_facts_node(fields[3], f"{path}.encoding")
    replacement_values = json_tagged_array(fields[4], f"{path}.replacements")
    if len(replacement_values) > patch_limits.max_replacements:
        raise resource(
            f"{path}.replacements", "replacement count exceeds configured limit"
        )
    patch_bytes = 0
    replacements = []
    for index, item in enumerate(replacement_values):
        replacement_path = f"{path}.replacements[{index}]"
        replacement_fields = json_record_fields(
            item,
            ["old_start", "old_end", "original", "replacement", "redact_original", "redact_replacement"],
            replacement_path,
        )
        old_start = json_tagged_uint64(replacement_fields[0], f"{replacement_path}.old_start")
        old_end = json_tagged_uint64(replacement_fields[1], f"{replacement_path}.old_end")
        original = json_tagged_bytes(replacement_fields[2], f"{replacement_path}.original")
        replacement = json_tagged_bytes(replacement_fields[3], f"{replacement_path}.replacement")
        redact_original = json_tagged_boolean(replacement_fields[4], f"{replacement_path}.redact_original")
        redact_replacement = json_tagged_boolean(replacement_fields[5], f"{replacement_path}.redact_replacement")
        patch_bytes += len(original) + len(replacement)
        if patch_bytes > patch_limits.max_patch_bytes:
            raise resource(
                f"{path}.replacements", "patch bytes exceed the configured limit"
            )
        replacements.append(
            SourceReplacement(
                old_start, old_end, original, replacement,
                redact_original, redact_replacement,
            )
        )
    metadata = json_string_map(fields[5], f"{path}.metadata")
    return SourcePatch(base_digest, target_digest, encoding, replacements, metadata)


def _encoding_facts_node(facts: EncodingFacts) -> tuple:
    profile_default = tagged_null()
    if facts.profile_default is not None:
        profile_default = _source_encoding_node(facts.profile_default)
    bom = tagged_null()
    if facts.bom is not None:
        bom = tagged_string(facts.bom)
    declaration = tagged_null()
    if facts.declaration is not None:
        declaration = _source_encoding_node(facts.declaration)
    caller_override = tagged_null()
    if facts.caller_override is not None:
        caller_override = _source_encoding_node(facts.caller_override)
    selected = tagged_null()
    if facts.selected is not None:
        selected = _source_encoding_node(facts.selected)
    return tagged_object(
        [
            ("profile_default", profile_default),
            ("bom_policy", tagged_string(facts.bom_policy)),
            ("bom", bom),
            ("declaration", declaration),
            ("caller_override", caller_override),
            ("selected", selected),
        ]
    )


def _parse_encoding_facts_node(node: tuple, path: str) -> EncodingFacts:
    fields = json_record_fields(
        node,
        ["profile_default", "bom_policy", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    # profile_default is a required core.source-encoding@1 record at the
    # value level; the tree codec mirrors that acceptance and rejects Null.
    profile_default = _parse_source_encoding_node(fields[0], f"{path}.profile_default")
    bom_policy = json_tagged_string(fields[1], f"{path}.bom_policy")
    bom = None
    if not json_is_tagged_null(fields[2]):
        bom = json_tagged_string(fields[2], f"{path}.bom")
    declaration = None
    if not json_is_tagged_null(fields[3]):
        declaration = _parse_source_encoding_node(fields[3], f"{path}.declaration")
    caller_override = None
    if not json_is_tagged_null(fields[4]):
        caller_override = _parse_source_encoding_node(fields[4], f"{path}.caller_override")
    selected = _parse_source_encoding_node(fields[5], f"{path}.selected")
    return EncodingFacts(
        profile_default, bom_policy, bom, declaration, caller_override, selected
    )


def _source_encoding_node(encoding: SourceEncoding) -> tuple:
    code_page = tagged_null()
    if encoding.windows_code_page is not None:
        code_page = tagged_integer(encoding.windows_code_page)
    return tagged_object(
        [
            ("schema", tagged_string("core.source-encoding@1")),
            ("kind", tagged_string(encoding.kind)),
            ("windows_code_page", code_page),
        ]
    )


def _parse_source_encoding_node(node: tuple, path: str) -> SourceEncoding:
    fields = json_record_fields(node, ["schema", "kind", "windows_code_page"], path)
    schema = json_tagged_string(fields[0], f"{path}.schema")
    if schema != "core.source-encoding@1":
        raise protocol_error(
            ProtocolErrorKind.SCHEMA_MISMATCH, f"{path}.schema", "expected core.source-encoding@1"
        )
    kind = json_tagged_string(fields[1], f"{path}.kind")
    code_page = None
    if not json_is_tagged_null(fields[2]):
        code_page = json_tagged_uint32(fields[2], f"{path}.windows_code_page")
    return SourceEncoding(kind, code_page)
