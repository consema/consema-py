"""Intent documents for the CLI machine records (RFC 0015 §4/§8/§9).

Frozen facts: the eleven commands and their payload schemas (§6.1), the
six exit classes and the classification table (§5.1/§5.2), the envelope
shape of core.cli-output@1 (§4), the per-status presence rules of the plan
(§8.2) and result (§9.2) manifests, and the SemVer-core product_version
shape (§3.3).
"""

import pytest

from consema.core import PortableValue
from consema.protocol import (
    BatchPlanFileEntry,
    BatchPlanFileStatus,
    BatchPlanMessage,
    BatchResultFileEntry,
    BatchResultFileStatus,
    BatchResultMessage,
    CliCommand,
    CliOutputMessage,
    ContentDigest,
    ErrorCodeRegistry,
    ExitClass,
    ProtocolError,
    ProtocolErrorKind,
    Redaction,
    classify_error_code,
    exit_code,
    parse_cli_command,
)
from consema.protocol.cli import (
    EncodingFacts,
    FormatOperationId,
    EditOperationSummary,
    SourceEncoding,
    SourcePatch,
    SourceReplacement,
    windows_code_page_from_number,
)
from consema.protocol.diagnostic import Diagnostic
from consema.protocol.error_registry import DiagnosticCategory
from consema.protocol.exit_class import parse_exit_class
from consema.protocol.limits import ProtocolLimits
from consema.protocol.registry_descriptor import ProfileReference

LIMITS = ProtocolLimits()


def test_eleven_commands_and_their_payload_schemas():
    assert [command.value for command in CliCommand] == [
        "inspect", "capabilities", "query", "project", "materialize", "convert",
        "edit", "plan", "apply", "conformance", "explain",
    ]
    assert parse_cli_command("plan") is CliCommand.PLAN
    assert parse_cli_command("nope") is None
    from consema.protocol import payload_schemas

    assert payload_schemas(CliCommand.PLAN) == ["core.batch-plan@1"]
    assert payload_schemas(CliCommand.APPLY) == ["core.batch-result@1"]
    assert "core.query-result@1" in payload_schemas(CliCommand.QUERY)


def test_redaction_invariant():
    assert Redaction(True, 3).redacted
    assert Redaction(False, 0).count == 0
    with pytest.raises(ProtocolError) as caught:
        Redaction(True, 0)
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def test_semantic_version_product_version():
    for good in ("0.14.0", "1.2.3", "0.14.0-rc.1", "1.0.0-beta.2"):
        CliOutputMessage(
            CliCommand.PLAN, ExitClass.SUCCESS, good,
            _plan_payload(), [], Redaction(False, 0),
        )
    for bad in ("1.2", "01.2.3", "1.2.3+build", "1.2.3-", "1.2.3-01"):
        with pytest.raises(ProtocolError) as caught:
            CliOutputMessage(
                CliCommand.PLAN, ExitClass.SUCCESS, bad,
                _plan_payload(), [], Redaction(False, 0),
            )
        assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def _plan_payload() -> PortableValue:
    return PortableValue.object(
        [
            ("schema", PortableValue.string("core.batch-plan@1")),
            ("product_version", PortableValue.string("0.14.0")),
            ("command", PortableValue.string("plan")),
            ("files", PortableValue.sequence([])),
        ]
    )


def test_cli_output_envelope_round_trip():
    message = CliOutputMessage(
        CliCommand.PLAN, ExitClass.SUCCESS, "0.14.0", _plan_payload(), [], Redaction(False, 0)
    )
    value = message.to_value()
    decoded = CliOutputMessage.from_value(value)
    assert decoded.command is CliCommand.PLAN
    assert decoded.exit_class is ExitClass.SUCCESS
    assert decoded.product_version == "0.14.0"
    assert decoded.redaction == Redaction(False, 0)
    stream = message.to_json(LIMITS)
    assert CliOutputMessage.from_json(stream, LIMITS).product_version == "0.14.0"
    pvce_stream = message.to_pvce(LIMITS)
    assert CliOutputMessage.from_pvce(pvce_stream, LIMITS).command is CliCommand.PLAN


def test_cli_output_rejects_payload_schema_mismatch():
    payload = PortableValue.object([("schema", PortableValue.string("cli.inspect@1"))])
    with pytest.raises(ProtocolError) as caught:
        CliOutputMessage(
            CliCommand.PLAN, ExitClass.SUCCESS, "0.14.0", payload, [], Redaction(False, 0)
        )
    assert caught.value.kind is ProtocolErrorKind.SCHEMA_MISMATCH
    assert "not published by plan" in caught.value.detail


