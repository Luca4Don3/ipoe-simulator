from __future__ import annotations

import os
import time
from typing import Any

from .interfaces import InterfaceInfo
from .network_backend import NetworkBackend, NetworkStateError
from .powershell_runtime import get_powershell_runtime


def _require_windows() -> None:
    if os.name != "nt":
        raise NetworkStateError("网卡配置仅支持 Windows 10/11")


def _run_powershell(script: str, timeout: int = 45, expect_json: bool = False) -> Any:
    _require_windows()
    output = get_powershell_runtime().run(script, timeout=timeout)
    if not expect_json:
        return output
    try:
        import json
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise NetworkStateError(f"PowerShell 未返回有效 JSON: {output[:300]}") from exc


def _snapshot_script(interface_index: int) -> str:
    return f"""
$ErrorActionPreference = 'Stop'
$idx = {int(interface_index)}
$adapter = Get-NetAdapter -InterfaceIndex $idx -ErrorAction Stop
$ipif = Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1
$ips = @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {{
    [pscustomobject]@{{
        ip_address = $_.IPAddress
        prefix_length = [int]$_.PrefixLength
        prefix_origin = [string]$_.PrefixOrigin
        suffix_origin = [string]$_.SuffixOrigin
        skip_as_source = [bool]$_.SkipAsSource
    }}
}})
$routes = @(Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {{
    [pscustomobject]@{{
        destination_prefix = $_.DestinationPrefix
        next_hop = $_.NextHop
        route_metric = [int]$_.RouteMetric
        protocol = [string]$_.Protocol
    }}
}})
$dns = Get-DnsClientServerAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction Stop
$registryPath = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\' + [string]$adapter.InterfaceGuid
$registry = Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue
$nameServer = if ($null -ne $registry) {{ [string]$registry.NameServer }} else {{ '' }}
[pscustomobject]@{{
    schema = 1
    interface_index = $idx
    interface_guid = [string]$adapter.InterfaceGuid
    interface_name = [string]$adapter.Name
    dhcp = [string]$ipif.Dhcp
    automatic_metric = [string]$ipif.AutomaticMetric
    interface_metric = [int]$ipif.InterfaceMetric
    addresses = $ips
    routes = $routes
    dns_mode = if ([string]::IsNullOrWhiteSpace($nameServer)) {{ 'automatic' }} else {{ 'static' }}
    dns_servers = @($dns.ServerAddresses)
}} | ConvertTo-Json -Depth 7 -Compress
"""


def capture_snapshot(interface_index: int) -> dict[str, Any]:
    data = _run_powershell(_snapshot_script(interface_index), expect_json=True)
    if not isinstance(data, dict) or int(data.get("interface_index", -1)) != int(interface_index):
        raise NetworkStateError("网卡快照内容无效")
    data["addresses"] = data.get("addresses") or []
    data["routes"] = data.get("routes") or []
    data["dns_servers"] = data.get("dns_servers") or []
    if isinstance(data["addresses"], dict):
        data["addresses"] = [data["addresses"]]
    if isinstance(data["routes"], dict):
        data["routes"] = [data["routes"]]
    if isinstance(data["dns_servers"], str):
        data["dns_servers"] = [data["dns_servers"]]
    return data


def _clear_interface_script(interface_index: int) -> str:
    return f"""
$ErrorActionPreference = 'Stop'
$idx = {int(interface_index)}
Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop
Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Protocol -ne 'Local' }} |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
"""


def prepare_for_dhcp(interface_index: int) -> None:
    _run_powershell(_clear_interface_script(interface_index))


def _prefix_length(mask: str) -> int:
    try:
        octets = [int(part) for part in mask.split(".")]
    except ValueError as exc:
        raise NetworkStateError(f"无效子网掩码: {mask}") from exc
    if len(octets) != 4 or any(value < 0 or value > 255 for value in octets):
        raise NetworkStateError(f"无效子网掩码: {mask}")
    bits = "".join(f"{value:08b}" for value in octets)
    if "01" in bits:
        raise NetworkStateError(f"子网掩码不连续: {mask}")
    return bits.count("1")


