"""CLI автооткликов.

По умолчанию — РЕАЛЬНЫЕ отклики (нужно для отладки UI).
--debug только включает подробные логи и HTML-дампы, клики не отключает.

    # один отклик + debug HTML/логи
    python run.py --debug --limit 15 --apply-limit 1 --pause 2.5

    # только матчинг, без кликов
    python run.py --dry-run --debug --limit 20
"""

from __future__ import annotations

import argparse
import sys

from browser import create_driver
from config import (
    load_env,
    pause_between_actions_sec,
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
        "--limit",
        type=int,
        default=40,
        help="Сколько NEW без score отдать в LLM за одну страницу (по умолчанию 40)",
    )
    parser.add_argument(
        "--apply-limit",
        type=int,
        default=1,
        help="Сколько откликов за прогон (по умолчанию 1)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=40,
        help="Максимум страниц поиска (пагинация), по умолчанию 40",
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

    pause_sec = (
        max(0.0, float(args.pause))
        if args.pause is not None
        else pause_between_actions_sec()
    )

    dry_run = bool(args.dry_run)
    logger.info("SEARCH_URL: {}", url)
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info("mode: {}", "dry-run (без кликов)" if dry_run else "LIVE (реальные отклики)")
    logger.info("debug: {}", args.debug)
    logger.info(
        "limit={} apply_limit={} max_pages={} pause={}s",
        args.limit,
        args.apply_limit,
        args.max_pages,
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
            limit=args.limit,
            max_applies=args.apply_limit,
            max_pages=args.max_pages,
            pause_sec=pause_sec,
            dumper=dumper,
        ).run()
    finally:
        logger.debug("Закрываю браузер")
        driver.quit()
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
