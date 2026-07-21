from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


class InterfaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class InterfaceInfo:
    pcap_name: str
    name: str
    description: str
    mac: str
    index: int

    def display(self) -> str:
        return f"[{self.index}] {self.name} | {self.description} | {self.mac}"

    def to_dict(self) -> dict[str, object]:
        return {
            "pcap_name": self.pcap_name,
            "name": self.name,
            "description": self.description,
            "mac": self.mac,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InterfaceInfo":
        return cls(
            pcap_name=str(value["pcap_name"]),
            name=str(value["name"]),
            description=str(value.get("description", "")),
            mac=str(value.get("mac", "")),
            index=int(value["index"]),
        )


@dataclass(frozen=True)
class WindowsInterfaceState:
    safe: bool
    reason: str = ""


def windows_interface_states() -> dict[int, WindowsInterfaceState]:
    """查询 Windows 链路状态；公开 InterfaceInfo/CLI/schema 保持不变。"""
    if sys.platform != "win32":
        return {}
    from .network_backend import NetworkStateError
    from .powershell_runtime import get_powershell_runtime

    script = """
$ErrorActionPreference = 'Stop'
@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | ForEach-Object {
    [pscustomobject]@{
        index = [int]$_.InterfaceIndex
        status = [string]$_.Status
        name = [string]$_.Name
        description = [string]$_.InterfaceDescription
    }
}) | ConvertTo-Json -Compress
"""
    try:
        import json
        raw = json.loads(get_powershell_runtime().run(script))
    except Exception as exc:
        if isinstance(exc, NetworkStateError):
            raise InterfaceError(f"Windows 网卡状态查询失败: {exc}") from exc
        raise InterfaceError(f"Windows 网卡状态查询失败: {exc}") from exc
    rows = raw if isinstance(raw, list) else [raw]
    result: dict[int, WindowsInterfaceState] = {}
    virtual_tokens = ("virtual", "vmware", "hyper-v", "wintun", "tap", "loopback")
    for row in rows:
        combined = f"{row.get('name', '')} {row.get('description', '')}".lower()
        reasons: list[str] = []
        if str(row.get("status", "")).lower() != "up":
            reasons.append(f"状态为 {row.get('status') or 'Unknown'}")
        if "bluetooth" in combined or "蓝牙" in combined:
            reasons.append("Bluetooth 接口")
        if any(token in combined for token in virtual_tokens):
            reasons.append("虚拟接口")
        result[int(row["index"])] = WindowsInterfaceState(not reasons, "、".join(reasons))
    return result


def _load_scapy_conf() -> Any:
    try:
        from scapy.all import conf
    except ImportError as exc:
        raise InterfaceError("需要 Scapy: python -m pip install -r requirements.txt") from exc
    # Windows 依赖 Npcap，macOS 使用系统 libpcap/BPF；Linux 保持原生
    # PF_PACKET，避免错误切换到 libpcap socket。
    conf.use_pcap = not sys.platform.startswith("linux")
    return conf


def list_interfaces(include_virtual: bool = False) -> list[InterfaceInfo]:
    conf = _load_scapy_conf()
    result: list[InterfaceInfo] = []
    excluded = ("loopback", "wintun", "tap", "virtual", "vmware", "hyper-v")
    for iface in conf.ifaces.values():
        mac = str(getattr(iface, "mac", "") or "")
        if not mac or mac == ":".join(["00"] * 6):
            continue
        name = str(getattr(iface, "name", "") or "")
        description = str(getattr(iface, "description", "") or "")
        combined = f"{iface} {name} {description}".lower()
        if not include_virtual and any(token in combined for token in excluded):
            continue
        pcap_name = str(getattr(iface, "network_name", "") or name)
        try:
            index = int(getattr(iface, "index"))
        except (TypeError, ValueError):
            continue
        result.append(InterfaceInfo(pcap_name, name, description, mac, index))
    result.sort(key=lambda item: item.index)
    return result


def resolve_interface(selector: str | int | None) -> InterfaceInfo:
    interfaces = list_interfaces()
    if not interfaces:
        interfaces = list_interfaces(include_virtual=True)
    if not interfaces:
        raise InterfaceError("未找到可用网络接口")
    if selector is None or str(selector).strip() == "":
        if len(interfaces) == 1:
            return interfaces[0]
        listing = "\n".join(f"  {item.display()}" for item in interfaces)
        raise InterfaceError(f"检测到多个网卡，必须使用 --interface 明确选择:\n{listing}")
    needle = str(selector).strip().lower()
    exact = [
        item
        for item in interfaces
        if needle
        in {
            str(item.index).lower(),
            item.pcap_name.lower(),
            item.name.lower(),
            item.description.lower(),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        item
        for item in interfaces
        if needle in item.name.lower() or needle in item.description.lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if not exact and not partial:
        raise InterfaceError(f"找不到网络接口: {selector}")
    matches = exact or partial
    listing = "\n".join(f"  {item.display()}" for item in matches)
    raise InterfaceError(f"网络接口选择不唯一: {selector}\n{listing}")
