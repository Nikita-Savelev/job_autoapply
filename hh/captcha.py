"""Детект капчи hh.ru: картинка в Telegram, ответ вставляем в форму."""

from __future__ import annotations

import time

from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from hh import selectors as sel
from hh.highlight import show_banner, visual_enabled
from notify.telegram import send_telegram, send_telegram_photo, wait_telegram_text

# Сколько ждать ответ на одну попытку (сек). 0 = без лимита на попытку.
DEFAULT_CAPTCHA_WAIT_SEC = 3600.0
_MAX_ATTEMPTS = 3
_RESULT_WAIT_SEC = 8.0


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


def refresh_captcha_image(driver: WebDriver) -> None:
    """Перезагрузить <img> капчи (на случай, если картинка не подтянулась)."""
    try:
        n = driver.execute_script(
            """
            const imgs = document.querySelectorAll(
              "[data-qa='account-captcha-picture'], img[src*='/captcha/picture']"
            );
            let n = 0;
            for (const img of imgs) {
              try {
                const u = new URL(img.getAttribute('src') || img.src, location.origin);
                u.searchParams.set('_r', String(Date.now()));
                img.src = u.toString();
                n += 1;
              } catch (e) {}
            }
            return n;
            """
        )
        if n:
            logger.info("Капча: перезагрузил {} картинок", n)
            time.sleep(0.4)
    except Exception as exc:  # noqa: BLE001
        logger.debug("refresh_captcha_image failed: {}", exc)


def _captcha_image_el(driver: WebDriver) -> WebElement | None:
    for css in (sel.CAPTCHA_PICTURE, sel.CAPTCHA_IMG_SRC):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if el.is_displayed():
                        return el
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _captcha_png(driver: WebDriver) -> bytes | None:
    el = _captcha_image_el(driver)
    if el is None:
        refresh_captcha_image(driver)
        el = _captcha_image_el(driver)
    if el is None:
        return None
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", el
        )
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.3)
    try:
        data = el.screenshot_as_png
    except Exception as exc:  # noqa: BLE001
        logger.warning("Капча: скриншот картинки не снялся: {}", exc)
        return None
    if data and len(data) > 400:
        return data
    refresh_captcha_image(driver)
    time.sleep(0.6)
    el = _captcha_image_el(driver)
    if el is None:
        return None
    try:
        return el.screenshot_as_png
    except Exception as exc:  # noqa: BLE001
        logger.warning("Капча: повторный скриншот не снялся: {}", exc)
        return None


def _captcha_input(driver: WebDriver) -> WebElement | None:
    for css in (sel.CAPTCHA_INPUT, sel.CAPTCHA_INPUT_NAME):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if el.is_displayed():
                        return el
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _captcha_error_text(driver: WebDriver) -> str:
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, sel.CAPTCHA_ERROR):
            try:
                if not el.is_displayed():
                    continue
            except Exception:  # noqa: BLE001
                continue
            text = (el.text or "").strip()
            if text:
                return text
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _submit_captcha_text(driver: WebDriver, answer: str) -> None:
    field = _captcha_input(driver)
    if field is None:
        raise CaptchaTimeout("Капча на экране, но поле ввода не найдено")
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", field
        )
    except Exception:  # noqa: BLE001
        pass
    field.click()
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(Keys.BACKSPACE)
    field.send_keys(answer)
    current = (field.get_attribute("value") or "").strip()
    if current != answer.strip():
        driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            const proto = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value'
            );
            proto.set.call(el, value);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            field,
            answer,
        )
    button = driver.execute_script(
        """
        const field = arguments[0];
        const form = field.closest('form');
        if (form) {
          const btn = form.querySelector("button[type='submit']");
          if (btn) return btn;
        }
        const buttons = [...document.querySelectorAll("button[type='submit']")];
        return buttons.find((b) => (b.innerText || '').includes('Отправить')) || null;
        """,
        field,
    )
    if button is None:
        logger.warning("Капча: кнопка «Отправить» не найдена, жму Enter")
        field.send_keys(Keys.ENTER)
        return
    try:
        button.click()
    except Exception:  # noqa: BLE001
        driver.execute_script("arguments[0].click();", button)


