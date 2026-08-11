"""Suite ``consema.cli.conformance@1`` (cli-v1.json, 40 cases): the
``core.cli-output@1`` envelope, the exit-code classification table, the
``core.batch-plan@1`` and ``core.batch-result@1`` manifests, the redaction
record contract, and the transport/patch budgets. Dispatch is by capability,
mirroring go/conformance/cli_v1.go.

Every case is driven by tagged JSON text: the input bytes are decoded
through the protocol v7 records and the re-encode must reproduce the input
bytes exactly. The Python ``consema.protocol.cli`` record codecs implement
the byte-exact transports; the patch-replacement budget (cli.limit@1) is
enforced runner-side because the Python batch-plan decoder does not carry
source-patch limits.
"""

from __future__ import annotations

from consema.conformance import compare
from consema.conformance import runner
from consema.core.equal import equal as core_equal
from consema.core.value import Kind, PortableValue
from consema.protocol.canonical import decode_json, encode_json, encode_pvce
from consema.protocol.cli import (
    BatchPlanMessage,
    BatchPlanFileStatus,
    BatchResultMessage,
    BatchResultFileStatus,
    CliOutputMessage,
    Redaction,
    parse_cli_command,
)
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, resource
from consema.protocol.exit_class import classify_error_code, exit_code, parse_exit_class
from consema.protocol.limits import ProtocolLimits


def run(conformance_runner: runner.Runner, data: runner.SuiteData) -> runner.SuiteReport:
    report = runner.SuiteReport(suite=data.suite, expected_cases=len(data.cases))
    handlers = {
        "cli.envelope@1": _envelope,
        "cli.exit-code@1": _exit_code,
        "cli.batch-plan@1": _batch_plan,
        "cli.batch-result@1": _batch_result,
        "cli.redaction@1": _redaction,
        "cli.limit@1": _limit,
    }
    for vector in data.cases:
        handler = handlers.get(vector.capability)
        if handler is None:
            report.failed.append(
                runner.CaseFailure(id=vector.id, message=f"unknown capability {vector.capability}")
            )
            continue
        message = handler(vector)
        if message:
            report.failed.append(runner.CaseFailure(id=vector.id, message=message))
        else:
            report.passed.append(vector.id)
    return report


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _input_json(vector: runner.Case) -> bytes:
    text = compare.string_field(vector.input, "json")
    if text is None:
        raise ValueError("missing input.json")
    return text.encode("utf-8")


def _expect_rejection(vector: runner.Case, error: BaseException | None) -> str | None:
    if error is None:
        return "record must be rejected"
    if not isinstance(error, ProtocolError):
        return f"unexpected error type: {error!r}"
    expected_code = compare.string_field(vector.expected, "error_code")
    if error.code != expected_code:
        return f"rejection {error.code} != {expected_code}"
    expected_path = compare.string_field(vector.expected, "error_path")
    if expected_path is not None and error.path != expected_path:
        return f"rejection path {error.path} != {expected_path}"
    return None


def _facts_equal(actual: PortableValue, expected: PortableValue) -> bool:
    return core_equal(actual, expected)


# ---------------------------------------------------------------------------
# cli.envelope@1
# ---------------------------------------------------------------------------


