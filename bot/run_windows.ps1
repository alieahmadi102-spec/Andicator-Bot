# SNRZ bot launcher for Windows / Windows Server.
#
#   .\run_windows.ps1              -> 5-minute chart, DRY RUN (nothing is sent)
#   .\run_windows.ps1 -Tf 60       -> 1-hour chart
#   .\run_windows.ps1 -Live        -> actually send orders (asks first)
#
# It checks the three things that actually go wrong -- Python missing, the
# MetaTrader5 package missing, the terminal not running -- and says which one
# it is instead of dying with a stack trace.

param(
    [int]    $Tf     = 5,
    [string] $Symbol = "XAUUSD",
    [double] $Risk   = 1.0,
    [switch] $Live
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  SNRZ bot launcher" -ForegroundColor Cyan
Write-Host "  -----------------" -ForegroundColor Cyan

# 1) Python -------------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "`n  Python is not installed (or not on PATH)." -ForegroundColor Red
    Write-Host "  Get it from https://www.python.org/downloads/windows/"
    Write-Host "  During setup TICK 'Add python.exe to PATH', then reopen PowerShell."
    exit 1
}
Write-Host "  python    $(& $py.Source --version 2>&1)"

# 2) the MetaTrader5 package --------------------------------------------------
& $py.Source -c "import MetaTrader5" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  installing the MetaTrader5 package ..." -ForegroundColor Yellow
    & $py.Source -m pip install --quiet --upgrade pip
    & $py.Source -m pip install --quiet MetaTrader5
    & $py.Source -c "import MetaTrader5" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n  pip could not install MetaTrader5." -ForegroundColor Red
        Write-Host "  It needs 64-bit Python on Windows. Check: python -c `"import platform;print(platform.architecture())`""
        exit 1
    }
}
Write-Host "  MetaTrader5 package ok"

# 3) the terminal itself ------------------------------------------------------
if (-not (Get-Process terminal64, terminal -ErrorAction SilentlyContinue)) {
    Write-Host "`n  MetaTrader 5 does not look like it is running." -ForegroundColor Red
    Write-Host "  Open it, log in to the account you want, and run this again."
    Write-Host "  (The bot talks to the terminal -- it cannot log in by itself.)"
    exit 1
}
Write-Host "  MetaTrader 5 is running"

# 4) go -----------------------------------------------------------------------
$argv = @("mt5_runner.py", "--tf", $Tf, "--symbol", $Symbol, "--risk", $Risk)
if ($Live) {
    Write-Host "`n  *** LIVE MODE -- real orders will be sent ***" -ForegroundColor Red
    $argv += "--live"
} else {
    Write-Host "  mode      DRY RUN (nothing is sent to the broker)" -ForegroundColor Green
}
Write-Host ""
& $py.Source $argv
