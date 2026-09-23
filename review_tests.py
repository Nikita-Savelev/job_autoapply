"""Live-отклики на вакансии с опросником (как в реальном пайплайне).

Письмо + ответы LLM + финальная «Откликнуться» после теста.
По умолчанию — настоящий отклик (submit=True). Для ручной проверки формы:

    python review_tests.py --review

Live:

    python review_tests.py
    python review_tests.py --pause 1.5
"""

from __future__ import annotations

import argparse
import sys

from browser import create_driver, quit_driver
from config import (
    HH_BASE,
    load_env,
    load_resume_text,
    pause_between_actions_sec,
    pg_conninfo,
    resume_path,
    target_role,
)
from cover_letter import generate_cover_letter
from db.models import Vacancy, VacancyStatus
from db.store import VacancyStore
from debug import PageDumper
from hh.actions import (
    AlreadyResponded,
    ApplyOutcome,
    ResponseTestRequired,
    apply_with_letter,
    detect_response_button_state,
)
from hh.company import get_or_fetch_company
from hh.vacancy import scrape_vacancy_page, vacancy_tab
from logging_setup import setup_logging
from loguru import logger

# Вакансии из tests_example (опросник при отклике)
REVIEW_VACANCIES: list[tuple[str, str]] = [
    ("137044973", "Аналитик-разработчик (Python) — Дефанс Страхование"),
    ("136920411", "Fullstack-разработчик — ФЦК"),
    ("135690129", "Разработчик Python control plane — Бифорком Тек"),
]


def _wait_enter(message: str) -> None:
    print()
    try:
        input(f"{message}\n→ Enter, чтобы продолжить… ")
    except EOFError:
        logger.warning("stdin закрыт — продолжаю без паузы")


