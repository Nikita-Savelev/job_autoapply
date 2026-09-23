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


def create_driver(
    *,
    headless: bool = False,
    background: bool = True,
) -> webdriver.Chrome:
    """Открыть Chrome с отдельным профилем под hh.ru.

    background=True — окно не забирает фокус при новых вкладках.
    Для входа в аккаунт нужен background=False.
    """
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
    elif background:
        # Окно уходит под другие, но страница должна продолжать рисоваться.
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-background-timer-throttling")

    # Selenium Manager подтянет chromedriver сам (Selenium 4.6+)
    driver = webdriver.Chrome(options=options)
    if background and not headless:
        try:
            from browser_focus import hold_chrome_in_background

            hold_chrome_in_background(driver.service.process.pid)
            logger.info("Chrome остаётся на заднем плане и не забирает фокус")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось удержать Chrome на заднем плане: {}", exc)
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
        from browser_focus import release_chrome_focus

        release_chrome_focus()
    except Exception:  # noqa: BLE001
        pass
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
