"""Настройки Selenium-автооткликов на hh.ru."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent  # hh_autoapply/
JOB_SEARCH_ROOT = ROOT.parent  # job_search/ — только для context/
ENV_PATH = ROOT / ".env"
CONTEXT_DIR = JOB_SEARCH_ROOT / "context"

# Профиль Chrome: cookies/сессия между запусками (не в git)
PROFILE_DIR = ROOT / ".chrome_profile"

# HTML-дампы страниц в debug-режиме (не в git)
DEBUG_PAGES_DIR = ROOT / "debug_pages"

# Резюме для LLM-матчера (локальная копия)
RESUME_DIR = ROOT / "resume"
DEFAULT_RESUME_PATH = RESUME_DIR / "resume.txt"

HH_BASE = "https://hh.ru"
HH_LOGIN_URL = f"{HH_BASE}/account/login"

DEFAULT_TARGET_ROLE = "Python Backend Developer"

# Ежедневный широкий поиск: text=Python, без региона (вся РФ / удалёнка),
# 100 карточек на странице. period=0 — всё время; для daily позже period=7.
DEFAULT_DAILY_SEARCH_URL = (
    "https://hh.ru/search/vacancy?"
    "items_on_page=100&ored_clusters=true&text=Python&search_period=0"
    "&hhtmFromLabel=search_order_button&hhtmFrom=vacancy_search_list"
)

DEFAULT_PG = {
    "name": "hh_autoapply",
    "user": "hh_autoapply",
    "password": "hh_autoapply",
    "host": "127.0.0.1",
    "port": "5433",
}

# OpenAI-совместимый API (OpenAI / OpenRouter / локальный proxy)
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"


def load_env() -> None:
    load_dotenv(ENV_PATH)


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def search_url() -> str:
    return env("HH_SEARCH_URL") or DEFAULT_DAILY_SEARCH_URL


def build_daily_search_url(
    *,
    base: str | None = None,
    period: int | None = None,
    items_on_page: int = 100,
    drop_area: bool = True,
) -> str:
    """Собрать URL ежедневного поиска.

    Канон — DEFAULT_DAILY_SEARCH_URL (Python, без региона, 100/стр).
    ``base`` / HH_SEARCH_URL подставляются только если явно переданы
    или env уже содержит text= (иначе игнорируем мусорный старый URL).

    - без area (весь сайт), если drop_area=True
    - items_on_page=100
    - period: None = как в base; 0 = всё время; 7 = за неделю
    """
    if base is not None and base.strip():
        raw = base.strip()
    else:
        from_env = env("HH_SEARCH_URL")
        # Берём env только если это похоже на поиск по text=
        if from_env and "text=" in from_env and "/search/vacancy" in from_env:
            raw = from_env
        else:
            raw = DEFAULT_DAILY_SEARCH_URL

    if raw.startswith("//"):
        raw = "https:" + raw
    elif not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")

    parts = urlparse(raw)
    flat: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if drop_area and (key == "area" or key.startswith("area[")):
            continue
        flat[key] = value

    flat["items_on_page"] = str(items_on_page)
    flat.setdefault("ored_clusters", "true")
    flat.setdefault("text", "Python")
    if period is not None:
        flat["search_period"] = str(int(period))

    query = urlencode(list(flat.items()))
    return urlunparse(
        (
            parts.scheme or "https",
            parts.netloc or "hh.ru",
            parts.path or "/search/vacancy",
            "",
            query,
            "",
        )
    )


def target_role() -> str:
    return env("HH_TARGET_ROLE", DEFAULT_TARGET_ROLE)


def pause_between_actions_sec() -> float:
    """Пауза после клика/скролла/ввода (HH_PAUSE_SEC, по умолчанию 2.5)."""
    raw = env("HH_PAUSE_SEC", "2.5")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.5


def resolve_action_pause(*, explicit: float | None, debug: bool = False) -> float:
    """Пауза между действиями.

    Live (без --debug): 0, если не передали --pause.
    --debug без --pause: HH_PAUSE_SEC, чтобы было видно клики.
    """
    if explicit is not None:
        return max(0.0, float(explicit))
    if debug:
        return pause_between_actions_sec()
    return 0.0


def pg_conninfo() -> dict[str, str | int]:
    return {
        "dbname": env("PG_NAME", DEFAULT_PG["name"]),
        "user": env("PG_USER", DEFAULT_PG["user"]),
        "password": env("PG_PASSWORD", DEFAULT_PG["password"]),
        "host": env("PG_HOST", DEFAULT_PG["host"]),
        "port": int(env("PG_PORT", DEFAULT_PG["port"])),
    }


def pg_dsn_display() -> str:
    c = pg_conninfo()
    return f"postgresql://{c['user']}@{c['host']}:{c['port']}/{c['dbname']}"


def resume_path() -> Path:
    raw = env("HH_RESUME_PATH")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    return DEFAULT_RESUME_PATH


def load_resume_text() -> str:
    path = resume_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Нет файла резюме: {path}\n"
            "Положи текст резюме в resume/resume.txt или задай HH_RESUME_PATH."
        )
    return path.read_text(encoding="utf-8").strip()


def llm_api_key() -> str:
    return env("OPENAI_API_KEY") or env("LLM_API_KEY")


def llm_base_url() -> str:
    return env("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/")


def llm_model() -> str:
    return env("OPENAI_MODEL", DEFAULT_LLM_MODEL)


def daily_apply_limit() -> int:
    """Жёсткий потолок откликов за календарный день (БД), независимо от CLI."""
    raw = env("HH_DAILY_APPLY_LIMIT", "200") or "200"
    try:
        n = int(raw)
    except ValueError:
        n = 200
    return max(0, n)


def daily_apply_tz_name() -> str:
    return env("HH_DAILY_TZ", "Europe/Moscow") or "Europe/Moscow"


def day_start_utc() -> datetime:
    """Начало «сегодня» в HH_DAILY_TZ, в UTC."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(daily_apply_tz_name())
    local_now = datetime.now(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)
