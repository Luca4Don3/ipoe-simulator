"""旧模块名兼容层；真实实现位于 macos_network/linux_network。"""

from __future__ import annotations

from .network_backend import NetworkStateError
from .network_transaction import NetworkTransaction, restore_from_journal
from .platform_network import capture_snapshot, get_backend, verify_restored

__all__ = [
    "NetworkStateError",
    "NetworkTransaction",
    "capture_snapshot",
    "get_backend",
    "restore_from_journal",
    "verify_restored",
]
