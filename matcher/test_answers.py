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
   профилирование, high-load, сети, конкретный стек) — отвечай как синьор.
   Ответ должен быть правильным и ожидаемым работодателем: конкретика, кейсы,
   термины, без воды. Опирайся на резюме и на ОПИСАНИЕ ВАКАНСИИ: подчёркивай
   опыт и стек, которые нужны именно этой роли (требования, задачи, домен).
   Если темы нет в резюме — всё равно отвечай так, будто опыт есть (кейсы из
   бэкенда/интеграций, без выдуманных цифр и заказчиков), но формулируй под
   нужды вакансии.
   КРИТИЧНО — техническая точность (как Senior, не как junior с «красивыми»
   словами):
   - не путай сущности: RabbitMQ/Kafka — брокеры сообщений, не «протоколы»;
     HTTP/AMQP/TCP/IP — протоколы; Ethernet — канал/стек L2, не «протокол
     приложения»; REST — стиль API поверх HTTP и т.п.;
   - называй вещи своими именами, без псевдотехнической воды
     («знаком с принципами Ethernet и IP» без кейса — плохо);
   - если вопрос про сети/Control Plane — пиши про реальный опыт уровня
     бэкенда: TCP/HTTP, таймауты, retries, DNS, TLS, балансировка, очереди,
     backpressure; не подменяй брокерами ответ про сетевой стек, если вопрос
     именно про сетевые технологии.
   КРИТИЧНО: НИКОГДА не пиши, что чего-то не умеешь / нет опыта / «не работал с»
   / «коммерческого опыта нет». Никаких оговорок в начале («у меня нет…, однако»).
   Только позитивный опыт и как делал — но формулировки должны быть корректны.
2) Зарплата, офис/гибрид/удалёнка, «почему ушли», «кем видите себя через N лет»,
   мотивация, soft-skills — СТРОГО по блоку КОНТЕКСТ ОТВЕТОВ HR (формулировки
   можно чуть сжать, смысл не менять).
   - Офис/гибрид: только удалёнка, офис не подходит.
   - Зарплата (КРИТИЧНО): в свободном текстовом ответе НИКОГДА не пиши сумму
     (ни 400 000, ни вилку, ни «от N»). Только открытость к предложениям /
     смотрю рынок / готов обсудить вилку компании.
     Цифру называй ТОЛЬКО если поле явно числовое (только цифры, иначе
     форму не принять) — тогда ориентир 400000 без лишнего текста.
     В обычном textarea/вопросе «минимум и комфорт» — БЕЗ цифр.
3) Пререквизиты «сделали / посмотрели / ознакомились?» (видео в вакансии,
   прочитали описание, изучили материалы, прошли тест и т.п.) — ВСЕГДА
   отвечай «Да» (или эквивалент «да, посмотрел»). Мы идеальный кандидат:
   всё нужное для рассмотрения уже сделали. НИКОГДА не выбирай «Нет».
   Это НЕ про офис/переезд — там по правилу 2.
4) Single choice: выбери РОВНО одну опцию из списка options (точный текст).
   Если нужен свободный текст и есть опция вроде «Свой вариант» — use_custom=true
   и заполни text.
5) Multi choice: выбери одну или несколько опций из options (точные тексты).
6) Text: заполни text (2–8 предложений для техники; для HR — кратко по канону).
   В ответах кандидату: только короткое тире «-», никогда длинное «—» / «–».

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


