from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_CMD = ROOT / "run.cmd"
LAUNCHER = ROOT / "scripts" / "windows_launcher.ps1"


class WindowsLauncherTests(unittest.TestCase):
    def test_launcher_cleans_only_bounded_temporary_assets(self) -> None:
        text = (ROOT / "scripts" / "windows_launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("Remove-StaleTemporaryFiles", text)
        self.assertIn("owner.json", text)
        self.assertIn("AddHours(-24)", text)
        self.assertIn("Remove-Item -LiteralPath $pythonArchive", text)
        self.assertIn("Remove-Item -LiteralPath $scapyArchive", text)
        self.assertNotIn("Remove-Item -LiteralPath $runtimeDirectory", text)

    def test_batch_launcher_prefers_powershell_7_and_propagates_exit_code(self) -> None:
        content = RUN_CMD.read_text(encoding="utf-8")
        self.assertLess(content.index("where pwsh.exe"), content.index("where powershell.exe"))
        self.assertIn('set "exit_code=%errorlevel%"', content)
        self.assertIn("exit /b %exit_code%", content)
        self.assertIn('"%~dp0runtime\\python.exe" -u "%~dp0ipoedhcp.py" %*', content)
        self.assertIn("net session >nul 2>&1", content)
        self.assertIn("唯一正确命令: .\\run.cmd --restore", content)

    def test_batch_launcher_logs_before_runtime_discovery(self) -> None:
        content = RUN_CMD.read_text(encoding="utf-8")
        self.assertLess(
            content.index('INFO launcher: run.cmd'),
            content.index("where pwsh.exe"),
        )
        self.assertIn('cd /d "%~dp0"', content)
        self.assertIn('chcp 65001', content)

    def test_powershell_launcher_covers_elevation_and_python_validation(self) -> None:
        content = LAUNCHER.read_text(encoding="utf-8")
        required_fragments = (
            "Start-Process",
            "-Verb RunAs",
            "-Wait",
            "-PassThru",
            "NativeErrorCode -eq 1223",
            "runtime\\python.exe",
            "Install-PythonRuntime",
            "Invoke-VerifiedDownload",
            "Get-FileHash",
            "Expand-Archive",
            "ExtractToDirectory",
            "依赖 SHA-256 不匹配",
            "Get-Command 'py.exe'",
            "[Version]'3.9.0'",
            "[Version]'3.15.0'",
            "3.9–3.14",
            "与 Windows 架构",
            "@LauncherArguments",
            "$LauncherArguments = @($args)",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, content)

    def test_restore_routes_directly_and_has_strict_arguments(self) -> None:
        content = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("Join-Path $RootDirectory 'ipoedhcp.py'", content)
        self.assertIn("唯一正确命令: .\\run.cmd --restore", content)
        self.assertIn("--restore 仅允许附加 --log-level INFO|DEBUG", content)
        self.assertIn("存在待恢复 journal，优先安装锁定运行时", content)
        self.assertIn("if ($restoreMode)", content)
        restore_tail = content[content.index("if ($restoreMode)", content.index("$businessScript")):]
        self.assertNotIn("Read-Host '按 Enter 键关闭窗口'", restore_tail)
        self.assertTrue(restore_tail.rstrip().endswith("exit $exitCode"))

    def test_committed_line_endings_follow_platform_rules(self) -> None:
        attributes = subprocess.run(
            ["git", "check-attr", "eol", "--", "run.cmd", "run.sh"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertIn("run.cmd: eol: crlf", attributes)
        self.assertIn("run.sh: eol: lf", attributes)
        for path in (RUN_CMD, LAUNCHER):
            with self.subTest(path=path.name):
                content = path.read_bytes()
                self.assertIn(b"\r\n", content)
                self.assertNotIn(b"\n", content.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
