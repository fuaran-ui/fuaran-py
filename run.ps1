<#
.SYNOPSIS
    Stage-0 entry point for the fuaran-py sibling — the happy-path verify gate.
.DESCRIPTION
    Per the workspace "Every new sibling ships a run.ps1" mandate, this is the
    "drop into the repo, run one command, the thing works" entry point. It
    resolves a real CPython interpreter (skipping the Windows Store alias stub),
    provisions a local .venv with the dev tooling on first run, then runs the
    full gate:

        ruff check  (lint)  ->  ruff format --check  ->  mypy  ->  pytest

    The Python analogue of the F#-side `dotnet tool restore -> fantomas --check
    -> build -> test` Stage-0 shape.

.PARAMETER SkipInstall   Skip provisioning / refreshing the .venv.
.PARAMETER SkipFormat    Skip the `ruff format --check` gate.
.PARAMETER SkipLint      Skip the `ruff check` lint gate.
.PARAMETER SkipTypecheck Skip the `mypy` gate.
.PARAMETER SkipTests     Skip `pytest`.
.PARAMETER ReleaseCheck  The PRE-TAG check, and nothing else: refuse when the committed corpus
                         snapshot is behind the specification's `main` as its remote reports it
                         now (`sync_corpus.py --check --remote`). Run it before `git tag`; it needs
                         no .venv (standard library + git) and no gate stage runs.

.EXAMPLE
    .\run.ps1
    Provision (first run) + lint + format-check + type-check + test.

.EXAMPLE
    .\run.ps1 -ReleaseCheck
    Before tagging a release: exit 0 only when the corpus snapshot is the specification's `main`.

.EXAMPLE
    .\run.ps1 -SkipInstall -SkipLint -SkipFormat -SkipTypecheck
    Run the tests only against an already-provisioned .venv.
#>

#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch] $SkipInstall,
    [switch] $SkipFormat,
    [switch] $SkipLint,
    [switch] $SkipTypecheck,
    [switch] $SkipTests,
    [switch] $ReleaseCheck
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# SEED $LASTEXITCODE BEFORE ANY STAGE READS IT.
#
# PowerShell leaves it UNSET until the session's first native command, and it does not
# reset it between commands afterwards. Both halves bite:
#
#   * unset, `$LASTEXITCODE -ne 0` is `$null -ne 0`, which is TRUE — so a check reached
#     before any native command runs reports a failure that did not happen;
#   * set, it survives into a stage that ran no native command of its own, which is then
#     graded on whatever the last unrelated one left behind.
#
# The sibling `Fuaran-Program` launcher shipped the first half and returned a green `exit
# $null` having built and tested nothing. Seeding it costs one line and removes the class.
$LASTEXITCODE = 0

function Find-Python {
    # Resolve a real CPython 3.12+ interpreter. The bare `python` on PATH is often
    # the Windows Store alias stub (under WindowsApps), which is not a usable
    # interpreter — skip it explicitly.
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) { return $venvPython }

    $candidates = @()
    foreach ($cmd in (Get-Command python.exe, python3.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        if ($cmd.Source -notmatch 'WindowsApps') { $candidates += $cmd.Source }
    }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
    $candidates += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "No real CPython 3.12+ interpreter found. Install one (e.g. winget install Python.Python.3.13)."
}

$python = Find-Python

if ($ReleaseCheck) {
    # The pre-tag check. A `v*` tag publishes, and the publish job refuses a snapshot behind the
    # specification's main - it checks that out fresh, so the question is about main NOW, not about
    # what the local clone last fetched. A version cannot be re-tagged once it is refused, so ask
    # before the tag exists. Exit 1 means it could not ask, 2 means behind; both refuse the tag.
    Write-Host "==> release check: corpus snapshot vs the specification's main" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $python conformance/sync_corpus.py --check --remote
    exit $LASTEXITCODE
}

$venvDir = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not $SkipInstall) {
    # EVERY native command here is checked, including the ones that only PROVISION.
    #
    # `$ErrorActionPreference = 'Stop'` governs cmdlet errors only, so before this a failed
    # `python -m venv` or `pip install` left the script running. The gate stages below would
    # usually still catch it — a venv with no ruff exits non-zero — but they catch it as a
    # confusing tool failure three stages later, and the case they do NOT catch is the
    # dangerous one: the tools are already present from a previous run, so a failed
    # `pip install -e` leaves the suite measuring a STALE dependency set while every gate
    # reports green. A provisioning step is part of what the gate asserts, not preamble to
    # it.
    if (-not (Test-Path $venvPython)) {
        Write-Host "==> Creating .venv ..." -ForegroundColor Cyan
        & $python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "creating the .venv failed (exit $LASTEXITCODE)" }
    }
    Write-Host "==> Installing fuaran-py + dev tooling ..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip --quiet
    if ($LASTEXITCODE -ne 0) { throw "upgrading pip failed (exit $LASTEXITCODE)" }
    & $venvPython -m pip install -e ".[dev]" --quiet
    if ($LASTEXITCODE -ne 0) { throw "installing the package + dev tooling failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path $venvPython)) { $venvPython = $python }

if (-not $SkipLint) {
    Write-Host "==> ruff check" -ForegroundColor Cyan
    & $venvPython -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
}

if (-not $SkipFormat) {
    Write-Host "==> ruff format --check" -ForegroundColor Cyan
    & $venvPython -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "ruff format check failed (run: ruff format .)" }
}

if (-not $SkipTypecheck) {
    Write-Host "==> mypy" -ForegroundColor Cyan
    & $venvPython -m mypy
    if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
}

if (-not $SkipTests) {
    Write-Host "==> pytest" -ForegroundColor Cyan
    & $venvPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

Write-Host "`nfuaran-py: all gates green." -ForegroundColor Green
