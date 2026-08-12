"""Suite ``consema.semantic-model-v6.conformance@1`` (semantic-model-v6.json,
25 cases): the v6 registry facts and frozen vector digests, the source-v2
records (encoding, snapshot, patch), the materialization v2 records, the Java
UTF-16 string record, the INI/Properties query-result records, and the v6
protocol envelope facts. Dispatch is by case id, mirroring
go/conformance/semantic_model_v6.go.

The v6 record codecs that the Python protocol package does not implement are
transcribed locally from go/protocol/records_source.go,
records_materialization.go, records_java_utf16.go, and records_line_query.go;
the source snapshot/patch machinery reuses ``consema.document.source`` and
``consema.document.source_patch``.
"""

from __future__ import annotations

import hashlib

from consema.conformance import compare
from consema.conformance import loader
from consema.conformance import protocol_records as records
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.document.source import (
    BomKind,
    BomPolicy,
    EncodingFacts,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
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
from consema.document.structural import LocationError
from consema.properties.java_string import JavaString, JavaStringStatus
from consema.protocol.contract import ContractId, ContractRegistry, ProtocolMessage
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.errors import (
    ProtocolError,
    ProtocolErrorKind,
    invalid,
    protocol_error,
    resource,
)
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import ProfileReference, RegistryManifest
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    integer_value,
    nullable_string,
    optional_string,
    schema_fields,
    sequence_of,
    string_of,
    unsigned32,
    unsigned64,
)
from consema.protocol.query import MatchRole, QueryDomain


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "registry.v6-manifest": _registry_manifest,
        "registry.v1-v5-frozen": _registry_frozen,
        "registry.v6-additive-contracts": _registry_contracts,
        "registry.v6-error-codes": _registry_errors,
        "source-encoding.mandatory-code-pages": _source_code_pages,
        "source-encoding.reject-unsupported": _source_reject_code_page,
        "source.bom-policy-distinct": _source_bom_policy,
        "source.snapshot-v2-code-page-boundaries": _source_boundaries,
        "source.snapshot-v2-reject-digest": _source_digest,
        "source.patch-v2-atomic-apply": _source_patch,
        "materialization.request-v2-roundtrip": _materialization_request,
        "materialization.result-v2-version-closure": _materialization_result,
        "java-utf16.edge-matrix": _java_matrix,
        "java-utf16.reject-noncanonical-unit": _java_rejection,
        "java-utf16.reject-byte-mismatch": _java_rejection,
        "ini-query.all-roles": _ini_roles,
        "properties-query.all-roles": _properties_roles,
        "line-query.reject-domain-role": _line_domain_rejection,
        "line-query.reject-ordinal-and-count": _line_ordinal_rejection,
        "line-query.reject-process-local": _line_process_local,
        "protocol.v1-v5-reject-v6-contracts": _protocol_old_rejection,
        "protocol.exact-version-dispatch": _protocol_version_dispatch,
        "protocol.v6-nested-error-code": _protocol_nested_error,
        "protocol.new-contract-canonical-bytes": _protocol_canonical_bytes,
        "protocol.new-payload-schema-and-limits": _protocol_schema_limits,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message="runner does not recognize published v6 case")
            )
            continue
        if handler is _registry_frozen:
            message = handler(conformance_runner, vector)
        else:
            message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _dual_roundtrip(schema: str, payload: PortableValue) -> str | None:
    contract_id, contract_version = schema.rsplit("@", 1)
    contract = ContractId(contract_id, int(contract_version))
    registry = ContractRegistry(6)
    envelope = ProtocolMessage(contract, payload, registry)
    limits = ProtocolLimits()
    json_bytes = envelope.to_json(limits)
    pvce_bytes = envelope.to_pvce(limits)
    decoded_json = ProtocolMessage.from_json(json_bytes, limits, registry)
    decoded_pvce = ProtocolMessage.from_pvce(pvce_bytes, limits, registry)
    if not core_equal(decoded_json.payload, envelope.payload):
        return "dual canonical transport did not close"
    if not core_equal(decoded_pvce.payload, envelope.payload):
        return "dual canonical transport did not close"
    return None


def _expect_code(vector: runner.Case, error: BaseException | None) -> str | None:
    if error is None:
        return "record must be rejected"
    if not isinstance(error, ProtocolError):
        return f"unexpected error type: {error!r}"
    expected = compare.string_field(vector.expected, "code")
    if error.code != expected:
        return f"rejection {error.code} != {expected}"
    expected_path = compare.string_field(vector.expected, "path")
    if expected_path is not None and error.path != expected_path:
        return f"rejection path {error.path} != {expected_path}"
    return None


def _code_page_encoding(number: int) -> SourceEncoding:
    page = WindowsCodePage.from_number(number)
    if page is None:
        raise ValueError(f"unsupported code page {number}")
    return SourceEncoding.windows_code_page(page)


def _code_page_snapshot(number: int, raw: bytes) -> SourceSnapshot:
    return SourceSnapshot.from_raw(
        raw,
        EncodingRequest.new(_code_page_encoding(number)).with_bom_policy(BomPolicy.TREAT_AS_CONTENT),
        SourceLimits(),
    )


def _replace_object_field(value: PortableValue, name: str, replacement: PortableValue) -> PortableValue:
    """Replaces one existing field or appends a new trailing field
    (Go replaceObjectField / appendObjectField)."""
    if value.kind is not Kind.OBJECT:
        raise ValueError("value must be Object")
    entries = []
    found = False
    for key, item in value.as_object():
        if key == name:
            entries.append((key, replacement))
            found = True
        else:
            entries.append((key, item))
    if not found:
        entries.append((name, replacement))
    return PortableValue.object(entries)


def _completion_success(processed: int, produced: int) -> PortableValue:
    return records.Completion.new(records.CompletionStatus.SUCCESS, processed, produced).to_value()


def _completion_produced(value: PortableValue) -> int:
    fields = exact_fields(
        value, ["schema", "status", "processed", "produced", "limit_name", "failure_code"], "$"
    )
    return unsigned64(fields[3], "$.produced")


# ---------------------------------------------------------------------------
# registry facts
# ---------------------------------------------------------------------------


def _registry_manifest(vector: runner.Case) -> str | None:
    manifest = RegistryManifest.build(6, ContractRegistry(6), ErrorCodeRegistry(6))
    value = manifest.to_value()
    decoded = RegistryManifest.from_value(value)
    if not core_equal(decoded.to_value(), value):
        return "manifest round-trip changed the record"
    semantic_model = compare.string_field(vector.expected, "semantic_model")
    contract_count = compare.integer_field(vector.expected, "contract_count")
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    if (
        decoded.semantic_model.schema() != semantic_model
        or len(decoded.contracts) != contract_count
        or len(decoded.error_codes) != error_code_count
    ):
        return "v6 manifest facts differ"
    return None


def _registry_frozen(conformance_runner: runner.Runner, vector: runner.Case) -> str | None:
    contract_counts = compare.integer_sequence(vector.expected, "contract_counts")
    error_counts = compare.integer_sequence(vector.expected, "error_code_counts")
    if contract_counts is None or error_counts is None:
        return "unexpected expectation facts"
    if len(contract_counts) != 5 or len(error_counts) != 5:
        return "unexpected expectation facts"
    for index in range(5):
        version = index + 1
        manifest = RegistryManifest.build(
            version, ContractRegistry(version), ErrorCodeRegistry(version)
        )
        value = manifest.to_value()
        decoded = RegistryManifest.from_value(value)
        decoded.to_value()
        if (
            len(manifest.contracts) != contract_counts[index]
            or len(manifest.error_codes) != error_counts[index]
        ):
            return "a frozen registry changed"
    previous = compare.sequence_field(vector.input, "previous_vectors")
    if previous is None:
        return "missing input.previous_vectors"
    expected_files = [
        ("semantic-model-v5", "semantic-model-v5.json"),
        ("protocol-v2", "protocol-v2.json"),
        ("source-v1", "source-v1.json"),
    ]
    if len(previous) != len(expected_files):
        return "previous vector count differed"
    for index, item in enumerate(previous):
        name = compare.string_field(item, "name")
        recorded = compare.string_field(item, "sha256")
        expected_name, file_name = expected_files[index]
        data = loader.read_vector_file(conformance_runner.vectors_dir, file_name)
        digest = hashlib.sha256(data).hexdigest()
        if name != expected_name or digest != recorded:
            return "a frozen vector changed"
    return None


