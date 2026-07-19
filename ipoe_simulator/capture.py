from __future__ import annotations

from datetime import datetime
import secrets
from pathlib import Path

from .interfaces import InterfaceInfo


class CaptureError(RuntimeError):
    pass


def default_capture_path(project_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + secrets.token_hex(4)
    return project_root / ".temp" / f"dhcp-{stamp}.pcap"


def capture_dhcp(interface: InterfaceInfo, duration: int, output_path: str | Path) -> Path:
    if duration <= 0 or duration > 3600:
        raise CaptureError("抓包时长必须在 1-3600 秒之间")
    try:
        from scapy.all import sniff, wrpcap
    except ImportError as exc:
        raise CaptureError("需要 Scapy: python -m pip install -r requirements.txt") from exc

    output = Path(output_path).resolve()
    if output.exists():
        raise CaptureError(f"拒绝覆盖已有抓包文件: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        packets = sniff(
            iface=interface.pcap_name,
            filter="udp and (port 67 or port 68)",
            timeout=duration,
            store=True,
        )
    except Exception as exc:
        raise CaptureError(f"在网卡 {interface.name} 上抓包失败: {exc}") from exc
    if not packets:
        raise CaptureError("抓包期间未捕获到 DHCP 报文")
    try:
        wrpcap(str(output), packets)
    except Exception as exc:
        raise CaptureError(f"保存抓包文件失败 {output}: {exc}") from exc
    return output
