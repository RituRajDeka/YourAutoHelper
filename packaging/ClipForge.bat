@echo off
REM =====================================================================
REM  ClipForge - just double-click this file.
REM  On the first run it downloads everything it needs (Python, libraries,
REM  ffmpeg, AI model) into this same folder. Later runs open instantly.
REM  Nothing else to install.
REM =====================================================================
setlocal
cd /d "%~dp0"

echo.
echo   Starting ClipForge... (the first-time setup takes a while)
echo.

REM Get the latest launcher from GitHub
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Ai-Haris/clipforge/main/packaging/run.ps1' -OutFile 'run.ps1'"
if not exist "run.ps1" (
  echo.
  echo   Could not download the launcher. Check your internet and try again.
  echo.
  pause
  exit /b 1
)

REM Run the installer + app
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"

echo.
echo   ClipForge has stopped. You can close this window.
pause >nul