def _captcha_image_src(driver: WebDriver) -> str:
    el = _captcha_image_el(driver)
    if el is None:
        return ""
    try:
        return (el.get_attribute("src") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _wait_captcha_outcome(
    driver: WebDriver,
    *,
    before_src: str,
    before_err: str,
) -> str:
    """ok — капча ушла; bad — новая ошибка или сменилась картинка.

    Текст ошибки с прошлой попытки сам по себе не считается провалом.
    """
    deadline = time.time() + _RESULT_WAIT_SEC
    while time.time() < deadline:
        if not captcha_visible(driver):
            return "ok"
        src = _captcha_image_src(driver)
        err = _captcha_error_text(driver)
        src_changed = bool(src and before_src and src != before_src)
        err_changed = bool(err and err != before_err)
        if src_changed or err_changed:
            time.sleep(0.4)
            if not captcha_visible(driver):
                return "ok"
            if err:
                logger.info("Капча не принята: {}", err)
            return "bad"
        time.sleep(0.4)
    if not captcha_visible(driver):
        return "ok"
    return "bad"


def resolve_captcha_if_present(
    driver: WebDriver,
    *,
    context: str = "",
    timeout_sec: float = DEFAULT_CAPTCHA_WAIT_SEC,
) -> bool:
    """Если капча видна — прислать картинку в Telegram и вставить ответ.

    До трёх попыток подряд. Каждая ждёт текст не дольше timeout_sec.

    Returns:
        True — капча была и исчезла; False — капчи не было.
    Raises:
        CaptchaTimeout — нет ответа или три неудачные попытки.
    """
    if not captcha_visible(driver):
        return False

    url = ""
    try:
        url = driver.current_url or ""
    except Exception:  # noqa: BLE001
        pass

    logger.warning("КАПЧА обнаружена. {}", context or url)
    if visual_enabled():
        show_banner(driver, "КАПЧА — жду текст в Telegram")

    per_attempt = float(timeout_sec) if timeout_sec > 0 else 3600.0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if not captcha_visible(driver):
            logger.info("Капча снята — продолжаю")
            return True

        png = _captcha_png(driver)
        caption_lines = [
            f"Капча hh.ru, попытка {attempt} из {_MAX_ATTEMPTS}.",
            "Ответь сообщением: текст с картинки.",
        ]
        if context:
            caption_lines.append(f"Контекст: {context}")
        caption = "\n".join(caption_lines)
        send_telegram(caption + "\nКартинка следующим сообщением.")
        sent = False
        if png:
            sent = send_telegram_photo(png, caption)
        if not sent:
            send_telegram(
                caption + "\nКартинку приложить не удалось — реши капчу в Chrome."
            )

        since = time.time()
        answer = wait_telegram_text(
            timeout_sec=per_attempt,
            since_ts=since,
            stop_if=lambda: not captcha_visible(driver),
        )
        if not captcha_visible(driver):
            logger.info("Капча снята — продолжаю")
            if visual_enabled():
                show_banner(driver, "Капча OK — продолжаю")
            time.sleep(1.0)
            return True
        if not answer:
            send_telegram("⛔ hh_autoapply: текст капчи не пришёл, прогон остановлен.")
            raise CaptchaTimeout(
                f"Нет ответа на капчу за {per_attempt:.0f}s ({context or url})"
            )

        logger.info("Капча: вставляю ответ, попытка {}/{}", attempt, _MAX_ATTEMPTS)
        if visual_enabled():
            show_banner(driver, f"Капча: отправляю попытку {attempt}")
        before_src = _captcha_image_src(driver)
        before_err = _captcha_error_text(driver)
        _submit_captcha_text(driver, answer)
        if (
            _wait_captcha_outcome(
                driver, before_src=before_src, before_err=before_err
            )
            == "ok"
        ):
            logger.info("Капча снята — продолжаю")
            if visual_enabled():
                show_banner(driver, "Капча OK — продолжаю")
            time.sleep(1.0)
            return True
        logger.warning(
            "Капча: попытка {}/{} не прошла", attempt, _MAX_ATTEMPTS
        )

    send_telegram(
        "⛔ hh_autoapply: капча не прошла за 3 попытки, прогон остановлен."
    )
    raise CaptchaTimeout(
        f"Капча не прошла за {_MAX_ATTEMPTS} попытки ({context or url})"
    )