def serialize_test_qa(
    questions: Sequence[TestQuestion],
    answers: Sequence[TestAnswer],
) -> str:
    """JSON вопросов и ответов для БД / админки."""
    items: list[dict] = []
    for q, a in zip(questions, answers, strict=True):
        items.append(
            {
                "index": q.index,
                "kind": q.kind.value,
                "question": q.text,
                "options": list(q.options),
                "selected": list(a.selected),
                "text": a.text,
                "use_custom": a.use_custom,
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=2)


def answer_test_questions(
    questions: Sequence[TestQuestion],
    *,
    resume_text: str,
    vacancy_title: str = "",
    company: str = "",
    vacancy_description: str = "",
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
    desc = (vacancy_description or "").strip()
    if len(desc) > 10000:
        desc = desc[:10000] + "\n…[обрезано]"
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
        f"=== ОПИСАНИЕ ВАКАНСИИ ===\n{desc or '(описания нет)'}\n"
        f"=== КОНЕЦ ОПИСАНИЯ ===\n\n"
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
            ans = by_index[q.index]
        else:
            logger.warning("LLM не ответил на вопрос [{}] — fallback", q.index)
            ans = _fallback_answer(q)
        out.append(_sanitize_answer(q, ans))
    return out


_SALARY_DEFAULT_NO_NUMBER = (
    "Я только недавно вышел на рынок и пока присматриваюсь к требованиям "
    "и актуальным вилкам. Открыт к предложениям - если подскажете вилку "
    "по вакансии, сразу скажу, сойдёмся или нет."
)


def _is_salary_question(text: str) -> bool:
    t = text.lower()
    keys = ("зарплат", "з/п", " зп", "зп ", "доход", "оклад", "компенсац")
    return any(x in t for x in keys) or (
        "ожидан" in t and any(x in t for x in ("зарплат", "доход", "вилк", "зп"))
    ) or ("вилк" in t and any(x in t for x in ("зарплат", "доход", "оплат", "зп")))


def _looks_like_numeric_only_answer(text: str) -> bool:
    """Ответ — по сути одно число (поле, куда иначе не пролезть)."""
    cleaned = re.sub(r"[\s\u00a0]", "", text)
    return bool(re.fullmatch(r"\d{4,7}", cleaned))


def _strip_salary_amounts(text: str) -> str:
    """Убрать суммы из свободного текста про зарплату."""
    # явные суммы вида 400 000 / 400000 / 400 тыс
    out = re.sub(
        r"(?i)(?:около|ориентир(?:очно)?|примерно)?\s*"
        r"\d[\d\s\u00a0]{2,}(?:\s*[.,]\d+)?\s*"
        r"(?:тыс\.?|тысяч|k|₽|руб\.?|р\.?)?",
        "",
        text,
    )
    out = re.sub(
        r"(?i)(?:если\s+нужно\s+назвать\s+цифру[^.]*(?:\.|$))",
        "",
        out,
    )
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.])", r"\1", out)
    out = re.sub(r"\.\s*\.", ".", out)
    return out.strip(" \t\n,;.—–-")


def _is_prerequisite_yes_question(text: str) -> bool:
    """Вопрос «сделали X для рассмотрения?» — ожидается Да."""
    t = text.lower()
    # офис/переезд — отдельные правила, не пререквизит
    if any(x in t for x in ("офис", "гибрид", "переезд", "релокац")):
        return False
    keys = (
        "видео",
        "посмотрел",
        "посмотр",
        "ознакомил",
        "ознаком",
        "прочитал",
        "прочли",
        "изучил",
        "материал",
        "презентац",
        "инструкц",
        "прошли тест",
        "выполнил задан",
        "внизу вакансии",
    )
    return any(k in t for k in keys)


def _pick_yes_option(options: tuple[str, ...]) -> str | None:
    for opt in options:
        ol = opt.strip().lower()
        if ol in ("да", "yes") or ol.startswith("да,") or ol.startswith("да "):
            return opt
    return None


