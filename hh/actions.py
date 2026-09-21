"""Действия на UI: скрыть вакансию, откликнуться с письмом."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from loguru import logger
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from hh import selectors as sel
from hh.highlight import clear_highlight, flash_click, show_banner, visual_enabled


class ApplyOutcome(StrEnum):
    LETTER_SENT = "letter_sent"
    APPLIED_NO_LETTER = "applied_no_letter"
    ALREADY_RESPONDED = "already_responded"
    TEST_REQUIRED = "test_required"
    UNKNOWN = "unknown"


@dataclass
class ApplyResult:
    outcome: ApplyOutcome
    detail: str = ""


@dataclass(frozen=True)
class ResponseButtonState:
    """Состояние основной кнопки отклика на странице вакансии."""

    can_apply: bool
    label: str = ""
    already_responded: bool = False


class ActionNotImplementedError(NotImplementedError):
    """Селектор / сценарий ещё не доведён."""


class ResponseTestRequired(Exception):
    """Нужен тест при отклике (пока stub)."""


class AlreadyResponded(Exception):
    """На hh.ru уже есть отклик (кнопка Чат/Отказ/Собеседование)."""

    def __init__(self, label: str = "") -> None:
        self.label = (label or "").strip() or "already_responded"
        super().__init__(f"Уже откликались ранее: {self.label}")


class LetterSubmitUnavailable(Exception):
    """UI письма есть, но кнопки отправки нет - письмо НЕ вводим."""


def _pause(sec: float, *, why: str, driver: WebDriver | None = None) -> None:
    if sec <= 0:
        return
    logger.debug("pause {:.1f}s — {}", sec, why)
    if driver is not None and visual_enabled() and why:
        show_banner(driver, why)
    time.sleep(sec)


def _scroll_into_view(driver: WebDriver, el: WebElement) -> None:
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
        el,
    )


def _click(driver: WebDriver, el: WebElement, *, pause_sec: float, label: str) -> None:
    logger.info("click: {}", label)
    _scroll_into_view(driver, el)
    hold = 0.4 if pause_sec > 0 else 0.0
    flash_click(driver, el, label=label, hold_sec=hold)
    _pause(pause_sec, why=f"перед кликом: {label}", driver=driver)
    try:
        el.click()
    except Exception:
        logger.debug("обычный click не вышел — JS click ({})", label)
        driver.execute_script("arguments[0].click();", el)
    show_banner(driver, f"OK: {label}")
    _pause(pause_sec, why=f"после клика: {label}", driver=driver)
    clear_highlight(driver)


def _find_first(
    driver: WebDriver,
    locators: list[tuple[str, str]],
    *,
    timeout: float = 8.0,
) -> WebElement:
    last_exc: Exception | None = None
    end = time.time() + timeout
    while time.time() < end:
        for by, value in locators:
            try:
                els = driver.find_elements(by, value)
                for el in els:
                    if el.is_displayed():
                        return el
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        time.sleep(0.25)
    raise TimeoutException(
        f"Не найден элемент: {locators!r}; last={last_exc}"
    )


def _exists(driver: WebDriver, css: str) -> bool:
    try:
        return any(el.is_displayed() for el in driver.find_elements(By.CSS_SELECTOR, css))
    except Exception:
        return False


def _looks_like_test(driver: WebDriver) -> bool:
    from hh.response_test import looks_like_response_test

    if looks_like_response_test(driver):
        return True
    url = (driver.current_url or "").lower()
    if "question" in url or "/test" in url or "quiz" in url:
        return True
    for css in sel.RESPONSE_TEST_MARKERS:
        if _exists(driver, css):
            return True
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    markers = (
        "пройдите тест",
        "ответьте на вопросы",
        "тестовое задание для отклика",
        "чтобы откликнуться, ответьте",
        "для отклика необходимо ответить",
    )
    return any(m in body for m in markers)


def _submit_locators() -> list[tuple[str, str]]:
    return [
        (By.CSS_SELECTOR, sel.RESPONSE_LETTER_SUBMIT),
        (By.CSS_SELECTOR, sel.RESPONSE_SUBMIT_POPUP),
        (
            By.XPATH,
            "//*[@data-qa='primary-actions']"
            "//button[.//span[contains(normalize-space(.),'Отправить')]]",
        ),
        (
            By.XPATH,
            "//button[@type='submit' and .//span[contains(normalize-space(.),'Отправить')]]",
        ),
    ]


def _find_submit_button(
    driver: WebDriver,
    *,
    timeout: float = 5.0,
    require_enabled: bool = True,
) -> WebElement | None:
    """Кнопка отправки/отклика в форме письма.

    В модалке с обязательным письмом кнопка сначала disabled
    (data-qa=vacancy-response-submit-popup, текст «Откликнуться») —
    тогда require_enabled=False, чтобы детектить форму до ввода текста.
    """
    end = time.time() + timeout
    while time.time() < end:
        for by, value in _submit_locators():
            try:
                for el in driver.find_elements(by, value):
                    try:
                        if not el.is_displayed():
                            continue
                        if require_enabled and not el.is_enabled():
                            continue
                        return el
                    except Exception:
                        continue
            except Exception:
                continue
        time.sleep(0.25)
    return None


def _is_response_letter_modal(driver: WebDriver) -> bool:
    """Модалка отклика с письмом (обязательным или нет) — ещё до «резюме доставлено».

    Textarea уже открыта, submit = vacancy-response-submit-popup («Откликнуться»),
    кнопки «Приложить сопроводительное» ещё нет.
    """
    if not _exists(driver, sel.RESPONSE_LETTER_TEXTAREA):
        return False
    if _exists(driver, sel.RESPONSE_SUCCESS_ATTACH_LETTER):
        return False
    return (
        _find_submit_button(driver, timeout=0.8, require_enabled=False) is not None
        or _exists(driver, sel.RESPONSE_SUBMIT_POPUP)
        or _exists(driver, "[data-qa='response-popup-close']")
    )


def hide_vacancy_on_serp(driver: WebDriver, hh_id: str) -> None:
    raise ActionNotImplementedError(
        f"hide_vacancy_on_serp({hh_id}): доработать на живой выдаче"
    )


def detect_response_button_state(driver: WebDriver) -> ResponseButtonState:
    """Проверить, можно ли ещё откликнуться или отклик уже был."""
    # Явный маркер hh: vacancy-response-link-view-topic («Чат» и т.п.)
    for css in (sel.VACANCY_RESPONSE_VIEW_TOPIC,):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                if not el.is_displayed():
                    continue
                label = (el.text or "").strip() or "Чат"
                return ResponseButtonState(
                    can_apply=False,
                    already_responded=True,
                    label=label.split("\n")[0].strip()[:80],
                )
        except Exception:
            continue

    # Кнопки top/bottom: смотрим текст
    for css in (
        sel.VACANCY_RESPONSE_BUTTON,
        sel.VACANCY_RESPONSE_BUTTON_ALT,
        sel.SEARCH_RESPONSE_BUTTON,
    ):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                if not el.is_displayed():
                    continue
                label = (el.text or "").strip()
                label_l = label.lower()
                if "откликнуться" in label_l:
                    return ResponseButtonState(can_apply=True, label=label)
                if any(t in label_l for t in sel.VACANCY_ALREADY_RESPONDED_TEXTS):
                    short = label.split("\n")[0].strip()[:80] or "already"
                    return ResponseButtonState(
                        can_apply=False,
                        already_responded=True,
                        label=short,
                    )
        except Exception:
            continue

    # Запас: xpath «Откликнуться»
    try:
        for el in driver.find_elements(By.XPATH, sel.VACANCY_RESPONSE_BY_TEXT_XPATH):
            if el.is_displayed():
                return ResponseButtonState(
                    can_apply=True, label=(el.text or "Откликнуться").strip()
                )
    except Exception:
        pass

    return ResponseButtonState(can_apply=False, label="")


def apply_with_letter(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float = 2.0,
    dumper=None,
    resume_text: str = "",
    vacancy_title: str = "",
    company: str = "",
) -> ApplyResult:
    """Отклик со страницы вакансии (вкладка вакансии уже открыта).

    Сценарии:
    - опросник работодателя → ответы (LLM) + письмо → «Откликнуться»;
    - обязательное письмо в модалке;
    - опциональное после доставки резюме;
    - просмотрен работодателем → Chatik.
    """
    state = detect_response_button_state(driver)
    if state.already_responded:
        logger.info(
            "apply_with_letter: уже откликались ранее (кнопка {!r})",
            state.label,
        )
        show_banner(driver, f"Уже обработано: {state.label}")
        raise AlreadyResponded(state.label)

    logger.info("apply_with_letter: ищу кнопку «Откликнуться»")
    show_banner(driver, "Ищу кнопку «Откликнуться»")
    try:
        btn = _find_response_button(driver)
    except TimeoutException:
        # ещё раз: возможно кнопка сменилась на «Чат»
        state = detect_response_button_state(driver)
        if state.already_responded:
            raise AlreadyResponded(state.label) from None
        raise
    _click(driver, btn, pause_sec=pause_sec, label="Откликнуться")

    if dumper is not None:
        try:
            dumper.save(driver, label="after_response_click")
        except Exception as exc:  # noqa: BLE001
            logger.debug("dump after_response_click failed: {}", exc)

    # Попап «вакансия в другой стране» — всегда «Все равно откликнуться»
    _confirm_foreign_country_popup(driver, pause_sec=pause_sec, dumper=dumper)

    if _looks_like_test(driver):
        return _complete_employer_test(
            driver,
            letter=letter,
            resume_text=resume_text,
            vacancy_title=vacancy_title,
            company=company,
            pause_sec=pause_sec,
            dumper=dumper,
        )

    if not _wait_letter_flow_available(driver, timeout=15.0, pause_sec=pause_sec):
        # ещё раз на случай, если попап появился с задержкой
        if _confirm_foreign_country_popup(driver, pause_sec=pause_sec, dumper=dumper):
            if _looks_like_test(driver):
                return _complete_employer_test(
                    driver,
                    letter=letter,
                    resume_text=resume_text,
                    vacancy_title=vacancy_title,
                    company=company,
                    pause_sec=pause_sec,
                    dumper=dumper,
                )
            if _wait_letter_flow_available(driver, timeout=10.0, pause_sec=pause_sec):
                return _attach_and_send_letter(
                    driver, letter, pause_sec=pause_sec, dumper=dumper
                )
        logger.warning("Нет UI письма после отклика. url={}", driver.current_url)
        if dumper is not None:
            try:
                dumper.save(driver, label="response_unknown_ui")
            except Exception:  # noqa: BLE001
                pass
        return ApplyResult(
            outcome=ApplyOutcome.UNKNOWN,
            detail="После «Откликнуться» нет кнопки приложения письма",
        )

    return _attach_and_send_letter(driver, letter, pause_sec=pause_sec, dumper=dumper)


def _complete_employer_test(
    driver: WebDriver,
    *,
    letter: str,
    resume_text: str,
    vacancy_title: str,
    company: str,
    pause_sec: float,
    dumper=None,
) -> ApplyResult:
    """Пройти опросник работодателя и отправить отклик с письмом."""
    from hh.response_test import complete_response_test

    if not (resume_text or "").strip():
        if dumper is not None:
            try:
                dumper.save(driver, label="response_test_no_resume")
            except Exception:  # noqa: BLE001
                pass
        raise ResponseTestRequired(
            "Нужен опросник, но resume_text пустой — нечем отвечать"
        )

    logger.info("Сценарий с тестом/опросником — отвечаю автоматически")
    show_banner(driver, "Опросник работодателя")
    if dumper is not None:
        try:
            dumper.save(driver, label="response_test_start")
        except Exception:  # noqa: BLE001
            pass

    try:
        complete_response_test(
            driver,
            letter=letter,
            resume_text=resume_text,
            vacancy_title=vacancy_title,
            company=company,
            pause_sec=pause_sec,
            dumper=dumper,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось пройти опросник: {}", exc)
        if dumper is not None:
            try:
                dumper.save(driver, label="response_test_failed")
            except Exception:  # noqa: BLE001
                pass
        raise ResponseTestRequired(f"Опросник не пройден: {exc}") from exc

    return ApplyResult(outcome=ApplyOutcome.LETTER_SENT, detail="with_test")


def _find_response_button(driver: WebDriver) -> WebElement:
    locators = [
        (By.CSS_SELECTOR, sel.VACANCY_RESPONSE_BUTTON),
        (By.CSS_SELECTOR, sel.VACANCY_RESPONSE_BUTTON_ALT),
        (By.CSS_SELECTOR, sel.SEARCH_RESPONSE_BUTTON),
        (By.XPATH, sel.VACANCY_RESPONSE_BY_TEXT_XPATH),
    ]
    return _find_first(driver, locators, timeout=10.0)


def _letter_attach_ready(driver: WebDriver) -> bool:
    """Есть кнопка/форма приложения письма (блок «Резюме доставлено» или старый UI)."""
    checks = (
        sel.RESPONSE_SUCCESS_ATTACH_LETTER,
        sel.RESPONSE_SUCCESS_ATTACH_LETTER_TEXT,
        sel.RESPONSE_LETTER_TOGGLE,
        sel.RESPONSE_LETTER_TOGGLE_TEXT,
        sel.RESPONSE_LETTER_TEXTAREA,
        sel.RESPONSE_LETTER_INFORMER,
    )
    return any(_exists(driver, css) for css in checks)


def _confirm_foreign_country_popup(
    driver: WebDriver,
    *,
    pause_sec: float = 0.0,
    dumper=None,
    wait_sec: float = 3.0,
) -> bool:
    """Попап «вакансия в другой стране» → всегда «Все равно откликнуться».

    В i18n hh: vacancy.respond.popup.title / .force
    («Вы откликаетесь на вакансию в другой стране» / «Все равно откликнуться»).
    """
    end = time.time() + max(0.5, wait_sec)
    clicked = False
    while time.time() < end:
        # по тексту кнопки
        try:
            els = driver.find_elements(By.XPATH, sel.FOREIGN_COUNTRY_FORCE_XPATH)
        except Exception:
            els = []
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                label = (el.text or "").strip().split("\n")[0][:80]
                logger.info(
                    "Попап «другая страна» — нажимаю «{}»",
                    label or "Все равно откликнуться",
                )
                show_banner(driver, "Другая страна → Все равно откликнуться")
                _click(
                    driver,
                    el,
                    pause_sec=pause_sec,
                    label="Все равно откликнуться",
                )
                clicked = True
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("force-click foreign popup failed: {}", exc)
        if clicked:
            break

        # детект по заголовку (кнопка могла ещё не прогрузиться)
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            body = ""
        if sel.FOREIGN_COUNTRY_POPUP_TITLE in body:
            time.sleep(0.25)
            continue
        # заголовка нет — попапа, скорее всего, нет
        if time.time() > end - wait_sec + 0.6:
            break
        time.sleep(0.25)

    if clicked and dumper is not None:
        try:
            dumper.save(driver, label="after_foreign_country_confirm")
        except Exception as exc:  # noqa: BLE001
            logger.debug("dump after_foreign_country_confirm failed: {}", exc)
    return clicked


def _wait_letter_flow_available(
    driver: WebDriver,
    *,
    timeout: float,
    pause_sec: float = 0.0,
) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _looks_like_test(driver):
            return False
        # попап мог всплыть с задержкой
        _confirm_foreign_country_popup(
            driver, pause_sec=pause_sec, wait_sec=0.4
        )
        if _letter_attach_ready(driver):
            return True
        time.sleep(0.3)
    return False


def _attach_button_locators() -> list[tuple[str, str]]:
    return [
        (By.CSS_SELECTOR, sel.RESPONSE_SUCCESS_ATTACH_LETTER),
        (By.CSS_SELECTOR, sel.RESPONSE_SUCCESS_ATTACH_LETTER_TEXT),
        (By.CSS_SELECTOR, sel.RESPONSE_LETTER_TOGGLE),
        (By.CSS_SELECTOR, sel.RESPONSE_LETTER_TOGGLE_TEXT),
        (
            By.XPATH,
            "//button[.//span[contains(normalize-space(.),'Приложить сопроводительное')]]"
            " | //button[contains(normalize-space(.),'Приложить сопроводительное')]"
            " | //button[.//span[contains(normalize-space(.),'Приложить письмо')]]",
        ),
    ]


def _letter_viewed_by_employer(driver: WebDriver) -> bool:
    """Предупреждение: отклик уже просмотрен — обычная отправка письма недоступна.

    Ищем только видимый короткий узел с текстом предупреждения
    (не i18n-каталог в hidden <template>).
    """
    needle = sel.LETTER_VIEWED_WARNING_TEXT
    try:
        els = driver.find_elements(
            By.XPATH,
            f"//*[contains(normalize-space(.),'{needle}')]",
        )
    except Exception:
        return False
    for el in els:
        try:
            tag = (el.tag_name or "").lower()
            if tag in ("html", "body", "script", "style", "template", "noscript"):
                continue
            if not el.is_displayed():
                continue
            text = (el.text or "").strip()
            if needle not in text:
                continue
            # слишком большой контейнер — скорее страница целиком
            if len(text) > 400:
                continue
            return True
        except Exception:
            continue
    return False


def _send_letter_via_chatik(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float,
    dumper=None,
) -> ApplyResult:
    """Фолбэк: сопроводительное через виджет Chatik («Добавить сопроводительное»)."""
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    # форма письма может перекрывать кнопку «Чат»
    _try_close_popup(driver, pause_sec=min(pause_sec, 1.0))

    _open_chatik_near_letter(driver, pause_sec=pause_sec)

    if dumper is not None:
        try:
            dumper.save(driver, label="chatik_opened")
        except Exception:  # noqa: BLE001
            pass

    show_banner(driver, "Жду iframe чата")
    _switch_to_chatik_iframe(driver, timeout=15.0)
    _pause(min(pause_sec, 1.5), why="iframe chatik загружен", driver=driver)

    show_banner(driver, "«Добавить сопроводительное» в чате")
    add_btn = _find_first(
        driver,
        [
            (By.CSS_SELECTOR, sel.CHATIK_ADD_LETTER),
            (By.CSS_SELECTOR, sel.CHATIK_ADD_LETTER_TEXT),
            (
                By.XPATH,
                "//a[.//span[contains(normalize-space(.),'Добавить сопроводительное')]]"
                " | //*[@data-qa='chatik-chat-message-applicant-action']",
            ),
        ],
        timeout=12.0,
    )
    _click(
        driver,
        add_btn,
        pause_sec=pause_sec,
        label="Добавить сопроводительное (чат)",
    )

    # превью «Сопроводительное письмо» + textarea
    try:
        WebDriverWait(driver, 8).until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, sel.CHATIK_MESSAGE_INPUT)
            )
        )
    except TimeoutException as exc:
        raise LetterSubmitUnavailable(
            "После «Добавить сопроводительное» нет поля ввода в чате"
        ) from exc

    show_banner(driver, f"Ввод письма в чат ({len(letter)} символов)")
    textarea = _fill_chatik_textarea(driver, letter, pause_sec=pause_sec)

    if dumper is not None:
        try:
            dumper.save(driver, label="chatik_letter_filled")
        except Exception:  # noqa: BLE001
            pass

    # отправка: кнопка (Enter в textarea = перенос строки)
    sent = False
    send_btn = None
    try:
        send_btn = _find_first(driver, _chatik_send_locators(), timeout=6.0)
    except TimeoutException:
        send_btn = None

    if send_btn is not None:
        _click(driver, send_btn, pause_sec=pause_sec, label="Отправить в чат")
        sent = True
    else:
        logger.info("Кнопки send в чате нет — пробую Ctrl+Enter")
        try:
            textarea.send_keys(Keys.CONTROL, Keys.ENTER)
            sent = True
            _pause(pause_sec, why="после Ctrl+Enter в chatik", driver=driver)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ctrl+Enter в chatik не сработал: {}", exc)

    if not sent:
        raise LetterSubmitUnavailable(
            "Не удалось отправить сопроводительное в чате (нет кнопки/Ctrl+Enter)"
        )

    if dumper is not None:
        try:
            dumper.save(driver, label="chatik_after_send")
        except Exception:  # noqa: BLE001
            pass

    # закрыть виджет
    try:
        driver.switch_to.default_content()
        close_btn = _find_first(
            driver,
            [(By.CSS_SELECTOR, sel.CHATIK_CLOSE)],
            timeout=3.0,
        )
        _click(
            driver,
            close_btn,
            pause_sec=min(pause_sec, 1.0),
            label="Закрыть чат",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("не закрыл chatik: {}", exc)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

    logger.info("письмо отправлено через Chatik")
    show_banner(driver, "Письмо отправлено в чат")
    return ApplyResult(outcome=ApplyOutcome.LETTER_SENT, detail="via_chatik")


def _attach_and_send_letter(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float,
    dumper=None,
) -> ApplyResult:
    in_modal = _is_response_letter_modal(driver)
    if in_modal:
        show_banner(driver, "Модалка отклика с письмом")
        logger.info(
            "Форма письма уже открыта (обязательное/модалка) — без «Приложить»"
        )
    else:
        show_banner(driver, "Ищу кнопку «Приложить сопроводительное»")
        # Кнопка из блока «Резюме доставлено»: responded-success-attach-cover-letter
        if not _exists(driver, sel.RESPONSE_LETTER_TEXTAREA):
            toggle = _find_first(driver, _attach_button_locators(), timeout=10.0)
            _click(
                driver,
                toggle,
                pause_sec=pause_sec,
                label="Приложить сопроводительное письмо",
            )

    # Ждём либо textarea, либо предупреждение «уже просмотрен»
    end = time.time() + 12.0
    textarea_ready = False
    while time.time() < end:
        if _letter_viewed_by_employer(driver):
            logger.info(
                "Отклик уже просмотрен работодателем — письмо через чат"
            )
            show_banner(driver, "Просмотрен → письмо в чат")
            if dumper is not None:
                try:
                    dumper.save(driver, label="letter_viewed_before_type")
                except Exception:  # noqa: BLE001
                    pass
            return _send_letter_via_chatik(
                driver, letter, pause_sec=pause_sec, dumper=dumper
            )
        if _exists(driver, sel.RESPONSE_LETTER_TEXTAREA):
            textarea_ready = True
            break
        time.sleep(0.25)

    if not textarea_ready:
        # textarea нет, но чат рядом может быть доступен (уже после отклика)
        if _exists(driver, sel.RESPONSE_SUCCESS_OPEN_CHAT):
            logger.info("textarea письма нет — пробую отправку через Chatik")
            return _send_letter_via_chatik(
                driver, letter, pause_sec=pause_sec, dumper=dumper
            )
        raise LetterSubmitUnavailable(
            "Не появилась форма письма и нет кнопки чата"
        )

    in_modal = in_modal or _is_response_letter_modal(driver)
    submit_present = _find_submit_button(
        driver, timeout=5.0, require_enabled=False
    )
    submit_enabled = _find_submit_button(
        driver, timeout=1.0, require_enabled=True
    )

    # В модалке отклика submit часто disabled, пока письмо пустое —
    # текст вводим сразу, затем ждём активации кнопки.
    if submit_enabled is None and submit_present is None:
        if _letter_viewed_by_employer(driver):
            logger.info("Нет submit + просмотрен — фолбэк Chatik")
            if dumper is not None:
                try:
                    dumper.save(driver, label="letter_submit_missing_chat_fallback")
                except Exception:  # noqa: BLE001
                    pass
            return _send_letter_via_chatik(
                driver, letter, pause_sec=pause_sec, dumper=dumper
            )
        # Чат-фолбэк только после успешного отклика, не из модалки
        if not in_modal and _exists(driver, sel.RESPONSE_SUCCESS_OPEN_CHAT):
            logger.info("Нет submit после отклика — фолбэк Chatik")
            if dumper is not None:
                try:
                    dumper.save(driver, label="letter_submit_missing_chat_fallback")
                except Exception:  # noqa: BLE001
                    pass
            _try_close_popup(driver, pause_sec=min(pause_sec, 1.0))
            return _send_letter_via_chatik(
                driver, letter, pause_sec=pause_sec, dumper=dumper
            )
        if dumper is not None:
            try:
                dumper.save(driver, label="letter_submit_missing_before_type")
            except Exception:  # noqa: BLE001
                pass
        # модалку не закрываем — иначе срываем обязательный отклик
        if not in_modal:
            _try_close_popup(driver, pause_sec=pause_sec)
        raise LetterSubmitUnavailable(
            "Кнопка отправки письма не найдена — текст в форму не вводили"
        )

    if submit_enabled is None and submit_present is not None:
        logger.info(
            "Кнопка отклика/отправки пока disabled — сначала ввожу письмо "
            "({} символов)",
            len(letter),
        )
    else:
        logger.info(
            "кнопка отправки найдена, пишу сопроводительное ({} символов)",
            len(letter),
        )

    show_banner(driver, f"Ввод письма ({len(letter)} символов)")
    _fill_cover_letter_textarea(driver, letter, pause_sec=pause_sec)
    show_banner(driver, "Письмо введено, готовлю отправку")

    if dumper is not None:
        try:
            dumper.save(driver, label="letter_filled")
        except Exception:  # noqa: BLE001
            pass

    # после ввода снова мог появиться блокер «просмотрен»
    if _letter_viewed_by_employer(driver):
        logger.info("После ввода: отклик просмотрен — отправляю через чат")
        _try_close_popup(driver, pause_sec=min(pause_sec, 1.0))
        return _send_letter_via_chatik(
            driver, letter, pause_sec=pause_sec, dumper=dumper
        )

    submit = _find_submit_button(driver, timeout=10.0, require_enabled=True)
    if submit is None:
        if not in_modal and _exists(driver, sel.RESPONSE_SUCCESS_OPEN_CHAT):
            logger.info("Submit не активировался — фолбэк через Chatik")
            _try_close_popup(driver, pause_sec=min(pause_sec, 1.0))
            return _send_letter_via_chatik(
                driver, letter, pause_sec=pause_sec, dumper=dumper
            )
        if dumper is not None:
            try:
                dumper.save(driver, label="letter_submit_missing_after_type")
            except Exception:  # noqa: BLE001
                pass
        raise LetterSubmitUnavailable(
            "После ввода письма кнопка отправки так и не активировалась"
        )

    # перед отправкой ещё раз убедимся, что React видит текст
    value = _textarea_value(driver)
    if not value.strip():
        raise LetterSubmitUnavailable(
            "После ввода textarea пустая для React — письмо не отправляем"
        )
    logger.debug("textarea value len перед submit={}", len(value))

    submit_label = (submit.text or "").strip().split("\n")[0][:40] or "Отправить"
    _click(
        driver,
        submit,
        pause_sec=pause_sec,
        label=f"{submit_label} (письмо)",
    )

    if dumper is not None:
        try:
            dumper.save(driver, label="after_letter_submit")
        except Exception:  # noqa: BLE001
            pass

    # редкий кейс: submit кликнули, но всплыло предупреждение о просмотре
    if _letter_viewed_by_employer(driver):
        logger.info("После submit: просмотрен — досылаю письмо в чат")
        return _send_letter_via_chatik(
            driver, letter, pause_sec=pause_sec, dumper=dumper
        )

    logger.info("письмо отправлено ({})", "модалка" if in_modal else "после отклика")
    return ApplyResult(
        outcome=ApplyOutcome.LETTER_SENT,
        detail="modal" if in_modal else "ok",
    )


def _open_chatik_near_letter(driver: WebDriver, *, pause_sec: float) -> None:
    """Открыть чат рядом с кнопкой сопроводительного (view-topic)."""
    show_banner(driver, "Открываю чат для письма")
    # предпочитаем кнопку «Чат» рядом с attach в блоке успеха
    locators = [
        (
            By.XPATH,
            "//button[@data-qa='responded-success-attach-cover-letter']"
            "/ancestor::div[contains(@class,'magritte-box') or @data-qa='flex'][1]"
            "//button[@data-qa='vacancy-response-link-view-topic']",
        ),
        (By.CSS_SELECTOR, sel.RESPONSE_SUCCESS_OPEN_CHAT),
        (By.CSS_SELECTOR, sel.VACANCY_RESPONSE_VIEW_TOPIC),
        (
            By.XPATH,
            "//button[.//span[normalize-space()='Чат']]"
            " | //a[.//span[normalize-space()='Чат']]",
        ),
    ]
    btn = _find_first(driver, locators, timeout=10.0)
    _click(driver, btn, pause_sec=pause_sec, label="Чат (рядом с письмом)")


def _switch_to_chatik_iframe(driver: WebDriver, *, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < end:
        try:
            frames = driver.find_elements(By.CSS_SELECTOR, sel.CHATIK_IFRAME)
            for frame in frames:
                if not frame.is_displayed():
                    continue
                driver.switch_to.frame(frame)
                # iframe загрузился, если есть body
                try:
                    driver.find_element(By.TAG_NAME, "body")
                    return
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    driver.switch_to.default_content()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.3)
    raise TimeoutException(f"Chatik iframe не загрузился: {last_exc}")


def _chatik_send_locators() -> list[tuple[str, str]]:
    locs: list[tuple[str, str]] = [
        (By.CSS_SELECTOR, css) for css in sel.CHATIK_SEND_BUTTONS
    ]
    locs.extend(
        [
            (
                By.CSS_SELECTOR,
                "[data-qa*='send']:not([disabled]), "
                "[data-qa*='submit']:not([disabled])",
            ),
            (
                By.XPATH,
                "//button[@type='submit' and not(@disabled)]",
            ),
            (
                By.XPATH,
                "//button[contains(@aria-label,'Отправить') and not(contains(@aria-label,'запись'))"
                " and not(contains(@aria-label,'Отменить')) and not(@disabled)]",
            ),
            (
                By.XPATH,
                "//div[contains(@class,'magritte-right') or @data-magritte-chat-input-focus-priority='1']"
                "//button[not(@disabled) and not(@aria-label='uploadFileButton')"
                " and not(contains(@aria-label,'Отменить'))"
                " and not(contains(@aria-label,'запись'))]",
            ),
        ]
    )
    return locs


def _fill_chatik_textarea(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float,
) -> WebElement:
    """Заполнить textarea сообщения в iframe Chatik (React/Magritte)."""
    hold = 0.4 if pause_sec > 0 else 0.0
    textarea = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, sel.CHATIK_MESSAGE_INPUT))
    )
    _scroll_into_view(driver, textarea)
    flash_click(driver, textarea, label="поле сообщения в чате", hold_sec=hold)
    try:
        textarea.click()
    except Exception:
        driver.execute_script("arguments[0].click();", textarea)
    _pause(min(pause_sec, 1.0), why="фокус chatik textarea", driver=driver)

    textarea.send_keys(Keys.CONTROL, "a")
    textarea.send_keys(Keys.BACKSPACE)

    ok = driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.focus();
        const proto = window.HTMLTextAreaElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
            desc.set.call(el, value);
        } else {
            el.value = value;
        }
        el.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            cancelable: true,
            inputType: 'insertText',
            data: value
        }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return el.value || '';
        """,
        textarea,
        letter,
    )
    current = ok or ""
    if len(current.strip()) < max(20, len(letter) // 5):
        logger.info("Chatik: React не принял JS-value — send_keys")
        textarea.click()
        textarea.send_keys(Keys.CONTROL, "a")
        textarea.send_keys(Keys.BACKSPACE)
        chunk = 200
        for i in range(0, len(letter), chunk):
            textarea.send_keys(letter[i : i + chunk])
        current = (
            driver.execute_script("return arguments[0].value || '';", textarea)
            or ""
        )

    if not current.strip():
        raise LetterSubmitUnavailable(
            "Не удалось заполнить сообщение сопроводительного в чате"
        )
    _pause(pause_sec, why="после ввода в chatik", driver=driver)
    clear_highlight(driver)
    logger.info("chatik textarea заполнена, len={}", len(current))
    return textarea


def _textarea_value(driver: WebDriver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, sel.RESPONSE_LETTER_TEXTAREA)
        return driver.execute_script("return arguments[0].value || '';", el) or ""
    except Exception:
        return ""


def _fill_cover_letter_textarea(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float,
) -> None:
    """Ввод письма так, чтобы Magritte/React подхватил значение.

    Простого el.value недостаточно — сначала клик по полю, затем native setter
    + InputEvent, с запасным send_keys.
    """
    # клик по обёртке поля (активирует magritte textarea)
    hold = 0.5 if pause_sec > 0 else 0.0
    for wrap_css in (
        "[data-qa='textarea-wrapper']",
        "[data-qa='textarea-native-wrapper']",
    ):
        try:
            wrap = driver.find_element(By.CSS_SELECTOR, wrap_css)
            if wrap.is_displayed():
                _scroll_into_view(driver, wrap)
                flash_click(driver, wrap, label="поле письма (обёртка)", hold_sec=hold)
                wrap.click()
                _pause(min(pause_sec, 1.5), why="клик по обёртке textarea", driver=driver)
                break
        except Exception:
            continue

    textarea = WebDriverWait(driver, 8).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, sel.RESPONSE_LETTER_TEXTAREA))
    )
    _scroll_into_view(driver, textarea)
    flash_click(driver, textarea, label="textarea письма", hold_sec=hold)
    _pause(pause_sec, why="перед фокусом textarea", driver=driver)
    try:
        textarea.click()
    except Exception:
        driver.execute_script("arguments[0].click();", textarea)
    _pause(min(pause_sec, 1.0), why="после клика по textarea", driver=driver)

    # очистка
    textarea.send_keys(Keys.CONTROL, "a")
    textarea.send_keys(Keys.BACKSPACE)
    _pause(min(pause_sec, 0.3) if pause_sec > 0 else 0.0, why="после очистки textarea", driver=driver)

    # React-friendly set value
    ok = driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.focus();
        const proto = window.HTMLTextAreaElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
            desc.set.call(el, value);
        } else {
            el.value = value;
        }
        el.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            cancelable: true,
            inputType: 'insertText',
            data: value
        }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return el.value || '';
        """,
        textarea,
        letter,
    )
    logger.debug("после native setter value_len={}", len(ok or ""))

    # если React не принял — дописываем через send_keys (как человек)
    current = _textarea_value(driver)
    if len(current.strip()) < max(20, len(letter) // 5):
        logger.info("React не принял JS-value — ввожу через send_keys")
        textarea.click()
        textarea.send_keys(Keys.CONTROL, "a")
        textarea.send_keys(Keys.BACKSPACE)
        # чанками, чтобы не рвать драйвер на длинном тексте
        chunk = 200
        for i in range(0, len(letter), chunk):
            textarea.send_keys(letter[i : i + chunk])
        current = _textarea_value(driver)

    if not current.strip():
        raise LetterSubmitUnavailable(
            "Не удалось заполнить textarea сопроводительного (value пустой)"
        )

    # снять фокус / вернуть — часто триггерит валидацию magritte
    try:
        textarea.send_keys(Keys.TAB)
    except Exception:
        driver.execute_script("arguments[0].blur();", textarea)
    _pause(pause_sec, why="после ввода письма", driver=driver)
    clear_highlight(driver)
    logger.info("textarea заполнена, len={}", len(current))


def _try_close_popup(driver: WebDriver, *, pause_sec: float) -> None:
    try:
        close_btn = _find_first(
            driver,
            [
                (By.CSS_SELECTOR, sel.RESPONSE_POPUP_CLOSE),
                (
                    By.XPATH,
                    "//button[.//span[contains(normalize-space(.),'Закрыть')]]",
                ),
            ],
            timeout=2.0,
        )
        _click(driver, close_btn, pause_sec=min(pause_sec, 1.5), label="Закрыть попап")
    except Exception as exc:  # noqa: BLE001
        logger.debug("не удалось закрыть попап: {}", exc)
