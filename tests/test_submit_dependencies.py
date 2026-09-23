from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import submit_dependencies as sd  # noqa: E402


def _dry_run_payload(argv: list[str], env: dict[str, str]) -> dict:
    base_env = {
        "GITHUB_REPOSITORY": "Luca4Don3/ipoe-simulator",
        "GITHUB_SHA": "a9d759d3e22e8d2d3e5cc34a3f420dd38510ab84",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    base_env.update(env)
    buffer = io.StringIO()
    with mock.patch.dict(os.environ, base_env, clear=False):
        with redirect_stdout(buffer):
            code = sd.main(["--dry-run", "--root", str(ROOT), *argv])
    assert code == 0, code
    return json.loads(buffer.getvalue())


class SubmitDependenciesCorrelatorTests(unittest.TestCase):
    def test_correlator_is_stable_across_run_attempts_and_run_ids(self) -> None:
        first = _dry_run_payload(
            ["--run-id", "111"],
            {"GITHUB_RUN_ATTEMPT": "1"},
        )
        second = _dry_run_payload(
            ["--run-id", "222"],
            {"GITHUB_RUN_ATTEMPT": "2"},
        )
        self.assertEqual(first["job"]["correlator"], "Dependency submission-1")
        self.assertEqual(second["job"]["correlator"], "Dependency submission-1")
        self.assertEqual(first["job"]["correlator"], second["job"]["correlator"])
        self.assertNotEqual(first["job"]["id"], second["job"]["id"])

    def test_correlator_ignores_workflow_and_attempt_environment(self) -> None:
        payload = _dry_run_payload(
            ["--run-id", "333"],
            {
                "GITHUB_RUN_ATTEMPT": "7",
                "GITHUB_WORKFLOW": "some-other-workflow",
            },
        )
        self.assertEqual(payload["job"]["correlator"], sd.CORRELATOR)
        self.assertEqual(payload["job"]["correlator"], "Dependency submission-1")
        self.assertEqual(payload["job"]["id"], "333")

    def test_correlator_constant_does_not_embed_run_attempt(self) -> None:
        self.assertEqual(sd.CORRELATOR, "Dependency submission-1")
        self.assertFalse(sd.CORRELATOR.endswith("-2"))
        payload = _dry_run_payload(
            ["--run-id", "444"],
            {"GITHUB_RUN_ATTEMPT": "3"},
        )
        correlator = payload["job"]["correlator"]
        self.assertEqual(correlator, "Dependency submission-1")
        self.assertNotIn("444", correlator)
        self.assertNotIn("-3", correlator)

    def test_lock_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory)
            (broken / "release-dependencies.json").write_text(
                (ROOT / "release-dependencies.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (broken / "requirements.txt").write_text(
                "scapy==9.9.9 --hash=sha256:"
                + ("0" * 64)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                sd.build_snapshot(
                    broken,
                    sha="a" * 40,
                    ref="refs/heads/main",
                    job_id="1",
                    job_url="",
                    correlator=sd.CORRELATOR,
                    detector_url="https://github.com/example/repo",
                )


if __name__ == "__main__":
    unittest.main()
