"""统一的 CLI 日志配置。"""

from __future__ import annotations

import logging
import os
import sys


LOGGER_NAME = "ipoe-simulator"
LOG_LEVELS = ("DEBUG", "INFO")
_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    *, level_name: str | None = None, verbose: bool = False
) -> None:
    """将应用日志统一写入 stderr，默认只输出 INFO 及以上业务事件。"""

    selected = level_name or os.environ.get(
        "IPOE_LOG_LEVEL", "DEBUG" if verbose else "INFO"
    )
    selected = selected.upper()
    if selected not in LOG_LEVELS:
        selected = "INFO"
    level = getattr(logging, selected.upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{component}")


logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())
