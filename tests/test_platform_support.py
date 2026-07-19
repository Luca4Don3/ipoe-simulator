from __future__ import annotations

import importlib
import unittest

from ipoe_simulator.dependencies import (
    DependencyError,
    classify_windows_architecture,
    linux_distribution_status,
    macos_status,
    validate_python_version,
)


class ArchitectureTests(unittest.TestCase):
    def test_python_supported_boundaries(self) -> None:
        self.assertEqual(validate_python_version((3, 9, 0)), "3.9.0")
        self.assertEqual(validate_python_version((3, 14, 99)), "3.14.99")

    def test_python_rejects_versions_outside_supported_range(self) -> None:
        for version in ((3, 8, 20), (3, 15, 0), (2, 7, 18), (4, 0, 0)):
            with self.subTest(version=version):
                with self.assertRaisesRegex(DependencyError, "需要 Python 3.9–3.14"):
                    validate_python_version(version)

    def test_windows_three_architectures(self) -> None:
        self.assertEqual(classify_windows_architecture("x86", 32), "x86")
        self.assertEqual(classify_windows_architecture("AMD64", 64), "x64")
        self.assertEqual(classify_windows_architecture("ARM64", 64), "arm64")
        self.assertEqual(
            classify_windows_architecture("x86", 32, "AMD64"),
            "x64-process-32bit",
        )

    def test_supported_macos_matrix(self) -> None:
        for version in ("14.7", "15.5", "26.0"):
            for architecture in ("x86_64", "aarch64"):
                self.assertTrue(
                    macos_status(version, architecture=architecture)["supported"]
                )
        self.assertFalse(
            macos_status("13.7", architecture="x86_64")["supported"]
        )

    def test_linux_minimum_and_architecture_matrix(self) -> None:
        cases = (
            ({"ID": "centos", "VERSION_ID": "6.5"}, True, True),
            ({"ID": "ubuntu", "VERSION_ID": "12.04"}, True, True),
            ({"ID": "sles", "VERSION_ID": "11.3"}, True, True),
            ({"ID": "opensuse", "VERSION_ID": "13.1"}, True, True),
            ({"ID": "ubuntu", "VERSION_ID": "24.04"}, True, False),
        )
        for release, supported, legacy in cases:
            status = linux_distribution_status(
                release,
                architecture="x86_64",
            )
            self.assertEqual(status["supported"], supported)
            self.assertEqual(status["legacy"], legacy)
        self.assertFalse(
            linux_distribution_status(
                {"ID": "centos", "VERSION_ID": "6.5"},
                architecture="aarch64",
            )["supported"]
        )

    def test_cross_platform_modules_import(self) -> None:
        for module in (
            "ipoe_simulator.windows_network",
            "ipoe_simulator.macos_network",
            "ipoe_simulator.linux_network",
            "ipoe_simulator.platform_network",
        ):
            self.assertIsNotNone(importlib.import_module(module))


if __name__ == "__main__":
    unittest.main()
