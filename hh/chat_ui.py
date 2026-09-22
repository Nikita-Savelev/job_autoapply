"""Selenium: список чатов / тред / отправка (полноэкранный hh.ru/chat)."""

from __future__ import annotations

import re
import time

from loguru import logger
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from chat.dom_parse import (
    is_message_root_qa,
    parse_chat_ids_from_list,
    parse_employer_hh_id,
    parse_messages_html,
    parse_participant_subtitle,
    parse_participant_title,
    parse_vacancy_hh_id,
)
from chat.models import ChatMessage, ChatThread, MessageDirection
from debug.page_dump import PageDumper
from hh import selectors as sel
from hh.navigate import go


CHAT_LIST_URL = sel.CHAT_PAGE_URL


def open_chat_list(
    driver: WebDriver,
    *,
    dumper: PageDumper | None = None,
    pause_sec: float = 2.5,
) -> None:
    from hh.highlight import show_banner

    show_banner(driver, "Открываю список чатов")
    go(driver, CHAT_LIST_URL, dumper=dumper, label="chat_list")
    time.sleep(pause_sec)


def open_chat_thread(
    driver: WebDriver,
    chat_id: str,
    *,
    dumper: PageDumper | None = None,
    pause_sec: float = 2.5,
) -> None:
    from hh.highlight import flash_click, show_banner

    show_banner(driver, f"Открываю чат {chat_id}")
    # клик по ячейке списка, если есть; иначе прямой URL
    cells = driver.find_elements(
        By.CSS_SELECTOR, f"[data-qa='chatik-open-chat-{chat_id}']"
    )
    if cells:
        flash_click(driver, cells[0], label=f"open chat {chat_id}")
        try:
            cells[0].click()
        except Exception:
            go(
                driver,
                f"{CHAT_LIST_URL}/{chat_id}",
                dumper=dumper,
                label=f"chat_{chat_id}",
                chat_id=chat_id,
            )
    else:
        go(
            driver,
            f"{CHAT_LIST_URL}/{chat_id}",
            dumper=dumper,
            label=f"chat_{chat_id}",
            chat_id=chat_id,
        )
    time.sleep(pause_sec)
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, sel.CHAT_MESSAGES_SCROLLER)
            )
        )
    except TimeoutException:
        logger.warning("chat {}: нет #chatik_messages_scroller", chat_id)
        return
    # иначе HH оставляет чат «непрочитанным» в списке
    scroll_thread_to_latest(driver, pause_sec=min(1.0, pause_sec))


def scroll_thread_to_latest(
    driver: WebDriver,
    *,
    pause_sec: float = 0.8,
) -> None:
    """Пролистать тред до конца (прочитать / снять unread)."""
    from hh.highlight import show_banner

    show_banner(driver, "Листаю к последнему сообщению")
    try:
        for btn in driver.find_elements(By.CSS_SELECTOR, sel.CHAT_SCROLL_DOWN):
            try:
                if btn.is_displayed():
                    btn.click()
                    time.sleep(pause_sec)
                    break
            except Exception:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("scroll-down button: {}", exc)

    try:
        for _ in range(4):
            at_bottom = driver.execute_script(
                """
                const el = document.querySelector('#chatik_messages_scroller');
                if (!el) return true;
                el.scrollTop = el.scrollHeight;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
                const msgs = el.querySelectorAll(
                  "[data-qa^='chatik-chat-message-']"
                );
                if (msgs.length) {
                  msgs[msgs.length - 1].scrollIntoView(
                    { block: 'end', inline: 'nearest' }
                  );
                }
                el.scrollTop = el.scrollHeight;
                return el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
                """
            )
            time.sleep(pause_sec)
            if at_bottom:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("scroll_thread_to_latest failed: {}", exc)


def list_thread_ids(driver: WebDriver) -> list[str]:
    """ID из списка слева (data-qa=chatik-open-chat-*)."""
    return parse_chat_ids_from_list(driver.page_source)


