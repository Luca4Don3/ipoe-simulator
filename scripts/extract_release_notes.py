from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract_release_notes(notes: str, version: str) -> str:
    heading = re.compile(rf"^# IPoE Simulator v{re.escape(version)}\s*$", re.MULTILINE)
    matches = list(heading.finditer(notes))
    if len(matches) != 1:
        raise ValueError(f"版本 v{version} 标题必须且只能出现一次，实际 {len(matches)} 次")
    body_start = matches[0].end()
    separator = re.search(r"^---\s*$", notes[body_start:], re.MULTILINE)
    body_end = body_start + separator.start() if separator else len(notes)
    body = notes[body_start:body_end].strip()
    if not body:
        raise ValueError(f"版本 v{version} 的 Release Notes 正文为空")
    return body + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="提取单个版本的 Release Notes")
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = args.version_file.read_text(encoding="utf-8").strip()
    if not version:
        parser.error("VERSION 为空")
    try:
        body = extract_release_notes(
            args.notes_file.read_text(encoding="utf-8"),
            version,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
