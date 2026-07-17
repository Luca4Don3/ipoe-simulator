#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

from ipoe_simulator.platform_network import NetworkStateError, restore_from_journal


SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF


def wait_for_parent(pid: int) -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, INFINITE)
    finally:
        kernel32.CloseHandle(handle)


def wait_for_parent_pipe(fd: int) -> None:
    if os.name == "nt":
        return
    try:
        while os.read(fd, 4096):
            pass
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="IPoE 网卡异常恢复 watchdog")
    parent = parser.add_mutually_exclusive_group(required=True)
    parent.add_argument("--parent-pid", type=int)
    parent.add_argument("--parent-fd", type=int)
    parser.add_argument("--journal", required=True)
    args = parser.parse_args()
    if os.name == "nt":
        if args.parent_pid is None:
            parser.error("Windows watchdog 需要 --parent-pid")
        wait_for_parent(args.parent_pid)
    else:
        if args.parent_fd is None:
            parser.error("POSIX watchdog 需要 --parent-fd")
        wait_for_parent_pipe(args.parent_fd)
    journal = Path(args.journal)
    if not journal.exists():
        return 0
    try:
        restore_from_journal(journal)
        return 0
    except NetworkStateError as exc:
        error_path = journal.parent / "recovery-watchdog-error.log"
        try:
            error_path.write_text(str(exc) + "\n", encoding="utf-8")
        except OSError:
            pass
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
