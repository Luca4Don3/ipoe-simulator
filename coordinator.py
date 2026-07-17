#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from ipoe_simulator.app_logging import LOG_LEVELS, configure_logging, get_logger
from ipoe_simulator.profile import Config, ConfigError, DEFAULT_CONFIG, OPTION_CODES


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "ipoedhcp_config.json"
LOGGER = get_logger("coordinator")


class CoordinatorError(RuntimeError):
    pass


def run_script(script: str, args: list[str], description: str) -> None:
    path = ROOT / script
    if not path.exists():
        raise CoordinatorError(f"脚本不存在: {path}")
    LOGGER.info("子流程开始 script=%s description=%s", script, description)
    result = subprocess.run([sys.executable, "-u", str(path), *args], cwd=ROOT, check=False)
    if result.returncode != 0:
        LOGGER.error("子流程失败 script=%s returncode=%s", script, result.returncode)
        raise CoordinatorError(f"{script} 失败，退出码 {result.returncode}")
    LOGGER.info("子流程完成 script=%s", script)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IPoE DHCP 统筹管理器")
    p.add_argument("--capture", nargs="?", const=True, metavar="秒数")
    p.add_argument("--extract", nargs="?", const=True, metavar="PCAP")
    p.add_argument("--dhcp", action="store_true")
    p.add_argument("--all", action="store_true")
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


def do_capture(config: Config, duration: int | None) -> None:
    seconds = duration or int(config.get("capture", "duration", default=30))
    interface = config.get("device", "interface")
    output = config.get("capture", "pcap_file")
    args = ["--capture-only", str(seconds), *config_args(config)]
    if interface:
        args.extend(("--interface", str(interface)))
    if output:
        args.extend(("--capture-output", str(output)))
    run_script("ipoedhcp.py", args, f"开始抓包: {seconds}s")


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


def interactive(config: Config) -> int:
    while True:
        print("\nIPoE DHCP 统筹管理器")
        print(f"MAC: {config.get('device', 'mac', default='(未设置)')}")
        print("1. 导入/抓包  2. 提取参数  3. 直接拨号  4. 完整流程  5. 查看配置  0. 退出")
        choice = input("选择: ").strip()
        try:
            if choice == "1":
                seconds = input("抓包时长秒数 [30]: ").strip() or "30"
                config.set(int(seconds), "capture", "duration")
                interface = input("网卡 GUID/ifIndex/名称: ").strip()
                if interface:
                    config.set(interface, "device", "interface")
                config.save()
                do_capture(config, int(seconds))
            elif choice == "2":
                source = input("PCAP/PCAPNG 路径: ").strip()
                do_extract(config, source)
            elif choice == "3":
                do_dhcp(config)
            elif choice == "4":
                config.save()
                do_capture(config, int(config.get("capture", "duration", default=30)))
                do_extract(config, config.get("capture", "pcap_file"))
                do_dhcp(config)
            elif choice == "5":
                print(json.dumps(config.data, indent=2, ensure_ascii=False))
            elif choice == "0":
                return 0
        except (ValueError, CoordinatorError, ConfigError) as exc:
            LOGGER.error("交互流程失败 error=%s", exc)


def main() -> int:
    args = parser().parse_args()
    if args.log_level:
        os.environ["IPOE_LOG_LEVEL"] = args.log_level
    configure_logging(level_name=args.log_level)
    try:
        config = Config(args.config)
        if args.reset:
            config.data = json.loads(json.dumps(DEFAULT_CONFIG))
            config.save()
            print(json.dumps(config.data, indent=2, ensure_ascii=False))
            return 0
        apply_cli(config, args)
        if args.show:
            print(json.dumps(config.data, indent=2, ensure_ascii=False))
            return 0
        if args.interactive or not any((args.capture is not None, args.extract is not None, args.dhcp, args.all)):
            return interactive(config)
        config.save()
        if args.all:
            do_capture(config, args.duration)
            do_extract(config, config.get("capture", "pcap_file"))
            do_dhcp(config)
        else:
            if args.capture is not None:
                duration = int(args.capture) if str(args.capture).isdigit() else args.duration
                do_capture(config, duration)
            if args.extract is not None:
                source = args.extract if isinstance(args.extract, str) else args.pcap
                do_extract(config, source)
            if args.dhcp:
                do_dhcp(config)
        return 0
    except (CoordinatorError, ConfigError, ValueError) as exc:
        LOGGER.error("流程失败 error=%s", exc)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
