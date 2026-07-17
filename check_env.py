#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
import platform
import shutil
import socket
import sys
from pathlib import Path

from ipoe_simulator.app_logging import LOG_LEVELS, configure_logging, get_logger
from ipoe_simulator.dependencies import (
    ensure_scapy,
    is_admin,
    linux_dependency_install_command,
    linux_distribution_status,
    macos_status,
    normalized_architecture,
    npcap_status,
    package_manager,
    windows_architecture,
)
from ipoe_simulator.interfaces import InterfaceError, list_interfaces
from ipoe_simulator.platform_network import default_state_directory
from ipoe_simulator.powershell_runtime import powershell_status


ROOT = Path(__file__).resolve().parent
LOGGER = get_logger("check-env")


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _platform_checks(checks: dict[str, object]) -> bool:
    ok = True
    if os.name == "nt":
        checks["platform"] = f"Windows {platform.release()}"
        try:
            architecture = windows_architecture()
            checks["architecture"] = architecture
            if architecture.endswith("-process-32bit"):
                checks["architecture_error"] = "32 位进程与原生系统架构不匹配"
                ok = False
        except Exception as exc:
            checks["architecture_error"] = str(exc)
            ok = False
        checks["network_manager"] = "Windows NetTCPIP/PowerShell"
        try:
            powershell = powershell_status()
            checks["powershell"] = powershell["executable"]
            checks["powershell_version"] = powershell["version"]
            checks["powershell_edition"] = powershell["edition"]
            checks["powershell_5_1_fallback"] = powershell[
                "is_powershell_5_1_fallback"
            ]
        except Exception as exc:
            checks["powershell"] = False
            checks["powershell_error"] = str(exc)
            ok = False
    elif sys.platform == "darwin":
        status = macos_status()
        checks["platform"] = status["detail"]
        checks["architecture"] = status["architecture"]
        checks["platform_supported"] = status["supported"]
        ok = ok and bool(status["supported"])
        commands = {
            command: shutil.which(command) or False
            for command in ("networksetup", "ifconfig", "route")
        }
        checks["platform_commands"] = commands
        if not all(commands.values()):
            ok = False
    elif sys.platform.startswith("linux"):
        status = linux_distribution_status()
        checks["platform"] = status["detail"]
        checks["architecture"] = status["architecture"]
        checks["platform_supported"] = status["supported"]
        checks["legacy_runtime"] = status["runtime"] if status["legacy"] else False
        ok = ok and bool(status["supported"])
        manager = package_manager()
        checks["package_manager"] = manager or False
        if manager:
            checks["dependency_install_command"] = linux_dependency_install_command(
                manager
            )
        commands = {
            command: shutil.which(command) or False
            for command in ("ip", "tcpdump")
        }
        checks["platform_commands"] = commands
        if not all(commands.values()):
            ok = False
    else:
        checks["platform"] = sys.platform
        checks["platform_supported"] = False
        checks["architecture"] = normalized_architecture()
        ok = False
    return ok


def _packet_backend_checks(checks: dict[str, object]) -> bool:
    if os.name == "nt":
        npcap_ok, npcap_detail = npcap_status()
        checks["packet_backend"] = npcap_detail if npcap_ok else False
        if not npcap_ok:
            checks["packet_backend_error"] = npcap_detail
        return npcap_ok
    if sys.platform == "darwin":
        libpcap = ctypes.util.find_library("pcap")
        checks["packet_backend"] = (
            f"system libpcap/BPF ({libpcap})" if libpcap else False
        )
        return bool(libpcap)
    if sys.platform.startswith("linux"):
        available = hasattr(socket, "AF_PACKET")
        checks["packet_backend"] = "Scapy PF_PACKET" if available else False
        return available
    checks["packet_backend"] = False
    return False


