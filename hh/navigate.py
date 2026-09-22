"""Навигация с опциональным HTML-дампом (debug)."""

from __future__ import annotations

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
    chat_id: str | None = None,
) -> Path | None:
    """driver.get + сохранение страницы, если включён debug."""
    logger.debug("navigate go label={!r} url={} chat_id={}", label, url, chat_id)
    driver.get(url)
    if dumper is None:
        return None
    return dumper.save(
        driver, label=label or "navigate", url=url, chat_id=chat_id
    )


def dump_current(
    driver: WebDriver,
    dumper: PageDumper | None,
    *,
    label: str = "current",
    chat_id: str | None = None,
) -> Path | None:
    if dumper is None:
        return None
    return dumper.save(driver, label=label, chat_id=chat_id)
