@echo off
setlocal
rem Prefer the self-contained launcher (works even when PowerShell 5.1
rem Start-Process fails on duplicate PATH / Path environment keys).
set "PYEXE=%~dp0..\.venv\Scripts\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" "%~dp0launch.py"
    if errorlevel 1 (
        echo.
        echo Startup failed. See 数仓\server.stdout.log and server.stderr.log
        pause
        exit /b 1
    )
    start "" "http://127.0.0.1:8765"
    exit /b 0
)
echo .venv not found, falling back to start.ps1 ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