def _registry_contracts(vector: runner.Case) -> str | None:
    old = ContractRegistry(5)
    current = ContractRegistry(6)
    expected = compare.string_sequence(vector.expected, "contracts")
    if expected is None:
        return "missing expected.contracts"
    for schema in expected:
        contract_id, version_text = schema.rsplit("@", 1)
        contract = ContractId(contract_id, int(version_text))
        if old.recognizes(contract) or not current.recognizes(contract):
            return "v6 contract additions differ"
    if len(current.contracts()) != len(old.contracts()) + len(expected):
        return "v6 contract additions differ"
    return None


def _registry_errors(vector: runner.Case) -> str | None:
    old = ErrorCodeRegistry(5)
    current = ErrorCodeRegistry(6)
    expected = compare.string_sequence(vector.expected, "new_codes")
    if expected is None:
        return "missing expected.new_codes"
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    if len(current.codes()) != error_code_count or len(expected) != 34:
        return "v6 error-code additions differ"
    for code in expected:
        if old.contains(code):
            return "v6 error-code additions differ"
        descriptor = current.descriptor(code)
        if descriptor is None or descriptor.introduced != "0.8.0" or not descriptor.description:
            return "v6 error-code additions differ"
    return None


# ---------------------------------------------------------------------------
# source encoding record codecs (records_source.go)
# ---------------------------------------------------------------------------


def _wire_kind(encoding: SourceEncoding) -> str:
    """The Go/Rust wire kind spelling of one document encoding."""
    return {
        SourceEncodingKind.BINARY: "Binary",
        SourceEncodingKind.UTF8: "Utf8",
        SourceEncodingKind.UTF16LE: "Utf16Le",
        SourceEncodingKind.UTF16BE: "Utf16Be",
        SourceEncodingKind.LATIN1: "Latin1",
        SourceEncodingKind.WINDOWS_CODE_PAGE: "WindowsCodePage",
    }[encoding.kind]


def _parse_wire_kind(text: str, path: str) -> SourceEncodingKind:
    kinds = {
        "Binary": SourceEncodingKind.BINARY,
        "Utf8": SourceEncodingKind.UTF8,
        "Utf16Le": SourceEncodingKind.UTF16LE,
        "Utf16Be": SourceEncodingKind.UTF16BE,
        "Latin1": SourceEncodingKind.LATIN1,
        "WindowsCodePage": SourceEncodingKind.WINDOWS_CODE_PAGE,
    }
    kind = kinds.get(text)
    if kind is None:
        raise invalid(path, "unknown source encoding kind")
    return kind


def _source_encoding_value(encoding: SourceEncoding) -> PortableValue:
    code_page: PortableValue = PortableValue.null()
    if encoding.code_page is not None:
        code_page = PortableValue.integer(encoding.code_page.number)
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-encoding@1")),
            ("kind", PortableValue.string(_wire_kind(encoding))),
            ("windows_code_page", code_page),
        ]
    )


def _parse_source_encoding_value(value: PortableValue, path: str) -> SourceEncoding:
    fields = schema_fields(value, "core.source-encoding@1", ["schema", "kind", "windows_code_page"], path)
    kind = _parse_wire_kind(string_of(fields[1], path + ".kind"), path + ".kind")
    code_page = None
    if fields[2].kind is not Kind.NULL:
        page = unsigned32(fields[2], path + ".windows_code_page")
        resolved = WindowsCodePage.from_number(page)
        if resolved is None:
            raise invalid(path + ".windows_code_page", "unsupported Windows code page")
        code_page = resolved
    if kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
        if code_page is None:
            raise invalid(path + ".windows_code_page", "Windows code page requires a number")
        return SourceEncoding(kind=kind, code_page=code_page)
    if code_page is not None:
        raise invalid(path + ".windows_code_page", "non-Windows encoding requires null")
    return SourceEncoding(kind=kind)


def _encoding_facts_value(facts: EncodingFacts) -> PortableValue:
    bom = PortableValue.null()
    if facts.bom is not None:
        bom = PortableValue.string(facts.bom.value)
    declaration = PortableValue.null()
    if facts.declaration is not None:
        declaration = _source_encoding_value(facts.declaration)
    caller_override = PortableValue.null()
    if facts.caller_override is not None:
        caller_override = _source_encoding_value(facts.caller_override)
    return PortableValue.object(
        [
            ("profile_default", _source_encoding_value(facts.profile_default)),
            ("bom_policy", PortableValue.string(facts.bom_policy.value)),
            ("bom", bom),
            ("declaration", declaration),
            ("caller_override", caller_override),
            ("selected", _source_encoding_value(facts.selected)),
        ]
    )


def _parse_encoding_facts_value(value: PortableValue, path: str) -> EncodingFacts:
    fields = exact_fields(
        value,
        ["profile_default", "bom_policy", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    profile_default = _parse_source_encoding_value(fields[0], path + ".profile_default")
    policy_text = string_of(fields[1], path + ".bom_policy")
    try:
        policy = BomPolicy(policy_text)
    except ValueError:
        raise invalid(path + ".bom_policy", "unknown BOM policy") from None
    bom = None
    if fields[2].kind is not Kind.NULL:
        bom_text = string_of(fields[2], path + ".bom")
        try:
            bom = BomKind(bom_text)
        except ValueError:
            raise invalid(path + ".bom", "unknown BOM ID") from None
    declaration = None
    if fields[3].kind is not Kind.NULL:
        declaration = _parse_source_encoding_value(fields[3], path + ".declaration")
    caller_override = None
    if fields[4].kind is not Kind.NULL:
        caller_override = _parse_source_encoding_value(fields[4], path + ".caller_override")
    selected = _parse_source_encoding_value(fields[5], path + ".selected")
    try:
        return EncodingFacts.from_claim_with_bom_policy(
            profile_default, policy, bom, declaration, caller_override, selected
        )
    except SourceError as error:
        raise invalid(path, str(error)) from None


def _digest_value(digest) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string("sha256")),
            ("hex", PortableValue.string(digest.hex)),
        ]
    )


def _parse_digest(value: PortableValue, path: str):
    from consema.document.ids import ContentDigest

    fields = exact_fields(value, ["algorithm", "hex"], path)
    algorithm = string_of(fields[0], path + ".algorithm")
    if algorithm != "sha256":
        raise invalid(path, "expected sha256")
    hex_text = string_of(fields[1], path + ".hex")
    if len(hex_text) != 64 or any(
        character not in "0123456789abcdef" for character in hex_text
    ):
        raise invalid(path, "invalid lowercase sha256")
    return ContentDigest(bytes.fromhex(hex_text))


# ---------------------------------------------------------------------------
# core.source-snapshot@2 codec (records_source.go)
# ---------------------------------------------------------------------------


