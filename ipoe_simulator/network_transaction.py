"""跨平台网卡事务、恢复日志和异常恢复 watchdog 协调。"""

from __future__ import annotations

import json
import ipaddress
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .app_logging import get_logger
from .interfaces import InterfaceInfo
from .network_backend import NetworkBackend, NetworkStateError


JOURNAL_SCHEMA = 2
LOGGER = get_logger("transaction")


class JournalLockedError(NetworkStateError):
    """默认恢复日志正在由另一个实例持有。"""


class JournalLock:
    def __init__(self, journal: Path):
        self.path = journal.with_suffix(journal.suffix + ".lock")
        self.handle: Any | None = None

    def acquire(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+", encoding="utf-8")
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if self.handle.tell() == 0:
                    self.handle.write(" ")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(str(os.getpid()))
            self.handle.flush()
        except OSError as exc:
            self.release()
            owner = "unknown"
            try:
                owner = self.path.read_text(encoding="utf-8").strip() or owner
            except OSError:
                pass
            raise JournalLockedError(f"恢复日志正由 PID {owner} 使用，拒绝修改网卡/journal") from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    except OSError as exc:
        raise NetworkStateError(f"无法创建恢复日志 {path}: {exc}") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        if isinstance(exc, NetworkStateError):
            raise
        raise NetworkStateError(f"无法写入恢复日志 {path}: {exc}") from exc


def _default_backend() -> NetworkBackend:
    from .platform_network import get_backend

    return get_backend()


def _interface_from_v1(snapshot: dict[str, Any]) -> InterfaceInfo:
    try:
        index = int(snapshot["interface_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkStateError("schema v1 恢复日志缺少有效 interface_index") from exc
    name = str(snapshot.get("interface_name") or index)
    return InterfaceInfo(
        pcap_name=name,
        name=name,
        description=str(snapshot.get("interface_guid") or name),
        mac="",
        index=index,
    )


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NetworkStateError(f"恢复日志损坏，不能安全继续: {path}: {exc}") from exc
    if not isinstance(journal, dict):
        raise NetworkStateError(f"恢复日志根节点必须是对象: {path}")
    schema = journal.get("schema", 1)
    if schema not in (1, JOURNAL_SCHEMA):
        raise NetworkStateError(f"不支持的恢复日志 schema: {schema}")
    if not isinstance(journal.get("snapshot"), dict):
        raise NetworkStateError(f"恢复日志缺少 snapshot: {path}")
    app_routes = journal.get("app_routes", [])
    if not isinstance(app_routes, list) or any(not isinstance(item, str) for item in app_routes):
        raise NetworkStateError(f"恢复日志 app_routes 无效: {path}")
    if len(app_routes) > 256:
        raise NetworkStateError(f"恢复日志 app_routes 超过 256 个地址: {path}")
    try:
        normalized_routes = [ipaddress.IPv4Address(item) for item in app_routes]
    except ipaddress.AddressValueError as exc:
        raise NetworkStateError(f"恢复日志 app_routes 包含非法 IPv4 地址: {path}") from exc
    if any(
        item.is_multicast
        or item.is_unspecified
        or item.is_loopback
        or int(item) == 0xFFFFFFFF
        for item in normalized_routes
    ):
        raise NetworkStateError(f"恢复日志 app_routes 包含非单播 IPv4 地址: {path}")
    journal["app_routes"] = [str(item) for item in normalized_routes]
    return journal


def _journal_interface(journal: dict[str, Any]) -> InterfaceInfo:
    if journal.get("schema", 1) == 1:
        return _interface_from_v1(journal["snapshot"])
    try:
        return InterfaceInfo.from_dict(journal["interface"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkStateError("schema v2 恢复日志缺少有效 interface") from exc


def restore_from_journal(
    journal_path: str | Path,
    *,
    backend: NetworkBackend | None = None,
) -> None:
    path = Path(journal_path).resolve()
    lock = JournalLock(path)
    lock.acquire()
    try:
        _restore_from_journal_locked(path, backend=backend)
    finally:
        lock.release()
        if not path.exists():
            try:
                lock.path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("恢复成功但无法清理空闲 journal lock path=%s error=%s", lock.path, exc)


def _restore_from_journal_locked(
    path: Path,
    *,
    backend: NetworkBackend | None = None,
) -> None:
    if not path.exists():
        return
    LOGGER.info("检测到待恢复日志 journal=%s", path)
    journal = _read_journal(path)
    selected = backend or _default_backend()
    schema = journal.get("schema", 1)
    expected_platform = "windows" if schema == 1 else str(journal.get("platform", ""))
    if expected_platform != selected.platform_name:
        raise NetworkStateError(
            f"恢复日志属于 {expected_platform or '未知平台'}，"
            f"当前后端是 {selected.platform_name}；已拒绝跨平台恢复"
        )
    interface = _journal_interface(journal)
    snapshot = journal["snapshot"]
    attempts = journal.get("restore_attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempt: dict[str, Any] = {
        "started_at": int(time.time()),
        "ended_at": None,
        "phase": "starting",
        "last_completed_step": "恢复开始",
        "error": None,
    }
    attempts.append(attempt)
    journal["restore_attempts"] = attempts[-20:]
    journal["total_restore_attempts"] = int(journal.get("total_restore_attempts", 0)) + 1
    journal["status"] = "restoring"
    journal["updated_at"] = int(time.time())
    atomic_write_json(path, journal)
    LOGGER.info(
        "恢复开始 platform=%s interface_index=%s journal=%s",
        selected.platform_name,
        interface.index,
        path,
    )

    def progress(phase: str, step: str) -> None:
        attempt["phase"] = phase
        attempt["last_completed_step"] = step
        journal["updated_at"] = int(time.time())
        atomic_write_json(path, journal)
        LOGGER.info(
            "恢复进度 phase=%s step=%s journal=%s",
            phase,
            step,
            path,
        )

    try:
        with _defer_interrupts():
            errors = selected.restore_and_verify(
                interface,
                snapshot,
                journal.get("app_ip"),
                progress,
                journal.get("app_routes", []),
            )
    except BaseException as exc:
        attempt["ended_at"] = int(time.time())
        attempt["error"] = str(exc)
        journal["status"] = "restore_failed"
        journal["restore_errors"] = [str(exc)]
        journal["updated_at"] = int(time.time())
        atomic_write_json(path, journal)
        if isinstance(exc, NetworkStateError):
            LOGGER.critical("恢复失败，网卡状态可能未恢复 error=%s journal=%s", exc, path)
            raise
        LOGGER.critical("恢复失败，网卡状态可能未恢复 error=%s journal=%s", exc, path)
        raise NetworkStateError(f"恢复执行失败: {exc}") from exc
    if errors:
        attempt["ended_at"] = int(time.time())
        attempt["error"] = "; ".join(errors)
        journal["status"] = "restore_failed"
        journal["restore_errors"] = errors
        journal["updated_at"] = int(time.time())
        atomic_write_json(path, journal)
        LOGGER.critical("恢复校验失败，网卡状态可能未恢复 errors=%s journal=%s", errors, path)
        raise NetworkStateError("; ".join(errors))
    attempt["ended_at"] = int(time.time())
    attempt["phase"] = "verification"
    attempt["last_completed_step"] = "校验通过"
    journal["status"] = "restored"
    journal["updated_at"] = int(time.time())
    atomic_write_json(path, journal)
    LOGGER.info("恢复校验通过 journal=%s", path)
    path.unlink(missing_ok=True)
    LOGGER.info("journal 删除完成 journal=%s", path)


class _defer_interrupts:
    """恢复期间记录后续 Ctrl+C，但不让同一恢复流程重入。"""

    def __init__(self) -> None:
        self.previous: Any = None
        self.active = False

    def __enter__(self) -> "_defer_interrupts":
        if hasattr(signal, "SIGINT"):
            try:
                self.previous = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, self._handle)
                self.active = True
            except (ValueError, OSError):
                pass
        return self

    @staticmethod
    def _handle(signum: int, frame: Any) -> None:
        del signum, frame
        LOGGER.warning("恢复期间收到重复停止请求，将继续当前有界恢复")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self.active:
            signal.signal(signal.SIGINT, self.previous)


@dataclass
class NetworkTransaction:
    interface: InterfaceInfo
    journal_path: Path
    backend: NetworkBackend
    snapshot: dict[str, Any]
    app_ip: str | None = None
    app_routes: list[str] = field(default_factory=list)
    _watchdog_writer: int | None = field(default=None, repr=False)
    _journal_lock: JournalLock | None = field(default=None, repr=False)

    @classmethod
    def begin(
        cls,
        interface: InterfaceInfo,
        journal_path: str | Path,
        *,
        backend: NetworkBackend | None = None,
        start_watchdog: bool = True,
    ) -> "NetworkTransaction":
        if not isinstance(interface, InterfaceInfo):
            raise NetworkStateError(
                "NetworkTransaction.begin() 需要完整 InterfaceInfo，不能只传 interface index"
            )
        selected = backend or _default_backend()
        path = Path(journal_path).resolve()
        lock = JournalLock(path)
        lock.acquire()
        LOGGER.info(
            "事务开始 platform=%s interface_index=%s journal=%s",
            selected.platform_name,
            interface.index,
            path,
        )
        try:
            if path.exists():
                _restore_from_journal_locked(path, backend=selected)
            snapshot = selected.capture_snapshot(interface)
        except Exception:
            lock.release()
            raise
        transaction = cls(interface, path, selected, snapshot, _journal_lock=lock)
        try:
            transaction._write("snapshot_saved")
            if start_watchdog:
                transaction._start_watchdog()
            selected.prepare(interface, snapshot)
            transaction._write("interface_prepared")
        except Exception:
            try:
                transaction.restore()
            except Exception as restore_exc:
                raise NetworkStateError(
                    f"准备网卡失败，且自动恢复失败: {restore_exc}; 恢复日志: {path}"
                )
            raise
        return transaction

    @property
    def interface_index(self) -> int:
        """保留旧调用方只读属性。"""

        return self.interface.index

    def _write(self, status: str) -> None:
        atomic_write_json(
            self.journal_path,
            {
                "schema": JOURNAL_SCHEMA,
                "platform": self.backend.platform_name,
                "status": status,
                "owner_pid": os.getpid(),
                "updated_at": int(time.time()),
                "app_ip": self.app_ip,
                "app_routes": self.app_routes,
                "interface": self.interface.to_dict(),
                "snapshot": self.snapshot,
                "total_restore_attempts": 0,
                "restore_attempts": [],
            },
        )
        LOGGER.debug(
            "事务状态 status=%s platform=%s interface_index=%s journal=%s",
            status,
            self.backend.platform_name,
            self.interface.index,
            self.journal_path,
        )

    def _start_watchdog(self) -> None:
        script = Path(__file__).resolve().parents[1] / "recovery_watchdog.py"
        if not script.exists():
            raise NetworkStateError(f"恢复 watchdog 不存在: {script}")
        command = [
            sys.executable,
            str(script),
            "--journal",
            str(self.journal_path),
        ]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        read_fd: int | None = None
        if os.name == "nt":
            command.extend(("--parent-pid", str(os.getpid())))
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, True)
            command.extend(("--parent-fd", str(read_fd)))
            kwargs["pass_fds"] = (read_fd,)
            kwargs["start_new_session"] = True
            self._watchdog_writer = write_fd
        try:
            subprocess.Popen(command, **kwargs)
        except OSError as exc:
            self._close_watchdog()
            raise NetworkStateError(f"无法启动恢复 watchdog: {exc}") from exc
        finally:
            if read_fd is not None:
                os.close(read_fd)

    def _close_watchdog(self) -> None:
        if self._watchdog_writer is None:
            return
        try:
            os.close(self._watchdog_writer)
        except OSError:
            pass
        self._watchdog_writer = None

    def configure_lease(
        self,
        ip_address: str,
        subnet_mask: str,
        gateway: str | None,
        dns_servers: list[str],
        app_routes: list[str] | None = None,
    ) -> None:
        LOGGER.info(
            "事务应用租约 interface_index=%s ip=%s subnet_mask=%s gateway=%s dns_servers=%s",
            self.interface.index,
            ip_address,
            subnet_mask,
            gateway or "-",
            ",".join(dns_servers) or "-",
        )
        self.app_ip = ip_address
        if len(app_routes or []) > 256:
            raise NetworkStateError("程序静态路由超过 256 个地址")
        try:
            addresses = [ipaddress.IPv4Address(item) for item in (app_routes or [])]
        except ipaddress.AddressValueError as exc:
            raise NetworkStateError(f"程序静态路由包含非法 IPv4 地址: {exc}") from exc
        if any(
            item.is_multicast
            or item.is_unspecified
            or item.is_loopback
            or int(item) == 0xFFFFFFFF
            for item in addresses
        ):
            raise NetworkStateError("程序静态路由包含非单播 IPv4 地址")
        self.app_routes = list(dict.fromkeys(str(item) for item in addresses))
        if self.app_routes and not gateway:
            raise NetworkStateError("DHCP ACK 未提供网关，无法应用单播静态路由")
        self._write("lease_received")
        LOGGER.info("准备应用单播静态路由 count=%s", len(self.app_routes))
        LOGGER.debug("单播静态路由 routes=%s", [f"{item}/32" for item in self.app_routes])
        self.backend.apply_lease(
            self.interface,
            self.snapshot,
            ip_address,
            subnet_mask,
            gateway,
            dns_servers,
            self.app_routes,
        )
        self._write("lease_applied")

    def restore(self) -> None:
        LOGGER.info(
            "事务恢复请求 interface_index=%s journal=%s",
            self.interface.index,
            self.journal_path,
        )
        try:
            if self.journal_path.exists():
                if self._journal_lock is None:
                    restore_from_journal(self.journal_path, backend=self.backend)
                else:
                    _restore_from_journal_locked(self.journal_path, backend=self.backend)
        finally:
            self._close_watchdog()
            if self._journal_lock:
                self._journal_lock.release()
                self._journal_lock = None
