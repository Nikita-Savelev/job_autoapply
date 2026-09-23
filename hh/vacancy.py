"""Парсинг страницы одной вакансии + открытие в отдельной вкладке."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from debug.page_dump import PageDumper
from hh import selectors as sel
from hh.navigate import dump_current


@dataclass
class VacancyPage:
    title: str
    description: str
    url: str


@contextmanager
def vacancy_tab(
    driver: WebDriver,
    url: str,
    *,
    dumper: PageDumper | None = None,
    label: str = "vacancy",
    pause_sec: float = 2.0,
) -> Iterator[None]:
    """Открыть вакансию в новой вкладке, вернуть управление, затем закрыть вкладку.

    Список поиска остаётся в исходной вкладке.
    """
    import time

    main = driver.current_window_handle
    logger.info("Открываю вакансию в новой вкладке: {}", url)
    try:
        from hh.highlight import show_banner

        show_banner(driver, f"Открываю вакансию: {url[:80]}")
    except Exception:
        pass
    driver.switch_to.new_window("tab")
    try:
        driver.get(url)
        time.sleep(pause_sec)
        if dumper is not None:
            try:
                dumper.save(driver, label=label, url=url)
            except Exception as exc:  # noqa: BLE001
                logger.debug("dump vacancy tab failed: {}", exc)
        yield
    finally:
        try:
            driver.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close vacancy tab failed: {}", exc)
        try:
            driver.switch_to.window(main)
            logger.debug("Вернулся на вкладку списка")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось вернуться на список: {}", exc)
            handles = driver.window_handles
            if handles:
                driver.switch_to.window(handles[0])


def scrape_vacancy_page(
    driver: WebDriver,
    *,
    dumper: PageDumper | None = None,
) -> VacancyPage:
    """Собрать название и описание с открытой страницы вакансии."""
    title = ""
    description = ""
    try:
        title = driver.find_element(By.CSS_SELECTOR, sel.VACANCY_TITLE).text.strip()
    except Exception as exc:
        logger.debug("VACANCY_TITLE не найден: {}", exc)
        title = driver.title
    try:
        description = driver.find_element(
            By.CSS_SELECTOR, sel.VACANCY_DESCRIPTION
        ).text.strip()
    except Exception as exc:
        logger.debug("VACANCY_DESCRIPTION не найден: {}", exc)
        description = ""

    logger.debug("scrape_vacancy title={!r} desc_len={}", title, len(description))
    if not description:
        dump_current(driver, dumper, label="vacancy_no_description")

    return VacancyPage(title=title, description=description, url=driver.current_url)


def vacancy_closed_reason(driver: WebDriver) -> str | None:
    """Архив или закрытый доступ: в такую вакансию больше не заходим.

    Маркеры сняты с живых страниц:
    - архив 134485865: data-qa vacancy-title-archived-text / vacancy-archive-description,
      текст «В архиве с …»;
    - закрытый доступ 137159216: «Вам недоступна эта вакансия».
    """
    try:
        raw = driver.page_source or ""
    except Exception:
        return None
    # Только реальные узлы страницы. Фразы из словаря переводов
    # (applicant.negotiations.vacancyarchived) есть на каждой вакансии.
    if (
        'data-qa="vacancy-title-archived-text"' in raw
        or 'data-qa="vacancy-archive-description"' in raw
    ):
        return "archived"
    if (
        "Вам недоступна эта вакансия" in raw
        and 'data-qa="applicant-login-card"' in raw
    ):
        return "restricted"
    return None
