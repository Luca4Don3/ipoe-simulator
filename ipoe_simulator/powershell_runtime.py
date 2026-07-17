"""Windows PowerShell runtime discovery and invocation."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .app_logging import get_logger
from .network_backend import NetworkStateError


LOGGER = get_logger("powershell")


@dataclass(frozen=True)
class PowerShellRuntime:
    executable: str
    major: int
    minor: int
    patch: int
    edition: str

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def is_windows_powershell_51(self) -> bool:
        return self.major == 5 and self.minor == 1

    def run(self, script: str, *, timeout: int = 45) -> str:
        script = _UTF8_SETUP + script
        LOGGER.debug(
            "执行 PowerShell script_length=%s timeout_seconds=%s executable=%s",
            len(script),
            timeout,
            self.executable,
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            result = subprocess.run(
                [
                    self.executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkStateError(f"PowerShell 执行失败: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知 PowerShell 错误").strip()
            raise NetworkStateError(f"PowerShell 返回 {result.returncode}: {detail}")
        return result.stdout.strip()

    def run_json(self, script: str, *, timeout: int = 45) -> Any:
        output = self.run(script, timeout=timeout)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise NetworkStateError(
                f"PowerShell 未返回有效 JSON: {output[:300]}"
            ) from exc

    def probe_utf8_output(self) -> None:
        expected = "PowerShell UTF-8 probe: 中文"
        actual = self.run(f"Write-Output '{expected}'")
        if actual != expected:
            raise NetworkStateError(
                f"PowerShell UTF-8 输出探针失败: 期望 {expected!r}，实际 {actual!r}"
            )

    def probe_network_cmdlets(self) -> None:
        script = """
$ErrorActionPreference = 'Stop'
$required = @(
    'Get-NetAdapter',
    'Get-NetIPInterface',
    'Get-NetIPAddress',
    'Get-NetRoute',
    'Get-DnsClientServerAddress',
    'New-NetIPAddress',
    'Set-NetIPInterface',
    'Set-DnsClientServerAddress'
)
foreach ($name in $required) {
    Get-Command -Name $name -CommandType Cmdlet -ErrorAction Stop | Out-Null
}
'ok'
"""
        if self.run(script) != "ok":
            raise NetworkStateError("PowerShell 网卡 cmdlet 探针返回无效结果")


_UTF8_SETUP = (
    "$utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false; "
    "[Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8; "
)

_VERSION_SCRIPT = (
    _UTF8_SETUP
    + "$ErrorActionPreference='Stop'; "
    "[pscustomobject]@{ "
    "major=[int]$PSVersionTable.PSVersion.Major; "
    "minor=[int]$PSVersionTable.PSVersion.Minor; "
    "patch=[int]$PSVersionTable.PSVersion.Build; "
    "edition=[string]$PSVersionTable.PSEdition "
    "} | ConvertTo-Json -Compress"
)


def _candidate_paths(name: str) -> list[str]:
    paths: list[str] = []
    located = shutil.which(name)
    if located:
        paths.append(located)
    if name == "pwsh.exe":
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramW6432"),
            os.environ.get("LOCALAPPDATA"),
        ]
        suffixes = (
            os.path.join("PowerShell", "7", name),
            os.path.join("Microsoft", "PowerShell", "7", name),
        )
    else:
        roots = [os.environ.get("SystemRoot")]
        suffixes = (os.path.join("System32", "WindowsPowerShell", "v1.0", name),)
    extra_paths: list[str] = []
    for root in roots:
        if root:
            extra_paths.append(os.path.join(root, suffixes[0]))
            if len(suffixes) > 1:
                extra_paths.append(os.path.join(root, suffixes[1]))
    paths.extend(path for path in extra_paths if os.path.isfile(path))
    return list(dict.fromkeys(paths))


def _discover() -> PowerShellRuntime:
    if os.name != "nt":
        raise NetworkStateError("PowerShell 运行时仅支持 Windows")
    pwsh_candidates = _candidate_paths("pwsh.exe")
    powershell_candidates = _candidate_paths("powershell.exe")
    executable = (pwsh_candidates or powershell_candidates or [None])[0]
    name = "pwsh.exe" if pwsh_candidates else "powershell.exe"
    if not executable:
        raise NetworkStateError(
            "未找到受支持的 PowerShell 运行时；请安装 PowerShell 7（pwsh.exe）"
            "或启用 Windows PowerShell 5.1（powershell.exe）"
        )
    if not pwsh_candidates:
        LOGGER.info(
            "未找到 PowerShell 7，回退到 Windows PowerShell 5.1 executable=%s",
            executable,
        )
    encoded = base64.b64encode(_VERSION_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NetworkStateError(f"{name} 启动失败: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise NetworkStateError(f"{name} 返回 {result.returncode}: {detail}")
    try:
        data = json.loads(result.stdout.strip())
        major, minor, patch = (int(data[key]) for key in ("major", "minor", "patch"))
        edition = str(data.get("edition") or "unknown")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise NetworkStateError(f"{name} 版本信息无效: {exc}") from exc
    if (major, minor) < (5, 1):
        raise NetworkStateError(
            f"{name} 版本 {major}.{minor}.{patch} 过低；最低支持 PowerShell 5.1"
        )
    runtime = PowerShellRuntime(executable, major, minor, patch, edition)
    LOGGER.info(
        "PowerShell runtime selected executable=%s version=%s edition=%s fallback_5_1=%s",
        runtime.executable,
        runtime.version,
        runtime.edition,
        runtime.is_windows_powershell_51,
    )
    return runtime


@lru_cache(maxsize=1)
def get_powershell_runtime() -> PowerShellRuntime:
    return _discover()


def powershell_status() -> dict[str, object]:
    runtime = get_powershell_runtime()
    runtime.probe_utf8_output()
    runtime.probe_network_cmdlets()
    return {
        "executable": runtime.executable,
        "version": runtime.version,
        "edition": runtime.edition,
        "is_powershell_5_1_fallback": runtime.is_windows_powershell_51,
        "utf8_output": True,
        "network_cmdlets": True,
    }