def _source_snapshot_v2_value(snapshot: SourceSnapshot) -> PortableValue:
    status = "NotText" if snapshot.decoded_text() is None else "Available"
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-snapshot@2")),
            ("raw_bytes", PortableValue.bytes_value(snapshot.bytes())),
            ("digest", _digest_value(snapshot.digest())),
            ("encoding", _encoding_facts_value(snapshot.encoding)),
            ("decoded_status", PortableValue.string(status)),
        ]
    )


def _source_snapshot_v2_from_value(value: PortableValue, limits: SourceLimits) -> SourceSnapshot:
    fields = schema_fields(
        value,
        "core.source-snapshot@2",
        ["schema", "raw_bytes", "digest", "encoding", "decoded_status"],
        "$",
    )
    raw_value = fields[1]
    if raw_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.raw_bytes", "expected Bytes")
    claimed_digest = _parse_digest(fields[2], "$.digest")
    claimed_facts = _parse_encoding_facts_value(fields[3], "$.encoding")
    decoded_status = string_of(fields[4], "$.decoded_status")
    if decoded_status not in ("Available", "NotText"):
        raise invalid("$.decoded_status", "expected Available or NotText")
    request = EncodingRequest.new(claimed_facts.profile_default).with_bom_policy(
        claimed_facts.bom_policy
    )
    if claimed_facts.declaration is not None:
        request = request.with_declaration(claimed_facts.declaration)
    if claimed_facts.caller_override is not None:
        request = request.with_caller_override(claimed_facts.caller_override)
    try:
        snapshot = SourceSnapshot.from_raw(raw_value.as_bytes(), request, limits)
    except SourceError as error:
        raise invalid("$.raw_bytes", str(error)) from None
    if snapshot.digest() != claimed_digest:
        raise invalid("$.digest", "digest does not match raw_bytes")
    if snapshot.encoding != claimed_facts:
        raise invalid("$.encoding", "encoding facts do not match raw_bytes resolution")
    actual_status = "NotText" if snapshot.decoded_text() is None else "Available"
    if decoded_status != actual_status:
        raise invalid("$.decoded_status", "decoded status contradicts selected encoding")
    return snapshot


def _source_snapshot_v1_value(snapshot: SourceSnapshot) -> PortableValue:
    """The v1 snapshot encoding-facts form (encodingValueV1) used to forge the
    version-closure outcome."""
    facts = snapshot.encoding

    def name(encoding: SourceEncoding | None) -> str:
        if encoding is None:
            return ""
        return _wire_kind(encoding)

    bom: PortableValue = PortableValue.null()
    if facts.bom is not None:
        bom = PortableValue.string(facts.bom.value)
    declaration: PortableValue = PortableValue.null()
    if facts.declaration is not None:
        declaration = PortableValue.string(name(facts.declaration))
    caller_override: PortableValue = PortableValue.null()
    if facts.caller_override is not None:
        caller_override = PortableValue.string(name(facts.caller_override))
    status = "NotText" if snapshot.decoded_text() is None else "Available"
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-snapshot@1")),
            ("raw_bytes", PortableValue.bytes_value(snapshot.bytes())),
            ("digest", _digest_value(snapshot.digest())),
            (
                "encoding",
                PortableValue.object(
                    [
                        ("profile_default", PortableValue.string(name(facts.profile_default))),
                        ("bom", bom),
                        ("declaration", declaration),
                        ("caller_override", caller_override),
                        ("selected", PortableValue.string(name(facts.selected))),
                    ]
                ),
            ),
            ("decoded_status", PortableValue.string(status)),
        ]
    )


# ---------------------------------------------------------------------------
# core.source-patch@2 codec (records_source.go)
# ---------------------------------------------------------------------------


def _source_patch_v2_value(patch: SourcePatch) -> PortableValue:
    replacements = []
    for replacement in patch.replacements:
        replacements.append(
            PortableValue.object(
                [
                    ("old_start", PortableValue.integer(replacement.old_start)),
                    ("old_end", PortableValue.integer(replacement.old_end)),
                    ("original", PortableValue.bytes_value(replacement.original)),
                    ("replacement", PortableValue.bytes_value(replacement.replacement)),
                    ("redact_original", PortableValue.boolean(replacement.redact_original)),
                    ("redact_replacement", PortableValue.boolean(replacement.redact_replacement)),
                ]
            )
        )
    metadata = PortableValue.object(
        tuple((key, PortableValue.string(patch.metadata[key])) for key in sorted(patch.metadata))
    )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-patch@2")),
            ("base_digest", _digest_value(patch.base_digest)),
            ("target_digest", _digest_value(patch.target_digest)),
            ("encoding", _encoding_facts_value(patch.encoding)),
            ("replacements", PortableValue.sequence(tuple(replacements))),
            ("metadata", metadata),
        ]
    )


def _source_patch_v2_from_value(value: PortableValue, limits: SourcePatchLimits) -> SourcePatch:
    fields = schema_fields(
        value,
        "core.source-patch@2",
        ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"],
        "$",
    )
    base_digest = _parse_digest(fields[1], "$.base_digest")
    target_digest = _parse_digest(fields[2], "$.target_digest")
    facts = _parse_encoding_facts_value(fields[3], "$.encoding")
    replacement_values = sequence_of(fields[4], "$.replacements")
    if len(replacement_values) > limits.max_replacements:
        raise resource("$.replacements", "replacement count exceeds configured limit")
    replacements = []
    for index, replacement_value in enumerate(replacement_values):
        path = f"$.replacements[{index}]"
        replacement_fields = exact_fields(
            replacement_value,
            ["old_start", "old_end", "original", "replacement",
             "redact_original", "redact_replacement"],
            path,
        )
        old_start = unsigned64(replacement_fields[0], path + ".old_start")
        old_end = unsigned64(replacement_fields[1], path + ".old_end")
        if replacement_fields[2].kind is not Kind.BYTES:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path + ".original", "expected Bytes")
        if replacement_fields[3].kind is not Kind.BYTES:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path + ".replacement", "expected Bytes")
        replacements.append(
            SourceReplacement(
                old_start,
                old_end,
                replacement_fields[2].as_bytes(),
                replacement_fields[3].as_bytes(),
                boolean_of(replacement_fields[4], path + ".redact_original"),
                boolean_of(replacement_fields[5], path + ".redact_replacement"),
            )
        )
    metadata = {}
    if fields[5].kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.metadata", "expected Object<String, String>")
    for key, item in fields[5].as_object():
        metadata[key] = string_of(item, "$.metadata." + key)
    return SourcePatch.new(
        base_digest, target_digest, facts, replacements, metadata, limits
    )


# ---------------------------------------------------------------------------
# source cases
# ---------------------------------------------------------------------------


def _source_code_pages(vector: runner.Case) -> str | None:
    pages = compare.integer_sequence(vector.input, "code_pages")
    if pages is None:
        return "missing input.code_pages"
    accepted = 0
    for page in pages:
        encoding = _code_page_encoding(page)
        value = _source_encoding_value(encoding)
        try:
            decoded = _parse_source_encoding_value(value, "$")
        except ProtocolError as error:
            return f"published code page rejected: {error}"
        if (
            decoded.kind is SourceEncodingKind.WINDOWS_CODE_PAGE
            and decoded.code_page is not None
            and decoded.code_page.number == page
        ):
            accepted += 1
    accepted_count = compare.integer_field(vector.expected, "accepted_count")
    if accepted != accepted_count:
        return f"mandatory code-page count {accepted} != {accepted_count}"
    return None


