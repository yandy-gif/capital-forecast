@echo off
chcp 65001 >nul
setlocal
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if "%~1"=="" (
  echo 把 .xls 文件拖到本 bat 上，即可在同目录生成同名 .xlsx
  pause
  exit /b 0
)
"%PY%" "%~dp0convert_xls.py" %*
echo.
pause
