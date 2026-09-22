"""Опросник работодателя при отклике: сбор вопросов, ответы, письмо, submit."""

from __future__ import annotations

import time

from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from hh import selectors as sel
from hh.highlight import clear_highlight, flash_click, show_banner, visual_enabled
from matcher.test_answers import (
    QuestionKind,
    TestAnswer,
    TestQuestion,
    answer_test_questions,
    serialize_test_qa,
)


def looks_like_response_test(driver: WebDriver) -> bool:
    """Экран «Ответьте на вопросы» / task-body."""
    for css in (
        sel.TEST_ASKING,
        sel.TEST_TASK_BODY,
        *sel.RESPONSE_TEST_MARKERS,
    ):
        try:
            if any(
                el.is_displayed()
                for el in driver.find_elements(By.CSS_SELECTOR, css)
            ):
                return True
        except Exception:
            continue
    return False


def scrape_test_questions(driver: WebDriver) -> list[TestQuestion]:
    """Собрать все блоки task-body: текст вопроса + тип + опции."""
    bodies = [
        el
        for el in driver.find_elements(By.CSS_SELECTOR, sel.TEST_TASK_BODY)
        if el.is_displayed()
    ]
    questions: list[TestQuestion] = []
    for idx, body in enumerate(bodies):
        q_el = _first_displayed(body, sel.TEST_QUESTION)
        text = (q_el.text if q_el is not None else body.text or "").strip()
        text = " ".join(text.split())
        if not text:
            continue

        cells = [
            c
            for c in body.find_elements(By.CSS_SELECTOR, sel.TEST_OPTION_CELL)
            if c.is_displayed()
        ]
        options: list[str] = []
        has_custom = False
        for cell in cells:
            label = _cell_label(cell)
            if not label:
                continue
            if _is_custom_option(label):
                has_custom = True
                continue
            if label not in options:
                options.append(label)

        has_radio = bool(
            body.find_elements(By.CSS_SELECTOR, f"{sel.TEST_RADIO}, input[type='radio']")
        )
        has_check = bool(
            body.find_elements(
                By.CSS_SELECTOR, f"{sel.TEST_CHECKBOX}, input[type='checkbox']"
            )
        )
        textareas = [
            t
            for t in body.find_elements(By.CSS_SELECTOR, sel.TEST_TEXTAREA)
            if t.is_displayed()
        ]

        if has_check:
            kind = QuestionKind.MULTI
        elif has_radio or options:
            kind = QuestionKind.SINGLE
        elif textareas:
            kind = QuestionKind.TEXT
        else:
            kind = QuestionKind.TEXT

        questions.append(
            TestQuestion(
                index=idx,
                text=text,
                kind=kind,
                options=tuple(options),
                has_custom=has_custom
                or (kind in (QuestionKind.SINGLE, QuestionKind.MULTI) and bool(textareas)),
            )
        )
        logger.info(
            "test Q[{}] kind={} opts={} custom={} text={!r}",
            idx,
            kind.value,
            len(options),
            has_custom,
            text[:120],
        )
    return questions


