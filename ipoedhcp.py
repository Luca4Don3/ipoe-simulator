#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ipoe_simulator.capture import CaptureError, capture_dhcp, default_capture_path
from ipoe_simulator.dhcp_client import DhcpClient, DhcpError
from ipoe_simulator.dependencies import DependencyError, ensure_runtime
from ipoe_simulator.interfaces import InterfaceError, list_interfaces, resolve_interface
from ipoe_simulator.profile import Config, ConfigError, OPTION_CODES
from ipoe_simulator.platform_network import (
    NetworkStateError,
    NetworkTransaction,
    default_journal_path,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "ipoedhcp_config.json"
JOURNAL = default_journal_path(ROOT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IPoE DHCP Simulator")
    parser.add_argument("--mac", "-m", help="机顶盒 MAC")
    parser.add_argument("--interface", help="网卡 GUID、ifIndex、名称或唯一描述")
    for code in OPTION_CODES:
        parser.add_argument(f"--option{code}")
    parser.add_argument("--config", "-c", default=str(DEFAULT_CONFIG), help="JSON 配置文件")
    parser.add_argument("--capture-only", nargs="?", const=30, type=int, metavar="秒数")
    parser.add_argument("--capture-output", help="抓包输出路径，默认写入 .temp")
    parser.add_argument("--list-interfaces", action="store_true")
    parser.add_argument("--timeout", type=int, default=8, help="Offer/ACK 等待秒数")
    return parser


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
    print(f"网卡: {interface.display()}")
    print(f"开始抓取 DHCP，时长 {duration}s")
    saved = capture_dhcp(interface, duration, output)
    config.set(interface.pcap_name, "device", "interface")
    config.set(str(saved), "capture", "pcap_file")
    config.set(duration, "capture", "duration")
    config.save()
    print(f"抓包已保存: {saved}")
    return 0


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
    try:
        print(f"目标网卡: {interface.display()}")
        print("保存网卡状态并进入 DHCP 模拟...")
        transaction = NetworkTransaction.begin(interface, JOURNAL)
        lease = client.handshake()
        transaction.configure_lease(
            lease.ip_address,
            lease.subnet_mask,
            lease.gateway,
            lease.dns_servers,
        )
        print(f"已连接: {lease.ip_address}，按 Ctrl+C Stop 并恢复网卡")
        if config.get("behavior", "auto_renew", default=True):
            client.renew_forever()
        else:
            while True:
                client.stop_event.wait(3600)
    except KeyboardInterrupt:
        print("收到 Stop 请求")
        client.stop()
    except DhcpError as exc:
        print(f"DHCP 失败: {exc}", file=sys.stderr)
        exit_code = 4
    finally:
        try:
            client.release()
        except DhcpError as exc:
            print(str(exc), file=sys.stderr)
            if exit_code == 0:
                exit_code = 4
        if transaction is not None:
            try:
                print("正在恢复网卡完整状态...")
                transaction.restore()
                print("网卡状态已恢复并验证")
            except NetworkStateError as exc:
                print(f"严重错误: 网卡恢复失败: {exc}", file=sys.stderr)
                print(f"恢复日志保留于: {JOURNAL}", file=sys.stderr)
                exit_code = 5
    return exit_code


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.list_interfaces:
            for interface in list_interfaces(include_virtual=True):
                print(interface.display())
            return 0
        config = Config(args.config)
        _apply_arguments(config, args)
        if args.capture_only is not None:
            return _run_capture(config, args)
        return _run_dhcp(config, args)
    except (ConfigError, InterfaceError) as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2
    except CaptureError as exc:
        print(f"抓包失败: {exc}", file=sys.stderr)
        return 3
    except (NetworkStateError, DependencyError) as exc:
        print(f"网络错误: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
