"""Ежедневный прогон: широкий поиск Python по всему hh.ru.

Цель — не сузить выдачу, а обойти все релевантные вакансии с «Python».
Без региона (удалёнка / вся РФ), 100 карточек на странице.

Первый полный прогон (вся история публикаций)::

    python run_daily.py --period 0 --apply-limit 200

Ежедневно (только свежие за неделю) — позже::

    python run_daily.py --period 7 --apply-limit 200

URL по умолчанию — DEFAULT_DAILY_SEARCH_URL / HH_SEARCH_URL;
``--period`` и ``items_on_page=100`` накладываются сверху.
"""

from __future__ import annotations

import argparse
import sys

from browser import create_driver, quit_driver
from config import (
    build_daily_search_url,
    load_env,
    resolve_action_pause,
    pg_conninfo,
    pg_dsn_display,
    resume_path,
    target_role,
)
from db.store import VacancyStore
from debug import PageDumper
from logging_setup import setup_logging
from loguru import logger
from pipeline import AutoApplyPipeline


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Ежедневные автоотклики: Python по всему hh.ru"
    )
    parser.add_argument(
        "--period",
        type=int,
        default=0,
        metavar="DAYS",
        help="search_period hh.ru: 0=всё время, 1/3/7=за N дней (по умолчанию 0)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Базовый search URL (иначе HH_SEARCH_URL / daily default)",
    )
    parser.add_argument(
        "--apply-limit",
        type=int,
        default=200,
        help="Сколько откликов за прогон (единственный лимит; по умолчанию 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только scrape + LLM-матчинг, без кликов",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="DEBUG-логи + HTML-дампы",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SEC",
        help="Пауза между действиями. Live по умолчанию 0; --debug берёт HH_PAUSE_SEC",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Chrome без окна",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Показать окно Chrome на экране",
    )
    args = parser.parse_args(argv)

    dumper = PageDumper() if args.debug else None
    log_file = (dumper.run_dir / "run.log") if dumper else None
    setup_logging(debug=args.debug, log_file=log_file)

    url = build_daily_search_url(base=args.url, period=args.period)
    pause_sec = resolve_action_pause(explicit=args.pause, debug=bool(args.debug))
    dry_run = bool(args.dry_run)

    logger.info("DAILY SEARCH_URL: {}", url)
    logger.info("search_period={}", args.period)
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info("mode: {}", "dry-run (без кликов)" if dry_run else "LIVE")
    logger.info(
        "apply_limit={} (pages/match=∞) pause={}s",
        args.apply_limit,
        pause_sec,
    )
    if not dry_run:
        logger.warning(
            "Реальные отклики (apply_limit={}). Для проверки — --dry-run.",
            args.apply_limit,
        )

    try:
        store = VacancyStore(pg_conninfo())
    except Exception as exc:  # noqa: BLE001
        logger.error("Postgres недоступен: {}\nПодними: docker compose up -d", exc)
        return 1

    driver = create_driver(headless=args.headless or not args.foreground)
    try:
        stats = AutoApplyPipeline(
            driver,
            store,
            search_url=url,
            dry_run=dry_run,
            debug=args.debug,
            limit=None,
            max_applies=args.apply_limit,
            max_pages=None,
            pause_sec=pause_sec,
            dumper=dumper,
        ).run()
    finally:
        quit_driver(driver)
        counts = store.counts()
        store.close()

    logger.info(
        "ИТОГО daily: pages={} scraped={} skipped={} applied={} "
        "blocked={} errors={}",
        stats.pages,
        stats.scraped,
        stats.skipped,
        stats.applied,
        stats.blocked,
        stats.errors,
    )
    logger.info("counts in DB: {}", counts)
    if stats.debug_dir:
        logger.info("HTML+log: {}", stats.debug_dir)
    for note in stats.notes[:40]:
        logger.info("· {}", note)
    return 0 if stats.errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
