#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PE_MACHINE = {
    "x86": 0x014C,
    "x64": 0x8664,
    "arm64": 0xAA64,
}
REQUIRED_FILES = {
    "README.md",
    "LICENSE",
    "RELEASE_NOTES.md",
    "THIRD-PARTY-NOTICES.txt",
    "VERSION",
    "config.example.json",
    "coordinator.py",
    "run.cmd",
    "release-dependencies.json",
    "runtime/python.exe",
    "runtime/LICENSE.txt",
    "runtime/Lib/site-packages/scapy/__init__.py",
    "licenses/SCAPY-LICENSE.txt",
    "scripts/windows_launcher.ps1",
}
FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".temp",
    "__pycache__",
    "tests",
}
FORBIDDEN_NAMES = {
    "AGENTS.md",
    "HANDOFF.md",
    "ipoe-simulator.log",
    "ipoedhcp_config.json",
    "run.sh",
}


class ReleaseVerificationError(RuntimeError):
    pass


def read_pe_machine(executable: bytes) -> int:
    if executable[:2] != b"MZ":
        raise ReleaseVerificationError("runtime/python.exe 不是有效的 PE 文件")
    pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
    if executable[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ReleaseVerificationError("runtime/python.exe 缺少 PE 签名")
    return struct.unpack_from("<H", executable, pe_offset + 4)[0]


def verify_archive(
    archive: Path,
    architecture: str,
    version: str,
    *,
    execute_runtime: bool = False,
) -> None:
    expected_root = f"ipoe-simulator-v{version}-windows-{architecture}"
    expected_machine = PE_MACHINE[architecture]
    with zipfile.ZipFile(archive) as package:
        files = {
            PurePosixPath(name)
            for name in package.namelist()
            if name and not name.endswith("/")
        }
        roots = {path.parts[0] for path in files}
        if roots != {expected_root}:
            raise ReleaseVerificationError(
                f"ZIP 顶层目录应为 {expected_root!r}，实际为 {sorted(roots)!r}"
            )
        relative_files = {
            PurePosixPath(*path.parts[1:]).as_posix()
            for path in files
        }
        missing = sorted(REQUIRED_FILES - relative_files)
        if missing:
            raise ReleaseVerificationError(f"ZIP 缺少文件: {', '.join(missing)}")
        for path in files:
            relative = PurePosixPath(*path.parts[1:])
            if any(part in FORBIDDEN_PARTS for part in relative.parts):
                raise ReleaseVerificationError(f"ZIP 包含禁止目录: {relative}")
            if relative.name in FORBIDDEN_NAMES:
                raise ReleaseVerificationError(f"ZIP 包含禁止文件: {relative}")
            if relative.suffix in {".pyc", ".log"}:
                raise ReleaseVerificationError(f"ZIP 包含禁止文件类型: {relative}")

        python_path = f"{expected_root}/runtime/python.exe"
        actual_machine = read_pe_machine(package.read(python_path))
        if actual_machine != expected_machine:
            raise ReleaseVerificationError(
                "runtime/python.exe 架构不匹配: "
                f"期望 0x{expected_machine:04x}，实际 0x{actual_machine:04x}"
            )

        version_value = package.read(f"{expected_root}/VERSION").decode("utf-8").strip()
        if version_value != version:
            raise ReleaseVerificationError(
                f"VERSION 应为 {version!r}，实际为 {version_value!r}"
            )

    if execute_runtime:
        if os.name != "nt":
            raise ReleaseVerificationError("--execute-runtime 只能在 Windows 上使用")
        with tempfile.TemporaryDirectory(prefix="ipoe-release-verify-") as directory:
            with zipfile.ZipFile(archive) as package:
                package.extractall(directory)
            root = Path(directory) / expected_root
            result = subprocess.run(
                [
                    str(root / "runtime" / "python.exe"),
                    "-c",
                    "import platform, scapy; "
                    "print(platform.machine()); print(scapy.__version__)",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "无错误输出").strip()
                raise ReleaseVerificationError(
                    f"包内 Python/Scapy 执行验证失败: {detail}"
                )
            if "2.6.1" not in result.stdout.splitlines():
                raise ReleaseVerificationError(
                    f"包内 Scapy 版本不是 2.6.1: {result.stdout.strip()}"
                )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="验证 Windows Release ZIP")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--architecture", required=True, choices=sorted(PE_MACHINE))
    parser.add_argument("--version", required=True)
    parser.add_argument("--execute-runtime", action="store_true")
    args = parser.parse_args()
    try:
        verify_archive(
            args.archive,
            args.architecture,
            args.version,
            execute_runtime=args.execute_runtime,
        )
    except (OSError, zipfile.BadZipFile, ReleaseVerificationError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(f"验证通过: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
