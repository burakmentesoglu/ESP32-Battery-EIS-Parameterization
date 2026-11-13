# Launch the ESP32 Battery Pulse & EIS Parameterization Desktop GUI
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] Virtual environment not found at: $VenvPython" -ForegroundColor Red
    Write-Host "Please run Easy_Setup_Wizard.bat first to install Python dependencies." -ForegroundColor Yellow
    pause
    exit 1
}

& $VenvPython -m src.gui_app @args
