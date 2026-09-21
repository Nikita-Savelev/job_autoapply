"""Логирование: в --debug — подробный DEBUG в консоль и в файл прогона."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_CONFIGURED = False


def setup_logging(*, debug: bool = False, log_file: Path | None = None) -> None:
    global _CONFIGURED
    logger.remove()
    level = "DEBUG" if debug else "INFO"
    fmt = (
        "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> — <level>{message}</level>"
    )
    logger.add(sys.stderr, level=level, format=fmt, enqueue=False)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} — {message}",
            encoding="utf-8",
            enqueue=False,
        )
        logger.debug("Файл лога: {}", log_file)

    _CONFIGURED = True
    logger.debug("Логирование: level={}", level)


def get_logger():
    return logger
