"""Генерация персонализированного сопроводительного под вакансию и компанию."""

from __future__ import annotations

import httpx
from loguru import logger

from config import llm_api_key, llm_base_url, llm_model, load_resume_text
from cover_letter.contacts import (
    CANDIDATE_NAME,
    RESUME_NAME_ALIASES,
    candidate_email,
    candidate_phone,
    candidate_telegram,
)
from cover_letter.templates import (
    FALLBACK_TEMPLATE,
    contact_fields,
    load_structure_guide,
    load_voice_rules,
)

SYSTEM_PROMPT = """\
Ты пишешь сопроводительные письма от лица кандидата на hh.ru.
Письмо УНИКАЛЬНОЕ и персонализированное под конкретную вакансию и компанию.

Следуй структуре и правилам голоса из сообщения пользователя.
Критично:
- имя в письме ТОЛЬКО из блока КАНДИДАТ (Никита Гамиев). Фамилию из резюме не использовать;
- основной акцент письма — на требованиях и задачах вакансии (обязательный стек, ключевые обязанности);
- если есть блок О КОМПАНИИ — коротко и по фактам объясни, почему хочешь работать именно в этой компании
  (продукт, домен, масштаб, культура — только из переданного текста, не выдумывай);
- если в вакансии обязательны технологии, которых нет в тексте резюме: считай, что они просто не перечислены.
  Пиши так, будто кандидат уже работал с ними на одном из мест работы из резюме
  (назови реальное место из резюме и естественно встрой эти технологии в опыт);
- контакты бери ТОЛЬКО из блока КАНДИДАТ (не из текста резюме, там могут быть устаревшие);
- в блоке контактов ОБЯЗАТЕЛЬНО укажи Telegram из блока КАНДИДАТ (нельзя опускать);
- НИКОГДА не указывай зарплату, вилку, ожидаемый или минимальный доход;
- тон живой Senior Python, на «вы», без AI-клише;
- НИКОГДА не используй длинные тире (— / – / −). Только короткий дефис "-"
  или точка/запятая. Плохо: «роль — Backend». Ок: «роль - Backend».
- без стрелок, без ёлочек-кавычек.

Ответ: ТОЛЬКО текст письма на русском, без markdown.
Объём roughly 160-320 слов.
"""

def generate_cover_letter(
    *,
    vacancy_title: str,
    company: str | None = None,
    description: str | None = None,
    resume_text: str | None = None,
    recruiter_name: str | None = None,
    company_info: str | None = None,
    use_llm: bool = True,
) -> str:
    """Собрать письмо под вакансию/компанию (LLM) или fallback-шаблон."""
    title = (vacancy_title or "").strip() or "Backend-разработчик"
    company_name = (company or "").strip()
    desc = (description or "").strip()
    company_profile = (company_info or "").strip()
    resume = (resume_text or "").strip()
    if not resume:
        try:
            resume = load_resume_text()
        except FileNotFoundError:
            resume = ""

    if use_llm and llm_api_key():
        try:
            text = _generate_via_llm(
                vacancy_title=title,
                company=company_name,
                description=desc,
                resume_text=resume,
                recruiter_name=(recruiter_name or "").strip() or None,
                company_info=company_profile,
            )
            if text:
                text = finalize_cover_letter(text)
                logger.info(
                    "cover letter LLM ok: title={!r} company={!r} len={}",
                    title,
                    company_name,
                    len(text),
                )
                return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM cover letter failed, fallback: {}", exc)

    return finalize_cover_letter(
        _fallback_letter(title, company_name, desc, company_profile)
    )