def _ensure_vacancy(
    store: VacancyStore,
    *,
    hh_id: str,
    title: str,
    url: str,
    company_name: str,
    company_hh_id: str | None,
    description: str | None,
) -> Vacancy:
    vac = store.get(hh_id)
    if vac is not None:
        return vac
    return store.upsert_from_search(
        Vacancy(
            hh_id=hh_id,
            title=title,
            url=url,
            company=company_name or None,
            company_hh_id=company_hh_id,
            description=description,
        )
    )


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Отклики с опросником: live submit или --review без финальной кнопки"
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Только заполнить форму, финальную «Откликнуться» не жать",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SEC",
        help="Пауза между действиями (по умолчанию HH_PAUSE_SEC)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=True,
        help="DEBUG + HTML-дампы (вкл. по умолчанию)",
    )
    parser.add_argument(
        "--no-debug",
        action="store_true",
        help="Выключить debug-дампы",
    )
    args = parser.parse_args(argv)

    submit_test = not bool(args.review)
    debug = bool(args.debug) and not bool(args.no_debug)
    dumper = PageDumper() if debug else None
    log_file = (dumper.run_dir / "run.log") if dumper else None
    setup_logging(debug=debug, log_file=log_file)

    pause_sec = (
        max(0.0, float(args.pause))
        if args.pause is not None
        else pause_between_actions_sec()
    )

    resume_text = load_resume_text()
    store = VacancyStore(pg_conninfo())

    mode = "LIVE (реальный отклик)" if submit_test else "REVIEW (без финального submit)"
    logger.info("TESTS: {} вакансий, mode={}", len(REVIEW_VACANCIES), mode)
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("pause={}s debug={}", pause_sec, debug)
    if submit_test:
        logger.warning(
            "LIVE: после опросника жмём «Откликнуться» — отклик уйдёт работодателю."
        )
    else:
        logger.warning(
            "REVIEW: финальную кнопку не жмём — проверь форму вручную."
        )

    driver = create_driver(headless=False, background=False)
    driver.get(HH_BASE)
    try:
        total = len(REVIEW_VACANCIES)
        ok = 0
        for i, (hh_id, hint) in enumerate(REVIEW_VACANCIES, start=1):
            url = f"{HH_BASE}/vacancy/{hh_id}"
            logger.info("=== [{}/{}] {} — {}", i, total, hh_id, hint)
            logger.info("URL: {}", url)

            try:
                with vacancy_tab(
                    driver,
                    url,
                    dumper=dumper,
                    label=f"test_{hh_id}",
                    pause_sec=pause_sec,
                ):
                    page = scrape_vacancy_page(driver, dumper=dumper)
                    title = page.title or hint
                    logger.info("Страница: {!r}", title)

                    state = detect_response_button_state(driver)
                    if state.already_responded:
                        logger.warning(
                            "Уже откликались (кнопка {!r})",
                            state.label,
                        )
                        store.mark(
                            hh_id,
                            VacancyStatus.APPLIED,
                            skip_reason=f"already_on_hh: {state.label}",
                            description=page.description,
                        )
                        if args.review:
                            _wait_enter(
                                f"[{i}/{total}] Уже отклик. Enter — дальше"
                            )
                        continue

                    company = get_or_fetch_company(
                        driver,
                        store,
                        dumper=dumper,
                        pause_sec=pause_sec,
                    )
                    company_name = (company.name if company else "") or ""
                    company_hh_id = company.hh_id if company else None
                    company_info = company.profile_text() if company else ""

                    _ensure_vacancy(
                        store,
                        hh_id=hh_id,
                        title=title,
                        url=url,
                        company_name=company_name,
                        company_hh_id=company_hh_id,
                        description=page.description,
                    )

                    letter = generate_cover_letter(
                        vacancy_title=title,
                        company=company_name or None,
                        description=page.description,
                        resume_text=resume_text,
                        company_info=company_info or None,
                    )
                    logger.info("Письмо сгенерировано, len={}", len(letter))

                    result = apply_with_letter(
                        driver,
                        letter,
                        pause_sec=pause_sec,
                        dumper=dumper,
                        resume_text=resume_text,
                        vacancy_title=title,
                        company=company_name,
                        vacancy_description=page.description or "",
                        submit_test=submit_test,
                    )
                    logger.info(
                        "Исход: outcome={} detail={}",
                        result.outcome.value,
                        result.detail,
                    )

                    mark_kw = dict(
                        description=page.description,
                        cover_letter=letter,
                        company=company_name or None,
                        company_hh_id=company_hh_id,
                    )
                    if result.test_qa:
                        mark_kw["test_qa"] = result.test_qa

                    if result.outcome == ApplyOutcome.LETTER_SENT:
                        if submit_test:
                            store.mark(
                                hh_id,
                                VacancyStatus.APPLIED,
                                error_message="",
                                skip_reason="",
                                **mark_kw,
                            )
                            ok += 1
                            logger.info("APPLY ok (with_test) {}", hh_id)
                        else:
                            # review: только Q&A, статус не форсим в applied
                            vac = store.get(hh_id)
                            store.mark(
                                hh_id,
                                vac.status if vac else VacancyStatus.NEW,
                                **mark_kw,
                            )
                            logger.info("REVIEW: форма заполнена, Q&A в БД")
                            _wait_enter(
                                f"[{i}/{total}] Проверь форму. Enter — дальше"
                            )
                    else:
                        store.mark(
                            hh_id,
                            VacancyStatus.ERROR,
                            error_message=result.detail or result.outcome.value,
                            **mark_kw,
                        )
                        logger.warning("Неожиданный исход: {}", result)
                        if args.review:
                            _wait_enter(f"[{i}/{total}] Ошибка. Enter — дальше")

            except AlreadyResponded as exc:
                logger.warning("AlreadyResponded {}: {}", hh_id, exc)
                store.mark(
                    hh_id,
                    VacancyStatus.APPLIED,
                    skip_reason=f"already_on_hh: {exc.label}",
                )
                if args.review:
                    _wait_enter(f"[{i}/{total}] Уже отклик. Enter — дальше")
            except ResponseTestRequired as exc:
                logger.error("Опросник не пройден {}: {}", hh_id, exc)
                store.mark(
                    hh_id,
                    VacancyStatus.BLOCKED,
                    skip_reason="test_required",
                    error_message=str(exc),
                )
                if args.review:
                    _wait_enter(f"[{i}/{total}] Ошибка теста. Enter — дальше")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Сбой на {}: {}", hh_id, exc)
                store.mark(
                    hh_id,
                    VacancyStatus.ERROR,
                    error_message=str(exc),
                )
                if args.review:
                    _wait_enter(f"[{i}/{total}] Ошибка. Enter — дальше")

        logger.info(
            "Готово: {}/{} , mode={}",
            ok if submit_test else total,
            total,
            mode,
        )
        return 0
    finally:
        quit_driver(driver)
        store.close()


if __name__ == "__main__":
    sys.exit(main())
