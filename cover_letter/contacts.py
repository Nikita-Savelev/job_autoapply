"""Актуальные контакты для откликов (не из PDF резюме).

Можно переопределить через .env: HH_PHONE, HH_EMAIL, HH_TELEGRAM.
"""

from __future__ import annotations

from config import env

CANDIDATE_NAME = "Никита Гамиев"
# Фамилия в PDF-резюме может отличаться — в письмах всегда Гамиев (как в аккаунте hh).
RESUME_NAME_ALIASES = ("Савельев", "Савельева")

# Актуальные (Армения) — не брать устаревшие из PDF резюме
DEFAULT_PHONE = "+374 98 99-61-56"
DEFAULT_EMAIL = "nik1ta.savelev.2000@inbox.ru"
DEFAULT_TELEGRAM = "@GamievNikita"


def candidate_phone() -> str:
    return env("HH_PHONE", DEFAULT_PHONE)


def candidate_email() -> str:
    return env("HH_EMAIL", DEFAULT_EMAIL)


def candidate_telegram() -> str:
    return env("HH_TELEGRAM", DEFAULT_TELEGRAM)
