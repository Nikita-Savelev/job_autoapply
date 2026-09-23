"""LLM: один запрос — решения по всем названиям вакансий страницы."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

import httpx
from loguru import logger

from config import llm_api_key, llm_base_url, llm_model, target_role
from db.models import Vacancy


@dataclass(frozen=True)
class MatchDecision:
    accepted: bool
    score: float
    reason: str


SYSTEM_PROMPT = """\
Ты — ассистент по отбору вакансий для кандидата.
Тебе даны: текст резюме кандидата, целевая роль и список вакансий (id + название + компания).

Задача: для КАЖДОЙ вакансии решить, стоит ли откликаться, судя по названию
(и компании, если есть) относительно резюме и целевой роли.

Правила:
- Подходят роли близкие к Python / Backend / Django / FastAPI / микросервисы /
  интеграции / боты на Python, Senior Python и т.п.
- НЕ подходят: другой основной стек (C#, Java, Go, PHP, frontend-only, 1C, iOS/Android),
  чистый Team Lead / Tech Lead / руководитель без сильного hands-on backend,
  аналитик / DevOps-only / data scientist без backend, C++/CV hardware без Python,
  нерелевантные домены вроде «инженер-расчётчик» без IT-backend.
- ЖЁСТКО НЕ подходят любые роли тестирования, даже если в названии есть Python:
  QA, AQA, SDET, тестировщик, тестировщик-автоматизатор, test automation,
  инженер по автоматизации тестирования, Automation QA, Fullstack QA.
  Кандидат не тестировщик. suitable=false, даже если стек Python.
  «Автоматизация» без слова про тесты (боты, n8n, процессы) — это не QA, можно да.
- Fullstack на Python (разработка) — скорее да, даже если рядом Vue, React или Next.
  Fullstack с упором на React/Vue без Python — нет.
- «Senior», «Middle», «Старший», «Ведущий» в названии разработчика — это грейд, не руководство.
  Senior Python, Старший Python-разработчик, Ведущий фуллстек (Python),
  Fullstack с Python в названии — suitable=true.
  «Не hands-on» ставь только если в названии прямо Team Lead, Tech Lead, тимлид,
  руководитель, директор или Head.
- Если сомневаешься по одному названию — suitable=false и короткая reason.

Ответ СТРОГО одним JSON-объектом без markdown:
{
  "results": [
    {"hh_id": "...", "suitable": true, "score": 0.0, "reason": "кратко"}
  ]
}
score от 0 до 1. В results должны быть ВСЕ переданные hh_id ровно один раз.
"""


_MAX_MATCH_ATTEMPTS = 3


def match_vacancies_batch(
    vacancies: Sequence[Vacancy],
    resume_text: str,
    *,
    role: str | None = None,
) -> dict[str, MatchDecision]:
    """Решения по списку. Если модель пропустила id, запрос повторяется только на них."""
    if not vacancies:
        return {}

    key = llm_api_key()
    if not key:
        raise RuntimeError(
            "Не задан OPENAI_API_KEY (или LLM_API_KEY) в hh_autoapply/.env"
        )

    role = role or target_role()
    pending = list(vacancies)
    out: dict[str, MatchDecision] = {}
    for attempt in range(1, _MAX_MATCH_ATTEMPTS + 1):
        if not pending:
            break
        if attempt > 1:
            logger.warning(
                "LLM повтор {} на {} вакансий без ответа",
                attempt,
                len(pending),
            )
        out.update(
            _match_once(pending, resume_text, role=role, api_key=key)
        )
        pending = [v for v in pending if v.hh_id not in out]

    if pending:
        logger.warning(
            "LLM не вернул решения для {} id — оставляю без решения",
            [v.hh_id for v in pending],
        )

    logger.info(
        "LLM итог: suitable={} reject={} нет ответа={}",
        sum(1 for d in out.values() if d.accepted),
        sum(1 for d in out.values() if not d.accepted),
        len(pending),
    )
    return out


def _match_once(
    vacancies: Sequence[Vacancy],
    resume_text: str,
    *,
    role: str,
    api_key: str,
) -> dict[str, MatchDecision]:
    """Один запрос к LLM. В ответе только те id, которые модель вернула."""
    payload_vacancies = [
        {
            "hh_id": v.hh_id,
            "title": v.title,
            "company": v.company or "",
        }
        for v in vacancies
    ]

    user_content = (
        f"Целевая роль: {role}\n\n"
        f"=== РЕЗЮМЕ ===\n{resume_text}\n=== КОНЕЦ РЕЗЮМЕ ===\n\n"
        f"Вакансии (JSON):\n{json.dumps(payload_vacancies, ensure_ascii=False)}"
    )

    model = llm_model()
    url = f"{llm_base_url()}/chat/completions"
    logger.info(
        "LLM batch-match: {} вакансий, model={}, url={}",
        len(vacancies),
        model,
        url,
    )
    logger.debug(
        "LLM titles: {}",
        [(v.hh_id, v.title) for v in vacancies],
    )

    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        logger.debug("LLM HTTP status={}", resp.status_code)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    logger.debug("LLM raw content (trunc): {}", content[:2000])
    parsed = _parse_json_content(content)
    results = parsed.get("results") or parsed.get("vacancies") or []
    if not isinstance(results, list):
        raise ValueError(f"Неожиданный JSON от LLM: {parsed!r}")

    out: dict[str, MatchDecision] = {}
    for item in results:
        hh_id = str(item.get("hh_id", "")).strip()
        if not hh_id:
            continue
        suitable = bool(item.get("suitable"))
        try:
            score = float(item.get("score", 1.0 if suitable else 0.0))
        except (TypeError, ValueError):
            score = 1.0 if suitable else 0.0
        reason = str(item.get("reason") or ("suitable" if suitable else "rejected"))
        title = next((v.title for v in vacancies if v.hh_id == hh_id), "")
        if suitable and _is_qa_title(title):
            suitable = False
            score = 0.0
            reason = "QA/тестирование — не целевая роль"
            logger.info(
                "LLM override {} {!r}: suitable=False (QA-фильтр)",
                hh_id,
                title,
            )
        elif not suitable and _is_hands_on_python_title(title):
            suitable = True
            score = max(score, 0.75)
            reason = "Senior/ведущий Python — hands-on разработка"
            logger.info(
                "LLM override {} {!r}: suitable=True (грейд, не руководство)",
                hh_id,
                title,
            )
        out[hh_id] = MatchDecision(accepted=suitable, score=score, reason=reason)
        logger.info(
            "LLM decision {} {!r}: suitable={} score={} reason={}",
            hh_id,
            title,
            suitable,
            score,
            reason,
        )

    return out


_QA_TITLE_RE = re.compile(
    r"("
    r"тестиров|тестирован|"
    r"\bsdet\b|\baqa\b|\bqa\b|"
    r"test\s*automation|automation\s*qa|quality\s*assurance"
    r")",
    re.IGNORECASE,
)


def _is_qa_title(title: str) -> bool:
    """Название про тестирование, а не про разработку/автоматизацию процессов."""
    return bool(_QA_TITLE_RE.search(title or ""))


_LEAD_OR_JUNIOR_RE = re.compile(
    r"("
    r"team\s*lead|tech\s*lead|тимлид|руководитель|директор|\bhead\b|"
    r"junior|стажер|стажёр|младш|преподаватель|педагог"
    r")",
    re.IGNORECASE,
)

# Senior / ведущий / fullstack с Python — грейд разработчика, не отказ «не hands-on».
_HANDS_ON_PY_RE = re.compile(
    r"("
    r"(senior|middle|\bведущ\w*|\bстарш\w*).{0,90}(python|django|fastapi)"
    r"|"
    r"(python|django|fastapi).{0,90}(senior|middle|\bведущ\w*|\bстарш\w*)"
    r"|"
    r"(fullstack|фуллстек|full-stack|full\s*stack).{0,90}(python|django|fastapi)"
    r"|"
    r"(python|django|fastapi).{0,90}(fullstack|фуллстек|full-stack|full\s*stack)"
    r")",
    re.IGNORECASE,
)


def _is_hands_on_python_title(title: str) -> bool:
    """Python-разработчик уровня Senior/ведущий/fullstack, без роли руководителя и QA."""
    text = title or ""
    if _is_qa_title(text) or _LEAD_OR_JUNIOR_RE.search(text):
        return False
    return bool(_HANDS_ON_PY_RE.search(text))


def _parse_json_content(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
