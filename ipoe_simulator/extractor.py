from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profile import format_option, normalize_mac


class ExtractError(RuntimeError):
    pass


OPTION_NAME_TO_CODE: dict[Any, int] = {
    "hostname": 12,
    "vendor_specific": 43,
    "requested_addr": 50,
    "vendor_class_id": 60,
    "client_id": 61,
    "vendor_class": 125,
    "vendor_identifying_vendor_class": 125,
    12: 12,
    43: 43,
    50: 50,
    60: 60,
    61: 61,
    125: 125,
}

MESSAGE_TYPES = {
    "discover": 1,
    "offer": 2,
    "request": 3,
    "decline": 4,
    "ack": 5,
    "nak": 6,
    "release": 7,
    "inform": 8,
}


@dataclass
class Transaction:
    mac: str
    xid: int
    discover: list[tuple[Any, Any]] = field(default_factory=list)
    request: list[tuple[Any, Any]] = field(default_factory=list)
    offer: list[tuple[Any, Any]] = field(default_factory=list)
    ack: list[tuple[Any, Any]] = field(default_factory=list)
    first_packet: int = 0

    @property
    def score(self) -> int:
        return sum(
            weight
            for present, weight in (
                (bool(self.discover), 1),
                (bool(self.offer), 2),
                (bool(self.request), 2),
                (bool(self.ack), 4),
            )
            if present
        )


def _message_type(options: list[Any]) -> int | None:
    for option in options:
        if not isinstance(option, tuple) or len(option) < 2:
            continue
        if option[0] != "message-type" and option[0] != 53:
            continue
        value = option[1]
        if isinstance(value, str):
            return MESSAGE_TYPES.get(value.lower())
        if isinstance(value, bytes) and len(value) == 1:
            return value[0]
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _client_mac(bootp: Any, ether: Any, message_type: int | None) -> str:
    chaddr = bytes(bootp.chaddr)[:6]
    if chaddr and chaddr != b"\x00" * 6:
        return normalize_mac(chaddr.hex())
    if message_type in (1, 3) and ether is not None:
        return normalize_mac(str(ether.src))
    raise ExtractError("DHCP 报文缺少有效客户端 MAC")


def _option_dict(options: list[tuple[Any, Any]]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for option in options:
        if isinstance(option, tuple) and len(option) >= 2 and option[0] not in result:
            result[option[0]] = option[1]
    return result

def _ipv4(value: Any) -> str:
    if isinstance(value, int):
        try:
            return str(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError as exc:
            raise ExtractError(f"无效 IPv4 整数: {value}") from exc
    if isinstance(value, str):
        return value
    if isinstance(value, bytes) and len(value) >= 4:
        return ".".join(str(part) for part in value[:4])
    if isinstance(value, (list, tuple)) and value:
        return _ipv4(value[0])
    return str(value)


def _select_transaction(
    transactions: list[Transaction], requested_mac: str | None
) -> Transaction:
    candidates = transactions
    if requested_mac:
        wanted = normalize_mac(requested_mac)
        candidates = [transaction for transaction in candidates if transaction.mac == wanted]
        if not candidates:
            raise ExtractError(f"抓包中没有找到 MAC {wanted} 的 DHCP 会话")
    candidates = [transaction for transaction in candidates if transaction.discover or transaction.request]
    if not candidates:
        raise ExtractError("抓包中没有找到客户端 DHCP Discover/Request")
    best_score = max(transaction.score for transaction in candidates)
    best = [transaction for transaction in candidates if transaction.score == best_score]
    distinct_macs = {transaction.mac for transaction in best}
    if len(distinct_macs) > 1 and not requested_mac:
        detail = ", ".join(sorted(distinct_macs))
        raise ExtractError(f"抓包包含多个同等完整的客户端会话，请使用 --stb-mac 选择: {detail}")
    best.sort(key=lambda transaction: transaction.first_packet)
    return best[-1]


def extract_profile(pcap_path: str | Path, requested_mac: str | None = None) -> dict[str, Any]:
    path = Path(pcap_path).resolve()
    if not path.is_file():
        raise ExtractError(f"抓包文件不存在: {path}")
    try:
        from scapy.all import BOOTP, DHCP, Ether, rdpcap
    except ImportError as exc:
        raise ExtractError("需要 Scapy: python -m pip install -r requirements.txt") from exc
    try:
        packets = rdpcap(str(path))
    except Exception as exc:
        raise ExtractError(f"无法读取抓包文件 {path}: {exc}") from exc

    by_key: dict[tuple[str, int], Transaction] = {}
    for number, packet in enumerate(packets):
        if not packet.haslayer(DHCP) or not packet.haslayer(BOOTP):
            continue
        options = list(packet[DHCP].options)
        message_type = _message_type(options)
        if message_type not in (1, 2, 3, 5):
            continue
        bootp = packet[BOOTP]
        ether = packet[Ether] if packet.haslayer(Ether) else None
        try:
            mac = _client_mac(bootp, ether, message_type)
        except ExtractError:
            continue
        key = (mac, int(bootp.xid))
        transaction = by_key.setdefault(key, Transaction(mac, int(bootp.xid), first_packet=number))
        tuples = [option for option in options if isinstance(option, tuple)]
        if message_type == 1 and not transaction.discover:
            transaction.discover = tuples
        elif message_type == 2 and not transaction.offer:
            transaction.offer = tuples
        elif message_type == 3 and not transaction.request:
            transaction.request = tuples
        elif message_type == 5 and not transaction.ack:
            transaction.ack = tuples

    transaction = _select_transaction(list(by_key.values()), requested_mac)
    result: dict[str, Any] = {"mac": transaction.mac}
    for options in (transaction.discover, transaction.request):
        for key, value in options:
            code = OPTION_NAME_TO_CODE.get(key)
            if code is None:
                continue
            result.setdefault(f"option{code}", format_option(value))

    ack_options = _option_dict(transaction.ack)
    network: dict[str, Any] = {}
    if "subnet_mask" in ack_options:
        network["subnet_mask"] = _ipv4(ack_options["subnet_mask"])
    if "router" in ack_options:
        network["gateway"] = _ipv4(ack_options["router"])
    if "domain_name_server" in ack_options:
        dns = ack_options["domain_name_server"]
        if not isinstance(dns, (list, tuple)):
            dns = [dns]
        network["dns"] = [str(item) for item in dns]
    if network:
        result["network"] = network
    result["transaction"] = {
        "xid": f"0x{transaction.xid:08x}",
        "has_discover": bool(transaction.discover),
        "has_offer": bool(transaction.offer),
        "has_request": bool(transaction.request),
        "has_ack": bool(transaction.ack),
    }
    return result
