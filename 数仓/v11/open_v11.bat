@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem 已有服务在跑就不再重复启动
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo 启动 v11 数仓服务...
  start "v11 数仓服务" /min "%PY%" "%~dp0server11.py"
  timeout /t 3 >nul
) else (
  echo v11 数仓服务已在运行。
)

start "" "http://127.0.0.1:8770/"
exit /b 0
