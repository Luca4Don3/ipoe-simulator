from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coordinator
import ipoedhcp
from ipoe_simulator.network_backend import NetworkStateError


ROOT = Path(__file__).resolve().parents[1]


class ManualRestoreTests(unittest.TestCase):
    def test_ipoedhcp_restore_is_mutually_exclusive_with_other_actions(self) -> None:
        parser = ipoedhcp.build_parser()
        for arguments in (
            ["--restore", "--capture-only", "30"],
            ["--restore", "--list-interfaces"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_restore_accepts_log_level(self) -> None:
        args = ipoedhcp.build_parser().parse_args(
            ["--restore", "--log-level", "DEBUG"]
        )
        self.assertTrue(args.restore)
        self.assertEqual(args.log_level, "DEBUG")

    def test_missing_journal_succeeds_without_config_or_dhcp_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            with (
                mock.patch.object(ipoedhcp, "JOURNAL", journal),
                mock.patch.object(
                    ipoedhcp,
                    "Config",
                    side_effect=AssertionError("配置不应被读取"),
                ),
                mock.patch.object(
                    ipoedhcp,
                    "ensure_runtime",
                    side_effect=AssertionError("DHCP 依赖不应初始化"),
                ),
                mock.patch.object(
                    ipoedhcp,
                    "is_admin",
                    side_effect=AssertionError("无日志时不应检查权限"),
                ),
            ):
                self.assertEqual(ipoedhcp.main(["--restore"]), 0)

    def test_existing_journal_requires_admin_and_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            journal.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(ipoedhcp, "JOURNAL", journal),
                mock.patch.object(ipoedhcp, "is_admin", return_value=False),
                mock.patch.object(ipoedhcp, "restore_from_journal") as restore,
            ):
                self.assertEqual(ipoedhcp.main(["--restore"]), 5)
            restore.assert_not_called()
            self.assertTrue(journal.exists())

    def test_existing_journal_is_restored_without_runtime_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            journal.write_text("{}", encoding="utf-8")

            def restore(path: Path) -> None:
                self.assertEqual(path, journal)
                path.unlink()

            with (
                mock.patch.object(ipoedhcp, "JOURNAL", journal),
                mock.patch.object(ipoedhcp, "is_admin", return_value=True),
                mock.patch.object(ipoedhcp, "restore_from_journal", side_effect=restore),
                mock.patch.object(
                    ipoedhcp,
                    "ensure_runtime",
                    side_effect=AssertionError("DHCP 依赖不应初始化"),
                ),
            ):
                self.assertEqual(ipoedhcp.main(["--restore"]), 0)
            self.assertFalse(journal.exists())

    def test_restore_failure_returns_five_and_preserves_journal(self) -> None:
        failure_messages = (
            "恢复日志损坏",
            "已拒绝跨平台恢复",
            "injected restore failure",
            "injected verify failure",
        )
        for message in failure_messages:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                journal = Path(directory) / "network-recovery.json"
                journal.write_text("{}", encoding="utf-8")
                with (
                    mock.patch.object(ipoedhcp, "JOURNAL", journal),
                    mock.patch.object(ipoedhcp, "is_admin", return_value=True),
                    mock.patch.object(
                        ipoedhcp,
                        "restore_from_journal",
                        side_effect=NetworkStateError(message),
                    ),
                ):
                    self.assertEqual(ipoedhcp.main(["--restore"]), 5)
                self.assertTrue(journal.exists())


class CoordinatorRestoreTests(unittest.TestCase):
    def test_restore_conflicts_with_every_other_main_action(self) -> None:
        conflicts = (
            "--capture",
            "--extract",
            "--dhcp",
            "--show",
            "--reset",
            "--interactive",
        )
        for conflict in conflicts:
            arguments = ["--restore", conflict]
            with self.subTest(arguments=arguments):
                argument_parser = coordinator.parser()
                args = argument_parser.parse_args(arguments)
                with self.assertRaises(SystemExit):
                    coordinator.validate_actions(argument_parser, args)

    def test_restore_routes_before_loading_config(self) -> None:
        with (
            mock.patch.object(coordinator, "do_restore") as restore,
            mock.patch.object(
                coordinator,
                "Config",
                side_effect=AssertionError("配置不应被读取"),
            ),
        ):
            self.assertEqual(
                coordinator.main(
                    [
                        "--restore",
                        "--config",
                        "broken.json",
                        "--log-level",
                        "DEBUG",
                    ]
                ),
                0,
            )
        restore.assert_called_once_with("DEBUG")

    def test_restore_child_exit_code_is_propagated(self) -> None:
        with mock.patch.object(
            coordinator,
            "do_restore",
            side_effect=coordinator.CoordinatorError("restore failed", exit_code=5),
        ):
            self.assertEqual(coordinator.main(["--restore"]), 5)

    def test_launchers_forward_restore_argument(self) -> None:
        run_cmd = (ROOT / "run.cmd").read_text(encoding="utf-8")
        run_sh = (ROOT / "run.sh").read_text(encoding="utf-8")
        windows_launcher = (
            ROOT / "scripts" / "windows_launcher.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("%*", run_cmd)
        self.assertIn('"$@"', run_sh)
        self.assertIn("@LauncherArguments", windows_launcher)


if __name__ == "__main__":
    unittest.main()
