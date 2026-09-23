"""CLI автооткликов.

По умолчанию — РЕАЛЬНЫЕ отклики.
Единственный лимит — --apply-limit (сколько откликов).
Страницы поиска и матчинг — без ограничений (до конца выдачи).

    python run.py --apply-limit 50
    python run.py --dry-run --apply-limit 100
    python run.py --debug --apply-limit 1
"""

from __future__ import annotations

import argparse
import sys

from browser import create_driver, quit_driver
from config import (
    load_env,
    resolve_action_pause,
    pg_conninfo,
    pg_dsn_display,
    resume_path,
    search_url,
    target_role,
)
from db.store import VacancyStore
from debug import PageDumper
from logging_setup import setup_logging
from loguru import logger
from pipeline import AutoApplyPipeline


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="hh.ru автоотклики (Selenium)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только scrape + LLM-матчинг, БЕЗ кликов отклика на сайте",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="DEBUG-логи + HTML каждой страницы (отклики при этом идут, если нет --dry-run)",
    )
    parser.add_argument(
        "--apply-limit",
        type=int,
        default=1,
        help="Сколько откликов за прогон (единственный лимит; по умолчанию 1)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SEC",
        help="Пауза между действиями в секундах (по умолчанию HH_PAUSE_SEC или 2.5)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Chrome без окна",
    )
    args = parser.parse_args(argv)

    dumper = PageDumper() if args.debug else None
    log_file = (dumper.run_dir / "run.log") if dumper else None
    setup_logging(debug=args.debug, log_file=log_file)

    url = search_url()
    if not url:
        logger.error(
            "Не задан HH_SEARCH_URL в hh_autoapply/.env — "
            "вставь ссылку поиска с фильтрами."
        )
        return 1

    pause_sec = resolve_action_pause(explicit=args.pause, debug=bool(args.debug))

    dry_run = bool(args.dry_run)
    logger.info("SEARCH_URL: {}", url)
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info("mode: {}", "dry-run (без кликов)" if dry_run else "LIVE (реальные отклики)")
    logger.info("debug: {}", args.debug)
    logger.info(
        "apply_limit={} (pages/match=∞) pause={}s",
        args.apply_limit,
        pause_sec,
    )
    if not dry_run:
        logger.warning(
            "Будут реальные отклики на hh.ru (apply_limit={}). "
            "Для матчинга без кликов добавь --dry-run.",
            args.apply_limit,
        )

    try:
        store = VacancyStore(pg_conninfo())
        logger.debug("Postgres: подключение ok")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Postgres недоступен: {}\n"
            "Подними: docker compose up -d",
            exc,
        )
        return 1

    driver = create_driver(headless=args.headless)
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
        logger.debug("Закрываю браузер")
        quit_driver(driver)
        counts = store.counts()
        store.close()

    logger.info(
        "Результат pages={} scraped={} upserted={} skipped={} "
        "applied={} blocked={} errors={}",
        stats.pages,
        stats.scraped,
        stats.upserted,
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
