param(
    [string]$CaseFile = '',
    [string]$OutDir = '',
    # consema-rs checkout directory (multi-repo mode); default: <repo
    # root>\consema-rs (CI layout) or a sibling consema-rs checkout (G109)
    [string]$RustWorkspace = ''
)

# ---------------------------------------------------------------------------
# Cross-language protocol exchange verification for Python (L5
# differential harness, multi-language-implementation-plan L5;
# https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §3.4; roadmap §22.2
# (line numbers may drift, the section heading is the anchor);
# "protocol cross-encode/decode 100%" extended to Python; the Go twin:
# go-verify-protocol-exchange.ps1).
#
# Pipeline (Python never imports or calls Rust, RFC 0016 §1.1):
#   1. builds the minimal Rust exchange example
#      (consema-conformance/examples/emit_protocol_exchange.rs);
#   2. emit mode: runs it over the provisioned case set
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
# Per-invocation unique directory suffix (G44, 2026-08-14): a fixed shared
# capture/evidence/output/workDir path would let two concurrent runs
# truncate or interleave each other's files and flip the SKIPPED/PASSED
# verdicts; every default TEMP/target path below carries this nonce.
$nonce = [Guid]::NewGuid().ToString('N')
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$pythonDir = Join-Path $workspaceRoot 'python'
# The Rust emitter workspace lives in the consema-rs repository checkout
# (multi-repo mode): this repository carries the Python implementation only.
# Default resolution (G109, adversarial audit 2026-08-13 — the old default
# only matched the CI nested layout): <repo root>\consema-rs (CI) first,
# then a sibling consema-rs checkout; -RustWorkspace overrides either.
if (-not $RustWorkspace) {
    $nested = Join-Path $workspaceRoot 'consema-rs'
    $sibling = Join-Path (Split-Path -Parent $workspaceRoot) 'consema-rs'
    if (Test-Path (Join-Path $nested 'Cargo.toml')) {
        $RustWorkspace = $nested
    }
    elseif (Test-Path (Join-Path $sibling 'Cargo.toml')) {
        $RustWorkspace = $sibling
    }
    else {
        Write-Error "consema-rs checkout not found: tried $nested (CI multi-repo mode) and $sibling (side-by-side layout); pass -RustWorkspace explicitly"
        exit 1
    }
}
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
    # Forward-slash separators: Join-Path normalizes per-host (wave-5:
    # the backslash form was Windows-only).
    $CaseFile = Join-Path $workspaceRoot 'conformance/differential/protocol-exchange/cases.json'
}
if (-not (Test-Path $CaseFile)) {
    Write-Error "protocol-exchange case file not found: $CaseFile"
    exit 1
}
# UTF8 explicit: PowerShell 5.1 Get-Content defaults to the ANSI codepage.
$cases = Get-Content $CaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$caseCount = @($cases.cases).Count
# Exact frozen count (G66, 2026-08-14): the shared protocol-exchange
# case set is pinned at 83 — any drift (fewer OR more) fails, not a loose
# >= floor.
if ($caseCount -ne 83) {
    Write-Error "protocol-exchange case file has $caseCount cases, want exactly 83"
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
# The built example binary is `debug/examples/emit_protocol_exchange` on
# POSIX hosts and `debug\examples\emit_protocol_exchange.exe` on Windows;
# probe both spellings (wave-5: the previous hardcoded .exe form was
# Windows-only and failed on POSIX + pwsh hosts that satisfy every stated
# requirement).
$example = Join-Path $targetDir 'debug/examples/emit_protocol_exchange'
if (Test-Path "$example.exe") { $example = "$example.exe" }
if (-not (Test-Path $example)) {
    Write-Error "Rust example binary not found: $example (.exe probed on Windows)"
    exit 1
}
if ($OutDir -eq '') {
    $OutDir = Join-Path $targetDir "python-differential-exchange-$nonce"
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
$pythonEvidenceDir = Join-Path $targetDir "python-differential-exchange-py-$nonce"
$pythonEvidenceDir = [System.IO.Path]::GetFullPath($pythonEvidenceDir)
if (Test-Path $pythonEvidenceDir) { Remove-Item $pythonEvidenceDir -Recurse -Force }
Write-Host "[3/4] running the Python exchange test (test_protocol_exchange.py) + emitting the Python encoder files -> $pythonEvidenceDir"
$env:CONSEMA_EXCHANGE_RUST_DIR = $OutDir
$env:CONSEMA_EXCHANGE_PYTHON_DIR = $pythonEvidenceDir
$logDir = Join-Path $env:TEMP "consema-python-exchange-$nonce"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stdoutFile = Join-Path $logDir 'python-test.stdout.txt'
$stderrFile = Join-Path $logDir 'python-test.stderr.txt'
# Windows PowerShell 5.1: with $ErrorActionPreference = 'Stop' a native
# command writing to redirected stderr turns into a terminating
# NativeCommandError before the diagnostics can be captured, so the EAP
# is relaxed around this native call (pytest writes stderr on failure).
$EAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Push-Location $pythonDir
try {
    & $python -m pytest tests/differential/test_protocol_exchange.py -v 1> $stdoutFile 2> $stderrFile
    $testCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
$ErrorActionPreference = $EAP
Get-Content $stdoutFile | ForEach-Object { Write-Host $_ }
if (Test-Path $stderrFile) {
    Get-Content $stderrFile | ForEach-Object { Write-Host $_ }
}

# The exchange test must have RUN (not skipped) and passed.
$output = Get-Content $stdoutFile -Raw
# The guard matches only the env-gated test's OWN skip marker (wave-5:
# the old `-match 'SKIPPED'` matched ANY skip in the file — a
# data-missing skip of test_case_file_integrity or a future legitimate
# skip of an unrelated test was mis-attributed to the exchange test and
# killed the verification even when it passed).
if ($output -match 'test_protocol_exchange SKIPPED') {
    Write-Error 'the protocol-exchange test skipped: the Rust exchange directory was not provisioned (CONSEMA_EXCHANGE_*)'
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
# Windows PowerShell 5.1: with $ErrorActionPreference = 'Stop' a native
# command writing to redirected stderr turns into a terminating
# NativeCommandError before the diagnostics can be captured, so the EAP
# is relaxed around this native call (the Rust emitter writes stderr).
$EAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $example --verify $CaseFile $pythonEvidenceDir 1> $verifyLog 2> $verifyErr
$ErrorActionPreference = $EAP
$verifyCode = $LASTEXITCODE
Get-Content $verifyLog | ForEach-Object { Write-Host $_ }
if (Test-Path $verifyErr) {
    Get-Content $verifyErr | ForEach-Object { Write-Host $_ }
}
if ($verifyCode -ne 0) {
    Write-Error "the Rust verify pass found divergences or failed (exit $verifyCode): a divergence here is a real Python encoder/decoder bug (the record codec gaps are closed; measured status in the header above)"
    exit $verifyCode
}
# Wave-4 (D5-02 family): the reverse leg must prove the verify mode
# actually ran — assert the emit_protocol_exchange summary line, mirroring
# the normalized-differential reverse-leg assertion (the Rust emitter
# prints "emit_protocol_exchange (verify): N accept cases and M reject
# cases verified into <dir>").
$verifySummary = [regex]::Match((Get-Content $verifyLog -Raw), 'emit_protocol_exchange \(verify\): \d+ accept cases and \d+ reject cases verified')
if ($verifySummary.Success) {
    Write-Host "RESULT (verify): $($verifySummary.Value)"
} else {
    Write-Error 'cannot find the emit_protocol_exchange verify summary line in the verify-mode output'
    exit 1
}
Write-Host "protocol exchange verification complete (exit 0)"
exit 0
