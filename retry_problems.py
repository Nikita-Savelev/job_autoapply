"""Повторный отклик по проблемным вакансиям (error / blocked).

Берёт из БД status=error|blocked, снова открывает карточку и откликается
текущим пайплайном (письмо + опросник + капча-пауза).

    python retry_problems.py
    python retry_problems.py --status blocked
    python retry_problems.py --limit 10
    python retry_problems.py --dry-run
"""

from __future__ import annotations

import argparse
import sys

from browser import create_driver, quit_driver
from config import (
    load_env,
    load_resume_text,
    pause_between_actions_sec,
    pg_conninfo,
    pg_dsn_display,
    resume_path,
    target_role,
)
from cover_letter import generate_cover_letter
from db.models import VacancyStatus
from db.store import VacancyStore
from debug import PageDumper
from hh.actions import (
    AlreadyResponded,
    ApplyOutcome,
    LetterSubmitUnavailable,
    ResponseTestRequired,
    apply_with_letter,
    detect_response_button_state,
)
from hh.captcha import CaptchaTimeout, resolve_captcha_if_present
from hh.company import get_or_fetch_company
from hh.vacancy import scrape_vacancy_page, vacancy_tab
from logging_setup import setup_logging
from loguru import logger

_STATUS_MAP = {
    "error": VacancyStatus.ERROR,
    "blocked": VacancyStatus.BLOCKED,
}


def _is_dead_session(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        "invalid session" in msg
        or "invalidsessionid" in name.lower()
        or name == "InvalidSessionIdException"
    )



