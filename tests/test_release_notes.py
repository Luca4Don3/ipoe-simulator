from __future__ import annotations

import unittest

from scripts.extract_release_notes import extract_release_notes


class ReleaseNotesTests(unittest.TestCase):
    def test_extracts_only_requested_version(self) -> None:
        notes = "# IPoE Simulator v1.2.3\n\ncurrent\n\n---\n\n# IPoE Simulator v1.2.2\n\nold\n"
        self.assertEqual(extract_release_notes(notes, "1.2.3"), "current\n")

    def test_missing_duplicate_and_empty_sections_fail(self) -> None:
        cases = (
            ("# IPoE Simulator v1.0.0\n\nbody\n", "1.0.1"),
            ("# IPoE Simulator v1.0.0\n\na\n---\n# IPoE Simulator v1.0.0\n\nb\n", "1.0.0"),
            ("# IPoE Simulator v1.0.0\n\n---\n", "1.0.0"),
        )
        for notes, version in cases:
            with self.subTest(notes=notes):
                with self.assertRaises(ValueError):
                    extract_release_notes(notes, version)


if __name__ == "__main__":
    unittest.main()
