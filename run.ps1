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

.EXAMPLE
    .\run.ps1
    Provision (first run) + lint + format-check + type-check + test.

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
    [switch] $SkipTests
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Find-Python {
    # Resolve a real CPython 3.13+ interpreter. The bare `python` on PATH is often
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
    throw "No real CPython 3.13+ interpreter found. Install one (e.g. winget install Python.Python.3.13)."
}

$python = Find-Python
$venvDir = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not $SkipInstall) {
    if (-not (Test-Path $venvPython)) {
        Write-Host "==> Creating .venv ..." -ForegroundColor Cyan
        & $python -m venv $venvDir
    }
    Write-Host "==> Installing fuaran-py + dev tooling ..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -e ".[dev]" --quiet
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