def list_threads(driver: WebDriver) -> list[ChatThread]:
    """Карточки списка: id, title, subtitle, preview (в т.ч. бейдж «Отказ»)."""
    threads: list[ChatThread] = []
    cells = driver.find_elements(By.CSS_SELECTOR, sel.CHATIK_OPEN_CHAT)
    for cell in cells:
        qa = cell.get_attribute("data-qa") or ""
        m = re.match(r"chatik-open-chat-(\d+)$", qa)
        if not m:
            continue
        chat_id = m.group(1)
        title = _safe_child_text(cell, sel.CHAT_CELL_TITLE)
        subtitle = _safe_child_text(cell, sel.CHAT_CELL_SUBTITLE)
        preview = _cell_last_message_preview(cell) or subtitle
        threads.append(
            ChatThread(
                chat_id=chat_id,
                title=title or "",
                subtitle=subtitle,
                url=f"{CHAT_LIST_URL}/{chat_id}",
                last_message_preview=preview,
            )
        )
    return threads


def _cell_last_message_preview(cell: WebElement) -> str | None:
    """Текст под вакансией: бейдж «Отказ» / превью сообщения."""
    try:
        els = cell.find_elements(By.CSS_SELECTOR, sel.CHAT_CELL_LAST_MESSAGE)
        for el in els:
            t = " ".join((el.text or "").split()).strip()
            if t:
                return t[:500]
    except Exception:
        pass
    return _safe_child_text(cell, sel.CHAT_CELL_META)


def read_thread(driver: WebDriver, *, chat_id: str | None = None) -> ChatThread:
    """Текущий открытый тред: шапка + сообщения."""
    html = driver.page_source
    if not chat_id:
        m = re.search(r"/chat/(\d+)", driver.current_url or "")
        chat_id = m.group(1) if m else "unknown"
    messages = parse_messages_html(html)
    # fallback через элементы, если парсер пуст
    if not messages:
        messages = _read_messages_via_elements(driver)
    header_title = parse_participant_title(html) or _safe_text(
        driver, sel.CHAT_PARTICIPANT_TITLE
    )
    header_sub = parse_participant_subtitle(html) or _safe_text(
        driver, sel.CHAT_PARTICIPANT_SUBTITLE
    )
    # холодный чат: title=вакансия, subtitle=компания; иначе title=компания
    company_name = header_sub or header_title
    company_hh_id = parse_employer_hh_id(html)
    vacancy_hh_id = parse_vacancy_hh_id(html)
    thread = ChatThread(
        chat_id=chat_id,
        # title оставляем пустым — вакансию берём из карточки списка
        title="",
        subtitle=company_name,
        url=f"{CHAT_LIST_URL}/{chat_id}",
        company_hh_id=company_hh_id,
        vacancy_hh_id=vacancy_hh_id,
        company=company_name,
        messages=messages,
        last_message_preview=messages[-1].text[:500] if messages else None,
    )
    return thread


def read_messages(driver: WebDriver) -> list[ChatMessage]:
    return read_thread(driver).messages


def fill_message(
    driver: WebDriver,
    text: str,
    *,
    pause_sec: float = 2.5,
) -> bool:
    """Вставить текст в composer (без отправки)."""
    from hh.highlight import clear_highlight, flash_click, show_banner

    body = (text or "").strip()
    if not body:
        return False
    try:
        area = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, sel.CHAT_THREAD_TEXTAREA))
        )
    except TimeoutException:
        logger.error("chat: не найден textarea composer")
        return False
    try:
        flash_click(driver, area, label="composer textarea")
        show_banner(driver, f"Ввод ответа ({len(body)} симв.)")
        time.sleep(pause_sec)
        area.click()
        time.sleep(pause_sec)
        area.clear()
        area.send_keys(body)
        time.sleep(pause_sec)
        clear_highlight(driver)
        logger.info("chat: текст вставлен ({} симв.)", len(body))
        return True
    except (StaleElementReferenceException, ElementClickInterceptedException) as exc:
        logger.error("chat fill failed: {}", exc)
        return False


def wait_manual_continue(*, prompt: str) -> None:
    """Enter в терминале = пользователь отправил сообщение в UI."""
    try:
        input(f"{prompt}\n→ Enter в терминале, чтобы проверить доставку и идти дальше… ")
    except EOFError:
        logger.warning("stdin закрыт — продолжаю без подтверждения")


