"""Фабрика Selenium WebDriver (Chrome)."""

from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config import PROFILE_DIR


def create_driver(*, headless: bool = False) -> webdriver.Chrome:
    """Открыть Chrome с отдельным профилем под hh.ru."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")
    # Не ждать полную прогрузку картинок/стилей — достаточно DOM
    options.page_load_strategy = "eager"
    # Без картинок — быстрее страница
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.images": 2,
        },
    )
    if headless:
        options.add_argument("--headless=new")

    # Selenium Manager подтянет chromedriver сам (Selenium 4.6+)
    return webdriver.Chrome(options=options)
