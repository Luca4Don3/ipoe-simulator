from __future__ import annotations

import unittest

from ipoe_simulator.extractor import (
    ExtractError,
    TcpStream,
    _channel_endpoints,
    _ctc_calls,
    _http_bodies,
    _ipv4,
)


class ExtractorTests(unittest.TestCase):
    def test_ipv4_integer_and_nested_sequence(self) -> None:
        self.assertEqual(_ipv4(0xC0000201), "192.0.2.1")
        self.assertEqual(_ipv4([(0xC0000235,)]), "192.0.2.53")

    def test_invalid_ipv4_integer_is_explicit(self) -> None:
        with self.assertRaisesRegex(ExtractError, "无效 IPv4 整数"):
            _ipv4(1 << 32)

    def test_tcp_reassembly_handles_out_of_order_and_retransmission(self) -> None:
        stream = TcpStream()
        stream.add(104, b"ef")
        stream.add(100, b"abcd")
        stream.add(100, b"abcd")
        self.assertEqual(stream.assemble(), b"abcdef")

    def test_tcp_reassembly_rejects_conflicting_overlap(self) -> None:
        stream = TcpStream()
        stream.add(100, b"abcd")
        with self.assertRaisesRegex(ExtractError, "冲突重叠"):
            stream.add(102, b"XY")

    def test_chunked_gzip_channel_list_and_filters(self) -> None:
        import gzip

        channel = (
            "Authentication.CTCSetConfig('Channel',"
            "'{\"ChannelURL\":\"rtsp://198.51.100.10/live?rrsip=198.51.100.11\","
            "\"ChannelSDP\":\"a=control:rtsp://198.51.100.12/live\","
            "\"TimeShiftURL\":\"rtsp://198.51.100.10/back\","
            "\"ChannelFCCIP\":\"239.1.1.1\",\"rrsip\":\"192.0.2.10\"}')"
        ).encode()
        compressed = gzip.compress(channel)
        chunks = b"".join(
            f"{len(part):x}\r\n".encode() + part + b"\r\n"
            for part in (compressed[:9], compressed[9:])
        ) + b"0\r\n\r\n"
        response = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
            b"Content-Encoding: gzip\r\n\r\n" + chunks
        )
        bodies = _http_bodies(response)
        calls = _ctc_calls(bodies[0])
        routes = _channel_endpoints(
            [value for name, value in calls if name == "Channel"],
            {"192.0.2.10"},
        )
        self.assertEqual(routes, ["198.51.100.10", "198.51.100.11", "198.51.100.12"])

    def test_identity_content_length_and_incomplete_calls(self) -> None:
        body = b"Authentication.CTCSetConfig('Channel','{}')"
        response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        self.assertEqual(_http_bodies(response), [body])
        self.assertEqual(_ctc_calls(body), [("Channel", "{}")])
        with self.assertRaisesRegex(ExtractError, "不完整|无法完整解析"):
            _ctc_calls(b"Authentication.CTCSetConfig('Channel','broken")

    def test_ctc_call_requires_closing_parenthesis(self) -> None:
        with self.assertRaisesRegex(ExtractError, "缺少闭合括号"):
            _ctc_calls(b"Authentication.CTCSetConfig('Channel','{}'")

    def test_incomplete_hex_and_unicode_escapes_are_explicit(self) -> None:
        for escaped in (br"\x", br"\x1", br"\u", br"\u123"):
            with self.subTest(escaped=escaped), self.assertRaisesRegex(
                ExtractError, "转义不完整"
            ):
                _ctc_calls(
                    b"Authentication.CTCSetConfig('Channel','" + escaped + b"')"
                )

    def test_malformed_rtsp_url_is_ignored(self) -> None:
        self.assertEqual(
            _channel_endpoints(
                ["ChannelURL=rtsp://[invalid/live;ChannelFCCIP=198.51.100.10"],
                set(),
            ),
            ["198.51.100.10"],
        )

    def test_missing_channel_segment_is_explicit(self) -> None:
        stream = TcpStream()
        stream.add(100, b"HTTP/1.1 200 OK\r\n\r\nAuthentication.CTCSetConfig")
        stream.add(200, b"tail")
        with self.assertRaisesRegex(ExtractError, "缺少报文段"):
            stream.assemble()

    def test_channel_endpoint_limit_and_legal_empty_result(self) -> None:
        values = ",".join(
            f"ChannelFCCIP=198.18.{number // 256}.{number % 256}"
            for number in range(1, 258)
        )
        with self.assertRaisesRegex(ExtractError, "超过 256"):
            _channel_endpoints([values], set())
        self.assertEqual(
            _channel_endpoints(["ChannelFCCIP=239.1.1.1"], set()), []
        )

    @unittest.skipUnless(__import__("importlib").util.find_spec("scapy"), "Scapy 未安装")
    def test_broadcast_transaction_is_selected(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from scapy.all import BOOTP, DHCP, Ether, IP, UDP, wrpcap

        from ipoe_simulator.extractor import extract_profile

        # 合成测试数据：本地管理 MAC 与 RFC 5737 文档保留地址。
        mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
        peer_mac = ":".join(("02", "00", "00", "00", "00", "02"))
        broadcast_mac = ":".join(["ff"] * 6)
        broadcast_ip = ".".join(["255"] * 4)
        subnet_mask = ".".join(("255", "255", "255", "0"))
        xid = 0x12345678
        discover = Ether(src=mac, dst=broadcast_mac) / IP(src="0.0.0.0", dst=broadcast_ip) / UDP(sport=68, dport=67) / BOOTP(xid=xid, chaddr=bytes.fromhex(mac.replace(":", "")), flags=0x8000) / DHCP(options=[("message-type", "discover"), ("vendor_class_id", b"ITV"), "end"])
        offer = Ether(src=peer_mac, dst=broadcast_mac) / IP(src="0.0.0.0", dst=broadcast_ip) / UDP(sport=67, dport=68) / BOOTP(op=2, xid=xid, chaddr=bytes.fromhex(mac.replace(":", "")), yiaddr="192.0.2.10", siaddr="192.0.2.1") / DHCP(options=[("message-type", "offer"), ("server_id", "192.0.2.1"), "end"])
        request = Ether(src=mac, dst=broadcast_mac) / IP(src="0.0.0.0", dst=broadcast_ip) / UDP(sport=68, dport=67) / BOOTP(xid=xid, chaddr=bytes.fromhex(mac.replace(":", "")), flags=0x8000) / DHCP(options=[("message-type", "request"), ("vendor_class_id", b"ITV"), "end"])
        ack = Ether(src=peer_mac, dst=broadcast_mac) / IP(src="192.0.2.1", dst=broadcast_ip) / UDP(sport=67, dport=68) / BOOTP(op=2, xid=xid, chaddr=bytes.fromhex(mac.replace(":", "")), yiaddr="192.0.2.10") / DHCP(options=[("message-type", "ack"), ("subnet_mask", subnet_mask), ("router", "192.0.2.1"), "end"])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pcap"
            wrpcap(str(path), [discover, offer, request, ack])
            result = extract_profile(path)
        self.assertEqual(result["mac"], mac)
        self.assertEqual(result["option60"], "ITV")

    @unittest.skipUnless(__import__("importlib").util.find_spec("scapy"), "Scapy 未安装")
    def test_pcap_nonstandard_http_segmented_channel_routes(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from scapy.all import BOOTP, DHCP, Ether, IP, TCP, UDP, Raw, wrpcapng

        from ipoe_simulator.extractor import extract_profile

        # 合成测试数据：本地管理 MAC、RFC 5737 地址及必要的组播地址。
        mac = "aa:bb:cc:dd:ee:ff"
        peer_mac = "02:00:00:00:00:02"
        broadcast_mac = "ff:ff:ff:ff:ff:ff"
        xid = 0x10203040
        base = [
            Ether(src=mac, dst=broadcast_mac) / IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=68, dport=67) / BOOTP(xid=xid, chaddr=bytes.fromhex(mac.replace(":", ""))) / DHCP(options=[("message-type", "discover"), "end"]),
            Ether(src=mac, dst=broadcast_mac) / IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=68, dport=67) / BOOTP(xid=xid, chaddr=bytes.fromhex(mac.replace(":", ""))) / DHCP(options=[("message-type", "request"), "end"]),
            Ether(src=peer_mac, dst=broadcast_mac) / IP(src="192.0.2.1", dst="255.255.255.255") / UDP(sport=67, dport=68) / BOOTP(op=2, xid=xid, chaddr=bytes.fromhex(mac.replace(":", "")), yiaddr="192.0.2.10") / DHCP(options=[("message-type", "ack"), ("subnet_mask", "255.255.255.0"), ("router", "192.0.2.1"), "end"]),
        ]
        body = (
            "Authentication.CTCSetConfig('accountinfo','proxy=203.0.113.99');"
            "Authentication.CTCSetConfig('Channel','{"
            "\"ChannelURL\":\"rtsp://198.51.100.10/live?rrsip=198.51.100.11\","
            "\"ChannelSDP\":\"rtsp://198.51.100.12/live\","
            "\"ChannelFCCIP\":\"239.1.1.1\","
            "\"TimeShiftURL\":\"rtsp://192.0.2.10/back\"}')"
        ).encode()
        response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        cut1, cut2 = 53, 127
        parts = [(5000, response[:cut1]), (5000 + cut1, response[cut1:cut2]), (5000 + cut2, response[cut2:])]
        tcp_packets = [
            Ether(src=peer_mac, dst=mac) / IP(src="192.0.2.20", dst="192.0.2.10") / TCP(sport=8088, dport=49152, seq=sequence, flags="PA") / Raw(load=payload)
            for sequence, payload in (parts[1], parts[0], parts[0], parts[2])
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "channel.pcapng"
            wrpcapng(str(path), base + tcp_packets)
            self.assertEqual(path.read_bytes()[:4], bytes.fromhex("0a0d0d0a"))
            result = extract_profile(path)
        self.assertEqual(
            result["network"]["unicast_routes"],
            ["198.51.100.10", "198.51.100.11", "198.51.100.12"],
        )


if __name__ == "__main__":
    unittest.main()
