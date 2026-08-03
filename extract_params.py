#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ipoe_simulator.app_logging import LOG_LEVELS, configure_logging, get_logger
from ipoe_simulator.extractor import ExtractError, extract_profile
from ipoe_simulator.dependencies import ensure_scapy
from ipoe_simulator.profile import Config, ConfigError, OPTION_CODES


ROOT = Path(__file__).resolve().parent
LOGGER = get_logger("extract")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从机顶盒 DHCP 抓包提取 IPoE 参数")
    parser.add_argument("pcap", help="PCAP 或 PCAPNG 文件")
    parser.add_argument("--json", dest="json_path", help="合并写入 JSON 配置")
    parser.add_argument("--stb-mac", help="多客户端抓包中指定机顶盒 MAC")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default=None, help="日志级别")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging_ready = False
    try:
        config: Config | None = None
        if args.json_path:
            config_path = Path(args.json_path)
            if not config_path.is_absolute():
                config_path = ROOT / config_path
            config = Config(config_path)
        configure_logging(
            level_name=args.log_level,
            log_directory=config.log_directory(ROOT) if config else ROOT,
        )
        logging_ready = True
        ensure_scapy(auto_install=True)
        result = extract_profile(args.pcap, args.stb_mac)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if "unicast_routes" not in (result.get("network") or {}):
            LOGGER.warning("抓包中未检测到明文 ChannelList；保留配置中的现有单播路由")
        if config is not None:
            config.merge_extracted(result, args.pcap)
            config.save()
            LOGGER.info("配置已保存 path=%s", config.path)
        elif (result.get("network") or {}).get("unicast_routes"):
            LOGGER.warning("已提取单播路由，但未使用 --json；这些路由不会自动进入拨号配置")

        command = [sys.executable, str(ROOT / "ipoedhcp.py"), "--mac", result["mac"]]
        if config is not None:
            command.extend(("--config", str(config.path)))
        for code in OPTION_CODES:
            key = f"option{code}"
            if result.get(key):
                command.extend((f"--{key}", str(result[key])))
        LOGGER.info("提取完成 pcap=%s", args.pcap)
        LOGGER.info("直接运行命令: %s", subprocess.list2cmdline(command))
        return 0
    except (ExtractError, ConfigError, OSError) as exc:
        if not logging_ready:
            configure_logging(level_name=args.log_level, log_directory=ROOT)
        LOGGER.error("参数提取失败 error=%s", exc)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
