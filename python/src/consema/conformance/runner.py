"""The Python conformance runner over the shared language-neutral vectors
(RFC 0016 §7; https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §2).

The runner executes the 18 shared vector suites (519 cases) from the
repository ``conformance/vectors`` directory, verifies the aggregate digest
against the Feature-Complete Manifest (fc-manifest-0.13.0.json), and
reports per-suite pass/fail with documented skips (never silent). The
vector files themselves are the authority: the runner holds no vector
content copy, but the suite/case counts and the aggregate digest are hard
pins inside this module (conformance/README.md rules 3-4; the pins live
here, the vector content is authoritative).

Suite-level fixed validations (mirroring the Go runner's ``runSuite``
fixed checks, symbol-anchored): the suite and semantic-model identifiers
(every suite whose vector file declares a semantic_model must carry the
same declaration in its registration — 12 of the 18 suites declare one),
case-ID uniqueness, the frozen case-count assertion, and unknown-case
rejection.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

from consema.conformance import loader
from consema.core.value import PortableValue

# The frozen aggregate digest and inventory pins (five-runner shared pin;
# https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §4.2; fc-manifest-0.13.0.json).
AGGREGATE_SHA256 = "cfd6e296da5b22b62d37b076d35bf6bbf58b0678ceddb37eea51a8b47200ab6a"
EXPECTED_SUITES = 18
EXPECTED_CASES = 519


@dataclass
class SkipRecord:
    """One documented skip: the case was not executed because its
    capability is not implemented.

    Note (G67, 2026-08-14): no SkipRecord is ever constructed in this
    codebase — every frozen (N, 0, 0) suite surface executes all of its
    cases, and any skip is a failure by the hard pin. The type is kept for
    API shape only (it is exported by consema.conformance.__init__).
    """

    id: str
    capability: str
    reason: str


@dataclass
class CaseFailure:
    """One failed case with its message."""

    id: str
    message: str


@dataclass
class SuiteReport:
    """The run report of one vector suite."""

    suite: str
    semantic_model: str = ""
    expected_cases: int = 0
    passed: list[str] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    failed: list[CaseFailure] = field(default_factory=list)

    def count_asserted(self) -> bool:
        """Whether the frozen case count matched the vector file."""
        return self.expected_cases == len(self.passed) + len(self.skipped) + len(self.failed)

    def conformant(self) -> bool:
        """Whether every executed case passed and the count assertion held."""
        return len(self.failed) == 0 and self.count_asserted()


@dataclass
class DigestResult:
    """The aggregate vector digest verification."""

    ok: bool
    computed: str
    recorded: str
    suites: int
    cases: int


@dataclass
class RunReport:
    """The complete conformance run result."""

    digest: DigestResult
    suites: list[SuiteReport] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    skipped: int = 0
    failed: int = 0

    def conformant(self) -> bool:
        """Whether every applicable case passed, every count assertion held,
        and the aggregate digest matched the manifest.

        The hard execution floor (G67, 2026-08-14): the registered suite
        inventory must actually cover the frozen 18 suites / 519 cases — an
        empty registration (a lost suite import) must fail, not pass
        silently.
        """
        if not self.digest.ok:
            return False
        if len(self.suites) != EXPECTED_SUITES or self.total != EXPECTED_CASES:
            return False
        for suite in self.suites:
            if not suite.conformant():
                return False
        return True


@dataclass
class Case:
    """One loaded vector case."""

    id: str
    capability: str
    contract: str
    input: object
    expected: object
    index: int


@dataclass
class SuiteData:
    """One loaded vector suite."""

    suite: str
    semantic_model: str
    cases: list[Case]


@dataclass
class SuiteDefinition:
    """One frozen vector suite definition."""

    file: str
    suite_id: str
    semantic_model: str
    expected_cases: int
    run: callable  # (runner, suite_data) -> SuiteReport


# The frozen 18-suite inventory in fc-manifest order
# (fc-manifest-0.13.0.json; case counts re-pinned by the digest check).
SUITE_DEFINITIONS: list[SuiteDefinition] = []


class Runner:
    """Executes the shared vector suites from explicit repository paths."""

    def __init__(self, vectors_dir: str, fixtures_dir: str, manifest_path: str):
        self.vectors_dir = vectors_dir
        self.fixtures_dir = fixtures_dir
        self.manifest_path = manifest_path

    def verify_vectors_digest(self) -> DigestResult:
        computed, suites, cases = loader.compute_vectors_digest(self.vectors_dir)
        recorded, recorded_suites, recorded_cases = self._manifest_conformance_suite()
        return DigestResult(
            ok=(computed == recorded == AGGREGATE_SHA256
                and suites == recorded_suites == EXPECTED_SUITES
                and cases == recorded_cases == EXPECTED_CASES),
            computed=computed,
            recorded=recorded,
            suites=suites,
            cases=cases,
        )

    def _manifest_conformance_suite(self) -> tuple[str, int, int]:
        import json as json_module

        with open(self.manifest_path, "r", encoding="utf-8") as handle:
            manifest = json_module.load(handle)
        try:
            record = manifest["digests"]["conformance_suite"]
        except KeyError as error:
            # A manifest missing the frozen key is a strict-decode failure
            # of the input file (data, RFC 0015 §5.1), not an internal
            # error — report it as ValueError so the CLI classifies it
            # exit 2 (wave-4 R18).
            raise ValueError(
                "manifest is missing digests.conformance_suite"
            ) from error
        return (
            record["aggregate_sha256"],
            int(record["suites"]),
            int(record["cases"]),
        )

    def run(self) -> RunReport:
        digest = self.verify_vectors_digest()
        reports: list[SuiteReport] = []
        for definition in suite_definitions():
            reports.append(self.run_suite(definition))
        report = RunReport(digest=digest, suites=reports)
        for suite in reports:
            report.total += len(suite.passed) + len(suite.skipped) + len(suite.failed)
            report.passed += len(suite.passed)
            report.skipped += len(suite.skipped)
            report.failed += len(suite.failed)
        return report

    def run_suite(self, definition: SuiteDefinition) -> SuiteReport:
        try:
            data = self.load_suite(definition)
        except (OSError, ValueError) as error:
            return SuiteReport(
                suite=definition.suite_id,
                expected_cases=definition.expected_cases,
                failed=[CaseFailure(id="suite.parse", message=str(error))],
            )
        report = SuiteReport(
            suite=data.suite,
            semantic_model=data.semantic_model,
            expected_cases=definition.expected_cases,
        )
        # Wave-4 R4 (2026-08-15): the semantic-model identifier check is
        # vector-driven — every suite whose vector file declares a
        # semantic_model must carry the same declaration in its frozen
        # registration; a vector with no declaration skips the comparison.
        # This validates all 12 vectors that declare a semantic_model
        # (previously only the 4 registered declarations were compared).
        if data.suite != definition.suite_id or (
            data.semantic_model and data.semantic_model != definition.semantic_model
        ):
            report.failed.append(
                CaseFailure(id="suite.schema", message="unexpected suite or semantic-model identifier")
            )
            return report
        seen: set[str] = set()
        for vector in data.cases:
            if vector.id in seen:
                report.failed.append(CaseFailure(id=vector.id, message="duplicate case id"))
                continue
            seen.add(vector.id)
        if definition.expected_cases != len(data.cases):
            report.failed.append(
                CaseFailure(
                    id="suite.count",
                    message=(
                        f"case count changed: expected {definition.expected_cases}, "
                        f"found {len(data.cases)}"
                    ),
                )
            )
        sub = definition.run(self, data)
        report.passed.extend(sub.passed)
        report.skipped.extend(sub.skipped)
        report.failed.extend(sub.failed)
        return report

    def load_suite(self, definition: SuiteDefinition) -> SuiteData:
        data = loader.read_vector_file(self.vectors_dir, definition.file)
        root = loader.load_vector_root(data)
        suite_name = _string_member(root, "suite", required=True)
        semantic_model = _string_member(root, "semantic_model", required=False) or ""
        cases_value = loader._object_field(root, "cases")
        if cases_value is None or cases_value.kind.value != "Sequence":
            raise ValueError("cases field must be a Sequence")
        cases: list[Case] = []
        for index, item in enumerate(cases_value.as_sequence()):
            case_object = _require_object(item, f"case {index}")
            cases.append(
                Case(
                    id=_string_member(case_object, "id", required=True),
                    capability=_string_member(case_object, "capability", required=False) or "",
                    contract=_string_member(case_object, "contract", required=False) or "",
                    input=_object_member(case_object, "input") or _EMPTY_OBJECT,
                    expected=_object_member(case_object, "expected") or _EMPTY_OBJECT,
                    index=index,
                )
            )
        return SuiteData(suite=suite_name, semantic_model=semantic_model, cases=cases)

    def fixture_bytes(self, *parts: str) -> bytes:
        """Reads one fixture under ``conformance/fixtures``."""
        with open(os.path.join(self.fixtures_dir, *parts), "rb") as handle:
            return handle.read()


# The empty PortableValue object used when a vector case carries no
# input/expected field (G41, 2026-08-14): a real empty object instead of
# None, so handler code never hits a None where a PortableValue is
# expected.
_EMPTY_OBJECT = PortableValue.object(())


def _string_member(value, name: str, required: bool) -> str:
    field_value = loader._object_field(value, name)
    if field_value is None:
        if required:
            raise ValueError(f"{name} field is absent")
        return ""
    if field_value.kind.value != "String":
        raise ValueError(f"{name} field must be a String")
    return field_value.as_string()


def _object_member(value, name: str):
    return loader._object_field(value, name)


def _require_object(value, what: str):
    if value.kind.value != "Object":
        raise ValueError(f"{what} must be an Object")
    return value


def default_manifest_path(vectors_dir: str) -> str:
    """Derives the manifest path from the vectors directory (repository
    layout: conformance/vectors -> docs/fc-manifest-0.13.0.json)."""
    return os.path.join(os.path.dirname(os.path.dirname(vectors_dir)), "docs", "fc-manifest-0.13.0.json")


def repository_paths() -> tuple[str, str]:
    """Repository-relative runner paths: this module lives at
    python/src/consema/conformance, so the repository root is four levels
    up from the conformance package."""
    conformance_dir = os.path.dirname(os.path.abspath(__file__))
    python_dir = os.path.dirname(os.path.dirname(os.path.dirname(conformance_dir)))
    repo_root = os.path.dirname(python_dir)
    return (
        os.path.join(repo_root, "conformance", "vectors"),
        os.path.join(repo_root, "conformance", "fixtures"),
    )


def run_argv(argv: list[str] | None = None) -> int:
    """Runs the conformance runner CLI; returns the process exit code
    (0 success, 1 usage, 2 data, 5 internal; RFC 0015 §5.1 — exit classes
    3 (limit) and 4 (precondition) are never returned by this CLI: a
    suite-internal resource-limit failure is recorded as a case failure
    (exit 2) and an unexpected exception is exit 5)."""
    parser = argparse.ArgumentParser(
        prog="python -m consema.conformance",
        description="Consema conformance runner (18 suites / 519 shared vectors)",
    )
    default_vectors, default_fixtures = repository_paths()
    parser.add_argument("--vectors", default=default_vectors, help="conformance/vectors directory")
    parser.add_argument("--fixtures", default=default_fixtures, help="conformance/fixtures directory")
    parser.add_argument("--manifest", default="", help="Feature-Complete Manifest path")
    parser.add_argument("--quiet", action="store_true", help="suppress the human report")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_error:
        # argparse exits 2 on usage errors; RFC 0015 §5.1 maps usage to
        # exit class 1. --help exits 0 (keep argparse's own exit).
        if exit_error.code not in (0, 2):
            raise
        return 0 if exit_error.code == 0 else 1
    manifest = args.manifest or default_manifest_path(args.vectors)
    runner = Runner(args.vectors, args.fixtures, manifest)
    try:
        report = runner.run()
    except (OSError, ValueError) as error:
        # Input data failures are data errors (exit 2; RFC 0015 §5.1
        # input-file read failures + request/plan files failing strict
        # decode): missing/unreadable vectors dir, manifest or fixtures
        # (OSError), a corrupt manifest (JSON decode failure or missing
        # digests.conformance_suite key — ValueError), and vector-content
        # parse errors raised at the digest stage (verify_vectors_digest
        # runs before the suite loop). Wave-4 R18: these are corrupted
        # input data, not internal errors — the generic handler below
        # stays reserved for genuine internal bugs (exit 5).
        print(f"consema-conformance: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 — CLI boundary
        print(f"consema-conformance: {error}", file=sys.stderr)
        return 5
    if not args.quiet:
        print_human_report(report)
    return 0 if report.conformant() else 2


def print_human_report(report: RunReport) -> None:
    """Per-suite pass/fail report mirroring the Go runner's shape."""
    print(
        f"conformance vectors digest: {report.digest.computed} "
        f"(recorded {report.digest.recorded}, {report.digest.suites} suites, {report.digest.cases} cases)"
    )
    if not report.digest.ok:
        print("digest MISMATCH: the vector inventory differs from the Feature-Complete Manifest")
    for suite in report.suites:
        print(
            f"suite {suite.suite}: {len(suite.passed)} passed, {len(suite.skipped)} skipped, "
            f"{len(suite.failed)} failed (expected {suite.expected_cases} cases)"
        )
        for skip in suite.skipped:
            print(f"  skip {skip.id} [{skip.capability}]: {skip.reason}")
        for failure in suite.failed:
            print(f"  FAIL {failure.id}: {failure.message}")
    print(f"total: {report.passed} passed, {report.skipped} skipped, {report.failed} failed")
    if report.conformant():
        print("conformant")
    else:
        print("NOT CONFORMANT")


