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

from .network_backend import NetworkStateError


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


_VERSION_SCRIPT = (
    "$ErrorActionPreference='Stop'; "
    "[pscustomobject]@{ "
    "major=[int]$PSVersionTable.PSVersion.Major; "
    "minor=[int]$PSVersionTable.PSVersion.Minor; "
    "patch=[int]$PSVersionTable.PSVersion.Build; "
    "edition=[string]$PSVersionTable.PSEdition "
    "} | ConvertTo-Json -Compress"
)


def _discover() -> PowerShellRuntime:
    if os.name != "nt":
        raise NetworkStateError("PowerShell 运行时仅支持 Windows")
    found = [(name, shutil.which(name)) for name in ("pwsh.exe", "powershell.exe")]
    available = [(name, path) for name, path in found if path]
    if not available:
        raise NetworkStateError(
            "未找到受支持的 PowerShell 运行时；请安装 PowerShell 7（pwsh.exe）"
            "或启用 Windows PowerShell 5.1（powershell.exe）"
        )
    errors: list[str] = []
    for name, executable in available:
        encoded = base64.b64encode(_VERSION_SCRIPT.encode("utf-16-le")).decode("ascii")
        try:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知错误").strip()
            errors.append(f"{name}: PowerShell 返回 {result.returncode}: {detail}")
            continue
        try:
            data = json.loads(result.stdout.strip())
            major, minor, patch = (int(data[key]) for key in ("major", "minor", "patch"))
            edition = str(data.get("edition") or "unknown")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: 版本信息无效: {exc}")
            continue
        if (major, minor) < (5, 1):
            raise NetworkStateError(
                f"{name} 版本 {major}.{minor}.{patch} 过低；最低支持 PowerShell 5.1"
            )
        return PowerShellRuntime(executable, major, minor, patch, edition)
    detail = "; ".join(errors)
    raise NetworkStateError(f"PowerShell 运行时检测失败: {detail}")


@lru_cache(maxsize=1)
def get_powershell_runtime() -> PowerShellRuntime:
    return _discover()


def powershell_status() -> dict[str, object]:
    runtime = get_powershell_runtime()
    return {
        "executable": runtime.executable,
        "version": runtime.version,
        "edition": runtime.edition,
        "is_powershell_5_1_fallback": runtime.is_windows_powershell_51,
    }
