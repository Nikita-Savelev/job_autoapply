"""Доменные модели чата."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class ChatThreadStatus(StrEnum):
    """Статусы треда (важные для sweep / БД / админки).

    new            — увидели в списке, ещё не открывали / не синхронизировали
    awaiting_us    — последнее сообщение не наше, бот должен ответить
    awaiting_them  — наше исходящее подтверждено в UI, ждём HR
    needs_human    — эскалация (тест / TG / stuck); bot_paused
    rejected       — отказ работодателя (бейдж «Отказ» / текст отказа)
    interview      — собеседование / приглашение (бейдж в списке)
    closed         — диалог закрыт по иной причине
    error          — сбой send/verify/UI; можно ретраить
    """

    NEW = "new"
    AWAITING_US = "awaiting_us"
    AWAITING_THEM = "awaiting_them"
    NEEDS_HUMAN = "needs_human"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    CLOSED = "closed"
    ERROR = "error"


class ChatSource(StrEnum):
    RESPONSE = "response"  # первое сообщение наше → чат после отклика
    INBOUND = "inbound"  # первое сообщение их → холодный поиск по резюме
    UNKNOWN = "unknown"


class MessageDirection(StrEnum):
    IN = "in"
    OUT = "out"


class ChatActionKind(StrEnum):
    REPLY = "reply"
    SKIP = "skip"
    ESCALATE = "escalate"


class EscalateReason(StrEnum):
    """Когда TG + bot_paused (человек забирает тред до их следующего сообщения)."""

    INTERVIEW = "interview"
    EXTERNAL_TASK = "external_task"
    BOT_STUCK = "bot_stuck"
    CAPTCHA = "captcha"
    OTHER = "other"


@dataclass
class ChatMessage:
    direction: MessageDirection
    text: str
    sent_at: datetime | None = None
    author_label: str | None = None
    external_id: str | None = None
    our_action: str | None = None  # auto | human | none


@dataclass
class ChatThread:
    chat_id: str
    title: str = ""
    subtitle: str | None = None
    url: str = ""
    source: ChatSource = ChatSource.UNKNOWN
    status: ChatThreadStatus = ChatThreadStatus.NEW
    vacancy_hh_id: str | None = None
    company_hh_id: str | None = None
    company: str | None = None
    unread: bool = False
    bot_paused: bool = False
    paused_reason: str | None = None
    last_message_preview: str | None = None
    # Подпись времени в списке: «13:46» — сегодня, «пн» / «27.09» — старше.
    list_time_label: str = ""
    last_message_at: datetime | None = None
    last_inbound_hash: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def last_message(self) -> ChatMessage | None:
        return self.messages[-1] if self.messages else None

    def last_inbound_text(self) -> str | None:
        for msg in reversed(self.messages):
            if msg.direction == MessageDirection.IN and msg.text.strip():
                return msg.text.strip()
        return None

    def infer_source(self) -> ChatSource:
        """Первое наше → отклик; первое их → холодная рассылка."""
        if not self.messages:
            return ChatSource.UNKNOWN
        if self.messages[0].direction == MessageDirection.OUT:
            return ChatSource.RESPONSE
        return ChatSource.INBOUND

    def needs_our_reply(self) -> bool:
        """Триггер: последнее сообщение не наше (даже если уже прочитано)."""
        if self.bot_paused:
            return False
        last = self.last_message()
        if last is None:
            return False
        return last.direction == MessageDirection.IN

    def reactivate_from_closed_if_needed(self) -> bool:
        """rejected/closed/interview + новое входящее → awaiting_us (не при том же отказе)."""
        if self.status not in (
            ChatThreadStatus.CLOSED,
            ChatThreadStatus.REJECTED,
            ChatThreadStatus.INTERVIEW,
        ):
            return False
        from chat.classify import (
            is_hh_interview_badge,
            is_hh_reject_badge,
            looks_like_reject,
        )

        if is_hh_reject_badge(self.last_message_preview):
            return False
        if is_hh_interview_badge(self.last_message_preview):
            return False
        if looks_like_reject(self.last_inbound_text() or ""):
            return False
        has_new_inbound = self.unread or (
            self.last_message() is not None
            and self.last_message().direction == MessageDirection.IN
        )
        if not has_new_inbound:
            return False
        self.status = ChatThreadStatus.AWAITING_US
        self.unread = False
        self.bot_paused = False
        self.paused_reason = None
        return True


@dataclass
class ChatDraft:
    kind: ChatActionKind
    text: str = ""
    reason: str = ""
    delay_sec: float = 0.0
    escalate_reason: EscalateReason | None = None
