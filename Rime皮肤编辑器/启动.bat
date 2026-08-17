@echo off
chcp 65001 >nul
set "EDITOR_DIR=%~dp0"
for %%I in ("%EDITOR_DIR%..") do set "RIME_ROOT=%%~fI"
set "SERVER=%EDITOR_DIR%local\local_server.ps1"
where powershell.exe >nul 2>nul
if not errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SERVER%" -Root "%RIME_ROOT%"
  exit /b %errorlevel%
)
where powershell >nul 2>nul
if not errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SERVER%" -Root "%RIME_ROOT%"
  exit /b %errorlevel%
)
echo PowerShell was not found. Please open index.html and choose the Rime config folder in Chrome or Edge.
pause
exit /b 1
