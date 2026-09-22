"""Telegram-уведомления для hh_autoapply.

Токен бота — BOT_TOKEN из finance_bot/.env (не дублируем в этом проекте).
Chat id захардкожен (личный).
"""

from __future__ import annotations

from pathlib import Path

import httpx
from dotenv import dotenv_values
from loguru import logger

from config import ROOT

# job_search/hh_autoapply → MyProjects/finance_bot/.env
FINANCE_BOT_ENV = ROOT.parent.parent / "finance_bot" / ".env"

# Личный Telegram user id
TG_NOTIFY_CHAT_ID = 1414395118

_API = "https://api.telegram.org"


def _bot_token() -> str:
    """BOT_TOKEN из finance_bot/.env; опционально HH_TG_BOT_TOKEN поверх."""
    from config import env

    override = env("HH_TG_BOT_TOKEN")
    if override:
        return override
    if not FINANCE_BOT_ENV.is_file():
        logger.warning("Нет {} — Telegram-уведомления недоступны", FINANCE_BOT_ENV)
        return ""
    vals = dotenv_values(FINANCE_BOT_ENV)
    return (vals.get("BOT_TOKEN") or "").strip()


def send_telegram(text: str, *, chat_id: int = TG_NOTIFY_CHAT_ID) -> bool:
    """Отправить сообщение. Токен в лог не пишем. False при сбое."""
    token = _bot_token()
    if not token:
        logger.error("Telegram: пустой BOT_TOKEN (finance_bot/.env)")
        return False
    url = f"{_API}/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code != 200:
            logger.error(
                "Telegram send failed: HTTP {} body={}",
                resp.status_code,
                (resp.text or "")[:200],
            )
            return False
        logger.info("Telegram: уведомление отправлено chat_id={}", chat_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram send error: {}", exc)
        return False
