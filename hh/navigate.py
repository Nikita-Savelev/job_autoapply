"""Навигация с опциональным HTML-дампом (debug)."""

from __future__ import annotations

from pathlib import Path

from pathlib import Path

from loguru import logger
from selenium.webdriver.remote.webdriver import WebDriver

from debug.page_dump import PageDumper


def go(
    driver: WebDriver,
    url: str,
    *,
    dumper: PageDumper | None = None,
    label: str = "",
) -> Path | None:
    """driver.get + сохранение страницы, если включён debug."""
    logger.debug("navigate go label={!r} url={}", label, url)
    driver.get(url)
    if dumper is None:
        return None
    path = dumper.save(driver, label=label or "navigate", url=url)
    logger.debug("HTML dump → {}", path)
    return path


def dump_current(
    driver: WebDriver,
    dumper: PageDumper | None,
    *,
    label: str = "current",
) -> Path | None:
    if dumper is None:
        return None
    path = dumper.save(driver, label=label)
    logger.debug("HTML dump (current) → {}", path)
    return path
