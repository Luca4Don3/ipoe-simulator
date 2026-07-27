from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ipoe_simulator.interfaces import InterfaceInfo
from ipoe_simulator.linux_network import LinuxNetworkBackend
from ipoe_simulator.network_backend import NetworkStateError


INTERFACE = InterfaceInfo("test0", "test0", "test", "aa:bb:cc:dd:ee:ff", 7)


class FailingRunner:
    def run(self, args: list[str], **kwargs: object) -> SimpleNamespace:
        raise NetworkStateError("unavailable")


class ResolvedRunner:
    def run(self, args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["resolvectl", "dns"]:
            return SimpleNamespace(returncode=0, stdout="Link 7: 192.0.2.53\n")
        raise NetworkStateError("unexpected command")


class LinuxDnsTests(unittest.TestCase):
    def make_backend(self, root: Path, runner: object | None = None) -> LinuxNetworkBackend:
        (root / "etc").mkdir()
        return LinuxNetworkBackend(runner=runner or FailingRunner(), root=root)  # type: ignore[arg-type]

    def test_regular_resolv_conf_snapshot_apply_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            path = root / "etc/resolv.conf"
            path.write_text("nameserver 192.0.2.1\n", encoding="ascii")
            snapshot = backend._capture_dns(INTERFACE)
            backend._apply_dns(INTERFACE, snapshot, ["198.51.100.53"])
            self.assertEqual(path.read_text(encoding="ascii"), "nameserver 198.51.100.53\n")
            backend._restore_dns(INTERFACE, snapshot)
            self.assertEqual(path.read_text(encoding="ascii"), "nameserver 192.0.2.1\n")

    def test_working_resolvectl_is_preferred_over_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root, ResolvedRunner())
            os.symlink("../run/systemd/resolve/stub-resolv.conf", root / "etc/resolv.conf")
            self.assertEqual(backend._capture_dns(INTERFACE)["backend"], "systemd-resolved")

    def test_unmanaged_symlink_is_rejected_during_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            os.symlink("../run/resolvconf/resolv.conf", root / "etc/resolv.conf")
            with self.assertRaisesRegex(NetworkStateError, "符号链接"):
                backend._capture_dns(INTERFACE)

    def test_snapshot_file_replaced_by_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            path = root / "etc/resolv.conf"
            target = root / "target"
            path.write_text("old", encoding="ascii")
            snapshot = backend._capture_dns(INTERFACE)
            path.unlink()
            target.write_text("protected", encoding="ascii")
            os.symlink(target, path)
            with self.assertRaises(NetworkStateError):
                backend._apply_dns(INTERFACE, snapshot, ["192.0.2.53"])
            self.assertEqual(target.read_text(encoding="ascii"), "protected")

    def test_old_journal_symlink_target_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            path = root / "etc/resolv.conf"
            os.symlink("../run/old", path)
            snapshot = {"backend": "resolv.conf", "symlink": "../run/expected", "content_base64": base64.b64encode(b"old").decode("ascii")}
            with self.assertRaisesRegex(NetworkStateError, "符号链接状态已变化"):
                backend._restore_dns(INTERFACE, snapshot)

    def test_old_journal_unchanged_symlink_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            target = root / "run/resolvconf/resolv.conf"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"changed")
            os.symlink("../run/resolvconf/resolv.conf", root / "etc/resolv.conf")
            snapshot = {"backend": "resolv.conf", "symlink": "../run/resolvconf/resolv.conf", "content_base64": base64.b64encode(b"original").decode("ascii")}
            backend._restore_dns(INTERFACE, snapshot)
            self.assertEqual(target.read_bytes(), b"original")

    def test_apply_and_restore_write_failures_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            path = root / "etc/resolv.conf"
            path.write_bytes(b"original")
            snapshot = backend._capture_dns(INTERFACE)
            with mock.patch.object(
                backend,
                "_write_regular_file_no_follow",
                side_effect=NetworkStateError("injected DNS write failure"),
            ):
                with self.assertRaisesRegex(NetworkStateError, "injected DNS write failure"):
                    backend._apply_dns(INTERFACE, snapshot, ["192.0.2.53"])
                with self.assertRaisesRegex(NetworkStateError, "injected DNS write failure"):
                    backend._restore_dns(INTERFACE, snapshot)


if __name__ == "__main__":
    unittest.main()
