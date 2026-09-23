"""Генерация текста ответа в чат (LLM)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from loguru import logger

from chat.classify import (
    ChatIntent,
    classify_thread,
    intent_to_action,
    is_hh_reject_badge,
    looks_like_needs_reply,
    looks_like_reject,
)
from chat.delay import compute_reply_delay
from chat.models import (
    ChatActionKind,
    ChatDraft,
    ChatThread,
    EscalateReason,
    MessageDirection,
)
from config import llm_api_key, llm_base_url, llm_model, target_role
from matcher.test_answers import load_hr_answer_context

NO_REPLY_TOKEN = "NO_REPLY"
REJECT_TOKEN = "REJECT"

# Лестница ЗП в чате: 1-й вопрос, повторное уточнение, дальше — человек.
_SALARY_FIRST = (
    "Я только недавно вышел на рынок и пока только присматриваюсь "
    "к требованиям и актуальным вилкам. Но я открыт к любым предложениям)"
)
_SALARY_SECOND = (
    "Честно, я бы и сам рад назвать цифру, но такой в уме пока нет. "
    "Если подскажете вилку по вакансии, скажу сразу, сойдёмся или нет."
)

_CONTEXT_DIR = Path(__file__).resolve().parents[2] / "context"


def _load_style_context() -> str:
    """Правила голоса для чата (без полных примеров intro с CTA)."""
    chunks: list[str] = []
    for name in ("communication-rules.md",):
        path = _CONTEXT_DIR / name
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8").strip())
    if not chunks:
        return "(см. system hint)"
    # режем, чтобы не раздувать промпт
    text = "\n\n".join(chunks)
    if len(text) > 6000:
        text = text[:6000] + "\n…"
    return text


SYSTEM_HINT_BASE = """\
Ты пишешь ответ в чате hh.ru от кандидата (Senior Python Backend, ~5 лет, Армения, только remote).
Канон: ЗП/офис/гражданство РФ/GitHub по эталону; без «нет опыта».
Оформление (трудовой договор / ИП / ГПХ / самозанятость) - да, любой формат.
Отказ только от офиса/гибрида: работа строго remote (живу в Армении).

Главное - МЕНЬШЕ ВОДЫ (покороче по умолчанию):
- Обычно 2-4 коротких предложения. Если вопрос широкий (архитектура, несколько
  подпунктов) - можно длиннее, но без воды и без разжёвывания.
- Плохо: «В проектах на BOTTEC я использовал модели машинного обучения для создания
  ИИ-ассистентов, что включает в себя интеграцию с различными API...»
- Ок: «На BOTTEC делал ИИ-ассистентов: LangChain, LLM API, обработка данных.»
- Не пересказывай очевидное. Не объясняй «что такое» то, о чём спросили.
- Без хвостов: «готов обсудить», «буду рад», «дайте знать», «жду вопросов»,
  «если нужно больше деталей», «с радостью расскажу».
- Без длинных тире (—/–), стрелок, ёлочек. Дефис "-" или запятая/точка.
- Не начинай с «Спасибо за информацию», если уже общались.

История (ОНИ и МЫ):
- Не повторяй то, что МЫ уже писали; на повтор вопроса - 1 фраза-отсылка.
- Отказ работодателя → REJECT. Финал «спасибо, ждите» → NO_REPLY.
- Не путай «хотите откликнуться?» с отказом.
- Нет нового вопроса → NO_REPLY. Есть вопрос → обычный текст.