def _source_reject_code_page(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    if page is None:
        return "missing input.code_page"
    value = PortableValue.object(
        [
            ("schema", PortableValue.string("core.source-encoding@1")),
            ("kind", PortableValue.string("WindowsCodePage")),
            ("windows_code_page", PortableValue.integer(page)),
        ]
    )
    try:
        _parse_source_encoding_value(value, "$")
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "record must be rejected"


def _source_bom_policy(vector: runner.Case) -> str | None:
    hex_text = compare.string_field(vector.input, "hex")
    if hex_text is None:
        return "missing input.hex"
    raw = bytes.fromhex(hex_text)
    latin1 = SourceEncoding.latin1()
    detected = SourceSnapshot.from_raw(raw, EncodingRequest.new(latin1), SourceLimits())
    content = SourceSnapshot.from_raw(
        raw, EncodingRequest.new(latin1).with_bom_policy(BomPolicy.TREAT_AS_CONTENT), SourceLimits()
    )
    message = _dual_roundtrip("core.source-snapshot@2", _source_snapshot_v2_value(detected))
    if message:
        return message
    message = _dual_roundtrip("core.source-snapshot@2", _source_snapshot_v2_value(content))
    if message:
        return message
    detect_text = compare.string_field(vector.expected, "detect_text")
    content_text = compare.string_field(vector.expected, "content_text")
    detected_text = detected.decoded_text()
    content_text_value = content.decoded_text()
    if detected_text is None or content_text_value is None:
        return "BOM policies must decode text"
    if detected_text != detect_text or content_text_value != content_text:
        return "BOM policies did not remain distinct"
    if (
        detected.encoding.bom_policy is not BomPolicy.DETECT_UNICODE
        or content.encoding.bom_policy is not BomPolicy.TREAT_AS_CONTENT
    ):
        return "BOM policies did not remain distinct"
    return None


def _decoded_position_resolves(snapshot: SourceSnapshot, raw_byte: int) -> bool:
    """Whether one raw offset is a decoded scalar boundary (source.rs:623-641).

    The Go decoder resolves the terminal boundary (``raw_byte == len``) when
    the final scalar ends at the source end, while the Python document layer
    rejects offsets at or beyond the end (document/source.py:632). The
    terminal boundary is resolved runner-side to mirror the Go behavior.
    """
    if raw_byte > len(snapshot.bytes()):
        return False
    if raw_byte == 0:
        return True
    try:
        snapshot.decoded_position(raw_byte)
        return True
    except LocationError:
        return raw_byte == len(snapshot.bytes())


def _source_boundaries(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    hex_text = compare.string_field(vector.input, "hex")
    if page is None or hex_text is None:
        return "missing input.code_page/hex"
    snapshot = _code_page_snapshot(page, bytes.fromhex(hex_text))
    payload = _source_snapshot_v2_value(snapshot)
    try:
        decoded = _source_snapshot_v2_from_value(payload, SourceLimits())
    except ProtocolError as error:
        return f"snapshot decode failed: {error}"
    text = compare.string_field(vector.expected, "text")
    boundaries = compare.integer_sequence(vector.expected, "raw_boundaries")
    invalid_boundary = compare.integer_field(vector.expected, "invalid_raw_boundary")
    if text is None or boundaries is None or invalid_boundary is None:
        return "missing expected facts"
    decoded_text = decoded.decoded_text()
    if decoded_text is None:
        return "snapshot must decode text"
    if decoded_text != text:
        return f"decoded text {decoded_text!r} != {text!r}"
    for boundary in boundaries:
        if not _decoded_position_resolves(decoded, boundary):
            return f"boundary {boundary} must resolve"
    if _decoded_position_resolves(decoded, invalid_boundary):
        return f"invalid raw boundary {invalid_boundary} must fail"
    from consema.document.source import DecodedOffset

    raw_byte = decoded.raw_byte_at(DecodedOffset.unicode_scalar(1))
    if raw_byte != 2:
        return f"raw byte at scalar 1 must be 2, got {raw_byte}"
    return None


def _source_digest(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    hex_text = compare.string_field(vector.input, "hex")
    if page is None or hex_text is None:
        return "missing input.code_page/hex"
    snapshot = _code_page_snapshot(page, bytes.fromhex(hex_text))
    encoded = _source_snapshot_v2_value(snapshot)
    digest_value = compare.object_field(encoded, "digest")
    if digest_value is None:
        return "digest field missing"
    forged_digest = _replace_object_field(
        digest_value, "hex", PortableValue.string("0" * 64)
    )
    forged = _replace_object_field(encoded, "digest", forged_digest)
    try:
        _source_snapshot_v2_from_value(forged, SourceLimits())
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "forged digest must be rejected"


def _source_patch(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    base_hex = compare.string_field(vector.input, "base_hex")
    start = compare.integer_field(vector.input, "start")
    end = compare.integer_field(vector.input, "end")
    replacement_hex = compare.string_field(vector.input, "replacement_hex")
    if page is None or base_hex is None or start is None or end is None or replacement_hex is None:
        return "missing input facts"
    base_bytes = bytes.fromhex(base_hex)
    base = _code_page_snapshot(page, base_bytes)
    if start > len(base_bytes) or end > len(base_bytes) or start > end:
        return "replacement range out of bounds"
    patch = SourcePatch.create(
        base,
        [SourceReplacement.new(start, end, base_bytes[start:end], bytes.fromhex(replacement_hex))],
        {},
        SourcePatchLimits(),
    )
    wire = _source_patch_v2_value(patch)
    try:
        decoded = _source_patch_v2_from_value(wire, SourcePatchLimits())
    except ProtocolError as error:
        return f"patch decode failed: {error}"
    try:
        target = decoded.apply(base, SourcePatchLimits())
    except SourcePatchError as error:
        return f"patch apply failed: {error}"
    target_hex = compare.string_field(vector.expected, "target_hex")
    wrong_base_code = compare.string_field(vector.expected, "wrong_base_code")
    if target_hex is None or wrong_base_code is None:
        return "missing expected facts"
    if target.bytes().hex() != target_hex:
        return f"target hex {target.bytes().hex()} != {target_hex}"
    wrong = _code_page_snapshot(page, b"wrong")
    try:
        decoded.apply(wrong, SourcePatchLimits())
    except SourcePatchError as error:
        if error.code != wrong_base_code:
            return f"wrong-base code {error.code} != {wrong_base_code}"
    else:
        return "wrong base must be rejected"
    return None


# ---------------------------------------------------------------------------
# materialization v2 records (records_materialization.go)
# ---------------------------------------------------------------------------

_DEFAULT_MATERIALIZATION_LIMITS = {
    "max_input_nodes": 1_000_000,
    "max_output_bytes": 64 << 20,
    "max_depth": 256,
    "max_report_entries": 100_000,
    "max_provenance_entries": 2_000_000,
}


def _profile_reference_value(profile: ProfileReference) -> PortableValue:
    return PortableValue.object(
        [
            ("id", PortableValue.string(profile.id)),
            ("version", PortableValue.integer(profile.version)),
        ]
    )


def _parse_profile_reference(value: PortableValue, path: str) -> ProfileReference:
    fields = exact_fields(value, ["id", "version"], path)
    identifier = string_of(fields[0], path + ".id")
    version = unsigned32(fields[1], path + ".version")
    return ProfileReference(identifier, version)


def _materialization_limits_value(limits: dict) -> PortableValue:
    return PortableValue.object(
        [
            ("max_input_nodes", PortableValue.integer(limits["max_input_nodes"])),
            ("max_output_bytes", PortableValue.integer(limits["max_output_bytes"])),
            ("max_depth", PortableValue.integer(limits["max_depth"])),
            ("max_report_entries", PortableValue.integer(limits["max_report_entries"])),
            ("max_provenance_entries", PortableValue.integer(limits["max_provenance_entries"])),
        ]
    )


def _parse_materialization_limits(value: PortableValue, path: str) -> dict:
    fields = exact_fields(
        value,
        ["max_input_nodes", "max_output_bytes", "max_depth",
         "max_report_entries", "max_provenance_entries"],
        path,
    )
    return {
        "max_input_nodes": unsigned64(fields[0], path + ".max_input_nodes"),
        "max_output_bytes": unsigned64(fields[1], path + ".max_output_bytes"),
        "max_depth": unsigned64(fields[2], path + ".max_depth"),
        "max_report_entries": unsigned64(fields[3], path + ".max_report_entries"),
        "max_provenance_entries": unsigned64(fields[4], path + ".max_provenance_entries"),
    }


def _materialization_request_v2_value(
    profile: ProfileReference,
    style_id: str,
    style_version: int,
    encoding: SourceEncoding,
    newline: str,
    mapping_policy: str,
    representability: str,
    limits: dict | None = None,
) -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-request@2")),
            ("target_profile", _profile_reference_value(profile)),
            (
                "style",
                PortableValue.object(
                    [("id", PortableValue.string(style_id)), ("version", PortableValue.integer(style_version))]
                ),
            ),
            ("encoding", _source_encoding_value(encoding)),
            ("newline", PortableValue.string(newline)),
            ("mapping_policy", PortableValue.string(mapping_policy)),
            ("representability", PortableValue.string(representability)),
            ("limits", _materialization_limits_value(limits or _DEFAULT_MATERIALIZATION_LIMITS)),
        ]
    )


def _materialization_request_v2_from_value(value: PortableValue) -> dict:
    fields = schema_fields(
        value,
        "core.materialization-request@2",
        ["schema", "target_profile", "style", "encoding", "newline",
         "mapping_policy", "representability", "limits"],
        "$",
    )
    profile = _parse_profile_reference(fields[1], "$.target_profile")
    style_fields = exact_fields(fields[2], ["id", "version"], "$.style")
    style_id = string_of(style_fields[0], "$.style.id")
    style_version = unsigned32(style_fields[1], "$.style.version")
    encoding = _parse_source_encoding_value(fields[3], "$.encoding")
    newline = string_of(fields[4], "$.newline")
    if newline not in ("None", "Lf", "CrLf"):
        raise invalid("$.newline", "unknown newline policy")
    mapping_policy = string_of(fields[5], "$.mapping_policy")
    if mapping_policy not in ("RequireObject", "UniqueStringEntriesToObject"):
        raise invalid("$.mapping_policy", "unknown mapping policy")
    representability = string_of(fields[6], "$.representability")
    if representability != "ExactOnly":
        raise invalid("$.representability", "requires ExactOnly")
    limits = _parse_materialization_limits(fields[7], "$.limits")
    return {
        "target_profile": profile,
        "style_id": style_id,
        "style_version": style_version,
        "encoding": encoding,
        "newline": newline,
        "mapping_policy": mapping_policy,
        "representability": representability,
        "limits": limits,
    }


def _materialization_request_v1_from_value(value: PortableValue) -> None:
    """The v1 decoder: encoding is a lowercase string, so a disguised v2
    payload fails with wrong-type at $.encoding (materialization.rs:59-66)."""
    fields = schema_fields(
        value,
        "core.materialization-request@1",
        ["schema", "target_profile", "style", "encoding", "newline",
         "mapping_policy", "representability", "limits"],
        "$",
    )
    _parse_profile_reference(fields[1], "$.target_profile")
    style_fields = exact_fields(fields[2], ["id", "version"], "$.style")
    string_of(style_fields[0], "$.style.id")
    unsigned32(style_fields[1], "$.style.version")
    text = string_of(fields[3], "$.encoding")
    if text not in ("binary", "utf-8", "utf-16le", "utf-16be", "latin-1"):
        raise invalid("$.encoding", "unknown source encoding")


def _materialization_report_value() -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-report@1")),
            ("events", PortableValue.sequence(())),
        ]
    )


