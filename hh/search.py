"""Сбор вакансий со страницы поиска."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from loguru import logger
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from db.models import Vacancy
from debug.page_dump import PageDumper
from hh import selectors as sel
from hh.navigate import dump_current, go


def _extract_hh_id(url: str) -> str | None:
    path = urlparse(url).path
    m = re.search(r"/vacancy/(\d+)", path)
    return m.group(1) if m else None


def open_search(
    driver: WebDriver,
    search_url: str,
    *,
    dumper: PageDumper | None = None,
) -> None:
    logger.info("Открываю поиск: {}", search_url)
    go(driver, search_url, dumper=dumper, label="search")
    logger.debug("URL после перехода: {}", driver.current_url)


_SCROLL_JS = """
const cards = document.querySelectorAll(arguments[0]);
if (cards.length) {
  cards[cards.length - 1].scrollIntoView({block: 'end'});
} else {
  window.scrollTo(0, document.body.scrollHeight);
}
return cards.length;
"""


def _scroll_search_results(driver: WebDriver) -> None:
    """Докрутить выдачу, пока не появятся все карточки страницы.

    HH сначала рисует часть списка (часто ~20) и догружает остальные при прокрутке.
    Карточки ищутся внутри скрипта: ссылка из Python устаревает, когда список перерисовывается.
    """
    import time

    last = -1
    stable = 0
    for _ in range(25):
        try:
            n = int(
                driver.execute_script(_SCROLL_JS, sel.SEARCH_VACANCY_CARDS) or 0
            )
        except StaleElementReferenceException:
            time.sleep(0.45)
            continue
        if n >= 100:
            logger.info("Выдача прокручена: карточек {}", n)
            return
        if n == last:
            stable += 1
            if stable >= 3:
                logger.info("Выдача прокручена: карточек {} (больше не растёт)", n)
                return
        else:
            stable = 0
            last = n
        time.sleep(0.45)
    logger.info("Выдача: стоп прокрутки, карточек {}", max(last, 0))


def _node_text(card, css: str) -> tuple[str, str]:
    """Текст и href узла. textContent читается и у карточки вне экрана."""
    try:
        el = card.find_element(By.CSS_SELECTOR, css)
    except Exception:
        return "", ""
    try:
        text = (el.get_attribute("textContent") or "").strip()
    except Exception:
        text = ""
    if not text:
        try:
            text = (el.text or "").strip()
        except Exception:
            text = ""
    try:
        href = (el.get_attribute("href") or "").strip()
    except Exception:
        href = ""
    return text, href


_CARDS_JS = """
const textOf = (root, css) => {
  if (!root) return '';
  const el = root.querySelector(css);
  return el ? (el.textContent || '').replace(/\\s+/g, ' ').trim() : '';
};
const cards = document.querySelectorAll(arguments[0]);
const out = [];
for (const card of cards) {
  const titleA = card.querySelector(arguments[1]);
  const href = titleA ? (titleA.href || titleA.getAttribute('href') || '') : '';
  let title = textOf(card, arguments[2]);
  if (!title && titleA) title = (titleA.textContent || '').replace(/\\s+/g, ' ').trim();
  const company = textOf(card, arguments[3]) || textOf(card, arguments[4]);
  const salary = textOf(card, arguments[5]);
  const address = textOf(card, arguments[6]);
  const snippet = textOf(card, arguments[7]) || address;
  out.push({title, href, company, salary, snippet});
}
return out;
"""


def _parse_search_cards(driver: WebDriver) -> tuple[list[Vacancy], int]:
    """Один снимок DOM. Поэлементный Selenium теряет текст, пока список дорисовывается."""
    try:
        raw = driver.execute_script(
            _CARDS_JS,
            sel.SEARCH_VACANCY_CARDS,
            sel.SEARCH_VACANCY_TITLE,
            sel.SEARCH_VACANCY_TITLE_TEXT,
            sel.SEARCH_VACANCY_COMPANY,
            sel.SEARCH_VACANCY_COMPANY_FALLBACK,
            sel.SEARCH_VACANCY_SALARY,
            sel.SEARCH_VACANCY_ADDRESS,
            sel.SEARCH_VACANCY_SNIPPET,
        )
    except Exception as exc:
        logger.warning("Снимок карточек через JS не удался: {}", exc)
        raw = None
    if not isinstance(raw, list):
        return _parse_search_cards_selenium(driver)

    result: list[Vacancy] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        href = str(item.get("href") or "").strip()
        hh_id = _extract_hh_id(href)
        if not hh_id or not title:
            logger.debug("Карточка #{}: пропуск (hh_id/title пусты)", i)
            continue
        company = str(item.get("company") or "").strip()
        salary = str(item.get("salary") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        result.append(
            Vacancy(
                hh_id=hh_id,
                title=title,
                url=href.split("?")[0],
                company=company or None,
                salary=salary or None,
                snippet=snippet or None,
            )
        )
    return result, len(raw)


def _parse_search_cards_selenium(driver: WebDriver) -> tuple[list[Vacancy], int]:
    cards = driver.find_elements(By.CSS_SELECTOR, sel.SEARCH_VACANCY_CARDS)
    result: list[Vacancy] = []
    for i, card in enumerate(cards, start=1):
        title, href = _node_text(card, sel.SEARCH_VACANCY_TITLE_TEXT)
        title_href_text, title_href = _node_text(card, sel.SEARCH_VACANCY_TITLE)
        if not title:
            title = title_href_text
        if not href:
            href = title_href
        hh_id = _extract_hh_id(href)
        if not hh_id or not title:
            logger.debug("Карточка #{}: пропуск (hh_id/title пусты)", i)
            continue
        company, _ = _node_text(card, sel.SEARCH_VACANCY_COMPANY)
        if not company:
            company, _ = _node_text(card, sel.SEARCH_VACANCY_COMPANY_FALLBACK)
        salary, _ = _node_text(card, sel.SEARCH_VACANCY_SALARY)
        address, _ = _node_text(card, sel.SEARCH_VACANCY_ADDRESS)
        snippet, _ = _node_text(card, sel.SEARCH_VACANCY_SNIPPET)
        if not snippet:
            snippet = address
        result.append(
            Vacancy(
                hh_id=hh_id,
                title=title,
                url=href.split("?")[0],
                company=company or None,
                salary=salary or None,
                snippet=snippet or None,
            )
        )
    return result, len(cards)


def scrape_search_page(
    driver: WebDriver,
    *,
    dumper: PageDumper | None = None,
) -> list[Vacancy]:
    """Собрать карточки с текущей страницы выдачи."""
    import time

    _scroll_search_results(driver)
    merged: dict[str, Vacancy] = {}
    dom_n = 0
    for attempt in range(1, 4):
        parsed, dom_n = _parse_search_cards(driver)
        for vac in parsed:
            merged[vac.hh_id] = vac
        logger.info(
            "Найдено DOM-карточек: {} разобрано: {} всего: {} (попытка {})",
            dom_n,
            len(parsed),
            len(merged),
            attempt,
        )
        if dom_n == 0 or len(merged) >= dom_n:
            break
        logger.warning(
            "Разобрано {} из {} карточек — жду и читаю страницу снова",
            len(merged),
            dom_n,
        )
        time.sleep(0.8)
        _scroll_search_results(driver)
    result = list(merged.values())
    if dom_n and len(result) < dom_n:
        logger.warning(
            "После повторов разобрано {} из {} DOM-карточек",
            len(result),
            dom_n,
        )

    if not result:
        logger.warning("0 вакансий после парсинга — дамп HTML для правки селекторов")
        dump_current(driver, dumper, label="search_empty_or_bad_selectors")
    else:
        logger.info("Спарсено вакансий: {}", len(result))

    return result


def _current_search_page(url: str) -> int:
    from urllib.parse import parse_qs, urlparse

    raw = parse_qs(urlparse(url).query).get("page", ["0"])
    try:
        return int(raw[0])
    except (TypeError, ValueError):
        return 0


def _next_numbered_page(driver: WebDriver):
    """Следующий номер в pager-page, если стрелки «дальше» уже нет.

    На хвосте выдачи HH оставляет ссылки 17–20, но снимает data-qa=pager-next.
    """
    from urllib.parse import parse_qs, urlparse

    current = _current_search_page(driver.current_url)
    best = None
    best_n = None
    try:
        links = driver.find_elements(By.CSS_SELECTOR, sel.SEARCH_PAGER_PAGE)
    except Exception as exc:
        logger.debug("pager-page не найден: {}", exc)
        return None
    for el in links:
        try:
            if not el.is_displayed():
                continue
            if (el.get_attribute("aria-current") or "").lower() == "true":
                continue
            href = (el.get_attribute("href") or "").strip()
            if not href:
                continue
            raw = parse_qs(urlparse(href).query).get("page", [])
            if not raw:
                continue
            n = int(raw[0])
        except Exception:
            continue
        if n <= current:
            continue
        if best_n is None or n < best_n:
            best = el
            best_n = n
    return best


def go_next_search_page(
    driver: WebDriver,
    *,
    dumper: PageDumper | None = None,
    pause_sec: float = 0.0,
) -> bool:
    """Клик по «следующая страница». False — если кнопки нет / конец выдачи."""
    import time

    try:
        nexts = driver.find_elements(By.CSS_SELECTOR, sel.SEARCH_PAGER_NEXT)
    except Exception as exc:
        logger.debug("pager-next не найден: {}", exc)
        nexts = []

    target = None
    for el in nexts:
        try:
            if el.is_displayed() and el.is_enabled():
                target = el
                break
        except Exception:
            continue
    if target is None:
        target = _next_numbered_page(driver)
    if target is None:
        logger.info("Следующей страницы поиска нет")
        return False

    href = (target.get_attribute("href") or "").strip()
    before = driver.current_url
    logger.info("Переход на следующую страницу поиска: {}", href or "(click)")
    try:
        from hh.highlight import show_banner

        show_banner(driver, "Следующая страница поиска")
    except Exception:
        pass

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", target
        )
        target.click()
    except Exception:
        if href:
            driver.get(href)
        else:
            logger.warning("Не удалось кликнуть pager-next и нет href")
            return False

    if pause_sec > 0:
        time.sleep(pause_sec)
    else:
        time.sleep(0.8)

    after = driver.current_url
    if after == before and href and href not in after:
        # иногда SPA не успела — форс по href
        try:
            driver.get(href)
            if pause_sec > 0:
                time.sleep(pause_sec)
        except Exception:
            pass

    if dumper is not None:
        try:
            dumper.save(driver, label="search_next_page", url=driver.current_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("dump search_next_page failed: {}", exc)

    logger.info("URL после пагинации: {}", driver.current_url)
    return True
