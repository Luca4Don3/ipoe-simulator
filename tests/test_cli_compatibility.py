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


if __name__ == "__main__":
    unittest.main()
