@echo off
chcp 65001 >nul
title Data Sanctuary
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [!] Python not found.
  echo       Install Python 3.10+ and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

python "%~dp0app\server\run.py" %*
if errorlevel 1 (
  echo.
  echo   [!] Startup failed. Copy the message above when reporting.
  echo.
  pause
)
endlocal
