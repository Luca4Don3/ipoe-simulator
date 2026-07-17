from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ipoe_simulator.profile import Config, ConfigError, normalize_mac, option_bytes


class ProfileTests(unittest.TestCase):
    mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
    zero_mac = ":".join(["00"] * 6)

    def test_normalize_mac(self) -> None:
        self.assertEqual(normalize_mac("-".join(("AA", "BB", "CC", "DD", "EE", "FF"))), self.mac)

    def test_invalid_mac(self) -> None:
        with self.assertRaises(ConfigError):
            normalize_mac(self.zero_mac)

    def test_option50(self) -> None:
        self.assertEqual(option_bytes("192.0.2.10", 50), b"\xc0\x00\x02\x0a")
        self.assertEqual(option_bytes("0x0102", 43), b"\x01\x02")

    def test_atomic_config_round_trip_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(path)
            config.set("12", "device", "interface")
            config.merge_extracted({"mac": self.mac, "option60": "STB"}, "capture.pcap")
            config.save()
            loaded = Config(path)
            self.assertEqual(loaded.get("device", "interface"), "12")
            self.assertEqual(loaded.get("device", "mac"), self.mac)
            self.assertEqual(loaded.get("dhcp_options", "option60"), "STB")
            json.loads(path.read_text(encoding="utf-8"))

    def test_log_directory_defaults_to_project_root_and_accepts_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root / "config.json")
            self.assertEqual(config.log_directory(root), root.resolve())
            config.set("logs", "logging", "directory")
            self.assertEqual(config.log_directory(root), (root / "logs").resolve())

    def test_restore_cannot_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "config.json")
            config.set(self.mac, "device", "mac")
            config.set("12", "device", "interface")
            config.set(False, "behavior", "restore_on_exit")
            with self.assertRaises(ConfigError):
                config.validate_for_dhcp()


if __name__ == "__main__":
    unittest.main()