def _materialization_report_from_value(value: PortableValue, registry: ErrorCodeRegistry) -> PortableValue:
    fields = schema_fields(
        value, "core.materialization-report@1", ["schema", "events"], "$"
    )
    sequence_of(fields[1], "$.events")
    return value


def _materialization_provenance_value() -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-provenance-map@1")),
            ("entries", PortableValue.sequence(())),
        ]
    )


def _materialization_provenance_from_value(value: PortableValue) -> PortableValue:
    fields = schema_fields(
        value, "core.materialization-provenance-map@1", ["schema", "entries"], "$"
    )
    sequence_of(fields[1], "$.entries")
    return value


def _materialization_result_v2_value(
    profile: ProfileReference, target_source_id: str, snapshot: SourceSnapshot, fidelity: str
) -> PortableValue:
    if not target_source_id or len(target_source_id) > 4096:
        raise invalid("$.outcome.target_source_id", "invalid target source ID")
    if fidelity not in ("Exact", "Transformed", "Lossy"):
        raise invalid("$.outcome.fidelity", "unknown materialization fidelity")
    outcome = PortableValue.object(
        [
            ("kind", PortableValue.string("Complete")),
            ("target_source_id", PortableValue.string(target_source_id)),
            ("snapshot", _source_snapshot_v2_value(snapshot)),
            ("fidelity", PortableValue.string(fidelity)),
            ("report", _materialization_report_value()),
            ("provenance", _materialization_provenance_value()),
        ]
    )
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.materialization-result@2")),
            ("target_profile", _profile_reference_value(profile)),
            ("outcome", outcome),
        ]
    )


def _materialization_result_v2_from_value(
    value: PortableValue, registry: ErrorCodeRegistry
) -> PortableValue:
    fields = schema_fields(
        value, "core.materialization-result@2", ["schema", "target_profile", "outcome"], "$"
    )
    profile = _parse_profile_reference(fields[1], "$.target_profile")
    outcome = fields[2]
    if outcome.kind is not Kind.OBJECT:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.outcome", "expected Object")
    kind_value = compare.object_field(outcome, "kind")
    if kind_value is None:
        raise invalid("$.outcome", "missing kind")
    kind = string_of(kind_value, "$.outcome.kind")
    if kind == "Complete":
        complete = exact_fields(
            outcome,
            ["kind", "target_source_id", "snapshot", "fidelity", "report", "provenance"],
            "$.outcome",
        )
        target_source_id = string_of(complete[1], "$.outcome.target_source_id")
        snapshot = _source_snapshot_v2_from_value(complete[2], SourceLimits())
        fidelity = string_of(complete[3], "$.outcome.fidelity")
        if fidelity not in ("Exact", "Transformed", "Lossy"):
            raise invalid("$.outcome.fidelity", "unknown materialization fidelity")
        report = _materialization_report_from_value(complete[4], registry)
        provenance = _materialization_provenance_from_value(complete[5])
        return _materialization_result_v2_value(profile, target_source_id, snapshot, fidelity)
    if kind == "Failed":
        raise invalid("$.outcome.kind", "failed outcomes land with the source milestone")
    raise invalid("$.outcome.kind", "unknown materialization outcome")