def test_cli_output_revalidates_diagnostics_against_registry():
    # The envelope re-validates every diagnostic's code/category against its
    # registry: "cli.data.io@1" is a v7-only code, so a v6 envelope rejects it.
    diagnostic = _make_diagnostic()
    with pytest.raises(ProtocolError) as caught:
        CliOutputMessage(
            CliCommand.PLAN, ExitClass.SUCCESS, "0.14.0",
            _plan_payload(), [diagnostic], Redaction(False, 0),
            registry=ErrorCodeRegistry(6),
        )
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE


def _make_diagnostic():
    from consema.protocol import Severity

    return Diagnostic(
        code="cli.data.io@1",
        category=DiagnosticCategory.ENCODING,
        severity=Severity.ERROR,
        primary=None,
        related=[],
        arguments={},
        notes=[],
        fixes=[],
        occurrence=1,
        registry=ErrorCodeRegistry(7),
    )


def _digest_of(data: bytes) -> ContentDigest:
    return ContentDigest.of(data)


def _patch(base: bytes, target: bytes) -> SourcePatch:
    return SourcePatch(
        base_digest=_digest_of(base),
        target_digest=_digest_of(target),
        encoding=EncodingFacts(
            profile_default=SourceEncoding("Utf8", None),
            bom_policy="TreatAsContent",
            bom=None,
            declaration=None,
            caller_override=None,
            selected=SourceEncoding("Utf8", None),
        ),
        replacements=[],
        metadata={},
    )


def test_batch_plan_planned_entry_constraints():
    planned = BatchPlanFileEntry(
        path="a.toml",
        status=BatchPlanFileStatus.PLANNED,
        profile=ProfileReference("toml.1.0", 1),
        source_digest=_digest_of(b"base"),
        operations=[EditOperationSummary(FormatOperationId("edit.set", 1), {"field": "x"})],
        source_patch=_patch(b"base", b"target"),
        failure_code=None,
        diagnostics=None,
    )
    message = BatchPlanMessage("0.14.0", [planned])
    value = message.to_value()
    decoded = BatchPlanMessage.from_value(value)
    assert decoded.files[0].path == "a.toml"
    assert decoded.files[0].status is BatchPlanFileStatus.PLANNED
    assert decoded.files[0].source_digest == _digest_of(b"base")

    # A planned entry whose source_digest does not equal the patch base
    # digest is rejected.
    with pytest.raises(ProtocolError) as caught:
        BatchPlanFileEntry(
            path="a.toml",
            status=BatchPlanFileStatus.PLANNED,
            profile=ProfileReference("toml.1.0", 1),
            source_digest=_digest_of(b"other"),
            operations=[],
            source_patch=_patch(b"base", b"target"),
            failure_code=None,
            diagnostics=None,
        )
    assert "source_digest must equal source_patch.base_digest" in caught.value.detail


def test_batch_plan_failed_entry_constraints():
    failed = BatchPlanFileEntry(
        path="a.toml",
        status=BatchPlanFileStatus.FAILED,
        profile=None,
        source_digest=None,
        operations=None,
        source_patch=None,
        failure_code="cli.limit.file-size@1",
        diagnostics=[_make_diagnostic()],
    )
    message = BatchPlanMessage("0.14.0", [failed])
    value = message.to_value()
    decoded = BatchPlanMessage.from_value(value)
    assert decoded.files[0].failure_code == "cli.limit.file-size@1"

    with pytest.raises(ProtocolError):
        BatchPlanFileEntry(
            path="a.toml",
            status=BatchPlanFileStatus.FAILED,
            profile=None,
            source_digest=None,
            operations=None,
            source_patch=None,
            failure_code=None,
            diagnostics=[],
        )


def test_batch_plan_json_transport_carries_bytes_leaves():
    base = b"a = 1\n"
    target = b"a = 2\n"
    patch = SourcePatch(
        base_digest=_digest_of(base),
        target_digest=_digest_of(target),
        encoding=EncodingFacts(
            profile_default=SourceEncoding("Utf8", None),
            bom_policy="TreatAsContent",
            bom=None,
            declaration=None,
            caller_override=None,
            selected=SourceEncoding("Utf8", None),
        ),
        replacements=[
            SourceReplacement(
                old_start=4, old_end=5, original=b"1", replacement=b"2",
                redact_original=False, redact_replacement=False,
            )
        ],
        metadata={},
    )
    planned = BatchPlanFileEntry(
        path="a.toml",
        status=BatchPlanFileStatus.PLANNED,
        profile=ProfileReference("toml.1.0", 1),
        source_digest=_digest_of(base),
        operations=[EditOperationSummary(FormatOperationId("edit.set", 1), {"field": "a"})],
        source_patch=patch,
        failure_code=None,
        diagnostics=None,
    )
    message = BatchPlanMessage("0.14.0", [planned])
    stream = message.to_json(LIMITS)
    decoded = BatchPlanMessage.from_json(stream, LIMITS)
    assert decoded.files[0].source_patch.replacements[0].original == b"1"
    assert decoded.files[0].source_patch.replacements[0].replacement == b"2"
    # The value and PVCE transports carry the same bytes.
    assert BatchPlanMessage.from_value(message.to_value()).files[0].source_patch.replacements[0].replacement == b"2"
    assert BatchPlanMessage.from_pvce(message.to_pvce(LIMITS), LIMITS).files[0].path == "a.toml"