def _manager_checks(
    checks: dict[str, object],
    interfaces: list[object],
) -> bool:
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        from ipoe_simulator.macos_network import MacOSNetworkBackend

        backend = MacOSNetworkBackend()
        managers: dict[str, object] = {}
        ok = True
        for interface in interfaces:
            try:
                service = backend._service(interface)  # 只读环境探测
                managers[interface.name] = {
                    "type": "networksetup",
                    "service": service["service"],
                }
            except Exception as exc:
                managers[interface.name] = {"error": str(exc)}
                ok = False
        checks["network_managers"] = managers
        return ok
    if sys.platform.startswith("linux"):
        from ipoe_simulator.linux_network import LinuxNetworkBackend

        backend = LinuxNetworkBackend()
        managers = {}
        ok = True
        for interface in interfaces:
            try:
                _, addresses = backend._addresses(interface)  # 只读环境探测
                from ipoe_simulator.linux_network import detect_network_manager

                manager = detect_network_manager(
                    interface,
                    backend.runner,
                    dynamic_addresses=any(
                        bool(item.get("dynamic")) for item in addresses
                    ),
                )
                managers[interface.name] = manager
            except Exception as exc:
                managers[interface.name] = {"error": str(exc)}
                ok = False
        checks["network_managers"] = managers
        return ok
    return False


def run() -> tuple[bool, dict[str, object]]:
    checks: dict[str, object] = {}
    ok = _platform_checks(checks)
    version = sys.version_info
    checks["python"] = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < (3, 10):
        checks["python_error"] = "需要 Python 3.10+"
        ok = False

    admin = is_admin()
    checks["admin"] = admin
    if not admin:
        checks["permission_error"] = (
            "需要管理员权限"
            if os.name == "nt"
            else "需要 root/sudo；程序不会自动提权"
        )
        ok = False

    try:
        # Windows 维持原有自动安装行为；POSIX 环境检测保持只读。
        checks["scapy"] = ensure_scapy(auto_install=os.name == "nt")
    except Exception as exc:
        checks["scapy_error"] = str(exc)
        ok = False

    ok = _packet_backend_checks(checks) and ok

    interfaces: list[object] = []
    if "scapy_error" not in checks:
        try:
            interfaces = list_interfaces()
            checks["interfaces"] = [
                {
                    "index": interface.index,
                    "name": interface.name,
                    "description": interface.description,
                    "mac": interface.mac,
                }
                for interface in interfaces
            ]
            if not interfaces:
                ok = False
        except InterfaceError as exc:
            checks["interfaces_error"] = str(exc)
            ok = False
    if interfaces:
        ok = _manager_checks(checks, interfaces) and ok

    try:
        state_dir = default_state_directory(ROOT)
        ancestor = _nearest_existing_parent(state_dir)
        writable = os.access(ancestor, os.W_OK)
        checks["recovery"] = {
            "state_directory": str(state_dir),
            "existing_ancestor": str(ancestor),
            "writable": writable,
            "watchdog": str(ROOT / "recovery_watchdog.py"),
            "watchdog_exists": (ROOT / "recovery_watchdog.py").exists(),
        }
        if not writable or not (ROOT / "recovery_watchdog.py").exists():
            ok = False
    except Exception as exc:
        checks["recovery_error"] = str(exc)
        ok = False

    checks["ok"] = ok
    return ok, checks


def main() -> int:
    parser = argparse.ArgumentParser(description="IPoE DHCP 运行环境检测")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default=None, help="日志级别")
    args = parser.parse_args()
    configure_logging(level_name=args.log_level)
    ok, checks = run()
    if args.json:
        print(json.dumps(checks, indent=2, ensure_ascii=False))
    else:
        for key, value in checks.items():
            if key == "ok":
                continue
            if value is False or key.endswith("_error"):
                LOGGER.error("环境检查失败 key=%s value=%s", key, value)
            else:
                LOGGER.info("环境检查 key=%s value=%s", key, value)
        if ok:
            LOGGER.info("环境就绪")
        else:
            LOGGER.error("环境检测未通过；未执行任何网卡修改")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
