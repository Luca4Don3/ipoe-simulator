from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from ipoe_simulator.app_logging import configure_logging, get_logger


class AppLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_logging(level_name="INFO")

    def test_info_is_default_and_debug_requires_explicit_level(self) -> None:
        stream = io.StringIO()
        with patch("ipoe_simulator.app_logging.sys.stderr", stream):
            configure_logging()
            logger = get_logger("test")
            logger.debug("debug-hidden")
            logger.info("info-visible")

        output = stream.getvalue()
        self.assertNotIn("debug-hidden", output)
        self.assertIn("info-visible", output)
        self.assertIn("INFO", output)

        stream = io.StringIO()
        with patch("ipoe_simulator.app_logging.sys.stderr", stream):
            configure_logging(level_name="DEBUG")
            get_logger("test").debug("debug-visible")
        self.assertIn("debug-visible", stream.getvalue())

    def test_unknown_environment_level_falls_back_to_info(self) -> None:
        stream = io.StringIO()
        with patch("ipoe_simulator.app_logging.sys.stderr", stream), patch.dict(
            "ipoe_simulator.app_logging.os.environ",
            {"IPOE_LOG_LEVEL": "WARNING"},
            clear=False,
        ):
            configure_logging()
            get_logger("test").info("info-visible")
        self.assertIn("info-visible", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
