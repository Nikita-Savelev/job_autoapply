"""Postgres: чаты и сообщения."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from chat.models import (
    ChatMessage,
    ChatSource,
    ChatThread,
    ChatThreadStatus,
    MessageDirection,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def inbound_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:32]


class ChatStore:
    """Отдельный store рядом с VacancyStore; те же PG credentials."""

    def __init__(self, conninfo: dict[str, Any] | None = None, **kwargs: Any) -> None:
        params = dict(conninfo or {})
        params.update(kwargs)
        self._conn = psycopg.connect(**params, row_factory=dict_row)
        self._conn.autocommit = False
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_threads (
                    chat_id            TEXT PRIMARY KEY,
                    title              TEXT NOT NULL DEFAULT '',
                    subtitle           TEXT,
                    url                TEXT NOT NULL DEFAULT '',
                    source             TEXT NOT NULL DEFAULT 'unknown',
                    status             TEXT NOT NULL DEFAULT 'new',
                    bot_paused         BOOLEAN NOT NULL DEFAULT FALSE,
                    paused_reason      TEXT,
                    vacancy_hh_id      TEXT,
                    company_hh_id      TEXT,
                    company_name       TEXT,
                    last_inbound_at    TIMESTAMPTZ,
                    last_outbound_at   TIMESTAMPTZ,
                    last_inbound_hash  TEXT,
                    last_preview       TEXT,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id             BIGSERIAL PRIMARY KEY,
                    chat_id        TEXT NOT NULL REFERENCES chat_threads(chat_id)
                                   ON DELETE CASCADE,
                    direction      TEXT NOT NULL,
                    text           TEXT NOT NULL,
                    sent_at        TIMESTAMPTZ,
                    author_label   TEXT,
                    external_id    TEXT,
                    our_action     TEXT,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (chat_id, external_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_actions (
                    id             BIGSERIAL PRIMARY KEY,
                    chat_id        TEXT NOT NULL REFERENCES chat_threads(chat_id)
                                   ON DELETE CASCADE,
                    kind           TEXT NOT NULL,
                    draft          TEXT,
                    delay_sec      DOUBLE PRECISION,
                    escalate_reason TEXT,
                    status         TEXT NOT NULL DEFAULT 'planned',
                    error_message  TEXT,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_threads_status "
                "ON chat_threads(status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_chat "
                "ON chat_messages(chat_id)"
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ChatStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def upsert_thread(self, thread: ChatThread) -> None:
        now = _utc_now()
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_threads (
                    chat_id, title, subtitle, url, source, status,
                    bot_paused, paused_reason, vacancy_hh_id, company_hh_id,
                    company_name, last_inbound_hash, last_preview,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    subtitle = COALESCE(EXCLUDED.subtitle, chat_threads.subtitle),
                    url = COALESCE(NULLIF(EXCLUDED.url, ''), chat_threads.url),
                    source = CASE
                        WHEN EXCLUDED.source = 'unknown' THEN chat_threads.source
                        ELSE EXCLUDED.source END,
                    status = EXCLUDED.status,
                    bot_paused = EXCLUDED.bot_paused,
                    paused_reason = EXCLUDED.paused_reason,
                    vacancy_hh_id = COALESCE(
                        EXCLUDED.vacancy_hh_id, chat_threads.vacancy_hh_id
                    ),
                    company_hh_id = COALESCE(
                        EXCLUDED.company_hh_id, chat_threads.company_hh_id
                    ),
                    company_name = COALESCE(
                        EXCLUDED.company_name, chat_threads.company_name
                    ),
                    last_inbound_hash = COALESCE(
                        EXCLUDED.last_inbound_hash, chat_threads.last_inbound_hash
                    ),
                    last_preview = COALESCE(
                        EXCLUDED.last_preview, chat_threads.last_preview
                    ),
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    thread.chat_id,
                    thread.title or "",
                    thread.subtitle,
                    thread.url or f"https://hh.ru/chat/{thread.chat_id}",
                    thread.source.value,
                    thread.status.value,
                    thread.bot_paused,
                    thread.paused_reason,
                    thread.vacancy_hh_id,
                    thread.company_hh_id,
                    thread.company,
                    thread.last_inbound_hash,
                    thread.last_message_preview,
                    now,
                    now,
                ),
            )
        self._conn.commit()

    def set_bot_paused(
        self,
        chat_id: str,
        paused: bool,
        *,
        reason: str | None = None,
        status: ChatThreadStatus | None = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat_threads SET
                    bot_paused = %s,
                    paused_reason = %s,
                    status = COALESCE(%s, status),
                    updated_at = NOW()
                WHERE chat_id = %s
                """,
                (
                    paused,
                    reason,
                    status.value if status else None,
                    chat_id,
                ),
            )
        self._conn.commit()

    def append_messages(self, chat_id: str, messages: list[ChatMessage]) -> int:
        """Вставить сообщения; external_id уникален в рамках чата если задан."""
        if not messages:
            return 0
        added = 0
        with self._conn.cursor() as cur:
            for msg in messages:
                ext = msg.external_id
                if ext:
                    cur.execute(
                        """
                        INSERT INTO chat_messages (
                            chat_id, direction, text, sent_at,
                            author_label, external_id, our_action
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chat_id, external_id) DO UPDATE SET
                            text = EXCLUDED.text,
                            sent_at = COALESCE(EXCLUDED.sent_at, chat_messages.sent_at),
                            author_label = COALESCE(
                                EXCLUDED.author_label, chat_messages.author_label
                            ),
                            direction = EXCLUDED.direction
                        """,
                        (
                            chat_id,
                            msg.direction.value,
                            msg.text,
                            msg.sent_at,
                            msg.author_label,
                            ext,
                            msg.our_action,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO chat_messages (
                            chat_id, direction, text, sent_at,
                            author_label, external_id, our_action
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            chat_id,
                            msg.direction.value,
                            msg.text,
                            msg.sent_at,
                            msg.author_label,
                            None,
                            msg.our_action,
                        ),
                    )
                added += cur.rowcount
            # обновить last_* на треде
            last_any = messages[-1] if messages else None
            last_in = next(
                (m for m in reversed(messages) if m.direction == MessageDirection.IN),
                None,
            )
            last_out = next(
                (m for m in reversed(messages) if m.direction == MessageDirection.OUT),
                None,
            )
            if last_any:
                cur.execute(
                    """
                    UPDATE chat_threads SET
                        last_preview = %s,
                        updated_at = NOW()
                    WHERE chat_id = %s
                    """,
                    (last_any.text[:500], chat_id),
                )
            if last_in:
                cur.execute(
                    """
                    UPDATE chat_threads SET
                        last_inbound_at = COALESCE(%s, last_inbound_at),
                        last_inbound_hash = %s,
                        updated_at = NOW()
                    WHERE chat_id = %s
                    """,
                    (
                        last_in.sent_at,
                        inbound_hash(last_in.text),
                        chat_id,
                    ),
                )
            if last_out:
                cur.execute(
                    """
                    UPDATE chat_threads SET
                        last_outbound_at = COALESCE(%s, last_outbound_at),
                        updated_at = NOW()
                    WHERE chat_id = %s
                    """,
                    (last_out.sent_at, chat_id),
                )
        self._conn.commit()
        return added

    def get_thread(self, chat_id: str) -> ChatThread | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_threads WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
        if not row:
            return None
        return ChatThread(
            chat_id=row["chat_id"],
            title=row["title"] or "",
            subtitle=row["subtitle"],
            url=row["url"] or "",
            source=ChatSource(row["source"] or "unknown"),
            status=ChatThreadStatus(row["status"] or "new"),
            bot_paused=bool(row["bot_paused"]),
            paused_reason=row["paused_reason"],
            vacancy_hh_id=row["vacancy_hh_id"],
            company_hh_id=row["company_hh_id"],
            company=row["company_name"],
            last_inbound_hash=row["last_inbound_hash"],
            last_message_preview=row["last_preview"],
        )

    def clear_pause_if_new_inbound(self, chat_id: str, inbound_text: str) -> bool:
        """Если bot_paused и пришёл новый inbound (другой hash) — снять паузу."""
        h = inbound_hash(inbound_text)
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT bot_paused, last_inbound_hash FROM chat_threads WHERE chat_id = %s",
                (chat_id,),
            )
            row = cur.fetchone()
            if not row or not row["bot_paused"]:
                return False
            if row["last_inbound_hash"] == h:
                return False
            cur.execute(
                """
                UPDATE chat_threads SET
                    bot_paused = FALSE,
                    paused_reason = NULL,
                    status = %s,
                    last_inbound_hash = %s,
                    updated_at = NOW()
                WHERE chat_id = %s
                """,
                (ChatThreadStatus.AWAITING_US.value, h, chat_id),
            )
        self._conn.commit()
        return True

    def reactivate_closed_if_unread(
        self,
        chat_id: str,
        *,
        inbound_text: str | None = None,
        unread: bool = True,
    ) -> bool:
        """closed + непрочитанное/новое входящее → awaiting_us."""
        if not unread and not inbound_text:
            return False
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT status, last_inbound_hash FROM chat_threads WHERE chat_id = %s",
                (chat_id,),
            )
            row = cur.fetchone()
            if not row or row["status"] != ChatThreadStatus.CLOSED.value:
                return False
            h = inbound_hash(inbound_text) if inbound_text else None
            cur.execute(
                """
                UPDATE chat_threads SET
                    status = %s,
                    last_inbound_hash = COALESCE(%s, last_inbound_hash),
                    last_preview = COALESCE(%s, last_preview),
                    updated_at = NOW()
                WHERE chat_id = %s AND status = %s
                """,
                (
                    ChatThreadStatus.AWAITING_US.value,
                    h,
                    (inbound_text[:500] if inbound_text else None),
                    chat_id,
                    ChatThreadStatus.CLOSED.value,
                ),
            )
            changed = cur.rowcount > 0
        self._conn.commit()
        return changed
