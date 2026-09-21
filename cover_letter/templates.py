"""Канон структуры + fallback-шаблон (контакты — в contacts.py)."""

from __future__ import annotations

from pathlib import Path

from cover_letter.contacts import (
    candidate_email,
    candidate_phone,
    candidate_telegram,
)

STRUCTURE_PATH = Path(__file__).resolve().parent / "structure.md"
VOICE_RULES_PATH = Path(__file__).resolve().parent / "voice_rules.md"

FALLBACK_TEMPLATE = """\
Здравствуйте!

Меня зовут Никита Гамиев, хочу присоединиться к вашей команде \
в роли {vacancy_title}{company_part}.

Я Backend / Senior Python разработчик, около пяти лет коммерческой разработки: \
Django и FastAPI, PostgreSQL, микросервисы, интеграции, боты и LLM-обвязка.

{why_company}

{why_me}

Буду рад обсудить детали или тестовое.

Связаться удобнее по телефону {phone}, почте {email} или в Telegram {telegram}.

С уважением,
Никита Гамиев
"""


def load_structure_guide() -> str:
    if STRUCTURE_PATH.exists():
        return STRUCTURE_PATH.read_text(encoding="utf-8")
    return "Структура: приветствие, представление, почему компания, почему я, контакты."


def load_voice_rules() -> str:
    if VOICE_RULES_PATH.exists():
        return VOICE_RULES_PATH.read_text(encoding="utf-8")
    return ""


def contact_fields() -> dict[str, str]:
    return {
        "phone": candidate_phone(),
        "email": candidate_email(),
        "telegram": candidate_telegram(),
    }
