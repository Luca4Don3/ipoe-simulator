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
    exit $ExitCode
}

function Stop-RestoreArguments {
    param([Parameter(Mandatory = $true)][string] $Message)

    Write-LauncherLog -Level ERROR -Message $Message
    [Console]::Error.WriteLine("错误: $Message")
    [Console]::Error.WriteLine('唯一正确命令: .\run.cmd --restore')
    exit 2
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

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string] $Url,
        [Parameter(Mandatory = $true)][string] $ExpectedSha256,
        [Parameter(Mandatory = $true)][string] $Destination
    )

    Write-LauncherLog -Level INFO -Message "下载运行依赖 url=$Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination
    $actual = Get-FileSha256 -Path $Destination
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "依赖 SHA-256 不匹配: expected=$ExpectedSha256 actual=$actual"
    }
}

function Remove-StaleTemporaryFiles {
    $temporaryDirectory = Join-Path $RootDirectory '.temp'
    if (-not (Test-Path -LiteralPath $temporaryDirectory -PathType Container)) {
        return
    }
    $cutoff = (Get-Date).AddHours(-24)
    foreach ($directory in @(Get-ChildItem -LiteralPath $temporaryDirectory -Directory -Filter 'runtime-install-*')) {
        $ownerPath = Join-Path $directory.FullName 'owner.json'
        $remove = $false
        if (Test-Path -LiteralPath $ownerPath -PathType Leaf) {
            try {
                $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
                Get-Process -Id ([int] $owner.pid) -ErrorAction Stop | Out-Null
            } catch {
                $remove = $true
            }
        } elseif ($directory.LastWriteTime -lt $cutoff) {
            $remove = $true
        }
        if ($remove) {
            try {
                Remove-Item -LiteralPath $directory.FullName -Recurse -Force
            } catch {
                Write-LauncherLog -Level INFO -Message (
                    "临时目录清理失败 path=$($directory.FullName) error=$($_.Exception.Message)"
                )
            }
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $temporaryDirectory -File -Filter '*.tmp' -Recurse)) {
        if ($file.LastWriteTime -lt $cutoff) {
            try {
                Remove-Item -LiteralPath $file.FullName -Force
            } catch {
                Write-LauncherLog -Level INFO -Message (
                    "临时文件清理失败 path=$($file.FullName) error=$($_.Exception.Message)"
                )
            }
        }
    }
}

