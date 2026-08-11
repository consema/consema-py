"""Suite ``consema.protocol.conformance@2`` (protocol-v2.json, 11 cases):
registry manifests, error-code manifest, and the transferable source
snapshot / source patch dual transports plus their rejections. Dispatch is
by case id, mirroring go/conformance/protocol_v2.go exactly.

The transferable snapshot/patch records are the ``core.source-snapshot@1``
and ``core.source-patch@1`` wire codecs (the Go v2 runner externalizes
through the v1 message codecs; go/protocol/records_source.go
sourceSnapshotValue / encodingValueV1 / sourcePatchRecordValue), transcribed
here as :class:`_SourceSnapshotMessage` and :class:`_SourcePatchMessage`
because the Python protocol package owns the envelope and registries but
not these record codecs. The envelope transport closure (canonical tagged
JSON and PVCE/1) is exercised through the shared
``consema.protocol.contract.ProtocolMessage``.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.core import pvce
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.document.ids import ContentDigest
from consema.document.source import (
    BomKind,
    BomPolicy,
    EncodingFacts,
    EncodingRequest,
    SourceEncoding,
    SourceEncodingKind,
    SourceError,
    SourceErrorKind,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.source_patch import (
    SourcePatch,
    SourcePatchError,
    SourcePatchLimits,
    SourceReplacement,
)
from consema.protocol.contract import ContractId, ContractRegistry, ProtocolMessage
from consema.protocol.error_registry import (
    ErrorCodeRegistry,
    error_code_manifest_value,
    validate_error_code_manifest_value,
)
from consema.protocol.errors import (
    ProtocolError,
    ProtocolErrorKind,
    invalid,
    protocol_error,
    resource,
)
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import RegistryManifest
from consema.protocol.schema import (
    boolean_of,
    exact_fields,
    schema_fields,
    sequence_of,
    string_map_from_object,
    string_map_object,
    string_of,
)

# The Go wire spellings of the v1 encoding kinds (protocol_v2.go:59-73 and
# records_source.go encodingFromNameV1).
_GO_ENCODING_BY_NAME = {
    "Binary": SourceEncoding.binary,
    "Utf8": SourceEncoding.utf8,
    "Utf16Le": SourceEncoding.utf16le,
    "Utf16Be": SourceEncoding.utf16be,
    "Latin1": SourceEncoding.latin1,
}


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "protocol.v2.registry-manifest": _registry_manifest,
        "protocol.v2.registry-v1-frozen": _registry_manifest,
        "protocol.v2.error-code-manifest": _error_code_manifest,
        "protocol.v2.snapshot-dual-transport": _snapshot_transport,
        "protocol.v2.patch-dual-transport": _patch_transport,
        "protocol.v2.reject-source-under-v1": _reject_source_under_v1,
        "protocol.v2.reject-forged-digest": _reject_forged_digest,
        "protocol.v2.reject-forged-encoding": _reject_forged_encoding,
        "protocol.v2.snapshot-resource-limit": _snapshot_resource_limit,
        "protocol.v2.patch-resource-limit": _patch_resource_limit,
        "protocol.v2.patch-stale-after-wire": _patch_stale_after_wire,
    }
    for vector in data.cases:
        handler = handlers.get(vector.id)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(
                    id=vector.id, message="runner does not recognize published protocol v2 case"
                )
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# registry and error-code manifest cases
# ---------------------------------------------------------------------------


def _manifest_for(version: int) -> RegistryManifest:
    return RegistryManifest.build(
        version, ContractRegistry(version), ErrorCodeRegistry(version)
    )


def _registry_manifest(vector: runner.Case) -> str | None:
    frozen_v1 = vector.id == "protocol.v2.registry-v1-frozen"
    manifest = _manifest_for(1 if frozen_v1 else 2)
    registry = ContractRegistry(1 if frozen_v1 else 2)
    value = manifest.to_value()
    roundtripped = RegistryManifest.from_value(value)
    roundtrip_value = roundtripped.to_value()
    if not core_equal(roundtrip_value, value):
        return "manifest round-trip changed the record"
    semantic_model = compare.string_field(vector.expected, "semantic_model")
    contract_count = compare.integer_field(vector.expected, "contract_count")
    error_code_count = compare.integer_field(vector.expected, "error_code_count")
    recognizes = compare.boolean_field(vector.expected, "recognizes_source_snapshot")
    is_current = compare.boolean_field(vector.expected, "is_current")
    if semantic_model is None or contract_count is None or error_code_count is None:
        return "missing expected registry facts"
    if recognizes is None or is_current is None:
        return "missing expected registry facts"
    source_contract = ContractId("core.source-snapshot", 1)
    if (
        roundtripped.semantic_model.schema() != semantic_model
        or len(roundtripped.contracts) != contract_count
        or len(roundtripped.error_codes) != error_code_count
        or registry.recognizes(source_contract) != recognizes
    ):
        return "registry facts differ"
    if not frozen_v1:
        v2_manifest = _manifest_for(2)
        if core_equal(roundtrip_value, v2_manifest.to_value()) != is_current:
            return "is_current fact differs"
    elif is_current:
        return "v1 manifest must not be current"
    return None


def _error_code_manifest(vector: runner.Case) -> str | None:
    manifest = error_code_manifest_value(2)
    validate_error_code_manifest_value(manifest)
    error_codes = compare.sequence_field(manifest, "error_codes")
    expected_count = compare.integer_field(vector.expected, "error_code_count")
    required_code = compare.string_field(vector.expected, "required_code")
    if error_codes is None or expected_count is None or required_code is None:
        return "missing expected facts"
    v2_registry = ErrorCodeRegistry(2)
    v1_registry = ErrorCodeRegistry(1)
    if (
        len(error_codes) != expected_count
        or not v2_registry.contains(required_code)
        or v1_registry.contains(required_code)
    ):
        return "error-code manifest facts differ"
    return None


# ---------------------------------------------------------------------------
# vector-to-snapshot / replacement construction (protocol_v2.go:174-248)
# ---------------------------------------------------------------------------


def _encoding_from_input_name(name: str) -> SourceEncoding | None:
    return {
        "binary": SourceEncoding.binary,
        "utf-8": SourceEncoding.utf8,
        "utf-16le": SourceEncoding.utf16le,
        "utf-16be": SourceEncoding.utf16be,
        "latin-1": SourceEncoding.latin1,
    }.get(name)


def _snapshot_from_vector(vector: runner.Case, field: str) -> SourceSnapshot | str:
    raw_hex = compare.string_field(vector.input, field)
    if raw_hex is None:
        return f"missing input.{field}"
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return f"invalid hex in input.{field}"
    encoding_name = compare.string_field(vector.input, "encoding")
    if encoding_name is None:
        return "missing input.encoding"
    encoding = _encoding_from_input_name(encoding_name)
    if encoding is None:
        return f"unknown encoding {encoding_name!r}"
    try:
        return SourceSnapshot.from_raw(raw, EncodingRequest.new(encoding()), SourceLimits())
    except SourceError as error:
        return f"snapshot construction failed: {error.code}"


def _replacements_from_vector(vector: runner.Case) -> list[SourceReplacement] | str:
    replacements = compare.sequence_field(vector.input, "replacements")
    if replacements is None:
        return "missing input.replacements"
    output: list[SourceReplacement] = []
    for item in replacements:
        if item.kind is not Kind.OBJECT:
            return "replacement must be Object"
        old_start = compare.integer_field(item, "old_start")
        old_end = compare.integer_field(item, "old_end")
        original_hex = compare.string_field(item, "original_hex")
        replacement_hex = compare.string_field(item, "replacement_hex")
        if old_start is None or old_end is None or original_hex is None or replacement_hex is None:
            return "replacement fields missing"
        try:
            original = bytes.fromhex(original_hex)
            replacement = bytes.fromhex(replacement_hex)
        except ValueError:
            return "invalid replacement hex"
        output.append(
            SourceReplacement.new(old_start, old_end, original, replacement)
        )
    return output


# ---------------------------------------------------------------------------
# the v1 source-snapshot record codec (records_source.go sourceSnapshotValue /
# encodingValueV1 / sourceSnapshotFromValue / encodingFromValueV1)
# ---------------------------------------------------------------------------


def _go_encoding_name(encoding: SourceEncoding) -> str:
    return {
        SourceEncodingKind.BINARY: "Binary",
        SourceEncodingKind.UTF8: "Utf8",
        SourceEncodingKind.UTF16LE: "Utf16Le",
        SourceEncodingKind.UTF16BE: "Utf16Be",
        SourceEncodingKind.LATIN1: "Latin1",
        SourceEncodingKind.WINDOWS_CODE_PAGE: "WindowsCodePage",
    }[encoding.kind]


def _encoding_from_go_name(text: str, path: str) -> SourceEncoding:
    factory = _GO_ENCODING_BY_NAME.get(text)
    if factory is None:
        raise invalid(path, "unknown encoding ID")
    return factory()


def _nullable_string(value: str | None) -> PortableValue:
    if value is None:
        return PortableValue.null()
    return PortableValue.string(value)


def _optional_bom(value: PortableValue, path: str) -> BomKind | None:
    if value.kind is Kind.NULL:
        return None
    text = string_of(value, path)
    try:
        return BomKind(text)
    except ValueError:
        raise invalid(path, "unknown BOM ID") from None


def _optional_encoding_v1(value: PortableValue, path: str) -> SourceEncoding | None:
    if value.kind is Kind.NULL:
        return None
    return _encoding_from_go_name(string_of(value, path), path)


def _encoding_value_v1(facts: EncodingFacts) -> PortableValue:
    bom = facts.bom.value if facts.bom is not None else None
    return PortableValue.object(
        [
            ("profile_default", PortableValue.string(_go_encoding_name(facts.profile_default))),
            ("bom", _nullable_string(bom)),
            ("declaration", _nullable_string(_go_encoding_name(facts.declaration)) if facts.declaration is not None else PortableValue.null()),
            ("caller_override", _nullable_string(_go_encoding_name(facts.caller_override)) if facts.caller_override is not None else PortableValue.null()),
            ("selected", PortableValue.string(_go_encoding_name(facts.selected))),
        ]
    )


def _map_source_error(path: str, error: SourceError) -> ProtocolError:
    """The protocol mapping of a source construction failure
    (records_source.go mapSourceError)."""
    if error.kind is SourceErrorKind.RESOURCE_LIMIT:
        return resource(path, error.name or "source")
    return invalid(path, str(error))


def _facts_from_claim_v1(
    profile_default: SourceEncoding,
    bom: BomKind | None,
    declaration: SourceEncoding | None,
    caller_override: SourceEncoding | None,
    selected: SourceEncoding,
) -> EncodingFacts:
    """Validates a structurally complete v1 encoding-facts claim
    (records_source.go factsFromClaim; the v1 policy is always
    DetectUnicode)."""
    try:
        return EncodingFacts.from_claim_with_bom_policy(
            profile_default,
            BomPolicy.DETECT_UNICODE,
            bom,
            declaration,
            caller_override,
            selected,
        )
    except SourceError as error:
        raise _map_source_error("$.encoding", error) from None


def _encoding_from_value_v1(value: PortableValue, path: str) -> EncodingFacts:
    fields = exact_fields(
        value,
        ["profile_default", "bom", "declaration", "caller_override", "selected"],
        path,
    )
    profile_default = _encoding_from_go_name(
        string_of(fields[0], path + ".profile_default"), path + ".profile_default"
    )
    bom = _optional_bom(fields[1], path + ".bom")
    declaration = _optional_encoding_v1(fields[2], path + ".declaration")
    caller_override = _optional_encoding_v1(fields[3], path + ".caller_override")
    selected = _encoding_from_go_name(
        string_of(fields[4], path + ".selected"), path + ".selected"
    )
    return _facts_from_claim_v1(profile_default, bom, declaration, caller_override, selected)


def _facts_to_request_v1(facts: EncodingFacts) -> EncodingRequest:
    """Rebuilds the v1 resolution request from claimed facts
    (records_source.go factsToRequestV1)."""
    request = EncodingRequest.new(facts.profile_default)
    if facts.declaration is not None:
        request = request.with_declaration(facts.declaration)
    if facts.caller_override is not None:
        request = request.with_caller_override(facts.caller_override)
    return request


def _digest_value(digest: ContentDigest) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string(digest.algorithm)),
            ("hex", PortableValue.string(digest.hex)),
        ]
    )


def _parse_digest(value: PortableValue, path: str) -> ContentDigest:
    fields = exact_fields(value, ["algorithm", "hex"], path)
    algorithm = string_of(fields[0], path + ".algorithm")
    if algorithm != "sha256":
        raise invalid(path, "expected sha256")
    hex_text = string_of(fields[1], path + ".hex")
    if len(hex_text) != 64 or not all(
        ("0" <= character <= "9") or ("a" <= character <= "f") for character in hex_text
    ):
        raise invalid(path, "invalid lowercase sha256")
    return ContentDigest.from_bytes(bytes.fromhex(hex_text))


def _snapshot_value(
    schema: str, snapshot: SourceSnapshot, encoding: PortableValue
) -> PortableValue:
    status = "NotText" if snapshot.decoded_text() is None else "Available"
    return PortableValue.object(
        [
            ("schema", PortableValue.string(schema)),
            ("raw_bytes", PortableValue.bytes_value(snapshot.bytes())),
            ("digest", _digest_value(snapshot.digest())),
            ("encoding", encoding),
            ("decoded_status", PortableValue.string(status)),
        ]
    )


class _SourceSnapshotMessage:
    """The transferable ``core.source-snapshot@1`` content fact
    (records_source.go SourceSnapshotMessageV1)."""

    __slots__ = ("snapshot",)

    def __init__(self, snapshot: SourceSnapshot):
        self.snapshot = snapshot

    @classmethod
    def from_snapshot(cls, snapshot: SourceSnapshot) -> _SourceSnapshotMessage:
        """Rejects Windows code pages and non-DetectUnicode policies under
        source contract v1 (records_source.go ensureV1EncodingFacts)."""
        facts = snapshot.encoding_facts()
        if facts.bom_policy is not BomPolicy.DETECT_UNICODE:
            raise invalid("$.encoding", "core source v1 requires DetectUnicode BOM policy")
        for encoding in (
            facts.profile_default,
            facts.declaration,
            facts.caller_override,
            facts.selected,
        ):
            if encoding is not None and encoding.kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
                raise invalid("$.encoding", "core source v1 does not support Windows code pages")
        return cls(snapshot)

    def to_value(self) -> PortableValue:
        return _snapshot_value(
            "core.source-snapshot@1", self.snapshot, _encoding_value_v1(self.snapshot.encoding_facts())
        )

    @classmethod
    def from_value(cls, value: PortableValue, limits: SourceLimits) -> _SourceSnapshotMessage:
        fields = schema_fields(
            value,
            "core.source-snapshot@1",
            ["schema", "raw_bytes", "digest", "encoding", "decoded_status"],
            "$",
        )
        raw = fields[1]
        if raw.kind is not Kind.BYTES:
            raise protocol_error(ProtocolErrorKind.WRONG_TYPE, "$.raw_bytes", "expected Bytes")
        claimed_digest = _parse_digest(fields[2], "$.digest")
        claimed_encoding = _encoding_from_value_v1(fields[3], "$.encoding")
        decoded_status = string_of(fields[4], "$.decoded_status")
        if decoded_status not in ("Available", "NotText"):
            raise invalid("$.decoded_status", "expected Available or NotText")
        try:
            snapshot = SourceSnapshot.from_raw(
                raw.as_bytes(), _facts_to_request_v1(claimed_encoding), limits
            )
        except SourceError as error:
            raise _map_source_error("$.raw_bytes", error) from None
        if snapshot.digest() != claimed_digest:
            raise invalid("$.digest", "digest does not match raw_bytes")
        if snapshot.encoding_facts() != claimed_encoding:
            raise invalid("$.encoding", "encoding facts do not match raw_bytes resolution")
        actual_status = "NotText" if snapshot.decoded_text() is None else "Available"
        if decoded_status != actual_status:
            raise invalid("$.decoded_status", "decoded status contradicts selected encoding")
        return cls(snapshot)


# ---------------------------------------------------------------------------
# the v1 source-patch record codec (records_source.go sourcePatchRecordValue /
# sourcePatchFromValue)
# ---------------------------------------------------------------------------


class _SourcePatchMessage:
    """The transferable ``core.source-patch@1`` verification facts
    (records_source.go SourcePatchMessageV1)."""

    __slots__ = ("patch",)

    def __init__(self, patch: SourcePatch):
        self.patch = patch

    @classmethod
    def from_patch(cls, patch: SourcePatch) -> _SourcePatchMessage:
        facts = _facts_from_claim_v1(
            patch.encoding.profile_default,
            patch.encoding.bom,
            patch.encoding.declaration,
            patch.encoding.caller_override,
            patch.encoding.selected,
        )
        if facts.bom_policy is not BomPolicy.DETECT_UNICODE:
            raise invalid("$.encoding", "core source v1 requires DetectUnicode BOM policy")
        for encoding in (
            facts.profile_default,
            facts.declaration,
            facts.caller_override,
            facts.selected,
        ):
            if encoding is not None and encoding.kind is SourceEncodingKind.WINDOWS_CODE_PAGE:
                raise invalid("$.encoding", "core source v1 does not support Windows code pages")
        return cls(patch)

    def to_value(self) -> PortableValue:
        replacement_values = []
        for replacement in self.patch.replacements:
            replacement_values.append(
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
        return PortableValue.object(
            [
                ("schema", PortableValue.string("core.source-patch@1")),
                ("base_digest", _digest_value(self.patch.base_digest)),
                ("target_digest", _digest_value(self.patch.target_digest)),
                ("encoding", _encoding_value_v1(self.patch.encoding)),
                ("replacements", PortableValue.sequence(tuple(replacement_values))),
                ("metadata", string_map_object(self.patch.metadata)),
            ]
        )

    @classmethod
    def from_value(
        cls, value: PortableValue, limits: SourcePatchLimits
    ) -> _SourcePatchMessage:
        fields = schema_fields(
            value,
            "core.source-patch@1",
            ["schema", "base_digest", "target_digest", "encoding", "replacements", "metadata"],
            "$",
        )
        base_digest = _parse_digest(fields[1], "$.base_digest")
        target_digest = _parse_digest(fields[2], "$.target_digest")
        claimed_encoding = _encoding_from_value_v1(fields[3], "$.encoding")
        replacement_values = sequence_of(fields[4], "$.replacements")
        if len(replacement_values) > limits.max_replacements:
            raise resource("$.replacements", "replacement count exceeds configured limit")
        replacements: list[SourceReplacement] = []
        for index, replacement_value in enumerate(replacement_values):
            path = f"$.replacements[{index}]"
            replacement_fields = exact_fields(
                replacement_value,
                ["old_start", "old_end", "original", "replacement",
                 "redact_original", "redact_replacement"],
                path,
            )
            old_start = _unsigned64(replacement_fields[0], path + ".old_start")
            old_end = _unsigned64(replacement_fields[1], path + ".old_end")
            original = replacement_fields[2]
            if original.kind is not Kind.BYTES:
                raise protocol_error(
                    ProtocolErrorKind.WRONG_TYPE, path + ".original", "expected Bytes"
                )
            replacement = replacement_fields[3]
            if replacement.kind is not Kind.BYTES:
                raise protocol_error(
                    ProtocolErrorKind.WRONG_TYPE, path + ".replacement", "expected Bytes"
                )
            redact_original = boolean_of(replacement_fields[4], path + ".redact_original")
            redact_replacement = boolean_of(replacement_fields[5], path + ".redact_replacement")
            replacements.append(
                SourceReplacement(
                    old_start=old_start,
                    old_end=old_end,
                    original=original.as_bytes(),
                    replacement=replacement.as_bytes(),
                    redact_original=redact_original,
                    redact_replacement=redact_replacement,
                )
            )
        metadata = string_map_from_object(fields[5], "$.metadata")
        patch = SourcePatch.new(
            base_digest,
            target_digest,
            claimed_encoding,
            replacements,
            metadata,
            SourcePatchLimits(),
        )
        return cls(patch)


def _unsigned64(value: PortableValue, path: str) -> int:
    if value.kind is not Kind.INTEGER:
        raise protocol_error(ProtocolErrorKind.WRONG_TYPE, path, "expected Integer")
    number = value.as_integer()
    if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE, path, "expected an unsigned 64-bit Integer"
        )
    return number


# ---------------------------------------------------------------------------
# dual-transport closure
# ---------------------------------------------------------------------------


def _dual_transport_closed(
    contract: ContractId, payload: PortableValue, registry: ContractRegistry
) -> str | None:
    """Envelope JSON/PVCE closure (protocol_v2.go:268-310)."""
    limits = ProtocolLimits()
    try:
        envelope = ProtocolMessage(contract, payload, registry)
    except ProtocolError as error:
        return f"envelope: {error.code}"
    try:
        json_bytes = envelope.to_json(limits)
        decoded_json = ProtocolMessage.from_json(json_bytes, limits, registry)
        pvce_bytes = envelope.to_pvce(limits)
        decoded_pvce = ProtocolMessage.from_pvce(pvce_bytes, limits, registry)
    except ProtocolError as error:
        return f"transport: {error.code}"
    if not core_equal(decoded_json.payload, envelope.payload) or not core_equal(
        decoded_pvce.payload, envelope.payload
    ):
        return "dual transport did not close"
    return None


def _expect_code(vector: runner.Case, error: object) -> str | None:
    """Compares one failure with the expected.code fact."""
    expected = compare.string_field(vector.expected, "code")
    if expected is None:
        return "missing expected.code"
    actual = _failure_code(error)
    if actual != expected:
        return f"failure code {actual} != {expected}"
    return None


def _failure_code(error: object) -> str:
    if isinstance(error, ProtocolError):
        return error.code
    if isinstance(error, SourceError):
        return error.code
    if isinstance(error, SourcePatchError):
        return error.code
    return str(error)


# ---------------------------------------------------------------------------
# the dual-transport cases
# ---------------------------------------------------------------------------


def _snapshot_transport(vector: runner.Case) -> str | None:
    built = _snapshot_from_vector(vector, "raw_hex")
    if isinstance(built, str):
        return built
    snapshot = built
    try:
        message = _SourceSnapshotMessage.from_snapshot(snapshot)
        payload = message.to_value()
    except ProtocolError as error:
        return f"snapshot message: {error.code}"
    registry = ContractRegistry(2)
    contract = ContractId("core.source-snapshot", 1)
    closed = _dual_transport_closed(contract, payload, registry)
    if closed:
        return closed
    json_equal = compare.boolean_field(vector.expected, "json_equal")
    pvce_equal = compare.boolean_field(vector.expected, "pvce_equal")
    expected_digest = compare.string_field(vector.expected, "digest")
    if json_equal is not True or pvce_equal is not True:
        return "unexpected expectation facts"
    if snapshot.digest().hex != expected_digest:
        return f"digest {snapshot.digest().hex} != {expected_digest}"
    try:
        decoded = _SourceSnapshotMessage.from_value(payload, SourceLimits())
    except ProtocolError as error:
        return f"decode: {error.code}"
    if decoded.snapshot.bytes() != snapshot.bytes():
        return "decoded snapshot differs"
    return None


def _patch_transport(vector: runner.Case) -> str | None:
    built_base = _snapshot_from_vector(vector, "base_hex")
    if isinstance(built_base, str):
        return built_base
    built_replacements = _replacements_from_vector(vector)
    if isinstance(built_replacements, str):
        return built_replacements
    try:
        patch = SourcePatch.create(
            built_base,
            built_replacements,
            {"actor": "protocol-v2"},
            SourcePatchLimits(),
        )
        message = _SourcePatchMessage.from_patch(patch)
        payload = message.to_value()
    except (SourceError, SourcePatchError, ProtocolError) as error:
        return f"patch construction: {_failure_code(error)}"
    registry = ContractRegistry(2)
    contract = ContractId("core.source-patch", 1)
    closed = _dual_transport_closed(contract, payload, registry)
    if closed:
        return closed
    json_equal = compare.boolean_field(vector.expected, "json_equal")
    pvce_equal = compare.boolean_field(vector.expected, "pvce_equal")
    target_hex = compare.string_field(vector.expected, "target_hex")
    if json_equal is not True or pvce_equal is not True:
        return "unexpected expectation facts"
    try:
        decoded = _SourcePatchMessage.from_value(payload, SourcePatchLimits())
        target = decoded.patch.apply(built_base, SourcePatchLimits())
    except (SourcePatchError, ProtocolError) as error:
        return f"patch decode/apply: {_failure_code(error)}"
    if target.bytes().hex() != target_hex:
        return f"target hex {target.bytes().hex()} != {target_hex}"
    return None


# ---------------------------------------------------------------------------
# rejections
# ---------------------------------------------------------------------------


def _reject_source_under_v1(vector: runner.Case) -> str | None:
    built = _snapshot_from_vector(vector, "raw_hex")
    if isinstance(built, str):
        return built
    try:
        payload = _SourceSnapshotMessage.from_snapshot(built).to_value()
        ProtocolMessage(
            ContractId("core.source-snapshot", 1), payload, ContractRegistry(1)
        )
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "envelope must be rejected"


def _reject_forged_digest(vector: runner.Case) -> str | None:
    built = _snapshot_from_vector(vector, "raw_hex")
    if isinstance(built, str):
        return built
    try:
        payload = _SourceSnapshotMessage.from_snapshot(built).to_value()
        forged = _replace_object_field(
            payload, "digest", _replacement_digest_value("00" * 32)
        )
        _SourceSnapshotMessage.from_value(forged, SourceLimits())
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "forged digest must be rejected"


def _reject_forged_encoding(vector: runner.Case) -> str | None:
    built = _snapshot_from_vector(vector, "raw_hex")
    if isinstance(built, str):
        return built
    forged_selected = compare.string_field(vector.input, "forged_selected")
    if forged_selected is None:
        return "missing input.forged_selected"
    try:
        payload = _SourceSnapshotMessage.from_snapshot(built).to_value()
        encoding_value = compare.object_field(payload, "encoding")
        if encoding_value is None:
            return "encoding field missing"
        forged_encoding = _replace_object_field(
            encoding_value, "selected", PortableValue.string(forged_selected)
        )
        forged = _replace_object_field(payload, "encoding", forged_encoding)
        _SourceSnapshotMessage.from_value(forged, SourceLimits())
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "forged encoding must be rejected"


def _snapshot_resource_limit(vector: runner.Case) -> str | None:
    built = _snapshot_from_vector(vector, "raw_hex")
    if isinstance(built, str):
        return built
    max_raw_bytes = compare.integer_field(vector.input, "max_raw_bytes")
    if max_raw_bytes is None:
        return "missing input.max_raw_bytes"
    try:
        payload = _SourceSnapshotMessage.from_snapshot(built).to_value()
        limits = SourceLimits(max_raw_bytes=max_raw_bytes)
        _SourceSnapshotMessage.from_value(payload, limits)
    except ProtocolError as error:
        return _expect_code(vector, error)
    return "snapshot must fail under the raw-byte limit"


def _patch_resource_limit(vector: runner.Case) -> str | None:
    built_base = _snapshot_from_vector(vector, "base_hex")
    if isinstance(built_base, str):
        return built_base
    built_replacements = _replacements_from_vector(vector)
    if isinstance(built_replacements, str):
        return built_replacements
    max_replacements = compare.integer_field(vector.input, "max_replacements")
    if max_replacements is None:
        return "missing input.max_replacements"
    try:
        patch = SourcePatch.create(
            built_base, built_replacements, {}, SourcePatchLimits()
        )
        payload = _SourcePatchMessage.from_patch(patch).to_value()
        limits = SourcePatchLimits(max_replacements=max_replacements)
        _SourcePatchMessage.from_value(payload, limits)
    except (SourceError, SourcePatchError, ProtocolError) as error:
        return _expect_code(vector, error)
    return "patch must fail under the replacement limit"


def _patch_stale_after_wire(vector: runner.Case) -> str | None:
    built_base = _snapshot_from_vector(vector, "base_hex")
    if isinstance(built_base, str):
        return built_base
    built_replacements = _replacements_from_vector(vector)
    if isinstance(built_replacements, str):
        return built_replacements
    stale_hex = compare.string_field(vector.input, "stale_hex")
    if stale_hex is None:
        return "missing input.stale_hex"
    try:
        patch = SourcePatch.create(
            built_base, built_replacements, {}, SourcePatchLimits()
        )
        payload = _SourcePatchMessage.from_patch(patch).to_value()
        transported = pvce.encode(payload)
        transported_value = pvce.decode(transported)
        decoded = _SourcePatchMessage.from_value(transported_value, SourcePatchLimits())
        stale_bytes = bytes.fromhex(stale_hex)
        encoding_name = compare.string_field(vector.input, "encoding")
        if encoding_name is None:
            return "missing input.encoding"
        encoding = _encoding_from_input_name(encoding_name)
        if encoding is None:
            return f"unknown encoding {encoding_name!r}"
        stale = SourceSnapshot.from_raw(
            stale_bytes, EncodingRequest.new(encoding()), SourceLimits()
        )
        decoded.patch.apply(stale, SourcePatchLimits())
    except (SourceError, SourcePatchError, ProtocolError) as error:
        expected = compare.string_field(vector.expected, "code")
        if expected is None:
            return "missing expected.code"
        actual = _failure_code(error)
        if actual != expected:
            return f"apply error {actual} != {expected}"
        return None
    return "stale base must be rejected"


# ---------------------------------------------------------------------------
# tampering helpers (protocol_v2.go:592-620)
# ---------------------------------------------------------------------------


def _replace_object_field(
    value: PortableValue, name: str, replacement: PortableValue
) -> PortableValue:
    if value.kind is not Kind.OBJECT:
        raise ValueError("value must be Object")
    entries = []
    found = False
    for key, item in value.as_object():
        if key == name:
            item = replacement
            found = True
        entries.append((key, item))
    if not found:
        raise ValueError(f"field {name} is absent")
    return PortableValue.object(entries)


def _replacement_digest_value(hex_text: str) -> PortableValue:
    return PortableValue.object(
        [
            ("algorithm", PortableValue.string("sha256")),
            ("hex", PortableValue.string(hex_text)),
        ]
    )


runner.register_suite("protocol-v2.json", "consema.protocol.conformance@2", "core.semantic-model@2", 11, run)
