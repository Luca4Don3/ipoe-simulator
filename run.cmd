@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

>>"ipoe-simulator.log" echo %date% %time% INFO launcher: run.cmd 启动 path=%~f0

set "ps_exe="
where pwsh.exe >nul 2>&1
if not errorlevel 1 set "ps_exe=pwsh.exe"
if not defined ps_exe (
  where powershell.exe >nul 2>&1
  if not errorlevel 1 set "ps_exe=powershell.exe"
)
if not defined ps_exe (
  >>"ipoe-simulator.log" echo %date% %time% ERROR launcher: 未找到 pwsh.exe 或 powershell.exe
  >&2 echo 错误: 未找到 PowerShell 7 或 Windows PowerShell 5.1。
  pause
  exit /b 5
)

"%ps_exe%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_launcher.ps1" %*
set "exit_code=%errorlevel%"
exit /b %exit_code%
