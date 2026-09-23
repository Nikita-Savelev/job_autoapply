"""Очередь поисковых URL → live-отклики с общим лимитом откликов.

Проходит все ссылки из probe_search_urls до конца каждой выдачи.
Единственный лимит — --apply-limit (суммарно по очереди).

    python run_queue.py --apply-limit 200
    python run_queue.py --apply-limit 200 --dry-run
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
    target_role,
)
from db.store import VacancyStore
from debug import PageDumper
from logging_setup import setup_logging
from loguru import logger
from pipeline import AutoApplyPipeline
from probe_search_urls import build_candidates

# Порядок: сначала самые урожайные по probe, broad — в конце
QUEUE_ORDER = (
    "G_python_razrab_remote",
    "F_python_dev_name_exp3_6",
    "B_python_razrab_name",
    "C_python_backend_name",
    "E_backend_python_name",
    "I_senior_python_name",
    "H_python_name_prof96",
    "D_python_name_only",
    "J_python_fastapi_django_name",
    "A_broad_python_all_fields",
)


def _ordered_queue() -> list[tuple[str, str]]:
    by_name = dict(build_candidates())
    out: list[tuple[str, str]] = []
    for name in QUEUE_ORDER:
        if name in by_name:
            out.append((name, by_name[name]))
    for name, url in by_name.items():
        if name not in QUEUE_ORDER:
            out.append((name, url))
    return out


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Очередь search URL → отклики (общий apply-limit)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только матчинг, без кликов",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="DEBUG + HTML-дампы",
    )
    parser.add_argument(
        "--apply-limit",
        type=int,
        default=200,
        help="Сколько откликов суммарно по всей очереди (единственный лимит)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SEC",
        help="Пауза между действиями",
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

    pause_sec = resolve_action_pause(explicit=args.pause, debug=bool(args.debug))
    queue = _ordered_queue()
    dry_run = bool(args.dry_run)
    remaining = max(0, int(args.apply_limit))

    logger.info("QUEUE: {} ссылок, apply_limit={}", len(queue), remaining)
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info(
        "mode: {}",
        "dry-run (без кликов)" if dry_run else "LIVE (реальные отклики)",
    )
    logger.info("pages/match=∞ pause={}s", pause_sec)
    for i, (name, url) in enumerate(queue, 1):
        logger.info("  [{}] {} → {}", i, name, url[:100])

    try:
        store = VacancyStore(pg_conninfo())
    except Exception as exc:  # noqa: BLE001
        logger.error("Postgres недоступен: {}", exc)
        return 1

    if not dry_run:
        rem_day = store.remaining_daily_applies()
        logger.info(
            "Дневной лимит: осталось {} (HH_DAILY_APPLY_LIMIT)",
            rem_day,
        )
        remaining = min(remaining, rem_day)
        if remaining <= 0:
            logger.warning("Дневной лимит откликов исчерпан — очередь не стартуем")
            store.close()
            return 0
        logger.warning(
            "LIVE: до {} реальных откликов по очереди из {} ссылок "
            "(с учётом дневного лимита).",
            remaining,
            len(queue),
        )

    driver = create_driver(headless=args.headless)
    total_applied = 0
    total_skipped = 0
    total_blocked = 0
    total_errors = 0
    total_scraped = 0
    try:
        for i, (name, url) in enumerate(queue, 1):
            if remaining <= 0:
                logger.info("Лимит откликов исчерпан — очередь стоп")
                break
            # пересчитать дневной остаток между ссылками
            if not dry_run:
                remaining = min(remaining, store.remaining_daily_applies())
                if remaining <= 0:
                    logger.warning("Дневной лимит исчерпан mid-queue — стоп")
                    break
            logger.info(
                "=== QUEUE [{}/{}] {} | remaining_applies={} ===",
                i,
                len(queue),
                name,
                remaining,
            )
            logger.info("URL: {}", url)
            stats = AutoApplyPipeline(
                driver,
                store,
                search_url=url,
                dry_run=dry_run,
                debug=args.debug,
                limit=None,
                max_applies=remaining,
                max_pages=None,
                pause_sec=pause_sec,
                dumper=dumper,
            ).run()
            total_applied += stats.applied
            total_skipped += stats.skipped
            total_blocked += stats.blocked
            total_errors += stats.errors
            total_scraped += stats.scraped
            remaining = max(0, remaining - stats.applied)
            logger.info(
                "QUEUE [{}] done: applied=+{} (всего {}) remaining={}",
                name,
                stats.applied,
                total_applied,
                remaining,
            )
    finally:
        quit_driver(driver)
        counts = store.counts()
        store.close()

    logger.info(
        "ИТОГО queue: scraped={} skipped={} applied={} blocked={} errors={} "
        "limit_left={}",
        total_scraped,
        total_skipped,
        total_applied,
        total_blocked,
        total_errors,
        remaining,
    )
    logger.info("counts in DB: {}", counts)
    return 0 if total_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
