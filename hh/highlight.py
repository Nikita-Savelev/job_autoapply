"""Визуальная подсветка действий бота на странице (для отладки глазами)."""

from __future__ import annotations

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from config import env

_BANNER_ID = "hh-autoapply-banner"
_STYLE_ID = "hh-autoapply-highlight-style"


def visual_enabled() -> bool:
    """По умолчанию включено; выключить: HH_HIGHLIGHT=0."""
    raw = env("HH_HIGHLIGHT", "1").lower()
    return raw not in {"0", "false", "no", "off"}


def ensure_styles(driver: WebDriver) -> None:
    driver.execute_script(
        """
        if (document.getElementById(arguments[0])) return;
        const s = document.createElement('style');
        s.id = arguments[0];
        s.textContent = `
          #hh-autoapply-banner {
            position: fixed !important;
            top: 12px !important;
            left: 50% !important;
            transform: translateX(-50%) !important;
            z-index: 2147483647 !important;
            max-width: min(90vw, 720px) !important;
            padding: 10px 18px !important;
            border-radius: 10px !important;
            background: rgba(15, 23, 42, 0.92) !important;
            color: #f8fafc !important;
            font: 600 15px/1.35 system-ui, sans-serif !important;
            box-shadow: 0 8px 28px rgba(0,0,0,.35) !important;
            pointer-events: none !important;
            text-align: center !important;
          }
          .hh-autoapply-target {
            outline: 4px solid #f59e0b !important;
            outline-offset: 3px !important;
            box-shadow: 0 0 0 6px rgba(245, 158, 11, 0.35),
                        0 0 24px rgba(245, 158, 11, 0.55) !important;
            background-color: rgba(245, 158, 11, 0.12) !important;
            transition: outline 0.15s ease, box-shadow 0.15s ease !important;
          }
        `;
        document.documentElement.appendChild(s);
        """,
        _STYLE_ID,
    )


def show_banner(driver: WebDriver, text: str) -> None:
    if not visual_enabled():
        return
    try:
        ensure_styles(driver)
        driver.execute_script(
            """
            let b = document.getElementById(arguments[0]);
            if (!b) {
              b = document.createElement('div');
              b.id = arguments[0];
              document.documentElement.appendChild(b);
            }
            b.textContent = arguments[1];
            """,
            _BANNER_ID,
            text[:180],
        )
    except Exception:
        pass


def highlight_element(
    driver: WebDriver,
    el: WebElement,
    *,
    label: str = "",
) -> None:
    if not visual_enabled():
        return
    try:
        ensure_styles(driver)
        if label:
            show_banner(driver, label)
        driver.execute_script(
            """
            document.querySelectorAll('.hh-autoapply-target').forEach(n => {
              n.classList.remove('hh-autoapply-target');
            });
            arguments[0].classList.add('hh-autoapply-target');
            """,
            el,
        )
    except Exception:
        pass


def clear_highlight(driver: WebDriver) -> None:
    if not visual_enabled():
        return
    try:
        driver.execute_script(
            """
            document.querySelectorAll('.hh-autoapply-target').forEach(n => {
              n.classList.remove('hh-autoapply-target');
            });
            """
        )
    except Exception:
        pass


def flash_click(
    driver: WebDriver,
    el: WebElement,
    *,
    label: str,
    hold_sec: float = 0.8,
) -> None:
    """Подсветить элемент и показать баннер перед кликом."""
    import time

    if not visual_enabled():
        return
    highlight_element(driver, el, label=f"CLICK: {label}")
    if hold_sec > 0:
        time.sleep(min(hold_sec, 2.0))
