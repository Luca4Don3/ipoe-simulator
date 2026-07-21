from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coordinator
from ipoe_simulator.interfaces import InterfaceInfo
from ipoe_simulator.profile import Config


INTERFACE = InterfaceInfo(
    pcap_name=r"\Device\NPF_{TEST}",
    name="以太网 3",
    description="USB Ethernet Adapter",
    mac="aa:bb:cc:dd:ee:ff",
    index=6,
)


class CoordinatorInteractiveTests(unittest.TestCase):
    def config(self, directory: str) -> Config:
        return Config(Path(directory) / "config.json")

    def test_selects_interface_and_persists_stable_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            with (
                mock.patch.object(coordinator, "list_interfaces", return_value=[INTERFACE]),
                mock.patch.object(
                    coordinator,
                    "resolve_interface",
                    return_value=INTERFACE,
                ) as resolve,
                mock.patch("builtins.input", return_value="6"),
            ):
                selected = coordinator.select_interface(config)

            self.assertEqual(selected, INTERFACE)
            resolve.assert_called_once_with("6")
            self.assertEqual(
                config.get("device", "interface"),
                INTERFACE.pcap_name,
            )
            self.assertEqual(
                self.config(directory).get("device", "interface"),
                INTERFACE.pcap_name,
            )

    def test_reuses_configured_interface_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.set("6", "device", "interface")
            with (
                mock.patch.object(coordinator, "list_interfaces", return_value=[INTERFACE]),
                mock.patch.object(
                    coordinator,
                    "resolve_interface",
                    return_value=INTERFACE,
                ),
                mock.patch(
                    "builtins.input",
                    return_value="",
                ),
            ):
                selected = coordinator.select_interface(config)

            self.assertEqual(selected, INTERFACE)

    def test_configured_interface_can_be_changed(self) -> None:
        replacement = InterfaceInfo(
            pcap_name=r"\Device\NPF_{OTHER}",
            name="以太网",
            description="Ethernet Adapter",
            mac="11:22:33:44:55:66",
            index=4,
        )
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.set(INTERFACE.pcap_name, "device", "interface")
            with (
                mock.patch.object(
                    coordinator,
                    "list_interfaces",
                    return_value=[replacement, INTERFACE],
                ),
                mock.patch.object(
                    coordinator,
                    "resolve_interface",
                    side_effect=[INTERFACE, replacement],
                ),
                mock.patch("builtins.input", return_value="4"),
            ):
                selected = coordinator.select_interface(config)

            self.assertEqual(selected, replacement)
            self.assertEqual(
                config.get("device", "interface"),
                replacement.pcap_name,
            )

    def test_interactive_capture_uses_fresh_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.set("existing.pcapng", "capture", "pcap_file")
            with (
                mock.patch("builtins.input", side_effect=["1", "30", "0", "0"]),
                mock.patch.object(
                    coordinator,
                    "select_interface",
                    side_effect=[INTERFACE],
                ),
                mock.patch.object(coordinator, "do_capture") as capture,
            ):
                self.assertEqual(coordinator.interactive(config), 0)

            capture.assert_called_once_with(config, 30, fresh_output=True)

    def test_extract_can_continue_to_interface_selection_and_dhcp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            with (
                mock.patch("builtins.input", return_value="1"),
                mock.patch.object(
                    coordinator,
                    "select_interface",
                    return_value=INTERFACE,
                ) as select,
                mock.patch.object(coordinator, "do_dhcp") as dhcp,
            ):
                result = coordinator.after_extract(config)

            self.assertIsNone(result)
            select.assert_called_once_with(config)
            dhcp.assert_called_once_with(config)

    def test_extract_can_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            with mock.patch("builtins.input", return_value="0"):
                self.assertEqual(coordinator.after_extract(config), 0)

    def test_direct_dhcp_selects_interface_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            with (
                mock.patch("builtins.input", side_effect=["3", "0"]),
                mock.patch.object(
                    coordinator,
                    "select_interface",
                    return_value=INTERFACE,
                ) as select,
                mock.patch.object(coordinator, "do_dhcp") as dhcp,
            ):
                self.assertEqual(coordinator.interactive(config), 0)

            select.assert_called_once_with(config)
            dhcp.assert_called_once_with(config)

    def test_ctrl_c_at_menu_exits_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                self.assertEqual(coordinator.interactive(config), 0)

    def test_pending_journal_restricts_interactive_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            journal = Path(directory) / "network-recovery.json"
            journal.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(coordinator, "JOURNAL", journal),
                mock.patch("builtins.input", side_effect=["1", "0"]),
                mock.patch.object(coordinator, "do_capture") as capture,
            ):
                self.assertEqual(coordinator.interactive(config), 0)
            capture.assert_not_called()

    def test_successful_restore_unlocks_interactive_menu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            journal = Path(directory) / "network-recovery.json"
            journal.write_text("{}", encoding="utf-8")

            def restore() -> None:
                journal.unlink()

            with (
                mock.patch.object(coordinator, "JOURNAL", journal),
                mock.patch("builtins.input", side_effect=["6", "0"]),
                mock.patch.object(coordinator, "do_restore", side_effect=restore),
            ):
                self.assertEqual(coordinator.interactive(config), 0)

    def test_clear_config_requires_clear_and_restores_full_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.set("aa:bb:cc:dd:ee:ff", "device", "mac")
            with mock.patch("builtins.input", return_value="clear"):
                self.assertFalse(coordinator.clear_config(config))
            self.assertEqual(config.get("device", "mac"), "aa:bb:cc:dd:ee:ff")

            with mock.patch("builtins.input", return_value="CLEAR"):
                self.assertTrue(coordinator.clear_config(config))
            self.assertEqual(config.data, coordinator.DEFAULT_CONFIG)
            self.assertEqual(self.config(directory).data, coordinator.DEFAULT_CONFIG)

    def test_manual_edit_cancellation_preserves_formal_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            config.save()
            before = config.path.read_bytes()
            responses = ["aa:bb:cc:dd:ee:ff", *("" for _ in range(10)), "CANCEL"]
            with mock.patch("builtins.input", side_effect=responses):
                self.assertFalse(coordinator.edit_config(config))
            self.assertEqual(config.path.read_bytes(), before)

    def test_manual_edit_saves_once_after_save_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self.config(directory)
            responses = ["aa:bb:cc:dd:ee:ff", *("" for _ in range(10)), "SAVE"]
            with (
                mock.patch("builtins.input", side_effect=responses),
                mock.patch.object(config, "save", wraps=config.save) as save,
            ):
                self.assertTrue(coordinator.edit_config(config))
            save.assert_called_once_with()
            self.assertEqual(config.get("device", "mac"), "aa:bb:cc:dd:ee:ff")

    def test_manual_value_retries_invalid_input(self) -> None:
        with mock.patch("builtins.input", side_effect=["invalid", "192.0.2.1"]):
            value = coordinator._manual_value(
                "IPv4", "", lambda item: str(__import__("ipaddress").IPv4Address(item))
            )
        self.assertEqual(value, "192.0.2.1")

    def test_parent_waits_for_child_cleanup_after_ctrl_c(self) -> None:
        process = mock.Mock(pid=1234)
        process.wait.side_effect = [KeyboardInterrupt, 0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "child.py").write_text("", encoding="utf-8")
            with (
                mock.patch.object(coordinator, "ROOT", root),
                mock.patch.object(
                    coordinator.subprocess,
                    "Popen",
                    return_value=process,
                ),
            ):
                coordinator.run_script("child.py", [], "test")

        self.assertEqual(process.wait.call_count, 2)

if __name__ == "__main__":
    unittest.main()
