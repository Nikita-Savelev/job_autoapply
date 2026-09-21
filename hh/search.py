"""Сбор вакансий со страницы поиска."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from loguru import logger
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


def scrape_search_page(
    driver: WebDriver,
    *,
    dumper: PageDumper | None = None,
) -> list[Vacancy]:
    """Собрать карточки с текущей страницы выдачи."""
    cards = driver.find_elements(By.CSS_SELECTOR, sel.SEARCH_VACANCY_CARDS)
    logger.info("Найдено DOM-карточек: {} (селектор {})", len(cards), sel.SEARCH_VACANCY_CARDS)
    result: list[Vacancy] = []

    for i, card in enumerate(cards, start=1):
        try:
            title_el = card.find_element(By.CSS_SELECTOR, sel.SEARCH_VACANCY_TITLE)
            href = title_el.get_attribute("href") or ""
            try:
                title = card.find_element(
                    By.CSS_SELECTOR, sel.SEARCH_VACANCY_TITLE_TEXT
                ).text.strip()
            except Exception:
                title = title_el.text.strip()
        except Exception as exc:
            logger.debug("Карточка #{}: нет title ({})", i, exc)
            continue

        hh_id = _extract_hh_id(href)
        if not hh_id or not title:
            logger.debug("Карточка #{}: пропуск (hh_id/title пусты)", i)
            continue

        company = _safe_text(card, sel.SEARCH_VACANCY_COMPANY) or _safe_text(
            card, sel.SEARCH_VACANCY_COMPANY_FALLBACK
        )
        salary = _safe_text(card, sel.SEARCH_VACANCY_SALARY)
        address = _safe_text(card, sel.SEARCH_VACANCY_ADDRESS)
        snippet = _safe_text(card, sel.SEARCH_VACANCY_SNIPPET) or address

        vac = Vacancy(
            hh_id=hh_id,
            title=title,
            url=href.split("?")[0],
            company=company or None,
            salary=salary or None,
            snippet=snippet or None,
        )
        result.append(vac)
        logger.debug(
            "Карточка #{}: id={} title={!r} company={!r} salary={!r}",
            i,
            hh_id,
            title,
            company,
            salary,
        )

    if not result:
        logger.warning("0 вакансий после парсинга — дамп HTML для правки селекторов")
        dump_current(driver, dumper, label="search_empty_or_bad_selectors")
    else:
        logger.info("Спарсено вакансий: {}", len(result))

    return result


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
        return False

    target = None
    for el in nexts:
        try:
            if el.is_displayed() and el.is_enabled():
                target = el
                break
        except Exception:
            continue
    if target is None:
        logger.info("Следующей страницы поиска нет (pager-next отсутствует)")
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


def _safe_text(root, css: str) -> str:
    try:
        return root.find_element(By.CSS_SELECTOR, css).text.strip()
    except Exception:
        return ""
