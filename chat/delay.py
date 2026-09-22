"""Человеческие паузы перед отправкой ответа в чат."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DelayConfig:
    """base + len(text)/cps + jitter, в [min_sec, max_sec]."""

    base_min: float = 8.0
    base_max: float = 20.0
    chars_per_sec: float = 5.5
    jitter_ratio: float = 0.15
    min_sec: float = 15.0
    max_sec: float = 180.0


DEFAULT_DELAY = DelayConfig()


def compute_reply_delay(text: str, *, cfg: DelayConfig = DEFAULT_DELAY) -> float:
    """Секунды ожидания перед send: «прочитал» + «набрал» + шум."""
    body = text or ""
    base = random.uniform(cfg.base_min, cfg.base_max)
    typing = len(body) / max(cfg.chars_per_sec, 0.1)
    total = base + typing
    jitter = total * random.uniform(-cfg.jitter_ratio, cfg.jitter_ratio)
    return max(cfg.min_sec, min(cfg.max_sec, total + jitter))


def remaining_reply_delay(
    planned_sec: float,
    last_inbound_at: datetime | None,
    *,
    now: datetime | None = None,
) -> float:
    """Сколько ещё ждать: если с сообщения HR уже прошло ≥ planned — 0."""
    planned = max(0.0, float(planned_sec))
    if last_inbound_at is None or planned <= 0:
        return planned
    now = now or datetime.now(timezone.utc)
    inbound = last_inbound_at
    if inbound.tzinfo is None:
        inbound = inbound.replace(tzinfo=timezone.utc)
    elapsed = (now - inbound.astimezone(timezone.utc)).total_seconds()
    if elapsed >= planned:
        return 0.0
    return planned - elapsed
