"""Telegram-уведомления для hh_autoapply.

Токен — HH_TG_BOT_TOKEN из .env этого проекта.
Если его нет, запасной вариант — BOT_TOKEN из finance_bot/.env.
Chat id захардкожен (личный).
Ответ на капчу читаем через getUpdates, пока ждём текст.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx
from dotenv import dotenv_values
from loguru import logger

from config import ROOT

# job_search/hh_autoapply → MyProjects/finance_bot/.env
FINANCE_BOT_ENV = ROOT.parent.parent / "finance_bot" / ".env"

# Личный Telegram user id
TG_NOTIFY_CHAT_ID = 1414395118

_API = "https://api.telegram.org"
# Подтверждённый update_id + 1. Иначе старое сообщение в чате сойдёт за ответ на капчу.
_update_offset: int | None = None


def _bot_token() -> str:
    """HH_TG_BOT_TOKEN из .env проекта, иначе BOT_TOKEN finance_bot."""
    from config import env, load_env

    load_env()
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


def send_telegram_photo(
    png: bytes,
    caption: str,
    *,
    chat_id: int = TG_NOTIFY_CHAT_ID,
) -> bool:
    """Картинка в личный чат. В подписи просим ответить текстом."""
    token = _bot_token()
    if not token:
        logger.error("Telegram: пустой BOT_TOKEN (finance_bot/.env)")
        return False
    url = f"{_API}/bot{token}/sendPhoto"
    data = {
        "chat_id": str(chat_id),
        "caption": caption[:1000],
        "reply_markup": json.dumps(
            {
                "force_reply": True,
                "input_field_placeholder": "Текст с картинки",
            }
        ),
    }
    files = {"photo": ("captcha.png", png, "image/png")}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, data=data, files=files)
        if resp.status_code != 200:
            logger.error(
                "Telegram photo failed: HTTP {} body={}",
                resp.status_code,
                (resp.text or "")[:200],
            )
            return False
        logger.info("Telegram: картинка отправлена chat_id={}", chat_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram photo error: {}", exc)
        return False


def wait_telegram_text(
    *,
    timeout_sec: float,
    chat_id: int = TG_NOTIFY_CHAT_ID,
    since_ts: float | None = None,
    stop_if: Callable[[], bool] | None = None,
) -> str | None:
    """Ждать текстовый ответ в чате уведомлений.

    None — время вышло или сработал stop_if (капчу уже сняли в браузере).
    Команды со «/» пропускаем.
    """
    global _update_offset

    token = _bot_token()
    if not token:
        logger.error("Telegram: пустой BOT_TOKEN, ответ не прочитать")
        return None

    not_before = since_ts if since_ts is not None else time.time()
    deadline = time.time() + max(1.0, float(timeout_sec))
    url = f"{_API}/bot{token}/getUpdates"
    conflict_noted = False

    while time.time() < deadline:
        if stop_if is not None and stop_if():
            return None
        long_poll = int(min(20, max(1, deadline - time.time())))
        params: dict[str, str | int] = {
            "timeout": long_poll,
            "allowed_updates": '["message"]',
        }
        if _update_offset is not None:
            params["offset"] = _update_offset
        try:
            with httpx.Client(timeout=long_poll + 15) as client:
                resp = client.get(url, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram getUpdates: {}", exc)
            time.sleep(2)
            continue
        if resp.status_code == 409:
            logger.warning("Telegram getUpdates занят другим опросом, повтор")
            if not conflict_noted:
                conflict_noted = True
                send_telegram(
                    "Ответ на капчу пока не читается: другой процесс держит бота. "
                    "Пришли текст ещё раз через несколько секунд."
                )
            time.sleep(2)
            continue
        if resp.status_code != 200:
            logger.error(
                "Telegram getUpdates HTTP {} body={}",
                resp.status_code,
                (resp.text or "")[:200],
            )
            time.sleep(2)
            continue
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram getUpdates: не JSON ({})", exc)
            time.sleep(2)
            continue
        for upd in payload.get("result") or []:
            try:
                _update_offset = int(upd["update_id"]) + 1
            except (KeyError, TypeError, ValueError):
                continue
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id")) != str(chat_id):
                continue
            if (msg.get("date") or 0) < not_before - 2:
                continue
            text = (msg.get("text") or "").strip()
            if not text or text.startswith("/"):
                continue
            logger.info("Telegram: получен ответ, {} символов", len(text))
            return text
    return None