def _envelope(vector: runner.Case) -> str | None:
    json_bytes = _input_json(vector)
    limits = ProtocolLimits()
    if compare.object_field(vector.expected, "error_code") is not None:
        try:
            CliOutputMessage.from_json(json_bytes, limits)
        except ProtocolError as error:
            return _expect_rejection(vector, error)
        return "record must be rejected"
    try:
        envelope = CliOutputMessage.from_json(json_bytes, limits)
    except ProtocolError as error:
        return f"envelope decode: {error}"
    try:
        re_encoded = envelope.to_json(limits)
    except ProtocolError as error:
        return f"envelope re-encode: {error}"
    if re_encoded != json_bytes:
        return "envelope re-encode must reproduce the input bytes exactly"
    try:
        pvce = envelope.to_pvce(limits)
    except ProtocolError as error:
        return f"envelope PVCE encode: {error}"
    expected_pvce = compare.string_field(vector.expected, "pvce_hex")
    if expected_pvce is not None and pvce.hex() != expected_pvce:
        return f"pvce_hex {pvce.hex()} != {expected_pvce}"
    try:
        decoded = CliOutputMessage.from_pvce(pvce, limits)
    except ProtocolError as error:
        return f"envelope PVCE decode: {error}"
    if not _facts_equal(decoded.to_value(), envelope.to_value()):
        return "dual transport must decode to the same envelope"
    again = envelope.to_json(limits)
    if again != re_encoded:
        return "envelope JSON is not byte-deterministic"
    return _assert_envelope_facts(envelope, vector)


def _assert_envelope_facts(envelope: CliOutputMessage, vector: runner.Case) -> str | None:
    expected_command = compare.string_field(vector.expected, "command")
    if expected_command is not None and envelope.command.value != expected_command:
        return f"command {envelope.command.value} != {expected_command}"
    expected_exit_class = compare.string_field(vector.expected, "exit_class")
    if expected_exit_class is not None and envelope.exit_class.value != expected_exit_class:
        return f"exit_class {envelope.exit_class.value} != {expected_exit_class}"
    expected_version = compare.string_field(vector.expected, "product_version")
    if expected_version is not None and envelope.product_version != expected_version:
        return "product_version mismatch"
    expected_schema = compare.string_field(vector.expected, "payload_schema")
    if expected_schema is not None:
        payload = envelope.payload
        if payload.kind is not Kind.OBJECT or not payload.as_object():
            return "payload has no schema first field"
        first = payload.as_object()[0]
        if first[0] != "schema" or first[1].kind is not Kind.STRING or first[1].as_string() != expected_schema:
            return "payload schema mismatch"
    expected_redacted = compare.boolean_field(vector.expected, "redacted")
    if expected_redacted is not None and envelope.redaction.redacted != expected_redacted:
        return "redaction.redacted mismatch"
    expected_count = compare.integer_field(vector.expected, "count")
    if expected_count is not None and envelope.redaction.count != expected_count:
        return "redaction.count mismatch"
    expected_diagnostics = compare.integer_field(vector.expected, "diagnostics_count")
    if expected_diagnostics is not None and len(envelope.diagnostics) != expected_diagnostics:
        return "diagnostics count mismatch"
    expected_code = compare.string_field(vector.expected, "diagnostic_code")
    if expected_code is not None:
        codes = [diagnostic.code for diagnostic in envelope.diagnostics]
        if expected_code not in codes:
            return f"diagnostic {expected_code} not found"
    return None


def _payload_contains_string(value: PortableValue, needle: str) -> bool:
    if value.kind is Kind.STRING:
        return value.as_string() == needle
    if value.kind is Kind.OBJECT:
        return any(_payload_contains_string(item, needle) for _, item in value.as_object())
    if value.kind is Kind.SEQUENCE:
        return any(_payload_contains_string(item, needle) for item in value.as_sequence())
    return False


# ---------------------------------------------------------------------------
# cli.exit-code@1
# ---------------------------------------------------------------------------


def _exit_code(vector: runner.Case) -> str | None:
    names = compare.string_sequence(vector.input, "names")
    if names is not None:
        codes = compare.integer_sequence(vector.input, "codes")
        if codes is None:
            return "missing input.codes"
        if len(names) != len(codes):
            return "class table count mismatch"
        for index, (name, code) in enumerate(zip(names, codes)):
            exit_class = parse_exit_class(name)
            if exit_class is None:
                return f"unknown class {name}"
            if exit_code(exit_class) != code:
                return f"class table row {index}: {name} maps to {exit_code(exit_class)} instead of {code}"
        return None
    codes = compare.string_sequence(vector.input, "codes")
    classes = compare.string_sequence(vector.expected, "classes")
    if codes is None or classes is None:
        return "missing input.codes or expected.classes"
    if len(codes) != len(classes):
        return "code/class count mismatch"
    for index, code in enumerate(codes):
        actual = classify_error_code(code)
        if actual.value != classes[index]:
            return f"matrix row {index}: {code} classifies as {actual.value} instead of {classes[index]}"
    return None


