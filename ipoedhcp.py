#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from ipoe_simulator.app_logging import LOG_LEVELS, LoggingError, configure_logging, get_logger
from ipoe_simulator.capture import CaptureError, capture_dhcp, default_capture_path
from ipoe_simulator.dhcp_client import DhcpClient, DhcpError, DhcpStopped
from ipoe_simulator.dependencies import DependencyError, ensure_runtime, is_admin
from ipoe_simulator.interfaces import InterfaceError, list_interfaces, resolve_interface
from ipoe_simulator.profile import Config, ConfigError, OPTION_CODES
from ipoe_simulator.platform_network import (
    NetworkStateError,
    NetworkTransaction,
    default_journal_path,
    restore_from_journal,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "ipoedhcp_config.json"
JOURNAL = default_journal_path(ROOT)
LOGGER = get_logger("cli")


class StopController:
    def __init__(self, client: DhcpClient):
        self.client = client
        self.requested = False
        self.phase = "DHCP 交换"

    def handle(self, _signum, _frame) -> None:
        self.client.stop()
        if not self.requested:
            self.requested = True
            LOGGER.warning("已收到停止请求，正在安全恢复，请勿重复按键")
        else:
            LOGGER.warning("%s阶段收到重复停止请求，安全清理仍在继续", self.phase)


def _timeout(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout 必须是整数") from exc
    if not 1 <= result <= 300:
        raise argparse.ArgumentTypeError("timeout 必须在 1-300 秒之间")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IPoE DHCP Simulator")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--restore", action="store_true", help="从默认恢复日志恢复网卡")
    actions.add_argument("--capture-only", nargs="?", const=30, type=int, metavar="秒数")
    actions.add_argument("--list-interfaces", action="store_true")
    parser.add_argument("--mac", "-m", help="机顶盒 MAC")
    parser.add_argument("--interface", help="网卡 GUID、ifIndex、名称或唯一描述")
    for code in OPTION_CODES:
        parser.add_argument(f"--option{code}")
    parser.add_argument("--config", "-c", default=str(DEFAULT_CONFIG), help="JSON 配置文件")
    parser.add_argument("--capture-output", help="抓包输出路径，默认写入 .temp")
    parser.add_argument("--timeout", type=_timeout, default=8, help="Offer/ACK 等待秒数（1-300）")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default=None, help="日志级别")
    return parser


def _run_restore() -> int:
    try:
        if not JOURNAL.exists():
            LOGGER.info("无待恢复状态 journal=%s", JOURNAL)
            return 0
        if not is_admin():
            if sys.platform == "win32":
                LOGGER.error("恢复失败：需要管理员权限 journal=%s", JOURNAL)
            else:
                LOGGER.error(
                    "恢复失败：需要 root/sudo 权限；本程序不会自动提权 journal=%s",
                    JOURNAL,
                )
            return 5
        LOGGER.info("开始手动恢复网卡状态 journal=%s", JOURNAL)
        restore_from_journal(JOURNAL)
    except NetworkStateError:
        raise
    except OSError as exc:
        raise NetworkStateError(f"手动恢复无法安全完成: {exc}") from exc
    LOGGER.info("手动恢复网卡状态并验证成功")
    return 0


def _apply_arguments(config: Config, args: argparse.Namespace) -> None:
    if args.mac:
        config.set(args.mac, "device", "mac")
    if args.interface:
        config.set(args.interface, "device", "interface")
    for code in OPTION_CODES:
        value = getattr(args, f"option{code}")
        if value:
            config.set(value, "dhcp_options", f"option{code}")


def _run_capture(config: Config, args: argparse.Namespace) -> int:
    ensure_runtime(auto_install=True, require_admin=True)
    selector = args.interface or config.get("device", "interface")
    interface = resolve_interface(selector)
    duration = args.capture_only
    output = Path(args.capture_output) if args.capture_output else default_capture_path(ROOT)
    LOGGER.info("抓包开始 interface=%s duration_seconds=%s", interface.display(), duration)
    saved = capture_dhcp(interface, duration, output)
    config.set(interface.pcap_name, "device", "interface")
    config.set(str(saved), "capture", "pcap_file")
    config.set(duration, "capture", "duration")
    config.save()
    LOGGER.info("抓包完成 output=%s", saved)
    return 0


