from __future__ import annotations

import ipaddress
import re
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .profile import ConfigError, format_option, normalize_mac


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

MAX_TCP_STREAM_BYTES = 4 * 1024 * 1024
MAX_TCP_SEGMENTS = 4096
MAX_TCP_STREAMS = 64
MAX_UNICAST_ROUTES = 256
CHANNEL_MARKER = b"Authentication.CTCSetConfig"


@dataclass
class TcpStream:
    segments: list[tuple[int, bytes]] = field(default_factory=list)
    total_bytes: int = 0

    def add(self, sequence: int, payload: bytes) -> None:
        if not payload:
            return
        if (sequence, payload) in self.segments:
            return
        if len(self.segments) >= MAX_TCP_SEGMENTS:
            raise ExtractError("ChannelList TCP 重组超出报文段数量限制")
        if self.total_bytes + len(payload) > MAX_TCP_STREAM_BYTES:
            raise ExtractError("ChannelList TCP 重组超出 4 MiB 资源限制")
        for old_sequence, old_data in self.segments:
            start = max(sequence, old_sequence)
            end = min(sequence + len(payload), old_sequence + len(old_data))
            if start < end:
                left = payload[start - sequence : end - sequence]
                right = old_data[start - old_sequence : end - old_sequence]
                if left != right:
                    raise ExtractError("ChannelList TCP 重组检测到冲突重叠")
        self.segments.append((sequence, payload))
        self.total_bytes += len(payload)

    def assemble(self) -> bytes:
        if not self.segments:
            return b""
        ordered = sorted(self.segments)
        output = bytearray(ordered[0][1])
        end = ordered[0][0] + len(ordered[0][1])
        for sequence, data in ordered[1:]:
            if sequence > end:
                partial = b"".join(chunk for _, chunk in ordered)
                if CHANNEL_MARKER.lower() in partial.lower():
                    raise ExtractError("ChannelList TCP 重组缺少报文段")
                return b""
            overlap = max(0, end - sequence)
            if overlap < len(data):
                output.extend(data[overlap:])
                end += len(data) - overlap
        return bytes(output)


