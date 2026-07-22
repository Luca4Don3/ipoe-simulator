#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from ipoe_simulator.app_logging import LOG_LEVELS, configure_logging, get_logger
from ipoe_simulator.interfaces import (
    InterfaceError,
    InterfaceInfo,
    list_interfaces,
    resolve_interface,
    windows_interface_states,
)
from ipoe_simulator.profile import (
    Config,
    ConfigError,
    DEFAULT_CONFIG,
    OPTION_CODES,
    normalize_mac,
    option_bytes,
)
from ipoe_simulator.platform_network import default_journal_path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "ipoedhcp_config.json"
LOGGER = get_logger("coordinator")
JOURNAL = default_journal_path(ROOT)


class CoordinatorError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 6):
        super().__init__(message)
        self.exit_code = exit_code


class CoordinatorStop(RuntimeError):
    """父进程收到停止请求后，在子进程安全结束时退出整个程序。"""


def run_script(
    script: str,
    args: list[str],
    description: str,
    *,
    propagate_exit_code: bool = False,
) -> None:
    path = ROOT / script
    if not path.exists():
        raise CoordinatorError(f"脚本不存在: {path}")
    LOGGER.info("子流程开始 script=%s description=%s", script, description)
    process = subprocess.Popen([sys.executable, "-u", str(path), *args], cwd=ROOT)
    stop_requested = False
    while True:
        try:
            return_code = process.wait()
            break
        except KeyboardInterrupt:
            if not stop_requested:
                stop_requested = True
                LOGGER.warning("已收到停止请求，正在等待子流程安全恢复，请勿重复按键")
            else:
                LOGGER.warning("子流程清理期间收到重复停止请求，继续等待安全退出")
    if return_code != 0:
        LOGGER.error("子流程失败 script=%s returncode=%s", script, return_code)
        raise CoordinatorError(
            f"{script} 失败，退出码 {return_code}",
            exit_code=return_code if propagate_exit_code else 6,
        )
    LOGGER.info("子流程完成 script=%s", script)
    if stop_requested:
        raise CoordinatorStop()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IPoE DHCP 统筹管理器")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--capture", nargs="?", const=True, metavar="秒数")
    p.add_argument("--extract", nargs="?", const=True, metavar="PCAP")
    p.add_argument("--dhcp", action="store_true")
    p.add_argument("--mac", "-m")
    p.add_argument("--interface")
    for code in OPTION_CODES:
        p.add_argument(f"--option{code}")
    p.add_argument("--duration", type=int)
    p.add_argument("--pcap")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    p.add_argument("--show", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--interactive", "-i", action="store_true")
    p.add_argument("--log-level", choices=LOG_LEVELS, default=None, help="日志级别")
    return p


def validate_actions(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.restore:
        return
    conflicts = (
        args.capture is not None,
        args.extract is not None,
        args.dhcp,
        args.show,
        args.reset,
        args.interactive,
    )
    if any(conflicts):
        p.error("--restore 不能与抓包、提取、拨号、查看、重置或交互主操作组合")


def apply_cli(config: Config, args: argparse.Namespace) -> None:
    if args.mac:
        config.set(args.mac, "device", "mac")
    if args.interface:
        config.set(args.interface, "device", "interface")
    for code in OPTION_CODES:
        value = getattr(args, f"option{code}")
        if value:
            config.set(value, "dhcp_options", f"option{code}")
    if args.pcap:
        config.set(str(Path(args.pcap).resolve()), "capture", "pcap_file")
    if args.duration:
        config.set(args.duration, "capture", "duration")


def config_args(config: Config) -> list[str]:
    return ["--config", str(config.path)]


def do_capture(
    config: Config,
    duration: int | None,
    *,
    fresh_output: bool = False,
) -> str:
    original = json.loads(json.dumps(config.data))
    seconds = duration or int(config.get("capture", "duration", default=30))
    interface = config.get("device", "interface")
    output = None if fresh_output else config.get("capture", "pcap_file")
    args = ["--capture-only", str(seconds), *config_args(config)]
    if interface:
        args.extend(("--interface", str(interface)))
    if output:
        args.extend(("--capture-output", str(output)))
    run_script("ipoedhcp.py", args, f"开始抓包: {seconds}s")
    config.load()
    captured = str(config.get("capture", "pcap_file", default=""))
    if not captured:
        raise CoordinatorError("抓包完成但未返回输出路径")
    if fresh_output:
        config.data = original
        config.save()
    return captured


def do_extract(config: Config, pcap: str | None) -> None:
    source = pcap or config.get("capture", "pcap_file")
    if not source:
        raise CoordinatorError("未指定 PCAP/PCAPNG 文件")
    run_script(
        "extract_params.py",
        [str(source), "--json", str(config.path)],
        f"提取参数: {source}",
    )
    config.load()


def do_dhcp(config: Config) -> None:
    config.save()
    run_script("ipoedhcp.py", config_args(config), "开始 IPoE DHCP 模拟")


def do_restore(log_level: str | None = None) -> None:
    args = ["--restore"]
    if log_level:
        args.extend(("--log-level", log_level))
    run_script(
        "ipoedhcp.py",
        args,
        "从默认恢复日志恢复网卡",
        propagate_exit_code=True,
    )


def select_interface(config: Config) -> InterfaceInfo | None:
    selector = config.get("device", "interface")
    current: InterfaceInfo | None = None
    if selector:
        try:
            current = resolve_interface(selector)
        except InterfaceError as exc:
            print(f"当前网卡不可用: {exc}")

    interfaces = list_interfaces(include_virtual=True)
    states = windows_interface_states()
    risky = False
    if states:
        safe_interfaces = [item for item in interfaces if states.get(item.index) and states[item.index].safe]
        if safe_interfaces:
            interfaces = safe_interfaces
        else:
            risky = True
    if not interfaces:
        interfaces = list_interfaces(include_virtual=True)
    if not interfaces:
        raise CoordinatorError("未找到可用网络接口")
    print("\n可用网络接口:")
    for interface in interfaces:
        state = states.get(interface.index)
        suffix = f" | 风险: {state.reason}" if state and not state.safe else ""
        print(f"  {interface.display()}{suffix}")
    while True:
        default = f" [Enter 使用当前: {current.index}]" if current else ""
        selector = input(
            f"选择网卡 ifIndex/名称{default} [0 返回]: "
        ).strip()
        if selector == "0":
            return None
        if not selector and current is not None:
            interface = current
            state = states.get(interface.index)
            if state and not state.safe:
                confirmation = input(
                    f"该接口存在风险（{state.reason}），输入 USE 继续，其他输入取消: "
                ).strip()
                if confirmation != "USE":
                    print("已取消风险接口选择。")
                    return None
            config.set(interface.pcap_name, "device", "interface")
            config.save()
            print(f"已选择网卡: {interface.display()}")
            return interface
        if not selector:
            print("必须选择网络接口；输入 0 返回主菜单。")
            continue
        try:
            interface = resolve_interface(selector)
        except InterfaceError as exc:
            print(f"网卡选择无效: {exc}")
            continue
        state = states.get(interface.index)
        if state and not state.safe:
            confirmation = input(
                f"该接口存在风险（{state.reason}），输入 USE 继续，其他输入取消: "
            ).strip()
            if confirmation != "USE":
                print("已取消风险接口选择。")
                return None
        elif risky:
            raise CoordinatorError("所选接口状态已失效，请刷新后重试")
        config.set(interface.pcap_name, "device", "interface")
        config.save()
        print(f"已选择网卡: {interface.display()}")
        return interface


def after_extract(config: Config) -> int | None:
    while True:
        print("\n参数提取完成，下一步:")
        print("1. 直接拨号  2. 返回主菜单  0. 退出")
        choice = input("选择: ").strip()
        if choice == "1":
            if select_interface(config) is not None:
                do_dhcp(config)
            return None
        if choice == "2":
            return None
        if choice == "0":
            return 0
        print("无效选择，请输入 0、1 或 2。")


def clear_config(config: Config) -> bool:
    print(
        "将恢复完整默认 JSON 配置；不会删除 PCAP、日志、runtime 或恢复 journal。"
    )
    if input("输入 CLEAR 确认清空，其他输入取消: ").strip() != "CLEAR":
        print("已取消清空配置。")
        return False
    config.data = json.loads(json.dumps(DEFAULT_CONFIG))
    config.save()
    print("配置已清空。")
    return True


def _manual_value(label: str, current: object, validator):
    shown = current if current not in (None, "", []) else "(未设置)"
    while True:
        value = input(f"{label} [{shown}]（Enter 保留，- 清空）: ").strip()
        if not value:
            return current
        if value == "-":
            return [] if isinstance(current, list) else ""
        try:
            return validator(value)
        except (ConfigError, ValueError) as exc:
            print(f"输入无效: {exc}")


def edit_config(config: Config) -> bool:
    draft = json.loads(json.dumps(config.data))
    device = draft["device"]
    options = draft["dhcp_options"]
    network = draft["network"]
    capture = draft["capture"]

    device["mac"] = _manual_value("MAC", device.get("mac", ""), normalize_mac)
    for code in OPTION_CODES:
        key = f"option{code}"

        def validate_option(value: str, selected: int = code) -> str:
            option_bytes(value, selected)
            return value

        options[key] = _manual_value(
            f"Option {code}", options.get(key, ""), validate_option
        )

    def ipv4(value: str) -> str:
        return str(ipaddress.IPv4Address(value))

    def subnet_mask(value: str) -> str:
        network_value = ipaddress.IPv4Network(f"0.0.0.0/{value}")
        return str(network_value.netmask)

    def dns_servers(value: str) -> list[str]:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("DNS 至少包含一个 IPv4 地址，清空请使用 -")
        return [str(ipaddress.IPv4Address(item)) for item in items]

    def duration(value: str) -> int:
        seconds = int(value)
        if not 1 <= seconds <= 3600:
            raise ValueError("抓包时长必须在 1–3600 秒之间")
        return seconds

    network["subnet_mask"] = _manual_value(
        "子网掩码", network.get("subnet_mask", ""), subnet_mask
    )
    network["gateway"] = _manual_value("网关", network.get("gateway", ""), ipv4)
    network["dns"] = _manual_value(
        "DNS（多个用逗号分隔）", network.get("dns", []), dns_servers
    )
    capture["duration"] = _manual_value(
        "抓包时长秒数", capture.get("duration", 30), duration
    )

    print("\n待保存配置:")
    print(json.dumps(draft, indent=2, ensure_ascii=False))
    if input("输入 SAVE 保存，其他输入取消: ").strip() != "SAVE":
        print("已取消手动填写，原配置未改变。")
        return False
    config.data = draft
    config.save()
    print("手动配置已保存。")
    return True


def interactive(config: Config) -> int:
    while True:
        print("\nIPoE DHCP 统筹管理器")
        print(f"MAC: {config.get('device', 'mac', default='(未设置)')}")
        print(
            f"网卡: {config.get('device', 'interface', default='(未选择)')}"
        )
        restricted = JOURNAL.exists()
        if restricted:
            print(f"警告：存在待恢复 journal，仅允许查看配置、恢复网卡或退出: {JOURNAL}")
            print("5. 查看配置  6. 恢复网卡  0. 退出")
        else:
            print(
                "1. 抓包  2. 提取参数  3. 直接拨号  "
                "5. 查看配置  6. 恢复网卡  7. 清空配置  8. 手动填写  0. 退出"
            )
        try:
            choice = input("选择: ").strip()
            if choice == "":
                continue
            if restricted and choice not in {"0", "5", "6"}:
                print("操作被拒绝：必须先成功恢复网卡并删除 journal。")
                continue
            if choice == "1":
                seconds = input("抓包时长秒数 [30]: ").strip() or "30"
                config.set(int(seconds), "capture", "duration")
                if select_interface(config) is None:
                    continue
                config.save()
                captured = do_capture(
                    config,
                    int(seconds),
                    fresh_output=True,
                )
                print(f"抓包已保存，正式配置未改变: {captured}")
            elif choice == "2":
                source = input("PCAP/PCAPNG 路径: ").strip()
                do_extract(config, source)
                result = after_extract(config)
                if result is not None:
                    return result
            elif choice == "3":
                if select_interface(config) is not None:
                    do_dhcp(config)
            elif choice == "5":
                print(json.dumps(config.data, indent=2, ensure_ascii=False))
            elif choice == "6":
                do_restore()
            elif choice == "7":
                clear_config(config)
            elif choice == "8":
                edit_config(config)
            elif choice == "0":
                return 0
            else:
                print("无效选择。")
        except (KeyboardInterrupt, EOFError, CoordinatorStop):
            print("\n已取消，正常退出。")
            LOGGER.info("交互流程由用户取消")
            return 0
        except (ValueError, CoordinatorError, ConfigError, InterfaceError) as exc:
            LOGGER.error("交互流程失败 error=%s", exc)


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    validate_actions(argument_parser, args)
    logging_ready = False
    if args.log_level:
        os.environ["IPOE_LOG_LEVEL"] = args.log_level
    try:
        if args.restore:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
            logging_ready = True
            do_restore(args.log_level)
            return 0
        if JOURNAL.exists() and not args.show:
            raise CoordinatorError(
                f"存在待恢复 journal，必须先执行 --restore: {JOURNAL}",
                exit_code=5,
            )
        config = Config(args.config)
        configure_logging(
            level_name=args.log_level,
            log_directory=config.log_directory(ROOT),
        )
        logging_ready = True
        if args.reset:
            config.data = json.loads(json.dumps(DEFAULT_CONFIG))
            config.save()
            print(json.dumps(config.data, indent=2, ensure_ascii=False))
            return 0
        apply_cli(config, args)
        if args.show:
            print(json.dumps(config.data, indent=2, ensure_ascii=False))
            return 0
        if args.interactive or not any((args.capture is not None, args.extract is not None, args.dhcp)):
            return interactive(config)
        config.save()
        if args.capture is not None:
            duration = int(args.capture) if str(args.capture).isdigit() else args.duration
            do_capture(config, duration)
        if args.extract is not None:
            source = args.extract if isinstance(args.extract, str) else args.pcap
            do_extract(config, source)
        if args.dhcp:
            do_dhcp(config)
        return 0
    except CoordinatorStop:
        LOGGER.info("子流程已安全停止，统筹器正常退出")
        return 0
    except (CoordinatorError, ConfigError, ValueError) as exc:
        if not logging_ready:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
        LOGGER.error("流程失败 error=%s", exc)
        return exc.exit_code if isinstance(exc, CoordinatorError) else 6


if __name__ == "__main__":
    raise SystemExit(main())
