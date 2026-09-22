"""Чат HH (chatik): ответы на сообщения HR / ассистентов / холодные офферы."""

from __future__ import annotations

from chat.models import (
    ChatActionKind,
    ChatMessage,
    ChatThread,
    ChatThreadStatus,
    EscalateReason,
)

__all__ = [
    "ChatActionKind",
    "ChatMessage",
    "ChatThread",
    "ChatThreadStatus",
    "EscalateReason",
]
