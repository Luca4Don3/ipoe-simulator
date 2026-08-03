from __future__ import annotations

import os
import time
from typing import Any, Callable

from .interfaces import InterfaceInfo
from .network_backend import NetworkBackend, NetworkStateError
from .powershell_runtime import get_powershell_runtime


def _require_windows() -> None:
    if os.name != "nt":
        raise NetworkStateError("网卡配置仅支持 Windows 10/11")


def _normalize_dns_servers(data: dict[str, Any]) -> list[str]:
    """规范化 dns_servers 字段并返回去重后的有效字符串列表。

    PowerShell ConvertTo-Json 在单元素数组上可能返回标量；不同架构上
    Get-DnsClientServerAddress 也偶有非字符串元素。统一规范化后用于快照
    与收敛校验，避免在 ARM64 上因序列化差异陷入虚假循环。
    """
    servers = data.get("dns_servers") or []
    if isinstance(servers, str):
        servers = [servers]
    data["dns_servers"] = [str(server) for server in servers if server]
    return data["dns_servers"]


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
    if isinstance(data["addresses"], dict):
        data["addresses"] = [data["addresses"]]
    if isinstance(data["routes"], dict):
        data["routes"] = [data["routes"]]
    _normalize_dns_servers(data)
    if data.get("dns_mode") == "static" and not data["dns_servers"]:
        raise NetworkStateError("Windows 网卡快照不一致：静态 DNS 模式没有服务器；已在修改前停止")
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
    app_routes: list[str],
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
    for number, address in enumerate(app_routes, start=1):
        route_script = f"""
$ErrorActionPreference = 'Stop'
$idx = {int(interface_index)}
New-NetRoute -PolicyStore ActiveStore -InterfaceIndex $idx -AddressFamily IPv4 -DestinationPrefix {_ps_string(address + '/32')} -NextHop {_ps_string(gateway or '')} -ErrorAction Stop | Out-Null
"""
        try:
            _run_powershell(route_script)
        except NetworkStateError as exc:
            raise NetworkStateError(
                f"应用单播静态路由失败（第 {number}/{len(app_routes)} 条）"
            ) from exc


def _restore_script(snapshot: dict[str, Any]) -> str:
    idx = int(snapshot["interface_index"])
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


def _dns_set_script(snapshot: dict[str, Any]) -> str:
    """生成仅重设静态 DNS 的轻量脚本，用于收敛期主动重发。

    在 ARM64 Windows 11 上 `Set-DnsClientServerAddress` 与 `Get-DnsClientServerAddress`
    偶发不同步，单次下发后读取仍返回旧值，导致收敛校验始终失败。这里先
    `-ResetServerAddresses` 再以原快照 `-ServerAddresses` 重新下发，用两次写
    操作补充 ARM64 上单次写入不生效的情形。
    """
    idx = int(snapshot["interface_index"])
    servers = [str(server) for server in snapshot.get("dns_servers", []) if server]
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$idx = {idx}",
        "Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses -ErrorAction Stop",
    ]
    if snapshot.get("dns_mode") == "static" and servers:
        dns = ",".join(_ps_string(server) for server in servers)
        lines.append(
            f"Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses @({dns}) -ErrorAction Stop"
        )
    return "\n".join(lines)


def _address_set(snapshot: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(item.get("ip_address")), int(item.get("prefix_length", -1)))
        for item in snapshot.get("addresses", [])
        if str(item.get("prefix_origin", "")).lower() != "wellknown"
    }