def complete_response_test(
    driver: WebDriver,
    *,
    letter: str,
    resume_text: str,
    vacancy_title: str = "",
    company: str = "",
    vacancy_description: str = "",
    pause_sec: float = 2.0,
    dumper=None,
    submit: bool = True,
) -> str:
    """Ответить на все вопросы, прикрепить письмо, опционально нажать «Откликнуться».

    Returns:
        JSON вопросов и ответов (для сохранения в БД).

    Args:
        submit: если False — только заполняем форму (для ручной проверки), submit не жмём.

    Raises:
        Exception: если не удалось собрать/заполнить/отправить.
    """
    show_banner(driver, "Опросник работодателя — собираю вопросы")
    questions = scrape_test_questions(driver)
    if not questions:
        raise RuntimeError("Экран теста есть, но вопросы не найдены")

    if dumper is not None:
        try:
            dumper.save(driver, label="response_test_questions")
        except Exception:  # noqa: BLE001
            pass

    answers = answer_test_questions(
        questions,
        resume_text=resume_text,
        vacancy_title=vacancy_title,
        company=company,
        vacancy_description=vacancy_description,
    )
    if len(answers) != len(questions):
        raise RuntimeError(
            f"Ответов {len(answers)} != вопросов {len(questions)}"
        )

    qa_json = serialize_test_qa(questions, answers)
    logger.info("опросник: {} вопросов, qa_json_len={}", len(questions), len(qa_json))

    bodies = [
        el
        for el in driver.find_elements(By.CSS_SELECTOR, sel.TEST_TASK_BODY)
        if el.is_displayed()
    ]
    for q, ans in zip(questions, answers, strict=True):
        if q.index >= len(bodies):
            raise RuntimeError(f"Нет DOM для вопроса [{q.index}]")
        body = bodies[q.index]
        _scroll(driver, body)
        show_banner(driver, f"Ответ на вопрос {q.index + 1}/{len(questions)}")
        _fill_question(driver, body, q, ans, pause_sec=pause_sec)

    _attach_letter_in_test(driver, letter, pause_sec=pause_sec)

    if dumper is not None:
        try:
            dumper.save(driver, label="response_test_filled")
        except Exception:  # noqa: BLE001
            pass

    if not submit:
        logger.info(
            "опросник заполнен ({} вопросов), submit пропущен (review)",
            len(questions),
        )
        show_banner(driver, "Форма заполнена — проверь вручную (submit не жали)")
        return qa_json

    _submit_test_form(driver, pause_sec=pause_sec)

    if dumper is not None:
        try:
            dumper.save(driver, label="response_test_submitted")
        except Exception:  # noqa: BLE001
            pass

    logger.info("опросник отправлен ({} вопросов)", len(questions))
    show_banner(driver, "Опросник отправлен")
    return qa_json


def _fill_question(
    driver: WebDriver,
    body: WebElement,
    question: TestQuestion,
    answer: TestAnswer,
    *,
    pause_sec: float,
) -> None:
    kind = question.kind
    if kind == QuestionKind.TEXT:
        ta = _first_displayed(body, sel.TEST_TEXTAREA)
        if ta is None:
            raise RuntimeError(f"Q[{question.index}]: нет textarea")
        if not answer.text.strip():
            raise RuntimeError(f"Q[{question.index}]: пустой текстовый ответ")
        _fill_textarea(driver, ta, answer.text, pause_sec=pause_sec)
        return

    if answer.use_custom or (
        question.has_custom
        and answer.text
        and not answer.selected
    ):
        custom_cell = _find_custom_cell(body)
        if custom_cell is None:
            raise RuntimeError(f"Q[{question.index}]: нет «Свой вариант»")
        _click_el(driver, custom_cell, pause_sec=pause_sec, label="Свой вариант")
        ta = _first_displayed(body, sel.TEST_TEXTAREA)
        if ta is None:
            raise RuntimeError(f"Q[{question.index}]: нет textarea для своего варианта")
        _fill_textarea(driver, ta, answer.text, pause_sec=pause_sec)
        return

    selected = list(answer.selected)
    if not selected:
        raise RuntimeError(f"Q[{question.index}]: не выбраны опции")

    if kind == QuestionKind.SINGLE:
        selected = selected[:1]

    for want in selected:
        cell = _find_option_cell(body, want)
        if cell is None:
            raise RuntimeError(
                f"Q[{question.index}]: опция не найдена: {want!r}"
            )
        if kind == QuestionKind.MULTI and _option_is_checked(cell):
            logger.info(
                "Q[{}]: чекбокс {!r} уже выбран — клик пропускаю",
                question.index,
                want[:40],
            )
            continue
        # radio: повторный клик обычно безвреден, но тоже пропускаем если уже выбран
        if kind == QuestionKind.SINGLE and _option_is_checked(cell):
            logger.info(
                "Q[{}]: radio {!r} уже выбран — клик пропускаю",
                question.index,
                want[:40],
            )
            continue
        _click_el(
            driver,
            cell,
            pause_sec=pause_sec,
            label=f"опция: {want[:40]}",
        )