def _restore_transaction(transaction: NetworkTransaction) -> bool:
    try:
        LOGGER.info("开始恢复网卡状态 journal=%s", JOURNAL)
        transaction.restore()
        LOGGER.info("网卡状态恢复并验证成功")
        return True
    except NetworkStateError as exc:
        LOGGER.critical(
            "严重错误：网卡恢复失败 error=%s journal=%s",
            exc,
            JOURNAL,
        )
        return False


def _run_dhcp(config: Config, args: argparse.Namespace) -> int:
    ensure_runtime(auto_install=True, require_admin=True)
    config.validate_for_dhcp()
    interface = resolve_interface(config.get("device", "interface"))
    config.set(interface.pcap_name, "device", "interface")
    config.save()
    options = {
        code: str(config.get("dhcp_options", f"option{code}"))
        for code in OPTION_CODES
        if config.get("dhcp_options", f"option{code}")
    }
    client = DhcpClient(interface, config.get("device", "mac"), options, timeout=args.timeout)
    transaction: NetworkTransaction | None = None
    exit_code = 0
    stop = StopController(client)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, stop.handle)
    try:
        LOGGER.info("DHCP 事务开始 interface=%s", interface.display())
        LOGGER.info("保存网卡快照并准备进入 DHCP 模拟 journal=%s", JOURNAL)
        transaction = NetworkTransaction.begin(interface, JOURNAL)
        lease = client.handshake()
        transaction.configure_lease(
            lease.ip_address,
            lease.subnet_mask,
            lease.gateway,
            lease.dns_servers,
        )
        LOGGER.info(
            "DHCP 租约已应用 ip=%s subnet_mask=%s gateway=%s dns_servers=%s",
            lease.ip_address,
            lease.subnet_mask,
            lease.gateway or "-",
            ",".join(lease.dns_servers) or "-",
        )
        LOGGER.info("进入运行状态，按 Ctrl+C 停止并恢复网卡")
        if config.get("behavior", "auto_renew", default=True):
            client.renew_forever()
        else:
            LOGGER.info("auto_renew=false；租约到期后将自动恢复网卡")
            client.wait_until_expiry()
    except DhcpStopped:
        LOGGER.info("DHCP 交换已按停止请求结束")
    except DhcpError as exc:
        LOGGER.error("DHCP 失败 error=%s", exc)
        exit_code = 4
    finally:
        stop.phase = "DHCP Release"
        try:
            client.release()
        except DhcpError as exc:
            LOGGER.error("DHCP Release 失败 error=%s", exc)
            if exit_code == 0:
                exit_code = 4
        if transaction is not None:
            stop.phase = "网卡恢复与校验"
            if not _restore_transaction(transaction):
                exit_code = 5
        stop.phase = "退出"
        signal.signal(signal.SIGINT, previous_sigint)
        if stop.requested:
            LOGGER.info("安全恢复流程结束，程序正常退出 exit_code=%s", exit_code)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging_ready = False
    try:
        if args.restore:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
            logging_ready = True
            return _run_restore()
        if JOURNAL.exists() and not args.list_interfaces:
            raise NetworkStateError(
                f"存在待恢复 journal，必须先执行 --restore: {JOURNAL}"
            )
        config = Config(args.config)
        configure_logging(
            level_name=args.log_level,
            log_directory=config.log_directory(ROOT),
        )
        logging_ready = True
        if args.list_interfaces:
            for interface in list_interfaces(include_virtual=True):
                print(interface.display())
            return 0
        _apply_arguments(config, args)
        if args.capture_only is not None:
            return _run_capture(config, args)
        return _run_dhcp(config, args)
    except (ConfigError, InterfaceError) as exc:
        if not logging_ready:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
        LOGGER.error("参数错误 error=%s", exc)
        return 2
    except CaptureError as exc:
        if not logging_ready:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
        LOGGER.error("抓包失败 error=%s", exc)
        return 3
    except (NetworkStateError, DependencyError, LoggingError, OSError) as exc:
        if not logging_ready:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
        LOGGER.error("网络错误 error=%s", exc)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