def test_batch_result_status_presence_rules():
    completed = BatchResultFileEntry(
        "a.toml", BatchResultFileStatus.COMPLETED, None, _digest_of(b"target"), False
    )
    pending = BatchResultFileEntry("b.toml", BatchResultFileStatus.PENDING, None, None, False)
    message = BatchResultMessage("0.14.0", [completed, pending])
    decoded = BatchResultMessage.from_value(message.to_value())
    assert decoded.files[0].status is BatchResultFileStatus.COMPLETED
    assert decoded.files[1].status is BatchResultFileStatus.PENDING

    with pytest.raises(ProtocolError):
        BatchResultFileEntry("a.toml", BatchResultFileStatus.COMPLETED, "cli.write.io@1", None, False)
    with pytest.raises(ProtocolError):
        BatchResultFileEntry("a.toml", BatchResultFileStatus.FAILED, None, None, False)
    with pytest.raises(ProtocolError):
        BatchResultFileEntry("a.toml", "unknown-status", None, None, False)


def test_batch_plan_json_command_must_be_plan():
    payload = PortableValue.object(
        [
            ("schema", PortableValue.string("core.batch-plan@1")),
            ("product_version", PortableValue.string("0.14.0")),
            ("command", PortableValue.string("apply")),
            ("files", PortableValue.sequence([])),
        ]
    )
    with pytest.raises(ProtocolError) as caught:
        BatchPlanMessage.from_value(payload)
    assert 'expected command "plan"' in caught.value.detail


def test_windows_code_page_registry():
    assert windows_code_page_from_number(1252) == 1252
    assert windows_code_page_from_number(65001) == 65001
    assert windows_code_page_from_number(9999) is None


def test_digest_sha256():
    digest = ContentDigest.of(b"abc")
    assert digest.algorithm == "sha256"
    assert digest.hex() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert digest == ContentDigest.of(b"abc")


def test_exit_classes_and_codes():
    assert exit_code(ExitClass.SUCCESS) == 0
    assert exit_code(ExitClass.USAGE) == 1
    assert exit_code(ExitClass.DATA) == 2
    assert exit_code(ExitClass.LIMIT) == 3
    assert exit_code(ExitClass.PRECONDITION) == 4
    assert exit_code(ExitClass.INTERNAL) == 5
    assert parse_exit_class("usage") is ExitClass.USAGE
    assert parse_exit_class("nope") is None


def test_classify_error_code_table():
    # RFC 0015 §5.2 family table.
    assert classify_error_code("cli.usage.unknown-command@1") is ExitClass.USAGE
    assert classify_error_code("cli.data.io@1") is ExitClass.DATA
    assert classify_error_code("cli.detection.ambiguous@1") is ExitClass.DATA
    assert classify_error_code("cli.limit.file-size@1") is ExitClass.LIMIT
    assert classify_error_code("core.protocol.resource-limit@1") is ExitClass.LIMIT
    assert classify_error_code("json.parse.resource-limit@1") is ExitClass.LIMIT
    assert classify_error_code("cli.write.permission@1") is ExitClass.PRECONDITION
    assert classify_error_code("cli.interrupted.signal@1") is ExitClass.PRECONDITION
    assert classify_error_code("core.source.patch-base-mismatch@1") is ExitClass.PRECONDITION
    assert classify_error_code("core.edit.conflicting-edits@1") is ExitClass.PRECONDITION
    assert classify_error_code("cli.internal.unclassified@1") is ExitClass.INTERNAL
    assert classify_error_code("core.protocol.invalid-json@1") is ExitClass.DATA
    assert classify_error_code("core.source.invalid-sequence@1") is ExitClass.DATA
    # Format-layer codes pass through unchanged (never invent classes).
    assert classify_error_code("toml.parse.syntax@1") is ExitClass.DATA
