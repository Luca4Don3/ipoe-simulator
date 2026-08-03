from __future__ import annotations

import unittest
from unittest import mock

from ipoe_simulator.interfaces import InterfaceInfo
from ipoe_simulator.linux_network import LinuxNetworkBackend
from ipoe_simulator.macos_network import MacOSNetworkBackend
from ipoe_simulator.network_backend import CommandResult
from ipoe_simulator.windows_network import apply_lease as apply_windows_lease
from ipoe_simulator.windows_network import verify_restored as verify_windows_restored


# 合成测试数据：本地管理 MAC；IP 使用 RFC 5737 或 RFC 2544 保留网段。
INTERFACE = InterfaceInfo("eth0", "eth0", "Ethernet", "aa:bb:cc:dd:ee:ff", 7)


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, args, *, timeout=45, allowed_returncodes=(0,)):
        command = tuple(str(item) for item in args)
        self.commands.append(command)
        return CommandResult(command, 0, "", "")


class UnicastRouteBackendTests(unittest.TestCase):
    def test_windows_uses_active_store_32_and_gateway_without_metric(self) -> None:
        scripts: list[str] = []
        with mock.patch(
            "ipoe_simulator.windows_network._run_powershell",
            side_effect=lambda script: scripts.append(script),
        ):
            apply_windows_lease(
                7,
                "192.0.2.10",
                "255.255.255.0",
                "192.0.2.1",
                [],
                ["198.51.100.10"],
            )
        route_line = next(
            line
            for script in scripts
            for line in script.splitlines()
            if "198.51.100.10/32" in line
        )
        self.assertIn("-PolicyStore ActiveStore", route_line)
        self.assertIn("-NextHop '192.0.2.1'", route_line)
        self.assertNotIn("RouteMetric", route_line)

    def test_linux_replaces_32_routes_via_ack_gateway(self) -> None:
        runner = RecordingRunner()
        backend = LinuxNetworkBackend(runner=runner)
        snapshot = {"link": {"mac": ""}, "dns": {"backend": "systemd-resolved"}}
        with mock.patch("ipoe_simulator.linux_network._require_linux"):
            backend.apply_lease(
                INTERFACE,
                snapshot,
                "192.0.2.10",
                "255.255.255.0",
                "192.0.2.1",
                [],
                ["198.51.100.10"],
            )
        self.assertIn(
            ("ip", "-4", "route", "replace", "198.51.100.10/32", "via", "192.0.2.1", "dev", "eth0"),
            runner.commands,
        )

    def test_macos_sets_additional_host_routes(self) -> None:
        runner = RecordingRunner()
        backend = MacOSNetworkBackend(runner=runner)
        snapshot = {"service": "IPTV"}
        with mock.patch("ipoe_simulator.macos_network._require_macos"), mock.patch.object(
            backend, "_validate_identity"
        ):
            backend.apply_lease(
                INTERFACE,
                snapshot,
                "192.0.2.10",
                "255.255.255.0",
                "192.0.2.1",
                [],
                ["198.51.100.10"],
            )
        self.assertIn(
            ("networksetup", "-setadditionalroutes", "IPTV", "198.51.100.10", "255.255.255.255", "192.0.2.1"),
            runner.commands,
        )

    def test_windows_verification_reports_program_route_residue(self) -> None:
        original = {
            "dhcp": "Enabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "automatic",
            "dns_servers": [],
        }
        current = {
            **original,
            "routes": [{
                "destination_prefix": "198.51.100.10/32",
                "next_hop": "192.0.2.1",
                "route_metric": 0,
                "protocol": "NetMgmt",
            }],
        }
        errors = verify_windows_restored(
            original, current, None, ["198.51.100.10"]
        )
        self.assertTrue(any("程序配置的静态路由" in error for error in errors))

    def test_linux_verification_reports_program_route_residue(self) -> None:
        backend = LinuxNetworkBackend(runner=RecordingRunner())
        original = {
            "manager": {"type": "NetworkManager", "connection": "IPTV"},
            "addresses": [{"ip_address": "192.0.2.10", "prefix_length": 24, "dynamic": True}],
            "routes": [],
            "dns": {"automatic": True},
            "link": {"up": True},
        }
        current = {
            **original,
            "routes": [{
                "destination": "198.51.100.10/32",
                "gateway": "192.0.2.1",
                "metric": 0,
                "table": "main",
            }],
        }
        errors = backend.verify_restored(
            original, current, None, ["198.51.100.10"]
        )
        self.assertTrue(any("程序配置的静态路由" in error for error in errors))

    def test_linux_verification_normalizes_host_route_without_32(self) -> None:
        backend = LinuxNetworkBackend(runner=RecordingRunner())
        original = {
            "manager": {"type": "NetworkManager", "connection": "IPTV"},
            "addresses": [],
            "routes": [],
            "dns": {"automatic": True},
            "link": {"up": True},
        }
        current = {
            **original,
            "routes": [{
                "destination": "198.51.100.10",
                "gateway": "192.0.2.1",
                "metric": 0,
                "table": "main",
            }],
        }
        errors = backend.verify_restored(
            original, current, None, ["198.51.100.10"]
        )
        self.assertTrue(any("程序配置的静态路由" in error for error in errors))

    def test_macos_verification_reports_program_route_residue(self) -> None:
        backend = MacOSNetworkBackend(runner=RecordingRunner())
        original = {
            "service_enabled": True,
            "configuration_mode": "dhcp",
            "dns_mode": "automatic",
            "dns_servers": [],
            "additional_routes": [],
            "addresses": [],
        }
        current = {
            **original,
            "additional_routes": [{
                "destination": "198.51.100.10",
                "subnet_mask": "255.255.255.255",
                "gateway": "192.0.2.1",
            }],
        }
        errors = backend.verify_restored(
            original, current, None, ["198.51.100.10"]
        )
        self.assertTrue(any("程序配置的静态路由" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
