$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$LauncherArguments = @($args)

$RootDirectory = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $RootDirectory 'ipoe-simulator.log'

function Write-LauncherLog {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('INFO', 'ERROR')]
        [string] $Level,
        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    $line = '{0} {1} launcher: {2}{3}' -f (
        Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    ), $Level, $Message, [Environment]::NewLine
    [IO.File]::AppendAllText($LogPath, $line, $Utf8NoBom)
}

function Stop-Launcher {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Message,
        [int] $ExitCode = 1
    )

    Write-LauncherLog -Level ERROR -Message $Message
    [Console]::Error.WriteLine("错误: $Message")
    [void] (Read-Host '按 Enter 键关闭窗口')
    exit $ExitCode
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string] $Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-NativeWindowsArchitecture {
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = $env:PROCESSOR_ARCHITECTURE
    }
    switch -Regex ($architecture.ToUpperInvariant()) {
        '^(AMD64|X86_64)$' { return 'x64' }
        '^(ARM64|AARCH64)$' { return 'arm64' }
        '^(X86|I[3-6]86)$' { return 'x86' }
        default { throw "不支持的 Windows 架构: $architecture" }
    }
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Executable,
        [string[]] $PrefixArguments = @(),
        [Parameter(Mandatory = $true)]
        [string] $RequiredArchitecture
    )

    $probe = @'
import json, platform, struct, sys
machine = platform.machine().lower()
bits = struct.calcsize("P") * 8
if bits == 32:
    architecture = "x86"
elif machine in ("amd64", "x86_64"):
    architecture = "x64"
elif machine in ("arm64", "aarch64"):
    architecture = "arm64"
else:
    architecture = machine
print(json.dumps({"version": list(sys.version_info[:3]), "architecture": architecture}))
'@
    try {
        $output = & $Executable @PrefixArguments -c $probe 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "探测退出码 $LASTEXITCODE`: $output"
        }
        $result = $output | ConvertFrom-Json
        $version = [Version]::new(
            [int] $result.version[0],
            [int] $result.version[1],
            [int] $result.version[2]
        )
        if ($version -lt [Version]'3.9.0' -or $version -ge [Version]'3.15.0') {
            throw "Python $version 不在支持范围 3.9–3.14"
        }
        if ([string] $result.architecture -ne $RequiredArchitecture) {
            throw "Python 架构 $($result.architecture) 与 Windows 架构 $RequiredArchitecture 不匹配"
        }
        return [pscustomobject]@{
            Executable = $Executable
            PrefixArguments = $PrefixArguments
            Version = $version.ToString()
            Architecture = [string] $result.architecture
        }
    } catch {
        Write-LauncherLog -Level INFO -Message (
            "跳过 Python 候选 executable=$Executable reason=$($_.Exception.Message)"
        )
        return $null
    }
}

function Find-Python {
    param([Parameter(Mandatory = $true)][string] $RequiredArchitecture)

    $candidates = @()
    $bundled = Join-Path $RootDirectory 'runtime\python.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        $candidates += ,@($bundled, @())
    }
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $candidates += ,@($launcher.Source, @('-3'))
    }
    foreach ($name in @('python.exe', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $candidates += ,@($command.Source, @())
        }
    }

    foreach ($candidate in $candidates) {
        $result = Test-PythonCandidate `
            -Executable $candidate[0] `
            -PrefixArguments $candidate[1] `
            -RequiredArchitecture $RequiredArchitecture
        if ($null -ne $result) {
            return $result
        }
    }
    return $null
}

Set-Location -LiteralPath $RootDirectory
Write-LauncherLog -Level INFO -Message (
    "PowerShell 启动 edition=$($PSVersionTable.PSEdition) version=$($PSVersionTable.PSVersion)"
)

if (-not (Test-IsAdministrator)) {
    $powerShellExecutable = (Get-Process -Id $PID).Path
    $arguments = @(
        '-NoLogo',
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        $PSCommandPath
    ) + $LauncherArguments
    $argumentLine = ($arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    Write-LauncherLog -Level INFO -Message "请求管理员权限 executable=$powerShellExecutable"
    try {
        $process = Start-Process `
            -FilePath $powerShellExecutable `
            -ArgumentList $argumentLine `
            -Verb RunAs `
            -Wait `
            -PassThru
        Write-LauncherLog -Level INFO -Message "提权进程结束 exit_code=$($process.ExitCode)"
        exit $process.ExitCode
    } catch {
        $message = if ($_.Exception.NativeErrorCode -eq 1223) {
            '用户取消了管理员权限请求'
        } else {
            "无法请求管理员权限: $($_.Exception.Message)"
        }
        Stop-Launcher -Message $message -ExitCode 5
    }
}

try {
    $requiredArchitecture = Get-NativeWindowsArchitecture
} catch {
    Stop-Launcher -Message $_.Exception.Message -ExitCode 5
}

$python = Find-Python -RequiredArchitecture $requiredArchitecture
if ($null -eq $python) {
    Stop-Launcher `
        -Message "未找到 Python 3.9–3.14（$requiredArchitecture）。请使用对应架构的便携包或安装匹配的 Python。" `
        -ExitCode 5
}

Write-LauncherLog -Level INFO -Message (
    "Python 已选择 executable=$($python.Executable) version=$($python.Version) architecture=$($python.Architecture)"
)

$coordinator = Join-Path $RootDirectory 'coordinator.py'
try {
    & $python.Executable @($python.PrefixArguments) -u $coordinator @LauncherArguments
    $exitCode = $LASTEXITCODE
} catch {
    Write-LauncherLog -Level ERROR -Message "业务进程启动失败 error=$($_.Exception.Message)"
    [Console]::Error.WriteLine("错误: 业务进程启动失败: $($_.Exception.Message)")
    $exitCode = 6
}

if ($exitCode -ne 0) {
    Write-LauncherLog -Level ERROR -Message "业务进程异常退出 exit_code=$exitCode"
    [Console]::Error.WriteLine("错误: IPoE Simulator 异常退出，退出码 $exitCode。")
} else {
    Write-LauncherLog -Level INFO -Message '业务进程正常退出 exit_code=0'
}
[void] (Read-Host '按 Enter 键关闭窗口')
exit $exitCode
