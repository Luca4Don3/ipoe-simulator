#!/usr/bin/env python3
"""Submit locked dependencies to the GitHub dependency graph.

The repository pins Scapy with ``scapy==<version> --hash=sha256:<digest>`` in
``requirements.txt``. The GitHub pip parser does not resolve a version from
that hash-checking form, so the package appears in the graph without a
version and never receives vulnerability matching. This script reads the
already-locked Scapy version and digest from ``release-dependencies.json``,
verifies they match ``requirements.txt``, and submits a dependency snapshot
that carries the explicit ``pkg:pypi/scapy@<version>`` purl.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "https://api.github.com"
SNAPSHOT_VERSION = 0
MANIFEST_NAME = "requirements.txt"
DETECTOR_NAME = "ipoe-simulator-release-dependencies"


def _scapy_lock(root: Path) -> tuple[str, str]:
    lock = json.loads((root / "release-dependencies.json").read_text(encoding="utf-8"))
    scapy = lock["scapy"]
    return str(scapy["version"]), str(scapy["sha256"]).lower()


def _scapy_requirement(root: Path) -> tuple[str, str]:
    text = (root / "requirements.txt").read_text(encoding="utf-8")
    version = re.search(r"^scapy==([0-9][^\s\\#]*)", text, re.MULTILINE)
    digest = re.search(r"sha256:([0-9a-fA-F]{64})", text)
    if version is None or digest is None:
        raise SystemExit("requirements.txt 缺少 scapy== 版本或 sha256 锁定")
    return version.group(1), digest.group(1).lower()


def build_snapshot(
    root: Path,
    *,
    sha: str,
    ref: str,
    job_id: str,
    job_url: str,
    correlator: str,
    detector_url: str,
    scanned: str | None = None,
) -> dict:
    lock_version, lock_digest = _scapy_lock(root)
    req_version, req_digest = _scapy_requirement(root)
    if lock_version != req_version:
        raise SystemExit(
            f"Scapy 版本不一致: release-dependencies.json={lock_version} "
            f"requirements.txt={req_version}"
        )
    if lock_digest != req_digest:
        raise SystemExit("Scapy SHA-256 不一致: release-dependencies.json 与 requirements.txt")
    purl = f"pkg:pypi/scapy@{lock_version}"
    return {
        "version": SNAPSHOT_VERSION,
        "sha": sha,
        "ref": ref,
        "job": {
            "correlator": correlator,
            "id": job_id,
            "html_url": job_url,
        },
        "detector": {
            "name": DETECTOR_NAME,
            "version": lock_version,
            "url": detector_url,
        },
        "manifests": {
            MANIFEST_NAME: {
                "name": MANIFEST_NAME,
                "file": {"source_location": MANIFEST_NAME},
                "resolved": {
                    purl: {
                        "package_url": purl,
                        "relationship": "direct",
                        "scope": "runtime",
                        "dependencies": [],
                    }
                },
            }
        },
        "scanned": scanned or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def submit(api_url: str, repo: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/repos/{repo}/dependency-graph/snapshots",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": DETECTOR_NAME,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"依赖图提交失败 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"依赖图提交失败: {exc.reason}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提交依赖图快照")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL))
    parser.add_argument("--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", "dependency-submission"))
    parser.add_argument("--dry-run", action="store_true", help="只打印快照，不提交")
    args = parser.parse_args(argv)

    if not args.sha or not args.ref:
        parser.error("--sha 与 --ref 必填（或提供 GITHUB_SHA/GITHUB_REF）")
    if not args.repo and not args.dry_run:
        parser.error("--repo 必填（或提供 GITHUB_REPOSITORY）")

    job_url = f"{args.server_url}/{args.repo}/actions/runs/{args.run_id}" if args.repo else ""
    payload = build_snapshot(
        args.root,
        sha=args.sha,
        ref=args.ref,
        job_id=args.run_id,
        job_url=job_url,
        correlator=f"{args.workflow}-{args.run_attempt}",
        detector_url=f"{args.server_url}/{args.repo}" if args.repo else args.server_url,
    )

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.token:
        parser.error("--token 必填（或提供 GITHUB_TOKEN）")

    result = submit(args.api_url, args.repo, args.token, payload)
    if result.get("result") not in (None, "SUCCESS"):
        raise SystemExit(f"依赖图提交未成功: {result}")
    print(f"依赖图快照已提交: id={result.get('id')} result={result.get('result')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
