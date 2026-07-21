from __future__ import annotations

import unittest

from ipoe_simulator.windows_network import _restore_script, verify_restored


class NetworkStateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
