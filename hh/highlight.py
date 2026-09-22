"""Визуальная подсветка действий бота на странице (для отладки глазами)."""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from config import env

_BANNER_ID = "hh-autoapply-banner"
_STYLE_ID = "hh-autoapply-highlight-style-v2"

# Активный драйвер — чтобы INFO-логи дублировать в баннер окна Chrome
_active_driver: WebDriver | None = None
_log_lines: deque[str] = deque(maxlen=2)
_mirroring = False  # защита от рекурсии sink → banner → log
_last_short: str = ""

# Длинный лог → короткая общая подпись для баннера (порядок важен)
_BANNER_RULES: tuple[tuple[str, str | None], ...] = (
    (r"(?i)llm test-answers|отвечаю на.*вопрос", "LLM: жду ответы…"),
    (r"(?i)опросник отправлен", "Опросник отправлен"),
    (r"(?i)submit пропущен|форм[аы] заполнен", "Опросник заполнен"),
    (r"(?i)собираю вопросы|опросник работодателя", "Опросник: вопросы"),
    (r"(?i)ответ на вопрос|заполняю ответ", "Заполняю опросник"),
    (r"(?i)сопроводительн|ввод письма|cover letter|письмо сгенерир", "Письмо"),
    (r"(?i)открываю вакансию|apply open|vacancy tab", "Открываю вакансию"),
    (r"(?i)ищу кнопку|откликнутьс", "Отклик"),
    (r"(?i)друг(ая|ой) стран", "Подтверждаю страну"),
    (r"(?i)чат|chatik", "Письмо в чат"),
    (r"(?i)странниц[аы] поиска|следующ(ая|ей) страниц", "Поиск: страница"),
    (r"(?i)компани[яю]:", "Страница компании"),
    (r"(?i)\bskip\b|скип", "Скип"),
    (r"(?i)apply ok|отклик отправлен|letter_sent", "Готово: отклик"),
    (r"(?i)already|уже отклик", "Уже откликались"),
    (r"(?i)blocked|опросник не", "Ошибка опросника"),
    (r"(?i)review tests|проверк", "Проверка теста"),
    (r"(?i)sweep|список чатов|open chat|composer|исходящ|awaiting_them|typing delay", "Чат"),
    (r"(?i)escalate|needs_human|bot_paused", "Чат: эскалация"),
    # шум — не показывать в баннере
    (r"(?i)test answer \[|пререквизит|вырезал сум|убрал сум|убрал формулир|Q\[\d+\]", None),
    (r"(?i)TARGET_ROLE|RESUME:|SEARCH_URL|pause=|debug:|DB:|counts in|HTML\+log|Файл лога", None),
    (r"(?i)company saved|company updated|Postgres|резюме загружено", None),
)


def _short_banner_text(text: str) -> str | None:
    """Сжать лог до короткой общей фразы; None = не показывать."""
    raw = " ".join(str(text).split())
    if not raw:
        return None
    # явные CLICK: из flash_click
    if raw.upper().startswith("CLICK:"):
        label = raw.split(":", 1)[-1].strip()
        short = label.split("\n")[0][:40]
        return f"Клик: {short}" if short else "Клик"
    for pattern, label in _BANNER_RULES:
        if re.search(pattern, raw):
            return label
    # общее укорочение неизвестных INFO
    if len(raw) <= 48:
        return raw
    return raw[:45] + "…"


def visual_enabled() -> bool:
    """По умолчанию включено; выключить: HH_HIGHLIGHT=0."""
    raw = env("HH_HIGHLIGHT", "1").lower()
    return raw not in {"0", "false", "no", "off"}


def set_visual_driver(driver: WebDriver | None) -> None:
    """Привязать браузер для зеркала логов (вызывать после create_driver)."""
    global _active_driver
    _active_driver = driver
    if driver is None:
        _log_lines.clear()


def ensure_styles(driver: WebDriver) -> None:
    driver.execute_script(
        """
        if (document.getElementById(arguments[0])) return;
        const s = document.createElement('style');
        s.id = arguments[0];
        s.textContent = `
          #hh-autoapply-banner {
            position: fixed !important;
            top: 10px !important;
            left: 50% !important;
            transform: translateX(-50%) !important;
            z-index: 2147483647 !important;
            max-width: min(92vw, 820px) !important;
            max-height: 140px !important;
            overflow: hidden !important;
            padding: 10px 16px !important;
            border-radius: 10px !important;
            background: rgba(15, 23, 42, 0.94) !important;
            color: #f8fafc !important;
            font: 500 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas,
                  system-ui, sans-serif !important;
            box-shadow: 0 8px 28px rgba(0,0,0,.4) !important;
            pointer-events: none !important;
            text-align: left !important;
            white-space: pre-wrap !important;
            word-break: break-word !important;
            border: 1px solid rgba(148, 163, 184, 0.35) !important;
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


def _render_banner(driver: WebDriver, text: str) -> None:
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
            text,
        )
    except Exception:
        pass


def show_banner(driver: WebDriver, text: str) -> None:
    if not visual_enabled():
        return
    short = _short_banner_text(text)
    if short is None:
        return
    _render_banner(driver, short)


def push_status(text: str) -> None:
    """Добавить короткий статус в ленту и отрисовать на активном драйвере."""
    global _mirroring, _last_short
    if not visual_enabled() or _active_driver is None or _mirroring:
        return
    short = _short_banner_text(text)
    if short is None:
        return
    if short == _last_short:
        return
    _last_short = short
    _log_lines.append(short)
    _mirroring = True
    try:
        _render_banner(_active_driver, "\n".join(_log_lines))
    finally:
        _mirroring = False


def loguru_banner_sink(message: Any) -> None:
    """Sink loguru: INFO+ из наших модулей → баннер в Chrome."""
    if not visual_enabled() or _active_driver is None or _mirroring:
        return
    try:
        record = message.record
        if record["level"].no < 20:  # ниже INFO
            return
        name = str(record["name"] or "")
        skip_prefixes = (
            "urllib3",
            "selenium",
            "httpx",
            "httpcore",
            "asyncio",
            "WDM",
            "charset_normalizer",
        )
        if name.startswith(skip_prefixes):
            return
        # только наш код / явные действия
        allow = (
            name.startswith(
                (
                    "hh",
                    "pipeline",
                    "matcher",
                    "cover_letter",
                    "db",
                    "browser",
                    "debug",
                    "config",
                    "__main__",
                )
            )
            or name in {"run", "review_tests", "login", "logging_setup"}
        )
        if not allow:
            return
        text = str(record["message"])
        push_status(text)
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
