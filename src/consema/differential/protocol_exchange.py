"""Cross-language protocol exchange of the Python implementation against the
Rust authority (milestone 0.19.0 G5.3; docs/five-language-ci-design.md §3.4;
roadmap §16.6 line 1549; the Go twin:
go/conformance/differential/protocol-exchange/exchange_test.go).

The checked-in case set (go/conformance/differential/protocol-exchange/
cases.json) carries one RFC 0015 machine record per case as canonical
transport JSON plus the expected outcome (empty error code = accept,
registered ``core.protocol.*@1`` code = reject). Both sides decode and
re-encode every case; the Rust example
(crates/consema-conformance/examples/emit_protocol_exchange.rs) emits one
``<case-id>.json.hex`` / ``<case-id>.pvce.hex`` / ``<case-id>.error.txt``
file per case, and this module:

- accept cases: decodes the canonical transport JSON, validates the record
  with the full typed record decoder, re-encodes byte-identically on both
  transports, compares the bytes with the Rust files byte for byte, and
  checks the Rust-encode -> Python-decode direction (equivalent record,
  byte-identical re-encode);
- reject cases: rejects the same transport bytes with the same registered
  error code (``core.protocol.*@1``; error text never participates).

The Python side also emits its own encoder files (CONSEMA_EXCHANGE_PYTHON_DIR),
which the Rust example's ``--verify`` mode closes: Python-encode ->
Rust-decode. Orchestration: scripts/python-verify-protocol-exchange.ps1.
Without the environment variables the exchange test skips (documented,
never silent) and only the case-file integrity checks run.

Measured status (2026-08-12): 83/83 cases verified (40/40 accept, 43/43
reject) after the envelope.py record codec fixes: the value-path wire
record is the schema-less ``object[("segments", ...)]`` shape
(crates/consema-protocol/src/query.rs:441-464) and the
materialization-request style reference rejects ``version: 0`` exactly
like the authority (Rust ``ContractId::new`` version validation,
materialization.rs:1357-1363).
"""

from __future__ import annotations

import dataclasses
import os

from consema.differential import case_files
from consema.protocol.canonical import decode_json, decode_pvce, encode_json, encode_pvce
from consema.protocol.contract import ContractId, ContractRegistry
from consema.protocol.envelope import validate_registered_payload
from consema.protocol.error_registry import ErrorCodeRegistry
from consema.protocol.errors import ProtocolError, ProtocolErrorKind, protocol_error
from consema.protocol.limits import ProtocolLimits
from consema.protocol.query import QueryFailure

CASE_FILE = os.path.join("protocol-exchange", "cases.json")
MANIFEST = "consema.differential.protocol-exchange@1"

# The directory of the Rust encoder's per-case files and the directory the
# Python side writes its own encoder bytes into (consumed by the Rust
# example's --verify pass; the Go harness uses the same first variable).
RUST_DIR_ENV = "CONSEMA_EXCHANGE_RUST_DIR"
PYTHON_DIR_ENV = "CONSEMA_EXCHANGE_PYTHON_DIR"

# The closed record inventory of the exchange set: exactly the protocol
# record surface both implementations decode in full (exchange_test.go
# allRecords; the Python dispatch is validate_registered_payload,
# python/src/consema/protocol/envelope.py:2768-2859).
ALL_RECORDS = [
    "core.batch-plan@1",
    "core.batch-result@1",
    "core.cancellation-request@1",
    "core.capability-declaration@1",
    "core.change-set@1",
    "core.cli-output@1",
    "core.completion@1",
    "core.diagnostic@1",
    "core.error-code-registry@1",
    "core.execution-policy@1",
    "core.graph-projection-result@1",
    "core.graph-provenance-map@1",
    "core.graph-query-result@1",
    "core.ini-query-result@1",
    "core.java-properties-query-result@1",
    "core.java-utf16-string@1",
    "core.materialization-request@2",
    "core.materialization-result@2",
    "core.portable-graph@1",
    "core.portable-value-json@1",
    "core.profile-descriptor@1",
    "core.projection-report@1",
    "core.projection-request@1",
    "core.projection-result@1",
    "core.provenance-map@1",
    "core.query-definition@1",
    "core.query-result@1",
    "core.registry-manifest@1",
    "core.source-encoding@1",
    "core.source-patch@2",
    "core.source-snapshot@2",
    "core.yaml-query-result@1",
]


