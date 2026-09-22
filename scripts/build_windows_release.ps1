[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('x86', 'x64', 'arm64')]
    [string] $Architecture,
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$RootDirectory = Split-Path -Parent $PSScriptRoot
$Version = [IO.File]::ReadAllText(
    (Join-Path $RootDirectory 'VERSION'),
    $Utf8NoBom
).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION 格式无效: $Version"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $RootDirectory '.temp\release'
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$WorkDirectory = Join-Path $RootDirectory ".temp\build-windows-$Architecture"
$PackageName = "ipoe-simulator-v$Version-windows-$Architecture"
$PackageDirectory = Join-Path $WorkDirectory $PackageName
$ArchivePath = Join-Path $OutputDirectory "$PackageName.zip"
$DependencyLock = Get-Content -LiteralPath (Join-Path $RootDirectory 'release-dependencies.json') -Raw | ConvertFrom-Json

if (Test-Path -LiteralPath $WorkDirectory) {
    Remove-Item -LiteralPath $WorkDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$rootFiles = @(
    'README.md',
    'LICENSE',
    'RELEASE_NOTES.md',
    'SECURITY.md',
    'THIRD-PARTY-NOTICES.txt',
    'VERSION',
    'check_env.py',
    'config.example.json',
    'coordinator.py',
    'extract_params.py',
    'ipoedhcp.py',
    'recovery_watchdog.py',
    'requirements.txt',
    'release-dependencies.json',
    'run.cmd'
)
foreach ($relativePath in $rootFiles) {
    Copy-Item `
        -LiteralPath (Join-Path $RootDirectory $relativePath) `
        -Destination (Join-Path $PackageDirectory $relativePath)
}
Copy-Item `
    -LiteralPath (Join-Path $RootDirectory 'ipoe_simulator') `
    -Destination (Join-Path $PackageDirectory 'ipoe_simulator') `
    -Recurse
New-Item `
    -ItemType Directory `
    -Path (Join-Path $PackageDirectory 'scripts') `
    -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $PSScriptRoot 'windows_launcher.ps1') `
    -Destination (Join-Path $PackageDirectory 'scripts\windows_launcher.ps1')
New-Item -ItemType Directory -Path (Join-Path $PackageDirectory 'licenses') -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $RootDirectory 'licenses\SCAPY-LICENSE.txt') `
    -Destination (Join-Path $PackageDirectory 'licenses\SCAPY-LICENSE.txt')

Get-ChildItem -LiteralPath $PackageDirectory -Recurse -Directory |
    Where-Object Name -eq '__pycache__' |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $PackageDirectory -Recurse -File -Include '*.pyc', '*.log' |
    Remove-Item -Force

if (Test-Path -LiteralPath $ArchivePath) {
    Remove-Item -LiteralPath $ArchivePath -Force
}
Compress-Archive `
    -LiteralPath $PackageDirectory `
    -DestinationPath $ArchivePath `
    -CompressionLevel Optimal

$VerifyArguments = @(
    (Join-Path $PSScriptRoot 'verify_windows_release.py'),
    $ArchivePath,
    '--architecture',
    $Architecture,
    '--version',
    $Version
)
python @VerifyArguments
if ($LASTEXITCODE -ne 0) {
    throw "Release ZIP 验证失败，退出码 $LASTEXITCODE"
}
Write-Host "构建完成: $ArchivePath"
