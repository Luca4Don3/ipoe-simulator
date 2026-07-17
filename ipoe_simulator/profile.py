from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any


OPTION_CODES = (12, 43, 50, 60, 61, 125)

DEFAULT_CONFIG: dict[str, Any] = {
    "device": {"mac": "", "interface": ""},
    "dhcp_options": {f"option{code}": "" for code in OPTION_CODES},
    "capture": {"pcap_file": "", "duration": 30},
    "network": {"subnet_mask": "", "gateway": "", "dns": []},
    "behavior": {"auto_renew": True, "restore_on_exit": True},
}


class ConfigError(ValueError):
    pass


def deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_mac(value: str) -> str:
    compact = value.replace(":", "").replace("-", "").replace(" ", "")
    if len(compact) != 12:
        raise ConfigError("MAC 地址必须包含 12 个十六进制字符")
    try:
        raw = bytes.fromhex(compact)
    except ValueError as exc:
        raise ConfigError("MAC 地址包含非法字符") from exc
    if raw == b"\x00" * 6 or raw == b"\xff" * 6:
        raise ConfigError("MAC 地址不能是全零或广播地址")
    return ":".join(f"{byte:02x}" for byte in raw)


def option_bytes(value: str, code: int) -> bytes:
    if not value:
        return b""
    if value.lower().startswith("0x"):
        try:
            return bytes.fromhex(value[2:])
        except ValueError as exc:
            raise ConfigError(f"Option {code} 的十六进制值无效") from exc
    if code == 50:
        parts = value.split(".")
        if len(parts) != 4:
            raise ConfigError("Option 50 必须是 IPv4 地址或 0x 十六进制值")
        try:
            octets = [int(part, 10) for part in parts]
        except ValueError as exc:
            raise ConfigError("Option 50 包含非数字 IPv4 字段") from exc
        if any(octet < 0 or octet > 255 for octet in octets):
            raise ConfigError("Option 50 的 IPv4 字段超出 0-255")
        return bytes(octets)
    return value.encode("utf-8")


def format_option(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytes):
        if value and all(32 <= byte <= 126 for byte in value):
            return value.decode("ascii")
        return "0x" + value.hex()
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, int) and 0 <= item <= 255 for item in value):
            return "0x" + bytes(value).hex()
    return str(value)


class Config:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).resolve()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            incoming = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件 {self.path}: {exc}") from exc
        if not isinstance(incoming, dict):
            raise ConfigError("配置文件根节点必须是 JSON 对象")
        self.data = deep_merge(DEFAULT_CONFIG, incoming)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = self.path.parent / ".temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.", suffix=".tmp", dir=temp_dir
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def get(self, *keys: str, default: Any = None) -> Any:
        current: Any = self.data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return default if current == "" else current

    def set(self, value: Any, *keys: str) -> None:
        if not keys:
            raise ConfigError("配置键不能为空")
        current = self.data
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                child = {}
                current[key] = child
            current = child
        current[keys[-1]] = value

    def merge_extracted(self, extracted: dict[str, Any], pcap_path: str) -> None:
        if extracted.get("mac"):
            self.set(normalize_mac(extracted["mac"]), "device", "mac")
        for code in OPTION_CODES:
            key = f"option{code}"
            if key in extracted:
                self.set(extracted[key], "dhcp_options", key)
        network = extracted.get("network") or {}
        for key in ("subnet_mask", "gateway", "dns"):
            if key in network and network[key] not in (None, "", []):
                self.set(network[key], "network", key)
        self.set(str(Path(pcap_path).resolve()), "capture", "pcap_file")

    def validate_for_dhcp(self) -> None:
        mac = self.get("device", "mac")
        if not mac:
            raise ConfigError("必须提供机顶盒 MAC 地址")
        self.set(normalize_mac(mac), "device", "mac")
        interface = self.get("device", "interface")
        if not interface:
            raise ConfigError("必须明确指定网络接口")
        if self.get("behavior", "restore_on_exit", default=True) is not True:
            raise ConfigError("restore_on_exit 不允许关闭；网卡恢复是强制安全策略")
        for code in OPTION_CODES:
            value = self.get("dhcp_options", f"option{code}")
            if value:
                option_bytes(str(value), code)
