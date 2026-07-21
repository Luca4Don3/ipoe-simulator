from __future__ import annotations

import base64
import json
import subprocess
import unittest
from unittest.mock import patch

from ipoe_simulator.network_backend import NetworkStateError
from ipoe_simulator.powershell_runtime import (
    PowerShellRuntime,
    _candidate_paths,
    _inspect_candidate,
    get_powershell_runtime,
)


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
    @patch("ipoe_simulator.powershell_runtime._inspect_candidate")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_prefers_capable_powershell_7(self, candidates, inspect) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [r"C:\pwsh.exe"],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        inspect.return_value = PowerShellRuntime(r"C:\pwsh.exe", 7, 4, 6, "Core")

        runtime = get_powershell_runtime()

        self.assertEqual(runtime.executable, r"C:\pwsh.exe")
        inspect.assert_called_once_with(r"C:\pwsh.exe", "pwsh.exe")

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._inspect_candidate")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_falls_back_after_each_powershell_7_failure(self, candidates, inspect) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [
                r"C:\pwsh-start.exe",
                r"C:\pwsh-version.exe",
                r"C:\pwsh-utf8.exe",
                r"C:\pwsh-network.exe",
            ],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        fallback = PowerShellRuntime(r"C:\powershell.exe", 5, 1, 19041, "Desktop")
        inspect.side_effect = [
            NetworkStateError("启动失败"),
            NetworkStateError("版本信息无效"),
            NetworkStateError("UTF-8 输出探针失败"),
            NetworkStateError("网卡 cmdlet 探针返回无效结果"),
            fallback,
        ]

        runtime = get_powershell_runtime()

        self.assertIs(runtime, fallback)
        self.assertEqual(inspect.call_count, 5)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._inspect_candidate")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_reports_all_candidate_failures(self, candidates, inspect) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [r"C:\pwsh.exe"],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        inspect.side_effect = [
            NetworkStateError("PowerShell 7 网络能力缺失"),
            NetworkStateError("最低支持 PowerShell 5.1"),
        ]

        with self.assertRaises(NetworkStateError) as raised:
            get_powershell_runtime()

        message = str(raised.exception)
        self.assertIn(r"C:\pwsh.exe: PowerShell 7 网络能力缺失", message)
        self.assertIn(r"C:\powershell.exe: 最低支持 PowerShell 5.1", message)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths", return_value=[])
    def test_reports_missing_runtime(self, _candidates) -> None:
        with self.assertRaisesRegex(NetworkStateError, "未找到受支持的 PowerShell"):
            get_powershell_runtime()

    @patch.object(PowerShellRuntime, "probe_network_cmdlets")
    @patch.object(PowerShellRuntime, "probe_utf8_output")
    @patch("ipoe_simulator.powershell_runtime.subprocess.run")
    def test_rejects_power_shell_50(self, run, utf8_probe, network_probe) -> None:
        run.return_value = _version_result(5, 0, 10586, "Desktop")

        with self.assertRaisesRegex(NetworkStateError, "最低支持 PowerShell 5.1"):
            _inspect_candidate(r"C:\powershell.exe", "powershell.exe")

        utf8_probe.assert_not_called()
        network_probe.assert_not_called()

    @patch.object(PowerShellRuntime, "probe_network_cmdlets")
    @patch.object(PowerShellRuntime, "probe_utf8_output")
    @patch("ipoe_simulator.powershell_runtime.subprocess.run")
    def test_candidate_checks_version_utf8_and_network(
        self, run, utf8_probe, network_probe
    ) -> None:
        run.return_value = _version_result(5, 1, 19041, "Desktop")

        runtime = _inspect_candidate(r"C:\powershell.exe", "powershell.exe")

        self.assertTrue(runtime.is_windows_powershell_51)
        utf8_probe.assert_called_once_with()
        network_probe.assert_called_once_with()
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    @patch("ipoe_simulator.powershell_runtime.subprocess.Popen")
    def test_network_probe_accepts_cmdlet_and_function(self, popen) -> None:
        process = popen.return_value
        process.communicate.return_value = ("ok", "")
        process.returncode = 0

        PowerShellRuntime(r"C:\powershell.exe", 5, 1, 19041, "Desktop").probe_network_cmdlets()

        encoded = popen.call_args.args[0][-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("-CommandType Cmdlet,Function", script)

    @patch("ipoe_simulator.powershell_runtime.subprocess.Popen")
    def test_runtime_invocation_prepends_utf8_setup(self, popen) -> None:
        process = popen.return_value
        process.communicate.return_value = ("ok", "")
        process.returncode = 0

        PowerShellRuntime(r"C:\pwsh.exe", 7, 4, 6, "Core").run("Write-Output 'ok'")

        encoded = popen.call_args.args[0][-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("UTF8Encoding", script)

    @patch("ipoe_simulator.powershell_runtime._terminate_process")
    @patch("ipoe_simulator.powershell_runtime.subprocess.Popen")
    def test_runtime_timeout_reaps_process(self, popen, terminate) -> None:
        process = popen.return_value
        process.communicate.side_effect = subprocess.TimeoutExpired("pwsh", 3)

        with self.assertRaisesRegex(NetworkStateError, "PowerShell 执行超时"):
            PowerShellRuntime(r"C:\pwsh.exe", 7, 4, 6, "Core").run("hang", timeout=3)

        terminate.assert_called_once_with(process)

    @patch("ipoe_simulator.powershell_runtime.os.path.isfile", return_value=True)
    @patch("ipoe_simulator.powershell_runtime.shutil.which", return_value=None)
    @patch.dict(
        "ipoe_simulator.powershell_runtime.os.environ",
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
        clear=True,
    )
    def test_finds_per_user_powershell_7_path(self, _which, _isfile) -> None:
        candidates = _candidate_paths("pwsh.exe")
        normalized = {path.replace("/", "\\") for path in candidates}
        self.assertIn(
            r"C:\Users\tester\AppData\Local\Microsoft\PowerShell\7\pwsh.exe",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