def click_send(driver: WebDriver, *, pause_sec: float = 2.5) -> bool:
    """Нажать «Отправить» (кнопка или Ctrl+Enter). Пауза HH_PAUSE_SEC вокруг клика."""
    from selenium.webdriver.common.keys import Keys

    from hh.highlight import flash_click, show_banner

    show_banner(driver, "Отправляю сообщение")
    time.sleep(pause_sec)

    btn = _find_send_button(driver)
    if btn is not None:
        try:
            flash_click(driver, btn, label="Отправить")
            time.sleep(pause_sec)
            btn.click()
            time.sleep(pause_sec)
            logger.info("chat: отправлено кнопкой")
            show_banner(driver, "Отправлено")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat: клик Отправить не удался: {}", exc)

    # fallback: Ctrl+Enter в textarea (Enter без Ctrl = перенос строки)
    try:
        area = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, sel.CHAT_THREAD_TEXTAREA))
        )
        flash_click(driver, area, label="composer Ctrl+Enter")
        time.sleep(pause_sec)
        area.click()
        time.sleep(min(0.4, pause_sec))
        area.send_keys(Keys.CONTROL, Keys.ENTER)
        time.sleep(pause_sec)
        logger.info("chat: отправлено Ctrl+Enter")
        show_banner(driver, "Отправлено (Ctrl+Enter)")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("chat: авто-отправка не удалась: {}", exc)
        show_banner(driver, "Ошибка отправки")
        return False


def send_message(
    driver: WebDriver,
    text: str,
    *,
    pause_sec: float = 2.5,
) -> bool:
    """Вставить текст и отправить."""
    if not fill_message(driver, text, pause_sec=pause_sec):
        return False
    return click_send(driver, pause_sec=pause_sec)


def _find_send_button(driver: WebDriver):
    """Кнопка отправки рядом с composer."""
    css_list = list(sel.CHAT_SEND_BUTTONS) + [
        "[data-qa*='send']:not([disabled])",
        "[data-qa*='submit']:not([disabled])",
    ]
    for css in css_list:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                if el.is_displayed() and el.is_enabled():
                    return el
        except Exception:
            continue
    xpaths = (
        "//button[contains(@aria-label,'Отправить') and not(contains(@aria-label,'запись'))"
        " and not(contains(@aria-label,'Отменить')) and not(@disabled)]",
        "//div[@data-qa='chatik-message-input']"
        "//button[not(@disabled) and not(@aria-label='uploadFileButton')"
        " and not(contains(@aria-label,'Отменить'))"
        " and not(contains(@aria-label,'запись'))]",
        "//div[contains(@class,'magritte-right')]"
        "//button[not(@disabled) and not(@aria-label='uploadFileButton')]",
    )
    for xp in xpaths:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed() and el.is_enabled():
                    return el
        except Exception:
            continue
    return None


def verify_outbound_delivered(
    driver: WebDriver,
    expected_text: str,
    *,
    chat_id: str | None = None,
) -> bool:
    """После ручной отправки: наше исходящее видно в истории (не обязательно last)."""
    from hh.highlight import show_banner

    live = read_thread(driver, chat_id=chat_id)
    ok = outbound_matches(live.messages, expected_text)
    if ok:
        show_banner(driver, "Исходящее видно в чате — OK")
        logger.info("chat {}: исходящее подтверждено в DOM", chat_id or "?")
        if live.needs_our_reply():
            logger.info(
                "chat {}: после нашего ответа уже есть новое входящее — снова awaiting_us",
                chat_id or "?",
            )
    else:
        show_banner(driver, "Исходящее НЕ найдено — статус не меняем")
        last = live.messages[-1] if live.messages else None
        logger.error(
            "chat {}: verify fail; last={!r} expected[:80]={!r}",
            chat_id or "?",
            (last.direction.value, (last.text[:80] if last else None)),
            expected_text[:80],
        )
    return ok


