from __future__ import annotations

import unittest
from unittest import mock

import ipoedhcp
from ipoe_simulator.network_backend import NetworkStateError


class DhcpInterruptTests(unittest.TestCase):
    def test_retries_restore_after_repeated_ctrl_c(self) -> None:
        transaction = mock.Mock()
        transaction.restore.side_effect = [KeyboardInterrupt, None]

        self.assertTrue(ipoedhcp._restore_transaction(transaction))

        self.assertEqual(transaction.restore.call_count, 2)

    def test_restore_network_failure_remains_explicit(self) -> None:
        transaction = mock.Mock()
        transaction.restore.side_effect = NetworkStateError("restore failed")

        self.assertFalse(ipoedhcp._restore_transaction(transaction))

        transaction.restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