def verify_restored(
    original: dict[str, Any],
    current: dict[str, Any],
    app_ip: str | None,
    app_routes: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    original_dhcp = str(original.get("dhcp", "")).lower()
    current_dhcp = str(current.get("dhcp", "")).lower()
    if original_dhcp != current_dhcp:
        errors.append(f"DHCP 状态不一致: 期望 {original_dhcp}, 实际 {current_dhcp}")
    current_ips = {ip for ip, _ in _address_set(current)}
    original_ips = {ip for ip, _ in _address_set(original)}
    manual_current_ips = {
        str(item.get("ip_address"))
        for item in current.get("addresses", [])
        if str(item.get("prefix_origin", "")).lower() == "manual"
    }
    if app_ip and app_ip in manual_current_ips and app_ip not in original_ips:
        errors.append(f"程序配置的地址仍然存在: {app_ip}")
    if original_dhcp != "enabled" and _address_set(original) != _address_set(current):
        errors.append("静态 IPv4 地址未完整恢复")
    original_dns_mode = str(original.get("dns_mode"))
    current_dns_mode = str(current.get("dns_mode"))
    if original_dns_mode != current_dns_mode:
        errors.append(f"DNS 模式不一致: 期望 {original_dns_mode}, 实际 {current_dns_mode}")
    if original_dns_mode == "static":
        expected = sorted({str(s) for s in (original.get("dns_servers") or []) if s})
        actual = sorted({str(s) for s in (current.get("dns_servers") or []) if s})
        if expected != actual:
            missing = [s for s in expected if s not in set(actual)]
            extra = [s for s in actual if s not in set(expected)]
            detail = f"期望 {expected}, 实际 {actual}"
            if missing:
                detail += f", 缺失 {missing}"
            if extra:
                detail += f", 多余 {extra}"
            errors.append(f"静态 DNS 未完整恢复: {detail}")
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
    original_route_rows = {
        (
            str(route.get("destination_prefix")),
            str(route.get("next_hop")),
            int(route.get("route_metric", 0)),
            str(route.get("protocol", "")).lower(),
        )
        for route in original.get("routes", [])
    }
    app_prefixes = {f"{address}/32" for address in (app_routes or [])}
    residual = [
        route
        for route in current.get("routes", [])
        if str(route.get("destination_prefix")) in app_prefixes
        and str(route.get("protocol", "")).lower() not in {"local", "dhcp"}
        and (
            str(route.get("destination_prefix")),
            str(route.get("next_hop")),
            int(route.get("route_metric", 0)),
            str(route.get("protocol", "")).lower(),
        ) not in original_route_rows
    ]
    if residual:
        errors.append("程序配置的静态路由仍然存在")
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
        app_routes: list[str],
    ) -> None:
        apply_lease(
            interface.index,
            ip_address,
            subnet_mask,
            gateway,
            dns_servers,
            app_routes,
        )

    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        _run_powershell(_restore_script(snapshot), timeout=30)

    def restore_and_verify(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        app_ip: str | None,
        progress: Callable[[str, str], None],
        app_routes: list[str] | None = None,
    ) -> list[str]:
        deadline = time.monotonic() + 30.0
        progress("configuration", "配置写入开始")
        self._run_with_budget(_restore_script(snapshot), deadline)
        progress("convergence", "配置写入完成")
        last_errors: list[str] = []
        dns_redelivered = False
        while True:
            current = self._capture_with_budget(interface.index, deadline)
            last_errors = verify_restored(snapshot, current, app_ip, app_routes or [])
            if not last_errors:
                progress("verification", "校验通过")
                return []
            if time.monotonic() >= deadline:
                raise NetworkStateError(
                    "Windows 网卡恢复在 30 秒内未收敛: " + "; ".join(last_errors)
                )
            # ARM64 PowerShell 上 Set-DnsClientServerAddress 与 Get 不同步时，
            # 仅剩 DNS 不一致可主动重发一次（先 Reset 再 Set）以推进收敛。
            if (
                not dns_redelivered
                and snapshot.get("dns_mode") == "static"
                and last_errors
                and all("静态 DNS" in error for error in last_errors)
            ):
                progress("configuration", "重新下发静态 DNS 以推进收敛")
                self._run_with_budget(_dns_set_script(snapshot), deadline)
                dns_redelivered = True
                continue
            progress("convergence", "; ".join(last_errors))
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NetworkStateError("Windows 网卡恢复超过 30 秒总预算")
        return remaining

    @classmethod
    def _run_with_budget(cls, script: str, deadline: float) -> Any:
        return _run_powershell(script, timeout=cls._remaining(deadline))

    @classmethod
    def _capture_with_budget(cls, interface_index: int, deadline: float) -> dict[str, Any]:
        data = _run_powershell(
            _snapshot_script(interface_index),
            timeout=cls._remaining(deadline),
            expect_json=True,
        )
        if not isinstance(data, dict) or int(data.get("interface_index", -1)) != interface_index:
            raise NetworkStateError("网卡快照内容无效")
        data["addresses"] = data.get("addresses") or []
        data["routes"] = data.get("routes") or []
        for key in ("addresses", "routes"):
            if isinstance(data[key], dict):
                data[key] = [data[key]]
        _normalize_dns_servers(data)
        return data

    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
        app_routes: list[str],
    ) -> list[str]:
        return verify_restored(original, current, app_ip, app_routes)


def restore_from_journal(journal_path: str | Path) -> None:
    """保留旧模块公开入口，并交由共享 schema v1/v2 恢复层处理。"""

    from .network_transaction import restore_from_journal as restore

    restore(journal_path, backend=WindowsNetworkBackend())


from .network_transaction import NetworkTransaction  # noqa: E402,F401
