from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import coordinator
import extract_params
import ipoedhcp
from ipoe_simulator.profile import OPTION_CODES


class CliCompatibilityTests(unittest.TestCase):
    mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))

    def test_ipoedhcp_existing_arguments_remain_available(self) -> None:
        parser = ipoedhcp.build_parser()
        args = parser.parse_args(
            [
                "--mac",
                self.mac,
                "--interface",
                "12",
                "--option60",
                "ITV",
                "--timeout",
                "9",
            ]
        )
        self.assertEqual(args.mac, self.mac)
        self.assertEqual(args.interface, "12")
        self.assertEqual(args.option60, "ITV")
        self.assertEqual(args.timeout, 9)
        for code in OPTION_CODES:
            self.assertTrue(hasattr(args, f"option{code}"))

    def test_coordinator_existing_arguments_remain_available(self) -> None:
        args = coordinator.parser().parse_args(
            ["--dhcp", "--interface", "12", "--option60", "ITV"]
        )
        self.assertTrue(args.dhcp)
        self.assertEqual(args.interface, "12")
        self.assertEqual(args.option60, "ITV")

    def test_log_level_is_available_without_changing_existing_arguments(self) -> None:
        ipoe_args = ipoedhcp.build_parser().parse_args(["--log-level", "DEBUG"])
        coordinator_args = coordinator.parser().parse_args(["--log-level", "INFO"])
        self.assertEqual(ipoe_args.log_level, "DEBUG")
        self.assertEqual(coordinator_args.log_level, "INFO")

    def test_coordinator_combined_actions_remain_available(self) -> None:
        args = coordinator.parser().parse_args(
            ["--capture", "30", "--extract", "capture.pcap", "--dhcp"]
        )
        coordinator.validate_actions(coordinator.parser(), args)
        self.assertEqual(args.capture, "30")
        self.assertEqual(args.extract, "capture.pcap")
        self.assertTrue(args.dhcp)

    def test_removed_all_action_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            coordinator.parser().parse_args(["--all"])
        self.assertEqual(raised.exception.code, 2)

    def test_extract_json_path_is_forwarded_to_generated_command(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "dial.json"
            result = {"mac": self.mac, "network": {"unicast_routes": ["198.51.100.10"]}}
            with mock.patch(
                "sys.argv",
                ["extract_params.py", "synthetic.pcap", "--json", str(config_path)],
            ), mock.patch.object(extract_params, "ensure_scapy"), mock.patch.object(
                extract_params, "extract_profile", return_value=result
            ), self.assertLogs("ipoe-simulator.extract", level="INFO") as captured:
                self.assertEqual(extract_params.main(), 0)
        command_log = next(line for line in captured.output if "直接运行命令" in line)
        self.assertIn("--config", command_log)
        self.assertIn(str(config_path.resolve()), command_log)

    def test_extract_without_json_warns_routes_are_not_forwarded(self) -> None:
        result = {"mac": self.mac, "network": {"unicast_routes": ["198.51.100.10"]}}
        with mock.patch(
            "sys.argv", ["extract_params.py", "synthetic.pcap"]
        ), mock.patch.object(extract_params, "ensure_scapy"), mock.patch.object(
            extract_params, "extract_profile", return_value=result
        ), self.assertLogs("ipoe-simulator.extract", level="WARNING") as captured:
            self.assertEqual(extract_params.main(), 0)
        self.assertTrue(any("不会自动进入拨号配置" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
