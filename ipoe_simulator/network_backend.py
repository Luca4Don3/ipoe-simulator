"""共享网络后端协议和命令执行基础设施。"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .interfaces import InterfaceInfo


class NetworkStateError(RuntimeError):
    """网卡状态无法安全读取、修改或恢复。"""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """不经过 shell 执行平台命令，并保留原始错误输出。"""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: int = 45,
        allowed_returncodes: Iterable[int] = (0,),
    ) -> CommandResult:
        command = [str(value) for value in args]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NetworkStateError(f"命令执行失败 {command!r}: {exc}") from exc
        completed = CommandResult(
            tuple(command),
            result.returncode,
            result.stdout,
            result.stderr,
        )
        if result.returncode not in set(allowed_returncodes):
            detail = (result.stderr or result.stdout or "无错误输出").strip()
            raise NetworkStateError(
                f"命令返回 {result.returncode} {command!r}: {detail}"
            )
        return completed


class NetworkBackend(ABC):
    """平台后端必须实现的事务步骤。"""

    platform_name: str

    @abstractmethod
    def capture_snapshot(self, interface: InterfaceInfo) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def prepare(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_lease(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
    ) -> list[str]:
        raise NotImplementedError
