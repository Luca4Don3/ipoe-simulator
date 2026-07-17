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
$PythonVersion = '3.11.9'
$ScapyVersion = '2.6.1'

$PythonArchiveNames = @{
    x86 = "python-$PythonVersion-embed-win32.zip"
    x64 = "python-$PythonVersion-embed-amd64.zip"
    arm64 = "python-$PythonVersion-embed-arm64.zip"
}
$PythonArchiveName = $PythonArchiveNames[$Architecture]
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchiveName"
$DownloadDirectory = Join-Path $RootDirectory '.temp\downloads'
$PythonArchive = Join-Path $DownloadDirectory $PythonArchiveName

if (Test-Path -LiteralPath $WorkDirectory) {
    Remove-Item -LiteralPath $WorkDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $DownloadDirectory -Force | Out-Null

$rootFiles = @(
    'README.md',
    'LICENSE',
    'RELEASE_NOTES.md',
    'THIRD-PARTY-NOTICES.txt',
    'VERSION',
    'check_env.py',
    'config.example.json',
    'coordinator.py',
    'extract_params.py',
    'ipoedhcp.py',
    'recovery_watchdog.py',
    'requirements.txt',
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

if (-not (Test-Path -LiteralPath $PythonArchive -PathType Leaf)) {
    Write-Host "下载 Python $PythonVersion $Architecture`: $PythonUrl"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonArchive
}
$RuntimeDirectory = Join-Path $PackageDirectory 'runtime'
Expand-Archive -LiteralPath $PythonArchive -DestinationPath $RuntimeDirectory

$PthPath = Get-ChildItem `
    -LiteralPath $RuntimeDirectory `
    -Filter 'python*._pth' |
    Select-Object -First 1 -ExpandProperty FullName
if ([string]::IsNullOrWhiteSpace($PthPath)) {
    throw 'Python embeddable runtime 缺少 python*._pth'
}
$PthContent = [IO.File]::ReadAllText($PthPath, $Utf8NoBom)
$PthContent = $PthContent -replace '#import site', 'import site'
if ($PthContent -notmatch '(?m)^Lib\\site-packages$') {
    $PthContent = $PthContent.TrimEnd("`r", "`n") + "`r`nLib\site-packages`r`n"
}
[IO.File]::WriteAllText($PthPath, $PthContent, $Utf8NoBom)

$SitePackages = Join-Path $RuntimeDirectory 'Lib\site-packages'
New-Item -ItemType Directory -Path $SitePackages -Force | Out-Null
python -m pip install `
    --disable-pip-version-check `
    --no-input `
    --no-compile `
    --target $SitePackages `
    "scapy==$ScapyVersion"
if ($LASTEXITCODE -ne 0) {
    throw "Scapy 安装失败，退出码 $LASTEXITCODE"
}

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
if ($Architecture -ne 'arm64') {
    $VerifyArguments += '--execute-runtime'
}
python @VerifyArguments
if ($LASTEXITCODE -ne 0) {
    throw "Release ZIP 验证失败，退出码 $LASTEXITCODE"
}
Write-Host "构建完成: $ArchivePath"
