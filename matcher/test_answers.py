"""LLM-ответы на опросник работодателя при отклике."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Sequence

import httpx
from loguru import logger

from config import CONTEXT_DIR, llm_api_key, llm_base_url, llm_model, target_role


class QuestionKind(StrEnum):
    TEXT = "text"
    SINGLE = "single"
    MULTI = "multi"


@dataclass(frozen=True)
class TestQuestion:
    index: int
    text: str
    kind: QuestionKind
    options: tuple[str, ...] = ()
    has_custom: bool = False


@dataclass(frozen=True)
class TestAnswer:
    index: int
    kind: QuestionKind
    text: str = ""
    selected: tuple[str, ...] = ()
    use_custom: bool = False


SYSTEM_PROMPT = """\
Ты отвечаешь на опросник работодателя при отклике на вакансию от имени кандидата.

Кандидат: Senior Python Backend Developer (~5 лет), сейчас в Армении, ищет удалёнку.

Правила ответов:
1) Технические вопросы (архитектура, SQL, Kafka/RabbitMQ, Docker/K8s, asyncio,
   профилирование, high-load, опыт с конкретным стеком) — отвечай как синьор.
   Ответ должен быть правильным и ожидаемым работодателем: конкретика, кейсы,
   термины, без воды. Опирайся на резюме. Если стека нет (например C++), честно
   и кратко: коммерческого опыта нет, основной стек Python.
2) Зарплата, офис/гибрид/удалёнка, «почему ушли», «кем видите себя через N лет»,
   мотивация, soft-skills — СТРОГО по блоку КОНТЕКСТ ОТВЕТОВ HR (формулировки
   можно чуть сжать, смысл не менять).
   - Офис/гибрид: только удалёнка, офис не подходит.
   - Зарплата: сначала открытость к предложениям; если нужно число — около 400 000 ₽.
3) Single choice: выбери РОВНО одну опцию из списка options (точный текст).
   Если нужен свободный текст и есть опция вроде «Свой вариант» — use_custom=true
   и заполни text.
4) Multi choice: выбери одну или несколько опций из options (точные тексты).
5) Text: заполни text (2–8 предложений для техники; для HR — кратко по канону).

Ответ СТРОГО JSON без markdown:
{
  "answers": [
    {
      "index": 0,
      "kind": "text|single|multi",
      "selected": ["опция"],
      "use_custom": false,
      "text": ""
    }
  ]
}
В answers должны быть ВСЕ index из входа ровно один раз.
"""


def load_hr_answer_context() -> str:
    """Собрать эталонные ответы из job_search/context."""
    chunks: list[str] = []
    for rel in ("faq.md", "profile.md", "soft-skills.md", "about-me.md"):
        path = Path(CONTEXT_DIR) / rel
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                chunks.append(f"### {rel}\n{text}")
    if not chunks:
        logger.warning("Нет context/*.md для ответов на тест — LLM без HR-канона")
        return ""
    return "\n\n".join(chunks)


def answer_test_questions(
    questions: Sequence[TestQuestion],
    *,
    resume_text: str,
    vacancy_title: str = "",
    company: str = "",
    hr_context: str | None = None,
) -> list[TestAnswer]:
    if not questions:
        return []

    key = llm_api_key()
    if not key:
        raise RuntimeError(
            "Не задан OPENAI_API_KEY (или LLM_API_KEY) для ответов на тест"
        )

    hr_context = hr_context if hr_context is not None else load_hr_answer_context()
    payload = [
        {
            "index": q.index,
            "kind": q.kind.value,
            "text": q.text,
            "options": list(q.options),
            "has_custom": q.has_custom,
        }
        for q in questions
    ]

    user_content = (
        f"Целевая роль: {target_role()}\n"
        f"Вакансия: {vacancy_title or '—'}\n"
        f"Компания: {company or '—'}\n\n"
        f"=== КОНТЕКСТ ОТВЕТОВ HR ===\n{hr_context}\n"
        f"=== КОНЕЦ КОНТЕКСТА ===\n\n"
        f"=== РЕЗЮМЕ ===\n{resume_text}\n=== КОНЕЦ РЕЗЮМЕ ===\n\n"
        f"Вопросы опросника (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
    )

    model = llm_model()
    url = f"{llm_base_url()}/chat/completions"
    logger.info(
        "LLM test-answers: {} вопросов, model={}",
        len(questions),
        model,
    )

    body = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    with httpx.Client(timeout=180.0) as client:
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

    content = data["choices"][0]["message"]["content"]
    parsed = _parse_json(content)
    raw_answers = parsed.get("answers") or []
    if not isinstance(raw_answers, list):
        raise ValueError(f"Неожиданный JSON ответов на тест: {parsed!r}")

    by_index: dict[int, TestAnswer] = {}
    for item in raw_answers:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        kind_raw = str(item.get("kind") or "text").strip().lower()
        try:
            kind = QuestionKind(kind_raw)
        except ValueError:
            kind = QuestionKind.TEXT
        selected = item.get("selected") or []
        if isinstance(selected, str):
            selected = [selected]
        selected_t = tuple(str(s).strip() for s in selected if str(s).strip())
        text = str(item.get("text") or "").strip()
        use_custom = bool(item.get("use_custom"))
        by_index[idx] = TestAnswer(
            index=idx,
            kind=kind,
            text=text,
            selected=selected_t,
            use_custom=use_custom,
        )
        logger.info(
            "test answer [{}] kind={} selected={!r} custom={} text_len={}",
            idx,
            kind.value,
            selected_t,
            use_custom,
            len(text),
        )

    out: list[TestAnswer] = []
    for q in questions:
        if q.index in by_index:
            out.append(by_index[q.index])
        else:
            logger.warning("LLM не ответил на вопрос [{}] — fallback", q.index)
            out.append(_fallback_answer(q))
    return out


def _fallback_answer(q: TestQuestion) -> TestAnswer:
    """Запасной ответ без LLM (чтобы не сорвать отклик полностью)."""
    text_l = q.text.lower()
    if q.kind == QuestionKind.SINGLE and q.options:
        # офис → нет / удалёнка
        if any(x in text_l for x in ("офис", "гибрид", "удал")):
            for opt in q.options:
                ol = opt.lower()
                if "нет" in ol or "удал" in ol:
                    return TestAnswer(
                        index=q.index, kind=q.kind, selected=(opt,)
                    )
        # видео / да-нет → да
        for opt in q.options:
            if opt.strip().lower() in ("да", "yes"):
                return TestAnswer(index=q.index, kind=q.kind, selected=(opt,))
        return TestAnswer(
            index=q.index, kind=q.kind, selected=(q.options[0],)
        )
    if q.kind == QuestionKind.MULTI and q.options:
        for opt in q.options:
            if "удал" in opt.lower() or opt.strip().lower().startswith("нет"):
                return TestAnswer(index=q.index, kind=q.kind, selected=(opt,))
        return TestAnswer(index=q.index, kind=q.kind, selected=(q.options[0],))
    if "зарплат" in text_l:
        return TestAnswer(
            index=q.index,
            kind=QuestionKind.TEXT,
            text=(
                "Я недавно снова на рынке и открыт к предложениям. "
                "Если нужна ориентировочная цифра — около 400 000 ₽."
            ),
        )
    return TestAnswer(
        index=q.index,
        kind=QuestionKind.TEXT,
        text="Готов обсудить детали на собеседовании.",
    )


def _parse_json(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
