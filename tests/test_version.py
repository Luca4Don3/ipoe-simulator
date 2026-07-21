from __future__ import annotations

import unittest
from pathlib import Path

from ipoe_simulator import __version__


class VersionTests(unittest.TestCase):
    def test_package_version_matches_version_file(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), __version__)


if __name__ == "__main__":
    unittest.main()
