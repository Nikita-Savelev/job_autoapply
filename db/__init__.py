"""Слой хранения вакансий (PostgreSQL)."""

from __future__ import annotations

from db.models import Company, Vacancy, VacancyStatus
from db.store import VacancyStore

__all__ = ["Company", "Vacancy", "VacancyStatus", "VacancyStore"]