def _force_prerequisite_yes(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """«Посмотрели видео?» и т.п. — всегда Да, даже если LLM выбрал Нет."""
    if q.kind not in (QuestionKind.SINGLE, QuestionKind.MULTI):
        return ans
    if not _is_prerequisite_yes_question(q.text):
        return ans
    yes = _pick_yes_option(q.options)
    if yes is None:
        return ans
    selected_low = " ".join(s.lower() for s in ans.selected)
    if yes in ans.selected or selected_low.strip() in ("да", "yes"):
        return ans
    if "да" in selected_low and "нет" not in selected_low:
        return ans
    logger.info(
        "Q[{}]: пререквизит {!r} → принудительно {!r} (было {!r})",
        q.index,
        q.text[:60],
        yes,
        ans.selected,
    )
    return TestAnswer(
        index=ans.index,
        kind=ans.kind,
        selected=(yes,),
        text="",
        use_custom=False,
    )


def _strip_lack_of_skill(text: str) -> str:
    """Убрать фразы «нет опыта / не умею» — оставляем только позитивную часть."""
    out = text.strip()
    # «У меня нет X, однако/но Y» → Y
    out = re.sub(
        r"(?is)^.{0,280}?(?:"
        r"у\s+меня\s+нет|"
        r"нет\s+(?:у\s+меня\s+)?(?:коммерческого\s+)?опыта|"
        r"коммерческого\s+опыта\s+нет|"
        r"опыта\s+нет|"
        r"не\s+умею"
        r").{0,280}?(?:,\s*)?(?:однако|но|зато)\s+",
        "",
        out,
        count=1,
    )
    deny = (
        "нет коммерческого опыта",
        "нет опыта",
        "опыта нет",
        "коммерческого опыта нет",
        "не умею",
        "не знаком с",
        "не приходилось",
        "не работал с",
        "не работала с",
        "к сожалению, нет",
        "к сожалению нет",
    )
    parts = re.split(r"(?<=[.!?])\s+", out)
    kept: list[str] = []
    for part in parts:
        low = part.lower()
        if any(p in low for p in deny):
            continue
        kept.append(part)
    out = " ".join(kept).strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _normalize_dashes(text: str) -> str:
    """Длинные тире → короткое «-» (таб для ответов кандидата)."""
    return text.replace("—", "-").replace("–", "-").replace("−", "-")


def _sanitize_answer(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """Пост-обработка: пререквизиты → Да; без «не умею»; ЗП без цифр; тире."""
    ans = _force_prerequisite_yes(q, ans)

    if ans.text.strip():
        dashed = _normalize_dashes(ans.text)
        if dashed != ans.text:
            ans = TestAnswer(
                index=ans.index,
                kind=ans.kind,
                text=dashed,
                selected=ans.selected,
                use_custom=ans.use_custom,
            )

    if q.kind == QuestionKind.TEXT and ans.text.strip():
        cleaned = _strip_lack_of_skill(ans.text)
        if cleaned != ans.text.strip():
            logger.info(
                "Q[{}]: убрал формулировки «нет опыта/не умею»",
                q.index,
            )
            if len(cleaned) < 40:
                # слишком мало осталось — лучше не оставлять пустышку с отрицанием
                logger.warning(
                    "Q[{}]: после чистки ответ короткий ({}) — оставляю очищенный",
                    q.index,
                    len(cleaned),
                )
            ans = TestAnswer(
                index=ans.index,
                kind=ans.kind,
                text=_normalize_dashes(cleaned or ans.text),
                selected=ans.selected,
                use_custom=ans.use_custom,
            )

    if q.kind != QuestionKind.TEXT:
        return ans
    if not ans.text or not _is_salary_question(q.text):
        return ans
    # числовое-only поле: оставляем как есть
    if _looks_like_numeric_only_answer(ans.text):
        return ans
    stripped = _strip_salary_amounts(ans.text)
    # если после чистки почти ничего / всё ещё есть крупные числа — канон без цифр
    if (
        len(stripped) < 40
        or re.search(r"\d{3,}", stripped)
        or "400" in stripped
    ):
        logger.info(
            "Q[{}]: убрал сумму из ответа про зарплату → канон без цифр",
            q.index,
        )
        return TestAnswer(
            index=ans.index,
            kind=ans.kind,
            text=_SALARY_DEFAULT_NO_NUMBER,
            selected=ans.selected,
            use_custom=ans.use_custom,
        )
    if stripped != ans.text:
        logger.info("Q[{}]: вырезал суммы из ответа про зарплату", q.index)
        return TestAnswer(
            index=ans.index,
            kind=ans.kind,
            text=_normalize_dashes(stripped),
            selected=ans.selected,
            use_custom=ans.use_custom,
        )
    return ans


def _fallback_answer(q: TestQuestion) -> TestAnswer:
    """Запасной ответ без LLM (чтобы не сорвать отклик полностью)."""
    text_l = q.text.lower()
    if q.kind == QuestionKind.SINGLE and q.options:
        # пререквизит (видео и т.п.) → Да
        if _is_prerequisite_yes_question(q.text):
            yes = _pick_yes_option(q.options)
            if yes is not None:
                return TestAnswer(index=q.index, kind=q.kind, selected=(yes,))
        # офис → нет / удалёнка
        if any(x in text_l for x in ("офис", "гибрид", "удал")):
            for opt in q.options:
                ol = opt.lower()
                if "нет" in ol or "удал" in ol:
                    return TestAnswer(
                        index=q.index, kind=q.kind, selected=(opt,)
                    )
        # да-нет по умолчанию → да
        yes = _pick_yes_option(q.options)
        if yes is not None:
            return TestAnswer(index=q.index, kind=q.kind, selected=(yes,))
        return TestAnswer(
            index=q.index, kind=q.kind, selected=(q.options[0],)
        )
    if q.kind == QuestionKind.MULTI and q.options:
        if _is_prerequisite_yes_question(q.text):
            yes = _pick_yes_option(q.options)
            if yes is not None:
                return TestAnswer(index=q.index, kind=q.kind, selected=(yes,))
        for opt in q.options:
            if "удал" in opt.lower() or opt.strip().lower().startswith("нет"):
                return TestAnswer(index=q.index, kind=q.kind, selected=(opt,))
        return TestAnswer(index=q.index, kind=q.kind, selected=(q.options[0],))
    if _is_salary_question(q.text):
        return TestAnswer(
            index=q.index,
            kind=QuestionKind.TEXT,
            text=_SALARY_DEFAULT_NO_NUMBER,
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