def _ps_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_lease(
    interface_index: int,
    ip_address: str,
    subnet_mask: str,
    gateway: str | None,
    dns_servers: list[str],
) -> None:
    prefix = _prefix_length(subnet_mask)
    gateway_part = f" -DefaultGateway {_ps_string(gateway)}" if gateway else ""
    dns = ",".join(_ps_string(server) for server in dns_servers)
    dns_command = (
        f"Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses @({dns}) -ErrorAction Stop"
        if dns_servers
        else "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses -ErrorAction Stop"
    )
    script = _clear_interface_script(interface_index) + f"""
$idx = {int(interface_index)}
New-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -IPAddress {_ps_string(ip_address)} -PrefixLength {prefix}{gateway_part} -ErrorAction Stop | Out-Null
{dns_command}
"""
    _run_powershell(script)


def _restore_script(snapshot: dict[str, Any]) -> str:
    idx = int(snapshot["interface_index"])
    interface_name = _ps_string(str(snapshot.get("interface_name", "")))
    interface_guid = str(snapshot.get("interface_guid", ""))
    dhcp_enabled = str(snapshot.get("dhcp", "")).lower() == "enabled"
    automatic_metric = str(snapshot.get("automatic_metric", "")).lower() == "enabled"
    metric = int(snapshot.get("interface_metric", 25))
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$idx = {idx}",
        "$adapter = Get-NetAdapter -InterfaceIndex $idx -ErrorAction Stop",
    ]
    if interface_guid:
        lines.append(
            f"if ([string]$adapter.InterfaceGuid -ne {_ps_string(interface_guid)}) "
            "{ throw '接口 GUID 与恢复快照不一致，拒绝修改' }"
        )
    lines.extend(
        [
            "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop",
            "Get-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.Protocol -ne 'Local' } | Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue",
            "Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue",
        ]
    )
    if dhcp_enabled:
        lines.extend(
            [
                "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses -ErrorAction Stop",
                "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 -Dhcp Enabled -ErrorAction Stop",
                f"ipconfig.exe /renew {interface_name} | Out-Null",
            ]
        )
    else:
        for address in snapshot.get("addresses", []):
            if str(address.get("prefix_origin", "")).lower() == "wellknown":
                continue
            ip = _ps_string(str(address["ip_address"]))
            prefix = int(address["prefix_length"])
            skip = "$true" if address.get("skip_as_source") else "$false"
            lines.append(
                f"New-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -IPAddress {ip} "
                f"-PrefixLength {prefix} -SkipAsSource {skip} -ErrorAction Stop | Out-Null"
            )
        for route in snapshot.get("routes", []):
            protocol = str(route.get("protocol", "")).lower()
            destination = str(route.get("destination_prefix", ""))
            if protocol in {"local", "dhcp"} or not destination:
                continue
            next_hop = str(route.get("next_hop", "0.0.0.0"))
            if next_hop == "0.0.0.0" and destination != "0.0.0.0/0":
                continue
            lines.append(
                "New-NetRoute -InterfaceIndex $idx -AddressFamily IPv4 "
                f"-DestinationPrefix {_ps_string(destination)} -NextHop {_ps_string(next_hop)} "
                f"-RouteMetric {int(route.get('route_metric', 0))} -ErrorAction SilentlyContinue | Out-Null"
            )
        dns_servers = [str(server) for server in snapshot.get("dns_servers", []) if server]
        if snapshot.get("dns_mode") == "static" and dns_servers:
            dns = ",".join(_ps_string(server) for server in dns_servers)
            lines.append(
                f"Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses @({dns}) -ErrorAction Stop"
            )
        else:
            lines.append(
                "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses -ErrorAction Stop"
            )
    if automatic_metric:
        lines.append(
            "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 "
            "-AutomaticMetric Enabled -ErrorAction Stop"
        )
    else:
        lines.append(
            "Set-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4 "
            f"-AutomaticMetric Disabled -InterfaceMetric {metric} -ErrorAction Stop"
        )
    return "\n".join(lines)