# ---------------------------------------------------------------------------
# cli.batch-plan@1
# ---------------------------------------------------------------------------


def _batch_plan(vector: runner.Case) -> str | None:
    json_bytes = _input_json(vector)
    limits = ProtocolLimits()
    try:
        record = decode_json(json_bytes, limits)
    except ProtocolError as error:
        return f"plan transport decode: {error}"
    if compare.object_field(vector.expected, "error_code") is not None:
        try:
            BatchPlanMessage.from_json(json_bytes, limits)
        except ProtocolError as error:
            return _expect_rejection(vector, error)
        return "record must be rejected"
    try:
        plan = BatchPlanMessage.from_json(json_bytes, limits)
    except ProtocolError as error:
        return f"plan decode: {error}"
    transport = plan.to_json(limits)
    if transport != json_bytes:
        return "plan record must re-encode to the exact input bytes"
    re_encoded = plan.to_value()
    if not _facts_equal(re_encoded, record):
        return "plan re-encode must reproduce the record exactly"
    expected_pvce = compare.string_field(vector.expected, "pvce_hex")
    if expected_pvce is not None:
        pvce = encode_pvce(record, limits)
        if pvce.hex() != expected_pvce:
            return f"plan pvce_hex {pvce.hex()} != {expected_pvce}"
    expected_version = compare.string_field(vector.expected, "product_version")
    if expected_version is not None and plan.product_version != expected_version:
        return "plan product_version mismatch"
    expected_statuses = compare.string_sequence(vector.expected, "statuses")
    if expected_statuses is not None:
        if len(plan.files) != len(expected_statuses):
            return f"plan file count {len(plan.files)} != {len(expected_statuses)}"
        for index, entry in enumerate(plan.files):
            if entry.status.value != expected_statuses[index]:
                return f"plan status {entry.status.value} != {expected_statuses[index]}"
    expected_digest = compare.string_field(vector.expected, "source_digest_hex")
    if expected_digest is not None:
        entry = _planned_entry(plan)
        if entry is None or entry.source_digest is None or entry.source_digest.hex() != expected_digest:
            return "plan source_digest mismatch"
    expected_target = compare.string_field(vector.expected, "target_digest_hex")
    if expected_target is not None:
        entry = _planned_entry(plan)
        patch = entry.source_patch if entry is not None else None
        if patch is None or patch.target_digest.hex() != expected_target:
            return "plan patch target_digest mismatch"
    expected_failure = compare.string_field(vector.expected, "failure_code")
    if expected_failure is not None:
        found = any(
            entry.status is BatchPlanFileStatus.FAILED and entry.failure_code == expected_failure
            for entry in plan.files
        )
        if not found:
            return "plan failure_code mismatch"
    return None


def _planned_entry(plan: BatchPlanMessage):
    for entry in plan.files:
        if entry.status is BatchPlanFileStatus.PLANNED:
            return entry
    return None


# ---------------------------------------------------------------------------
# cli.batch-result@1
# ---------------------------------------------------------------------------


