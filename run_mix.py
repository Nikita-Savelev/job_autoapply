#!/usr/bin/env python3
"""Отклики и чат в одном Chrome, по кругу.

После каждой поисковой ссылки — догон чатов до N уже известных подряд.
Когда очередь ссылок кончилась, несколько кругов мониторинга и снова отклики.
Бесконечно.

Один процесс, один профиль. run_daily / run_chat / run_queue рядом не запускать.

    HH_PAUSE_SEC=1 .venv/bin/python run_mix.py --fast --debug
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger
from selenium.common.exceptions import WebDriverException

from browser import create_driver, quit_driver
from chat.pipeline import SweepConfig, run_chat_cycle, set_run_context
from chat.store import ChatStore
from config import (
    env,
    load_env,
    load_resume_text,
    pause_between_actions_sec,
    pg_conninfo,
    pg_dsn_display,
    resolve_action_pause,
    resume_path,
    target_role,
)
from db.store import VacancyStore
from debug import PageDumper
from logging_setup import setup_logging
from pipeline import AutoApplyPipeline
from run_queue import _ordered_queue


def _driver_alive(driver) -> bool:
    try:
        _ = driver.current_url
        return True
    except Exception:  # noqa: BLE001
        return False


def _remaining_applies(
    store: VacancyStore,
    *,
    dry_run: bool,
    apply_limit: int | None,
    already: int,
) -> int:
    remaining = store.remaining_daily_applies()
    if apply_limit is not None:
        remaining = min(remaining, max(0, apply_limit - already))
    if dry_run and remaining <= 0:
        return apply_limit if apply_limit is not None else 50
    return max(0, remaining)


def _apply_one(
    driver,
    store: VacancyStore,
    *,
    name: str,
    url: str,
    index: int,
    total_links: int,
    dry_run: bool,
    debug: bool,
    pause_sec: float,
    dumper: PageDumper | None,
    remaining: int,
) -> int:
    """Один поиск. Возвращает число откликов."""
    logger.info(
        "=== ОТКЛИКИ [{}/{}] {} | remaining={} ===",
        index,
        total_links,
        name,
        remaining,
    )
    stats = AutoApplyPipeline(
        driver,
        store,
        search_url=url,
        dry_run=dry_run,
        debug=debug,
        limit=None,
        max_applies=remaining,
        max_pages=None,
        pause_sec=pause_sec,
        dumper=dumper,
    ).run()
    return stats.applied


def _chat_catch(driver, *, dry_run: bool, cfg: SweepConfig) -> None:
    """Догон переписки: стоп после N чатов подряд, которые уже есть в БД."""
    logger.info(
        "ЧАТ догон: стоп после {} чатов подряд уже в БД",
        cfg.known_in_db_streak,
    )
    _log_chat("догон", run_chat_cycle(driver, dry_run=dry_run, cfg=cfg))


def _chat_cfg(*, known_streak: int, pages: int, fast: bool, dry_run: bool) -> SweepConfig:
    pause = pause_between_actions_sec()
    sleep_raw = env("HH_CHAT_SWEEP_SLEEP_SEC", "60") or "60"
    return SweepConfig(
        known_in_db_streak=known_streak,
        monitor_pages=max(1, pages),
        use_typing_delay=not fast,
        dry_run=dry_run,
        persist=not dry_run,
        manual_send=False,
        force_full_sweep=False,
        pause_between_chats_sec=pause,
        list_scroll_pause_sec=pause,
        sleep_after_sweep_sec=float(sleep_raw),
    )


def _log_chat(label: str, result) -> None:
    logger.info(
        "{}: seen={} created={} awaiting_us={} sent={} verified={} "
        "escalated={} skipped={} stop={}",
        label,
        result.seen,
        result.created,
        result.awaiting_us,
        result.sent,
        result.verified,
        result.escalated,
        result.skipped,
        result.stop_reason,
    )
    for err in result.errors:
        logger.error("{}", err)


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Чередование откликов и чата в одном Chrome"
    )
    parser.add_argument("--dry-run", action="store_true", help="Без кликов и без записи")
    parser.add_argument("--debug", action="store_true", help="DEBUG и HTML-дампы")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Чат без паузы «набора» (пауза между действиями — HH_PAUSE_SEC)",
    )
    parser.add_argument("--headless", action="store_true", help="Chrome без окна")
    parser.add_argument(
        "--apply-limit",
        type=int,
        default=None,
        help="Потолок откликов за один проход (ещё режется дневным лимитом)",
    )
    parser.add_argument(
        "--known-streak",
        type=int,
        default=5,
        metavar="N",
        help="Стоп догона чатов после N подряд уже в БД",
    )
    parser.add_argument(
        "--monitor-cycles",
        type=int,
        default=3,
        metavar="N",
        help="Сколько кругов мониторинга чатов после всей очереди ссылок",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Страниц списка чатов в мониторинге",
    )
    args = parser.parse_args(argv)

    dumper = PageDumper() if args.debug else None
    log_file = (dumper.run_dir / "run.log") if dumper else None
    setup_logging(debug=args.debug, log_file=log_file)

    queue = _ordered_queue()
    pause_sec = resolve_action_pause(explicit=None, debug=bool(args.debug))
    chat_pause = pause_between_actions_sec()
    dry_run = bool(args.dry_run)
    monitor_cycles = max(1, int(args.monitor_cycles))
    known_streak = max(1, int(args.known_streak))

    logger.info("MIX: {} поисковых ссылок, один Chrome", len(queue))
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info(
        "режим: {} | отклики pause={}s | чат после каждой ссылки, "
        "pause={:.1f}s fast={} | догон {} подряд | мониторинг {}×{} стр.",
        "dry-run" if dry_run else "LIVE",
        pause_sec,
        chat_pause,
        bool(args.fast),
        known_streak,
        monitor_cycles,
        max(1, int(args.pages)),
    )
    for i, (name, url) in enumerate(queue, 1):
        logger.info("  [{}] {}", i, name)

    try:
        vacancies = VacancyStore(pg_conninfo())
    except Exception as exc:  # noqa: BLE001
        logger.error("Postgres недоступен: {}", exc)
        return 1
    chats = ChatStore(pg_conninfo())
    set_run_context(
        store=chats,
        resume_text=load_resume_text(),
        dumper=dumper,
    )

    driver = None
    round_no = 0
    try:
        while True:
            round_no += 1
            logger.info("======== MIX круг {} ========", round_no)
            if driver is None or not _driver_alive(driver):
                if driver is not None:
                    quit_driver(driver)
                driver = create_driver(headless=bool(args.headless))

            catch = _chat_cfg(
                known_streak=known_streak,
                pages=int(args.pages),
                fast=bool(args.fast),
                dry_run=dry_run,
            )
            mon = replace(catch, known_in_db_streak=0)
            applied = 0
            for i, (name, url) in enumerate(queue, 1):
                if driver is None or not _driver_alive(driver):
                    if driver is not None:
                        quit_driver(driver)
                    driver = create_driver(headless=bool(args.headless))

                remaining = _remaining_applies(
                    vacancies,
                    dry_run=dry_run,
                    apply_limit=args.apply_limit,
                    already=applied,
                )
                if remaining <= 0 and not dry_run:
                    logger.info("Лимит откликов на этот проход исчерпан")
                    break

                try:
                    got = _apply_one(
                        driver,
                        vacancies,
                        name=name,
                        url=url,
                        index=i,
                        total_links=len(queue),
                        dry_run=dry_run,
                        debug=bool(args.debug),
                        pause_sec=pause_sec,
                        dumper=dumper,
                        remaining=remaining,
                    )
                except WebDriverException as exc:
                    logger.error("Chrome отвалился на откликах: {}", exc)
                    quit_driver(driver)
                    driver = None
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Поиск {} упал: {}", name, exc)
                    got = 0
                else:
                    applied += got
                    logger.info(
                        "ОТКЛИКИ [{}] done: applied=+{} (проход {})",
                        name,
                        got,
                        applied,
                    )

                if driver is None or not _driver_alive(driver):
                    break
                try:
                    _chat_catch(driver, dry_run=dry_run, cfg=catch)
                except WebDriverException as exc:
                    logger.error("Chrome отвалился на чатах: {}", exc)
                    quit_driver(driver)
                    driver = None
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Чат после {} упал: {}", name, exc)

            logger.info("Проход откликов закончен: applied={}", applied)
            if driver is None or not _driver_alive(driver):
                continue

            try:
                for n in range(1, monitor_cycles + 1):
                    logger.info("ЧАТ мониторинг {}/{}", n, monitor_cycles)
                    _log_chat(
                        f"мониторинг {n}/{monitor_cycles}",
                        run_chat_cycle(driver, dry_run=dry_run, cfg=mon),
                    )
                    if n < monitor_cycles:
                        logger.info(
                            "sleep {:.0f}s до следующего мониторинга",
                            mon.sleep_after_sweep_sec,
                        )
                        time.sleep(mon.sleep_after_sweep_sec)
            except WebDriverException as exc:
                logger.error("Chrome отвалился на чатах: {}", exc)
                quit_driver(driver)
                driver = None
            except Exception as exc:  # noqa: BLE001
                logger.exception("Проход чатов упал: {}", exc)
    except KeyboardInterrupt:
        logger.info("MIX остановлен")
        return 0
    finally:
        set_run_context(store=None, resume_text="", dumper=None)
        if driver is not None:
            quit_driver(driver)
        for store in (vacancies, chats):
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
