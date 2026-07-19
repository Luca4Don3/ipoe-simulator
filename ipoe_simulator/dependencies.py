from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class DependencyError(RuntimeError):
    pass


ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "release-dependencies.json"
LEGACY_LINUX_RUNTIME = Path("/opt/ipoe-simulator/runtime")
LEGACY_LINUX_STATE = Path("/var/lib/ipoe-simulator")


def release_dependencies() -> dict[str, Any]:
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyError(f"无法读取发布依赖清单 {LOCK_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise DependencyError("发布依赖清单根节点必须是对象")
    return data


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_download(url: str, expected_sha256: str, destination: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        actual = _sha256_file(destination)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise DependencyError(f"依赖下载失败: {exc}") from exc
    if actual.lower() != expected_sha256.lower():
        raise DependencyError(f"依赖 SHA-256 不匹配: 期望 {expected_sha256}，实际 {actual}")


def classify_windows_architecture(
    machine: str,
    pointer_bits: int,
    wow64: str = "",
) -> str:
    normalized = machine.upper()
    native = wow64.upper()
    if pointer_bits == 32 and native in {"AMD64", "X86_64"}:
        return "x64-process-32bit"
    if pointer_bits == 32 and native in {"ARM64", "AARCH64"}:
        return "arm64-process-32bit"
    if normalized in {"AMD64", "X86_64"}:
        return "x64" if pointer_bits == 64 else "x64-process-32bit"
    if normalized in {"ARM64", "AARCH64"}:
        return "arm64" if pointer_bits == 64 else "arm64-process-32bit"
    if normalized in {"X86", "I386", "I686"}:
        return "x86"
    raise DependencyError(f"不支持的 Windows 架构: {normalized or 'unknown'}")


def windows_architecture() -> str:
    if os.name != "nt":
        return platform.machine().lower() or "unknown"
    machine = (platform.machine() or os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
    pointer_bits = struct.calcsize("P") * 8
    return classify_windows_architecture(
        machine,
        pointer_bits,
        os.environ.get("PROCESSOR_ARCHITEW6432", ""),
    )


def normalized_architecture() -> str:
    machine = (platform.machine() or "unknown").lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "aarch64"
    if machine in {"x86", "i386", "i686"}:
        return "x86"
    return machine


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("\"'")
    return values


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:3]) or (0,)


def linux_distribution_status(
    os_release: dict[str, str] | None = None,
    *,
    architecture: str | None = None,
) -> dict[str, Any]:
    release = os_release if os_release is not None else read_os_release()
    distro = release.get("ID", "unknown").lower()
    like = release.get("ID_LIKE", "").lower().split()
    version = release.get("VERSION_ID", "")
    parsed = _version_tuple(version)
    arch = architecture or normalized_architecture()
    family = distro
    minimum: tuple[int, ...] | None = None
    legacy = False
    if distro in {"rhel", "centos"} or any(
        item in {"rhel", "centos", "fedora"} for item in like
    ):
        family = "rhel"
        minimum = (6, 5)
        legacy = parsed < (8,)
    elif distro == "ubuntu" or "ubuntu" in like:
        family = "ubuntu"
        minimum = (12, 4)
        legacy = parsed < (18, 4)
    elif distro in {"sles", "sled"} or "suse" in like:
        family = "sles"
        minimum = (11, 3)
        legacy = parsed < (15,)
    elif distro == "opensuse" or distro.startswith("opensuse"):
        family = "opensuse"
        minimum = (13, 1)
        legacy = parsed < (15,)
    supported = True
    reason = "通用现代 Linux（按能力检测）"
    if minimum is not None and parsed < minimum:
        supported = False
        reason = f"{family} {version or 'unknown'} 低于最低版本 {'.'.join(map(str, minimum))}"
    elif minimum is not None:
        reason = f"{family} {version or 'unknown'}"
    if arch not in {"x86_64", "aarch64"}:
        supported = False
        reason = f"Linux 不支持的架构: {arch}"
    elif legacy and arch != "x86_64":
        supported = False
        reason = f"遗留 Linux 仅支持 x86_64，当前为 {arch}"
    return {
        "id": distro,
        "family": family,
        "version": version,
        "architecture": arch,
        "legacy": legacy,
        "supported": supported,
        "detail": reason,
        "runtime": str(LEGACY_LINUX_RUNTIME) if legacy else sys.executable,
    }


def macos_status(
    version: str | None = None,
    *,
    architecture: str | None = None,
) -> dict[str, Any]:
    product_version = version if version is not None else platform.mac_ver()[0]
    arch = architecture or normalized_architecture()
    major = _version_tuple(product_version)[0]
    supported_versions = {14, 15, 26}
    supported = major in supported_versions and arch in {"x86_64", "aarch64"}
    detail = f"macOS {product_version or 'unknown'} / {arch}"
    if major not in supported_versions:
        detail += "（仅支持 macOS 14、15、26）"
    elif arch not in {"x86_64", "aarch64"}:
        detail += "（仅支持 Intel x86_64 与 Apple Silicon）"
    return {
        "version": product_version,
        "architecture": arch,
        "supported": supported,
        "detail": detail,
    }


def package_manager() -> str | None:
    for command in ("dnf", "yum", "apt-get", "zypper", "pacman"):
        if shutil.which(command):
            return command
    return None


def linux_dependency_install_command(manager: str) -> list[str]:
    commands = {
        "dnf": ["dnf", "install", "-y", "iproute", "tcpdump"],
        "yum": ["yum", "install", "-y", "iproute", "tcpdump"],
        "apt-get": ["apt-get", "install", "-y", "iproute2", "tcpdump"],
        "zypper": [
            "zypper",
            "--non-interactive",
            "install",
            "iproute2",
            "tcpdump",
        ],
        "pacman": ["pacman", "-S", "--needed", "--noconfirm", "iproute2", "tcpdump"],
    }
    try:
        return commands[manager]
    except KeyError as exc:
        raise DependencyError(f"不支持的 Linux 包管理器: {manager}") from exc


def ensure_linux_base_dependencies(auto_install: bool = True) -> dict[str, Any]:
    missing = [command for command in ("ip", "tcpdump") if not shutil.which(command)]
    manager = package_manager()
    if missing and not auto_install:
        command = linux_dependency_install_command(manager) if manager else []
        raise DependencyError(
            f"缺少 Linux 基础依赖: {', '.join(missing)}"
            + (f"; 可执行: {' '.join(command)}" if command else "; 未找到受支持的包管理器")
        )
    if missing:
        if not manager:
            raise DependencyError(
                f"缺少 Linux 基础依赖 {', '.join(missing)}，"
                "且未找到 yum/dnf/apt/zypper/pacman"
            )
        command = linux_dependency_install_command(manager)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "无错误输出").strip()
            raise DependencyError(
                f"Linux 基础依赖安装失败 {command!r}: {detail}"
            )
        still_missing = [
            command for command in ("ip", "tcpdump") if not shutil.which(command)
        ]
        if still_missing:
            raise DependencyError(
                "包管理器返回成功但依赖仍缺失: " + ", ".join(still_missing)
            )
    return {
        "package_manager": manager or "none",
        "ip": shutil.which("ip") or False,
        "tcpdump": shutil.which("tcpdump") or False,
    }


