"""Windows PowerShell runtime discovery and invocation."""

from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import signal
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

    def run(self, script: str, *, timeout: float = 45) -> str:
        script = _UTF8_SETUP + script
        LOGGER.debug(
            "执行 PowerShell script_length=%s timeout_seconds=%s executable=%s",
            len(script),
            timeout,
            self.executable,
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = [
                    self.executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ]
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(command, **kwargs)
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            assert process is not None
            _terminate_process(process)
            raise NetworkStateError(f"PowerShell 执行超时（{timeout} 秒）") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            if process is not None:
                _terminate_process(process)
            raise NetworkStateError(f"PowerShell 执行失败: {exc}") from exc
        except BaseException:
            if process is not None:
                _terminate_process(process)
            raise
        if process.returncode != 0:
            detail = (stderr or stdout or "未知 PowerShell 错误").strip()
            raise NetworkStateError(f"PowerShell 返回 {process.returncode}: {detail}")
        return stdout.strip()

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
    Get-Command -Name $name -CommandType Cmdlet,Function -ErrorAction Stop | Out-Null
}
'ok'
"""
        if self.run(script) != "ok":
            raise NetworkStateError("PowerShell 网卡 cmdlet 探针返回无效结果")


_UTF8_SETUP = (
    "$utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false; "
    "[Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8; "
)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """终止并回收 PowerShell，避免超时或中断留下恢复子进程。"""

    if process.poll() is not None:
        process.wait()
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass
        process.wait()

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
        patterns = (
            os.path.join("PowerShell", "*", name),
            os.path.join("Microsoft", "PowerShell", "*", name),
        )
    else:
        roots = [os.environ.get("SystemRoot")]
        patterns = (os.path.join("System32", "WindowsPowerShell", "v1.0", name),)
    extra_paths: list[str] = []
    for root in roots:
        if root:
            for pattern in patterns:
                extra_paths.extend(glob.glob(os.path.join(root, pattern)))
    paths.extend(path for path in extra_paths if os.path.isfile(path))
    unique: dict[str, str] = {}
    for path in paths:
        unique.setdefault(os.path.normcase(os.path.abspath(path)), path)
    return list(unique.values())


def _inspect_version(executable: str, name: str) -> PowerShellRuntime:
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
    return PowerShellRuntime(executable, major, minor, patch, edition)


def _probe_capabilities(runtime: PowerShellRuntime) -> PowerShellRuntime:
    runtime.probe_utf8_output()
    runtime.probe_network_cmdlets()
    return runtime


def _inspect_candidate(executable: str, name: str) -> PowerShellRuntime:
    """兼容既有内部调用：检查版本并执行能力探针。"""

    return _probe_capabilities(_inspect_version(executable, name))


def _discover() -> PowerShellRuntime:
    if os.name != "nt":
        raise NetworkStateError("PowerShell 运行时仅支持 Windows")
    pwsh_candidates = _candidate_paths("pwsh.exe")
    windows_candidates = _candidate_paths("powershell.exe")
    if not pwsh_candidates and not windows_candidates:
        raise NetworkStateError(
            "未找到受支持的 PowerShell 运行时；请安装 PowerShell 7（pwsh.exe）"
            "或启用 Windows PowerShell 5.1（powershell.exe）"
        )

    failures: list[str] = []
    pwsh_runtimes: list[PowerShellRuntime] = []
    for executable in pwsh_candidates:
        try:
            pwsh_runtimes.append(_inspect_version(executable, "pwsh.exe"))
        except NetworkStateError as exc:
            failures.append(f"{executable}: {exc}")
            LOGGER.warning(
                "PowerShell runtime candidate rejected executable=%s reason=%s",
                executable,
                exc,
            )
    if pwsh_runtimes:
        latest = max(pwsh_runtimes, key=lambda item: (item.major, item.minor, item.patch))
        try:
            runtime = _probe_capabilities(latest)
        except NetworkStateError as exc:
            failures.append(f"{latest.executable} ({latest.version}): {exc}")
            LOGGER.warning(
                "latest PowerShell runtime capability probe failed; falling back to 5.1 "
                "executable=%s version=%s reason=%s",
                latest.executable,
                latest.version,
                exc,
            )
        else:
            LOGGER.info(
                "PowerShell runtime selected executable=%s version=%s edition=%s fallback_5_1=false",
                runtime.executable,
                runtime.version,
                runtime.edition,
            )
            return runtime

    runtime: PowerShellRuntime | None = None
    for executable in windows_candidates:
        try:
            candidate = _inspect_version(executable, "powershell.exe")
            if not candidate.is_windows_powershell_51:
                raise NetworkStateError(
                    f"powershell.exe 版本 {candidate.version} 不是 Windows PowerShell 5.1"
                )
            runtime = _probe_capabilities(candidate)
            break
        except NetworkStateError as exc:
            failures.append(f"{executable}: {exc}")
            LOGGER.warning(
                "Windows PowerShell 5.1 candidate rejected executable=%s reason=%s",
                executable,
                exc,
            )
    if runtime is None:
        raise NetworkStateError(
            "所有 PowerShell 候选均不可用: " + "; ".join(failures)
        )
    LOGGER.info(
        "最新 PowerShell 不可用或能力不足，回退到 Windows PowerShell 5.1 "
        "executable=%s",
        runtime.executable,
    )
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
    return {
        "executable": runtime.executable,
        "version": runtime.version,
        "edition": runtime.edition,
        "is_powershell_5_1_fallback": runtime.is_windows_powershell_51,
        "utf8_output": True,
        "network_cmdlets": True,
    }
