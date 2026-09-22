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
Основной профиль - backend (Python, FastAPI/Django, Postgres, очереди, Docker),
не чистый AQA/SDET/мобильное QA и не C++/desktop-специалист.

СТИЛЬ (критично - меньше воды):
- Пиши плотно: факты, стек, действие, результат. Без вводных («хотел бы отметить»,
  «в своей практике я активно использовал», «что позволило обеспечить»).
- Техника: обычно 2-4 коротких предложения ИЛИ маркированный список из 2-4 пунктов.
  Не раздувай до эссе. HR-ответы - 1-3 предложения по канону.
- Не повторяй вопрос своими словами. Не пиши общие фразы без кейса.

Правила ответов:
1) Технические вопросы - как синьор: конкретика и термины, без воды.
   Опирайся на резюме и ОПИСАНИЕ ВАКАНСИИ. Если темы нет в резюме - отвечай так,
   будто опыт есть (кейсы из бэкенда/интеграций, без выдуманных цифр/заказчиков).
   КРИТИЧНО - точность:
   - RabbitMQ/Kafka - брокеры, не «протоколы»; HTTP/AMQP/TCP/IP - протоколы;
   - сети/Control Plane: TCP/HTTP, таймауты, retries, DNS, TLS, балансировка,
     сокеты, tcpdump/Wireshark при уместности;
     ЗАПРЕЩЕНО подменять ответ Kafka/Rabbit или только «микросервисы через REST»,
     если вопрос именно про сети;
   - годы: ~5 лет backend. Автотесты/AQA/pytest/QA на Python - тоже ~5 лет:
     эти практики шли параллельно коммерческой разработке (не «2 года AQA»).
     В single/multi бери опцию ближе к 5 / 5+ / 3-5, не 1-2.
   - C++/PyQt/редкий стек: не выдумывай годы; смежный опыт без «нет опыта»;
   - код: валидный Python (import с маленькой буквы).
     ЗАПРЕЩЕНО писать комментарии (#, docstring) в коде, если в вопросе
     явно не просили комментарии. Только код, без пояснений над/в скрипте.
   НИКОГДА: «нет опыта / не умею / не работал с». Только позитив и как делал.
2) Зарплата / офис / уход / мотивация / оформление - СТРОГО по КОНТЕКСТ ОТВЕТОВ HR.
   - Офис/гибрид: только удалёнка; в single/multi - «Нет».
   - Трудовой договор / официальное трудоустройство / ИП / ГПХ / самозанятость -
     ВСЕГДА да / готов (оформление любое). Не путать с офисом.
   - ЗП в textarea: РОВНО текст из КОНТЕКСТ (первый ответ про ожидания,
     со скобкой в конце). Не перефразируй. Без сумм
     (кроме числового-only поля -> 400000).
3) Пререквизиты (видео/ознакомились) - всегда «Да». Не про офис.
   Google Form/анкета «заполните» - не «посмотрел», а что заполните/пройдёте.
4) GitHub/портфолио: ВСЕГДА бери URL из блока КОНТАКТЫ (GitHub). В ответ -
   сама ссылка или одна короткая фраза + ссылка. Не уходи в «расскажу на собесе».
5) Телефон / Telegram / email: ТОЛЬКО из КОНТАКТЫ. Не выдумывай.
6) Гражданство: ВСЕГДА РФ / Россия / российское (не Армения). В single/multi —
   опция про РФ/Россию; в text — «РФ» или «Россия».
7) Single: ровно одна опция из options (точный текст). Свой вариант -> use_custom.
8) Multi: одна или несколько опций из options (точные тексты).
9) Text: техника коротко (см. СТИЛЬ); HR кратко. Только короткое тире «-».

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
    try:
        from cover_letter.contacts import (
            candidate_email,
            candidate_github,
            candidate_phone,
            candidate_telegram,
        )

        contacts_block = (
            f"Телефон: {candidate_phone()}\n"
            f"Email: {candidate_email()}\n"
            f"Telegram: {candidate_telegram()}\n"
            f"GitHub: {candidate_github()}"
        )
    except Exception:  # noqa: BLE001
        contacts_block = "(контакты не заданы)"

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
        f"=== КОНТАКТЫ (телефон/Telegram/email/GitHub в ответах) ===\n"
        f"{contacts_block}\n"
        f"=== КОНЕЦ КОНТАКТОВ ===\n\n"
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


