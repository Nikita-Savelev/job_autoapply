"""Классификация входящего и триггеры эскалации в Telegram."""

from __future__ import annotations

import re
from enum import StrEnum

from chat.models import ChatActionKind, ChatDraft, ChatThread, EscalateReason


class ChatIntent(StrEnum):
    QUESTION = "question"
    COLD_OFFER = "cold_offer"
    INTERVIEW = "interview"
    EXTERNAL_TASK = "external_task"
    REJECT = "reject"
    NO_REPLY = "no_reply"  # финал скрининга / «спасибо, ждите» — не отвечаем
    UNKNOWN = "unknown"


_URL_RE = re.compile(r"https?://\S+", re.I)


def is_hh_reject_badge(text: str | None) -> bool:
    """Статус «Отказ» в списке чатов (last-message под вакансией)."""
    t = " ".join((text or "").split()).strip().lower()
    return t in ("отказ", "отказ.", "отказ!")


def is_hh_interview_badge(text: str | None) -> bool:
    """Статус «Собеседование» / «Приглашение» в списке чатов."""
    t = " ".join((text or "").split()).strip().lower()
    if not t:
        return False
    if t in (
        "собеседование",
        "собеседование.",
        "приглашение",
        "приглашение.",
        "пригласили",
    ):
        return True
    # «Приглашение на собеседование» и т.п.
    return ("собеседован" in t or "приглашен" in t) and len(t) < 80


def looks_like_reject(text: str) -> bool:
    """Входящее — отказ работодателя (не требует ответа и внимания)."""
    if is_hh_reject_badge(text):
        return True
    t = (text or "").lower()
    if not t:
        return False
    markers = (
        "отказ",
        "отказываем",
        "отказали",
        "отказать",
        "не готовы продолж",
        "не готовы рассматривать",
        "не готовы рассмотреть",
        "не готовы пригласить",
        "не готовы направить",
        "не готовы принять",
        "выбрали другого",
        "выбрали иную кандидатуру",
        "выбрали другого кандидата",
        "к сожалению, ваша кандидатура",
        "ваша кандидатура нам не",
        "кандидатура не подходит",
        "нам не подойдёт",
        "нам не подойдет",
        "не будем продолжать",
        "вынуждены отказать",
        "кандидат не подходит",
        "остановили поиск",
        "вакансия закрыта",
        "нашли другого",
        "продолжим с другим",
        "продолжим с другими",
        "решили продолжить с друг",
        "не прошли на следующий",
        "не прошли дальше",
        "на этом этапе поиск завершён",
        "на этом этапе поиск завершен",
        "поиск по вакансии завершён",
        "поиск по вакансии завершен",
        "больше не рассматриваем",
        "не рассматриваем вашу кандидатуру",
        "не готовы рассматривать вашу кандидатуру",
        "к сожалению, мы не можем продолжить",
        "к сожалению мы не можем продолжить",
    )
    return any(x in t for x in markers)


def classify_thread(thread: ChatThread) -> ChatIntent:
    if is_hh_reject_badge(thread.last_message_preview):
        return ChatIntent.REJECT
    if is_hh_interview_badge(thread.last_message_preview):
        return ChatIntent.INTERVIEW

    text = (thread.last_inbound_text() or "").lower()
    if not text:
        return ChatIntent.UNKNOWN

    if looks_like_reject(text):
        return ChatIntent.REJECT

    # закрытие опросника роботом — ответа не ждут
    if _looks_like_no_reply_needed(text):
        return ChatIntent.NO_REPLY

    if is_hh_interview_badge(text) or any(
        x in text
        for x in (
            "приглашаем на собеседован",
            "приглашаю на собеседован",
            "приглашение на собеседован",
            "записать на собеседован",
            "выбрать время",
            "выберите удобный слот",
            "календар",
            "calendly",
            "назначаем созвон",
        )
    ):
        return ChatIntent.INTERVIEW

    if any(
        x in text
        for x in (
            "телеграм",
            "telegram",
            "whatsapp",
            "вотсап",
            "напишите в ",
            "продолжить обсуждение в",
        )
    ) and any(x in text for x in ("@", "http", "удобно", "ссылк", "перейд")):
        return ChatIntent.EXTERNAL_TASK

    if _looks_like_external_task(text):
        return ChatIntent.EXTERNAL_TASK

    if any(
        x in text
        for x in (
            "рассмотреть ваканси",
            "предлагаем ваканси",
            "есть вакансия",
            "открыта позиция",
            "подошла бы вакансия",
        )
    ):
        return ChatIntent.COLD_OFFER

    if "?" in text or any(
        x in text
        for x in (
            "расскажите",
            "подскажите",
            "какой у вас",
            "какие у вас",
            "укажите",
            "подтвердите",
            "когда можете",
            "готовой ли",
            "готовы ли",
            "интересует ли",
            "есть ли у вас",
            "хотите откликн",
            "хотите ли",
        )
    ):
        return ChatIntent.QUESTION

    return ChatIntent.UNKNOWN