def _batch_result(vector: runner.Case) -> str | None:
    if compare.object_field(vector.input, "branches") is not None:
        return _recovery_rule(vector)
    json_bytes = _input_json(vector)
    limits = ProtocolLimits()
    try:
        record = decode_json(json_bytes, limits)
    except ProtocolError as error:
        return f"result transport decode: {error}"
    if compare.object_field(vector.expected, "error_code") is not None:
        try:
            BatchResultMessage.from_json(json_bytes, limits)
        except ProtocolError as error:
            return _expect_rejection(vector, error)
        return "record must be rejected"
    try:
        result = BatchResultMessage.from_json(json_bytes, limits)
    except ProtocolError as error:
        return f"result decode: {error}"
    transport = result.to_json(limits)
    if transport != json_bytes:
        return "result record must re-encode to the exact input bytes"
    re_encoded = result.to_value()
    if not _facts_equal(re_encoded, record):
        return "result re-encode must reproduce the record exactly"
    expected_pvce = compare.string_field(vector.expected, "pvce_hex")
    if expected_pvce is not None:
        pvce = encode_pvce(record, limits)
        if pvce.hex() != expected_pvce:
            return f"result pvce_hex {pvce.hex()} != {expected_pvce}"
    expected_version = compare.string_field(vector.expected, "product_version")
    if expected_version is not None and result.product_version != expected_version:
        return "result product_version mismatch"
    expected_statuses = compare.string_sequence(vector.expected, "statuses")
    if expected_statuses is not None:
        if len(result.files) != len(expected_statuses):
            return f"result file count {len(result.files)} != {len(expected_statuses)}"
        for index, entry in enumerate(result.files):
            if entry.status.value != expected_statuses[index]:
                return f"result status {entry.status.value} != {expected_statuses[index]}"
    expected_digest = compare.string_field(vector.expected, "target_digest_hex")
    if expected_digest is not None:
        found = any(
            entry.status is BatchResultFileStatus.COMPLETED
            and entry.target_digest is not None
            and entry.target_digest.hex() == expected_digest
            for entry in result.files
        )
        if not found:
            return "result target_digest mismatch"
    expected_redacted = compare.boolean_field(vector.expected, "redacted")
    if expected_redacted is not None:
        if not result.files or result.files[0].redacted != expected_redacted:
            return "result redacted mismatch"
    expected_failure = compare.string_field(vector.expected, "failure_code")
    if expected_failure is not None:
        found = any(
            entry.status in (BatchResultFileStatus.FAILED, BatchResultFileStatus.SKIPPED_STALE)
            and entry.failure_code == expected_failure
            for entry in result.files
        )
        if not found:
            return "result failure_code mismatch"
    return None


def _recovery_rule(vector: runner.Case) -> str | None:
    branches = compare.sequence_field(vector.input, "branches")
    if branches is None:
        return "missing input.branches"
    for index, branch in enumerate(branches):
        disk = compare.string_field(branch, "disk")
        outcome = compare.string_field(branch, "outcome")
        expected = {"source": "redo", "target": "skip", "other": "stale"}.get(disk)
        if expected is None:
            return f"unknown disk branch {disk}"
        if outcome != expected:
            return f"branch {index} outcome {outcome} != {expected}"
    illegal = compare.object_field(vector.input, "illegal_branch")
    if illegal is not None:
        disk = compare.string_field(illegal, "disk")
        if disk in ("source", "target", "other"):
            return f"branch {disk} must not be in the three-way rule"
    return None


# ---------------------------------------------------------------------------
# cli.redaction@1
# ---------------------------------------------------------------------------


