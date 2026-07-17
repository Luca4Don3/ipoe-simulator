from __future__ import annotations

import unittest


class ExtractorTests(unittest.TestCase):
    @unittest.skipUnless(__import__("importlib").util.find_spec("scapy"), "Scapy 未安装")
    def test_broadcast_transaction_is_selected(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from scapy.all import BOOTP, DHCP, Ether, IP, UDP, wrpcap

        from ipoe_simulator.extractor import extract_profile

        mac = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
        peer_mac = ":".join(("00", "11", "22", "33", "44", "55"))
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


if __name__ == "__main__":
    unittest.main()
