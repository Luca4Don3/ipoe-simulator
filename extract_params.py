#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ipoe_simulator.extractor import ExtractError, extract_profile
from ipoe_simulator.dependencies import ensure_scapy
from ipoe_simulator.profile import Config, ConfigError, OPTION_CODES


ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从机顶盒 DHCP 抓包提取 IPoE 参数")
    parser.add_argument("pcap", help="PCAP 或 PCAPNG 文件")
    parser.add_argument("--json", dest="json_path", help="合并写入 JSON 配置")
    parser.add_argument("--stb-mac", help="多客户端抓包中指定机顶盒 MAC")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        ensure_scapy(auto_install=True)
        result = extract_profile(args.pcap, args.stb_mac)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.json_path:
            config_path = Path(args.json_path)
            if not config_path.is_absolute():
                config_path = ROOT / config_path
            config = Config(config_path)
            config.merge_extracted(result, args.pcap)
            config.save()
            print(f"配置已保存: {config.path}")

        command = [sys.executable, str(ROOT / "ipoedhcp.py"), "--mac", result["mac"]]
        for code in OPTION_CODES:
            key = f"option{code}"
            if result.get(key):
                command.extend((f"--{key}", str(result[key])))
        print("直接运行命令:")
        print("  " + subprocess.list2cmdline(command))
        return 0
    except (ExtractError, ConfigError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
