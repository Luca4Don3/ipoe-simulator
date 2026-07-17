"""运行平台网络后端选择与兼容公开入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .interfaces import InterfaceInfo
from .network_backend import NetworkBackend, NetworkStateError
from .network_transaction import NetworkTransaction
from .network_transaction import restore_from_journal as _restore_from_journal


def get_backend() -> NetworkBackend:
    if os.name == "nt":
        from .windows_network import WindowsNetworkBackend

        return WindowsNetworkBackend()
    if sys.platform == "darwin":
        from .macos_network import MacOSNetworkBackend

        return MacOSNetworkBackend()
    if sys.platform.startswith("linux"):
        from .linux_network import LinuxNetworkBackend

        return LinuxNetworkBackend()
    raise NetworkStateError(f"不支持的操作系统平台: {sys.platform}")


def default_state_directory(project_root: str | Path) -> Path:
    if os.name == "nt":
        return Path(project_root).resolve() / ".temp"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/IPoESimulator")
    if sys.platform.startswith("linux"):
        return Path("/var/lib/ipoe-simulator")
    raise NetworkStateError(f"不支持的操作系统平台: {sys.platform}")


def default_journal_path(project_root: str | Path) -> Path:
    return default_state_directory(project_root) / "network-recovery.json"


def capture_snapshot(interface: InterfaceInfo | int) -> dict[str, Any]:
    """兼容 Windows 旧的 index 调用；POSIX 必须传完整接口信息。"""

    backend = get_backend()
    if isinstance(interface, InterfaceInfo):
        return backend.capture_snapshot(interface)
    if backend.platform_name != "windows":
        raise NetworkStateError("macOS/Linux 快照必须传入完整 InterfaceInfo")
    from .windows_network import capture_snapshot as capture_windows_snapshot

    return capture_windows_snapshot(int(interface))


def restore_from_journal(journal_path: str | Path) -> None:
    _restore_from_journal(journal_path, backend=get_backend())


def verify_restored(
    original: dict[str, Any],
    current: dict[str, Any],
    app_ip: str | None,
) -> list[str]:
    return get_backend().verify_restored(original, current, app_ip)
