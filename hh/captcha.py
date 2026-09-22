"""Детект капчи hh.ru и ожидание ручного решения."""

from __future__ import annotations

import time

from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from hh import selectors as sel
from hh.highlight import show_banner, visual_enabled
from notify.telegram import send_telegram

# Сколько ждать ручного решения (сек). 0 = без лимита.
DEFAULT_CAPTCHA_WAIT_SEC = 3600.0
_POLL_SEC = 1.5
_LOG_EVERY_SEC = 30.0


class CaptchaTimeout(TimeoutError):
    """Капча не исчезла за отведённое время."""


def captcha_visible(driver: WebDriver) -> bool:
    """True, если на экране видна капча hh (картинка / поле ввода)."""
    for css in sel.CAPTCHA_MARKERS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if el.is_displayed():
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return False


def resolve_captcha_if_present(
    driver: WebDriver,
    *,
    context: str = "",
    timeout_sec: float = DEFAULT_CAPTCHA_WAIT_SEC,
) -> bool:
    """Если капча видна — уведомить в Telegram и ждать, пока пользователь решит.

    Returns:
        True — капча была и исчезла; False — капчи не было.
    Raises:
        CaptchaTimeout — не решили вовремя.
    """
    if not captcha_visible(driver):
        return False

    url = ""
    try:
        url = driver.current_url or ""
    except Exception:  # noqa: BLE001
        pass

    msg_lines = [
        "⚠️ hh_autoapply: появилась капча",
        "Реши её в открытом Chrome — прогон ждёт и продолжит сам.",
    ]
    if context:
        msg_lines.append(f"Контекст: {context}")
    if url:
        msg_lines.append(f"URL: {url}")
    text = "\n".join(msg_lines)

    logger.warning("КАПЧА обнаружена — жду ручного решения. {}", context or url)
    if visual_enabled():
        show_banner(driver, "КАПЧА — реши в Chrome, жду…")
    send_telegram(text)

    deadline = (
        None if timeout_sec <= 0 else (time.time() + float(timeout_sec))
    )
    last_log = 0.0
    while captcha_visible(driver):
        now = time.time()
        if deadline is not None and now >= deadline:
            logger.error("Капча не решена за {:.0f}s — стоп", timeout_sec)
            send_telegram(
                "⛔ hh_autoapply: капча не решена вовремя, прогон остановлен."
            )
            raise CaptchaTimeout(
                f"Капча не исчезла за {timeout_sec:.0f}s ({context or url})"
            )
        if now - last_log >= _LOG_EVERY_SEC:
            left = ""
            if deadline is not None:
                left = f", осталось ~{int(deadline - now)}s"
            logger.warning("Всё ещё капча — жду решения{}", left)
            if visual_enabled():
                show_banner(driver, "КАПЧА — всё ещё жду…")
            last_log = now
        time.sleep(_POLL_SEC)

    logger.info("Капча снята — продолжаю")
    if visual_enabled():
        show_banner(driver, "Капча OK — продолжаю")
    time.sleep(1.0)
    return True
