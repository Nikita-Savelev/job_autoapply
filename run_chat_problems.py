#!/usr/bin/env python3
"""Повторный live-прогон только по проблемным чатам.

Критерии (БД, последнее сообщение входящее):
  - status = awaiting_us
  - или явный вопрос / просьба ответить, а статус awaiting_them / rejected / closed
  - или внешняя ссылка (форма/тест) при status != needs_human/interview

Примеры:
  .venv/bin/python run_chat_problems.py --debug --fast
  .venv/bin/python run_chat_problems.py --list          # только показать id
  .venv/bin/python run_chat_problems.py --chat-id 5648124757 --fast
  .venv/bin/python run_chat_problems.py --dry-run --fast
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

from chat.classify import looks_like_needs_reply, looks_like_reject
from chat.pipeline import SweepConfig, run_chat_cycle, set_run_context
from chat.store import ChatStore
from config import load_env, load_resume_text, pause_between_actions_sec, pg_conninfo
from logging_setup import setup_logging

_URL_RE = re.compile(r"https?://\S+", re.I)


def _looks_like_external(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    has_url = bool(_URL_RE.search(t))
    markers = (
        "forms.gle",
        "forms.yandex",
        "docs.google",
        "тестов",
        "тестовое",
        "заполн",
        "ответьте",
        "по ссылке",
        "указанной ссылке",
        "hackerrank",
        "codility",
        "анкет",
    )
    return has_url and any(x in t for x in markers)


def find_problem_chat_ids(conn) -> list[tuple[str, str]]:
    """[(chat_id, reason), ...]"""
    import psycopg.rows

    conn.row_factory = psycopg.rows.dict_row
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH last_msg AS (
              SELECT DISTINCT ON (chat_id)
                chat_id, direction, text
              FROM chat_messages
              ORDER BY chat_id, id DESC
            )
            SELECT
              t.chat_id, t.status, t.bot_paused, t.paused_reason,
              m.direction AS last_dir, m.text AS last_text
            FROM chat_threads t
            LEFT JOIN last_msg m ON m.chat_id = t.chat_id
            ORDER BY t.updated_at DESC NULLS LAST
            """
        )
        rows = list(cur.fetchall())

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(cid: str, reason: str) -> None:
        if cid in seen:
            return
        seen.add(cid)
        out.append((cid, reason))

    for r in rows:
        cid = r["chat_id"]
        st = r["status"] or ""
        text = r["last_text"] or ""
        last_dir = r["last_dir"]

        if st == "awaiting_us":
            add(cid, "status=awaiting_us")
            continue

        if last_dir != "in":
            continue

        if looks_like_reject(text):
            continue

        if st in ("interview",):
            continue

        if _looks_like_external(text) and st not in ("needs_human",):
            add(cid, f"external_form status={st}")
            continue

        if looks_like_needs_reply(text) and st in (
            "awaiting_them",
            "rejected",
            "closed",
            "error",
        ):
            add(cid, f"unanswered_q status={st}")
            continue

    return out


def _prepare_thread(store: ChatStore, chat_id: str) -> None:
    """Снять skip по preview / разблокировать для повторной обработки."""
    import psycopg

    conn = store._conn  # noqa: SLF001
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE chat_threads SET
              last_preview = NULL,
              bot_paused = CASE
                WHEN status IN ('needs_human', 'interview') THEN bot_paused
                ELSE FALSE
              END,
              paused_reason = CASE
                WHEN status IN ('needs_human', 'interview') THEN paused_reason
                ELSE NULL
              END,
              status = CASE
                WHEN status IN ('rejected', 'closed', 'error', 'awaiting_them')
                  THEN 'awaiting_us'
                ELSE status
              END,
              updated_at = NOW()
            WHERE chat_id = %s
            RETURNING chat_id, status, bot_paused
            """,
            (chat_id,),
        )
        row = cur.fetchone()
    conn.commit()
    logger.info("prepare {}: {}", chat_id, row)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live-ретрай проблемных чатов HH"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Только вывести список id, без браузера",
    )
    parser.add_argument(
        "--chat-id",
        action="append",
        default=None,
        help="Явный chat_id (можно несколько раз); иначе авто из БД",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--manual-send", action="store_true")
    parser.add_argument("--step", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--sleep",
        type=float,
        default=None,
        help="Пауза между чатами (сек), по умолчанию HH_PAUSE_SEC",
    )
    args = parser.parse_args()

    load_env()
    setup_logging(debug=args.debug)

    store = ChatStore(pg_conninfo())
    try:
        if args.chat_id:
            problems = [(cid, "cli") for cid in args.chat_id]
        else:
            problems = find_problem_chat_ids(store._conn)  # noqa: SLF001

        if not problems:
            logger.info("проблемных чатов нет")
            return 0

        logger.info("проблемных: {}", len(problems))
        for cid, reason in problems:
            logger.info("  {} — {}", cid, reason)

        if args.list:
            return 0

        pause = pause_between_actions_sec()
        between = args.sleep if args.sleep is not None else pause
        cfg = SweepConfig(
            pause_between_chats_sec=pause,
            list_scroll_pause_sec=pause,
            dry_run=bool(args.dry_run),
            manual_send=bool(args.manual_send),
            use_typing_delay=not bool(args.fast),
            persist=not bool(args.dry_run),
            step_enter=bool(args.step),
            force_full_sweep=True,
        )

        resume = load_resume_text()
        dumper = None
        if args.debug:
            from debug.page_dump import PageDumper

            dumper = PageDumper()
            logger.info("HTML dumps → {}", dumper.run_dir)
        set_run_context(store=store, resume_text=resume, dumper=dumper)

        from browser import create_driver, quit_driver

        driver = create_driver()
        errors = 0
        try:
            for i, (cid, reason) in enumerate(problems, 1):
                logger.info(
                    "[{}/{}] chat {} ({})",
                    i,
                    len(problems),
                    cid,
                    reason,
                )
                if not args.dry_run:
                    _prepare_thread(store, cid)
                result = run_chat_cycle(
                    driver,
                    dry_run=cfg.dry_run,
                    chat_id=cid,
                    cfg=cfg,
                )
                logger.info(
                    "chat {}: drafted={} sent={} verified={} escalated={} "
                    "skipped={} errors={}",
                    cid,
                    result.drafted,
                    result.sent,
                    result.verified,
                    result.escalated,
                    result.skipped,
                    len(result.errors),
                )
                for err in result.errors:
                    logger.error("{}", err)
                    errors += 1
                if i < len(problems) and between > 0:
                    time.sleep(between)
        finally:
            set_run_context(store=None, resume_text="", dumper=None)
            quit_driver(driver)

        return 1 if errors else 0
    finally:
        try:
            store.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
