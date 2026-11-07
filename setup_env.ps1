# One-click environment setup script for Windows (PowerShell)
$ErrorActionPreference = "Stop"

Write-Host "1. Creating Python virtual environment (.venv)..." -ForegroundColor Cyan
python -m venv .venv

Write-Host "2. Upgrading pip and installing Python dependencies from requirements.txt..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host "3. Pre-building ESP32 firmware with PlatformIO..." -ForegroundColor Cyan
.\.venv\Scripts\pio.exe run -d firmware

Write-Host "`nSetup complete! Next steps:" -ForegroundColor Green
Write-Host "  - Plug in your ESP32 and run: .\flash_esp32.ps1"
Write-Host "  - Trigger a pulse measurement: .\run_pulse.ps1 -RRef 470"
Write-Host "  - Or run offline simulation:   .\.venv\Scripts\python.exe -m src.simulate_and_verify"
