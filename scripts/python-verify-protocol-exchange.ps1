param(
    [string]$CaseFile = '',
    [string]$OutDir = '',
    # consema-rs checkout directory (multi-repo mode); default: <repo root>\consema-rs
    [string]$RustWorkspace = ''
)

# ---------------------------------------------------------------------------
# Cross-language protocol exchange verification for Python (milestone 0.19.0
# G5.3; docs/five-language-ci-design.md §3.4; roadmap §22.2 line 1882
# "protocol cross-encode/decode 100%" extended to Python; the Go twin:
# go-verify-protocol-exchange.ps1).
#
# Pipeline (Python never imports or calls Rust, RFC 0016 §1.1):
#   1. builds the minimal Rust exchange example
#      (consema-conformance/examples/emit_protocol_exchange.rs);
#   2. emit mode: runs it over the checked-in case set
#      (conformance/differential/protocol-exchange/cases.json, the shared
#      single-authority case directory of the consema repository) into
#      <OutDir> as one `<case-id>.json.hex`/`.pvce.hex`/`.error.txt` per
#      case (the Rust side self-verifies decode/re-encode byte identity and
#      rejection codes);
#   3. runs the Python side (`python -m pytest
#      python/tests/differential/test_protocol_exchange.py` with
#      CONSEMA_EXCHANGE_RUST_DIR set), which compares the Python encoder
#      bytes with the Rust files byte for byte, decodes the Rust bytes back
#      (equivalent record + byte-identical re-encode), checks the rejection
#      codes, and emits the Python-side encoder files into the Python
#      evidence directory (CONSEMA_EXCHANGE_PYTHON_DIR);
#   4. verify mode: runs the Rust example's `--verify` pass over the Python
#      files, closing the Python-encode -> Rust-decode direction (record
#      equivalence + byte-identical re-encode + rejection-code agreement).
#
# Measured status (2026-08-12): 83/83 cases verified (40/40 accept +
# 43/43 reject). The previously open Python protocol-record codec gaps
# (core.value-path@1 / core.association-location@1 schema fields,
# materialization-request reference version 0) are closed: the Python
# encoder bytes match the Rust files byte for byte and every rejection
# code matches. The script exits non-zero on any divergence, so CI shows
# the true state.
#
# Requirements: cargo (or $env:CONSEMA_CARGO) and python 3.12 (or
# $env:CONSEMA_PYTHON) on PATH; the Rust workspace is the consema-rs checkout
# (<repo root>\consema-rs by default, -RustWorkspace overrides). Windows
# PowerShell 5.1 compatible, no third-party dependencies.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$pythonDir = Join-Path $workspaceRoot 'python'
# The Rust emitter workspace lives in the consema-rs repository checkout
# (multi-repo mode): this repository carries the Python implementation only.
# -RustWorkspace overrides the default sibling checkout <repo root>\consema-rs.
if (-not $RustWorkspace) { $RustWorkspace = Join-Path $workspaceRoot 'consema-rs' }
$RustWorkspace = [IO.Path]::GetFullPath($RustWorkspace)

# --- repo layout sanity ------------------------------------------------------
if (-not (Test-Path (Join-Path $RustWorkspace 'Cargo.toml')) -or
    -not (Test-Path (Join-Path $RustWorkspace 'consema-conformance\Cargo.toml'))) {
    Write-Error "consema-rs workspace not found: $RustWorkspace (checkout consema/consema-rs beside this repository, or pass -RustWorkspace)"
    exit 1
}
$python = if ($env:CONSEMA_PYTHON) { $env:CONSEMA_PYTHON } else { 'python' }
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    Write-Error "python is not on PATH ('$python')"
    exit 1
}

# --- case set ----------------------------------------------------------------
if ($CaseFile -eq '') {
    $CaseFile = Join-Path $workspaceRoot 'conformance\differential\protocol-exchange\cases.json'
}
if (-not (Test-Path $CaseFile)) {
    Write-Error "protocol-exchange case file not found: $CaseFile"
    exit 1
}
# UTF8 explicit: PowerShell 5.1 Get-Content defaults to the ANSI codepage.
$cases = Get-Content $CaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$caseCount = @($cases.cases).Count
if ($caseCount -lt 40) {
    Write-Error "protocol-exchange case file has $caseCount cases, want >= 40"
    exit 1
}