def register_suite(
    file: str, suite_id: str, semantic_model: str, expected_cases: int, run: callable
) -> None:
    """Registers one frozen suite definition (imported by the suite modules)."""
    SUITE_DEFINITIONS.append(
        SuiteDefinition(
            file=file,
            suite_id=suite_id,
            semantic_model=semantic_model,
            expected_cases=expected_cases,
            run=run,
        )
    )


def suite_definitions() -> list[SuiteDefinition]:
    """The registered suite inventory.

    The canonical entry is ``python -m consema.conformance``
    (consema/conformance/__main__.py), which imports this module once and
    calls :func:`run_argv` directly, so the canonical module object always
    holds the inventory. The ``python -m consema.conformance.runner`` form
    still works under the default warning filters but triggers a CPython
    RuntimeWarning (double import: the module is re-executed as ``__main__``
    after the package import already registered every suite into the
    canonical module object); under ``-W error::RuntimeWarning`` /
    ``PYTHONWARNINGS=error`` the warning becomes a hard failure and the CLI
    aborts before running anything — the warning-free entry is
    ``python -m consema.conformance``. Always read
    the canonical module object's list.
    """
    canonical = sys.modules.get("consema.conformance.runner")
    if canonical is not None and canonical is not sys.modules.get("__main__"):
        return canonical.SUITE_DEFINITIONS
    return SUITE_DEFINITIONS


def main() -> None:
    sys.exit(run_argv())


if __name__ == "__main__":
    main()