def _materialization_request(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    profile_name = compare.string_field(vector.input, "profile")
    style_name = compare.string_field(vector.input, "style")
    if page is None or profile_name is None or style_name is None:
        return "missing input facts"
    encoding = _code_page_encoding(page)
    profile = ProfileReference(profile_name, 1)
    value = _materialization_request_v2_value(
        profile, style_name, 1, encoding, "CrLf", "RequireObject", "ExactOnly"
    )
    try:
        decoded = _materialization_request_v2_from_value(value)
    except ProtocolError as error:
        return f"materialization request v2 decode failed: {error}"
    roundtrip = _materialization_request_v2_value(
        decoded["target_profile"],
        decoded["style_id"],
        decoded["style_version"],
        decoded["encoding"],
        decoded["newline"],
        decoded["mapping_policy"],
        decoded["representability"],
        decoded["limits"],
    )
    if not core_equal(roundtrip, value):
        return "materialization request v2 differed"
    encoding_value = compare.object_field(value, "encoding")
    if encoding_value is None:
        return "encoding field missing"
    kind = compare.string_field(encoding_value, "kind")
    encoding_kind = compare.string_field(vector.expected, "encoding_kind")
    if kind != encoding_kind:
        return f"encoding kind {kind} != {encoding_kind}"
    return None


def _materialization_result(vector: runner.Case) -> str | None:
    page = compare.integer_field(vector.input, "code_page")
    hex_text = compare.string_field(vector.input, "hex")
    if page is None or hex_text is None:
        return "missing input.code_page/hex"
    snapshot = _code_page_snapshot(page, bytes.fromhex(hex_text))
    profile = ProfileReference("ini.windows", 1)
    payload = _materialization_result_v2_value(profile, "target:ini", snapshot, "Exact")
    message = _dual_roundtrip("core.materialization-result@2", payload)
    if message:
        return message
    utf8_snapshot = SourceSnapshot.from_utf8(b"k=v")
    portable_profile = ProfileReference("ini.portable", 1)
    v2_payload = _materialization_result_v2_value(portable_profile, "target:ini", utf8_snapshot, "Exact")
    v1_value = _source_snapshot_v1_value(utf8_snapshot)
    outcome_value = compare.object_field(v2_payload, "outcome")
    if outcome_value is None:
        return "outcome field missing"
    forged_outcome = _replace_object_field(outcome_value, "snapshot", v1_value)
    mixed = _replace_object_field(v2_payload, "outcome", forged_outcome)
    mixed_version_code = compare.string_field(vector.expected, "mixed_version_code")
    try:
        _materialization_result_v2_from_value(mixed, ErrorCodeRegistry(6))
    except ProtocolError as error:
        if error.code != mixed_version_code:
            return f"mixed version error {error.code} != {mixed_version_code}"
    else:
        return "mixed version must be rejected"
    return None


# ---------------------------------------------------------------------------
# core.java-utf16-string@1 codec (records_java_utf16.go)
# ---------------------------------------------------------------------------


def _java_status_spelling(status: JavaStringStatus) -> str:
    return status.value


def _java_utf16_value(code_units: list[int], limits: ProtocolLimits) -> PortableValue:
    if len(code_units) > limits.max_container_entries:
        raise resource("$.code_units", "code-unit count exceeds the configured container limit")
    byte_len = len(code_units) * 2
    if byte_len > limits.max_blob_bytes:
        raise resource("$.bytes", "UTF-16 bytes exceed the configured blob limit")
    byte_values = bytearray()
    units = []
    for unit in code_units:
        byte_values.extend((unit >> 8, unit & 0xFF))
        units.append(PortableValue.string(f"{unit:04X}"))
    status = JavaString.from_code_units(code_units).status()
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.java-utf16-string@1")),
            ("encoding", PortableValue.string("UTF16BE/1")),
            ("code_units", PortableValue.sequence(tuple(units))),
            ("bytes", PortableValue.bytes_value(bytes(byte_values))),
            ("unicode_status", PortableValue.string(status.value)),
        ]
    )


def _parse_java_unit(text: str) -> int | None:
    if len(text) != 4:
        return None
    for character in text:
        if not ("0" <= character <= "9") and not ("A" <= character <= "F"):
            return None
    return int(text, 16)


def _java_utf16_from_value(value: PortableValue, limits: ProtocolLimits) -> PortableValue:
    """Strictly decodes and canonically re-verifies one exact Java string
    (java_utf16.rs:84-146)."""
    fields = schema_fields(
        value,
        "core.java-utf16-string@1",
        ["schema", "encoding", "code_units", "bytes", "unicode_status"],
        "$",
    )
    encoding = string_of(fields[1], "$.encoding")
    if encoding != "UTF16BE/1":
        raise invalid("$.encoding", "expected exact encoding UTF16BE/1")
    unit_values = sequence_of(fields[2], "$.code_units")
    if len(unit_values) > limits.max_container_entries:
        raise resource("$.code_units", "code-unit count exceeds the configured container limit")
    bytes_value = fields[3]
    if bytes_value.kind is not Kind.BYTES:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.bytes", "expected Bytes")
    raw_bytes = bytes_value.as_bytes()
    if len(raw_bytes) > limits.max_blob_bytes:
        raise resource("$.bytes", "UTF-16 bytes exceed the configured blob limit")
    if len(raw_bytes) % 2 != 0:
        raise invalid("$.bytes", "UTF-16 byte length must be even")
    if len(raw_bytes) != len(unit_values) * 2:
        raise invalid("$.bytes", "byte count does not equal two bytes per code unit")
    code_units = []
    for index, encoded in enumerate(unit_values):
        path = f"$.code_units[{index}]"
        text = string_of(encoded, path)
        unit = _parse_java_unit(text)
        if unit is None:
            raise invalid(path, "code unit must be exactly four uppercase hexadecimal digits")
        offset = index * 2
        if (unit >> 8) != raw_bytes[offset] or (unit & 0xFF) != raw_bytes[offset + 1]:
            raise invalid(path, "code unit and byte representation differ")
        code_units.append(unit)
    status_text = string_of(fields[4], "$.unicode_status")
    if status_text not in ("WellFormedUnicode", "UnpairedSurrogate"):
        raise invalid("$.unicode_status", "unknown Java Unicode status")
    canonical = _java_utf16_value(code_units, limits)
    if not core_equal(canonical, value):
        raise invalid("$", "Java UTF-16 string is not canonically encoded")
    return canonical


def _java_matrix(vector: runner.Case) -> str | None:
    cases_value = compare.sequence_field(vector.input, "cases")
    if cases_value is None:
        return "missing input.cases"
    accepted = 0
    for case_value in cases_value:
        units_value = compare.sequence_field(case_value, "units")
        if units_value is None:
            return "units missing"
        code_units = []
        for unit_value in units_value:
            text = unit_value.as_string()
            code_units.append(int(text, 16))
        status = compare.string_field(case_value, "status")
        if status is None:
            return "status missing"
        try:
            exact_value = _java_utf16_value(code_units, ProtocolLimits())
        except ProtocolError as error:
            return f"Java UTF-16 construction failed: {error}"
        decoded = _java_utf16_from_value(exact_value, ProtocolLimits())
        actual_status = compare.string_field(decoded, "unicode_status")
        if actual_status == status and core_equal(decoded, exact_value):
            accepted += 1
    accepted_count = compare.integer_field(vector.expected, "accepted_count")
    if accepted != accepted_count:
        return f"Java UTF-16 edge matrix {accepted} != {accepted_count}"
    return None


def _java_rejection(vector: runner.Case) -> str | None:
    unit = compare.string_field(vector.input, "unit")
    bytes_hex = compare.string_field(vector.input, "bytes_hex")
    status = compare.string_field(vector.input, "status")
    if unit is None or bytes_hex is None or status is None:
        return "missing input unit/bytes_hex/status"
    value = PortableValue.object(
        [
            ("schema", PortableValue.string("core.java-utf16-string@1")),
            ("encoding", PortableValue.string("UTF16BE/1")),
            ("code_units", PortableValue.sequence((PortableValue.string(unit),))),
            ("bytes", PortableValue.bytes_value(bytes.fromhex(bytes_hex))),
            ("unicode_status", PortableValue.string(status)),
        ]
    )
    try:
        _java_utf16_from_value(value, ProtocolLimits())
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "published rejection must fail"


# ---------------------------------------------------------------------------
# INI / Properties query-result records (records_line_query.go)
# ---------------------------------------------------------------------------