# Канон из job_search/context/faq.md (скобка в конце — часть шаблона).
_SALARY_DEFAULT_NO_NUMBER = (
    "Я только недавно вышел на рынок и пока только присматриваюсь "
    "к требованиям и актуальным вилкам. Но я открыт к любым предложениям)"
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


def _is_prerequisite_yes_question(text: str) -> bool:
    """Вопрос «сделали X для рассмотрения?» — ожидается Да."""
    t = text.lower()
    # офис/переезд — отдельные правила, не пререквизит
    if any(x in t for x in ("офис", "гибрид", "переезд", "релокац")):
        return False
    # внешняя анкета/форма — не «посмотрел видео»
    if any(
        x in t
        for x in (
            "forms.gle",
            "docs.google",
            "анкет",
            "заполните",
            "заполни",
            "google form",
        )
    ):
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


def _is_employment_form_question(text: str) -> bool:
    """Трудовой договор / оформление - да; не путать с офисом."""
    t = text.lower().replace("ё", "е")
    if any(x in t for x in ("офис", "гибрид", "переезд", "релокац")):
        return False
    keys = (
        "трудоустрой",
        "трудов",
        "договор",
        "гпх",
        "самозанят",
        "официальн",
        "оформлен",
        "в штат",
        "штатн",
    )
    if any(k in t for k in keys):
        return True
    # «ИП» отдельно: не ловить «опыт», «типичный»
    return bool(re.search(r"(?<![а-яa-z])ип(?![а-яa-z])", t))


def _force_employment_yes(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """Оформление / трудовой договор - всегда Да."""
    if not _is_employment_form_question(q.text):
        return ans
    if q.kind in (QuestionKind.SINGLE, QuestionKind.MULTI) and q.options:
        yes = _pick_yes_option(q.options)
        if yes is None:
            return ans
        selected_low = " ".join(s.lower() for s in ans.selected)
        if yes in ans.selected or selected_low.strip() in ("да", "yes"):
            return ans
        if "да" in selected_low and "нет" not in selected_low:
            return ans
        logger.info(
            "Q[{}]: оформление {!r} → принудительно {!r} (было {!r})",
            q.index,
            q.text[:60],
            yes,
            ans.selected,
        )
        return TestAnswer(
            index=ans.index,
            kind=ans.kind,
            selected=(yes,),
            use_custom=False,
        )
    if q.kind == QuestionKind.TEXT:
        low = (ans.text or "").lower()
        if low.startswith("да") or "готов" in low:
            return ans
        fixed = (
            "Да, готов. Формат оформления любой "
            "(трудовой договор, ИП, ГПХ и т.п.). "
            "Работа только удалённо."
        )
        logger.info("Q[{}]: оформление text → канон «да»", q.index)
        return TestAnswer(
            index=ans.index, kind=ans.kind, text=fixed, use_custom=False
        )
    return ans


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


def _question_asks_for_comments(text: str) -> bool:
    t = text.lower()
    return any(
        x in t
        for x in ("комментар", "поясн", "объясн", "документир", "docstring")
    )


def _looks_like_code_answer(text: str) -> bool:
    """Грубо: ответ похож на скрипт, а не на прозу."""
    t = text.strip()
    if "\n" not in t and not re.search(r"\b(import|def |class |subprocess)\b", t):
        return False
    hits = 0
    for pat in (
        r"(?m)^\s*import\s+\w",
        r"(?m)^\s*from\s+\w",
        r"(?m)^\s*def\s+\w",
        r"(?m)^\s*class\s+\w",
        r"\bsubprocess\b",
        r"(?m)^\s*with\s+open\(",
        r"(?m)^\s*print\(",
    ):
        if re.search(pat, t, re.I):
            hits += 1
    return hits >= 2 or bool(re.search(r"(?m)^\s*import\s+\w", t, re.I))


def _strip_inline_python_comment(line: str) -> str:
    """Убрать #… вне строк; кавычки — упрощённо."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _strip_code_comments(text: str) -> str:
    """Убрать #/пустые поясняющие строки из ответа-скрипта."""
    lines_out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        cleaned = _strip_inline_python_comment(line)
        # пустые строки после вычистки комментариев пропускаем пачкой позже
        lines_out.append(cleaned)
    # схлопнуть подряд идущие пустые, trim краёв
    compact: list[str] = []
    blank = False
    for line in lines_out:
        if not line.strip():
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        blank = False
        # Import X → import X
        m = re.match(r"^(\s*)Import(\s+\w)", line)
        if m:
            line = f"{m.group(1)}import{m.group(2)}{line[m.end():]}"
        compact.append(line)
    while compact and not compact[0].strip():
        compact.pop(0)
    while compact and not compact[-1].strip():
        compact.pop()
    return "\n".join(compact)


def _sanitize_code_answer(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    if q.kind != QuestionKind.TEXT or not ans.text.strip():
        return ans
    if _question_asks_for_comments(q.text):
        return ans
    if not _looks_like_code_answer(ans.text):
        return ans
    cleaned = _strip_code_comments(ans.text)
    if cleaned == ans.text.strip() or cleaned == ans.text:
        # ещё поправить Import даже без комментариев
        fixed = re.sub(r"(?m)^(\s*)Import(\s+\w)", r"\1import\2", ans.text)
        if fixed == ans.text:
            return ans
        cleaned = fixed
    if cleaned != ans.text:
        logger.info("Q[{}]: убрал комментарии / поправил Import в коде", q.index)
        return TestAnswer(
            index=ans.index,
            kind=ans.kind,
            text=cleaned,
            selected=ans.selected,
            use_custom=ans.use_custom,
        )
    return ans


def _sanitize_answer(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """Пост-обработка: пререквизиты → Да; гражданство РФ; ЗП; код без #; тире."""
    ans = _force_prerequisite_yes(q, ans)
    ans = _force_employment_yes(q, ans)
    ans = _prefer_qa_experience_years(q, ans)
    ans = _force_citizenship_rf(q, ans)

    if ans.text.strip():
        dashed = _normalize_dashes(ans.text)
        # Скобку у канона ЗП не трогаем; у остального — убрать «сиротскую» ).
        if not (
            q.kind == QuestionKind.TEXT and _is_salary_question(q.text)
        ):
            if dashed.endswith(")") and dashed.count("(") == 0:
                dashed = dashed.rstrip(") ").strip()
        if dashed != ans.text:
            ans = TestAnswer(
                index=ans.index,
                kind=ans.kind,
                text=dashed,
                selected=ans.selected,
                use_custom=ans.use_custom,
            )

    if q.kind == QuestionKind.TEXT and ans.text.strip():
        if _is_salary_question(q.text):
            if _looks_like_numeric_only_answer(ans.text):
                return ans
            if ans.text.strip() != _SALARY_DEFAULT_NO_NUMBER:
                logger.info(
                    "Q[{}]: зарплата → фиксированный шаблон",
                    q.index,
                )
            return TestAnswer(
                index=ans.index,
                kind=ans.kind,
                text=_SALARY_DEFAULT_NO_NUMBER,
                selected=ans.selected,
                use_custom=ans.use_custom,
            )
        cleaned = _strip_lack_of_skill(ans.text)
        if cleaned != ans.text.strip():
            logger.info(
                "Q[{}]: убрал формулировки «нет опыта/не умею»",
                q.index,
            )
            if len(cleaned) < 40:
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
        ans = _boost_qa_years_in_text(q, ans)
        ans = _sanitize_code_answer(q, ans)

    return ans


def _is_citizenship_question(text: str) -> bool:
    t = text.lower()
    return any(
        x in t
        for x in (
            "гражданств",
            "citizen",
            "национальн",
            "passport",
            "паспорт",
        )
    )


def _pick_rf_citizenship_option(options: tuple[str, ...]) -> str | None:
    ranked: list[tuple[int, str]] = []
    for opt in options:
        ol = opt.lower()
        score = 0
        if any(x in ol for x in ("рф", "росси", "russian", "russia")):
            score = 10
        elif "армен" in ol or "armenia" in ol:
            score = -5
        if score > 0:
            ranked.append((score, opt))
    if not ranked:
        return None
    ranked.sort(key=lambda x: -x[0])
    return ranked[0][1]


def _force_citizenship_rf(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """Гражданство всегда РФ / Россия."""
    if not _is_citizenship_question(q.text):
        return ans
    if q.kind in (QuestionKind.SINGLE, QuestionKind.MULTI) and q.options:
        rf = _pick_rf_citizenship_option(q.options)
        if rf is not None:
            if rf in ans.selected:
                return ans
            logger.info(
                "Q[{}]: гражданство {!r} → {!r}",
                q.index,
                ans.selected,
                rf,
            )
            return TestAnswer(
                index=ans.index,
                kind=ans.kind,
                selected=(rf,),
                text="",
                use_custom=False,
            )
        if q.has_custom:
            logger.info("Q[{}]: гражданство → свой вариант «РФ»", q.index)
            return TestAnswer(
                index=ans.index,
                kind=ans.kind,
                selected=(),
                text="РФ",
                use_custom=True,
            )
        return ans
    if q.kind == QuestionKind.TEXT or ans.use_custom:
        text = (ans.text or "").strip()
        low = text.lower()
        if any(x in low for x in ("рф", "росси", "russian")) and "армен" not in low:
            return ans
        logger.info("Q[{}]: гражданство текст → РФ", q.index)
        return TestAnswer(
            index=ans.index,
            kind=ans.kind,
            text="РФ",
            selected=ans.selected if ans.use_custom else (),
            use_custom=ans.use_custom,
        )
    return ans


def _is_qa_years_question(text: str) -> bool:
    t = text.lower()
    if "лет" not in t and "опыт" not in t:
        return False
    return any(
        x in t
        for x in (
            "авто тест",
            "автотест",
            "автоматизац",
            "aqa",
            "sdet",
            "ручн",
            "mobile",
            "мобильн",
            "тестирован",
        )
    )


def _prefer_qa_experience_years(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """AQA/автотесты: ~5 лет (шли вместе с коммерческим backend)."""
    if q.kind not in (QuestionKind.SINGLE, QuestionKind.MULTI):
        return ans
    if not _is_qa_years_question(q.text):
        return ans
    if not ans.selected:
        return ans

    def _rank(opt: str) -> int:
        ol = opt.lower().replace(" ", "")
        if "5+" in ol or ol in ("5", "5лет", "более5", "более5лет"):
            return 100
        if "4-5" in ol or "3-5" in ol or "5лет" in ol:
            return 90
        if "3-4" in ol or ol.startswith("3"):
            return 70
        if "2-3" in ol:
            return 40
        if "1-2" in ol or ol.startswith("1"):
            return 10
        if "нет" in ol or "без опыта" in ol:
            return -1
        return 0

    preferred = max(q.options, key=_rank, default=None)
    if preferred is None or _rank(preferred) <= 0:
        return ans
    if preferred in ans.selected:
        return ans
    cur_rank = max((_rank(s) for s in ans.selected), default=0)
    if cur_rank >= 90:
        return ans
    logger.info(
        "Q[{}]: годы QA/автотесты {!r} → {!r} (~5 лет)",
        q.index,
        ans.selected,
        preferred,
    )
    return TestAnswer(
        index=ans.index,
        kind=ans.kind,
        selected=(preferred,),
        text=ans.text,
        use_custom=False,
    )


def _boost_qa_years_in_text(q: TestQuestion, ans: TestAnswer) -> TestAnswer:
    """В textarea про автотесты/AQA не занижать годы до 1-2."""
    if q.kind != QuestionKind.TEXT or not ans.text.strip():
        return ans
    if not _is_qa_years_question(q.text):
        return ans
    text = ans.text
    new = re.sub(
        r"(?i)(?:около|примерно)?\s*"
        r"(?:[12]|1\s*[-–]\s*2|2\s*[-–]\s*3)\s*"
        r"(?:года|год|лет)",
        "около 5 лет",
        text,
        count=1,
    )
    if new == text:
        return ans
    logger.info("Q[{}]: годы автотестов в тексте → около 5 лет", q.index)
    return TestAnswer(
        index=ans.index,
        kind=ans.kind,
        text=new,
        selected=ans.selected,
        use_custom=ans.use_custom,
    )


def _fallback_answer(q: TestQuestion) -> TestAnswer:
    """Запасной ответ без LLM (чтобы не сорвать отклик полностью)."""
    text_l = q.text.lower()
    if q.kind == QuestionKind.SINGLE and q.options:
        # пререквизит (видео и т.п.) → Да
        if _is_prerequisite_yes_question(q.text):
            yes = _pick_yes_option(q.options)
            if yes is not None:
                return TestAnswer(index=q.index, kind=q.kind, selected=(yes,))
        if _is_citizenship_question(q.text):
            rf = _pick_rf_citizenship_option(q.options)
            if rf is not None:
                return TestAnswer(index=q.index, kind=q.kind, selected=(rf,))
            if q.has_custom:
                return TestAnswer(
                    index=q.index, kind=q.kind, text="РФ", use_custom=True
                )
        # оформление / трудовой договор → да (не путать с офисом)
        if _is_employment_form_question(q.text):
            yes = _pick_yes_option(q.options)
            if yes is not None:
                return TestAnswer(index=q.index, kind=q.kind, selected=(yes,))
        # офис → нет / удалёнка
        if any(x in text_l for x in ("офис", "гибрид")) or (
            "удал" in text_l and "готов" not in text_l
        ):
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
    if _is_citizenship_question(q.text):
        return TestAnswer(index=q.index, kind=QuestionKind.TEXT, text="РФ")
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