def _option_is_checked(cell: WebElement) -> bool:
    """Уже выбран ли radio/checkbox внутри label[data-qa=cell]."""
    try:
        for inp in cell.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], input[type='radio']"):
            try:
                if inp.is_selected():
                    return True
            except Exception:
                pass
            checked = (inp.get_attribute("checked") or "").lower()
            if checked in ("true", "checked", "1"):
                return True
            cls = (inp.get_attribute("class") or "").lower()
            if "checked" in cls and "unchecked" not in cls:
                return True
            aria = (inp.get_attribute("aria-checked") or "").lower()
            if aria == "true":
                return True
        # Magritte: активная ячейка / иконка
        cell_cls = (cell.get_attribute("class") or "").lower()
        if "magritte-checked" in cell_cls:
            return True
        for box in cell.find_elements(
            By.CSS_SELECTOR,
            "[data-qa='checkbox'], [data-qa='radio']",
        ):
            bcls = (box.get_attribute("class") or "").lower()
            if "checked" in bcls and "unchecked" not in bcls:
                return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("не удалось проверить checked: {}", exc)
    return False


def _attach_letter_in_test(
    driver: WebDriver,
    letter: str,
    *,
    pause_sec: float,
) -> None:
    if not letter.strip():
        logger.warning("письмо пустое — в тесте не прикрепляю")
        return

    show_banner(driver, "Сопроводительное в форме теста")
    # уже открытое поле
    if _exists(driver, sel.RESPONSE_LETTER_TEXTAREA):
        ta = driver.find_element(By.CSS_SELECTOR, sel.RESPONSE_LETTER_TEXTAREA)
        _fill_textarea(driver, ta, letter, pause_sec=pause_sec)
        return

    # кнопка «добавить / приложить»
    for css in (
        sel.RESPONSE_LETTER_TOGGLE,
        sel.RESPONSE_LETTER_TOGGLE_TEXT,
        "[data-qa='vacancy-response-letter-toggle']",
    ):
        try:
            btns = [
                el
                for el in driver.find_elements(By.CSS_SELECTOR, css)
                if el.is_displayed()
            ]
        except Exception:
            btns = []
        if not btns:
            continue
        _click_el(
            driver,
            btns[0],
            pause_sec=pause_sec,
            label="Приложить сопроводительное (тест)",
        )
        break

    end = time.time() + 10.0
    while time.time() < end:
        if _exists(driver, sel.RESPONSE_LETTER_TEXTAREA):
            ta = driver.find_element(By.CSS_SELECTOR, sel.RESPONSE_LETTER_TEXTAREA)
            _fill_textarea(driver, ta, letter, pause_sec=pause_sec)
            return
        time.sleep(0.25)

    logger.warning("В форме теста нет поля сопроводительного — отправляю без него")


def _submit_test_form(driver: WebDriver, *, pause_sec: float) -> None:
    show_banner(driver, "Отправляю отклик после опросника")
    end = time.time() + 12.0
    submit = None
    while time.time() < end:
        for css in (
            sel.RESPONSE_SUBMIT_POPUP,
            sel.RESPONSE_LETTER_SUBMIT,
        ):
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if el.is_displayed() and el.is_enabled():
                        submit = el
                        break
                except Exception:
                    continue
            if submit is not None:
                break
        if submit is not None:
            break
        time.sleep(0.3)

    if submit is None:
        # xpath по тексту
        for el in driver.find_elements(
            By.XPATH,
            "//button[@type='submit' and .//span[contains(.,'Откликнуться')]]"
            " | //button[contains(.,'Откликнуться')]",
        ):
            try:
                if el.is_displayed() and el.is_enabled():
                    submit = el
                    break
            except Exception:
                continue

    if submit is None:
        raise RuntimeError("Кнопка «Откликнуться» после опросника не найдена/disabled")

    _click_el(driver, submit, pause_sec=pause_sec, label="Откликнуться (после теста)")


