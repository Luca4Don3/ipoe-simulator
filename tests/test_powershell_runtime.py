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
    _inspect_version,
    _probe_capabilities,
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
    @patch("ipoe_simulator.powershell_runtime._probe_capabilities")
    @patch("ipoe_simulator.powershell_runtime._inspect_version")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_prefers_capable_powershell_7(self, candidates, inspect, probe) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [r"C:\pwsh.exe"],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        selected = PowerShellRuntime(r"C:\pwsh.exe", 7, 4, 6, "Core")
        inspect.return_value = selected
        probe.return_value = selected

        runtime = get_powershell_runtime()

        self.assertEqual(runtime.executable, r"C:\pwsh.exe")
        inspect.assert_called_once_with(r"C:\pwsh.exe", "pwsh.exe")
        probe.assert_called_once_with(selected)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._probe_capabilities")
    @patch("ipoe_simulator.powershell_runtime._inspect_version")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_latest_pwsh_failure_falls_directly_back_to_51(
        self, candidates, inspect, probe
    ) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [r"C:\pwsh-6.exe", r"C:\pwsh-7.exe"],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        pwsh6 = PowerShellRuntime(r"C:\pwsh-6.exe", 6, 2, 7, "Core")
        pwsh7 = PowerShellRuntime(r"C:\pwsh-7.exe", 7, 6, 0, "Core")
        fallback = PowerShellRuntime(r"C:\powershell.exe", 5, 1, 19041, "Desktop")
        inspect.side_effect = [pwsh6, pwsh7, fallback]
        probe.side_effect = [NetworkStateError("UTF-8 输出探针失败"), fallback]

        runtime = get_powershell_runtime()

        self.assertIs(runtime, fallback)
        self.assertEqual(probe.call_args_list, [unittest.mock.call(pwsh7), unittest.mock.call(fallback)])

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._probe_capabilities", side_effect=lambda runtime: runtime)
    @patch("ipoe_simulator.powershell_runtime._inspect_version")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_only_powershell_6_is_supported(self, candidates, inspect, _probe) -> None:
        candidates.side_effect = lambda name: [r"C:\pwsh.exe"] if name == "pwsh.exe" else []
        inspect.return_value = PowerShellRuntime(r"C:\pwsh.exe", 6, 2, 7, "Core")
        self.assertEqual(get_powershell_runtime().major, 6)

    @patch("ipoe_simulator.powershell_runtime.os.name", "nt")
    @patch("ipoe_simulator.powershell_runtime._probe_capabilities")
    @patch("ipoe_simulator.powershell_runtime._inspect_version")
    @patch("ipoe_simulator.powershell_runtime._candidate_paths")
    def test_reports_all_candidate_failures(self, candidates, inspect, probe) -> None:
        candidates.side_effect = lambda name: {
            "pwsh.exe": [r"C:\pwsh.exe"],
            "powershell.exe": [r"C:\powershell.exe"],
        }[name]
        pwsh = PowerShellRuntime(r"C:\pwsh.exe", 7, 6, 0, "Core")
        inspect.side_effect = [pwsh, NetworkStateError("最低支持 PowerShell 5.1")]
        probe.side_effect = NetworkStateError("PowerShell 7 网络能力缺失")

        with self.assertRaises(NetworkStateError) as raised:
            get_powershell_runtime()

        message = str(raised.exception)
        self.assertIn(r"C:\pwsh.exe (7.6.0): PowerShell 7 网络能力缺失", message)
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
    @patch("ipoe_simulator.powershell_runtime.glob.glob")
    @patch("ipoe_simulator.powershell_runtime.shutil.which", return_value=None)
    @patch.dict(
        "ipoe_simulator.powershell_runtime.os.environ",
        {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
        clear=True,
    )
    def test_finds_per_user_powershell_7_path(
        self, _which, glob_paths, _isfile
    ) -> None:
        expected = r"C:\Users\tester\AppData\Local\Microsoft\PowerShell\7\pwsh.exe"
        glob_paths.side_effect = lambda pattern: [expected] if "Microsoft" in pattern else []
        candidates = _candidate_paths("pwsh.exe")
        normalized = {path.replace("/", "\\") for path in candidates}
        self.assertIn(
            expected,
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