_INI_ROLES = {
    MatchRole.INI_DOCUMENT,
    MatchRole.INI_SECTION,
    MatchRole.INI_DEFAULT_SECTION,
    MatchRole.INI_ENTRY,
    MatchRole.INI_PHYSICAL_LINE,
    MatchRole.INI_LOGICAL_LINE,
    MatchRole.INI_ERROR_LINE,
    MatchRole.INI_SYNTAX_PIECE,
}

_PROPERTIES_ROLES = {
    MatchRole.PROPERTIES_DOCUMENT,
    MatchRole.PROPERTIES_NATURAL_LINE,
    MatchRole.PROPERTIES_LOGICAL_LINE,
    MatchRole.PROPERTIES_PROPERTY,
    MatchRole.PROPERTIES_COMMENT,
    MatchRole.PROPERTIES_ESCAPE,
    MatchRole.PROPERTIES_ERROR_LINE,
    MatchRole.PROPERTIES_SYNTAX_PIECE,
}


def _external_locator(source_id: str, node_locator: str, role: MatchRole, ordinal: int) -> PortableValue:
    return PortableValue.object(
        [
            ("source_id", PortableValue.string(source_id)),
            ("node_locator", PortableValue.string(node_locator)),
            ("role", PortableValue.string(role.value)),
            ("ordinal", PortableValue.integer(ordinal)),
        ]
    )


def _parse_line_role(text: str, roles: set, path: str) -> MatchRole:
    role = records.parse_match_role(text)
    if role is None or role not in roles:
        raise invalid(path, "unknown match role")
    return role


def _ini_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "ini.native-semantic-query" and domain.version == 1:
        return role in _INI_ROLES and role is not MatchRole.INI_SYNTAX_PIECE
    if domain.id == "ini.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.INI_SYNTAX_PIECE
    return False


def _properties_domain_accepts_role(domain: QueryDomain, role: MatchRole) -> bool:
    if domain.id == "java-properties.native-semantic-query" and domain.version == 1:
        return role in _PROPERTIES_ROLES and role is not MatchRole.PROPERTIES_SYNTAX_PIECE
    if domain.id == "java-properties.lossless-syntax-query" and domain.version == 1:
        return role is MatchRole.PROPERTIES_SYNTAX_PIECE
    return False


def _line_query_result_value(
    schema: str,
    domain: QueryDomain,
    role: MatchRole,
    locators: list[PortableValue],
    completion_value: PortableValue,
    accept_role,
    roles: set,
) -> PortableValue:
    """Validates and encodes one line-format query result (line_query.rs)."""
    if not accept_role(domain, role):
        raise invalid("$", "line query domain and result role are inconsistent")
    if _completion_produced(completion_value) != len(locators):
        raise invalid("$", "completion count, role, or match ordinals are inconsistent")
    previous = 0
    for index, locator in enumerate(locators):
        fields = exact_fields(
            locator, ["source_id", "node_locator", "role", "ordinal"], f"$.matches[{index}]"
        )
        locator_role = _parse_line_role(
            string_of(fields[2], f"$.matches[{index}].role"), roles, f"$.matches[{index}].role"
        )
        ordinal = unsigned64(fields[3], f"$.matches[{index}].ordinal")
        if locator_role is not role:
            raise invalid("$", "completion count, role, or match ordinals are inconsistent")
        if index > 0 and ordinal <= previous:
            raise invalid("$", "completion count, role, or match ordinals are inconsistent")
        previous = ordinal
    return PortableValue.object(
        [
            ("schema", PortableValue.string(schema)),
            ("domain_id", PortableValue.string(domain.id)),
            ("domain_version", PortableValue.integer(domain.version)),
            ("role", PortableValue.string(role.value)),
            ("matches", PortableValue.sequence(tuple(locators))),
            ("completion", completion_value),
            ("diagnostics", PortableValue.sequence(())),
        ]
    )


def _role_list(vector: runner.Case, name: str, roles: set) -> list[MatchRole]:
    roles_value = compare.sequence_field(vector.input, name)
    if roles_value is None:
        raise ValueError(f"missing input.{name}")
    output = []
    for item in roles_value:
        output.append(_parse_line_role(item.as_string(), roles, f"input.{name}"))
    return output


def _ini_roles(vector: runner.Case) -> str | None:
    try:
        roles = _role_list(vector, "roles", _INI_ROLES)
    except (ProtocolError, ValueError) as error:
        return str(error)
    source_id = compare.string_field(vector.input, "source_id") or ""
    for ordinal, role in enumerate(roles):
        domain = QueryDomain("ini.native-semantic-query", 1)
        if role is MatchRole.INI_SYNTAX_PIECE:
            domain = QueryDomain("ini.lossless-syntax-query", 1)
        locator = _external_locator(source_id, f"ini:node:{ordinal}", role, ordinal)
        try:
            value = _line_query_result_value(
                "core.ini-query-result@1", domain, role, [locator], _completion_success(1, 1),
                _ini_domain_accepts_role, _INI_ROLES,
            )
        except ProtocolError as error:
            return f"unexpected rejection: {error}"
        message = _dual_roundtrip("core.ini-query-result@1", value)
        if message:
            return message
    role_count = compare.integer_field(vector.expected, "role_count")
    if len(roles) != role_count:
        return f"INI role count {len(roles)} != {role_count}"
    return None


def _properties_roles(vector: runner.Case) -> str | None:
    try:
        roles = _role_list(vector, "roles", _PROPERTIES_ROLES)
    except (ProtocolError, ValueError) as error:
        return str(error)
    source_id = compare.string_field(vector.input, "source_id") or ""
    for ordinal, role in enumerate(roles):
        domain = QueryDomain("java-properties.native-semantic-query", 1)
        if role is MatchRole.PROPERTIES_SYNTAX_PIECE:
            domain = QueryDomain("java-properties.lossless-syntax-query", 1)
        locator = _external_locator(source_id, f"properties:node:{ordinal}", role, ordinal)
        try:
            value = _line_query_result_value(
                "core.java-properties-query-result@1", domain, role, [locator],
                _completion_success(1, 1), _properties_domain_accepts_role, _PROPERTIES_ROLES,
            )
        except ProtocolError as error:
            return f"unexpected rejection: {error}"
        message = _dual_roundtrip("core.java-properties-query-result@1", value)
        if message:
            return message
    role_count = compare.integer_field(vector.expected, "role_count")
    if len(roles) != role_count:
        return f"Properties role count {len(roles)} != {role_count}"
    return None


def _line_domain_rejection(vector: runner.Case) -> str | None:
    role_text = compare.string_field(vector.input, "role")
    if role_text is None:
        return "missing input.role"
    try:
        role = _parse_line_role(role_text, _INI_ROLES, "$.role")
    except ProtocolError as error:
        return _expect_code(vector, error)
    try:
        _line_query_result_value(
            "core.ini-query-result@1", QueryDomain("ini.native-semantic-query", 1),
            role, [], _completion_success(0, 0), _ini_domain_accepts_role, _INI_ROLES,
        )
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "record must be rejected"


def _line_ordinal_rejection(vector: runner.Case) -> str | None:
    role_text = compare.string_field(vector.input, "role")
    ordinals = compare.integer_sequence(vector.input, "ordinals")
    produced = compare.integer_field(vector.input, "produced")
    if role_text is None or ordinals is None or produced is None:
        return "missing input facts"
    try:
        role = _parse_line_role(role_text, _PROPERTIES_ROLES, "$.role")
    except ProtocolError as error:
        return _expect_code(vector, error)
    locators = [
        _external_locator("source:properties", f"property:{index}", role, ordinal)
        for index, ordinal in enumerate(ordinals)
    ]
    try:
        _line_query_result_value(
            "core.java-properties-query-result@1",
            QueryDomain("java-properties.native-semantic-query", 1), role, locators,
            _completion_success(produced, produced), _properties_domain_accepts_role,
            _PROPERTIES_ROLES,
        )
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "record must be rejected"


