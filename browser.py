"""Фабрика Selenium WebDriver (Chrome)."""

from __future__ import annotations

from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver

from config import PROFILE_DIR

# Блокируем обычные картинки (CDN/расширения), но НЕ /captcha/picture
# (у капчи нет .png/.jpg в URL — она проходит).
_BLOCKED_IMAGE_URLS = (
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.webp",
    "*.svg",
    "*.ico",
    "*.avif",
    "*.bmp",
    "*://hhcdn.ru/*",
    "*://*.hhcdn.ru/*",
)


def create_driver(*, headless: bool = False) -> webdriver.Chrome:
    """Открыть Chrome с отдельным профилем под hh.ru."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")
    # Не ждать полную прогрузку картинок/стилей — достаточно DOM
    options.page_load_strategy = "eager"
    # Картинки в prefs разрешены: капча (/captcha/picture) должна грузиться.
    # Остальное режем через CDP Network.setBlockedURLs (см. _block_non_captcha_images).
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 1,
            "profile.default_content_setting_values.images": 1,
        },
    )
    if headless:
        options.add_argument("--headless=new")
        logger.info("Chrome без окна на экране")

    # Selenium Manager подтянет chromedriver сам (Selenium 4.6+)
    driver = webdriver.Chrome(options=options)
    _block_non_captcha_images(driver)
    try:
        from hh.highlight import set_visual_driver

        set_visual_driver(driver)
    except Exception:  # noqa: BLE001
        pass
    return driver


def _block_non_captcha_images(driver: WebDriver) -> None:
    """Резать jpg/png/CDN, оставить hh.ru/captcha/picture."""
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {"urls": list(_BLOCKED_IMAGE_URLS)},
        )
        logger.debug(
            "CDP: блокировка картинок вкл. ({} паттернов), captcha picture разрешена",
            len(_BLOCKED_IMAGE_URLS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось включить CDP block images: {}", exc)


def quit_driver(driver: webdriver.Chrome | None) -> None:
    """Закрыть браузер и отвязать баннер логов."""
    try:
        from hh.highlight import set_visual_driver

        set_visual_driver(None)
    except Exception:  # noqa: BLE001
        pass
    if driver is not None:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