# --- Rust side ---------------------------------------------------------------
$cargo = if ($env:CONSEMA_CARGO) { $env:CONSEMA_CARGO } else { 'cargo' }
if (-not (Get-Command $cargo -ErrorAction SilentlyContinue)) {
    Write-Error "cargo is not available ('$cargo')"
    exit 1
}
Write-Host "[1/4] building the Rust exchange example (emit_protocol_exchange)..."
Push-Location $RustWorkspace
try {
    & $cargo build --locked -p consema-conformance --example emit_protocol_exchange
    $buildExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($buildExit -ne 0) { exit $buildExit }

$targetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $RustWorkspace 'target' }
$example = Join-Path $targetDir 'debug\examples\emit_protocol_exchange.exe'
if (-not (Test-Path $example)) {
    Write-Error "Rust example binary not found: $example"
    exit 1
}
if ($OutDir -eq '') {
    $OutDir = Join-Path $targetDir 'python-differential-exchange'
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force $OutDir | Out-Null

# --- emit mode: Rust emits, Python compares ----------------------------------
Write-Host "[2/4] running the Rust exchange example over $caseCount cases -> $OutDir"
& $example $CaseFile $OutDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "emit_protocol_exchange failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

# --- Python side: forward comparison + reverse emission -----------------------
$pythonEvidenceDir = Join-Path $targetDir 'python-differential-exchange-py'
$pythonEvidenceDir = [System.IO.Path]::GetFullPath($pythonEvidenceDir)
if (Test-Path $pythonEvidenceDir) { Remove-Item $pythonEvidenceDir -Recurse -Force }
Write-Host "[3/4] running the Python exchange test (test_protocol_exchange.py) + emitting the Python encoder files -> $pythonEvidenceDir"
$env:CONSEMA_EXCHANGE_RUST_DIR = $OutDir
$env:CONSEMA_EXCHANGE_PYTHON_DIR = $pythonEvidenceDir
$logDir = Join-Path $env:TEMP 'consema-python-exchange'
New-Item -ItemType Directory -Force $logDir | Out-Null
$stdoutFile = Join-Path $logDir 'python-test.stdout.txt'
$stderrFile = Join-Path $logDir 'python-test.stderr.txt'
Push-Location $pythonDir
try {
    & $python -m pytest tests\differential\test_protocol_exchange.py -v 1> $stdoutFile 2> $stderrFile
    $testCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
Get-Content $stdoutFile | ForEach-Object { Write-Host $_ }
if (Test-Path $stderrFile) {
    Get-Content $stderrFile | ForEach-Object { Write-Host $_ }
}

# The exchange test must have RUN (not skipped) and passed.
$output = Get-Content $stdoutFile -Raw
if ($output -match 'SKIPPED') {
    Write-Error 'the protocol-exchange test skipped: the Rust exchange directory was not provisioned'
    exit 1
}
if ($output -notmatch 'test_protocol_exchange PASSED') {
    Write-Error "the protocol-exchange test did not pass (pytest exit $testCode)"
    if ($testCode -eq 0) { exit 1 } else { exit $testCode }
}
if ($testCode -ne 0) {
    exit $testCode
}

# --- verify mode: Rust closes the Python-encode direction --------------------
Write-Host "[4/4] verify: running the Rust --verify pass against the Python encoder files ($pythonEvidenceDir)"
$verifyLog = Join-Path $logDir 'rust-verify.stdout.txt'
$verifyErr = Join-Path $logDir 'rust-verify.stderr.txt'
& $example --verify $CaseFile $pythonEvidenceDir 1> $verifyLog 2> $verifyErr
$verifyCode = $LASTEXITCODE
Get-Content $verifyLog | ForEach-Object { Write-Host $_ }
if (Test-Path $verifyErr) {
    Get-Content $verifyErr | ForEach-Object { Write-Host $_ }
}
if ($verifyCode -ne 0) {
    Write-Error "the Rust verify pass found divergences or failed (exit $verifyCode): the Python record codec gaps in python/src/consema/protocol/envelope.py must close per roadmap §11.3"
    exit $verifyCode
}
Write-Host "protocol exchange verification complete (exit 0)"
exit 0