def _address_set(snapshot: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(item.get("ip_address")), int(item.get("prefix_length", -1)))
        for item in snapshot.get("addresses", [])
        if str(item.get("prefix_origin", "")).lower() != "wellknown"
    }


def verify_restored(original: dict[str, Any], current: dict[str, Any], app_ip: str | None) -> list[str]:
    errors: list[str] = []
    original_dhcp = str(original.get("dhcp", "")).lower()
    current_dhcp = str(current.get("dhcp", "")).lower()
    if original_dhcp != current_dhcp:
        errors.append(f"DHCP 状态不一致: 期望 {original_dhcp}, 实际 {current_dhcp}")
    current_ips = {ip for ip, _ in _address_set(current)}
    original_ips = {ip for ip, _ in _address_set(original)}
    if original_dhcp != "enabled" and app_ip and app_ip in current_ips and app_ip not in original_ips:
        errors.append(f"程序配置的地址仍然存在: {app_ip}")
    if original_dhcp != "enabled" and _address_set(original) != _address_set(current):
        errors.append("静态 IPv4 地址未完整恢复")
    if str(original.get("dns_mode")) == "static":
        if list(original.get("dns_servers") or []) != list(current.get("dns_servers") or []):
            errors.append("静态 DNS 未完整恢复")
    elif str(current.get("dns_mode")) != "automatic":
        errors.append("DNS 未恢复为自动获取")
    if str(original.get("automatic_metric", "")).lower() != str(
        current.get("automatic_metric", "")
    ).lower():
        errors.append("AutomaticMetric 状态未恢复")
    elif str(original.get("automatic_metric", "")).lower() == "disabled":
        if int(original.get("interface_metric", 25)) != int(
            current.get("interface_metric", 25)
        ):
            errors.append("InterfaceMetric 数值未恢复")
    if not errors and original_dhcp != "enabled":
        expected_routes = {
            (str(route.get("destination_prefix")), str(route.get("next_hop")), int(route.get("route_metric", 0)))
            for route in original.get("routes", [])
            if str(route.get("protocol", "")).lower() not in {"local", "dhcp"}
            and not (
                str(route.get("next_hop")) == "0.0.0.0"
                and str(route.get("destination_prefix")) != "0.0.0.0/0"
            )
        }
        actual_routes = {
            (str(route.get("destination_prefix")), str(route.get("next_hop")), int(route.get("route_metric", 0)))
            for route in current.get("routes", [])
            if str(route.get("protocol", "")).lower() not in {"local", "dhcp"}
            and not (
                str(route.get("next_hop")) == "0.0.0.0"
                and str(route.get("destination_prefix")) != "0.0.0.0/0"
            )
        }
        if expected_routes != actual_routes:
            errors.append("静态路由未完整恢复")
    return errors


class WindowsNetworkBackend(NetworkBackend):
    platform_name = "windows"

    def capture_snapshot(self, interface: InterfaceInfo) -> dict[str, Any]:
        return capture_snapshot(interface.index)

    def prepare(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        prepare_for_dhcp(interface.index)

    def apply_lease(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
    ) -> None:
        apply_lease(
            interface.index,
            ip_address,
            subnet_mask,
            gateway,
            dns_servers,
        )

    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _run_powershell(_restore_script(snapshot), timeout=90)

        deadline = time.monotonic() + 15.0
        last_errors: list[str] = []
        while True:
            current = capture_snapshot(interface.index)
            last_errors = verify_restored(snapshot, current, None)
            if not last_errors:
                return
            if time.monotonic() >= deadline:
                raise NetworkStateError(
                    "Windows 网卡恢复在 15 秒内未收敛: " + "; ".join(last_errors)
                )
            time.sleep(0.5)

    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
    ) -> list[str]:
        return verify_restored(original, current, app_ip)


def restore_from_journal(journal_path: str | Path) -> None:
    """保留旧模块公开入口，并交由共享 schema v1/v2 恢复层处理。"""

    from .network_transaction import restore_from_journal as restore

    restore(journal_path, backend=WindowsNetworkBackend())


from .network_transaction import NetworkTransaction  # noqa: E402,F401
