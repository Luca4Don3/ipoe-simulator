"""统一的 CLI 日志配置。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path


LOGGER_NAME = "ipoe-simulator"
LOG_LEVELS = ("DEBUG", "INFO")
LOG_FILE_NAME = "ipoe-simulator.log"
_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    *,
    level_name: str | None = None,
    log_directory: str | os.PathLike[str] | None = None,
    verbose: bool = False,
) -> None:
    """将应用日志写入 stderr 和指定目录的 UTF-8 日志文件。"""

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
    for existing in logger.handlers[:]:
        logger.removeHandler(existing)
        existing.close()
    logger.setLevel(level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    if log_directory is not None:
        directory = Path(log_directory).expanduser().resolve()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LoggingError(f"无法创建日志目录 {directory}: {exc}") from exc
        log_path = directory / LOG_FILE_NAME
        try:
            file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            )
            if os.name != "nt":
                os.chmod(log_path, 0o600)
        except OSError as exc:
            raise LoggingError(f"无法创建日志文件 {log_path}: {exc}") from exc
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.propagate = False


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{component}")


logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


class LoggingError(RuntimeError):
    pass