@dataclasses.dataclass(frozen=True)
class ExchangeResult:
    """The exchange run outcome."""

    accept_passed: int = 0
    accept_failed: int = 0
    reject_passed: int = 0
    reject_failed: int = 0
    failures: tuple[str, ...] = ()


def load_case_file() -> list[dict]:
    """Loads and validates the checked-in case set at the file level:
    manifest id, exact count, unique ids, known records, per-record positive
    and negative coverage, canonical transport JSON, and registered expected
    codes.

    Unlike the Go twin (exchange_test.go loadCaseFile), the Python side does
    not require every accept case to pass the Python typed record decode
    here: the Python record codecs are exactly what the exchange run
    measures (a record the Python codecs cannot accept is a run-level
    finding, reported case by case, never a silent fix)."""
    cases = case_files.load_case_file(
        CASE_FILE, MANIFEST, case_files.PROTOCOL_EXCHANGE_EXACT
    )
    known = set(ALL_RECORDS)
    coverage: dict[str, list[int]] = {record: [0, 0] for record in ALL_RECORDS}
    registry = ErrorCodeRegistry(1)
    for case in cases:
        case_id = case["id"]
        record = case.get("record")
        if record not in known:
            raise case_files.CaseFileError(
                f"case {case_id}: record {record!r} is not in the exchange inventory"
            )
        expected_code = (case.get("expected") or {}).get("error_code", "")
        if expected_code:
            if not registry.contains(expected_code):
                raise case_files.CaseFileError(
                    f"case {case_id}: expected code {expected_code!r} is not a registered protocol code"
                )
            coverage[record][1] += 1
            continue
        coverage[record][0] += 1
        # The strict canonicality check (parse + re-encode) keeps the file's
        # transport JSON honest: the Rust side must accept the same text.
        try:
            value = decode_json(case["json"].encode("utf-8"), ProtocolLimits())
        except ProtocolError as error:
            raise case_files.CaseFileError(
                f"case {case_id}: json is not canonical transport JSON: {error}"
            ) from None
        re_encoded = encode_json(value, ProtocolLimits())
        if re_encoded != case["json"].encode("utf-8"):
            raise case_files.CaseFileError(
                f"case {case_id}: Python typed re-encode is not byte-identical to the case json"
            )
        encode_pvce(value, ProtocolLimits())
    for record in ALL_RECORDS:
        accept_count, reject_count = coverage[record]
        if accept_count == 0 or reject_count == 0:
            raise case_files.CaseFileError(
                f"record {record} has no {'accept' if accept_count == 0 else 'reject'} case in the exchange set"
            )
    return cases


def decode_record(record: str, value) -> object:
    """Validates one record schema with its full typed record decoder and
    returns the re-encodeable value tree (exchange_test.go decodeRecord).

    The Python record codecs validate through ``validate_registered_payload``
    (the payload.rs dispatch mirror, envelope.py:2768-2859) and re-encode the
    validated transport value; ``core.portable-value-json@1`` has no
    record-level decoder: the transported value is the record."""
    if record == "core.portable-value-json@1":
        return value
    registry = ContractRegistry(1)
    try:
        validate_registered_payload(ContractId(*record.rsplit("@", 1)), value, registry)
    except QueryFailure as failure:
        # The registry dispatch mirrors the payload.rs mapping: any
        # QueryFailure becomes KindInvalidValue at "$.payload" (the same
        # mapping as validate_registered_payload and the Go payload.go
        # decodeRecord; exchange_test.go:573-594).
        raise protocol_error(
            ProtocolErrorKind.INVALID_VALUE,
            "$.payload",
            f"invalid query definition: {failure}",
        ) from None
    return value