def ensure_scapy(auto_install: bool = True) -> str:
    try:
        import scapy

        return str(getattr(scapy, "__version__", "unknown"))
    except ImportError:
        if not auto_install:
            raise DependencyError("Scapy 未安装")
    pip_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--require-hashes",
            "-r",
            str(ROOT / "requirements.txt"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if pip_result.returncode != 0:
        detail = (pip_result.stderr or pip_result.stdout or "未知 pip 错误").strip()
        raise DependencyError(f"Scapy 自动安装失败: {detail}")
    try:
        import scapy

        return str(getattr(scapy, "__version__", "unknown"))
    except ImportError as exc:
        raise DependencyError("Scapy 安装命令成功但仍无法导入，请检查 Python 环境") from exc


def npcap_path() -> Path:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "Npcap" / "wpcap.dll"


def npcap_status() -> tuple[bool, str]:
    path = npcap_path()
    if not path.exists():
        return False, f"未找到 {path}"
    try:
        ctypes.WinDLL(str(path))
    except OSError as exc:
        return False, f"Npcap 与当前 Python 架构不匹配或 DLL 加载失败: {exc}"
    return True, str(path)


def _authenticode_valid(path: Path) -> bool:
    from .powershell_runtime import get_powershell_runtime
    from .network_backend import NetworkStateError

    script = (
        "$ErrorActionPreference='Stop'; "
        f"(Get-AuthenticodeSignature -LiteralPath '{str(path).replace(chr(39), chr(39) * 2)}').Status"
    )
    try:
        return get_powershell_runtime().run(script, timeout=30).lower() == "valid"
    except NetworkStateError as exc:
        raise DependencyError(f"Npcap Authenticode 校验执行失败: {exc}") from exc


def install_npcap() -> str:
    if os.name != "nt":
        raise DependencyError("Npcap 只能在 Windows 上安装")
    current, detail = npcap_status()
    if current:
        return detail
    lock = release_dependencies()["npcap"]
    installer = Path(tempfile.gettempdir()) / f"ipoe-simulator-npcap-{lock['version']}.exe"
    try:
        _verified_download(str(lock["url"]), str(lock["sha256"]), installer)
        if not _authenticode_valid(installer):
            raise DependencyError("Npcap 安装包签名验证失败，已拒绝执行")
        # Authenticode 的 Valid 状态必须对应锁定的官方发布者。
        from .powershell_runtime import get_powershell_runtime
        publisher = get_powershell_runtime().run(
            f"(Get-AuthenticodeSignature -LiteralPath '{str(installer).replace(chr(39), chr(39) * 2)}').SignerCertificate.Subject",
            timeout=30,
        )
        if str(lock["publisher"]).lower() not in publisher.lower():
            raise DependencyError(f"Npcap 签名发布者不匹配: {publisher or 'unknown'}")
        result = subprocess.run([str(installer), "/S"], capture_output=True, text=True, timeout=300, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知安装器错误").strip()
            raise DependencyError(f"Npcap 安装失败，退出码 {result.returncode}: {detail}")
        current, detail = npcap_status()
        if not current:
            raise DependencyError(f"Npcap 安装器返回成功但驱动仍不可用: {detail}")
        return detail
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise DependencyError(f"Npcap 下载或安装失败: {exc}") from exc
    finally:
        try:
            installer.unlink(missing_ok=True)
        except OSError:
            pass


def is_admin() -> bool:
    if os.name != "nt":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_runtime(auto_install: bool = True, require_admin: bool = True) -> dict[str, object]:
    if sys.version_info < (3, 10):
        raise DependencyError("需要 Python 3.10+；遗留 Linux 必须使用自带 Python 3.11 runtime")
    admin = is_admin()
    if require_admin and not admin:
        if os.name == "nt":
            raise DependencyError("需要管理员权限，请以管理员身份运行")
        raise DependencyError("需要 root/sudo 权限；本程序不会自动提权")
    platform_name = "windows" if os.name == "nt" else sys.platform
    dependencies: dict[str, Any] = {}
    if os.name == "nt":
        architecture = windows_architecture()
        if architecture.endswith("-process-32bit"):
            native = architecture[: -len("-process-32bit")]
            raise DependencyError(
                f"当前是 32 位 Python 运行在 {native} Windows 上，"
                "请安装匹配架构的 Python"
            )
    elif sys.platform == "darwin":
        status = macos_status()
        if not status["supported"]:
            raise DependencyError(str(status["detail"]))
        architecture = str(status["architecture"])
        missing = [
            command
            for command in ("networksetup", "ifconfig", "route")
            if not shutil.which(command)
        ]
        if missing:
            raise DependencyError("缺少 macOS 系统命令: " + ", ".join(missing))
        dependencies = {
            command: shutil.which(command)
            for command in ("networksetup", "ifconfig", "route")
        }
    elif sys.platform.startswith("linux"):
        status = linux_distribution_status()
        if not status["supported"]:
            raise DependencyError(str(status["detail"]))
        architecture = str(status["architecture"])
        if status["legacy"]:
            executable = Path(sys.executable).resolve()
            runtime = LEGACY_LINUX_RUNTIME.resolve()
            if runtime not in executable.parents:
                raise DependencyError(
                    f"遗留 Linux 必须使用 {LEGACY_LINUX_RUNTIME} 下的自带 "
                    f"Python 3.11 + Scapy，当前解释器为 {executable}"
                )
        dependencies = ensure_linux_base_dependencies(auto_install=auto_install)
    else:
        raise DependencyError(f"不支持的操作系统平台: {sys.platform}")

    scapy_version = ensure_scapy(auto_install=auto_install)
    if os.name == "nt":
        npcap_ok, npcap_detail = npcap_status()
        if not npcap_ok and auto_install:
            npcap_detail = install_npcap()
            npcap_ok = True
        if not npcap_ok:
            raise DependencyError(npcap_detail)
        packet_backend = f"Npcap: {npcap_detail}"
    elif sys.platform == "darwin":
        npcap_detail = "system libpcap/BPF"
        packet_backend = npcap_detail
    else:
        npcap_detail = "Scapy PF_PACKET"
        packet_backend = npcap_detail
    return {
        "platform": platform_name,
        "python": platform.python_version(),
        "architecture": architecture,
        "python_machine": platform.machine(),
        "scapy": scapy_version,
        "npcap": npcap_detail,
        "packet_backend": packet_backend,
        "dependencies": dependencies,
        "admin": admin,
    }
