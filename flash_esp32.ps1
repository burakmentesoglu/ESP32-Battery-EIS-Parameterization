# One-click script to upload the pre-compiled firmware to ESP32 once plugged in via USB
$ErrorActionPreference = "Stop"
Write-Host "Uploading Battery Pulse Parameterization Firmware to ESP32..." -ForegroundColor Cyan
.\.venv\Scripts\pio.exe run -d firmware -t upload
Write-Host "Upload complete! You can now run .\run_pulse.ps1 to trigger a pulse measurement." -ForegroundColor Green
