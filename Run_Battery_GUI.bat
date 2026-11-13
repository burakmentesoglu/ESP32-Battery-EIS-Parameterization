@echo off
title ESP32 Battery Pulse and EIS Studio GUI
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" /d "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m src.gui_app %*
    exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m src.gui_app %*
    if %ERRORLEVEL% NEQ 0 pause
    exit /b %ERRORLEVEL%
)

echo [ERROR] Python virtual environment (.venv) not found.
echo Please double-click Easy_Setup_Wizard.bat first to install everything automatically.
echo.
pause
exit /b 1
