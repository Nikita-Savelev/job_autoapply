"""Сравнение поисковых URL hh.ru: suitable / new suitable (без откликов).

Скрейпит несколько страниц выдачи, матчит LLM по title (как в пайплайне),
в БД только читает статусы — скипы/отклики не пишет.

    python probe_search_urls.py
    python probe_search_urls.py --pages 5 --batch 40
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from browser import create_driver, quit_driver
from config import load_env, load_resume_text, pg_conninfo, target_role
from db.models import Vacancy, VacancyStatus
from db.store import VacancyStore
from hh.search import go_next_search_page, open_search, scrape_search_page
from logging_setup import setup_logging
from loguru import logger
from matcher.llm_batch import match_vacancies_batch

HH = "https://hh.ru/search/vacancy"


def _url_fields(search_fields: tuple[str, ...], **params: str | int) -> str:
    q = [(k, str(v)) for k, v in params.items()]
    for f in search_fields:
        q.append(("search_field", f))
    return f"{HH}?{urlencode(q)}"


# Набор кандидатов: имя → URL
def build_candidates() -> list[tuple[str, str]]:
    base = {
        "area": 1,  # Москва
        "enable_snippets": "false",
        "ored_clusters": "true",
        "order_by": "publication_time",
        "items_on_page": 50,
    }
    # Без региона: вся выдача hh.ru, больше вакансий чем только Москва.
    wide = {
        "enable_snippets": "false",
        "ored_clusters": "true",
        "order_by": "publication_time",
        "items_on_page": 50,
        "search_period": 0,
    }
    name = ("name",)
    name_desc = ("name", "description")
    all_fields = ("name", "company_name", "description")

    return [
        (
            "A_broad_python_all_fields",
            _url_fields(
                all_fields,
                text="Python",
                **base,
            ),
        ),
        (
            "B_python_razrab_name",
            _url_fields(
                name,
                text="Python разработчик",
                **base,
            ),
        ),
        (
            "C_python_backend_name",
            _url_fields(
                name,
                text="Python Backend",
                **base,
            ),
        ),
        (
            "D_python_name_only",
            _url_fields(
                name,
                text="Python",
                **base,
            ),
        ),
        (
            "E_backend_python_name",
            _url_fields(
                name,
                text="Backend Python",
                **base,
            ),
        ),
        (
            "F_python_dev_name_exp3_6",
            _url_fields(
                name,
                text="Python Developer",
                experience="between3And6",
                **base,
            ),
        ),
        (
            "G_python_razrab_remote",
            _url_fields(
                name,
                text="Python разработчик",
                schedule="remote",
                **base,
            ),
        ),
        (
            "H_python_name_prof96",
            # professional_role=96 — Программист, разработчик
            _url_fields(
                name,
                text="Python",
                professional_role="96",
                **base,
            ),
        ),
        (
            "I_senior_python_name",
            _url_fields(
                name,
                text="Senior Python",
                **base,
            ),
        ),
        (
            "J_python_fastapi_django_name",
            _url_fields(
                name,
                text="Python FastAPI OR Django OR Backend",
                **base,
            ),
        ),
        (
            "K_python_razrab_remote_ru",
            _url_fields(
                name,
                text="Python разработчик",
                schedule="remote",
                **wide,
            ),
        ),
        (
            "L_python_backend_ru",
            _url_fields(
                name,
                text="Python Backend",
                **wide,
            ),
        ),
        (
            "M_django_ru",
            _url_fields(
                name,
                text="Django",
                **wide,
            ),
        ),
        (
            "N_fastapi_ru",
            _url_fields(
                name,
                text="FastAPI",
                **wide,
            ),
        ),
        (
            "O_middle_python_ru",
            _url_fields(
                name,
                text="Middle Python",
                **wide,
            ),
        ),
        (
            "P_python_dev_remote_ru",
            _url_fields(
                name,
                text="Python Developer",
                schedule="remote",
                **wide,
            ),
        ),
    ]


@dataclass
class ProbeResult:
    name: str
    url: str
    pages: int = 0
    scraped: int = 0
    unique: int = 0
    suitable: int = 0
    new_suitable: int = 0  # suitable и ещё не applied/skipped/blocked
    already_done: int = 0  # suitable, но уже в финальном статусе
    sample_titles: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)


DONE_STATUSES = {
    VacancyStatus.APPLIED,
    VacancyStatus.SKIPPED,
    VacancyStatus.BLOCKED,
}


def _is_new_for_apply(store: VacancyStore, hh_id: str) -> bool:
    vac = store.get(hh_id)
    if vac is None:
        return True
    return vac.status not in DONE_STATUSES


def probe_one(
    driver,
    store: VacancyStore,
    resume_text: str,
    *,
    name: str,
    url: str,
    max_pages: int,
    batch_size: int,
    pause_sec: float,
) -> ProbeResult:
    res = ProbeResult(name=name, url=url)
    open_search(driver, url)
    time.sleep(pause_sec)

    by_id: dict[str, Vacancy] = {}
    for page_no in range(1, max_pages + 1):
        cards = scrape_search_page(driver)
        res.pages = page_no
        res.scraped += len(cards)
        for c in cards:
            by_id.setdefault(c.hh_id, c)
        logger.info(
            "[{}] стр.{} карточек={} unique_so_far={}",
            name,
            page_no,
            len(cards),
            len(by_id),
        )
        if page_no >= max_pages:
            break
        if not go_next_search_page(driver, pause_sec=pause_sec):
            logger.info("[{}] конец выдачи на стр.{}", name, page_no)
            break

    vacancies = list(by_id.values())
    res.unique = len(vacancies)
    if not vacancies:
        return res

    decisions: dict = {}
    for i in range(0, len(vacancies), batch_size):
        chunk = vacancies[i : i + batch_size]
        part = match_vacancies_batch(chunk, resume_text)
        decisions.update(part)

    suitable_vacs: list[Vacancy] = []
    for v in vacancies:
        d = decisions.get(v.hh_id)
        if d is not None and d.accepted:
            suitable_vacs.append(v)

    res.suitable = len(suitable_vacs)
    for v in suitable_vacs:
        if _is_new_for_apply(store, v.hh_id):
            res.new_suitable += 1
            res.new_ids.append(v.hh_id)
            if len(res.sample_titles) < 8:
                res.sample_titles.append(v.title)
        else:
            res.already_done += 1

    logger.info(
        "[{}] unique={} suitable={} new_suitable={} already_done={}",
        name,
        res.unique,
        res.suitable,
        res.new_suitable,
        res.already_done,
    )
    return res


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Probe HH search URLs")
    parser.add_argument("--pages", type=int, default=5, help="Страниц на ссылку")
    parser.add_argument("--batch", type=int, default=45, help="Размер LLM-батча")
    parser.add_argument("--pause", type=float, default=1.2)
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Подстрока имени кандидата (через запятую)",
    )
    args = parser.parse_args(argv)
    setup_logging(debug=False)

    candidates = build_candidates()
    if args.only:
        keys = {x.strip() for x in args.only.split(",") if x.strip()}
        candidates = [
            (n, u) for n, u in candidates if any(k in n for k in keys)
        ]

    resume = load_resume_text()
    store = VacancyStore(pg_conninfo())
    driver = create_driver(headless=False)
    results: list[ProbeResult] = []
    try:
        logger.info(
            "PROBE: {} ссылок, pages={}, role={!r}",
            len(candidates),
            args.pages,
            target_role(),
        )
        for name, url in candidates:
            logger.info("=== probe {} ===", name)
            logger.info("URL: {}", url)
            try:
                results.append(
                    probe_one(
                        driver,
                        store,
                        resume,
                        name=name,
                        url=url,
                        max_pages=args.pages,
                        batch_size=args.batch,
                        pause_sec=args.pause,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("probe {} failed: {}", name, exc)
                results.append(ProbeResult(name=name, url=url))
    finally:
        quit_driver(driver)
        store.close()

    # сводка
    print("\n" + "=" * 88)
    print(
        f"{'name':32} {'pages':>5} {'uniq':>5} {'suit':>5} "
        f"{'new':>5} {'done':>5} {'yield%':>7}"
    )
    print("-" * 88)
    ranked = sorted(
        results,
        key=lambda r: (r.new_suitable, r.suitable, r.unique),
        reverse=True,
    )
    for r in ranked:
        y = (100.0 * r.suitable / r.unique) if r.unique else 0.0
        print(
            f"{r.name:32} {r.pages:5d} {r.unique:5d} {r.suitable:5d} "
            f"{r.new_suitable:5d} {r.already_done:5d} {y:6.1f}%"
        )
    print("=" * 88)
    if ranked and ranked[0].new_suitable:
        best = ranked[0]
        print(f"\nBEST: {best.name}  new_suitable={best.new_suitable}")
        print(f"URL:\n{best.url}")
        if best.sample_titles:
            print("Примеры новых suitable:")
            for t in best.sample_titles:
                print(f"  · {t}")

    # пересечение: сколько unique new across probes
    all_new: set[str] = set()
    for r in results:
        all_new.update(r.new_ids)
    print(f"\nУникальных new_suitable по всем ссылкам: {len(all_new)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
