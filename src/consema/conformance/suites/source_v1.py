"""Suite ``consema.source.conformance@1`` (source-v1.json, 28 cases): source
snapshot digest/identity, encoding resolution, decoded locations, binary
coverage, raw-byte patches, and resource limits. Dispatch is by case id,
mirroring go/conformance/source_v1.go.
"""

from __future__ import annotations

from dataclasses import replace

from consema.conformance import compare
from consema.conformance import runner
from consema.core.value import Kind
from consema.document.ids import ContentDigest
from consema.document.source import (
    DecodedOffset,
    EncodingRequest,
    SourceEncoding,
    SourceError,
    SourceLimits,
    SourceSnapshot,
)
from consema.document.source_patch import (
    SourcePatch,
    SourcePatchError,
    SourcePatchLimits,
    SourceReplacement,
)
from consema.document.structural import (
    BinaryRegion,
    BinaryStructuralIndex,
    DocumentAuthority,
    LocationError,
    NodeRole,
)

_NOT_DECODED_BOUNDARY = "NotDecodedBoundary"
_DECODED_OFFSET_NOT_BOUNDARY = "DecodedOffsetNotBoundary"


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    for vector in data.cases:
        message = _dispatch(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


def _dispatch(vector: runner.Case) -> str | None:
    if vector.id == "source.digest.sha256-empty" or vector.id == "source.digest.sha256-abc":
        return _digest_case(vector)
    if vector.id == "source.identity.equal-bytes-distinct-snapshots":
        return _identity_case(vector)
    if vector.id.startswith("source.encoding."):
        return _encoding_case(vector)
    if vector.id.startswith("source.location."):
        return _location_case(vector)
    if vector.id.startswith("source.binary."):
        return _binary_case(vector)
    if vector.id.startswith("source.patch.") or vector.id == "source.resource.patch-count-limit":
        return _patch_case(vector)
    if vector.id.startswith("source.resource."):
        return _resource_case(vector)
    return "runner does not recognize published source case"


def _parse_encoding(name: str) -> SourceEncoding | None:
    factories = {
        "binary": SourceEncoding.binary,
        "utf-8": SourceEncoding.utf8,
        "utf-16le": SourceEncoding.utf16le,
        "utf-16be": SourceEncoding.utf16be,
        "latin-1": SourceEncoding.latin1,
    }
    factory = factories.get(name)
    return factory() if factory is not None else None


def _encoding_request(vector: runner.Case) -> EncodingRequest | None:
    """Rebuilds the deterministic resolution request from the vector's
    encoding facts (go/conformance/source_v1.go sourceEncodingRequest)."""
    encoding_name = compare.string_field(vector.input, "encoding")
    if encoding_name is None:
        return None
    encoding = _parse_encoding(encoding_name)
    if encoding is None:
        return None
    request = EncodingRequest.new(encoding)
    declaration = compare.string_field(vector.input, "declaration")
    if declaration is not None:
        parsed = _parse_encoding(declaration)
        if parsed is None:
            return None
        request = request.with_declaration(parsed)
    override = compare.string_field(vector.input, "caller_override")
    if override is not None:
        parsed = _parse_encoding(override)
        if parsed is None:
            return None
        request = request.with_caller_override(parsed)
    return request


def _snapshot_from_case(vector: runner.Case, name: str):
    """One snapshot from a hex input field under the case's encoding facts."""
    text = compare.string_field(vector.input, name)
    if text is None:
        return None, f"missing input.{name}"
    request = _encoding_request(vector)
    if request is None:
        return None, "missing or invalid input.encoding"
    try:
        return SourceSnapshot.from_raw(bytes.fromhex(text), request, SourceLimits()), None
    except SourceError as error:
        return None, f"snapshot construction failed: {error.code}"


def _digest_case(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "raw_hex")
    if text is None:
        return "missing input.raw_hex"
    expected = compare.string_field(vector.expected, "digest")
    if expected is None:
        return "missing expected.digest"
    return compare.require_equal(ContentDigest.of(bytes.fromhex(text)).hex, expected, "digest")


def _identity_case(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "raw_hex")
    if text is None:
        return "missing input.raw_hex"
    equal_digest = compare.boolean_field(vector.expected, "equal_digest")
    distinct_snapshot = compare.boolean_field(vector.expected, "distinct_snapshot")
    if equal_digest is None or distinct_snapshot is None:
        return "missing expected facts"
    raw = bytes.fromhex(text)
    first = SourceSnapshot.from_utf8(raw)
    second = SourceSnapshot.from_utf8(raw)
    if (first.digest() == second.digest()) != equal_digest:
        return "digest equality fact differs"
    if (first is not second) != distinct_snapshot:
        return "snapshot identity distinctness fact differs"
    return None


def _encoding_case(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "raw_hex")
    if text is None:
        return "missing input.raw_hex"
    request = _encoding_request(vector)
    if request is None:
        return "missing or invalid input.encoding"
    try:
        snapshot = SourceSnapshot.from_raw(bytes.fromhex(text), request, SourceLimits())
    except SourceError as error:
        return _expect_code(vector, error.code)
    expected_raw = compare.string_field(vector.expected, "raw_hex")
    expected_selected = compare.string_field(vector.expected, "selected")
    if expected_raw is None or expected_selected is None:
        return "missing expected.raw_hex/selected"
    message = compare.require_bytes_equal(snapshot.bytes(), expected_raw, "retained bytes")
    if message:
        return message
    message = compare.require_equal(
        snapshot.encoding_facts().selected.as_str, expected_selected, "selected"
    )
    if message:
        return message
    decoded_value = compare.object_field(vector.expected, "decoded_utf8_hex")
    if decoded_value is None:
        return "missing expected.decoded_utf8_hex"
    if decoded_value.kind is Kind.NULL:
        if snapshot.decoded_text() is not None:
            return "decoded text must be unavailable for binary sources"
        return None
    if decoded_value.kind is not Kind.STRING:
        return "expected.decoded_utf8_hex must be String or Null"
    text_out = snapshot.decoded_text()
    if text_out is None:
        return "decoded text unavailable"
    return compare.require_bytes_equal(
        text_out.encode("utf-8"), decoded_value.as_string(), "decoded text"
    )


def _location_case(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "raw_hex")
    if text is None:
        return "missing input.raw_hex"
    request = _encoding_request(vector)
    if request is None:
        return "missing or invalid input.encoding"
    try:
        snapshot = SourceSnapshot.from_raw(bytes.fromhex(text), request, SourceLimits())
    except SourceError as error:
        return f"snapshot construction failed: {error.code}"
    if snapshot.decoded_text() is None:
        try:
            snapshot.decoded_position(0)
        except LocationError as error:
            return _expect_code(vector, error.name)
        return "decoded_position must fail for binary sources"
    raw_byte = compare.integer_field(vector.input, "raw_byte")
    if raw_byte is None:
        return "missing input.raw_byte"
    try:
        position = snapshot.decoded_position(raw_byte)
    except LocationError as error:
        return f"decoded_position failed: {error.name}"
    expected_utf8 = compare.integer_field(vector.expected, "decoded_utf8_byte")
    expected_scalar = compare.integer_field(vector.expected, "unicode_scalar_offset")
    expected_utf16 = compare.integer_field(vector.expected, "utf16_code_unit_offset")
    if expected_utf8 is None or expected_scalar is None or expected_utf16 is None:
        return "missing expected location facts"
    message = compare.require_equal(
        position.decoded_utf8_byte, expected_utf8, "decoded_utf8_byte"
    )
    if message:
        return message
    message = compare.require_equal(
        position.unicode_scalar_offset, expected_scalar, "unicode_scalar_offset"
    )
    if message:
        return message
    message = compare.require_equal(
        position.utf16_code_unit_offset, expected_utf16, "utf16_code_unit_offset"
    )
    if message:
        return message
    for offset in (
        DecodedOffset.utf8_byte(position.decoded_utf8_byte),
        DecodedOffset.unicode_scalar(position.unicode_scalar_offset),
        DecodedOffset.utf16_code_unit(position.utf16_code_unit_offset),
    ):
        try:
            back = snapshot.raw_byte_at(offset)
        except LocationError as error:
            return f"raw_byte_at({offset.value}) failed: {error.name}"
        if back != raw_byte:
            return f"raw_byte_at({offset.value}) = {back}; want {raw_byte}"
    invalid_raw = compare.integer_field(vector.input, "invalid_raw_byte")
    if invalid_raw is None:
        return "missing input.invalid_raw_byte"
    try:
        snapshot.decoded_position(invalid_raw)
    except LocationError as error:
        if error.name != _NOT_DECODED_BOUNDARY:
            return (
                f"decoded_position({invalid_raw}) = {error.name}; "
                f"want {_NOT_DECODED_BOUNDARY}"
            )
    else:
        return f"decoded_position({invalid_raw}) must fail"
    invalid_utf16 = compare.integer_field(vector.input, "invalid_utf16_offset")
    if invalid_utf16 is None:
        return "missing input.invalid_utf16_offset"
    try:
        snapshot.raw_byte_at(DecodedOffset.utf16_code_unit(invalid_utf16))
    except LocationError as error:
        if error.name != _DECODED_OFFSET_NOT_BOUNDARY:
            return (
                f"raw_byte_at(utf16 {invalid_utf16}) = {error.name}; "
                f"want {_DECODED_OFFSET_NOT_BOUNDARY}"
            )
    else:
        return f"raw_byte_at(utf16 {invalid_utf16}) must fail"
    return None


def _binary_case(vector: runner.Case) -> str | None:
    source_len = compare.integer_field(vector.input, "source_len")
    if source_len is None:
        return "missing input.source_len"
    region_values = compare.sequence_field(vector.input, "regions")
    if region_values is None:
        return "input.regions must be a Sequence"
    authority = DocumentAuthority.fresh()
    regions = []
    for index, value in enumerate(region_values):
        start = compare.integer_field(value, "start")
        end = compare.integer_field(value, "end")
        kind = compare.string_field(value, "kind")
        if start is None or end is None or kind is None:
            return f"region {index} must carry start/end/kind"
        regions.append(
            BinaryRegion(
                node=authority.node_ref(index, NodeRole.BINARY_REGION),
                span=authority.span(start, end),
                kind=kind,
            )
        )
    try:
        index = BinaryStructuralIndex.new(authority.identity, source_len, regions)
    except LocationError as error:
        return _expect_code(vector, error.name)
    expected_count = compare.integer_field(vector.expected, "region_count")
    if expected_count is None:
        return "missing expected.region_count"
    actual_regions = index.regions
    last_end = actual_regions[-1].span.end_byte if actual_regions else 0
    if len(actual_regions) != expected_count or last_end != source_len:
        return (
            f"coverage {len(actual_regions)} regions ending at {last_end}; "
            f"want {expected_count} ending at {source_len}"
        )
    return None


def _patch_case(vector: runner.Case) -> str | None:
    mode = compare.string_field(vector.input, "mode")
    if mode is None:
        return "missing input.mode"
    base, message = _snapshot_from_case(vector, "base_hex")
    if message:
        return message
    replacements, message = _replacement_values(vector)
    if message:
        return message
    limits = _patch_limits(vector)
    metadata = {"actor": "conformance"}
    if mode == "create-apply":
        try:
            patch = SourcePatch.create(base, replacements, metadata, limits)
            target = patch.apply(base, limits)
        except SourcePatchError as error:
            return f"patch failed: {error.code}"
        expected = compare.string_field(vector.expected, "target_hex")
        if expected is None:
            return "missing expected.target_hex"
        message = compare.require_bytes_equal(target.bytes(), expected, "target bytes")
        if message:
            return message
        if target.digest() != patch.target_digest:
            return "applied digest != patch target digest"
        if patch.metadata.get("actor") != "conformance":
            return "patch metadata not retained"
        return None
    if mode == "stale-base":
        try:
            patch = SourcePatch.create(base, replacements, metadata, limits)
        except SourcePatchError as error:
            return f"patch construction failed: {error.code}"
        stale, message = _snapshot_from_case(vector, "stale_hex")
        if message:
            return message
        try:
            patch.apply(stale, limits)
        except SourcePatchError as error:
            return _expect_code(vector, error.code)
        return "apply must fail"
    if mode == "wrong-original":
        target_text = compare.string_field(vector.input, "target_hex")
        if target_text is None:
            return "missing input.target_hex"
        return _patch_from_facts_apply(
            vector, base, replacements, metadata, limits, bytes.fromhex(target_text)
        )
    if mode in ("overlap", "count-limit"):
        try:
            SourcePatch.create(base, replacements, metadata, limits)
        except SourcePatchError as error:
            return _expect_code(vector, error.code)
        return "construction must fail"
    if mode == "wrong-target":
        return _patch_from_facts_apply(
            vector, base, replacements, metadata, limits, b"deliberately-wrong-target"
        )
    if mode == "encoding-change":
        target_text = compare.string_field(vector.input, "target_hex")
        if target_text is None:
            return "missing input.target_hex"
        return _patch_from_facts_apply(
            vector, base, replacements, metadata, limits, bytes.fromhex(target_text)
        )
    return f"unknown patch mode {mode}"


def _patch_from_facts_apply(
    vector: runner.Case,
    base: SourceSnapshot,
    replacements: list[SourceReplacement],
    metadata: dict[str, str],
    limits: SourcePatchLimits,
    target_bytes: bytes,
) -> str | None:
    """Builds the patch from externally supplied digest facts and applies it
    to the base snapshot (wrong-original / wrong-target / encoding-change)."""
    target_digest = ContentDigest.of(target_bytes)
    try:
        patch = SourcePatch.new(
            base.digest(),
            target_digest,
            base.encoding_facts(),
            replacements,
            metadata,
            limits,
        )
        patch.apply(base, limits)
    except SourcePatchError as error:
        return _expect_code(vector, error.code)
    return "apply must fail"


def _resource_case(vector: runner.Case) -> str | None:
    text = compare.string_field(vector.input, "raw_hex")
    if text is None:
        return "missing input.raw_hex"
    limits = SourceLimits()
    max_raw = compare.integer_field(vector.input, "max_raw_bytes")
    if max_raw is not None:
        limits = replace(limits, max_raw_bytes=max_raw)
    max_decoded = compare.integer_field(vector.input, "max_decoded_utf8_bytes")
    if max_decoded is not None:
        limits = replace(limits, max_decoded_utf8_bytes=max_decoded)
    max_scalars = compare.integer_field(vector.input, "max_decoded_scalars")
    if max_scalars is not None:
        limits = replace(limits, max_decoded_scalars=max_scalars)
    request = _encoding_request(vector)
    if request is None:
        return "missing or invalid input.encoding"
    try:
        SourceSnapshot.from_raw(bytes.fromhex(text), request, limits)
    except SourceError as error:
        return _expect_code(vector, error.code)
    return "snapshot construction must fail"


def _replacement_values(vector: runner.Case):
    values = compare.sequence_field(vector.input, "replacements")
    if values is None:
        return None, "input.replacements must be a Sequence"
    replacements = []
    for value in values:
        start = compare.integer_field(value, "old_start")
        end = compare.integer_field(value, "old_end")
        original = compare.string_field(value, "original_hex")
        replacement = compare.string_field(value, "replacement_hex")
        if start is None or end is None or original is None or replacement is None:
            return None, "replacement facts must be present"
        replacements.append(
            SourceReplacement.new(
                start, end, bytes.fromhex(original), bytes.fromhex(replacement)
            )
        )
    return replacements, None


def _patch_limits(vector: runner.Case) -> SourcePatchLimits:
    limits = SourcePatchLimits()
    max_replacements = compare.integer_field(vector.input, "max_replacements")
    if max_replacements is not None:
        limits = replace(limits, max_replacements=max_replacements)
    max_patch_bytes = compare.integer_field(vector.input, "max_patch_bytes")
    if max_patch_bytes is not None:
        limits = replace(limits, max_patch_bytes=max_patch_bytes)
    return limits


def _expect_code(vector: runner.Case, actual: str) -> str | None:
    expected = compare.string_field(vector.expected, "code")
    if expected is None:
        return "missing expected.code"
    return compare.require_equal(actual, expected, "error code")


runner.register_suite("source-v1.json", "consema.source.conformance@1", "", 28, run)
