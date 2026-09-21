"""Парсинг страницы компании (работодателя) на hh.ru."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urljoin, urlparse

from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from config import HH_BASE
from db.models import Company
from db.store import VacancyStore
from debug.page_dump import PageDumper
from hh import selectors as sel


_EMPLOYER_ID_RE = re.compile(r"/employer/(\d+)")


@dataclass(frozen=True)
class EmployerLink:
    hh_id: str
    name: str
    url: str


def parse_employer_id(url: str) -> str | None:
    m = _EMPLOYER_ID_RE.search(url or "")
    return m.group(1) if m else None


def extract_employer_link(driver: WebDriver) -> EmployerLink | None:
    """Со страницы вакансии: ссылка на /employer/{id}."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, sel.VACANCY_COMPANY_NAME_LINK)
    except Exception:
        try:
            el = driver.find_element(
                By.CSS_SELECTOR,
                f"{sel.VACANCY_COMPANY_BLOCK} a[href*='/employer/']",
            )
        except Exception as exc:
            logger.debug("ссылка на компанию не найдена: {}", exc)
            return None

    href = (el.get_attribute("href") or "").strip()
    if not href:
        return None
    abs_url = urljoin(HH_BASE + "/", href)
    # без query-параметров для стабильного ключа/URL
    parsed = urlparse(abs_url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    hh_id = parse_employer_id(clean)
    if not hh_id:
        logger.debug("не разобрали employer id из {}", clean)
        return None
    name = (el.text or "").strip() or f"employer-{hh_id}"
    return EmployerLink(hh_id=hh_id, name=name, url=clean)


@contextmanager
def company_tab(
    driver: WebDriver,
    url: str,
    *,
    dumper: PageDumper | None = None,
    label: str = "company",
    pause_sec: float = 0.0,
) -> Iterator[None]:
    """Открыть компанию в новой вкладке, затем закрыть и вернуться."""
    parent = driver.current_window_handle
    logger.info("Открываю страницу компании: {}", url)
    try:
        from hh.highlight import show_banner

        show_banner(driver, f"Компания: {url[:80]}")
    except Exception:
        pass
    driver.switch_to.new_window("tab")
    try:
        driver.get(url)
        if pause_sec > 0:
            time.sleep(pause_sec)
        if dumper is not None:
            try:
                dumper.save(driver, label=label, url=url)
            except Exception as exc:  # noqa: BLE001
                logger.debug("dump company tab failed: {}", exc)
        yield
    finally:
        try:
            driver.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close company tab failed: {}", exc)
        try:
            driver.switch_to.window(parent)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось вернуться с вкладки компании: {}", exc)
            handles = driver.window_handles
            if handles:
                driver.switch_to.window(handles[-1])


def scrape_company_page(
    driver: WebDriver,
    *,
    hh_id: str,
    name_hint: str = "",
    url: str | None = None,
) -> Company:
    """Собрать описание и метаданные со страницы /employer/{id}."""
    page_url = url or driver.current_url
    name = _first_text(driver, sel.COMPANY_NAME) or name_hint or driver.title
    # убрать хвост «— работа в …» из title при необходимости
    if "—" in name and len(name) > 80:
        name = name.split("—", 1)[0].strip()
    description = _first_text(driver, sel.COMPANY_DESCRIPTION)
    industries = _first_text(driver, sel.COMPANY_INDUSTRIES)
    site = _first_attr(driver, sel.COMPANY_SITE, "href")

    # запасной вариант: длинный текстовый блок на странице
    if not description:
        description = _fallback_main_text(driver)

    extra_bits: list[str] = []
    for qa in (
        "[data-qa='company-sidebar']",
        "[data-qa='employer-sidebar']",
        "[data-qa='company-info']",
    ):
        t = _first_text(driver, qa)
        if t and t not in (description or "") and len(t) > 20:
            extra_bits.append(t)
            break

    company = Company(
        hh_id=hh_id,
        name=name[:300],
        url=page_url,
        description=description or None,
        industries=industries or None,
        site=site or None,
        extra="\n\n".join(extra_bits) if extra_bits else None,
    )
    logger.info(
        "scrape_company {} {!r} desc_len={} industries={!r}",
        hh_id,
        company.name,
        len(company.description or ""),
        (company.industries or "")[:80],
    )
    return company


def get_or_fetch_company(
    driver: WebDriver,
    store: VacancyStore,
    *,
    dumper: PageDumper | None = None,
    pause_sec: float = 0.0,
) -> Company | None:
    """Из открытой вкладки вакансии: взять компанию из БД или спарсить страницу."""
    link = extract_employer_link(driver)
    if link is None:
        logger.warning("Не нашли ссылку на компанию на странице вакансии")
        return None

    cached = store.get_company(link.hh_id)
    if cached is not None:
        logger.info(
            "company {} {!r} из БД (desc_len={})",
            cached.hh_id,
            cached.name,
            len(cached.description or ""),
        )
        return cached

    try:
        with company_tab(
            driver,
            link.url,
            dumper=dumper,
            label=f"company_{link.hh_id}",
            pause_sec=pause_sec,
        ):
            scraped = scrape_company_page(
                driver,
                hh_id=link.hh_id,
                name_hint=link.name,
                url=link.url,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось спарсить компанию {}: {}", link.hh_id, exc)
        # минимальная запись, чтобы не долбить ту же страницу бесконечно без смысла
        scraped = Company(
            hh_id=link.hh_id,
            name=link.name,
            url=link.url,
            description=None,
        )

    return store.upsert_company(scraped)


def _first_text(driver: WebDriver, css_list: str) -> str:
    for css in _split_css(css_list):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css)
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                text = (el.text or "").strip()
                if text:
                    return text
            except Exception:
                continue
    return ""


def _first_attr(driver: WebDriver, css_list: str, attr: str) -> str:
    for css in _split_css(css_list):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css)
        except Exception:
            continue
        for el in els:
            try:
                val = (el.get_attribute(attr) or "").strip()
                if val and not val.startswith("javascript:"):
                    return val
            except Exception:
                continue
    return ""


def _fallback_main_text(driver: WebDriver) -> str:
    for css in (
        "main",
        "[data-qa='employer-page']",
        "#HH-React-Root",
    ):
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            text = (el.text or "").strip()
            if len(text) > 200:
                # обрежем шум: возьмём середину/начало разумного объёма
                return text[:6000]
        except Exception:
            continue
    return ""


def _split_css(css_list: str) -> list[str]:
    return [p.strip() for p in css_list.split(",") if p.strip()]