def _decode_chunked(body: bytes) -> tuple[bytes, int]:
    output = bytearray()
    offset = 0
    while True:
        line_end = body.find(b"\r\n", offset)
        if line_end < 0:
            raise ExtractError("ChannelList chunked HTTP 响应不完整")
        size_text = body[offset:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as exc:
            raise ExtractError("ChannelList chunked HTTP 块长度无效") from exc
        offset = line_end + 2
        if size == 0:
            trailer_end = body.find(b"\r\n\r\n", offset)
            if trailer_end >= 0:
                return bytes(output), trailer_end + 4
            if body[offset : offset + 2] == b"\r\n":
                return bytes(output), offset + 2
            raise ExtractError("ChannelList chunked HTTP 尾部不完整")
        if offset + size + 2 > len(body) or body[offset + size : offset + size + 2] != b"\r\n":
            raise ExtractError("ChannelList chunked HTTP 数据不完整")
        output.extend(body[offset : offset + size])
        if len(output) > MAX_TCP_STREAM_BYTES:
            raise ExtractError("ChannelList HTTP 正文超出 4 MiB 资源限制")
        offset += size + 2


def _http_bodies(stream: bytes) -> list[bytes]:
    bodies: list[bytes] = []
    offset = 0
    while True:
        start = stream.find(b"HTTP/", offset)
        if start < 0:
            break
        header_end = stream.find(b"\r\n\r\n", start)
        if header_end < 0:
            if CHANNEL_MARKER.lower() in stream[start:].lower():
                raise ExtractError("ChannelList HTTP 响应头不完整")
            break
        header_lines = stream[start:header_end].split(b"\r\n")
        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            if b":" in line:
                key, value = line.split(b":", 1)
                headers[key.decode("ascii", "ignore").lower()] = value.decode("latin-1").strip()
        body_start = header_end + 4
        transfer = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer:
            body, consumed = _decode_chunked(stream[body_start:])
            offset = body_start + consumed
        elif "content-length" in headers:
            try:
                length = int(headers["content-length"])
            except ValueError as exc:
                raise ExtractError("ChannelList HTTP Content-Length 无效") from exc
            if length < 0 or length > MAX_TCP_STREAM_BYTES:
                raise ExtractError("ChannelList HTTP Content-Length 超出限制")
            if body_start + length > len(stream):
                if CHANNEL_MARKER.lower() in stream[body_start:].lower():
                    raise ExtractError("ChannelList HTTP 正文缺少报文段")
                break
            body = stream[body_start : body_start + length]
            offset = body_start + length
        else:
            next_response = stream.find(b"HTTP/", body_start)
            body = stream[body_start:] if next_response < 0 else stream[body_start:next_response]
            offset = len(stream) if next_response < 0 else next_response
        if "gzip" in headers.get("content-encoding", "").lower():
            try:
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = decompressor.decompress(body, MAX_TCP_STREAM_BYTES + 1)
                if len(body) > MAX_TCP_STREAM_BYTES:
                    raise ExtractError("ChannelList gzip HTTP 正文超出 4 MiB 资源限制")
                if not decompressor.eof:
                    raise ExtractError("ChannelList gzip HTTP 响应不完整或损坏")
            except zlib.error as exc:
                raise ExtractError("ChannelList gzip HTTP 响应不完整或损坏") from exc
        bodies.append(body)
    return bodies


def _unescape_js_string(value: bytes, quote: int) -> str:
    output = bytearray()
    index = 0
    while index < len(value):
        byte = value[index]
        if byte != 0x5C:
            output.append(byte)
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise ExtractError("ChannelList JavaScript 字符串转义不完整")
        escaped = value[index]
        mapping = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}
        if escaped in mapping:
            output.extend(mapping[escaped])
        elif escaped in (quote, 0x5C, ord("/")):
            output.append(escaped)
        elif escaped == ord("x"):
            if index + 2 >= len(value):
                raise ExtractError("ChannelList JavaScript 十六进制转义不完整")
            try:
                output.append(int(value[index + 1 : index + 3], 16))
            except ValueError as exc:
                raise ExtractError("ChannelList JavaScript 十六进制转义无效") from exc
            index += 2
        elif escaped == ord("u"):
            if index + 4 >= len(value):
                raise ExtractError("ChannelList JavaScript Unicode 转义不完整")
            try:
                output.extend(chr(int(value[index + 1 : index + 5], 16)).encode("utf-8"))
            except ValueError as exc:
                raise ExtractError("ChannelList JavaScript Unicode 转义无效") from exc
            index += 4
        else:
            output.append(escaped)
        index += 1
    return output.decode("utf-8", "replace")


def _ctc_calls(body: bytes) -> list[tuple[str, str]]:
    marker = re.compile(rb"Authentication\.CTCSetConfig\s*\(\s*(['\"])(.*?)\1\s*,\s*(['\"])", re.I | re.S)
    calls: list[tuple[str, str]] = []
    for match in marker.finditer(body):
        quote = match.group(3)[0]
        start = match.end()
        index = start
        escaped = False
        while index < len(body):
            byte = body[index]
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == quote:
                break
            index += 1
        if index >= len(body):
            raise ExtractError("ChannelList CTCSetConfig 调用不完整")
        closing = re.match(rb"\s*\)", body[index + 1 :])
        if closing is None:
            raise ExtractError("ChannelList CTCSetConfig 调用缺少闭合括号")
        name = _unescape_js_string(match.group(2), match.group(1)[0])
        value = _unescape_js_string(body[start:index], quote)
        calls.append((name, value))
    if body.lower().count(CHANNEL_MARKER.lower()) != len(calls):
        raise ExtractError("检测到 CTCSetConfig，但调用无法完整解析")
    return calls


def _valid_endpoint(value: str, excluded: set[str]) -> str | None:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None
    normalized = str(address)
    if normalized in excluded or address.is_multicast or address.is_unspecified or address.is_loopback or int(address) == 0xFFFFFFFF:
        return None
    return normalized


