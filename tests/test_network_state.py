from __future__ import annotations

import unittest
from unittest import mock

from ipoe_simulator.network_backend import NetworkStateError
from ipoe_simulator.windows_network import _restore_script, capture_snapshot, verify_restored


class NetworkStateTests(unittest.TestCase):
    def test_capture_rejects_static_dns_without_servers(self) -> None:
        snapshot = {
            "interface_index": 7,
            "addresses": [],
            "routes": [],
            "dns_mode": "static",
            "dns_servers": [],
        }
        with mock.patch("ipoe_simulator.windows_network._run_powershell", return_value=snapshot):
            with self.assertRaisesRegex(NetworkStateError, "修改前停止"):
                capture_snapshot(7)

    def test_legacy_static_empty_dns_resets_but_fails_verification(self) -> None:
        snapshot = {
            "interface_index": 7,
            "dhcp": "Disabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "static",
            "dns_servers": [],
        }
        self.assertIn("-ResetServerAddresses", _restore_script(snapshot))
        current = {**snapshot, "dns_mode": "automatic"}
        self.assertTrue(any("DNS 模式不一致" in error for error in verify_restored(snapshot, current, None)))

    def test_automatic_metric_restore_does_not_write_manual_metric(self) -> None:
        script = _restore_script({
            "interface_index": 7,
            "dhcp": "Enabled",
            "automatic_metric": "Enabled",
            "interface_metric": 42,
        })
        metric_line = next(line for line in script.splitlines() if "-AutomaticMetric" in line)
        self.assertIn("-AutomaticMetric Enabled", metric_line)
        self.assertNotIn("-InterfaceMetric", metric_line)
        self.assertNotIn("ipconfig.exe", script)
        self.assertIn("-Dhcp Enabled", script)
        self.assertIn("-ResetServerAddresses", script)

    def test_manual_metric_restore_writes_original_metric(self) -> None:
        script = _restore_script({
            "interface_index": 7,
            "dhcp": "Disabled",
            "automatic_metric": "Disabled",
            "interface_metric": 42,
            "addresses": [],
            "routes": [],
            "dns_servers": [],
        })
        metric_line = next(line for line in script.splitlines() if "-AutomaticMetric" in line)
        self.assertIn("-AutomaticMetric Disabled", metric_line)
        self.assertIn("-InterfaceMetric 42", metric_line)

    def test_static_state_matches(self) -> None:
        snapshot = {
            "dhcp": "Disabled",
            "automatic_metric": "Disabled",
            "addresses": [{"ip_address": "192.0.2.2", "prefix_length": 24, "prefix_origin": "Manual"}],
            "routes": [{"destination_prefix": "0.0.0.0/0", "next_hop": "192.0.2.1", "route_metric": 10, "protocol": "NetMgmt"}],
            "dns_mode": "static",
            "dns_servers": ["192.0.2.53"],
        }
        self.assertEqual(verify_restored(snapshot, snapshot, "198.51.100.2"), [])

    def test_program_address_is_reported(self) -> None:
        snapshot = {"dhcp": "Disabled", "automatic_metric": "Enabled", "addresses": [], "routes": [], "dns_mode": "automatic", "dns_servers": []}
        current = {**snapshot, "addresses": [{"ip_address": "198.51.100.2", "prefix_length": 24, "prefix_origin": "Manual"}]}
        errors = verify_restored(snapshot, current, "198.51.100.2")
        self.assertTrue(any("程序配置的地址" in error for error in errors))

    def test_dhcp_without_address_is_restored(self) -> None:
        snapshot = {
            "dhcp": "Enabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "automatic",
            "dns_servers": [],
        }
        self.assertEqual(verify_restored(snapshot, snapshot, "198.51.100.2"), [])

    def test_dhcp_program_manual_address_is_reported(self) -> None:
        snapshot = {
            "dhcp": "Enabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "automatic",
            "dns_servers": [],
        }
        current = {
            **snapshot,
            "addresses": [{
                "ip_address": "198.51.100.2",
                "prefix_length": 24,
                "prefix_origin": "Manual",
            }],
        }
        errors = verify_restored(snapshot, current, "198.51.100.2")
        self.assertTrue(any("程序配置的地址" in error for error in errors))

    def test_manual_interface_metric_is_verified(self) -> None:
        snapshot = {
            "dhcp": "Disabled",
            "automatic_metric": "Disabled",
            "interface_metric": 10,
            "addresses": [],
            "routes": [],
            "dns_mode": "automatic",
            "dns_servers": [],
        }
        current = {**snapshot, "interface_metric": 20}
        errors = verify_restored(snapshot, current, None)
        self.assertTrue(any("InterfaceMetric" in error for error in errors))

    def test_static_dns_order_independent_matches(self) -> None:
        snapshot = {
            "dhcp": "Disabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "static",
            "dns_servers": ["8.8.8.8", "1.1.1.1"],
        }
        current = {**snapshot, "dns_servers": ["1.1.1.1", "8.8.8.8"]}
        self.assertEqual(verify_restored(snapshot, current, None), [])

    def test_static_dns_mismatch_reports_difference(self) -> None:
        snapshot = {
            "dhcp": "Disabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "static",
            "dns_servers": ["8.8.8.8", "1.1.1.1"],
        }
        current = {**snapshot, "dns_servers": ["8.8.4.4"]}
        errors = verify_restored(snapshot, current, None)
        self.assertTrue(any("静态 DNS 未完整恢复" in error for error in errors))
        detail = next(error for error in errors if "静态 DNS" in error)
        self.assertIn("期望", detail)
        self.assertIn("实际", detail)
        self.assertIn("缺失", detail)
        self.assertIn("多余", detail)

    def test_dns_redelivered_when_only_dns_remains(self) -> None:
        from ipoe_simulator.interfaces import InterfaceInfo
        from ipoe_simulator.windows_network import WindowsNetworkBackend

        snapshot = {
            "interface_index": 7,
            "interface_guid": "guid",
            "dhcp": "Disabled",
            "automatic_metric": "Enabled",
            "addresses": [],
            "routes": [],
            "dns_mode": "static",
            "dns_servers": ["8.8.8.8"],
        }
        iface = InterfaceInfo(pcap_name="eth", name="eth", description="eth", mac="aa:bb:cc:dd:ee:ff", index=7)
        states = [
            {**snapshot, "dns_servers": []},
            {**snapshot, "dns_servers": ["8.8.8.8"]},
        ]
        calls: list[str] = []

        class Backend(WindowsNetworkBackend):
            def _run_with_budget(self, script, deadline):
                calls.append(script)
                return ""

            def _capture_with_budget(self, interface_index, deadline):
                calls.append(f"capture:{interface_index}")
                return states.pop(0)

        progresses: list[tuple[str, str]] = []
        backend = Backend()
        errors = backend.restore_and_verify(
            iface, snapshot, None, lambda phase, step: progresses.append((phase, step))
        )
        self.assertEqual(errors, [])
        self.assertTrue(any("重新下发静态 DNS" in step for _, step in progresses))
        # 配置脚本 + 首次 capture -> DNS 不一致 -> _dns_set_script 重发 -> capture 一致
        self.assertIn("重新下发静态 DNS", " ".join(step for _, step in progresses))
        self.assertEqual(
            sum(1 for c in calls if c.startswith("capture:")), 2
        )
        self.assertTrue(any("-ResetServerAddresses" in c for c in calls[1:]))
        self.assertTrue(
            any(
                "Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses @(" in c
                for c in calls
            )
        )


if __name__ == "__main__":
    unittest.main()