def _line_process_local(vector: runner.Case) -> str | None:
    return _expect_code(vector, records.process_local_error("$.ini_match.node"))


# ---------------------------------------------------------------------------
# protocol envelope cases
# ---------------------------------------------------------------------------


def _v6_new_payloads() -> list[tuple[str, PortableValue]]:
    encoding = _code_page_encoding(1252)
    snapshot = _code_page_snapshot(1252, b"k=1")
    patch = SourcePatch.create(snapshot, [], {}, SourcePatchLimits())
    profile = ProfileReference("ini.windows", 1)
    request_value = _materialization_request_v2_value(
        profile, "ini.windows-canonical", 1, encoding, "CrLf", "RequireObject", "ExactOnly"
    )
    result_value = _materialization_result_v2_value(profile, "target:ini", snapshot, "Exact")
    ini_value = _line_query_result_value(
        "core.ini-query-result@1", QueryDomain("ini.native-semantic-query", 1),
        MatchRole.INI_DOCUMENT, [], _completion_success(0, 0), _ini_domain_accepts_role, _INI_ROLES,
    )
    properties_value = _line_query_result_value(
        "core.java-properties-query-result@1",
        QueryDomain("java-properties.native-semantic-query", 1),
        MatchRole.PROPERTIES_DOCUMENT, [], _completion_success(0, 0),
        _properties_domain_accepts_role, _PROPERTIES_ROLES,
    )
    java_value = _java_utf16_value([0xD800], ProtocolLimits())
    return [
        ("core.ini-query-result@1", ini_value),
        ("core.java-properties-query-result@1", properties_value),
        ("core.java-utf16-string@1", java_value),
        ("core.materialization-request@2", request_value),
        ("core.materialization-result@2", result_value),
        ("core.source-encoding@1", _source_encoding_value(encoding)),
        ("core.source-patch@2", _source_patch_v2_value(patch)),
        ("core.source-snapshot@2", _source_snapshot_v2_value(snapshot)),
    ]


def _protocol_old_rejection(vector: runner.Case) -> str | None:
    expected_code = compare.string_field(vector.expected, "code")
    rejected = 0
    for schema, payload in _v6_new_payloads():
        contract_id, version_text = schema.rsplit("@", 1)
        contract = ContractId(contract_id, int(version_text))
        all_rejected = True
        for version in range(1, 6):
            try:
                ProtocolMessage(contract, payload, ContractRegistry(version))
            except ProtocolError as error:
                if error.code != expected_code:
                    all_rejected = False
            else:
                all_rejected = False
        if all_rejected:
            rejected += 1
    rejected_pairs = compare.integer_field(vector.expected, "rejected_pairs")
    if rejected != rejected_pairs:
        return f"an old registry accepted a v6 contract ({rejected} != {rejected_pairs})"
    return None


def _protocol_version_dispatch(vector: runner.Case) -> str | None:
    profile = ProfileReference("ini.portable", 1)
    v2_value = _materialization_request_v2_value(
        profile, "ini.portable-canonical", 1, SourceEncoding.utf8(), "Lf", "RequireObject", "ExactOnly"
    )
    disguised = _replace_object_field(
        v2_value, "schema", PortableValue.string("core.materialization-request@1")
    )
    try:
        _materialization_request_v1_from_value(disguised)
    except ProtocolError as error:
        if error.code != "core.protocol.wrong-type@1" or error.path != "$.encoding":
            return f"version dispatch {error}"
        return None
    return "disguised payload must be rejected"


def _completion_value_with_registry(
    status, processed: int, produced: int, failure_code: str | None, registry: ErrorCodeRegistry
) -> PortableValue:
    if failure_code is not None:
        registry.validate(failure_code, "$.failure_code")
        valid = status in (
            records.CompletionStatus.FAILED,
            records.CompletionStatus.UNSUPPORTED,
            records.CompletionStatus.NOT_APPLICABLE,
        ) and failure_code != ""
    else:
        valid = status in (records.CompletionStatus.SUCCESS, records.CompletionStatus.CANCELLED)
    if not valid:
        raise invalid("$", "completion status contradicts limit/failure fields")
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.completion@1")),
            ("status", PortableValue.string(status.value)),
            ("processed", PortableValue.integer(processed)),
            ("produced", PortableValue.integer(produced)),
            ("limit_name", PortableValue.null()),
            ("failure_code", nullable_string(failure_code)),
        ]
    )


def _protocol_nested_error(vector: runner.Case) -> str | None:
    code_text = compare.string_field(vector.input, "failure_code")
    if code_text is None:
        return "missing input.failure_code"
    v5_code = compare.string_field(vector.expected, "v5_code")
    try:
        _completion_value_with_registry(
            records.CompletionStatus.FAILED, 1, 0, code_text, ErrorCodeRegistry(5)
        )
    except ProtocolError as error:
        if error.code != v5_code:
            return f"v5 nested rejection {error.code} != {v5_code}"
    else:
        return "v5 accepted a v6 diagnostic code"
    try:
        payload = _completion_value_with_registry(
            records.CompletionStatus.FAILED, 1, 0, code_text, ErrorCodeRegistry(6)
        )
    except ProtocolError as error:
        return f"v6 rejected its own diagnostic code: {error}"
    return _dual_roundtrip("core.completion@1", payload)


def _protocol_canonical_bytes(vector: runner.Case) -> str | None:
    encoding = _code_page_encoding(1252)
    java_value = _java_utf16_value([0x0000, 0xD83D, 0xDE00, 0xD800], ProtocolLimits())
    registry = ContractRegistry(6)
    limits = ProtocolLimits()
    encoding_message = ProtocolMessage(ContractId("core.source-encoding", 1), _source_encoding_value(encoding), registry)
    java_message = ProtocolMessage(ContractId("core.java-utf16-string", 1), java_value, registry)
    actual = [
        encoding_message.to_json(limits).hex(),
        encoding_message.to_pvce(limits).hex(),
        java_message.to_json(limits).hex(),
        java_message.to_pvce(limits).hex(),
    ]
    for name in (
        "source_encoding_json_hex",
        "source_encoding_pvce_hex",
        "java_utf16_json_hex",
        "java_utf16_pvce_hex",
    ):
        expected = compare.string_field(vector.expected, name)
        if actual[0] != expected:
            return f"canonical hex differs for {name}"
        actual = actual[1:]
    return None


def _protocol_schema_limits(vector: runner.Case) -> str | None:
    exact_value = _java_utf16_value([0x41], ProtocolLimits())
    unknown = _replace_object_field(exact_value, "unknown", PortableValue.null())
    try:
        _java_utf16_from_value(unknown, ProtocolLimits())
    except ProtocolError as error:
        unknown_field_code = compare.string_field(vector.expected, "unknown_field_code")
        if error.code != unknown_field_code or error.path != "$.unknown":
            return f"unknown field rejection {error}"
    else:
        return "unknown field must be rejected"
    limit = compare.integer_field(vector.input, "max_units")
    if limit is None:
        return "missing input.max_units"
    try:
        _java_utf16_from_value(exact_value, ProtocolLimits(max_container_entries=limit))
    except ProtocolError as error:
        limit_code = compare.string_field(vector.expected, "limit_code")
        if error.code != limit_code or error.path != "$.code_units":
            return f"limit rejection {error}"
    else:
        return "unit limit must be rejected"
    return None


runner.register_suite(
    "semantic-model-v6.json", "consema.semantic-model-v6.conformance@1", "core.semantic-model@6", 25, run
)
