@echo off
setlocal
fltmc >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 coordinator.py %*
) else (
  python coordinator.py %*
)
set "exit_code=%errorlevel%"
pause
exit /b %exit_code%
