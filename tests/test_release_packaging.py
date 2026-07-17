from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_windows_release import (
    REQUIRED_FILES,
    ReleaseVerificationError,
    verify_archive,
)


def fake_pe(machine: int) -> bytes:
    content = bytearray(256)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 0x3C, 128)
    content[128:132] = b"PE\0\0"
    struct.pack_into("<H", content, 132, machine)
    return bytes(content)


class ReleasePackagingTests(unittest.TestCase):
    def make_archive(
        self,
        directory: Path,
        *,
        architecture: str = "x64",
        extra_file: str | None = None,
    ) -> Path:
        version = "0.1.0"
        root = f"ipoe-simulator-v{version}-windows-{architecture}"
        archive = directory / f"{root}.zip"
        machines = {"x86": 0x014C, "x64": 0x8664, "arm64": 0xAA64}
        with zipfile.ZipFile(archive, "w") as package:
            for relative in REQUIRED_FILES:
                content = b"placeholder"
                if relative == "runtime/python.exe":
                    content = fake_pe(machines[architecture])
                elif relative == "VERSION":
                    content = version.encode()
                package.writestr(f"{root}/{relative}", content)
            if extra_file:
                package.writestr(f"{root}/{extra_file}", b"forbidden")
        return archive

    def test_accepts_each_windows_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for architecture in ("x86", "x64", "arm64"):
                with self.subTest(architecture=architecture):
                    archive = self.make_archive(Path(directory), architecture=architecture)
                    verify_archive(archive, architecture, "0.1.0")

    def test_rejects_other_platform_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(Path(directory), extra_file="run.sh")
            with self.assertRaisesRegex(ReleaseVerificationError, "禁止文件"):
                verify_archive(archive, "x64", "0.1.0")

    def test_rejects_multiple_top_level_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(Path(directory))
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr("unexpected/file.txt", b"bad")
            with self.assertRaisesRegex(ReleaseVerificationError, "顶层目录"):
                verify_archive(archive, "x64", "0.1.0")


if __name__ == "__main__":
    unittest.main()
