"""The frozen CLI exit classes and the pure error classification.

Authority: RFC 0015 §5 (the six exit classes, their codes 0-5, and the
stable family mapping of §5.1/§5.2); crates/consema-protocol/src/exit_class.rs.
Go (go/protocol/exit_class.go) is a cross-reference only. ``classify_error_code``
is a pure function implemented once in the protocol layer (RFC 0016 §6: "the
SDK itself never classifies"); the CLI applies the mapped code only.
"""

from __future__ import annotations

import enum


class ExitClass(enum.Enum):
    """One of the six frozen CLI exit classes (RFC 0015 §5.1)."""

    SUCCESS = "success"
    USAGE = "usage"
    DATA = "data"
    LIMIT = "limit"
    PRECONDITION = "precondition"
    INTERNAL = "internal"


_EXIT_CODES = {
    ExitClass.SUCCESS: 0,
    ExitClass.USAGE: 1,
    ExitClass.DATA: 2,
    ExitClass.LIMIT: 3,
    ExitClass.PRECONDITION: 4,
    ExitClass.INTERNAL: 5,
}


def exit_code(exit_class: ExitClass) -> int:
    """The frozen process exit code for the class; codes 6-255 are reserved
    and never produced by v1."""
    return _EXIT_CODES[exit_class]


def parse_exit_class(name: str) -> ExitClass | None:
    try:
        return ExitClass(name)
    except ValueError:
        return None


def classify(exit_class: ExitClass) -> int:
    """Classifies one exit class into its frozen process exit code. The exit
    code expresses whether the operation produced a complete result, never
    the health of the data itself (RFC 0015 §5.1)."""
    return exit_code(exit_class)


def classify_error_code(code: str) -> ExitClass:
    """Classifies a stable error code into its frozen exit class — the
    exhaustive family table of RFC 0015 §5.2:

    - ``cli.usage.*`` -> usage (1);
    - ``cli.data.*`` and ``cli.detection.*`` (ambiguity) -> data (2);
    - ``cli.limit.*`` and any ``*-resource-limit@1`` (core or format-local)
      -> limit (3);
    - ``cli.write.*``, ``cli.interrupted.*``, the
      ``core.source.patch-*-mismatch@1`` precondition family, and
      ``core.edit.*`` conflicts -> precondition (4);
    - ``cli.internal.unclassified@1`` -> internal (5);
    - ``core.protocol.*`` strict-decode failures -> data (2), with
      ``core.protocol.resource-limit@1`` overridden to limit (3);
    - ``core.source.*`` diagnostics carried by FatalFormationFailure ->
      data (2);
    - any code outside these frozen families -> data (2): the operation did
      not produce a complete result. Format-layer codes pass through
      unchanged; they never invent new classes.

    Report-as-result outcomes (Recovered state reports, ambiguity fact
    reports, unauthorized-loss reports) classify as success (0) at the
    outcome level, not through error codes.
    """
    if code.startswith("cli.usage."):
        return ExitClass.USAGE
    if code.startswith("cli.data.") or code.startswith("cli.detection."):
        return ExitClass.DATA
    if code.startswith("cli.limit."):
        return ExitClass.LIMIT
    if code.startswith("cli.write.") or code.startswith("cli.interrupted."):
        return ExitClass.PRECONDITION
    if code.startswith("cli.internal."):
        return ExitClass.INTERNAL
    if code.endswith(".resource-limit@1"):
        return ExitClass.LIMIT
    if code.startswith("core.source.patch-") and code.endswith("-mismatch@1"):
        return ExitClass.PRECONDITION
    if code.startswith("core.edit."):
        return ExitClass.PRECONDITION
    return ExitClass.DATA