def _parse_statuses(raw: str) -> tuple[VacancyStatus, ...]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return (VacancyStatus.ERROR, VacancyStatus.BLOCKED)
    out: list[VacancyStatus] = []
    for p in parts:
        if p not in _STATUS_MAP:
            raise SystemExit(f"Неизвестный status={p!r}. Допустимо: error, blocked")
        out.append(_STATUS_MAP[p])
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="Ретрай откликов по error/blocked вакансиям"
    )
    parser.add_argument(
        "--status",
        default="error,blocked",
        help="Какие статусы брать: error,blocked (через запятую)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Сколько максимум обработать (0 = все)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только список id, без браузера и кликов",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="DEBUG + HTML-дампы",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        metavar="SEC",
        help="Пауза между действиями",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Chrome без окна",
    )
    args = parser.parse_args(argv)

    statuses = _parse_statuses(args.status)
    dumper = PageDumper() if args.debug else None
    log_file = (dumper.run_dir / "run.log") if dumper else None
    setup_logging(debug=args.debug, log_file=log_file)

    pause_sec = (
        max(0.0, float(args.pause))
        if args.pause is not None
        else pause_between_actions_sec()
    )

    store = VacancyStore(pg_conninfo())
    problems = store.list_by_status(*statuses)
    if args.limit and args.limit > 0:
        problems = problems[: args.limit]

    logger.info("RETRY problems: {} шт. statuses={}", len(problems), [s.value for s in statuses])
    logger.info("TARGET_ROLE: {}", target_role())
    logger.info("RESUME: {}", resume_path())
    logger.info("DB: {}", pg_dsn_display())
    logger.info(
        "mode: {} pause={}s",
        "dry-run" if args.dry_run else "LIVE",
        pause_sec,
    )

    if not problems:
        logger.info("Нечего ретраить — очередь пуста")
        store.close()
        return 0

    for i, vac in enumerate(problems, start=1):
        logger.info(
            "[{}/{}] {} {} | {} | {}",
            i,
            len(problems),
            vac.status.value,
            vac.hh_id,
            (vac.title or "")[:60],
            (vac.error_message or vac.skip_reason or "")[:80],
        )

    if args.dry_run:
        store.close()
        return 0

    resume_text = load_resume_text()
    driver = create_driver(headless=args.headless)

    applied = 0
    already = 0
    blocked = 0
    errors = 0
    stop_all = False

    def _recreate_driver() -> None:
        nonlocal driver
        logger.warning("Chrome session умерла — пересоздаю браузер")
        quit_driver(driver)
        driver = create_driver(headless=args.headless)

    try:
        for i, vac in enumerate(problems, start=1):
            if stop_all:
                break
            hh_id = vac.hh_id
            logger.info(
                "=== RETRY [{}/{}] {} {}",
                i,
                len(problems),
                hh_id,
                vac.title,
            )
            session_retries = 0
            while True:
                try:
                    resolve_captcha_if_present(
                        driver, context=f"retry перед {hh_id}"
                    )
                    with vacancy_tab(
                        driver,
                        vac.url,
                        dumper=dumper,
                        label=f"retry_{hh_id}",
                        pause_sec=pause_sec,
                    ):
                        resolve_captcha_if_present(
                            driver, context=f"retry на странице {hh_id}"
                        )
                        page = scrape_vacancy_page(driver, dumper=dumper)

                        state = detect_response_button_state(driver)
                        if state.already_responded:
                            logger.info(
                                "ALREADY on hh {} — {!r}", hh_id, state.label
                            )
                            store.mark(
                                hh_id,
                                VacancyStatus.APPLIED,
                                description=page.description,
                                skip_reason=f"already_on_hh: {state.label}",
                                error_message="",
                            )
                            already += 1
                            break

                        company = get_or_fetch_company(
                            driver,
                            store,
                            dumper=dumper,
                            pause_sec=pause_sec,
                        )
                        company_name = (
                            (company.name if company else None) or vac.company
                        )
                        company_hh_id = (
                            company.hh_id if company else vac.company_hh_id
                        )
                        company_info = (
                            company.profile_text() if company else ""
                        )
                        letter = generate_cover_letter(
                            vacancy_title=page.title or vac.title,
                            company=company_name,
                            description=page.description,
                            resume_text=resume_text,
                            company_info=company_info or None,
                        )
                        result = apply_with_letter(
                            driver,
                            letter,
                            pause_sec=pause_sec,
                            dumper=dumper,
                            resume_text=resume_text,
                            vacancy_title=page.title or vac.title,
                            company=company_name or "",
                            vacancy_description=page.description or "",
                        )
                        mark_kw = dict(
                            description=page.description,
                            cover_letter=letter,
                            company_hh_id=company_hh_id,
                            company=company_name,
                        )
                        if result.test_qa:
                            mark_kw["test_qa"] = result.test_qa

                        if result.outcome == ApplyOutcome.LETTER_SENT:
                            store.mark(
                                hh_id,
                                VacancyStatus.APPLIED,
                                error_message="",
                                skip_reason="",
                                **mark_kw,
                            )
                            applied += 1
                            logger.info(
                                "APPLY ok ({}) {}",
                                result.detail or "letter",
                                hh_id,
                            )
                        elif result.outcome == ApplyOutcome.APPLIED_NO_LETTER:
                            store.mark(
                                hh_id,
                                VacancyStatus.APPLIED,
                                error_message="отклик без UI письма",
                                **mark_kw,
                            )
                            applied += 1
                            logger.info("APPLY ok (no letter UI) {}", hh_id)
                        else:
                            store.mark(
                                hh_id,
                                VacancyStatus.ERROR,
                                error_message=result.detail
                                or result.outcome.value,
                                **mark_kw,
                            )
                            errors += 1
                            logger.warning(
                                "RETRY fail {}: {}", hh_id, result.detail
                            )
                    break
                except AlreadyResponded as exc:
                    store.mark(
                        hh_id,
                        VacancyStatus.APPLIED,
                        skip_reason=f"already_on_hh: {exc.label}",
                        error_message="",
                    )
                    already += 1
                    logger.info("ALREADY {}: {}", hh_id, exc.label)
                    break
                except ResponseTestRequired as exc:
                    store.mark(
                        hh_id,
                        VacancyStatus.BLOCKED,
                        skip_reason="test_required",
                        error_message=str(exc),
                    )
                    blocked += 1
                    logger.warning("BLOCKED (test) {}: {}", hh_id, exc)
                    break
                except LetterSubmitUnavailable as exc:
                    store.mark(
                        hh_id, VacancyStatus.ERROR, error_message=str(exc)
                    )
                    errors += 1
                    logger.warning("letter submit fail {}: {}", hh_id, exc)
                    break
                except CaptchaTimeout as exc:
                    store.mark(
                        hh_id,
                        VacancyStatus.BLOCKED,
                        skip_reason="captcha",
                        error_message=str(exc),
                    )
                    blocked += 1
                    logger.error("CAPTCHA timeout — стоп ретрая: {}", exc)
                    stop_all = True
                    break
                except Exception as exc:  # noqa: BLE001
                    if _is_dead_session(exc) and session_retries < 2:
                        session_retries += 1
                        logger.error(
                            "invalid session на {} — recreate ({}/2)",
                            hh_id,
                            session_retries,
                        )
                        _recreate_driver()
                        continue
                    logger.error("Ошибка retry {}: {}", hh_id, exc)
                    store.mark(
                        hh_id, VacancyStatus.ERROR, error_message=str(exc)
                    )
                    errors += 1
                    break
    finally:
        quit_driver(driver)
        counts = store.counts()
        store.close()

    logger.info(
        "ИТОГО retry: applied={} already={} blocked={} errors={} of {}",
        applied,
        already,
        blocked,
        errors,
        len(problems),
    )
    logger.info("counts in DB: {}", counts)
    return 0 if errors == 0 and blocked == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
