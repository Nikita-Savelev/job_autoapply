"""Модели вакансий для автооткликов."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class VacancyStatus(StrEnum):
    NEW = "new"
    SKIPPED = "skipped"
    APPLIED = "applied"
    ERROR = "error"
    BLOCKED = "blocked"


@dataclass
class Company:
    """Карточка работодателя с hh.ru (кэш, чтобы не парсить повторно)."""

    hh_id: str
    name: str
    url: str
    description: str | None = None
    industries: str | None = None
    site: str | None = None
    extra: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def profile_text(self, *, max_len: int = 4000) -> str:
        """Сводка для LLM / админки."""
        parts: list[str] = []
        if self.name:
            parts.append(f"Название: {self.name}")
        if self.industries:
            parts.append(f"Отрасли: {self.industries}")
        if self.site:
            parts.append(f"Сайт: {self.site}")
        if self.description:
            parts.append(f"Описание:\n{self.description}")
        if self.extra:
            parts.append(f"Дополнительно:\n{self.extra}")
        text = "\n".join(parts).strip()
        return text[:max_len] if max_len else text


@dataclass
class Vacancy:
    """Одна вакансия с поисковой выдачи / карточки."""

    hh_id: str
    title: str
    url: str
    status: VacancyStatus = VacancyStatus.NEW
    company: str | None = None
    company_hh_id: str | None = None
    salary: str | None = None
    snippet: str | None = None  # короткий текст с выдачи
    description: str | None = None  # полный текст со страницы вакансии
    match_score: float | None = None
    skip_reason: str | None = None
    cover_letter: str | None = None
    error_message: str | None = None
    raw_json: str | None = None  # запасной дамп карточки
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
