"""macOS networksetup/ifconfig/route 网络事务后端。"""

from __future__ import annotations

import ipaddress
import re
import sys
from typing import Any

from .interfaces import InterfaceInfo
from .network_backend import CommandRunner, NetworkBackend, NetworkStateError


DUMMY_ADDRESS = "192.0.2.1"
DUMMY_MASK = ".".join(["255"] * 4)


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise NetworkStateError("macOS 后端只能在 macOS 上执行")


def parse_service_order(output: str) -> dict[str, dict[str, Any]]:
    """解析 networksetup -listnetworkserviceorder。"""

    services: dict[str, dict[str, Any]] = {}
    pending: tuple[str, bool] | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        service_match = re.match(r"^\(\d+\)\s+(.+)$", line)
        if service_match:
            value = service_match.group(1).strip()
            disabled = value.startswith("*")
            pending = (value.lstrip("*").strip(), disabled)
            continue
        hardware_match = re.search(
            r"\(Hardware Port:\s*(.*?),\s*Device:\s*([^)]+)\)",
            line,
        )
        if hardware_match and pending:
            service, disabled = pending
            services[hardware_match.group(2).strip()] = {
                "service": service,
                "hardware_port": hardware_match.group(1).strip(),
                "enabled": not disabled,
            }
            pending = None
    return services


