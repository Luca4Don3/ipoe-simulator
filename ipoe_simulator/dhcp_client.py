from __future__ import annotations

import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any

from .interfaces import InterfaceInfo
from .profile import OPTION_CODES, option_bytes


BROADCAST_MAC = ":".join(["ff"] * 6)
BROADCAST_IP = ".".join(["255"] * 4)


class DhcpError(RuntimeError):
    pass


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
class DhcpReply:
    message_type: int
    offered_ip: str
    server_id: str | None
    subnet_mask: str | None
    gateway: str | None
    dns_servers: list[str]
    lease_time: int
    renewal_time: int


@dataclass
class Lease:
    ip_address: str
    server_id: str
    subnet_mask: str
    gateway: str | None
    dns_servers: list[str]
    lease_time: int
    renewal_time: int


def _message_type(options: list[Any]) -> int | None:
    for option in options:
        if not isinstance(option, tuple) or len(option) < 2:
            continue
        if option[0] not in ("message-type", 53):
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


def _option_map(options: list[Any]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for option in options:
        if isinstance(option, tuple) and len(option) >= 2:
            result.setdefault(option[0], option[1])
    return result


def _ipv4(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes) and len(value) >= 4:
        return ".".join(str(part) for part in value[:4])
    if isinstance(value, (list, tuple)) and value:
        return _ipv4(value[0])
    return str(value)


def _u32(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, bytes) and len(value) == 4:
        return struct.unpack("!I", value)[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DhcpClient:
    def __init__(
        self,
        interface: InterfaceInfo,
        mac: str,
        option_values: dict[int, str],
        timeout: int = 8,
    ):
        try:
            from scapy.all import AsyncSniffer, BOOTP, DHCP, Ether, IP, UDP, sendp
        except ImportError as exc:
            raise DhcpError("需要 Scapy: python -m pip install -r requirements.txt") from exc
        self.AsyncSniffer = AsyncSniffer
        self.BOOTP = BOOTP
        self.DHCP = DHCP
        self.Ether = Ether
        self.IP = IP
        self.UDP = UDP
        self.sendp = sendp
        self.interface = interface
        self.mac_str = mac.lower()
        self.mac = bytes.fromhex(mac.replace(":", ""))
        self.option_values = option_values
        self.timeout = timeout
        self.xid = 0
        self.lease: Lease | None = None
        self.stop_event = threading.Event()

    def _bootp(self, **values: Any) -> Any:
        return self.BOOTP(
            op=1,
            htype=1,
            hlen=6,
            hops=0,
            secs=0,
            chaddr=self.mac,
            **values,
        )

    def _packet(
        self,
        bootp: Any,
        options: list[Any],
        source_ip: str = "0.0.0.0",
        destination_ip: str = BROADCAST_IP,
    ) -> Any:
        return (
            self.Ether(dst=BROADCAST_MAC, src=self.mac_str)
            / self.IP(src=source_ip, dst=destination_ip)
            / self.UDP(sport=68, dport=67)
            / bootp
            / self.DHCP(options=options)
        )

    def _profile_options(self, include_requested_ip: bool = True) -> list[Any]:
        options: list[Any] = []
        for code in OPTION_CODES:
            value = self.option_values.get(code)
            if not value or (code == 50 and not include_requested_ip):
                continue
            raw = option_bytes(value, code)
            if code == 12:
                options.append(("hostname", raw))
            elif code == 43:
                options.append((43, raw))
            elif code == 50:
                options.append(("requested_addr", ".".join(str(part) for part in raw)))
            elif code == 60:
                options.append(("vendor_class_id", raw))
            elif code == 61:
                options.append(("client_id", raw))
            elif code == 125:
                options.append((125, raw))
        return options

    def _discover(self) -> Any:
        bootp = self._bootp(
            xid=self.xid,
            flags=0x8000,
            ciaddr="0.0.0.0",
            yiaddr="0.0.0.0",
            siaddr="0.0.0.0",
            giaddr="0.0.0.0",
        )
        options = [("message-type", "discover")]
        options.extend(self._profile_options(include_requested_ip=True))
        options.extend((("param_req_list", [1, 3, 6, 12, 15, 28, 31, 33, 51, 54, 58, 59]), "end"))
        return self._packet(bootp, options)

    def _request(self, offered_ip: str, server_id: str) -> Any:
        bootp = self._bootp(
            xid=self.xid,
            flags=0x8000,
            ciaddr="0.0.0.0",
            yiaddr="0.0.0.0",
            siaddr="0.0.0.0",
            giaddr="0.0.0.0",
        )
        options = [
            ("message-type", "request"),
            ("server_id", server_id),
            ("requested_addr", offered_ip),
        ]
        options.extend(self._profile_options(include_requested_ip=False))
        options.extend((("param_req_list", [1, 3, 6, 12, 15, 28, 31, 33, 51, 54, 58, 59]), "end"))
        return self._packet(bootp, options)

    def _renew(self) -> Any:
        if self.lease is None:
            raise DhcpError("尚未获得租约")
        bootp = self._bootp(
            xid=self.xid,
            flags=0,
            ciaddr=self.lease.ip_address,
            yiaddr="0.0.0.0",
            siaddr="0.0.0.0",
            giaddr="0.0.0.0",
        )
        options = [("message-type", "request")]
        options.extend(self._profile_options(include_requested_ip=False))
        options.extend((("param_req_list", [1, 3, 6, 12, 15, 28, 31, 33, 51, 54, 58, 59]), "end"))
        return self._packet(
            bootp,
            options,
            source_ip=self.lease.ip_address,
            destination_ip=self.lease.server_id,
        )

    def _release(self) -> Any:
        if self.lease is None:
            return None
        bootp = self._bootp(
            xid=self.xid,
            flags=0,
            ciaddr=self.lease.ip_address,
            yiaddr="0.0.0.0",
            siaddr="0.0.0.0",
            giaddr="0.0.0.0",
        )
        options = [("message-type", "release"), ("server_id", self.lease.server_id), "end"]
        return self._packet(
            bootp,
            options,
            source_ip=self.lease.ip_address,
            destination_ip=self.lease.server_id,
        )

    def _parse_reply(self, packet: Any) -> DhcpReply | None:
        if not packet.haslayer(self.BOOTP) or not packet.haslayer(self.DHCP):
            return None
        bootp = packet[self.BOOTP]
        if int(bootp.xid) != self.xid or bytes(bootp.chaddr)[:6] != self.mac:
            return None
        options = list(packet[self.DHCP].options)
        message_type = _message_type(options)
        if message_type is None:
            return None
        values = _option_map(options)
        server = _ipv4(values.get("server_id", values.get(54))) or _ipv4(bootp.siaddr)
        subnet = _ipv4(values.get("subnet_mask", values.get(1)))
        gateway = _ipv4(values.get("router", values.get(3)))
        dns_value = values.get("domain_name_server", values.get(6, []))
        if not isinstance(dns_value, (list, tuple)):
            dns_value = [dns_value] if dns_value else []
        dns_servers = [item for item in (_ipv4(value) for value in dns_value) if item]
        lease_time = _u32(values.get("lease_time", values.get(51)), 2400)
        renewal_time = _u32(values.get("renewal_time", values.get(58)), max(lease_time // 2, 60))
        return DhcpReply(
            message_type=message_type,
            offered_ip=str(bootp.yiaddr),
            server_id=server,
            subnet_mask=subnet,
            gateway=gateway,
            dns_servers=dns_servers,
            lease_time=lease_time,
            renewal_time=renewal_time,
        )

    def _exchange(self, packet: Any, expected: set[int], label: str) -> DhcpReply:
        ready = threading.Event()
        received = threading.Event()
        reply: list[DhcpReply] = []

        def handle(candidate: Any) -> None:
            parsed = self._parse_reply(candidate)
            if parsed is None or parsed.message_type not in expected | {6}:
                return
            reply.append(parsed)
            received.set()

        sniffer = self.AsyncSniffer(
            iface=self.interface.pcap_name,
            filter="udp and (port 67 or port 68)",
            prn=handle,
            store=False,
            started_callback=ready.set,
        )
        try:
            sniffer.start()
            if not ready.wait(2):
                raise DhcpError("二层抓包监听器未能启动")
            self.sendp(packet, iface=self.interface.pcap_name, verbose=False)
            if not received.wait(self.timeout):
                raise DhcpError(f"等待 {label} 超时 ({self.timeout}s)")
        except DhcpError:
            raise
        except Exception as exc:
            raise DhcpError(f"DHCP {label} 交换失败: {exc}") from exc
        finally:
            try:
                if sniffer.running:
                    sniffer.stop()
            except Exception:
                pass
        result = reply[-1]
        if result.message_type == 6:
            raise DhcpError("DHCP 服务器返回 NAK")
        return result

    def handshake(self) -> Lease:
        self.xid = struct.unpack("!I", os.urandom(4))[0]
        print(f"Discover (XID: 0x{self.xid:08x})")
        offer = self._exchange(self._discover(), {2}, "Offer")
        if not offer.offered_ip or offer.offered_ip == "0.0.0.0" or not offer.server_id:
            raise DhcpError("Offer 缺少地址或 DHCP Server ID")
        print(f"Offer: {offer.offered_ip}，服务器 {offer.server_id}")
        ack = self._exchange(self._request(offer.offered_ip, offer.server_id), {5}, "ACK")
        if not ack.subnet_mask:
            raise DhcpError("ACK 缺少子网掩码，拒绝修改网卡")
        server_id = ack.server_id or offer.server_id
        self.lease = Lease(
            ip_address=ack.offered_ip or offer.offered_ip,
            server_id=server_id,
            subnet_mask=ack.subnet_mask,
            gateway=ack.gateway,
            dns_servers=ack.dns_servers,
            lease_time=ack.lease_time,
            renewal_time=max(ack.renewal_time, 60),
        )
        print(f"ACK: {self.lease.ip_address}/{self.lease.subnet_mask}")
        return self.lease

    def renew_forever(self) -> None:
        if self.lease is None:
            raise DhcpError("尚未获得租约")
        while not self.stop_event.wait(self.lease.renewal_time):
            self.xid = struct.unpack("!I", os.urandom(4))[0]
            print("开始续租...")
            try:
                ack = self._exchange(self._renew(), {5}, "续租 ACK")
                self.lease.lease_time = ack.lease_time
                self.lease.renewal_time = max(ack.renewal_time, 60)
                print("续租成功")
            except DhcpError as exc:
                print(f"续租失败: {exc}")

    def stop(self) -> None:
        self.stop_event.set()

    def release(self) -> None:
        packet = self._release()
        if packet is None:
            return
        try:
            self.sendp(packet, iface=self.interface.pcap_name, verbose=False)
            print("已发送 DHCP Release")
        except Exception as exc:
            raise DhcpError(f"发送 DHCP Release 失败: {exc}") from exc