def _redaction(vector: runner.Case) -> str | None:
    samples = compare.sequence_field(vector.input, "samples")
    if samples is not None:
        for index, sample in enumerate(samples):
            redacted = compare.boolean_field(sample, "redacted")
            count = compare.integer_field(sample, "count")
            valid = compare.boolean_field(sample, "valid")
            if redacted is None or count is None or valid is None:
                return f"sample {index} facts missing"
            try:
                Redaction(redacted, count)
            except ProtocolError:
                ok = False
            else:
                ok = True
            if ok != valid:
                return f"sample {index} Redaction({redacted}, {count}) validity mismatch"
        return None
    json_bytes = _input_json(vector)
    limits = ProtocolLimits()
    if compare.object_field(vector.expected, "original_hex") is not None:
        try:
            record = decode_json(json_bytes, limits)
        except ProtocolError as error:
            return f"plan transport decode: {error}"
        try:
            plan = BatchPlanMessage.from_json(json_bytes, limits)
        except ProtocolError as error:
            return f"plan decode: {error}"
        entry = _planned_entry(plan)
        if entry is None:
            return "no planned entry"
        patch = entry.source_patch
        if patch is None:
            return "planned entry without source_patch"
        if not patch.replacements:
            return "no replacement in patch"
        replacement = patch.replacements[0]
        expected_original = compare.string_field(vector.expected, "original_hex")
        if expected_original is not None and replacement.original.hex() != expected_original:
            return "patch original bytes changed"
        expected_replacement = compare.string_field(vector.expected, "replacement_hex")
        if expected_replacement is not None and replacement.replacement.hex() != expected_replacement:
            return "patch replacement bytes changed"
        if not _facts_equal(plan.to_value(), record):
            return "plan bytes are not preserved through the record"
        transport = encode_json(record, limits)
        if transport != json_bytes:
            return "plan record must re-encode to the exact input bytes"
        return None
    try:
        envelope = CliOutputMessage.from_json(json_bytes, limits)
    except ProtocolError as error:
        return f"envelope decode: {error}"
    message = _assert_envelope_facts(envelope, vector)
    if message:
        return message
    re_encoded = envelope.to_json(limits)
    if re_encoded != json_bytes:
        return "envelope re-encode must reproduce the input bytes exactly"
    placeholder = compare.string_field(vector.expected, "placeholder")
    if placeholder is not None and not _payload_contains_string(envelope.payload, placeholder):
        return "placeholder value changed through the transport"
    return None


# ---------------------------------------------------------------------------
# cli.limit@1
# ---------------------------------------------------------------------------


def _limit(vector: runner.Case) -> str | None:
    json_bytes = _input_json(vector)
    limits = ProtocolLimits()
    if classify_error_code("core.protocol.resource-limit@1").value != "limit":
        return "resource-limit must classify as limit"
    try:
        decode_json(json_bytes, limits)
    except ProtocolError as error:
        return f"transport decode: {error}"
    max_bytes = compare.integer_field(vector.input, "max_bytes")
    if max_bytes is not None:
        try:
            decode_json(json_bytes, ProtocolLimits(max_bytes=max_bytes))
        except ProtocolError as error:
            if error.kind is not ProtocolErrorKind.RESOURCE_LIMIT:
                return f"decode must fail with ResourceLimit, got {error}"
            return None
        return "payload must exceed the transport budget"
    # The patch-replacement budget: the Python batch-plan decoder carries no
    # source-patch limits, so the budget is enforced runner-side mirroring
    # the Go FromValueWithRegistryAndPatchLimits check (resource error at
    # "$.replacements" when the replacement count exceeds the budget).
    try:
        record = decode_json(json_bytes, limits)
    except ProtocolError as error:
        return f"plan transport decode: {error}"
    try:
        plan = BatchPlanMessage.from_value(record, ErrorCodeRegistry(7))
    except ProtocolError as error:
        return f"plan decode: {error}"
    try:
        _enforce_patch_budget(plan, 0)
    except ProtocolError as error:
        if error.kind is not ProtocolErrorKind.RESOURCE_LIMIT:
            return f"plan decode must fail with ResourceLimit, got {error}"
        return None
    return "plan decode must fail with ResourceLimit"


def _enforce_patch_budget(plan: BatchPlanMessage, max_replacements: int) -> None:
    """Raises the resource-limit rejection a patch-bounded plan decode would
    produce (cli.go FromValueWithRegistryAndPatchLimits)."""
    for entry in plan.files:
        if entry.source_patch is not None:
            count = len(entry.source_patch.replacements)
            if count > max_replacements:
                raise resource("$.replacements", "replacement count exceeds configured limit")


runner.register_suite(
    "cli-v1.json", "consema.cli.conformance@1", "core.semantic-model@7", 40, run
)
