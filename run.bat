@echo off
REM Protein Peak-based Protein Matching Software V1.0
REM Copyright (c) 2026 Zhang Geyang
REM Independently developed; no third-party open-source code used.
REM ============================================================
REM  Windows launcher (thin shell). All logic lives in run.py;
REM  environment setup lives in env_bootstrap.ps1 (which creates
REM  the project-local .venv on first run).
REM  Usage: double-click, or:  run.bat [--port 8000] [--no-browser]
REM ============================================================
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [run] Virtual environment not found. Bootstrapping environment...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0env_bootstrap.ps1"
  if errorlevel 1 goto bootstrap_failed
)

if not exist "%VENV_PY%" (
  echo [run] Python interpreter still missing: %VENV_PY%
  goto bootstrap_failed
)

echo [run] Starting service with %VENV_PY%
"%VENV_PY%" "%~dp0run.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [run] Service exited with code %RC%.
  pause
)
endlocal & exit /b %RC%

:bootstrap_failed
echo.
echo [run] Environment bootstrap failed. Please check env_bootstrap.ps1 output.
pause
endlocal & exit /b 1
