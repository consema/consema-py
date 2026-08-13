param(
    [string]$CaseFile = '',
    [string]$OutDir = '',
    # consema-rs checkout directory (multi-repo mode); default: <repo root>\consema-rs
    [string]$RustWorkspace = ''
)

# ---------------------------------------------------------------------------
# Cross-language normalized-result differential verification for Python
# (L5 differential harness, multi-language-implementation-plan L5;
# https://github.com/consema/consema/blob/main/docs/five-language-ci-design.md §3.3; roadmap
# §11.2 lines 849-861; the Go twin: go-verify-normalized-differential.ps1).
#
# Bidirectional pipeline (Python never imports or calls Rust, RFC 0016
# §1.1):
#   1. builds the minimal Rust evidence example
#      (consema-conformance/examples/emit_normalized_results.rs);
#   2. forward direction: runs it over the provisioned case set
#      (conformance/differential/normalized/cases.json, the shared
#      single-authority case directory of the consema repository) into
#      <OutDir> as
#      one `<case-id>.txt` normalized-facts file per case;
#   3. forward comparison + reverse emission: runs the Python side
#      (`python -m pytest python/tests/differential/test_normalized.py` with
#      CONSEMA_DIFFERENTIAL_NORMALIZED_RUST_DIR set), which computes the
#      Python normalized results for the same input set and compares them
#      field by field with the Rust evidence files (case id + field + both
#      values on divergence), and emits the Python-side evidence files into
#      the Python evidence directory
#      (CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR);
#   4. reverse direction: runs the Rust example's consume mode
#      (`--consume <python-evidence-dir>`), which recomputes the Rust
#      results and compares them field by field with the Python evidence
#      files.
#
# Any divergence in either direction exits non-zero: forward via the
# pytest failure, reverse via the consume mode's exit 1. The compared facts
# are the language-neutral behavior surface of roadmap §11.2; a divergence
# is a finding for the roadmap §11.3 process (minimal cross-language
# reproducer -> classify as implementation/test/spec gap), never a silent
# Python-side "fix".
#
# Measured status (2026-08-12): 108/108 cases equal in both directions
# (11 source + 97 document cases; forward Python-vs-Rust comparison and
# reverse Rust consume mode both pass).
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
    $CaseFile = Join-Path $workspaceRoot 'conformance\differential\normalized\cases.json'
}
if (-not (Test-Path $CaseFile)) {
    Write-Error "normalized differential case file not found: $CaseFile"
    exit 1
}
# UTF8 explicit: PowerShell 5.1 Get-Content defaults to the ANSI codepage.
$cases = Get-Content $CaseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$caseCount = @($cases.cases).Count
if ($caseCount -lt 40) {
    Write-Error "normalized differential case file has $caseCount cases, want >= 40"
    exit 1
}

# --- Rust side ---------------------------------------------------------------
$cargo = if ($env:CONSEMA_CARGO) { $env:CONSEMA_CARGO } else { 'cargo' }
if (-not (Get-Command $cargo -ErrorAction SilentlyContinue)) {
    Write-Error "cargo is not available ('$cargo')"
    exit 1
}
Write-Host "[1/4] building the Rust evidence example (emit_normalized_results)..."
Push-Location $RustWorkspace
try {
    & $cargo build --locked -p consema-conformance --example emit_normalized_results
    $buildExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($buildExit -ne 0) { exit $buildExit }

$targetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $RustWorkspace 'target' }
$example = Join-Path $targetDir 'debug\examples\emit_normalized_results.exe'
if (-not (Test-Path $example)) {
    Write-Error "Rust example binary not found: $example"
    exit 1
}
if ($OutDir -eq '') {
    $OutDir = Join-Path $targetDir 'python-differential-normalized'
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force $OutDir | Out-Null

# --- forward direction: Rust emits, Python compares ---------------------------
Write-Host "[2/4] forward: running the Rust example over $caseCount cases -> $OutDir"
& $example $CaseFile $OutDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "emit_normalized_results failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

# --- Python side: forward comparison + reverse emission -----------------------
$pythonEvidenceDir = Join-Path $targetDir 'python-differential-normalized-py'
$pythonEvidenceDir = [System.IO.Path]::GetFullPath($pythonEvidenceDir)
if (Test-Path $pythonEvidenceDir) { Remove-Item $pythonEvidenceDir -Recurse -Force }
Write-Host "[3/4] running the Python differential test (test_normalized.py) + emitting the Python evidence files -> $pythonEvidenceDir"
$env:CONSEMA_DIFFERENTIAL_NORMALIZED_RUST_DIR = $OutDir
$env:CONSEMA_DIFFERENTIAL_NORMALIZED_PYTHON_DIR = $pythonEvidenceDir
$logDir = Join-Path $env:TEMP 'consema-python-normalized'
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
    & $python -m pytest tests\differential\test_normalized.py -v 1> $stdoutFile 2> $stderrFile
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

# The differential test must have RUN (not skipped) and passed.
$output = Get-Content $stdoutFile -Raw
if ($output -match 'SKIPPED') {
    Write-Error 'the normalized differential test skipped: the Rust evidence directory was not provisioned'
    exit 1
}
if ($output -notmatch 'test_forward_differential PASSED' -or
    $output -notmatch 'test_emit_python_normalized_results_env PASSED') {
    Write-Error "the normalized differential tests did not pass (pytest exit $testCode)"
    if ($testCode -eq 0) { exit 1 } else { exit $testCode }
}
if ($testCode -ne 0) {
    exit $testCode
}

# --- reverse direction: Rust consumes and compares the Python evidence -------
Write-Host "[4/4] reverse: running the Rust consume mode against the Python evidence files ($pythonEvidenceDir)"
$reverseLog = Join-Path $logDir 'rust-consume.stdout.txt'
$reverseErr = Join-Path $logDir 'rust-consume.stderr.txt'
# Windows PowerShell 5.1: with $ErrorActionPreference = 'Stop' a native
# command writing to redirected stderr turns into a terminating
# NativeCommandError before the diagnostics can be captured, so the EAP
# is relaxed around this native call (the Rust emitter writes stderr).
$EAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $example $CaseFile $OutDir --consume $pythonEvidenceDir 1> $reverseLog 2> $reverseErr
$ErrorActionPreference = $EAP
$consumeCode = $LASTEXITCODE
Get-Content $reverseLog | ForEach-Object { Write-Host $_ }
if (Test-Path $reverseErr) {
    Get-Content $reverseErr | ForEach-Object { Write-Host $_ }
}
if ($consumeCode -ne 0) {
    Write-Error "the Rust consume mode found divergences or failed (exit $consumeCode)"
    exit $consumeCode
}
$reverseSummary = [regex]::Match((Get-Content $reverseLog -Raw), 'reverse normalized-result differential: \d+/\d+ equal')
if ($reverseSummary.Success) {
    Write-Host "RESULT (reverse): $($reverseSummary.Value)"
} else {
    Write-Error 'cannot find the reverse normalized-result differential summary line in the consume-mode output'
    exit 1
}
Write-Host "bidirectional normalized-result differential verification complete (exit 0)"
exit 0
