from __future__ import annotations

import unittest

import coordinator
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


if __name__ == "__main__":
    unittest.main()