def _generate_via_llm(
    *,
    vacancy_title: str,
    company: str,
    description: str,
    resume_text: str,
    recruiter_name: str | None,
    company_info: str = "",
) -> str:
    structure = load_structure_guide()
    voice = load_voice_rules()
    phone = candidate_phone()
    email = candidate_email()
    telegram = candidate_telegram()

    company_block = (
        f"=== О КОМПАНИИ (со страницы работодателя) ===\n{company_info}\n\n"
        if company_info
        else "=== О КОМПАНИИ ===\n(отдельного описания нет — опирайся только на текст вакансии)\n\n"
    )

    user_content = (
        f"=== СТРУКТУРА ПИСЬМА ===\n{structure}\n\n"
        f"=== ПРАВИЛА ГОЛОСА ===\n{voice}\n\n"
        f"=== КАНДИДАТ (актуальные контакты) ===\n"
        f"ФИО: {CANDIDATE_NAME}\n"
        f"(Имя в письме только это. В резюме может быть другая фамилия — ИГНОРИРУЙ её.)\n"
        f"Телефон: {phone}\n"
        f"Email: {email}\n"
        f"Telegram: {telegram}\n"
        f"(В резюме могут быть старые контакты - ИГНОРИРУЙ их, используй только эти.)\n\n"
        f"=== РЕЗЮМЕ (опыт и стек; контакты и ФИО отсюда не брать) ===\n"
        f"{resume_text[:12000]}\n\n"
        f"{company_block}"
        f"=== ВАКАНСИЯ ===\n"
        f"Название: {vacancy_title}\n"
        f"Компания: {company or 'не указана'}\n"
        f"Имя рекрутера: {recruiter_name or 'неизвестно'}\n"
        f"Описание:\n{description[:10000] or '(описания нет)'}\n\n"
        "Напиши готовое сопроводительное. "
        "Главный акцент — на обязательных требованиях и задачах вакансии. "
        "В блоке «почему эта компания» опирайся на факты из О КОМПАНИИ "
        "(и вакансии), коротко объясни интерес именно к ней. "
        "Если обязательный стек не перечислен в резюме — опиши его как опыт "
        "на одном из мест работы из резюме (не пиши, что технологии «не указаны»). "
        "Зарплату и вилку не упоминай. "
        f"Представляйся только как {CANDIDATE_NAME}. "
        f"В конце обязательно укажи контакты, включая Telegram {telegram}."
    )
    url = f"{llm_base_url()}/chat/completions"
    model = llm_model()
    logger.debug("cover letter LLM request model={} company={!r}", model, company)

    body = {
        "model": model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {llm_api_key()}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    content = (data["choices"][0]["message"]["content"] or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()
    return finalize_cover_letter(content)


def finalize_cover_letter(text: str) -> str:
    """Постобработка: тире, имя как в аккаунте hh + обязательный Telegram."""
    body = _normalize_dashes(text or "")
    return ensure_telegram_in_letter(normalize_candidate_name(body))


def _normalize_dashes(text: str) -> str:
    """Длинные тире → короткий дефис '-'."""
    return (
        (text or "")
        .replace("—", "-")
        .replace("–", "-")
        .replace("−", "-")
    )


def normalize_candidate_name(text: str) -> str:
    """Заменить фамилию из резюме на Гамиев (как в аккаунте hh)."""
    body = text or ""
    for alias in RESUME_NAME_ALIASES:
        if alias in body:
            body = body.replace(alias, "Гамиев")
            logger.info("cover letter: заменил фамилию {} → Гамиев", alias)
    # частые варианты из PDF: «Савельев Никита» уже станет «Гамиев Никита» —
    # приведём представление к канону, если встретится перестановка
    if "Гамиев Никита" in body and CANDIDATE_NAME not in body:
        body = body.replace("Гамиев Никита", CANDIDATE_NAME)
    return body


def ensure_telegram_in_letter(text: str) -> str:
    """Гарантировать, что Telegram из contacts есть в письме."""
    telegram = candidate_telegram().strip()
    if not telegram:
        return text
    body = (text or "").strip()
    # handle без @ и с @ — LLM иногда пишет по-разному
    handle = telegram.lstrip("@").lower()
    lowered = body.lower()
    if handle and handle in lowered:
        return body
    if "telegram" in lowered and telegram.lower() in lowered:
        return body

    phone = candidate_phone()
    email = candidate_email()
    contact_line = (
        f"Связаться удобнее по телефону {phone}, почте {email} "
        f"или в Telegram {telegram}."
    )
    logger.info("cover letter: дописал обязательный Telegram {}", telegram)
    if body:
        return f"{body}\n\n{contact_line}"
    return contact_line

def _fallback_letter(
    title: str,
    company: str,
    description: str,
    company_info: str = "",
) -> str:
    company_part = f" ({company})" if company else ""
    why_company = (
        f"Вакансия {title} совпадает с тем, чем я занимаюсь последние годы: "
        "Python backend, интеграции и продакшен-сервисы."
    )
    if company:
        why_company = (
            f"Мне интересна {company}: по описанию вакансии {title} вижу задачи, "
            "близкие моему опыту в backend на Python."
        )
        if company_info:
            snippet = " ".join(company_info.split())[:220]
            why_company = (
                f"Мне интересна {company}: {snippet}. "
                f"Вакансия {title} совпадает с тем, чем я занимаюсь в Python backend."
            )

    desc_l = description.lower()
    bits: list[str] = []
    if "django" in desc_l:
        bits.append("Django")
    if "fastapi" in desc_l:
        bits.append("FastAPI")
    if "postgres" in desc_l or "postgresql" in desc_l:
        bits.append("PostgreSQL")
    if "kafka" in desc_l or "rabbit" in desc_l:
        bits.append("очереди")
    stack_hit = ", ".join(bits) if bits else "Python / Django / FastAPI / PostgreSQL"
    why_me = (
        "Последние около 5 лет - коммерческий Python backend: полный цикл, микросервисы, "
        f"интеграции; из пересечений с вакансией особенно близки: {stack_hit}. "
        "На BOTTEC выводил в прод 30+ проектов, в том числе для крупных заказчиков; "
        "ранее - high-load парсинг и финтех-поиск."
    )
    return FALLBACK_TEMPLATE.format(
        vacancy_title=title,
        company_part=company_part,
        why_company=why_company,
        why_me=why_me,
        **contact_fields(),
    ).strip()
