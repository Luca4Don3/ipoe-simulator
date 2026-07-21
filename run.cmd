@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

>>"ipoe-simulator.log" echo %date% %time% INFO launcher: run.cmd 启动 path=%~f0

if /i "%~1"=="-restore" goto invalid_restore
if /i "%~1"=="--restore" goto restore

:launcher

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

:restore
if "%~2"=="" goto restore_ready
if /i not "%~2"=="--log-level" goto invalid_restore
if "%~4" neq "" goto invalid_restore
if /i "%~3"=="INFO" goto restore_ready
if /i "%~3"=="DEBUG" goto restore_ready
goto invalid_restore

:restore_ready
if not exist "%~dp0runtime\python.exe" goto launcher
net session >nul 2>&1
if errorlevel 1 goto launcher
"%~dp0runtime\python.exe" -u "%~dp0ipoedhcp.py" %*
set "exit_code=%errorlevel%"
exit /b %exit_code%

:invalid_restore
>&2 echo 错误: --restore 仅允许附加 --log-level INFO^|DEBUG。
>&2 echo 唯一正确命令: .\run.cmd --restore
exit /b 2
