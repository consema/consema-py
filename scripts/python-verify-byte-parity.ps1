param(
    [string]$CaseFile = '',
    [string]$OutDir = '',
    # consema-rs checkout directory (multi-repo mode); default: <repo root>\consema-rs
    [string]$RustWorkspace = ''
)

# ---------------------------------------------------------------------------
# Cross-language PVCE/PGCE byte-parity verification for Python (L5
# differential harness, multi-language-implementation-plan L5;
# https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §3.2; roadmap §16.1 hard gate:
# "Rust 与 Go 的 PVCE/PGCE bytes 完全一致" extended to Python).
#
# Pipeline (Python never imports or calls Rust, RFC 0016 §1.1):
#   1. builds the minimal Rust encoder example
#      (consema-conformance/examples/emit_parity_bytes.rs);
#   2. runs it over the provisioned case set
#      (conformance/differential/cases.json, the shared single-authority
#      case directory of the consema repository) into <OutDir> as one
#      `<case-id>.hex` file per case;
#   3. runs the Python side (`python -m pytest
#      python/tests/differential/test_byte_parity.py` with
#      CONSEMA_DIFFERENTIAL_RUST_DIR set) which compares the Python encoder
#      bytes with the Rust byte files byte for byte and checks the
#      bidirectional direction (Rust bytes -> Python decode -> Python
#      re-encode).
#
# Requirements: cargo (or $env:CONSEMA_CARGO) and python 3.12 (or
# $env:CONSEMA_PYTHON) on PATH; the consema package must be importable
# (pip install -e python/ — the tests/ tree has no __init__.py and pytest
# collection errors when the package is not installed); the Rust workspace
# is the consema-rs checkout (<repo root>\consema-rs by default,
# -RustWorkspace overrides). Windows PowerShell 5.1 compatible, no
# third-party dependencies.
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
    $CaseFile = Join-Path $workspaceRoot 'conformance\differential\cases.json'
}
if (-not (Test-Path $CaseFile)) {
    Write-Error "differential case file not found: $CaseFile"
    exit 1
}
# UTF8 explicit: PowerShell 5.1 Get-Content defaults to the ANSI codepage.
$cases = Get-Content $CaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$caseCount = @($cases.cases).Count
if ($caseCount -lt 40) {
    Write-Error "differential case file has $caseCount cases, want >= 40"
    exit 1
}

# --- Rust side ---------------------------------------------------------------
$cargo = if ($env:CONSEMA_CARGO) { $env:CONSEMA_CARGO } else { 'cargo' }
if (-not (Get-Command $cargo -ErrorAction SilentlyContinue)) {
    Write-Error "cargo is not available ('$cargo')"
    exit 1
}
Write-Host "[1/3] building the Rust encoder example (emit_parity_bytes)..."
Push-Location $RustWorkspace
try {
    & $cargo build --locked -p consema-conformance --example emit_parity_bytes
    $buildExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($buildExit -ne 0) { exit $buildExit }

$targetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $RustWorkspace 'target' }
$example = Join-Path $targetDir 'debug\examples\emit_parity_bytes.exe'
if (-not (Test-Path $example)) {
    Write-Error "Rust example binary not found: $example"
    exit 1
}
if ($OutDir -eq '') {
    $OutDir = Join-Path $targetDir 'python-differential-parity'
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force $OutDir | Out-Null

Write-Host "[2/3] running the Rust encoder over $caseCount cases -> $OutDir"
& $example $CaseFile $OutDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "emit_parity_bytes failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

# --- Python side --------------------------------------------------------------
Write-Host "[3/3] running the Python byte-parity test (test_byte_parity.py)..."
$env:CONSEMA_DIFFERENTIAL_RUST_DIR = $OutDir
$logDir = Join-Path $env:TEMP 'consema-python-parity'
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
    & $python -m pytest tests\differential\test_byte_parity.py -v 1> $stdoutFile 2> $stderrFile
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

# The parity test must have RUN (not skipped) and passed.
$output = Get-Content $stdoutFile -Raw
if ($output -match 'SKIPPED') {
    Write-Error 'the byte-parity test skipped: the Rust byte directory was not provisioned'
    exit 1
}
if ($output -notmatch 'test_differential_byte_parity PASSED') {
    Write-Error "the byte-parity test did not pass (pytest exit $testCode)"
    if ($testCode -eq 0) { exit 1 } else { exit $testCode }
}
if ($testCode -ne 0) {
    exit $testCode
}

Write-Host "byte parity verification complete (exit 0)"
exit 0
