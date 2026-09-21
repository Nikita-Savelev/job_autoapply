"""Сопоставление вакансий с резюме (LLM batch)."""

from __future__ import annotations

from matcher.llm_batch import MatchDecision, match_vacancies_batch

__all__ = ["MatchDecision", "match_vacancies_batch"]
