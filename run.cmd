@echo off
setlocal
fltmc >nul 2>&1
if errorlevel 1 (
  set "ps_exe="
  where pwsh.exe >nul 2>&1
  if not errorlevel 1 set "ps_exe=pwsh.exe"
  if not defined ps_exe (
    where powershell.exe >nul 2>&1
    if not errorlevel 1 set "ps_exe=powershell.exe"
  )
  if not defined ps_exe (
    echo 未找到 pwsh.exe 或 powershell.exe 以请求管理员权限 1>&2
    exit /b 1
  )
  "%ps_exe%" -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
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