def rejection_code(case: dict) -> str:
    """Decodes one reject case on the Python side (transport then typed
    record decoder) and returns the registered rejection code
    (exchange_test.go goRejectionCode)."""
    try:
        value = decode_json(case["json"].encode("utf-8"), ProtocolLimits())
    except ProtocolError as error:
        return error.code
    try:
        decode_record(case.get("record", ""), value)
    except ProtocolError as error:
        return error.code
    return ""


def _first_diff(case_id: str, direction: str, py_bytes: bytes, rust_bytes: bytes) -> str:
    index = 0
    while index < len(py_bytes) and index < len(rust_bytes) and py_bytes[index] == rust_bytes[index]:
        index += 1
    return (
        f"case {case_id} ({direction}): Python {len(py_bytes)} bytes, Rust {len(rust_bytes)} bytes, "
        f"first difference at offset {index}\n  Python: {py_bytes.hex()}\n  Rust:   {rust_bytes.hex()}"
    )


def _read_hex_file(directory: str, name: str) -> bytes | None:
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return bytes.fromhex(handle.read().strip())


def _read_error_file(directory: str, case_id: str) -> str | None:
    path = os.path.join(directory, case_id + ".error.txt")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _write_hex(directory: str, name: str, data: bytes) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8", newline="") as handle:
        handle.write(data.hex() + "\n")


