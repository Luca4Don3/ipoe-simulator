from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from ipoe_simulator.network_backend import NetworkStateError
from ipoe_simulator.powershell_runtime import get_powershell_runtime


def _version_result(major: int, minor: int, patch_level: int, edition: str = "Core"):
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "major": major,
                "minor": minor,
                "patch": patch_level,
                "edition": edition,
            }
        ),
        stderr="",
    )


class PowerShellRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_powershell_runtime.cache_clear()

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime.shutil.which")
    @patch("ipoe_simulator.powershell_runtime.subprocess.run")
    def test_prefers_powershell_7(self, run, which) -> None:
        which.side_effect = lambda name: f"C:\\{name}" if name == "pwsh.exe" else None
        run.return_value = _version_result(7, 4, 6)

        runtime = get_powershell_runtime()

        self.assertEqual(runtime.executable, "C:\\pwsh.exe")
        self.assertEqual(runtime.version, "7.4.6")
        self.assertEqual(runtime.edition, "Core")
        self.assertFalse(runtime.is_windows_powershell_51)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime.shutil.which")
    @patch("ipoe_simulator.powershell_runtime.subprocess.run")
    def test_falls_back_to_windows_powershell_51(self, run, which) -> None:
        which.side_effect = lambda name: f"C:\\{name}" if name == "powershell.exe" else None
        run.return_value = _version_result(5, 1, 19041, "Desktop")

        runtime = get_powershell_runtime()

        self.assertEqual(runtime.executable, "C:\\powershell.exe")
        self.assertTrue(runtime.is_windows_powershell_51)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime.shutil.which", return_value="C:\\powershell.exe")
    @patch("ipoe_simulator.powershell_runtime.subprocess.run")
    def test_rejects_power_shell_50(self, run, _which) -> None:
        run.return_value = _version_result(5, 0, 10586, "Desktop")

        with self.assertRaisesRegex(NetworkStateError, "最低支持 PowerShell 5.1"):
            get_powershell_runtime()

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime.shutil.which", return_value=None)
    def test_reports_missing_runtime(self, _which) -> None:
        with self.assertRaisesRegex(NetworkStateError, "未找到受支持的 PowerShell"):
            get_powershell_runtime()


if __name__ == "__main__":
    unittest.main()
