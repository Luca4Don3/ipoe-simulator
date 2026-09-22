from __future__ import annotations

import ipaddress
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IPV4_PATTERN = re.compile(
    r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])"
)
MAC_PATTERN = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])"
)
DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def tracked_text_files() -> list[Path]:
    excluded_parts = {".git", ".temp", ".venv", "__pycache__"}
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in excluded_parts for part in path.parts):
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def is_netmask(address: ipaddress.IPv4Address) -> bool:
    try:
        ipaddress.ip_network(f"0.0.0.0/{address}")
    except ValueError:
        return False
    return True


class RepositoryHygieneTests(unittest.TestCase):
    def test_ipv4_literals_are_reserved_for_documentation_or_protocols(self) -> None:
        violations: list[str] = []
        for path in tracked_text_files():
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in IPV4_PATTERN.finditer(line):
                    try:
                        address = ipaddress.ip_address(match.group())
                    except ValueError:
                        continue
                    allowed = (
                        any(address in network for network in DOCUMENTATION_NETWORKS)
                        or address in BENCHMARK_NETWORK
                        or (
                            address.is_multicast
                            and "tests" in path.relative_to(ROOT).parts
                        )
                        or address.is_unspecified
                        or address.is_loopback
                        or int(address) == 0xFFFFFFFF
                        or is_netmask(address)
                    )
                    if not allowed:
                        violations.append(f"{path.relative_to(ROOT)}:{line_number}")
        self.assertEqual(violations, [])

    def test_mac_literals_are_local_or_protocol_constants(self) -> None:
        violations: list[str] = []
        for path in tracked_text_files():
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in MAC_PATTERN.finditer(line):
                    value = bytes.fromhex(match.group().replace(":", ""))
                    allowed = (
                        value == bytes(6)
                        or value == bytes([0xFF]) * 6
                        or bool(value[0] & 0x02)
                    )
                    if not allowed:
                        violations.append(f"{path.relative_to(ROOT)}:{line_number}")
        self.assertEqual(violations, [])

    def test_repository_governance_files_exist(self) -> None:
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        for name in ("config.yml", "bug_report.yml", "doc_issue.yml"):
            with self.subTest(template=name):
                self.assertTrue((ROOT / ".github" / "ISSUE_TEMPLATE" / name).is_file())

    def test_sensitive_runtime_files_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            ".env",
            "*.pcap",
            "*.pcapng",
            "network-recovery.json",
            "*.sqlite",
            "*.pem",
            "*.key",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore.splitlines())

    def test_actions_are_pinned_and_write_access_is_scoped(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        workflow_files = sorted(workflow_dir.glob("*.yml"))
        self.assertTrue(workflow_files)
        for path in workflow_files:
            with self.subTest(workflow=path.name):
                workflow = path.read_text(encoding="utf-8")
                action_references = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
                self.assertTrue(action_references)
                self.assertTrue(
                    all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_references)
                )
                self.assertIn("permissions:\n  contents: read", workflow)
                self.assertNotIn("permissions:\n  contents: write", workflow)
        release = (workflow_dir / "windows-release.yml").read_text(encoding="utf-8")
        self.assertRegex(
            release,
            r"(?ms)^  release:.*?^    permissions:\n      contents: write$",
        )
        submission = (workflow_dir / "dependency-submission.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            submission,
            r"(?ms)^  submit:.*?^    permissions:\n      contents: write$",
        )


if __name__ == "__main__":
    unittest.main()
