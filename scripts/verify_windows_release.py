#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
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
    "SECURITY.md",
    "THIRD-PARTY-NOTICES.txt",
    "VERSION",
    "config.example.json",
    "coordinator.py",
    "run.cmd",
    "release-dependencies.json",
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
    ".env",
    "AGENTS.md",
    "HANDOFF.md",
    "ipoe-simulator.log",
    "ipoedhcp_config.json",
    "network-recovery.json",
    "run.sh",
}
FORBIDDEN_SUFFIXES = {
    ".cookie",
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}


class ReleaseVerificationError(RuntimeError):
    pass


def _decode_package_text(package: zipfile.ZipFile, path: str) -> str:
    try:
        return package.read(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseVerificationError(f"{path} 不是有效 UTF-8 文本") from exc


def _verify_license_files(package: zipfile.ZipFile, expected_root: str) -> None:
    project_license_path = f"{expected_root}/LICENSE"
    project_license = _decode_package_text(package, project_license_path)
    required_gpl_v2_markers = (
        "GNU GENERAL PUBLIC LICENSE",
        "Version 2, June 1991",
        "TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION",
        "END OF TERMS AND CONDITIONS",
        "How to Apply These Terms to Your New Programs",
    )
    if len(project_license) < 15_000 or any(
        marker not in project_license for marker in required_gpl_v2_markers
    ):
        raise ReleaseVerificationError("LICENSE 不是完整的 GNU GPL v2 文本")

    notices_path = f"{expected_root}/THIRD-PARTY-NOTICES.txt"
    notices = _decode_package_text(package, notices_path)
    required_notice_markers = (
        "Python Software Foundation License Version 2",
        "Scapy is distributed under GPL-2.0-only",
        "project source is licensed under GPL-2.0-only",
        "Npcap",
        "not distributed with this project",
    )
    if any(marker not in notices for marker in required_notice_markers):
        raise ReleaseVerificationError("THIRD-PARTY-NOTICES.txt 缺少必要许可声明")

    scapy_license_path = f"{expected_root}/licenses/SCAPY-LICENSE.txt"
    scapy_license = _decode_package_text(package, scapy_license_path)
    required_scapy_markers = required_gpl_v2_markers + (
        "Scapy 2.7.0",
        "https://github.com/secdev/scapy",
        "Philippe Biondi and the Scapy project contributors",
        "GPL-2.0-only",
    )
    if len(scapy_license) < 15_000 or any(
        marker not in scapy_license for marker in required_scapy_markers
    ):
        raise ReleaseVerificationError(
            "licenses/SCAPY-LICENSE.txt 不是完整的 Scapy GPL-2.0 许可证文本"
        )


def read_pe_machine(executable: bytes) -> int:
    if executable[:2] != b"MZ":
        raise ReleaseVerificationError("runtime/python.exe 不是有效的 PE 文件")
    pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
    if executable[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ReleaseVerificationError("runtime/python.exe 缺少 PE 签名")
    return struct.unpack_from("<H", executable, pe_offset + 4)[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(
    archive: Path,
    architecture: str,
    version: str,
    *,
    execute_runtime: bool = False,
) -> None:
    expected_root = f"ipoe-simulator-v{version}-windows-{architecture}"
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
            if relative.name.startswith(".env."):
                raise ReleaseVerificationError(f"ZIP 包含禁止配置: {relative}")
            if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
                raise ReleaseVerificationError(f"ZIP 包含禁止文件类型: {relative}")

        runtime_files = [
            path for path in relative_files if path.startswith("runtime/")
        ]
        if runtime_files:
            raise ReleaseVerificationError(
                f"轻量包不应携带 Python runtime: {runtime_files[0]}"
            )

        _verify_license_files(package, expected_root)
        version_value = _decode_package_text(
            package, f"{expected_root}/VERSION"
        ).strip()
        if version_value != version:
            raise ReleaseVerificationError(
                f"VERSION 应为 {version!r}，实际为 {version_value!r}"
            )
    if execute_runtime:
        raise ReleaseVerificationError(
            "轻量包不支持构建机执行 runtime；请在 Windows 首次启动时验证自动安装"
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
    print(f"验证通过: {args.archive} SHA-256={sha256_file(args.archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