# --- helpers ---


def _exists(driver: WebDriver, css: str) -> bool:
    try:
        return any(
            el.is_displayed() for el in driver.find_elements(By.CSS_SELECTOR, css)
        )
    except Exception:
        return False


def _first_displayed(root: WebElement, css: str) -> WebElement | None:
    try:
        for el in root.find_elements(By.CSS_SELECTOR, css):
            if el.is_displayed():
                return el
    except Exception:
        return None
    return None


def _cell_label(cell: WebElement) -> str:
    try:
        for css in (sel.TEST_OPTION_TEXT,):
            for el in cell.find_elements(By.CSS_SELECTOR, css):
                t = (el.text or "").strip()
                if t:
                    return " ".join(t.split())
        return " ".join((cell.text or "").split())
    except Exception:
        return ""


def _is_custom_option(label: str) -> bool:
    low = label.strip().lower()
    return any(t in low for t in sel.TEST_CUSTOM_OPTION_TEXTS)


def _find_custom_cell(body: WebElement) -> WebElement | None:
    for cell in body.find_elements(By.CSS_SELECTOR, sel.TEST_OPTION_CELL):
        if not cell.is_displayed():
            continue
        if _is_custom_option(_cell_label(cell)):
            return cell
    return None


def _find_option_cell(body: WebElement, want: str) -> WebElement | None:
    want_n = " ".join(want.lower().split())
    cells = [
        c
        for c in body.find_elements(By.CSS_SELECTOR, sel.TEST_OPTION_CELL)
        if c.is_displayed()
    ]
    # exact
    for cell in cells:
        lab = _cell_label(cell).lower()
        if " ".join(lab.split()) == want_n:
            return cell
    # contains
    for cell in cells:
        lab = " ".join(_cell_label(cell).lower().split())
        if want_n in lab or lab in want_n:
            if _is_custom_option(lab):
                continue
            return cell
    return None


def _scroll(driver: WebDriver, el: WebElement) -> None:
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
        el,
    )


def _pause(sec: float, *, why: str, driver: WebDriver | None = None) -> None:
    if sec <= 0:
        return
    logger.debug("pause {:.1f}s — {}", sec, why)
    if driver is not None and visual_enabled() and why:
        show_banner(driver, why)
    time.sleep(sec)


def _click_el(
    driver: WebDriver,
    el: WebElement,
    *,
    pause_sec: float,
    label: str,
) -> None:
    logger.info("click: {}", label)
    _scroll(driver, el)
    hold = 0.35 if pause_sec > 0 else 0.0
    flash_click(driver, el, label=label, hold_sec=hold)
    _pause(pause_sec, why=f"перед кликом: {label}", driver=driver)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    _pause(pause_sec, why=f"после клика: {label}", driver=driver)
    clear_highlight(driver)


def _fill_textarea(
    driver: WebDriver,
    textarea: WebElement,
    value: str,
    *,
    pause_sec: float,
) -> None:
    _scroll(driver, textarea)
    hold = 0.3 if pause_sec > 0 else 0.0
    flash_click(driver, textarea, label="textarea ответа", hold_sec=hold)
    try:
        textarea.click()
    except Exception:
        driver.execute_script("arguments[0].click();", textarea)
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
        value,
    )
    current = ok or ""
    if len(current.strip()) < max(10, len(value) // 5):
        textarea.click()
        textarea.send_keys(Keys.CONTROL, "a")
        textarea.send_keys(Keys.BACKSPACE)
        chunk = 200
        for i in range(0, len(value), chunk):
            textarea.send_keys(value[i : i + chunk])
        current = (
            driver.execute_script("return arguments[0].value || '';", textarea)
            or ""
        )
    if not current.strip():
        raise RuntimeError("Не удалось заполнить textarea ответа в тесте")
    _pause(min(pause_sec, 1.0), why="после ввода ответа", driver=driver)
    clear_highlight(driver)
