"""Shared case-file loading for the Python differential harnesses
(docs/five-language-ci-design.md §3.4/§3.5).

The differential case sets are language-neutral (kind/format/profile/source/
steps) and currently live under ``go/conformance/differential/`` (the shared
``conformance/differential/`` migration is the second-language merge batch,
five-language-ci-design.md §3.5). This module reads them read-only from the
checked-in Go tree, exactly as the Go tests embed them, and applies the
integrity guards (manifest id, case-count floor, unique ids) that every
language harness pins.
"""

from __future__ import annotations

import json
import os

# The frozen case-count pins (five-language-ci-design.md §3.5: "单文件、五处
# 共钉，任何一侧漂移即红"): the checked-in files must carry exactly these
# counts today and never drop below the shared floor.
BYTE_PARITY_EXACT = 68
NORMALIZED_EXACT = 108
PROTOCOL_EXCHANGE_EXACT = 83
MIN_CASE_COUNT = 40


class CaseFileError(ValueError):
    """One integrity violation of a checked-in differential case file."""


def repository_root() -> str:
    """The repository root (this module lives at
    python/src/consema/differential, four levels below the root)."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(package_dir)))
    )


def differential_dir() -> str:
    """The checked-in differential case directory (the Go tree today)."""
    return os.path.join(repository_root(), "go", "conformance", "differential")


def case_file_path(name: str) -> str:
    """One checked-in case file path."""
    return os.path.join(differential_dir(), name)


def load_case_file(relative: str, manifest: str, exact: int) -> list[dict]:
    """Loads and validates one differential case file: manifest id, exact
    case count (with the shared >= 40 floor), and unique ids.

    Returns the case list (plain dicts, the file's JSON objects)."""
    path = case_file_path(relative)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as error:
        raise CaseFileError(f"cannot read differential case file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CaseFileError(f"{path} is not valid JSON: {error}") from error
    if document.get("manifest") != manifest:
        raise CaseFileError(
            f"{path} manifest = {document.get('manifest')!r}, want {manifest!r}"
        )
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise CaseFileError(f"{path} has no cases array")
    if len(cases) < MIN_CASE_COUNT:
        raise CaseFileError(
            f"{path} has {len(cases)} cases, want >= {MIN_CASE_COUNT} (the differential input set)"
        )
    if len(cases) != exact:
        raise CaseFileError(
            f"{path} has {len(cases)} cases, want exactly {exact} (the frozen count)"
        )
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise CaseFileError(f"{path} has a case without an id")
        if case_id in seen:
            raise CaseFileError(f"{path} has duplicate case id {case_id!r}")
        seen.add(case_id)
    return cases


def read_hex_file(directory: str, case_id: str) -> bytes:
    """Reads one golden ``<case-id>.hex`` file."""
    with open(os.path.join(directory, case_id + ".hex"), "r", encoding="utf-8") as handle:
        text = handle.read()
    return bytes.fromhex(text.strip())


def read_evidence_file(directory: str, case_id: str) -> list[str]:
    """Reads one evidence ``<case-id>.txt`` file into fact lines (the shared
    reader of both directions, mirroring the Go
    ``splitEvidenceLines``/Rust consume-mode reader)."""
    with open(os.path.join(directory, case_id + ".txt"), "r", encoding="utf-8") as handle:
        text = handle.read()
    content = text.rstrip("\r\n")
    if content == "":
        return []
    return content.split("\n")
