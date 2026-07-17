from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ipoe_simulator.interfaces import InterfaceInfo
from ipoe_simulator.linux_network import (
    detect_network_manager,
    parse_ip_json_addresses,
    parse_ip_json_routes,
    parse_ip_o_addresses,
    parse_ip_o_routes,
)
from ipoe_simulator.macos_network import (
    parse_additional_routes,
    parse_dns_servers,
    parse_ifconfig_ipv4,
    parse_ifconfig_mac,
    parse_network_info,
    parse_service_order,
)
from ipoe_simulator.network_backend import (
    CommandResult,
    NetworkStateError,
)


TEST_MAC = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
INTERFACE = InterfaceInfo("eth0", "eth0", "Ethernet", TEST_MAC, 2)


class StaticRunner:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]):
        self.responses = responses

    def run(
        self,
        args: list[str],
        *,
        timeout: int = 45,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        key = tuple(args)
        if key not in self.responses:
            raise NetworkStateError(f"missing command: {key}")
        returncode, stdout, stderr = self.responses[key]
        if returncode not in allowed_returncodes:
            raise NetworkStateError(stderr or stdout)
        return CommandResult(key, returncode, stdout, stderr)


class MacOSParserTests(unittest.TestCase):
    def test_service_and_configuration_parsing(self) -> None:
        order = """
An asterisk (*) denotes that a network service is disabled.
(1) Wi-Fi
(Hardware Port: Wi-Fi, Device: en0)
(2) *Thunderbolt Bridge
(Hardware Port: Thunderbolt Bridge, Device: bridge0)
"""
        services = parse_service_order(order)
        self.assertEqual(services["en0"]["service"], "Wi-Fi")
        self.assertTrue(services["en0"]["enabled"])
        self.assertFalse(services["bridge0"]["enabled"])

        mask = ".".join(("255", "255", "255", "0"))
        info = parse_network_info(
            f"""
Manual Configuration
IP address: 192.0.2.10
Subnet mask: {mask}
Router: 192.0.2.1
"""
        )
        self.assertEqual(info["mode"], "manual")
        self.assertEqual(info["gateway"], "192.0.2.1")

    def test_dns_routes_and_ifconfig_parsing(self) -> None:
        self.assertEqual(
            parse_dns_servers("There aren't any DNS Servers set on Wi-Fi.\n"),
            ("automatic", []),
        )
        routes = parse_additional_routes(
            f"""
Destination Address: 198.51.100.0
Subnet Mask: {'.'.join(('255', '0', '0', '0'))}
Router: 192.0.2.1
"""
        )
        self.assertEqual(routes[0]["destination"], "198.51.100.0")
        addresses = parse_ifconfig_ipv4(
            f"""
en0: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500
    ether {TEST_MAC}
    inet 192.0.2.10 netmask 0xffffff00 broadcast 192.0.2.255
"""
        )
        self.assertEqual(addresses[0]["prefix_length"], 24)
        self.assertEqual(
            parse_ifconfig_mac(f"    ether {TEST_MAC}\n"),
            TEST_MAC,
        )


class LinuxParserTests(unittest.TestCase):
    def test_modern_ip_json(self) -> None:
        addresses = parse_ip_json_addresses(
            json.dumps(
                [
                    {
                        "addr_info": [
                            {
                                "family": "inet",
                                "local": "192.0.2.10",
                                "prefixlen": 24,
                                "broadcast": "192.0.2.255",
                                "scope": "global",
                                "dynamic": True,
                            }
                        ]
                    }
                ]
            )
        )
        self.assertTrue(addresses[0]["dynamic"])
        routes = parse_ip_json_routes(
            json.dumps(
                [
                    {
                        "dst": "default",
                        "gateway": "192.0.2.1",
                        "metric": 100,
                        "protocol": "dhcp",
                    }
                ]
            )
        )
        self.assertEqual(routes[0]["gateway"], "192.0.2.1")

    def test_legacy_ip_oneline(self) -> None:
        addresses = parse_ip_o_addresses(
            "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 "
            "scope global dynamic eth0\\       valid_lft 100sec\n"
        )
        self.assertEqual(addresses[0]["prefix_length"], 24)
        self.assertTrue(addresses[0]["dynamic"])
        routes = parse_ip_o_routes(
            "default via 192.0.2.1 dev eth0 proto dhcp metric 100\n"
            "192.0.2.0/24 proto kernel scope link src 192.0.2.10\n"
        )
        self.assertEqual(routes[0]["metric"], 100)
        self.assertEqual(routes[1]["scope"], "link")

    def test_networkmanager_detection(self) -> None:
        command = (
            "nmcli",
            "-t",
            "-f",
            "GENERAL.STATE,GENERAL.CONNECTION",
            "device",
            "show",
            "eth0",
        )
        runner = StaticRunner(
            {
                command: (
                    0,
                    "GENERAL.STATE:100 (connected)\n"
                    "GENERAL.CONNECTION:Wired connection 1\n",
                    "",
                )
            }
        )
        manager = detect_network_manager(INTERFACE, runner)
        self.assertEqual(manager["type"], "NetworkManager")
        self.assertEqual(manager["connection"], "Wired connection 1")

    def test_unknown_dynamic_manager_fails_before_mutation(self) -> None:
        runner = StaticRunner({})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(NetworkStateError, "未知动态网络管理器"):
                detect_network_manager(
                    INTERFACE,
                    runner,
                    root=Path(directory),
                    dynamic_addresses=True,
                )


if __name__ == "__main__":
    unittest.main()