def _channel_endpoints(channel_values: list[str], excluded: set[str]) -> list[str]:
    endpoints: list[str] = []
    seen: set[str] = set()
    quoted_field = re.compile(
        r"(?is)['\"]?\b(ChannelURL|ChannelSDP|TimeShiftURL|ChannelFCCIP)\b['\"]?\s*[=:]\s*(['\"])(.*?)\2"
    )
    plain_field = re.compile(
        r"(?i)\b(ChannelURL|ChannelSDP|TimeShiftURL|ChannelFCCIP)\b\s*[=:]\s*([^\s,;<>}]+)"
    )
    for channel in channel_values:
        fields = [(field, value) for field, _, value in quoted_field.findall(channel)]
        fields.extend(plain_field.findall(quoted_field.sub("", channel)))
        for field, value in fields:
            candidates: list[str] = []
            if field.lower() == "channelfccip":
                candidates.extend(re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", value))
            else:
                for url in re.findall(r"(?i)rtsp://[^\s'\"<>]+", value):
                    try:
                        parsed = urlsplit(url)
                        hostname = parsed.hostname
                        query = parse_qsl(parsed.query, keep_blank_values=True)
                    except ValueError:
                        continue
                    if hostname:
                        candidates.append(hostname)
                    for key, item in query:
                        if key.lower() == "rrsip":
                            candidates.append(item)
                for item in re.findall(r"(?i)(?:[?&;,]|\b)rrsip=([0-9.]+)", value):
                    candidates.append(item)
            for candidate in candidates:
                normalized = _valid_endpoint(candidate, excluded)
                if normalized and normalized not in seen:
                    if len(endpoints) >= MAX_UNICAST_ROUTES:
                        raise ExtractError("ChannelList 单播端点超过 256 个限制")
                    seen.add(normalized)
                    endpoints.append(normalized)
        for candidate in re.findall(r"(?i)(?:[?&;,]|\b)rrsip\s*=\s*['\"]?([0-9.]+)", channel):
            normalized = _valid_endpoint(candidate, excluded)
            if normalized and normalized not in seen:
                if len(endpoints) >= MAX_UNICAST_ROUTES:
                    raise ExtractError("ChannelList 单播端点超过 256 个限制")
                seen.add(normalized)
                endpoints.append(normalized)
    return endpoints


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
        from scapy.all import BOOTP, DHCP, Ether, IP, TCP, rdpcap
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

    client_ips: set[str] = set()
    streams: dict[tuple[str, int, str, int], TcpStream] = defaultdict(TcpStream)
    for packet in packets:
        if not packet.haslayer(Ether) or not packet.haslayer(IP):
            continue
        ether = packet[Ether]
        ip = packet[IP]
        try:
            source_mac = normalize_mac(str(ether.src))
            destination_mac = normalize_mac(str(ether.dst))
        except ConfigError:
            continue
        if source_mac == transaction.mac:
            candidate = _valid_endpoint(str(ip.src), set())
            if candidate:
                client_ips.add(candidate)
        if destination_mac == transaction.mac:
            candidate = _valid_endpoint(str(ip.dst), set())
            if candidate:
                client_ips.add(candidate)
        if destination_mac != transaction.mac or not packet.haslayer(TCP):
            continue
        tcp = packet[TCP]
        payload = bytes(tcp.payload)
        if not payload:
            continue
        key = (str(ip.src), int(tcp.sport), str(ip.dst), int(tcp.dport))
        if key not in streams and len(streams) >= MAX_TCP_STREAMS:
            raise ExtractError("ChannelList TCP 重组超出 64 个流限制")
        sequence = int(tcp.seq) + (1 if "S" in str(tcp.flags) else 0)
        streams[key].add(sequence, payload)

    if transaction.ack:
        bootp_ip = next(
            (
                str(packet[BOOTP].yiaddr)
                for packet in packets
                if packet.haslayer(BOOTP)
                and int(packet[BOOTP].xid) == transaction.xid
                and str(packet[BOOTP].yiaddr) != "0.0.0.0"
            ),
            "",
        )
        if bootp_ip:
            client_ips.add(bootp_ip)

    channel_values: list[str] = []
    account_addresses: set[str] = set()
    channel_detected = False
    for stream in streams.values():
        data = stream.assemble()
        if not data:
            continue
        bodies = _http_bodies(data)
        if CHANNEL_MARKER.lower() in data.lower() and not any(
            CHANNEL_MARKER.lower() in body.lower() for body in bodies
        ):
            raise ExtractError("检测到 ChannelList，但未得到完整 HTTP 响应")
        for body in bodies:
            calls = _ctc_calls(body)
            for name, value in calls:
                lowered = name.strip().lower()
                if lowered == "channel":
                    channel_detected = True
                    channel_values.append(value)
                elif lowered == "accountinfo":
                    account_addresses.update(
                        re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", value)
                    )
    if channel_detected:
        excluded = client_ips | {
            normalized
            for value in account_addresses
            if (normalized := _valid_endpoint(value, set())) is not None
        }
        network["unicast_routes"] = _channel_endpoints(channel_values, excluded)
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
