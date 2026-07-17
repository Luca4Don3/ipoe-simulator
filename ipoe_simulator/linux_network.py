"""Linux iproute2 与常见网络管理器事务后端。"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from .interfaces import InterfaceInfo
from .network_backend import CommandRunner, NetworkBackend, NetworkStateError


NETWORKD_OVERRIDE_PREFIX = "00-ipoe-simulator-"
UNKNOWN_DYNAMIC_PROCESSES = {
    "connmand",
    "dhclient",
    "dhcpcd",
    "netifd",
    "wicd",
    "wickedd",
}


def _require_linux() -> None:
    if not sys.platform.startswith("linux"):
        raise NetworkStateError("Linux 后端只能在 Linux 上执行")


def _prefix_to_mask(prefix: int) -> str:
    try:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise NetworkStateError(f"无效 IPv4 前缀长度: {prefix}") from exc


def _mask_to_prefix(mask: str) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise NetworkStateError(f"无效子网掩码: {mask}") from exc


def parse_ip_json_addresses(output: str) -> list[dict[str, Any]]:
    try:
        links = json.loads(output)
    except json.JSONDecodeError as exc:
        raise NetworkStateError(f"ip -j 地址输出不是有效 JSON: {exc}") from exc
    addresses: list[dict[str, Any]] = []
    for link in links if isinstance(links, list) else []:
        for item in link.get("addr_info", []):
            if item.get("family") != "inet" or not item.get("local"):
                continue
            flags = [str(value).lower() for value in item.get("flags", [])]
            addresses.append(
                {
                    "ip_address": str(item["local"]),
                    "prefix_length": int(item["prefixlen"]),
                    "broadcast": str(item.get("broadcast") or ""),
                    "scope": str(item.get("scope") or "global"),
                    "dynamic": bool(item.get("dynamic")) or "dynamic" in flags,
                    "secondary": bool(item.get("secondary")) or "secondary" in flags,
                }
            )
    return addresses


def parse_ip_o_addresses(output: str) -> list[dict[str, Any]]:
    addresses: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\d+:\s+\S+\s+inet\s+"
        r"(?P<address>\d+\.\d+\.\d+\.\d+)/(?P<prefix>\d+)"
        r"(?:\s+brd\s+(?P<broadcast>\S+))?"
        r"\s+scope\s+(?P<scope>\S+)"
        r"(?P<rest>.*)$"
    )
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        rest = match.group("rest").lower().split()
        addresses.append(
            {
                "ip_address": match.group("address"),
                "prefix_length": int(match.group("prefix")),
                "broadcast": match.group("broadcast") or "",
                "scope": match.group("scope"),
                "dynamic": "dynamic" in rest,
                "secondary": "secondary" in rest,
            }
        )
    return addresses


def parse_ip_json_routes(output: str) -> list[dict[str, Any]]:
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise NetworkStateError(f"ip -j 路由输出不是有效 JSON: {exc}") from exc
    routes: list[dict[str, Any]] = []
    for item in values if isinstance(values, list) else []:
        destination = str(item.get("dst") or "default")
        routes.append(
            {
                "destination": destination,
                "gateway": str(item.get("gateway") or ""),
                "source": str(item.get("prefsrc") or ""),
                "metric": int(item.get("metric") or 0),
                "protocol": str(item.get("protocol") or ""),
                "scope": str(item.get("scope") or ""),
                "table": str(item.get("table") or "main"),
                "type": str(item.get("type") or "unicast"),
            }
        )
    return routes


def parse_ip_o_routes(output: str) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        tokens = raw_line.strip().split()
        if not tokens:
            continue
        destination = tokens[0]
        route: dict[str, Any] = {
            "destination": destination,
            "gateway": "",
            "source": "",
            "metric": 0,
            "protocol": "",
            "scope": "",
            "table": "main",
            "type": "unicast",
        }
        for key, field in (
            ("via", "gateway"),
            ("src", "source"),
            ("proto", "protocol"),
            ("scope", "scope"),
            ("table", "table"),
            ("metric", "metric"),
        ):
            if key not in tokens:
                continue
            index = tokens.index(key)
            if index + 1 >= len(tokens):
                continue
            value: Any = tokens[index + 1]
            if field == "metric":
                try:
                    value = int(value)
                except ValueError:
                    value = 0
            route[field] = value
        routes.append(route)
    return routes


def parse_nmcli_device(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        values[key.strip()] = value.replace(r"\:", ":").strip()
    return values


def parse_resolvectl_dns(output: str) -> list[str]:
    text = output.strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return [
        token
        for token in text.split()
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", token)
    ]


def _try_run(
    runner: CommandRunner,
    args: list[str],
    *,
    timeout: int = 20,
) -> tuple[bool, str]:
    try:
        result = runner.run(
            args,
            timeout=timeout,
            allowed_returncodes=(0, 1, 2, 3, 4, 5, 10),
        )
    except NetworkStateError:
        return False, ""
    return result.returncode == 0, result.stdout


def detect_network_manager(
    interface: InterfaceInfo,
    runner: CommandRunner,
    *,
    root: Path = Path("/"),
    dynamic_addresses: bool = False,
) -> dict[str, Any]:
    nm_ok, nm_output = _try_run(
        runner,
        [
            "nmcli",
            "-t",
            "-f",
            "GENERAL.STATE,GENERAL.CONNECTION",
            "device",
            "show",
            interface.name,
        ],
    )
    if nm_ok:
        values = parse_nmcli_device(nm_output)
        if "unmanaged" not in values.get("GENERAL.STATE", "").lower():
            return {
                "type": "NetworkManager",
                "managed": True,
                "state": values.get("GENERAL.STATE", ""),
                "connection": values.get("GENERAL.CONNECTION", ""),
            }

    networkd_ok, networkd_output = _try_run(
        runner,
        ["networkctl", "status", "--no-pager", interface.name],
    )
    if networkd_ok and "unmanaged" not in networkd_output.lower():
        network_file = ""
        match = re.search(r"^\s*Network File:\s*(.+)$", networkd_output, re.MULTILINE)
        if match:
            network_file = match.group(1).strip()
        return {
            "type": "systemd-networkd",
            "network_file": network_file,
        }

    interfaces_file = root / "etc/network/interfaces"
    interfaces_dir = root / "etc/network/interfaces.d"
    if interfaces_file.exists() or interfaces_dir.exists():
        content = ""
        try:
            if interfaces_file.exists():
                content += interfaces_file.read_text(encoding="utf-8", errors="replace")
            if interfaces_dir.exists():
                for path in sorted(interfaces_dir.glob("*")):
                    if path.is_file():
                        content += "\n" + path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
        except OSError as exc:
            raise NetworkStateError(f"无法读取 ifupdown 配置: {exc}") from exc
        if re.search(
            rf"^\s*iface\s+{re.escape(interface.name)}\s+inet\s+",
            content,
            re.MULTILINE,
        ):
            return {"type": "ifupdown"}

    rhel_config = root / f"etc/sysconfig/network-scripts/ifcfg-{interface.name}"
    suse_config = root / f"etc/sysconfig/network/ifcfg-{interface.name}"
    if rhel_config.exists():
        return {"type": "rhel-sysconfig", "config": str(rhel_config)}
    if suse_config.exists():
        return {"type": "suse-sysconfig", "config": str(suse_config)}

    process_ok, process_output = _try_run(runner, ["ps", "-eo", "comm=,args="])
    unknown: list[str] = []
    if process_ok:
        for line in process_output.splitlines():
            fields = line.strip().split(maxsplit=1)
            if not fields:
                continue
            process = Path(fields[0]).name.lower()
            arguments = fields[1] if len(fields) > 1 else ""
            if process not in UNKNOWN_DYNAMIC_PROCESSES:
                continue
            if process in {"connmand", "netifd", "wicd"} or re.search(
                rf"(^|\s){re.escape(interface.name)}($|\s)",
                arguments,
            ):
                unknown.append(process)
        unknown = sorted(set(unknown))
    if dynamic_addresses or unknown:
        detail = ", ".join(unknown) if unknown else "动态 IPv4 地址标记"
        raise NetworkStateError(
            f"接口 {interface.name} 可能由未知动态网络管理器控制 ({detail})；"
            "已在修改前停止"
        )
    return {"type": "unmanaged-static"}


class LinuxNetworkBackend(NetworkBackend):
    platform_name = "linux"

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        root: Path = Path("/"),
    ):
        self.runner = runner or CommandRunner()
        self.root = root

    def _addresses(self, interface: InterfaceInfo) -> tuple[str, list[dict[str, Any]]]:
        try:
            output = self.runner.run(
                ["ip", "-j", "-4", "addr", "show", "dev", interface.name]
            ).stdout
            return "json", parse_ip_json_addresses(output)
        except NetworkStateError:
            output = self.runner.run(
                ["ip", "-o", "-4", "addr", "show", "dev", interface.name]
            ).stdout
            return "oneline", parse_ip_o_addresses(output)

    def _routes(self, interface: InterfaceInfo, mode: str) -> list[dict[str, Any]]:
        if mode == "json":
            try:
                output = self.runner.run(
                    [
                        "ip",
                        "-j",
                        "-4",
                        "route",
                        "show",
                        "table",
                        "all",
                        "dev",
                        interface.name,
                    ]
                ).stdout
                return parse_ip_json_routes(output)
            except NetworkStateError:
                pass
        output = self.runner.run(
            ["ip", "-o", "-4", "route", "show", "dev", interface.name]
        ).stdout
        return parse_ip_o_routes(output)

    def _link(self, interface: InterfaceInfo) -> dict[str, Any]:
        output = self.runner.run(
            ["ip", "-o", "link", "show", "dev", interface.name]
        ).stdout
        flags_match = re.search(r"<([^>]+)>", output)
        mtu_match = re.search(r"\bmtu\s+(\d+)", output)
        mac_match = re.search(
            r"\blink/ether\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})",
            output,
        )
        flags = flags_match.group(1).split(",") if flags_match else []
        return {
            "up": "UP" in flags,
            "mtu": int(mtu_match.group(1)) if mtu_match else 0,
            "mac": mac_match.group(1).lower() if mac_match else "",
        }

    def _capture_dns(self, interface: InterfaceInfo) -> dict[str, Any]:
        resolved_ok, resolved_output = _try_run(
            self.runner,
            ["resolvectl", "dns", interface.name],
        )
        if resolved_ok:
            return {
                "backend": "systemd-resolved",
                "servers": parse_resolvectl_dns(resolved_output),
            }
        path = self.root / "etc/resolv.conf"
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise NetworkStateError(f"无法读取 {path}: {exc}") from exc
        return {
            "backend": "resolv.conf",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "symlink": os.readlink(path) if path.is_symlink() else "",
        }

    def capture_snapshot(self, interface: InterfaceInfo) -> dict[str, Any]:
        _require_linux()
        mode, addresses = self._addresses(interface)
        manager = detect_network_manager(
            interface,
            self.runner,
            root=self.root,
            dynamic_addresses=any(item["dynamic"] for item in addresses),
        )
        dns = self._capture_dns(interface)
        dns["automatic"] = bool(
            any(item["dynamic"] for item in addresses)
            and manager["type"] != "unmanaged-static"
        )
        override = self._networkd_override(interface)
        if manager["type"] == "systemd-networkd" and override.exists():
            raise NetworkStateError(f"临时 networkd 配置已存在，拒绝覆盖: {override}")
        return {
            "snapshot_schema": 1,
            "platform": self.platform_name,
            "interface_name": interface.name,
            "ip_output": mode,
            "link": self._link(interface),
            "addresses": addresses,
            "routes": self._routes(interface, mode),
            "dns": dns,
            "manager": manager,
        }

    def _networkd_override(self, interface: InterfaceInfo) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", interface.name)
        return self.root / f"run/systemd/network/{NETWORKD_OVERRIDE_PREFIX}{safe_name}.network"

    def _write_networkd_override(self, interface: InterfaceInfo) -> None:
        path = self._networkd_override(interface)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[Match]\n"
            f"Name={interface.name}\n\n"
            "[Link]\n"
            "Unmanaged=yes\n"
        )
        handle, temp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                raise NetworkStateError(f"拒绝覆盖已有 networkd 配置: {path}")
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _manager_prepare(
        self,
        interface: InterfaceInfo,
        manager: dict[str, Any],
    ) -> None:
        manager_type = manager["type"]
        if manager_type == "NetworkManager":
            try:
                self.runner.run(
                    ["nmcli", "device", "set", interface.name, "managed", "no"]
                )
                manager["suspend_mode"] = "managed-no"
            except NetworkStateError:
                self.runner.run(["nmcli", "device", "disconnect", interface.name])
                manager["suspend_mode"] = "disconnect"
        elif manager_type == "systemd-networkd":
            self._write_networkd_override(interface)
            self.runner.run(["networkctl", "reload"])
            self.runner.run(["networkctl", "reconfigure", interface.name])
        elif manager_type in {
            "ifupdown",
            "rhel-sysconfig",
            "suse-sysconfig",
        }:
            self.runner.run(
                ["ifdown", interface.name],
                allowed_returncodes=(0, 1),
            )
        elif manager_type != "unmanaged-static":
            raise NetworkStateError(f"不支持的 Linux 网络管理器: {manager_type}")

    def _manager_restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        manager = snapshot["manager"]
        manager_type = manager["type"]
        if manager_type == "NetworkManager":
            try:
                self.runner.run(
                    ["nmcli", "device", "set", interface.name, "managed", "yes"]
                )
            except NetworkStateError:
                # 旧版 nmcli 不支持 device set；disconnect 回退路径下设备
                # 本来仍由 NetworkManager 管理，connection up 可直接恢复。
                pass
            connection = str(manager.get("connection") or "")
            if connection and connection not in {"--", "(null)"}:
                self.runner.run(
                    ["nmcli", "connection", "up", "id", connection],
                    timeout=90,
                )
            else:
                self.runner.run(
                    ["nmcli", "device", "disconnect", interface.name],
                    allowed_returncodes=(0, 10),
                )
        elif manager_type == "systemd-networkd":
            override = self._networkd_override(interface)
            try:
                override.unlink(missing_ok=True)
            except OSError as exc:
                raise NetworkStateError(f"无法移除临时 networkd 配置 {override}: {exc}") from exc
            self.runner.run(["networkctl", "reload"])
            self.runner.run(["networkctl", "reconfigure", interface.name])
            self.runner.run(
                [
                    "networkctl",
                    "wait-online",
                    f"--interface={interface.name}",
                    "--timeout=30",
                ],
                timeout=35,
                allowed_returncodes=(0, 1),
            )
        elif manager_type in {
            "ifupdown",
            "rhel-sysconfig",
            "suse-sysconfig",
        }:
            if snapshot.get("link", {}).get("up"):
                self.runner.run(["ifup", interface.name], timeout=90)

    def _flush_ipv4(self, interface: InterfaceInfo) -> None:
        self.runner.run(
            ["ip", "-4", "addr", "flush", "dev", interface.name, "scope", "global"],
            allowed_returncodes=(0, 1),
        )
        self.runner.run(
            ["ip", "-4", "route", "flush", "dev", interface.name],
            allowed_returncodes=(0, 1),
        )

    def _validate_identity(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        expected_mac = str(snapshot.get("link", {}).get("mac") or "")
        if not expected_mac:
            return
        current_mac = str(self._link(interface).get("mac") or "")
        if current_mac != expected_mac:
            raise NetworkStateError("Linux 接口 MAC 与恢复快照不一致，拒绝修改")

    def prepare(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _require_linux()
        self._validate_identity(interface, snapshot)
        self._manager_prepare(interface, snapshot["manager"])
        self.runner.run(["ip", "link", "set", "dev", interface.name, "up"])
        self._flush_ipv4(interface)

    def _apply_dns(
        self,
        interface: InterfaceInfo,
        dns_snapshot: dict[str, Any],
        servers: list[str],
    ) -> None:
        if dns_snapshot["backend"] == "systemd-resolved":
            if servers:
                self.runner.run(["resolvectl", "dns", interface.name, *servers])
            else:
                self.runner.run(["resolvectl", "revert", interface.name])
            return
        path = self.root / "etc/resolv.conf"
        content = "".join(f"nameserver {server}\n" for server in servers)
        if not content:
            content = "# IPoE Simulator: DHCP lease did not provide DNS\n"
        try:
            path.write_text(content, encoding="ascii")
        except OSError as exc:
            raise NetworkStateError(f"无法写入 {path}: {exc}") from exc

    def apply_lease(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
    ) -> None:
        _require_linux()
        self._validate_identity(interface, snapshot)
        prefix = _mask_to_prefix(subnet_mask)
        self.runner.run(
            [
                "ip",
                "-4",
                "addr",
                "add",
                f"{ip_address}/{prefix}",
                "dev",
                interface.name,
            ]
        )
        if gateway:
            self.runner.run(
                [
                    "ip",
                    "-4",
                    "route",
                    "replace",
                    "default",
                    "via",
                    gateway,
                    "dev",
                    interface.name,
                ]
            )
        self._apply_dns(interface, snapshot["dns"], dns_servers)

    def _restore_dns(
        self,
        interface: InterfaceInfo,
        dns_snapshot: dict[str, Any],
    ) -> None:
        if dns_snapshot.get("automatic"):
            if dns_snapshot["backend"] == "systemd-resolved":
                self.runner.run(["resolvectl", "revert", interface.name])
            return
        if dns_snapshot["backend"] == "systemd-resolved":
            servers = list(dns_snapshot.get("servers") or [])
            if servers:
                self.runner.run(["resolvectl", "dns", interface.name, *servers])
            else:
                self.runner.run(["resolvectl", "revert", interface.name])
            return
        path = self.root / "etc/resolv.conf"
        expected_link = str(dns_snapshot.get("symlink") or "")
        current_link = os.readlink(path) if path.is_symlink() else ""
        if expected_link != current_link:
            raise NetworkStateError(
                f"{path} 符号链接状态已变化，拒绝覆盖；"
                f"期望 {expected_link or '普通文件'}，实际 {current_link or '普通文件'}"
            )
        try:
            content = base64.b64decode(
                str(dns_snapshot["content_base64"]),
                validate=True,
            )
            path.write_bytes(content)
        except (KeyError, ValueError, OSError) as exc:
            raise NetworkStateError(f"无法恢复 {path}: {exc}") from exc

    def _restore_static_ipv4(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        self._flush_ipv4(interface)
        for address in snapshot.get("addresses", []):
            command = [
                "ip",
                "-4",
                "addr",
                "add",
                f"{address['ip_address']}/{int(address['prefix_length'])}",
            ]
            if address.get("broadcast"):
                command.extend(("brd", str(address["broadcast"])))
            command.extend(("dev", interface.name))
            self.runner.run(command)
        for route in snapshot.get("routes", []):
            if (
                route.get("protocol") == "kernel"
                and not route.get("gateway")
                and route.get("scope") == "link"
            ):
                continue
            command = [
                "ip",
                "-4",
                "route",
                "replace",
                str(route["destination"]),
            ]
            if route.get("gateway"):
                command.extend(("via", str(route["gateway"])))
            command.extend(("dev", interface.name))
            if route.get("source"):
                command.extend(("src", str(route["source"])))
            if int(route.get("metric") or 0):
                command.extend(("metric", str(int(route["metric"]))))
            if route.get("table") and str(route["table"]) != "main":
                command.extend(("table", str(route["table"])))
            self.runner.run(command)

    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _require_linux()
        self._validate_identity(interface, snapshot)
        self._flush_ipv4(interface)
        self._manager_restore(interface, snapshot)
        dynamic = any(item.get("dynamic") for item in snapshot.get("addresses", []))
        if snapshot["manager"]["type"] == "unmanaged-static" or not dynamic:
            self._restore_static_ipv4(interface, snapshot)
        self._restore_dns(interface, snapshot["dns"])
        if not snapshot.get("link", {}).get("up"):
            self.runner.run(["ip", "link", "set", "dev", interface.name, "down"])

    @staticmethod
    def _address_set(snapshot: dict[str, Any]) -> set[tuple[str, int]]:
        return {
            (str(item["ip_address"]), int(item["prefix_length"]))
            for item in snapshot.get("addresses", [])
        }

    @staticmethod
    def _route_set(snapshot: dict[str, Any]) -> set[tuple[str, str, int, str]]:
        return {
            (
                str(item.get("destination") or ""),
                str(item.get("gateway") or ""),
                int(item.get("metric") or 0),
                str(item.get("table") or "main"),
            )
            for item in snapshot.get("routes", [])
            if not (
                item.get("protocol") == "kernel"
                and not item.get("gateway")
                and item.get("scope") == "link"
            )
        }

    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
    ) -> list[str]:
        errors: list[str] = []
        if original.get("manager", {}).get("type") != current.get("manager", {}).get(
            "type"
        ):
            errors.append("网络管理器状态未恢复")
        else:
            manager_type = original.get("manager", {}).get("type")
            if manager_type == "NetworkManager":
                if str(original.get("manager", {}).get("connection") or "") != str(
                    current.get("manager", {}).get("connection") or ""
                ):
                    errors.append("NetworkManager 活动连接未恢复")
            elif manager_type == "systemd-networkd":
                if str(original.get("manager", {}).get("network_file") or "") != str(
                    current.get("manager", {}).get("network_file") or ""
                ):
                    errors.append("systemd-networkd 配置归属未恢复")
        original_dynamic = any(
            item.get("dynamic") for item in original.get("addresses", [])
        )
        current_dynamic = any(
            item.get("dynamic") for item in current.get("addresses", [])
        )
        current_ips = {
            str(item["ip_address"]) for item in current.get("addresses", [])
        }
        original_ips = {
            str(item["ip_address"]) for item in original.get("addresses", [])
        }
        if (
            not original_dynamic
            and app_ip
            and app_ip in current_ips
            and app_ip not in original_ips
        ):
            errors.append(f"程序配置的地址仍然存在: {app_ip}")
        if not original_dynamic:
            if self._address_set(original) != self._address_set(current):
                errors.append("静态 IPv4 地址未完整恢复")
            if self._route_set(original) != self._route_set(current):
                errors.append("静态 IPv4 路由未完整恢复")
        elif not current_dynamic:
            errors.append("动态 IPv4/DHCP 状态未恢复")
        if original.get("dns", {}).get("automatic"):
            pass
        elif original.get("dns", {}).get("backend") == "resolv.conf":
            if original.get("dns", {}).get("content_base64") != current.get(
                "dns",
                {},
            ).get("content_base64"):
                errors.append("resolv.conf 未完整恢复")
        elif list(original.get("dns", {}).get("servers") or []) != list(
            current.get("dns", {}).get("servers") or []
        ):
            errors.append("systemd-resolved DNS 未完整恢复")
        if bool(original.get("link", {}).get("up")) != bool(
            current.get("link", {}).get("up")
        ):
            errors.append("接口启用状态未恢复")
        return errors
