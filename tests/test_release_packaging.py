from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_windows_release import (
    REQUIRED_FILES,
    ReleaseVerificationError,
    verify_archive,
)

class ReleasePackagingTests(unittest.TestCase):
    def test_release_dependency_versions_and_wheel_are_locked(self) -> None:
        lock = json.loads(
            (Path(__file__).resolve().parents[1] / "release-dependencies.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock["python"]["version"], "3.14.6")
        self.assertEqual(lock["scapy"]["version"], "2.7.0")
        requirements = (
            Path(__file__).resolve().parents[1] / "requirements.txt"
        ).read_text(encoding="utf-8")
        self.assertIn(lock["scapy"]["url"], requirements)
        self.assertIn(lock["scapy"]["sha256"], requirements)
        for architecture in ("x86", "x64", "arm64"):
            metadata = lock["python"]["architectures"][architecture]
            self.assertIn("3.14.6", metadata["url"])
            self.assertRegex(metadata["sha256"], r"^[0-9a-f]{64}$")

    def make_archive(
        self,
        directory: Path,
        *,
        architecture: str = "x64",
        extra_file: str | None = None,
        content_overrides: dict[str, bytes] | None = None,
    ) -> Path:
        version = "0.1.0"
        root = f"ipoe-simulator-v{version}-windows-{architecture}"
        archive = directory / f"{root}.zip"
        project_root = Path(__file__).resolve().parents[1]
        packaged_text_files = {
            "LICENSE",
            "THIRD-PARTY-NOTICES.txt",
            "licenses/SCAPY-LICENSE.txt",
        }
        overrides = content_overrides or {}
        with zipfile.ZipFile(archive, "w") as package:
            for relative in REQUIRED_FILES:
                content = b"placeholder"
                if relative == "VERSION":
                    content = version.encode()
                elif relative in packaged_text_files:
                    content = (project_root / relative).read_bytes()
                content = overrides.get(relative, content)
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

    def test_rejects_bundled_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(
                Path(directory),
                extra_file="runtime/python.exe",
            )
            with self.assertRaisesRegex(ReleaseVerificationError, "不应携带"):
                verify_archive(archive, "x64", "0.1.0")

    def test_rejects_incomplete_project_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(
                Path(directory), content_overrides={"LICENSE": b"GPL-2.0-only"}
            )
            with self.assertRaisesRegex(ReleaseVerificationError, "完整的 GNU GPL v2"):
                verify_archive(archive, "x64", "0.1.0")

    def test_rejects_incomplete_third_party_notices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(
                Path(directory),
                content_overrides={"THIRD-PARTY-NOTICES.txt": b"Scapy"},
            )
            with self.assertRaisesRegex(ReleaseVerificationError, "缺少必要许可声明"):
                verify_archive(archive, "x64", "0.1.0")

    def test_rejects_incomplete_scapy_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self.make_archive(
                Path(directory),
                content_overrides={
                    "licenses/SCAPY-LICENSE.txt": b"GPL-2.0-only"
                },
            )
            with self.assertRaisesRegex(ReleaseVerificationError, "Scapy GPL-2.0"):
                verify_archive(archive, "x64", "0.1.0")


if __name__ == "__main__":
    unittest.main()