Только текст ИЛИ NO_REPLY / REJECT. Без JSON.
"""

SYSTEM_HINT_COLD = """\
Холодный/скрининг: коротко подтверди интерес (если ок по канону), ответь на вопрос.
Встречный вопрос - максимум один и только если без него стоп (формат/стек/этап).
Без воды и без «готов обсудить».
"""


def compose_reply(
    thread: ChatThread,
    *,
    resume_text: str = "",
    vacancy_description: str = "",
    company_profile: str = "",
    vacancy_title: str = "",
    dry_run: bool = False,
) -> ChatDraft:
    intent = classify_thread(thread)
    kind, esc = intent_to_action(intent)

    if kind == ChatActionKind.SKIP:
        return ChatDraft(kind=kind, reason=f"intent={intent.value}")

    if kind == ChatActionKind.ESCALATE:
        return ChatDraft(
            kind=kind,
            reason=f"intent={intent.value}",
            escalate_reason=esc or EscalateReason.OTHER,
        )

    inbound = thread.last_inbound_text() or ""
    if not inbound:
        return ChatDraft(
            kind=ChatActionKind.ESCALATE,
            reason="empty_inbound",
            escalate_reason=EscalateReason.BOT_STUCK,
        )

    salary = _salary_reply(thread)
    if salary is not None:
        return salary

    tone = "cold" if intent == ChatIntent.COLD_OFFER else "screening"
    try:
        text = _llm_reply(
            thread,
            resume_text=resume_text,
            vacancy_description=vacancy_description,
            company_profile=company_profile,
            vacancy_title=vacancy_title,
            tone=tone,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("chat compose LLM fail: {}", exc)
        return ChatDraft(
            kind=ChatActionKind.ESCALATE,
            reason=f"llm_error:{exc}",
            escalate_reason=EscalateReason.BOT_STUCK,
        )

    text = _clean_reply(text)
    if text.upper() == REJECT_TOKEN:
        inbound = thread.last_inbound_text() or ""
        # LLM часто путает «хотите откликнуться?» с отказом — подтверждаем эвристикой
        if looks_like_reject(inbound) or is_hh_reject_badge(
            thread.last_message_preview
        ):
            return ChatDraft(
                kind=ChatActionKind.SKIP,
                reason="intent=reject;llm",
            )
        logger.warning(
            "chat {}: LLM вернул REJECT без признаков отказа — переспрашиваю",
            thread.chat_id,
        )
        try:
            text = _clean_reply(
                _llm_reply(
                    thread,
                    resume_text=resume_text,
                    vacancy_description=vacancy_description,
                    company_profile=company_profile,
                    vacancy_title=vacancy_title,
                    tone=tone,
                    extra_hint=(
                        "Последнее входящее — НЕ отказ. Нельзя отвечать REJECT. "
                        "Нужен обычный ответ кандидата на их сообщение."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat compose LLM retry fail: {}", exc)
            return ChatDraft(
                kind=ChatActionKind.ESCALATE,
                reason=f"llm_retry_error:{exc}",
                escalate_reason=EscalateReason.BOT_STUCK,
            )
        if text.upper() == REJECT_TOKEN:
            return ChatDraft(
                kind=ChatActionKind.ESCALATE,
                reason="llm_false_reject",
                escalate_reason=EscalateReason.BOT_STUCK,
            )
    if text.upper() == NO_REPLY_TOKEN or not text:
        inbound = thread.last_inbound_text() or ""
        if text.upper() == NO_REPLY_TOKEN and looks_like_needs_reply(inbound):
            logger.warning(
                "chat {}: LLM вернул NO_REPLY на вопрос — переспрашиваю",
                thread.chat_id,
            )
            try:
                text = _clean_reply(
                    _llm_reply(
                        thread,
                        resume_text=resume_text,
                        vacancy_description=vacancy_description,
                        company_profile=company_profile,
                        vacancy_title=vacancy_title,
                        tone=tone,
                        extra_hint=(
                            "Последнее входящее — вопрос к кандидату. "
                            "Нельзя отвечать NO_REPLY. Нужен обычный ответ."
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat compose LLM no_reply-retry fail: {}", exc)
                return ChatDraft(
                    kind=ChatActionKind.ESCALATE,
                    reason=f"llm_noreply_retry_error:{exc}",
                    escalate_reason=EscalateReason.BOT_STUCK,
                )
            if not text or text.upper() in (NO_REPLY_TOKEN, REJECT_TOKEN):
                return ChatDraft(
                    kind=ChatActionKind.ESCALATE,
                    reason="llm_false_no_reply",
                    escalate_reason=EscalateReason.BOT_STUCK,
                )
            # успешный retry — ниже уйдём в REPLY
        elif not text or text.upper() == NO_REPLY_TOKEN:
            return ChatDraft(
                kind=ChatActionKind.SKIP,
                reason=f"intent={intent.value};no_reply",
            )

    _ = dry_run
    return ChatDraft(
        kind=ChatActionKind.REPLY,
        text=text,
        reason=f"intent={intent.value};tone={tone}",
        delay_sec=compute_reply_delay(text),
    )


def _salary_reply(thread: ChatThread) -> ChatDraft | None:
    """Фиксированные реплики про ЗП. LLM сюда не пускаем: он сразу берёт вторую."""
    from matcher.test_answers import _is_salary_question

    last = thread.last_inbound_text() or ""
    if not _is_salary_question(last):
        return None

    sent_first = False
    sent_second = False
    for msg in thread.messages:
        if msg.direction != MessageDirection.OUT:
            continue
        body = msg.text or ""
        if (
            "только недавно вышел на рынок" in body
            or "открыт к любым предложениям" in body
        ):
            sent_first = True
        if "рад назвать цифру" in body or "такой в уме" in body:
            sent_second = True

    if sent_second:
        logger.info("chat {}: зарплата, третий заход → эскалация", thread.chat_id)
        return ChatDraft(
            kind=ChatActionKind.ESCALATE,
            reason="salary_third_ask",
            escalate_reason=EscalateReason.OTHER,
        )

    text = _SALARY_SECOND if sent_first else _SALARY_FIRST
    step = "second" if sent_first else "first"
    logger.info("chat {}: зарплата, шаг {}", thread.chat_id, step)
    return ChatDraft(
        kind=ChatActionKind.REPLY,
        text=text,
        reason=f"salary_{step}",
        delay_sec=compute_reply_delay(text),
    )


def _llm_reply(
    thread: ChatThread,
    *,
    resume_text: str,
    vacancy_description: str,
    company_profile: str,
    vacancy_title: str,
    tone: str,
    extra_hint: str = "",
) -> str:
    key = llm_api_key()
    if not key:
        raise RuntimeError("Не задан OPENAI_API_KEY / LLM_API_KEY")

    try:
        from cover_letter.contacts import (
            candidate_email,
            candidate_github,
            candidate_phone,
            candidate_telegram,
        )

        contacts = (
            f"Телефон: {candidate_phone()}\n"
            f"Email: {candidate_email()}\n"
            f"Telegram: {candidate_telegram()}\n"
            f"GitHub: {candidate_github()}"
        )
    except Exception:  # noqa: BLE001
        contacts = "(контакты не заданы)"

    hr = load_hr_answer_context()
    style = _load_style_context()
    history = _format_history(thread)
    system = SYSTEM_HINT_BASE
    if tone == "cold":
        system += "\n" + SYSTEM_HINT_COLD

    desc = (vacancy_description or "").strip()
    if len(desc) > 8000:
        desc = desc[:8000] + "\n…"
    company = (company_profile or thread.company or "").strip()
    if len(company) > 3000:
        company = company[:3000] + "\n…"

    last_in = thread.last_inbound_text() or ""
    user = (
        f"Целевая роль: {target_role()}\n"
        f"Чат: {thread.title or '—'}\n"
        f"Вакансия: {vacancy_title or '—'}\n\n"
        f"=== КОНТАКТЫ ===\n{contacts}\n=== КОНЕЦ ===\n\n"
        f"=== КОМПАНИЯ ===\n{company or '(нет)'}\n=== КОНЕЦ ===\n\n"
        f"=== ВАКАНСИЯ ===\n{desc or '(нет)'}\n=== КОНЕЦ ===\n\n"
        f"=== СТИЛЬ (обязательно) ===\n{style}\n=== КОНЕЦ ===\n\n"
        f"=== КАНОН HR ===\n{hr}\n=== КОНЕЦ ===\n\n"
        f"=== РЕЗЮМЕ ===\n{(resume_text or '')[:12000]}\n=== КОНЕЦ ===\n\n"
        f"=== ПОЛНАЯ ИСТОРИЯ ЧАТА (ОНИ = рекрутер/бот, МЫ = кандидат) ===\n"
        f"{history}\n"
        f"=== КОНЕЦ ИСТОРИИ ===\n\n"
        f"=== ПОСЛЕДНЕЕ ВХОДЯЩЕЕ (на него реакция) ===\n{last_in}\n"
        f"=== КОНЕЦ ===\n\n"
    )
    if extra_hint:
        user += f"⚠ {extra_hint}\n\n"
    user += (
        f"Ответь по сути последнего входящего: покороче и без воды; "
        f"длиннее только если вопрос этого требует. "
        f"Либо {NO_REPLY_TOKEN} / {REJECT_TOKEN}."
    )

    url = f"{llm_base_url()}/chat/completions"
    body = {
        "model": llm_model(),
        "temperature": 0.25,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    logger.info(
        "LLM chat-reply: chat={} model={} history_msgs={}",
        thread.chat_id,
        llm_model(),
        len(thread.messages),
    )
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"]["content"] or "")


def _format_history(thread: ChatThread, *, max_chars: int = 14000) -> str:
    """Вся переписка; при переполнении — начало + хвост."""
    lines: list[str] = []
    for msg in thread.messages:
        who = "МЫ" if msg.direction == MessageDirection.OUT else "ОНИ"
        body = (msg.text or "").strip()
        if len(body) > 1500:
            body = body[:1500] + "…"
        lines.append(f"{who}: {body}")
    if not lines:
        return "(пусто)"
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full
    # хвост важнее для «что уже ответили»
    head = "\n".join(lines[:4])
    tail_budget = max_chars - len(head) - 40
    tail_lines: list[str] = []
    size = 0
    for line in reversed(lines[4:]):
        if size + len(line) + 1 > tail_budget:
            break
        tail_lines.append(line)
        size += len(line) + 1
    tail_lines.reverse()
    return head + "\n…[середина обрезана]…\n" + "\n".join(tail_lines)


def _clean_reply(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    if t.startswith("{") and "text" in t[:80]:
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                action = str(obj.get("action", "")).lower()
                if action in ("reject", "отказ"):
                    return REJECT_TOKEN
                if action in ("skip", "no_reply"):
                    return NO_REPLY_TOKEN
                if obj.get("text"):
                    t = str(obj["text"]).strip()
        except json.JSONDecodeError:
            pass
    t = t.strip().strip('"').strip()
    compact = t.upper().replace(" ", "")
    if compact in ("REJECT", "ОТКАЗ"):
        return REJECT_TOKEN
    if compact in ("NO_REPLY", "NOREPLY"):
        return NO_REPLY_TOKEN
    # LLM любит em/en dash — в исходящих чатах только обычный "-"
    t = t.replace("—", "-").replace("–", "-").replace("−", "-")
    t = t.replace("→", "->").replace("«", '"').replace("»", '"')
    t = _strip_template_closers(t)
    return t


# Последнее предложение-филлер (не трогаем содержательные про remote/формат).
_CTA_MARKERS = (
    "готов обсудить",
    "готовы обсудить",
    "буду рад обсудить",
    "буду рад продолжить",
    "готов рассказать",
    "готов ответить",
    "дайте знать",
    "жду ваших вопросов",
    "жду ваших уточнений",
    "если нужно больше",
    "если нужны подробности",
    "если нужны детали",
    "если нужны примеры",
    "если у вас есть другие вопросы",
    "требуется дополнительная информация",
    "с радостью расскажу",
    "могу подробнее",
    "готов обсудить детали",
)


def _strip_template_closers(text: str) -> str:
    """Убрать шаблонный финальный CTA, оставив содержательную часть."""
    t = (text or "").strip()
    if not t:
        return t
    # режем по предложениям; хвост убираем, пока он филлер
    while True:
        parts = re.split(r"(?<=[.!?…])\s+", t)
        if len(parts) < 2:
            break
        last = parts[-1].strip()
        low = last.lower().replace("ё", "е")
        if not any(m in low for m in _CTA_MARKERS):
            break
        t = " ".join(parts[:-1]).strip()
    return t
