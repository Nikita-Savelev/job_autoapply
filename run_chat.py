#!/usr/bin/env python3
"""CLI чата HH.

Стандартный запуск (без флагов режима): догон ленты сверху, пока не встретятся
N чатов подряд, которые уже есть в БД, затем мониторинг первых страниц в цикле.

Примеры:
  python run_chat.py --debug                     # догон до 5 в БД, затем мониторинг
  python run_chat.py --loop --debug              # только мониторинг в цикле
  python run_chat.py --once --debug              # один проход monitor
  python run_chat.py --once --force --debug      # полный список (редко)
  python run_chat.py --once --chat-id 5649719078 --fast --debug
  python run_chat.py --unread --fast --debug     # непрочитанные, пауза 2 мин, выход
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

from chat.pipeline import (
    SweepConfig,
    run_chat_cycle,
    run_unread_drain,
    set_run_context,
)
from chat.store import ChatStore
from config import env, load_env, load_resume_text, pause_between_actions_sec, pg_conninfo
from logging_setup import setup_logging


def _cfg_from_args(args: argparse.Namespace) -> SweepConfig:
    pause = pause_between_actions_sec()
    default_sleep = "60" if not args.force else "120"
    return SweepConfig(
        stop_awaiting_them_streak=int(env("HH_CHAT_STOP_STREAK", "0") or "0"),
        sleep_after_sweep_sec=float(
            args.interval
            if args.interval is not None
            else (env("HH_CHAT_SWEEP_SLEEP_SEC", default_sleep) or default_sleep)
        ),
        sweep_interval_sec=float(env("HH_CHAT_SWEEP_INTERVAL_SEC", "600") or "600"),
        pause_between_chats_sec=pause,
        list_scroll_pause_sec=pause,
        dry_run=bool(args.dry_run),
        manual_send=bool(args.manual_send),
        use_typing_delay=not bool(args.fast),
        persist=not bool(args.dry_run),
        step_enter=bool(args.step),
        force_full_sweep=bool(args.force),
        monitor_pages=max(1, int(args.pages)),
        known_in_db_streak=0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Мониторинг/автоответы в чатах hh.ru"
    )
    parser.add_argument(
        "--unread",
        action="store_true",
        help="Непрочитанные: ответить, подождать и выйти, если новых нет",
    )
    parser.add_argument(
        "--unread-wait",
        type=float,
        default=120,
        metavar="SEC",
        help="Сколько ждать новые непрочитанные перед выходом (по умолчанию 120)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Один проход (monitor или --force sweep / --chat-id)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Только мониторинг в цикле, без догоняющего прогона",
    )
    parser.add_argument(
        "--known-streak",
        type=int,
        default=5,
        metavar="N",
        help="Стоп догона после N чатов подряд, уже лежащих в БД (по умолчанию 5)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Сколько страниц списка смотреть в monitor (по умолчанию 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не вставлять текст и не писать статусы в БД",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Без длинной паузы «набора» (HH_PAUSE_SEC остаётся)",
    )
    parser.add_argument(
        "--manual-send",
        action="store_true",
        help="Не жать «Отправить»: вставил текст → ты отправил → Enter",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Полный прогон всего списка (не monitor); неизменённое превью skip",
    )
    parser.add_argument(
        "--step",
        action="store_true",
        help="Ждать Enter после каждого чата (админка/логи)",
    )
    parser.add_argument("--chat-id", default=None, help="Только один тред")
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Сек sleep между проходами в --loop",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    standard = (
        not args.once
        and not args.loop
        and not args.chat_id
        and not args.force
        and not args.unread
    )

    load_env()
    setup_logging(debug=args.debug)
    cfg = _cfg_from_args(args)
    if standard:
        cfg = replace(cfg, known_in_db_streak=max(1, int(args.known_streak)))

    if args.unread:
        mode = "unread"
    elif cfg.force_full_sweep:
        mode = "force-sweep"
    elif cfg.known_in_db_streak > 0:
        mode = f"catch-up/{cfg.known_in_db_streak}"
    else:
        mode = f"monitor/{cfg.monitor_pages}p"
    logger.info(
        "chat {}: dry_run={} fast={} manual_send={} step_enter={} "
        "pause={:.1f}s sleep={:.0f}s",
        mode,
        cfg.dry_run,
        not cfg.use_typing_delay,
        cfg.manual_send,
        cfg.step_enter,
        cfg.pause_between_chats_sec,
        cfg.sleep_after_sweep_sec,
    )

    store = ChatStore(pg_conninfo())
    resume = load_resume_text()
    dumper = None
    if args.debug:
        from debug.page_dump import PageDumper

        dumper = PageDumper()
        logger.info("HTML dumps → {}", dumper.run_dir)
    set_run_context(store=store, resume_text=resume, dumper=dumper)

    from browser import create_driver, quit_driver

    driver = create_driver()
    code = 0

    def log_result(result) -> int:
        logger.info(
            "pass: seen={} created={} awaiting_us={} drafted={} sent={} "
            "verified={} escalated={} skipped={} paused={} stop={}",
            result.seen,
            result.created,
            result.awaiting_us,
            result.drafted,
            result.sent,
            result.verified,
            result.escalated,
            result.skipped,
            result.paused,
            result.stop_reason,
        )
        for err in result.errors:
            logger.error("{}", err)
        return 1 if result.errors else 0

    def one_pass(pass_cfg: SweepConfig) -> int:
        result = run_chat_cycle(
            driver,
            dry_run=pass_cfg.dry_run,
            chat_id=args.chat_id,
            cfg=pass_cfg,
        )
        return log_result(result)

    try:
        if args.unread:
            logger.info(
                "непрочитанные: ответ, затем пауза {:.0f}s; если новых нет — выход",
                args.unread_wait,
            )
            code = log_result(
                run_unread_drain(driver, cfg=cfg, wait_sec=float(args.unread_wait))
            )
        elif standard:
            logger.info(
                "догон ленты: стоп после {} чатов подряд уже в БД, затем мониторинг",
                cfg.known_in_db_streak,
            )
            code = one_pass(cfg)
            cfg = replace(cfg, known_in_db_streak=0)
            logger.info("догон закончен, дальше мониторинг / {} стр.", cfg.monitor_pages)
        if standard or args.loop:
            while True:
                code = one_pass(cfg)
                logger.info(
                    "sleep {:.0f}s до следующего прохода",
                    cfg.sleep_after_sweep_sec,
                )
                time.sleep(cfg.sleep_after_sweep_sec)
        elif not args.unread:
            code = one_pass(cfg)
    finally:
        set_run_context(store=None, resume_text="", dumper=None)
        try:
            store.close()
        except Exception:
            pass
        quit_driver(driver)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
