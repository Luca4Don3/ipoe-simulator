from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from ipoe_simulator.interfaces import InterfaceInfo
from ipoe_simulator.network_backend import NetworkBackend, NetworkStateError
from ipoe_simulator.network_transaction import (
    JOURNAL_SCHEMA,
    NetworkTransaction,
    restore_from_journal,
)


# 合成测试数据：本地管理 MAC；IP 使用 RFC 5737 文档保留网段。
INTERFACE = InterfaceInfo(
    pcap_name="test0",
    name="test0",
    description="offline test",
    mac=":".join(("aa", "bb", "cc", "dd", "ee", "ff")),
    index=7,
)
SUBNET_MASK = ".".join(("255", "255", "255", "0"))


class FakeBackend(NetworkBackend):
    def __init__(
        self,
        platform_name: str = "linux",
        *,
        fail_prepare: bool = False,
        fail_apply: bool = False,
        fail_restore: bool = False,
        verify_errors: list[str] | None = None,
    ):
        self.platform_name = platform_name
        self.fail_prepare = fail_prepare
        self.fail_apply = fail_apply
        self.fail_restore = fail_restore
        self.verify_errors = verify_errors or []
        self.original = {
            "interface_index": INTERFACE.index,
            "interface_name": INTERFACE.name,
            "addresses": [{"ip_address": "192.0.2.2", "prefix_length": 24}],
        }
        self.restored = False
        self.prepared = False
        self.applied = False
        self.received_app_routes: list[str] = []

    def capture_snapshot(self, interface: InterfaceInfo) -> dict[str, Any]:
        self.assert_interface(interface)
        return deepcopy(self.original)

    def prepare(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        self.assert_interface(interface)
        self.prepared = True
        if self.fail_prepare:
            raise NetworkStateError("injected prepare failure")

    def apply_lease(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
        app_routes: list[str],
    ) -> None:
        self.assert_interface(interface)
        self.applied = True
        self.received_app_routes = list(app_routes)
        if self.fail_apply:
            raise NetworkStateError("injected apply failure")

    def restore(
        self,
        interface: InterfaceInfo,
        snapshot: dict[str, Any],
    ) -> None:
        self.assert_interface(interface)
        if self.fail_restore:
            raise NetworkStateError("injected restore failure")
        self.restored = True

    def verify_restored(
        self,
        original: dict[str, Any],
        current: dict[str, Any],
        app_ip: str | None,
        app_routes: list[str],
    ) -> list[str]:
        return list(self.verify_errors)

    @staticmethod
    def assert_interface(interface: InterfaceInfo) -> None:
        if interface.index != INTERFACE.index:
            raise AssertionError("unexpected interface")


class TransactionTests(unittest.TestCase):
    def test_info_log_discloses_route_count_only(self) -> None:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE, journal, backend=backend, start_watchdog=False
            )
            with self.assertLogs("ipoe-simulator.transaction", level="INFO") as captured:
                transaction.configure_lease(
                    "192.0.2.10",
                    SUBNET_MASK,
                    "192.0.2.1",
                    ["192.0.2.53"],
                    ["198.51.100.10"],
                )
            transaction.restore()
        output = "\n".join(captured.output)
        self.assertIn("count=1", output)
        self.assertNotIn("198.51.100.10", output)

    def test_damaged_journal_is_preserved(self) -> None:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            journal.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(NetworkStateError, "恢复日志损坏"):
                restore_from_journal(journal, backend=backend)
            self.assertTrue(journal.exists())

    def test_schema_v2_round_trip(self) -> None:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE,
                journal,
                backend=backend,
                start_watchdog=False,
            )
            saved = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], JOURNAL_SCHEMA)
            self.assertEqual(saved["platform"], "linux")
            self.assertEqual(saved["interface"]["name"], INTERFACE.name)
            transaction.configure_lease(
                "198.51.100.10",
                SUBNET_MASK,
                "198.51.100.1",
                ["198.51.100.53"],
            )
            transaction.restore()
            self.assertTrue(backend.restored)
            self.assertFalse(journal.exists())

    def test_windows_schema_v1_is_restored(self) -> None:
        backend = FakeBackend(platform_name="windows")
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            journal.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "status": "lease_applied",
                        "app_ip": "198.51.100.10",
                        "snapshot": backend.original,
                    }
                ),
                encoding="utf-8",
            )
            restore_from_journal(journal, backend=backend)
            self.assertTrue(backend.restored)
            self.assertFalse(journal.exists())

    def test_prepare_failure_triggers_restore(self) -> None:
        backend = FakeBackend(fail_prepare=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            with self.assertRaisesRegex(NetworkStateError, "injected prepare"):
                NetworkTransaction.begin(
                    INTERFACE,
                    journal,
                    backend=backend,
                    start_watchdog=False,
                )
            self.assertTrue(backend.restored)
            self.assertFalse(journal.exists())

    def test_apply_failure_can_be_restored(self) -> None:
        backend = FakeBackend(fail_apply=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE,
                journal,
                backend=backend,
                start_watchdog=False,
            )
            with self.assertRaisesRegex(NetworkStateError, "injected apply"):
                transaction.configure_lease(
                    "198.51.100.10",
                    SUBNET_MASK,
                    None,
                    [],
                )
            self.assertTrue(journal.exists())
            transaction.restore()
            self.assertTrue(backend.restored)
            self.assertFalse(journal.exists())

    def test_app_routes_are_journaled_before_partial_apply_failure(self) -> None:
        backend = FakeBackend(fail_apply=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE, journal, backend=backend, start_watchdog=False
            )
            with self.assertRaisesRegex(NetworkStateError, "injected apply"):
                transaction.configure_lease(
                    "198.51.100.10",
                    SUBNET_MASK,
                    "198.51.100.1",
                    [],
                    ["203.0.113.10"],
                )
            saved = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], 2)
            self.assertEqual(saved["app_routes"], ["203.0.113.10"])
            self.assertEqual(backend.received_app_routes, ["203.0.113.10"])
            transaction.restore()
            self.assertFalse(journal.exists())

    def test_routes_require_gateway_before_backend_apply(self) -> None:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE, journal, backend=backend, start_watchdog=False
            )
            with self.assertRaisesRegex(NetworkStateError, "未提供网关"):
                transaction.configure_lease(
                    "198.51.100.10", SUBNET_MASK, None, [], ["203.0.113.10"]
                )
            self.assertFalse(backend.applied)
            transaction.restore()

    def test_restore_failure_preserves_journal(self) -> None:
        backend = FakeBackend(fail_restore=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE,
                journal,
                backend=backend,
                start_watchdog=False,
            )
            with self.assertRaisesRegex(NetworkStateError, "injected restore"):
                transaction.restore()
            saved = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "restore_failed")
            self.assertIn("injected restore failure", saved["restore_errors"][0])
            self.assertEqual(saved["total_restore_attempts"], 1)
            attempt = saved["restore_attempts"][-1]
            self.assertEqual(attempt["phase"], "configuration")
            self.assertIsNotNone(attempt["ended_at"])
            self.assertIn("injected restore failure", attempt["error"])

    def test_restore_interrupt_is_recorded_without_traceback(self) -> None:
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE,
                journal,
                backend=backend,
                start_watchdog=False,
            )
            backend.restore = lambda interface, snapshot: (_ for _ in ()).throw(
                KeyboardInterrupt()
            )

            with self.assertRaisesRegex(NetworkStateError, "恢复执行失败"):
                transaction.restore()

            saved = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "restore_failed")
            self.assertEqual(saved["total_restore_attempts"], 1)

    def test_verification_failure_preserves_journal(self) -> None:
        backend = FakeBackend(verify_errors=["injected verify failure"])
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            transaction = NetworkTransaction.begin(
                INTERFACE,
                journal,
                backend=backend,
                start_watchdog=False,
            )
            with self.assertRaisesRegex(NetworkStateError, "injected verify"):
                transaction.restore()
            saved = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "restore_failed")
            self.assertEqual(
                saved["restore_errors"],
                ["injected verify failure"],
            )

    def test_cross_platform_journal_is_rejected(self) -> None:
        backend = FakeBackend(platform_name="linux")
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "network-recovery.json"
            journal.write_text(
                json.dumps(
                    {
                        "schema": 2,
                        "platform": "macos",
                        "status": "snapshot_saved",
                        "interface": INTERFACE.to_dict(),
                        "snapshot": backend.original,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(NetworkStateError, "拒绝跨平台恢复"):
                restore_from_journal(journal, backend=backend)
            self.assertTrue(journal.exists())

    def test_begin_rejects_bare_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(NetworkStateError, "完整 InterfaceInfo"):
                NetworkTransaction.begin(  # type: ignore[arg-type]
                    7,
                    Path(directory) / "journal.json",
                    backend=FakeBackend(),
                    start_watchdog=False,
                )


if __name__ == "__main__":
    unittest.main()