def looks_like_needs_reply(text: str) -> bool:
    """Входящее явно ждёт ответа (вопрос / просьба рассказать)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if _looks_like_no_reply_needed(t):
        return False
    if looks_like_reject(t):
        return False
    if "?" in t:
        return True
    return any(
        x in t
        for x in (
            "расскажите",
            "подскажите",
            "укажите",
            "подтвердите",
            "какой у вас",
            "какие у вас",
            "есть ли у вас",
            "готовой ли",
            "готовы ли",
            "интересует ли",
            "хотите откликн",
            "хотите ли",
            "когда можете",
        )
    )


def _looks_like_no_reply_needed(text: str) -> bool:
    """Последнее входящее — финал / ожидание, а не вопрос к нам."""
    markers = (
        "ваши ответы отправлены",
        "ответы отправлены работодателю",
        "ответ отправлен работодателю",
        "передадим работодателю",
        "если ваш отклик его заинтересует",
        "если отклик заинтересует",
        "он напишет в этом же чате",
        "напишет в этом же чате или позвонит",
        "ожидайте сообщения от работодателя",
        "ожидайте ответа работодателя",
        "спасибо за ответы",
        "спасибо за ответ",
        "анкета заполнена",
        "опрос завершён",
        "опрос завершен",
    )
    if any(x in text for x in markers):
        return True
    # короткое «спасибо» без вопроса
    compact = " ".join(text.split())
    if compact in ("спасибо!", "спасибо.", "спасибо") and "?" not in text:
        return True
    return False


def _looks_like_external_task(text: str) -> bool:
    has_url = bool(_URL_RE.search(text))
    task_words = any(
        x in text
        for x in (
            "тестов",
            "тестовое",
            "задан",
            "forms.gle",
            "forms.yandex",
            "docs.google",
            "пройти по ссылке",
            "заполните форму",
            "ответьте",
            "по указанной ссылке",
            "анкет",
            "hackerrank",
            "codility",
            "я.диск",
            "google диск",
        )
    )
    if task_words and (has_url or "ссылк" in text):
        return True
    if has_url and any(
        x in text for x in ("перейд", "пройд", "выполн", "пришлите", "ответьте", "заполн")
    ):
        return True
    return False


def intent_to_action(intent: ChatIntent) -> tuple[ChatActionKind, EscalateReason | None]:
    if intent in (ChatIntent.REJECT, ChatIntent.NO_REPLY):
        return ChatActionKind.SKIP, None
    if intent in (
        ChatIntent.QUESTION,
        ChatIntent.COLD_OFFER,
        ChatIntent.UNKNOWN,
    ):
        return ChatActionKind.REPLY, None
    if intent == ChatIntent.INTERVIEW:
        return ChatActionKind.ESCALATE, EscalateReason.INTERVIEW
    if intent == ChatIntent.EXTERNAL_TASK:
        return ChatActionKind.ESCALATE, EscalateReason.EXTERNAL_TASK
    return ChatActionKind.ESCALATE, EscalateReason.OTHER


def stub_policy_draft(thread: ChatThread) -> ChatDraft:
    intent = classify_thread(thread)
    kind, esc = intent_to_action(intent)
    return ChatDraft(
        kind=kind,
        text="",
        reason=f"intent={intent.value}",
        escalate_reason=esc,
    )
