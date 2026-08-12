"""Intent documents for the `core.diagnostic@1` record.

Construction validates the code against the frozen error registry and the
category against the registry record (RFC 0016 §6: unknown code or category
contradiction is a protocol error; diagnostic.rs:336-351).
"""

import pytest

from consema.core import PortableValue
from consema.protocol import (
    Diagnostic,
    DiagnosticCategory,
    ErrorCodeRegistry,
    FixApplicability,
    ProtocolError,
    ProtocolErrorKind,
    Severity,
    SourceLocation,
)
from consema.protocol.diagnostic import (
    FixProposal,
    RelatedSourceLocation,
    diagnostic_node,
    parse_diagnostic_node,
)

REGISTRY = ErrorCodeRegistry(7)


def _diagnostic(**overrides) -> Diagnostic:
    args = dict(
        code="cli.write.permission@1",
        category=DiagnosticCategory.EDIT,
        severity=Severity.ERROR,
        primary=SourceLocation("source:one", 0, 5),
        related=[RelatedSourceLocation("cause", SourceLocation("source:one", 6, 9))],
        arguments={"file": "a.toml"},
        notes=["note-id"],
        fixes=[FixProposal("fix:one", FixApplicability.MACHINE_APPLICABLE, None, b"new")],
        occurrence=1,
    )
    args.update(overrides)
    return Diagnostic(registry=REGISTRY, **args)


def test_construction_validates_registered_code_and_category():
    diagnostic = _diagnostic()
    assert diagnostic.code == "cli.write.permission@1"
    assert diagnostic.category is DiagnosticCategory.EDIT


def test_unknown_code_is_rejected():
    with pytest.raises(ProtocolError) as caught:
        _diagnostic(code="example.not-registered@1")
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE
    assert "unregistered public code" in caught.value.detail


def test_category_contradiction_is_rejected():
    with pytest.raises(ProtocolError) as caught:
        _diagnostic(category=DiagnosticCategory.SYNTAX)
    assert caught.value.kind is ProtocolErrorKind.INVALID_VALUE
    assert "contradicts" in caught.value.detail


def test_value_level_round_trip():
    diagnostic = _diagnostic()
    value = diagnostic.to_value()
    decoded = Diagnostic.from_value(value, REGISTRY)
    assert decoded.code == diagnostic.code
    assert decoded.category is diagnostic.category
    assert decoded.severity is diagnostic.severity
    assert decoded.primary == diagnostic.primary
    assert decoded.arguments == diagnostic.arguments
    assert decoded.notes == diagnostic.notes
    assert decoded.occurrence == diagnostic.occurrence
    assert decoded.fixes[0].replacement == b"new"


def test_json_tree_level_round_trip_with_bytes_fixes():
    diagnostic = _diagnostic()
    node = diagnostic_node(diagnostic)
    decoded = parse_diagnostic_node(node, "$", REGISTRY)
    assert decoded.code == diagnostic.code
    assert decoded.fixes[0].replacement == b"new"


def test_occurrence_and_arguments_are_deterministic():
    diagnostic = _diagnostic(arguments={"b": "2", "a": "1"})
    value = diagnostic.to_value()
    arguments = dict(value.as_object())["arguments"]
    # The wire form sorts argument names (the Rust BTreeMap ordering).
    assert [key for key, _ in arguments.as_object()] == ["a", "b"]
