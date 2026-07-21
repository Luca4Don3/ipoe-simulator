from __future__ import annotations

import unittest
from unittest import mock

import ipoedhcp
from ipoe_simulator.network_backend import NetworkStateError


class DhcpInterruptTests(unittest.TestCase):
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
