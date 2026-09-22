"""Парсинг DOM чата из HTML (дампы / page_source) без Selenium."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chat.models import ChatMessage, MessageDirection

_MSG_ROOT_RE = re.compile(r'data-qa="chatik-chat-message-(\d+)"(?!-)')
_EMPLOYER_RE = re.compile(r"/employer/(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")
_HH_TZ = ZoneInfo("Europe/Moscow")

_MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def parse_messages_html(html: str) -> list[ChatMessage]:
    """Сообщения открытого треда в порядке DOM."""
    roots = list(_MSG_ROOT_RE.finditer(html))
    out: list[ChatMessage] = []
    # дата-разделитель может быть в предыдущем куске — тащим «текущую» дату
    running_day: datetime | None = None
    now = datetime.now(_HH_TZ)

    for i, m in enumerate(roots):
        mid = m.group(1)
        start = m.start()
        end = roots[i + 1].start() if i + 1 < len(roots) else len(html)
        # чуть назад — поймать «Сегодня» перед корнем
        lookback = html[max(0, start - 800) : end]
        chunk = html[start:end]
        day = _extract_day_label(lookback, now=now) or running_day
        if day is not None:
            running_day = day
        outgoing = (
            "chat-bubble_outgoing" in chunk or "message_my--" in chunk
        )
        text = _extract_bubble_text(chunk)
        if not text:
            continue
        author = _extract_qa_text(chunk, "chat-bubble-author-name")
        sent_at = _extract_sent_at(chunk, day=running_day, now=now)
        out.append(
            ChatMessage(
                direction=(
                    MessageDirection.OUT if outgoing else MessageDirection.IN
                ),
                text=text,
                author_label=author,
                external_id=mid,
                sent_at=sent_at,
            )
        )
    return out


def _extract_day_label(chunk: str, *, now: datetime) -> datetime | None:
    m = re.search(
        r'class="[^"]*chat-date[^"]*"[^>]*>.*?<span>([^<]+)</span>',
        chunk,
        re.S | re.I,
    )
    if not m:
        return None
    label = m.group(1).strip().lower()
    if label == "сегодня":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if label == "вчера":
        d = now - timedelta(days=1)
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    # «21 сентября» / «21 сентября 2025»
    m2 = re.match(
        r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?",
        label,
        re.I,
    )
    if not m2:
        return None
    day = int(m2.group(1))
    month = _MONTHS_RU.get(m2.group(2).lower())
    if not month:
        return None
    year = int(m2.group(3)) if m2.group(3) else now.year
    try:
        return datetime(year, month, day, tzinfo=_HH_TZ)
    except ValueError:
        return None


def _extract_sent_at(
    chunk: str,
    *,
    day: datetime | None,
    now: datetime,
) -> datetime | None:
    m = re.search(
        r'data-qa="chat-buble-display-time"[^>]*>.*?<span>(\d{1,2}):(\d{2})</span>',
        chunk,
        re.S | re.I,
    )
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    base = day or now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None


def _extract_bubble_text(chunk: str) -> str:
    m = re.search(
        r'data-qa="chat-bubble-text"[^>]*>(.*?)</span>',
        chunk,
        re.S | re.I,
    )
    if m:
        return _html_to_text(m.group(1))
    m = re.search(
        r'data-qa="chatik-chat-message-\d+-text"[^>]*>(.*?)</div>',
        chunk,
        re.S | re.I,
    )
    if m:
        return _html_to_text(m.group(1))
    return ""


def _extract_qa_text(chunk: str, qa: str) -> str | None:
    m = re.search(
        rf'data-qa="{re.escape(qa)}"[^>]*>(.*?)</(?:span|div)>',
        chunk,
        re.S | re.I,
    )
    if not m:
        return None
    t = _html_to_text(m.group(1))
    return t or None


def _html_to_text(fragment: str) -> str:
    frag = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    frag = re.sub(r"(?i)</p\s*>", "\n", frag)
    frag = _TAG_RE.sub("", frag)
    frag = (
        frag.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    lines = [ln.strip() for ln in frag.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


def parse_employer_hh_id(html: str) -> str | None:
    m = re.search(
        r'data-qa="participant-info-details"[^>]*href="([^"]+)"',
        html,
    )
    if not m:
        m = re.search(
            r'href="([^"]*employer/\d+[^"]*)"[^>]*data-qa="participant-info-details"',
            html,
        )
    if not m:
        return None
    em = _EMPLOYER_RE.search(m.group(1))
    return em.group(1) if em else None


def parse_vacancy_hh_id(html: str) -> str | None:
    """ID вакансии из шапки открытого чата."""
    m = re.search(
        r'data-qa="chatik-header-vacancy-link"[^>]*href="([^"]+)"',
        html,
        re.I,
    )
    if not m:
        m = re.search(
            r'href="([^"]*vacancy/\d+[^"]*)"[^>]*data-qa="chatik-header-vacancy-link"',
            html,
            re.I,
        )
    if not m:
        return None
    vm = re.search(r"/vacancy/(\d+)", m.group(1))
    return vm.group(1) if vm else None


def parse_participant_title(html: str) -> str | None:
    m = re.search(
        r'data-qa="participant-info-title"[^>]*>.*?<span[^>]*>'
        r"([^<]+)</span>",
        html,
        re.S,
    )
    if m:
        return m.group(1).strip() or None
    return None


def parse_participant_subtitle(html: str) -> str | None:
    m = re.search(
        r'data-qa="participant-info-subtitle"[^>]*>([^<]+)<',
        html,
    )
    if m:
        return m.group(1).strip() or None
    return None


def parse_chat_ids_from_list(html: str) -> list[str]:
    ids = re.findall(r'data-qa="chatik-open-chat-(\d+)"', html)
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def is_message_root_qa(qa: str) -> bool:
    return bool(re.fullmatch(r"chatik-chat-message-\d+", qa or ""))