def outbound_matches(messages: list[ChatMessage], expected_text: str) -> bool:
    """Наше исходящее есть среди последних сообщений (не обязательно самое последнее).

    HR/бот может сразу ответить — last будет IN, но наш OUT выше по истории.
    """
    if not messages or not (expected_text or "").strip():
        return False
    # смотрим с конца: любой OUT с похожим текстом
    for msg in reversed(messages[-12:]):
        if msg.direction != MessageDirection.OUT:
            continue
        if _text_soft_match(msg.text, expected_text):
            return True
    return False


def composer_is_writable(driver: WebDriver) -> bool:
    """Можно ли писать в чат (есть активный textarea)."""
    areas = driver.find_elements(By.CSS_SELECTOR, sel.CHAT_THREAD_TEXTAREA)
    if not areas:
        # запасной селектор
        areas = driver.find_elements(By.CSS_SELECTOR, sel.CHATIK_MESSAGE_INPUT)
    for area in areas:
        try:
            if not area.is_displayed():
                continue
            disabled = area.get_attribute("disabled")
            readonly = area.get_attribute("readonly")
            aria = (area.get_attribute("aria-disabled") or "").lower()
            if disabled or readonly or aria == "true":
                continue
            return True
        except StaleElementReferenceException:
            continue
    # эвристика по тексту на странице
    src = (driver.page_source or "").lower()
    if any(
        x in src
        for x in (
            "нельзя отправить",
            "отправка недоступна",
            "диалог завершён",
            "диалог завершен",
            "чат завершён",
            "чат завершен",
            "вы не можете писать",
        )
    ):
        return False
    return False


def dump_thread(
    driver: WebDriver,
    *,
    dumper: PageDumper | None,
    label: str,
    chat_id: str | None = None,
) -> None:
    if dumper is None:
        return
    cid = chat_id
    if not cid:
        m = re.search(r"/chat/(\d+)", getattr(driver, "current_url", "") or "")
        cid = m.group(1) if m else None
    if not cid:
        m = re.search(r"chat_(\d+)", label or "")
        cid = m.group(1) if m else None
    try:
        dumper.save(
            driver,
            label=label,
            url=getattr(driver, "current_url", None),
            chat_id=cid,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dump chat_id={} label={!r} failed: {}", cid, label, exc)


def _text_soft_match(got: str, expected: str) -> bool:
    a = " ".join((got or "").split()).strip().lower()
    b = " ".join((expected or "").split()).strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if a[:120] == b[:120]:
        return True
    if b[:80] in a or a[:80] in b:
        return True
    return False


def last_message_is_ours(messages: list[ChatMessage]) -> bool:
    if not messages:
        return False
    return messages[-1].direction == MessageDirection.OUT


def _read_messages_via_elements(driver: WebDriver) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    roots = driver.find_elements(By.CSS_SELECTOR, sel.CHAT_MESSAGE_ROOT)
    for root in roots:
        qa = root.get_attribute("data-qa") or ""
        if not is_message_root_qa(qa):
            continue
        mid = qa.rsplit("-", 1)[-1]
        outgoing = bool(root.find_elements(By.CSS_SELECTOR, sel.CHAT_BUBBLE_OUTGOING))
        text_els = root.find_elements(By.CSS_SELECTOR, sel.CHAT_BUBBLE_TEXT)
        text = (text_els[0].text or "").strip() if text_els else ""
        if not text:
            continue
        author_els = root.find_elements(By.CSS_SELECTOR, sel.CHAT_BUBBLE_AUTHOR)
        author = (author_els[0].text or "").strip() if author_els else None
        out.append(
            ChatMessage(
                direction=(
                    MessageDirection.OUT if outgoing else MessageDirection.IN
                ),
                text=text,
                author_label=author or None,
                external_id=mid,
            )
        )
    return out


def _safe_child_text(parent: WebElement, css: str) -> str | None:
    try:
        els = parent.find_elements(By.CSS_SELECTOR, css)
        if not els:
            return None
        t = (els[0].text or "").strip()
        return t or None
    except StaleElementReferenceException:
        return None


def _safe_text(driver: WebDriver, css: str) -> str | None:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if not els:
            return None
        return (els[0].text or "").strip() or None
    except Exception:
        return None