def run_exchange(rust_dir: str | None = None, python_out_dir: str | None = None) -> ExchangeResult:
    """Verifies the bidirectional cross-language exchange: Python-encoded
    bytes match the Rust encoder's bytes, Rust bytes decode under the Python
    typed record codec to equivalent records and re-encode byte-identically,
    and rejection cases reject with the same registered code on both sides."""
    cases = load_case_file()
    if rust_dir is None:
        rust_dir = os.environ.get(RUST_DIR_ENV)
    if python_out_dir is None:
        python_out_dir = os.environ.get(PYTHON_DIR_ENV)
    if python_out_dir is not None:
        os.makedirs(python_out_dir, exist_ok=True)

    known_ids = {case["id"] for case in cases}
    if rust_dir is not None:
        for name in os.listdir(rust_dir):
            base = name
            for suffix in (".json.hex", ".pvce.hex", ".error.txt"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            if base != name and base not in known_ids:
                raise case_files.CaseFileError(
                    f"rust file {name!r} does not correspond to any case (case file drift?)"
                )

    failures: list[str] = []
    accept_passed = accept_failed = reject_passed = reject_failed = 0
    limits = ProtocolLimits()
    for case in cases:
        case_id = case["id"]
        record = case.get("record", "")
        expected_code = (case.get("expected") or {}).get("error_code", "")
        if expected_code:
            reject_failed_before = len(failures)
            failures.extend(verify_reject_case(case, rust_dir, python_out_dir))
            if len(failures) == reject_failed_before:
                reject_passed += 1
            else:
                reject_failed += 1
            continue
        accept_failed_before = len(failures)
        failures.extend(verify_accept_case(case, record, rust_dir, python_out_dir, limits))
        if len(failures) == accept_failed_before:
            accept_passed += 1
        else:
            accept_failed += 1
    return ExchangeResult(
        accept_passed=accept_passed,
        accept_failed=accept_failed,
        reject_passed=reject_passed,
        reject_failed=reject_failed,
        failures=tuple(failures),
    )


def verify_accept_case(case: dict, record: str, rust_dir: str | None, python_out_dir: str | None, limits) -> list[str]:
    """Verifies one accept case end to end (exchange_test.go
    verifyAcceptCase)."""
    case_id = case["id"]
    failures: list[str] = []
    # Python side: decode the transport JSON, validate the typed record,
    # re-encode.
    try:
        value = decode_json(case["json"].encode("utf-8"), limits)
    except ProtocolError as error:
        return [f"case {case_id}: case json no longer decodes: {error}"]
    try:
        decode_record(record, value)
    except ProtocolError as error:
        return [f"case {case_id}: Python typed record decode failed: {error}"]
    py_json = encode_json(value, limits)
    py_pvce = encode_pvce(value, limits)
    if python_out_dir is not None:
        _write_hex(python_out_dir, case_id + ".json.hex", py_json)
        _write_hex(python_out_dir, case_id + ".pvce.hex", py_pvce)

    if rust_dir is None:
        return failures
    rust_json = _read_hex_file(rust_dir, case_id + ".json.hex")
    rust_pvce = _read_hex_file(rust_dir, case_id + ".pvce.hex")
    if rust_json is None or rust_pvce is None:
        return [f"case {case_id}: missing Rust byte file (run scripts/python-verify-protocol-exchange.ps1)"]
    if py_json != rust_json:
        failures.append(_first_diff(case_id, "json", py_json, rust_json))
    if py_pvce != rust_pvce:
        failures.append(_first_diff(case_id, "pvce", py_pvce, rust_pvce))

    # Rust encode -> Python decode: the Rust JSON bytes decode to an
    # equivalent record and re-encode byte-identically.
    try:
        rust_value = decode_json(rust_json, limits)
    except ProtocolError as error:
        failures.append(f"case {case_id}: Python cannot decode the Rust JSON bytes: {error}")
    else:
        try:
            decode_record(record, rust_value)
        except ProtocolError as error:
            failures.append(
                f"case {case_id}: Python typed decode of the Rust JSON bytes failed: {error}"
            )
        else:
            re_encoded = encode_json(rust_value, limits)
            if re_encoded != rust_json:
                failures.append(
                    f"case {case_id}: Python JSON re-encode of the Rust bytes is not byte-identical"
                )

    # Rust encode -> Python decode over the PVCE transport.
    try:
        rust_value_2 = decode_pvce(rust_pvce, limits)
    except ProtocolError as error:
        failures.append(f"case {case_id}: Python cannot decode the Rust PVCE bytes: {error}")
    else:
        try:
            decode_record(record, rust_value_2)
        except ProtocolError as error:
            failures.append(
                f"case {case_id}: Python typed decode of the Rust PVCE bytes failed: {error}"
            )
        else:
            re_encoded_2 = encode_pvce(rust_value_2, limits)
            if re_encoded_2 != rust_pvce:
                failures.append(
                    f"case {case_id}: Python PVCE re-encode of the Rust bytes is not byte-identical"
                )
    return failures


def verify_reject_case(case: dict, rust_dir: str | None, python_out_dir: str | None) -> list[str]:
    """Verifies one reject case cross-language: the Python side rejects with
    exactly the expected code (re-verified here), and the Rust side must
    have recorded the same code (exchange_test.go verifyRejectCase)."""
    case_id = case["id"]
    expected_code = (case.get("expected") or {}).get("error_code", "")
    failures: list[str] = []
    code = rejection_code(case)
    # Record the Python rejection code so the Rust --verify pass can compare
    # it (the same file contract as the Rust emitter's error files); written
    # even on divergence so the reverse direction reports the code
    # difference precisely.
    if python_out_dir is not None:
        with open(
            os.path.join(python_out_dir, case_id + ".error.txt"), "w", encoding="utf-8", newline=""
        ) as handle:
            handle.write(code + "\n")
    if code != expected_code:
        return [
            f"case {case_id}: Python rejection code {code!r} != expected {expected_code!r}"
        ]
    if rust_dir is None:
        return failures
    rust_code = _read_error_file(rust_dir, case_id)
    if rust_code is None:
        return [f"case {case_id}: missing Rust rejection file (run scripts/python-verify-protocol-exchange.ps1)"]
    if rust_code != expected_code:
        failures.append(
            f"case {case_id}: rejection codes diverge: Python {expected_code}, Rust {rust_code} (want {expected_code})"
        )
    return failures


def exchange_summary(result: ExchangeResult) -> str:
    return (
        f"protocol exchange: {result.accept_passed}/{result.accept_passed + result.accept_failed} "
        f"accept cases and {result.reject_passed}/{result.reject_passed + result.reject_failed} "
        f"reject cases verified"
    )