def parse_network_info(output: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    mode = "unknown"
    for raw_line in output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("dhcp configuration"):
            mode = "dhcp"
        elif lowered.startswith("manual configuration"):
            mode = "manual"
        elif ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
    return {
        "mode": mode,
        "ip_address": values.get("ip address", ""),
        "subnet_mask": values.get("subnet mask", ""),
        "gateway": values.get("router", ""),
    }


def parse_dns_servers(output: str) -> tuple[str, list[str]]:
    text = output.strip()
    if not text or "aren't any dns servers" in text.lower():
        return "automatic", []
    servers = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return "static", servers


def parse_additional_routes(output: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    current: dict[str, str] = {}
    keys = {
        "destination address": "destination",
        "subnet mask": "subnet_mask",
        "router": "gateway",
    }
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = keys.get(key.strip().lower())
        if not field:
            continue
        if field == "destination" and current:
            if set(current) == {"destination", "subnet_mask", "gateway"}:
                routes.append(current)
            current = {}
        current[field] = value.strip()
    if set(current) == {"destination", "subnet_mask", "gateway"}:
        routes.append(current)
    return routes


def _mask_to_prefix(mask: str) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise NetworkStateError(f"无效子网掩码: {mask}") from exc


def parse_ifconfig_ipv4(output: str) -> list[dict[str, Any]]:
    addresses: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*inet\s+(\d+\.\d+\.\d+\.\d+)\s+"
        r"netmask\s+(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)"
    )
    for line in output.splitlines():
        match = pattern.search(line)
        if not match or match.group(1).startswith("127."):
            continue
        mask = match.group(2)
        if mask.lower().startswith("0x"):
            numeric = int(mask, 16)
            mask = ".".join(str((numeric >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        addresses.append(
            {
                "ip_address": match.group(1),
                "subnet_mask": mask,
                "prefix_length": _mask_to_prefix(mask),
            }
        )
    return addresses


def parse_ifconfig_mac(output: str) -> str:
    match = re.search(
        r"^\s*ether\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s*$",
        output,
        re.MULTILINE,
    )
    return match.group(1).lower() if match else ""


class MacOSNetworkBackend(NetworkBackend):
    platform_name = "macos"

    def __init__(self, runner: CommandRunner | None = None):
        self.runner = runner or CommandRunner()

    def _service(self, interface: InterfaceInfo) -> dict[str, Any]:
        order = self.runner.run(
            ["networksetup", "-listnetworkserviceorder"]
        ).stdout
        services = parse_service_order(order)
        service = services.get(interface.name) or services.get(interface.pcap_name)
        if not service:
            raise NetworkStateError(
                f"找不到接口 {interface.name} 对应的 macOS network service"
            )
        return service

    def capture_snapshot(self, interface: InterfaceInfo) -> dict[str, Any]:
        _require_macos()
        service = self._service(interface)
        name = str(service["service"])
        info = parse_network_info(
            self.runner.run(["networksetup", "-getinfo", name]).stdout
        )
        if info["mode"] not in {"dhcp", "manual"}:
            raise NetworkStateError(
                f"无法识别 network service {name} 的 IPv4 配置模式"
            )
        dns_mode, dns_servers = parse_dns_servers(
            self.runner.run(
                ["networksetup", "-getdnsservers", name],
                allowed_returncodes=(0, 1),
            ).stdout
        )
        routes = parse_additional_routes(
            self.runner.run(
                ["networksetup", "-getadditionalroutes", name],
                allowed_returncodes=(0, 1),
            ).stdout
        )
        ifconfig = self.runner.run(["ifconfig", interface.name]).stdout
        return {
            "snapshot_schema": 1,
            "platform": self.platform_name,
            "interface_name": interface.name,
            "interface_mac": parse_ifconfig_mac(ifconfig),
            "service": name,
            "service_enabled": bool(service["enabled"]),
            "configuration_mode": info["mode"],
            "ip_address": info["ip_address"],
            "subnet_mask": info["subnet_mask"],
            "gateway": info["gateway"],
            "addresses": parse_ifconfig_ipv4(ifconfig),
            "dns_mode": dns_mode,
            "dns_servers": dns_servers,
            "additional_routes": routes,
        }

    def _validate_identity(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        current_service = self._service(interface)
        if str(current_service["service"]) != str(snapshot["service"]):
            raise NetworkStateError(
                "macOS network service 与恢复快照不一致，拒绝修改"
            )
        expected_mac = str(snapshot.get("interface_mac") or "")
        if expected_mac:
            current_output = self.runner.run(["ifconfig", interface.name]).stdout
            current_mac = parse_ifconfig_mac(current_output)
            if current_mac != expected_mac:
                raise NetworkStateError("macOS 接口 MAC 与恢复快照不一致，拒绝修改")

    def _set_service_enabled(self, service: str, enabled: bool) -> None:
        self.runner.run(
            [
                "networksetup",
                "-setnetworkserviceenabled",
                service,
                "on" if enabled else "off",
            ]
        )

    def _clear_runtime(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        service = str(snapshot["service"])
        if not snapshot.get("service_enabled", True):
            self._set_service_enabled(service, True)
        self.runner.run(
            [
                "networksetup",
                "-setmanual",
                service,
                DUMMY_ADDRESS,
                DUMMY_MASK,
                "0.0.0.0",
            ]
        )
        self.runner.run(
            ["ifconfig", interface.name, "inet", DUMMY_ADDRESS, "delete"],
            allowed_returncodes=(0, 1),
        )
        for address in snapshot.get("addresses", []):
            self.runner.run(
                [
                    "ifconfig",
                    interface.name,
                    "inet",
                    str(address["ip_address"]),
                    "delete",
                ],
                allowed_returncodes=(0, 1),
            )
        for route in snapshot.get("additional_routes", []):
            prefix = _mask_to_prefix(str(route["subnet_mask"]))
            destination = f"{route['destination']}/{prefix}"
            self.runner.run(
                [
                    "route",
                    "-n",
                    "delete",
                    "-net",
                    destination,
                    str(route["gateway"]),
                ],
                allowed_returncodes=(0, 1),
            )
        self.runner.run(
            ["networksetup", "-setadditionalroutes", service]
        )

    def prepare(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _require_macos()
        self._validate_identity(interface, snapshot)
        self._clear_runtime(interface, snapshot)

    def apply_lease(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
    ) -> None:
        _require_macos()
        self._validate_identity(interface, snapshot)
        service = str(snapshot["service"])
        self.runner.run(
            [
                "networksetup",
                "-setmanual",
                service,
                ip_address,
                subnet_mask,
                gateway or "0.0.0.0",
            ]
        )
        dns = dns_servers or ["Empty"]
        self.runner.run(["networksetup", "-setdnsservers", service, *dns])

    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _require_macos()
        self._validate_identity(interface, snapshot)
        self._clear_runtime(interface, snapshot)
        service = str(snapshot["service"])
        if snapshot["configuration_mode"] == "dhcp":
            self.runner.run(["networksetup", "-setdhcp", service])
        else:
            ip_address = str(snapshot.get("ip_address") or "")
            subnet_mask = str(snapshot.get("subnet_mask") or "")
            if not ip_address or not subnet_mask:
                raise NetworkStateError("原始 macOS 手动配置缺少 IPv4 地址或子网掩码")
            self.runner.run(
                [
                    "networksetup",
                    "-setmanual",
                    service,
                    ip_address,
                    subnet_mask,
                    str(snapshot.get("gateway") or "0.0.0.0"),
                ]
            )
            for address in snapshot.get("addresses", []):
                if str(address["ip_address"]) == ip_address:
                    continue
                self.runner.run(
                    [
                        "ifconfig",
                        interface.name,
                        "alias",
                        str(address["ip_address"]),
                        "netmask",
                        str(address["subnet_mask"]),
                    ]
                )
        dns = (
            list(snapshot.get("dns_servers") or [])
            if snapshot.get("dns_mode") == "static"
            else ["Empty"]
        )
        self.runner.run(["networksetup", "-setdnsservers", service, *dns])
        route_args: list[str] = []
        for route in snapshot.get("additional_routes", []):
            route_args.extend(
                (
                    str(route["destination"]),
                    str(route["subnet_mask"]),
                    str(route["gateway"]),
                )
            )
        self.runner.run(
            ["networksetup", "-setadditionalroutes", service, *route_args]
        )
        if not snapshot.get("service_enabled", True):
            self._set_service_enabled(service, False)

    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
    ) -> list[str]:
        errors: list[str] = []
        for field, label in (
            ("service_enabled", "network service 启用状态"),
            ("configuration_mode", "DHCP/手动模式"),
            ("dns_mode", "DNS 模式"),
        ):
            if original.get(field) != current.get(field):
                errors.append(f"{label}未恢复")
        if list(original.get("dns_servers") or []) != list(
            current.get("dns_servers") or []
        ):
            errors.append("DNS 服务器未完整恢复")
        if original.get("configuration_mode") == "manual":
            for field, label in (
                ("ip_address", "IPv4 地址"),
                ("subnet_mask", "子网掩码"),
                ("gateway", "网关"),
            ):
                if str(original.get(field) or "") != str(current.get(field) or ""):
                    errors.append(f"{label}未恢复")
            expected_addresses = {
                (item["ip_address"], int(item["prefix_length"]))
                for item in original.get("addresses", [])
            }
            current_addresses = {
                (item["ip_address"], int(item["prefix_length"]))
                for item in current.get("addresses", [])
            }
            if expected_addresses != current_addresses:
                errors.append("附加 IPv4 地址未完整恢复")
        if original.get("additional_routes", []) != current.get(
            "additional_routes",
            [],
        ):
            errors.append("附加路由未完整恢复")
        current_ips = {
            item["ip_address"] for item in current.get("addresses", [])
        }
        original_ips = {
            item["ip_address"] for item in original.get("addresses", [])
        }
        if (
            original.get("configuration_mode") != "dhcp"
            and app_ip
            and app_ip in current_ips
            and app_ip not in original_ips
        ):
            errors.append(f"程序配置的地址仍然存在: {app_ip}")
        return errors
