from __future__ import annotations

import unittest
from unittest import mock

import ipoedhcp
from ipoe_simulator.dhcp_client import DhcpClient, DhcpStopped
from ipoe_simulator.network_backend import NetworkStateError


class DhcpInterruptTests(unittest.TestCase):
    def test_offer_wait_propagates_controlled_stop(self) -> None:
        client = object.__new__(DhcpClient)
        client.stop_event = __import__("threading").Event()
        client.stop_event.set()
        client.timeout = 8
        client.interface = mock.Mock(pcap_name="test-interface")
        sniffer = mock.Mock(running=False)
        client.AsyncSniffer = mock.Mock(return_value=sniffer)
        client.sendp = mock.Mock()

        def start() -> None:
            callback = client.AsyncSniffer.call_args.kwargs["started_callback"]
            callback()

        sniffer.start.side_effect = start
        with self.assertRaises(DhcpStopped):
            client._exchange(mock.Mock(), {2}, "Offer")

    def test_first_and_repeated_interrupts_stop_once_without_raising(self) -> None:
        client = mock.Mock()
        controller = ipoedhcp.StopController(client)

        with mock.patch.object(ipoedhcp.LOGGER, "warning") as warning:
            controller.handle(None, None)
            controller.phase = "DHCP Release"
            controller.handle(None, None)
            controller.phase = "网卡恢复与校验"
            controller.handle(None, None)
            controller.phase = "退出"
            controller.handle(None, None)

        self.assertTrue(controller.requested)
        self.assertEqual(client.stop.call_count, 4)
        self.assertEqual(warning.call_count, 4)
        messages = [call.args for call in warning.call_args_list]
        self.assertEqual(messages[0], ("已收到停止请求，正在安全恢复，请勿重复按键",))
        self.assertEqual([args[1] for args in messages[1:]], [
            "DHCP Release", "网卡恢复与校验", "退出"
        ])

    def test_restore_is_not_reentered(self) -> None:
        transaction = mock.Mock()

        self.assertTrue(ipoedhcp._restore_transaction(transaction))

        transaction.restore.assert_called_once_with()

    def test_restore_network_failure_remains_explicit(self) -> None:
        transaction = mock.Mock()
        transaction.restore.side_effect = NetworkStateError("restore failed")

        self.assertFalse(ipoedhcp._restore_transaction(transaction))

        transaction.restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
