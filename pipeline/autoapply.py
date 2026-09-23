"""Пайплайн: scrape → LLM batch-match → hide | apply."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger
from selenium.webdriver.remote.webdriver import WebDriver

from config import daily_apply_limit, load_resume_text, pause_between_actions_sec, target_role
from cover_letter import generate_cover_letter
from db.models import Vacancy, VacancyStatus
from db.store import VacancyStore
from debug.page_dump import PageDumper
from hh import actions
from hh.actions import (
    AlreadyResponded,
    ApplyOutcome,
    LetterSubmitUnavailable,
    ResponseTestRequired,
    detect_response_button_state,
)
from hh.captcha import CaptchaTimeout, resolve_captcha_if_present
from hh.company import get_or_fetch_company
from hh.search import go_next_search_page, open_search, scrape_search_page
from hh.vacancy import scrape_vacancy_page, vacancy_closed_reason, vacancy_tab
from matcher import match_vacancies_batch


@dataclass
class PipelineStats:
    scraped: int = 0
    upserted: int = 0
    skipped: int = 0
    applied: int = 0
    blocked: int = 0
    errors: int = 0
    pages: int = 0
    notes: list[str] = field(default_factory=list)
    debug_dir: str | None = None


class AutoApplyPipeline:
    """Один прогон по SEARCH_URL с пагинацией.

    dry_run=True — только матчинг без кликов (флаг --dry-run).
    По умолчанию и в --debug — реальные отклики.
    max_applies — лимит за прогон; дополнительно жёсткий дневной потолок
    HH_DAILY_APPLY_LIMIT (по умолчанию 200) по applied_at в БД.
    Страницы и матчинг — без лимита (до конца выдачи / пока есть NEW).
    """

    # Размер куска для LLM (не лимит обработки — все NEW всё равно проходят)
    _LLM_CHUNK = 50

    def __init__(
        self,
        driver: WebDriver,
        store: VacancyStore,
        *,
        search_url: str,
        dry_run: bool = True,
        debug: bool = False,
        limit: int | None = None,
        max_applies: int = 1,
        max_pages: int | None = None,
        pause_sec: float | None = None,
        dumper: PageDumper | None = None,
    ) -> None:
        self.driver = driver
        self.store = store
        self.search_url = search_url
        self.dry_run = dry_run
        self.debug = debug
        # None = без лимита (все NEW → LLM)
        self.limit = limit
        self.max_applies = max_applies
        # None = листать до конца выдачи
        self.max_pages = max_pages
        self.pause = (
            max(0.0, float(pause_sec))
            if pause_sec is not None
            else pause_between_actions_sec()
        )
        self.role = target_role()
        self.dumper = dumper
        self._resume_text = ""
        self._retried_error_ids: set[str] = set()
        if self.debug and self.dumper is None:
            self.dumper = PageDumper()

    def run(self) -> PipelineStats:
        stats = PipelineStats()
        if self.dumper is not None:
            stats.debug_dir = str(self.dumper.run_dir)
            stats.notes.append(f"debug HTML → {self.dumper.run_dir}")
            logger.info("Debug HTML dir: {}", self.dumper.run_dir)

        daily_cap = daily_apply_limit()
        used_today = self.store.count_applied_today()
        remaining_today = max(0, daily_cap - used_today)
        if remaining_today <= 0:
            msg = f"stop: daily_limit {used_today}/{daily_cap}"
            stats.notes.append(msg)
            logger.warning(
                "Дневной лимит откликов исчерпан: {}/{} — прогон не стартуем",
                used_today,
                daily_cap,
            )
            return stats
        if self.max_applies > remaining_today:
            logger.info(
                "CLI max_applies={} урезан дневным лимитом до {} "
                "(уже сегодня {}/{})",
                self.max_applies,
                remaining_today,
                used_today,
                daily_cap,
            )
            self.max_applies = remaining_today
        else:
            logger.info(
                "Дневной лимит: уже {}/{}, на прогон max_applies={}",
                used_today,
                daily_cap,
                self.max_applies,
            )

        pages_label = str(self.max_pages) if self.max_pages else "∞"
        logger.info(
            "Старт dry_run={} debug={} match_limit={} max_applies={} "
            "max_pages={} pause={}s role={!r}",
            self.dry_run,
            self.debug,
            self.limit if self.limit is not None else "∞",
            self.max_applies,
            pages_label,
            self.pause,
            self.role,
        )

        open_search(self.driver, self.search_url, dumper=self.dumper)
        time.sleep(self.pause)

        resume_text = load_resume_text()
        logger.info("Резюме загружено: {} символов", len(resume_text))
        self._resume_text = resume_text

        page_no = 0
        while True:
            page_no += 1
            if stats.applied >= self.max_applies:
                break
            if self.max_pages is not None and page_no > self.max_pages:
                stats.notes.append(f"stop: max_pages={self.max_pages}")
                logger.info("Достигнут max_pages={} — стоп", self.max_pages)
                break

            stats.pages = page_no
            logger.info(
                "=== Страница поиска {}/{} ===",
                page_no,
                pages_label,
            )

            cards = scrape_search_page(self.driver, dumper=self.dumper)
            stats.scraped += len(cards)
            stats.notes.append(f"стр.{page_no}: карточек={len(cards)}")

            page_new = 0
            for card in cards:
                saved = self.store.upsert_from_search(card)
                stats.upserted += 1
                logger.debug(
                    "upsert id={} status={} title={!r}",
                    saved.hh_id,
                    saved.status.value,
                    saved.title,
                )
                if saved.status == VacancyStatus.NEW:
                    page_new += 1

            self._process_queue(stats, page_new=page_new)

            if stats.applied >= self.max_applies:
                logger.info(
                    "Достигнут max_applies={} — останавливаем прогон",
                    self.max_applies,
                )
                stats.notes.append(f"stop: max_applies={self.max_applies}")
                break

            if page_new == 0:
                logger.info(
                    "На странице {} нет NEW вакансий — листаем дальше",
                    page_no,
                )
            else:
                logger.info(
                    "Страница {} обработана (NEW было {}), листаем дальше "
                    "(applied={}/{})",
                    page_no,
                    page_new,
                    stats.applied,
                    self.max_applies,
                )

            if not go_next_search_page(
                self.driver, dumper=self.dumper, pause_sec=self.pause
            ):
                stats.notes.append("stop: нет следующей страницы поиска")
                logger.info("Конец выдачи — следующей страницы нет")
                break

        logger.info(
            "Финиш: pages={} scraped={} skipped={} applied={} blocked={} errors={}",
            stats.pages,
            stats.scraped,
            stats.skipped,
            stats.applied,
            stats.blocked,
            stats.errors,
        )
        return stats

    def _process_queue(self, stats: PipelineStats, *, page_new: int) -> None:
        """Обработать NEW/ERROR из БД на текущей итерации страницы."""
        if stats.applied >= self.max_applies:
            return

        db_new = [
            v
            for v in self.store.list_by_status(VacancyStatus.NEW)
            if not str(v.hh_id).startswith("test")
        ]
        already_matched = [v for v in db_new if v.match_score is not None]
        to_match = [v for v in db_new if v.match_score is None]
        # error ретраим один раз за прогон, не на каждой странице поиска
        to_retry = [
            v
            for v in self.store.list_by_status(VacancyStatus.ERROR)
            if v.hh_id not in self._retried_error_ids
        ]
        for v in to_retry:
            self._retried_error_ids.add(v.hh_id)

        if self.limit is not None:
            to_match = to_match[: self.limit]

        logger.info(
            "Очередь: на странице NEW={} | в БД NEW={} "
            "(к матчингу={}, matched={}) | ERROR retry={}",
            page_new,
            len(db_new),
            len(to_match),
            len(already_matched),
            len(to_retry),
        )

        apply_queue = list(already_matched) + list(to_retry)
        for vac in apply_queue:
            if stats.applied >= self.max_applies:
                return
            logger.info(
                "Очередь отклика (без LLM) {} status={} score={} title={!r}",
                vac.hh_id,
                vac.status.value,
                vac.match_score,
                vac.title,
            )
            self._apply(vac.hh_id, stats)

        if stats.applied >= self.max_applies or not to_match:
            return

        logger.info(
            "К LLM-матчингу: {} вакансий (status=new, без score)", len(to_match)
        )

        # Куски только для размера запроса к API — все to_match обрабатываются
        for i in range(0, len(to_match), self._LLM_CHUNK):
            if stats.applied >= self.max_applies:
                return
            chunk = to_match[i : i + self._LLM_CHUNK]
            decisions = match_vacancies_batch(
                chunk, self._resume_text, role=self.role
            )
            for vac in chunk:
                if stats.applied >= self.max_applies:
                    return

                decision = decisions[vac.hh_id]
                self.store.mark(
                    vac.hh_id, VacancyStatus.NEW, match_score=decision.score
                )
                logger.debug(
                    "После матча {} suitable={} reason={}",
                    vac.hh_id,
                    decision.accepted,
                    decision.reason,
                )

                if not decision.accepted:
                    self._skip(vac.hh_id, decision.reason, stats)
                    continue

                self._apply(vac.hh_id, stats)

    def _skip(self, hh_id: str, reason: str, stats: PipelineStats) -> None:
        logger.info("SKIP {} — {}", hh_id, reason)
        if self.dry_run:
            self.store.mark(
                hh_id, VacancyStatus.SKIPPED, skip_reason=f"[dry_run] {reason}"
            )
            stats.skipped += 1
            stats.notes.append(f"skip {hh_id}: {reason}")
            return

        try:
            logger.debug("hide_vacancy_on_serp({}) — пока stub, только БД", hh_id)
            # hide на сайте пока не реализован — только статус в БД
            self.store.mark(hh_id, VacancyStatus.SKIPPED, skip_reason=reason)
            stats.skipped += 1
            stats.notes.append(f"skip {hh_id}: {reason}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка skip {}: {}", hh_id, exc)
            self.store.mark(
                hh_id,
                VacancyStatus.ERROR,
                skip_reason=reason,
                error_message=str(exc),
            )
            stats.errors += 1
        # паузу не делаем: скип только в БД, без кликов по UI

    def _apply(self, hh_id: str, stats: PipelineStats) -> None:
        vac = self.store.get(hh_id)
        if vac is None:
            return

        if not self.dry_run:
            rem = self.store.remaining_daily_applies()
            if rem <= 0:
                logger.warning(
                    "Дневной лимит откликов исчерпан — стоп перед {}",
                    hh_id,
                )
                stats.notes.append("stop: daily_limit mid-run")
                # чтобы внешние циклы тоже остановились
                self.max_applies = stats.applied
                return

        if self.dry_run:
            company_info = ""
            if vac.company_hh_id:
                cached = self.store.get_company(vac.company_hh_id)
                if cached is not None:
                    company_info = cached.profile_text()
            letter = generate_cover_letter(
                vacancy_title=vac.title,
                company=vac.company,
                description=vac.snippet or "",
                resume_text=self._resume_text,
                company_info=company_info or None,
            )
            self.store.mark(
                hh_id,
                VacancyStatus.NEW,
                cover_letter=f"[dry_run would_apply]\n{letter}",
                match_score=vac.match_score,
            )
            stats.applied += 1
            stats.notes.append(f"would_apply(dry) {hh_id}: {vac.title}")
            logger.info("DRY would_apply {} — {}", hh_id, vac.title)
            logger.debug("DRY cover letter preview:\n{}", letter[:500])
            return

        try:
            resolve_captcha_if_present(
                self.driver, context=f"перед вакансией {hh_id}"
            )
            logger.info("APPLY open vacancy tab {} {}", hh_id, vac.url)
            with vacancy_tab(
                self.driver,
                vac.url,
                dumper=self.dumper,
                label=f"vacancy_{hh_id}",
                pause_sec=self.pause,
            ):
                resolve_captcha_if_present(
                    self.driver, context=f"на странице вакансии {hh_id}"
                )
                page = scrape_vacancy_page(self.driver, dumper=self.dumper)
                logger.debug(
                    "vacancy page title={!r} desc_len={}",
                    page.title,
                    len(page.description or ""),
                )
                closed = vacancy_closed_reason(self.driver)
                if closed:
                    self._mark_inactive(hh_id, closed, stats, description=page.description)
                    return

                # Уже откликались на hh: кнопка «Чат» / «Отказ» / «Собеседование»
                resp_state = detect_response_button_state(self.driver)
                if resp_state.already_responded:
                    logger.info(
                        "ALREADY on hh {} — кнопка {!r}, помечаем applied",
                        hh_id,
                        resp_state.label,
                    )
                    self.store.mark(
                        hh_id,
                        VacancyStatus.APPLIED,
                        description=page.description,
                        skip_reason=f"already_on_hh: {resp_state.label}",
                        error_message="",
                    )
                    stats.notes.append(
                        f"already_on_hh {hh_id}: {resp_state.label}"
                    )
                    # не тратим apply-limit — нового отклика не было
                    return

                company = get_or_fetch_company(
                    self.driver,
                    self.store,
                    dumper=self.dumper,
                    pause_sec=self.pause,
                )
                company_name = (
                    (company.name if company else None) or vac.company
                )
                company_hh_id = company.hh_id if company else vac.company_hh_id
                company_info = company.profile_text() if company else ""
                letter = generate_cover_letter(
                    vacancy_title=page.title or vac.title,
                    company=company_name,
                    description=page.description,
                    resume_text=self._resume_text,
                    company_info=company_info or None,
                )
                logger.debug(
                    "cover letter len={} company_info_len={}",
                    len(letter),
                    len(company_info),
                )
                result = actions.apply_with_letter(
                    self.driver,
                    letter,
                    pause_sec=self.pause,
                    dumper=self.dumper,
                    resume_text=self._resume_text,
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
                    self.store.mark(
                        hh_id,
                        VacancyStatus.APPLIED,
                        error_message="",
                        skip_reason="",
                        **mark_kw,
                    )
                    stats.applied += 1
                    tag = (
                        "applied+test"
                        if result.detail == "with_test"
                        else "applied+letter"
                    )
                    stats.notes.append(f"{tag} {hh_id}: {vac.title}")
                    logger.info("APPLY ok ({}) {}", result.detail or "letter", hh_id)
                elif result.outcome == ApplyOutcome.APPLIED_NO_LETTER:
                    self.store.mark(
                        hh_id,
                        VacancyStatus.APPLIED,
                        error_message="отклик без UI письма",
                        **mark_kw,
                    )
                    stats.applied += 1
                    stats.notes.append(f"applied(no letter UI) {hh_id}")
                else:
                    self.store.mark(
                        hh_id,
                        VacancyStatus.ERROR,
                        error_message=result.detail or result.outcome.value,
                        **mark_kw,
                    )
                    stats.errors += 1
                    stats.notes.append(f"apply unknown {hh_id}: {result.detail}")
        except AlreadyResponded as exc:
            logger.info("ALREADY on hh {}: {}", hh_id, exc.label)
            if self.dumper is not None:
                try:
                    self.dumper.save(
                        self.driver, label=f"already_responded_{hh_id}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            self.store.mark(
                hh_id,
                VacancyStatus.APPLIED,
                skip_reason=f"already_on_hh: {exc.label}",
            )
            stats.notes.append(f"already_on_hh {hh_id}: {exc.label}")
        except ResponseTestRequired as exc:
            logger.warning("BLOCKED (test) {}: {}", hh_id, exc)
            if self.dumper is not None:
                try:
                    self.dumper.save(
                        self.driver, label=f"blocked_test_{hh_id}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            self.store.mark(
                hh_id,
                VacancyStatus.BLOCKED,
                skip_reason="test_required",
                error_message=str(exc),
            )
            stats.blocked += 1
            stats.notes.append(f"blocked(test) {hh_id}")
        except LetterSubmitUnavailable as exc:
            logger.warning("Письмо не отправлено (submit UI): {} — {}", hh_id, exc)
            if self.dumper is not None:
                try:
                    self.dumper.save(
                        self.driver, label=f"letter_submit_fail_{hh_id}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            self.store.mark(
                hh_id,
                VacancyStatus.ERROR,
                error_message=str(exc),
            )
            stats.errors += 1
            stats.notes.append(f"letter_submit_fail {hh_id}")
        except CaptchaTimeout as exc:
            logger.error("CAPTCHA timeout {}: {}", hh_id, exc)
            self.store.mark(
                hh_id,
                VacancyStatus.BLOCKED,
                skip_reason="captcha",
                error_message=str(exc),
            )
            stats.blocked += 1
            stats.notes.append(f"blocked(captcha) {hh_id}")
            raise
        except Exception as exc:  # noqa: BLE001
            closed = None
            try:
                closed = vacancy_closed_reason(self.driver)
            except Exception:  # noqa: BLE001
                closed = None
            if closed:
                self._mark_inactive(hh_id, closed, stats)
                return
            logger.exception("Ошибка apply {}: {}", hh_id, exc)
            if self.dumper is not None:
                try:
                    self.dumper.save(self.driver, label=f"apply_error_{hh_id}")
                except Exception:  # noqa: BLE001
                    pass
            self.store.mark(
                hh_id, VacancyStatus.ERROR, error_message=str(exc)
            )
            stats.errors += 1
            stats.notes.append(f"error {hh_id}: {exc}")
        time.sleep(self.pause)

    def _mark_inactive(
        self,
        hh_id: str,
        reason: str,
        stats: PipelineStats,
        *,
        description: str | None = None,
    ) -> None:
        """Архив или закрытый доступ: статус inactive, больше не открываем."""
        label = "архив" if reason == "archived" else "доступ ограничен"
        logger.info("INACTIVE {} — {}", hh_id, label)
        self.store.mark(
            hh_id,
            VacancyStatus.INACTIVE,
            skip_reason=reason,
            error_message="",
            description=description,
        )
        stats.skipped += 1
        stats.notes.append(f"inactive({reason}) {hh_id}")