function Install-PythonRuntime {
    param([Parameter(Mandatory = $true)][string] $Architecture)

    $lockPath = Join-Path $RootDirectory 'release-dependencies.json'
    $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
    $python = $lock.python.architectures.$Architecture
    if ($null -eq $python) {
        throw "依赖清单缺少 Python $Architecture"
    }
    $downloadDirectory = Join-Path $RootDirectory '.temp\downloads'
    $stagingDirectory = Join-Path $RootDirectory (
        '.temp\runtime-install-' + [Guid]::NewGuid().ToString('N')
    )
    $runtimeDirectory = Join-Path $RootDirectory 'runtime'
    if (Test-Path -LiteralPath $runtimeDirectory) {
        throw "runtime 目录已存在但没有可用的 Python，请保留日志并检查该目录"
    }
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $stagingDirectory -Force | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $stagingDirectory 'owner.json'),
        ('{"pid":' + $PID + '}'),
        $Utf8NoBom
    )
    $pythonArchive = Join-Path $downloadDirectory (
        "python-$($lock.python.version)-embeddable-$Architecture.zip"
    )
    $scapyArchive = Join-Path $downloadDirectory "scapy-$($lock.scapy.version).whl"
    try {
        Invoke-VerifiedDownload `
            -Url ([string] $python.url) `
            -ExpectedSha256 ([string] $python.sha256) `
            -Destination $pythonArchive
        Expand-Archive -LiteralPath $pythonArchive -DestinationPath $stagingDirectory

        $pth = Get-ChildItem -LiteralPath $stagingDirectory -Filter 'python*._pth' |
            Select-Object -First 1 -ExpandProperty FullName
        if ([string]::IsNullOrWhiteSpace($pth)) {
            throw 'Python embeddable runtime 缺少 python*._pth'
        }
        $pthContent = [IO.File]::ReadAllText($pth, $Utf8NoBom)
        $pthContent = $pthContent -replace '#import site', 'import site'
        if ($pthContent -notmatch '(?m)^\.\.$') {
            $pthContent = $pthContent.TrimEnd("`r", "`n") + "`r`n..`r`n"
        }
        if ($pthContent -notmatch '(?m)^Lib\\site-packages$') {
            $pthContent = (
                $pthContent.TrimEnd("`r", "`n") +
                "`r`nLib\site-packages`r`n"
            )
        }
        [IO.File]::WriteAllText($pth, $pthContent, $Utf8NoBom)

        $sitePackages = Join-Path $stagingDirectory 'Lib\site-packages'
        New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
        Invoke-VerifiedDownload `
            -Url ([string] $lock.scapy.url) `
            -ExpectedSha256 ([string] $lock.scapy.sha256) `
            -Destination $scapyArchive
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($scapyArchive, $sitePackages)
        Remove-Item -LiteralPath (Join-Path $stagingDirectory 'owner.json') -Force
        Move-Item -LiteralPath $stagingDirectory -Destination $runtimeDirectory
        Remove-Item -LiteralPath $pythonArchive -Force
        Remove-Item -LiteralPath $scapyArchive -Force
        Write-LauncherLog -Level INFO -Message (
            "Python runtime 安装完成 version=$($lock.python.version) architecture=$Architecture"
        )
    } catch {
        if (Test-Path -LiteralPath $stagingDirectory) {
            Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
        }
        throw
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
bits = struct.calcsize('P') * 8
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
        return Test-PythonCandidate `
            -Executable $bundled `
            -PrefixArguments @() `
            -RequiredArchitecture $RequiredArchitecture
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
Remove-StaleTemporaryFiles
Write-LauncherLog -Level INFO -Message (
    "PowerShell 启动 edition=$($PSVersionTable.PSEdition) version=$($PSVersionTable.PSVersion)"
)

$restoreMode = $false
if ($LauncherArguments -contains '-restore') {
    Stop-RestoreArguments -Message '不支持 -restore'
}
if ($LauncherArguments -contains '--restore') {
    if ($LauncherArguments.Count -eq 1 -and $LauncherArguments[0] -eq '--restore') {
        $restoreMode = $true
    } elseif (
        $LauncherArguments.Count -eq 3 -and
        $LauncherArguments[0] -eq '--restore' -and
        $LauncherArguments[1] -eq '--log-level' -and
        $LauncherArguments[2] -in @('INFO', 'DEBUG')
    ) {
        $restoreMode = $true
    } else {
        Stop-RestoreArguments -Message '--restore 仅允许附加 --log-level INFO|DEBUG'
    }
}

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

$bundledPython = Join-Path $RootDirectory 'runtime\python.exe'
$journalPath = Join-Path $RootDirectory '.temp\network-recovery.json'
if (
    $restoreMode -and
    (Test-Path -LiteralPath $journalPath -PathType Leaf) -and
    -not (Test-Path -LiteralPath $bundledPython -PathType Leaf)
) {
    Write-LauncherLog -Level INFO -Message '存在待恢复 journal，优先安装锁定运行时'
    try {
        Install-PythonRuntime -Architecture $requiredArchitecture
    } catch {
        Stop-Launcher -Message "Python 运行时自动安装失败: $($_.Exception.Message)" -ExitCode 5
    }
}

$python = Find-Python -RequiredArchitecture $requiredArchitecture
if ($null -eq $python) {
    Write-LauncherLog -Level INFO -Message (
        "未找到 Python 3.9–3.14（$requiredArchitecture），开始安装锁定运行时"
    )
    try {
        Install-PythonRuntime -Architecture $requiredArchitecture
        $python = Find-Python -RequiredArchitecture $requiredArchitecture
        if ($null -eq $python) {
            throw '运行时安装完成但 Python 验证失败'
        }
    } catch {
        Stop-Launcher -Message "Python 运行时自动安装失败: $($_.Exception.Message)" -ExitCode 5
    }
}

Write-LauncherLog -Level INFO -Message (
    "Python 已选择 executable=$($python.Executable) version=$($python.Version) architecture=$($python.Architecture)"
)

$businessScript = if ($restoreMode) {
    Join-Path $RootDirectory 'ipoedhcp.py'
} else {
    Join-Path $RootDirectory 'coordinator.py'
}
try {
    & $python.Executable @($python.PrefixArguments) -u $businessScript @LauncherArguments
    $exitCode = $LASTEXITCODE
} catch {
    Write-LauncherLog -Level ERROR -Message "业务进程启动失败 error=$($_.Exception.Message)"
    [Console]::Error.WriteLine("错误: 业务进程启动失败: $($_.Exception.Message)")
    $exitCode = 6
}

if ($restoreMode) {
    Write-LauncherLog -Level $(if ($exitCode -eq 0) { 'INFO' } else { 'ERROR' }) -Message (
        "恢复进程结束 exit_code=$exitCode"
    )
    exit $exitCode
} elseif ($exitCode -ne 0) {
    Write-LauncherLog -Level ERROR -Message "业务进程异常退出 exit_code=$exitCode"
    [Console]::Error.WriteLine("错误: IPoE Simulator 异常退出，退出码 $exitCode。")
} else {
    Write-LauncherLog -Level INFO -Message '业务进程正常退出 exit_code=0'
}
exit $exitCode
