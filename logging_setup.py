"""Логирование: консоль и полный лог каждого запуска в logs/."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from config import ROOT

LOGS_DIR = ROOT / "logs"
_FILE_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | "
    "{name}:{function}:{line} — {message}"
)

_CONFIGURED = False
_BANNER_SINK_ID: int | None = None


def default_log_path() -> Path:
    """Один файл на запуск: logs/20260924_141100_run_mix.log."""
    script = Path(sys.argv[0]).stem or "run"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"{stamp}_{script}.log"


def _add_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        path,
        level="DEBUG",
        format=_FILE_FMT,
        encoding="utf-8",
        enqueue=False,
    )


def setup_logging(*, debug: bool = False, log_file: Path | None = None) -> Path:
    """Консоль: INFO, в --debug ещё и DEBUG. Файл всегда пишет DEBUG.

    ``log_file`` — дополнительная копия (рядом с HTML-дампом). Основной файл
    всегда в ``logs/``.
    """
    global _CONFIGURED, _BANNER_SINK_ID
    logger.remove()
    _BANNER_SINK_ID = None
    level = "DEBUG" if debug else "INFO"
    fmt = (
        "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> — <level>{message}</level>"
    )
    logger.add(sys.stderr, level=level, format=fmt, enqueue=False)

    main_log = default_log_path()
    _add_file(main_log)
    if log_file is not None and log_file.resolve() != main_log.resolve():
        _add_file(log_file)

    # INFO+ → баннер в окне Selenium (если драйвер привязан)
    try:
        from hh.highlight import loguru_banner_sink, visual_enabled

        if visual_enabled():
            _BANNER_SINK_ID = logger.add(
                loguru_banner_sink,
                level="INFO",
                format="{message}",
                enqueue=False,
            )
    except Exception:  # noqa: BLE001
        _BANNER_SINK_ID = None

    _CONFIGURED = True
    logger.info("Лог: {}", main_log)
    logger.debug("Логирование: level={}", level)
    return main_log


def get_logger():
    return logger
