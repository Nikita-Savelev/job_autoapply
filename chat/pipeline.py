"""Оркестрация чата: мониторинг верха списка + полный sweep.

Отладка как в apply: HH_HIGHLIGHT + HH_PAUSE_SEC (2.5).
По умолчанию — monitor: первые N страниц списка, open только при новом
чате или смене превью; ответ с typing delay.
`--force` — полный прогон списка. `--manual-send` — Отправить жмёт человек.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from loguru import logger
from selenium.webdriver.remote.webdriver import WebDriver

from chat.compose import compose_reply
from chat.models import (
    ChatActionKind,
    ChatDraft,
    ChatSource,
    ChatThread,
    ChatThreadStatus,
    EscalateReason,
)
from config import pause_between_actions_sec


class SweepStopReason(StrEnum):
    STREAK = "streak_awaiting_them"
    LIST_END = "list_end"
    LIMIT = "limit"
    SINGLE = "single_chat"
    EMPTY = "empty"
    MONITOR_PAGES = "monitor_pages"


@dataclass
class SweepConfig:
    """Параметры прогона списка (monitor или полный sweep)."""

    stop_awaiting_them_streak: int = 0  # 0 = не стопать; иначе N подряд awaiting_them
    sleep_after_sweep_sec: float = 120.0
    sweep_interval_sec: float = 600.0
    pause_between_chats_sec: float = 2.5
    list_scroll_pause_sec: float = 2.5
    max_chats_per_sweep: int = 0  # 0 = без лимита, до конца списка
    dry_run: bool = False  # True = не писать БД / не fill
    manual_send: bool = False  # False = бот сам жмёт «Отправить»
    use_typing_delay: bool = True
    persist: bool = True
    # после каждого обработанного чата — Enter в терминале (админка/логи)
    # по умолчанию выкл. (лайв); включить: --step
    step_enter: bool = False
    # True = полный sweep без stop по streak/status; превью без изменений всё равно skip
    force_full_sweep: bool = False
    # monitor: сколько «страниц» списка (viewport + скролл) смотреть сверху
    monitor_pages: int = 3


DEFAULT_SWEEP = SweepConfig()


def _wait_step_enter(thread: ChatThread, *, cfg: SweepConfig) -> None:
    """Пауза после чата: смотреть админку и логи."""
    if not cfg.step_enter:
        return
    status = thread.status.value if thread.status else "?"
    title = (thread.title or "").strip() or "—"
    prompt = (
        f"Чат {thread.chat_id} обработан | status={status} | {title}\n"
        f"https://hh.ru/chat/{thread.chat_id}\n"
        f"Проверь админку/логи, затем Enter → следующий чат"
    )
    try:
        input(f"{prompt}\n→ ")
    except EOFError:
        logger.warning("stdin закрыт — step_enter пропущен")
    except KeyboardInterrupt:
        logger.info("step_enter: прервано пользователем")
        raise


def _thread_after_handle(fallback: ChatThread) -> ChatThread:
    """Актуальный статус из БД после обработки (для промпта Enter)."""
    if _RUN_STORE is None:
        return fallback
    try:
        loaded = _RUN_STORE.get_thread(fallback.chat_id)
        return loaded or fallback
    except Exception as exc:  # noqa: BLE001
        logger.debug("step_enter: не перечитал тред: {}", exc)
        return fallback


# Контекст прогона (store / резюме / dumper) — выставляется run_chat
_RUN_STORE = None
_RUN_RESUME = ""
_RUN_DUMPER = None


def set_run_context(*, store=None, resume_text: str = "", dumper=None) -> None:
    global _RUN_STORE, _RUN_RESUME, _RUN_DUMPER
    _RUN_STORE = store
    _RUN_RESUME = resume_text or ""
    _RUN_DUMPER = dumper


@dataclass
class ChatCycleResult:
    seen: int = 0
    created: int = 0
    awaiting_us: int = 0
    drafted: int = 0
    sent: int = 0
    verified: int = 0
    escalated: int = 0
    skipped: int = 0
    paused: int = 0
    streak_hits: int = 0
    stop_reason: SweepStopReason | None = None
    errors: list[str] = field(default_factory=list)


def is_awaiting_recruiter(thread: ChatThread) -> bool:
    """Уже ответили и ждём HR: счётчик стопа sweep."""
    if thread.bot_paused:
        return False
    if thread.status == ChatThreadStatus.AWAITING_THEM:
        return True
    return bool(thread.messages) and not thread.needs_our_reply()


def run_chat_cycle(
    driver: WebDriver | None,
    *,
    dry_run: bool = True,
    chat_id: str | None = None,
    cfg: SweepConfig | None = None,
) -> ChatCycleResult:
    cfg = cfg or DEFAULT_SWEEP
    pause = pause_between_actions_sec()
    cfg = SweepConfig(
        **{
            **cfg.__dict__,
            "dry_run": dry_run,
            "pause_between_chats_sec": pause,
            "list_scroll_pause_sec": pause,
        }
    )
    result = ChatCycleResult()

    if driver is None:
        logger.info(
            "chat cycle: нет driver (dry_run={}); streak={}, manual_send={}",
            dry_run,
            cfg.stop_awaiting_them_streak,
            cfg.manual_send,
        )
        result.stop_reason = SweepStopReason.EMPTY
        return result

    if chat_id:
        result.stop_reason = SweepStopReason.SINGLE
        _process_chat_id(driver, chat_id, result=result, cfg=cfg)
        return result

    if cfg.force_full_sweep:
        return run_list_sweep(driver, cfg=cfg, result=result)
    return run_list_monitor(driver, cfg=cfg, result=result)


def run_list_monitor(
    driver: WebDriver,
    *,
    cfg: SweepConfig = DEFAULT_SWEEP,
    result: ChatCycleResult | None = None,
) -> ChatCycleResult:
    """Мониторинг: первые N страниц списка; open при новом чате или смене превью."""
    from hh import chat_ui
    from hh.highlight import show_banner

    result = result or ChatCycleResult()
    pages = max(1, int(cfg.monitor_pages or 3))

    show_banner(driver, f"Monitor: первые {pages} стр.")
    chat_ui.open_chat_list(
        driver,
        dumper=_RUN_DUMPER,
        pause_sec=cfg.list_scroll_pause_sec,
    )

    cards = _collect_list_pages(
        driver,
        pages=pages,
        pause_sec=cfg.list_scroll_pause_sec,
    )
    logger.info(
        "monitor: собрано {} чатов с первых {} стр.",
        len(cards),
        pages,
    )

    for card in cards:
        result.seen += 1

        prev_preview = None
        if _RUN_STORE is not None:
            try:
                row = _RUN_STORE.get_thread(card.chat_id)
                if row is not None:
                    prev_preview = row.last_message_preview
            except Exception:  # noqa: BLE001
                prev_preview = None

        known = _load_or_create_thread(card, result=result)

        # превью не менялось → не открываем
        if prev_preview is not None and _previews_match(
            card.last_message_preview, prev_preview
        ):
            logger.debug(
                "chat {}: preview без изменений — skip ({!r})",
                known.chat_id,
                (card.last_message_preview or "")[:60],
            )
            result.skipped += 1
            continue

        reason = "новый" if prev_preview is None else "превью изменилось"
        logger.info(
            "chat {}: {} — open ({!r})",
            known.chat_id,
            reason,
            (card.last_message_preview or "")[:60],
        )
        show_banner(driver, f"Monitor: {known.chat_id} ({reason})")

        handled = _open_and_handle(driver, known, result=result, cfg=cfg)
        _wait_step_enter(_thread_after_handle(handled), cfg=cfg)
        time.sleep(cfg.pause_between_chats_sec)

    result.stop_reason = SweepStopReason.MONITOR_PAGES
    return result


def _collect_list_pages(
    driver: WebDriver,
    *,
    pages: int,
    pause_sec: float = 2.5,
) -> list[ChatThread]:
    """Уникальные карточки с первых N viewport-страниц (сверху вниз)."""
    from hh import chat_ui

    seen: dict[str, ChatThread] = {}
    order: list[str] = []

    for page_i in range(max(1, pages)):
        for card in chat_ui.list_threads(driver):
            if card.chat_id in seen:
                # обновить превью, если DOM уже показал новое
                seen[card.chat_id] = card
                continue
            seen[card.chat_id] = card
            order.append(card.chat_id)
        if page_i + 1 >= pages:
            break
        if not _scroll_chat_list(driver, pause_sec=pause_sec):
            break

    return [seen[cid] for cid in order]


def run_list_sweep(
    driver: WebDriver,
    *,
    cfg: SweepConfig = DEFAULT_SWEEP,
    result: ChatCycleResult | None = None,
) -> ChatCycleResult:
    from hh import chat_ui
    from hh.highlight import show_banner

    result = result or ChatCycleResult()
    streak = 0
    seen_ids: set[str] = set()

    show_banner(driver, "Sweep: список чатов")
    chat_ui.open_chat_list(
        driver,
        dumper=_RUN_DUMPER,
        pause_sec=cfg.list_scroll_pause_sec,
    )

    while True:
        cards = chat_ui.list_threads(driver)
        batch = [c for c in cards if c.chat_id not in seen_ids]
        if not batch:
            if not _scroll_chat_list(driver, pause_sec=cfg.list_scroll_pause_sec):
                result.stop_reason = SweepStopReason.LIST_END
                break
            continue

        for card in batch:
            seen_ids.add(card.chat_id)
            result.seen += 1

            prev_preview = None
            if _RUN_STORE is not None:
                try:
                    row = _RUN_STORE.get_thread(card.chat_id)
                    if row is not None:
                        prev_preview = row.last_message_preview
                except Exception:  # noqa: BLE001
                    prev_preview = None

            known = _load_or_create_thread(card, result=result)

            from chat.classify import is_hh_reject_badge

            # превью не менялось → в чат не заходим (даже с --force)
            if prev_preview is not None and _previews_match(
                card.last_message_preview, prev_preview
            ):
                logger.info(
                    "chat {}: preview без изменений — skip open ({!r})",
                    known.chat_id,
                    (card.last_message_preview or "")[:60],
                )
                continue

            if not cfg.force_full_sweep:
                # уже синкнутый отказ/собес — не открываем повторно
                if (
                    known.status
                    in (ChatThreadStatus.REJECTED, ChatThreadStatus.INTERVIEW)
                    and known.source != ChatSource.UNKNOWN
                ):
                    logger.debug(
                        "chat {}: {} — skip (уже в БД)",
                        known.chat_id,
                        known.status.value,
                    )
                    continue
                # старые closed+Отказ → rejected
                if (
                    known.status == ChatThreadStatus.CLOSED
                    and is_hh_reject_badge(known.last_message_preview)
                    and known.source != ChatSource.UNKNOWN
                ):
                    known.status = ChatThreadStatus.REJECTED
                    if cfg.persist and not cfg.dry_run and _RUN_STORE is not None:
                        _RUN_STORE.upsert_thread(known)
                    continue

                if is_awaiting_recruiter(known) and not known.needs_our_reply():
                    streak += 1
                    result.streak_hits = streak
                    logger.info(
                        "chat {}: awaiting_them streak={}/{}",
                        known.chat_id,
                        streak,
                        cfg.stop_awaiting_them_streak or "∞",
                    )
                    show_banner(
                        driver,
                        f"Ждёт HR streak {streak}",
                    )
                    if (
                        cfg.stop_awaiting_them_streak > 0
                        and streak >= cfg.stop_awaiting_them_streak
                    ):
                        result.stop_reason = SweepStopReason.STREAK
                        show_banner(driver, "Stop: awaiting_them streak")
                        logger.info("sweep stop: {} подряд ждут HR", streak)
                        return result
                    continue

                streak = 0
            else:
                streak = 0

            handled = _open_and_handle(driver, known, result=result, cfg=cfg)
            _wait_step_enter(_thread_after_handle(handled), cfg=cfg)
            time.sleep(cfg.pause_between_chats_sec)

        if not _scroll_chat_list(driver, pause_sec=cfg.list_scroll_pause_sec):
            result.stop_reason = result.stop_reason or SweepStopReason.LIST_END
            break

    if result.stop_reason is None:
        result.stop_reason = SweepStopReason.LIST_END
    return result


def _norm_preview(text: str | None) -> str:
    t = (text or "").replace("\xa0", " ").replace("&nbsp;", " ")
    return " ".join(t.split()).strip().lower()


def _previews_match(list_preview: str | None, db_preview: str | None) -> bool:
    """Превью списка совпадает с сохранённым в БД (с учётом усечения HH)."""
    a = _norm_preview(list_preview)
    b = _norm_preview(db_preview)
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    # список часто короче полного текста из треда
    n = min(len(a), len(b), 120)
    return n >= 12 and a[:n] == b[:n]


def _scroll_chat_list(driver: WebDriver, *, pause_sec: float = 2.5) -> bool:
    from hh.highlight import show_banner

    show_banner(driver, "Скролл списка чатов")
    try:
        scrolled = driver.execute_script(
            """
            const root = document.querySelector("[data-qa='chatik-layout']");
            if (!root) return false;
            const candidates = root.querySelectorAll(
              "[class*='scroll'], [class*='Scroll'], [data-qa*='chat']"
            );
            let el = null;
            for (const c of candidates) {
              if (c.scrollHeight > c.clientHeight + 40) { el = c; break; }
            }
            if (!el) {
              window.scrollBy(0, 400);
              return true;
            }
            const before = el.scrollTop;
            el.scrollTop = before + Math.max(200, el.clientHeight * 0.6);
            return el.scrollTop !== before;
            """
        )
        time.sleep(pause_sec)
        return bool(scrolled)
    except Exception as exc:
        logger.warning("chat list scroll failed: {}", exc)
        return False


def _load_or_create_thread(
    card: ChatThread,
    *,
    result: ChatCycleResult,
) -> ChatThread:
    store = _RUN_STORE
    if store is None:
        return card
    existing = store.get_thread(card.chat_id)
    if existing is None:
        card.status = ChatThreadStatus.NEW
        store.upsert_thread(card)
        result.created += 1
        logger.info("chat {}: создан в БД", card.chat_id)
        return card
    # мержим карточку списка с БД
    existing.title = card.title or existing.title
    existing.subtitle = card.subtitle or existing.subtitle
    existing.last_message_preview = (
        card.last_message_preview or existing.last_message_preview
    )
    existing.unread = card.unread
    if existing.reactivate_from_closed_if_needed():
        logger.info("chat {}: closed→awaiting_us (список)", existing.chat_id)
        store.upsert_thread(existing)
    return existing


def _persist(thread: ChatThread) -> None:
    store = _RUN_STORE
    if store is None:
        return
    store.upsert_thread(thread)
    if thread.messages:
        store.append_messages(thread.chat_id, thread.messages)
    # после messages last_preview может стать текстом пузыря —
    # для skip оставляем превью/бейдж из списка (Отказ, Собеседование, …)
    if thread.last_message_preview:
        store.upsert_thread(thread)


def _vacancy_context(thread: ChatThread) -> tuple[str, str, str]:
    """resume уже в _RUN_RESUME; вернуть title, description, company_profile."""
    title = ""
    desc = ""
    company = thread.company or ""
    if not thread.vacancy_hh_id:
        return title, desc, company
    try:
        from config import pg_conninfo
        from db.store import VacancyStore

        with VacancyStore(pg_conninfo()) as vs:
            v = vs.get(thread.vacancy_hh_id)
            if v:
                title = v.title or ""
                desc = v.description or ""
                company = company or (v.company or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("vacancy context {}: {}", thread.vacancy_hh_id, exc)
    return title, desc, company


def _notify_escalate(thread: ChatThread, draft: ChatDraft) -> None:
    try:
        from notify.telegram import send_telegram

        reason = (draft.escalate_reason or EscalateReason.OTHER).value
        preview = (thread.last_inbound_text() or "")[:400]
        send_telegram(
            f"Чат HH нужен ты\n"
            f"id={thread.chat_id}\n"
            f"{thread.title}\n"
            f"reason={reason}\n"
            f"https://hh.ru/chat/{thread.chat_id}\n"
            f"---\n{preview}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("TG escalate fail: {}", exc)


def _open_and_handle(
    driver: WebDriver,
    thread: ChatThread,
    *,
    result: ChatCycleResult,
    cfg: SweepConfig,
) -> ChatThread:
    from hh import chat_ui
    from hh.highlight import show_banner
    from chat.classify import (
        ChatIntent,
        classify_thread,
        is_hh_interview_badge,
        is_hh_reject_badge,
        looks_like_reject,
    )

    pause = cfg.pause_between_chats_sec
    try:
        chat_ui.open_chat_thread(
            driver,
            thread.chat_id,
            dumper=_RUN_DUMPER,
            pause_sec=pause,
        )
        live = chat_ui.read_thread(driver, chat_id=thread.chat_id)
        chat_ui.dump_thread(
            driver, dumper=_RUN_DUMPER, label=f"chat_{thread.chat_id}_open",
            chat_id=thread.chat_id,
        )
        # список: title=вакансия, subtitle=компания; шапка: employer/vacancy ids
        live.title = thread.title or live.title or live.company or ""
        live.subtitle = thread.subtitle or live.subtitle or live.company
        # компания — subtitle списка / шапки, не путать с названием вакансии
        live.company = (
            thread.subtitle
            or live.company
            or thread.company
        )
        if live.company and live.title and live.company == live.title:
            live.company = thread.subtitle or live.company
        live.vacancy_hh_id = live.vacancy_hh_id or thread.vacancy_hh_id
        live.company_hh_id = live.company_hh_id or thread.company_hh_id
        live.url = live.url or thread.url
        live.bot_paused = thread.bot_paused
        live.status = thread.status
        live.unread = thread.unread or live.unread
        inferred = live.infer_source()
        if inferred != ChatSource.UNKNOWN:
            live.source = inferred
        elif thread.source != ChatSource.UNKNOWN:
            live.source = thread.source
        else:
            live.source = ChatSource.UNKNOWN
        # бейдж «Отказ» из списка важнее превью последнего пузыря
        if thread.last_message_preview:
            live.last_message_preview = thread.last_message_preview

        if live.reactivate_from_closed_if_needed():
            logger.info(
                "chat {}: closed → awaiting_us (новое входящее/unread)",
                live.chat_id,
            )
            show_banner(driver, "closed → снова активен")

        # сначала сохраняем историю (в т.ч. при отказе)
        if cfg.persist and not cfg.dry_run:
            _persist(live)

        intent = classify_thread(live)
        if intent == ChatIntent.REJECT:
            live.status = ChatThreadStatus.REJECTED
            live.bot_paused = False
            live.paused_reason = None
            live.last_message_preview = "Отказ"
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.skipped += 1
            show_banner(driver, "Отказ → rejected")
            logger.info(
                "chat {}: отказ → rejected (msgs={})",
                live.chat_id,
                len(live.messages),
            )
            return live

        if intent == ChatIntent.INTERVIEW:
            live.status = ChatThreadStatus.INTERVIEW
            live.bot_paused = True
            live.paused_reason = EscalateReason.INTERVIEW.value
            if not is_hh_interview_badge(live.last_message_preview):
                live.last_message_preview = "Собеседование"
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.escalated += 1
            show_banner(driver, "Собеседование → interview + TG")
            logger.info(
                "chat {}: собеседование → interview (msgs={})",
                live.chat_id,
                len(live.messages),
            )
            _notify_escalate(
                live,
                ChatDraft(
                    kind=ChatActionKind.ESCALATE,
                    reason="intent=interview",
                    escalate_reason=EscalateReason.INTERVIEW,
                ),
            )
            return live

        # read-only мог снова открыться
        if (
            live.bot_paused
            and live.paused_reason == "composer_readonly"
            and chat_ui.composer_is_writable(driver)
        ):
            live.bot_paused = False
            live.paused_reason = None
            live.status = ChatThreadStatus.AWAITING_US
            logger.info("chat {}: composer снова доступен — снимаем pause", live.chat_id)

        if live.bot_paused:
            result.paused += 1
            show_banner(driver, "bot_paused — skip")
            return live

        if intent == ChatIntent.NO_REPLY:
            live.status = ChatThreadStatus.AWAITING_THEM
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.skipped += 1
            show_banner(driver, "Финал скрининга — не отвечаем")
            logger.info("chat {}: no_reply (финал) → awaiting_them", live.chat_id)
            return live

        writable = chat_ui.composer_is_writable(driver)
        if not writable:
            chat_ui.dump_thread(
                driver,
                dumper=_RUN_DUMPER,
                label=f"chat_{thread.chat_id}_readonly",
                chat_id=thread.chat_id,
            )
            last_in = live.last_inbound_text() or ""
            if looks_like_reject(last_in) or is_hh_reject_badge(
                live.last_message_preview
            ):
                live.status = ChatThreadStatus.REJECTED
                live.bot_paused = False
                live.paused_reason = None
                live.last_message_preview = "Отказ"
                show_banner(driver, "Отказ (read-only) → rejected")
                logger.info("chat {}: отказ + readonly → rejected", live.chat_id)
            elif is_hh_interview_badge(live.last_message_preview):
                live.status = ChatThreadStatus.INTERVIEW
                live.bot_paused = True
                live.paused_reason = EscalateReason.INTERVIEW.value
                show_banner(driver, "Собес (read-only) → interview")
                logger.info("chat {}: interview badge + readonly", live.chat_id)
            elif not live.needs_our_reply():
                live.status = ChatThreadStatus.AWAITING_THEM
                show_banner(driver, "Чат read-only — ждём")
            else:
                # их сообщение есть, ответить нельзя — мониторим, не генерим текст
                live.status = ChatThreadStatus.NEEDS_HUMAN
                live.bot_paused = True
                live.paused_reason = "composer_readonly"
                show_banner(driver, "Чат read-only — не пишем")
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            if live.status == ChatThreadStatus.INTERVIEW:
                result.escalated += 1
                _notify_escalate(
                    live,
                    ChatDraft(
                        kind=ChatActionKind.ESCALATE,
                        reason="interview_readonly",
                        escalate_reason=EscalateReason.INTERVIEW,
                    ),
                )
            else:
                result.skipped += 1
            logger.info(
                "chat {}: composer недоступен → status={}",
                live.chat_id,
                live.status.value,
            )
            return live

        if not live.needs_our_reply():
            live.status = ChatThreadStatus.AWAITING_THEM
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.skipped += 1
            show_banner(driver, "Уже ждём HR")
            return live

        result.awaiting_us += 1
        live.status = ChatThreadStatus.AWAITING_US
        v_title, v_desc, company = _vacancy_context(live)
        draft = process_thread(
            live,
            dry_run=cfg.dry_run,
            resume_text=_RUN_RESUME,
            vacancy_description=v_desc,
            company_profile=company,
            vacancy_title=v_title,
        )

        if draft.kind == ChatActionKind.SKIP:
            reason = (draft.reason or "").lower()
            is_reject = (
                "intent=reject" in reason
                or classify_thread(live) == ChatIntent.REJECT
            )
            if is_reject:
                live.status = ChatThreadStatus.REJECTED
                live.last_message_preview = "Отказ"
                show_banner(driver, "Отказ → rejected")
            else:
                live.status = ChatThreadStatus.AWAITING_THEM
                show_banner(driver, f"skip: {draft.reason}")
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.skipped += 1
            logger.info("chat {}: skip ({}) → {}", live.chat_id, draft.reason, live.status.value)
            return live

        if draft.kind == ChatActionKind.ESCALATE:
            result.escalated += 1
            esc = draft.escalate_reason or EscalateReason.OTHER
            show_banner(driver, f"Escalate: {esc}")
            if esc == EscalateReason.INTERVIEW:
                live.status = ChatThreadStatus.INTERVIEW
                live.last_message_preview = (
                    live.last_message_preview
                    if is_hh_interview_badge(live.last_message_preview)
                    else "Собеседование"
                )
            else:
                live.status = ChatThreadStatus.NEEDS_HUMAN
            live.bot_paused = True
            live.paused_reason = esc.value
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            _notify_escalate(live, draft)
            return live

        if draft.kind != ChatActionKind.REPLY or not draft.text:
            result.skipped += 1
            return live

        result.drafted += 1
        if cfg.dry_run:
            logger.info(
                "dry-run chat {}: delay={:.0f}s text[:200]={!r}",
                live.chat_id,
                draft.delay_sec,
                draft.text[:200],
            )
            show_banner(driver, "dry-run: текст в логе, не вставляю")
            return live

        if cfg.use_typing_delay and draft.delay_sec > 0:
            from chat.delay import remaining_reply_delay

            last_in = None
            for msg in reversed(live.messages):
                if msg.direction.value == "in":
                    last_in = msg
                    break
            wait = remaining_reply_delay(
                draft.delay_sec,
                last_in.sent_at if last_in else None,
            )
            if wait <= 0:
                show_banner(driver, "Пауза набора: 0 (HR давно ждал)")
                logger.info(
                    "chat {}: skip typing delay (planned={:.0f}s, inbound_at={})",
                    live.chat_id,
                    draft.delay_sec,
                    last_in.sent_at if last_in else None,
                )
            else:
                show_banner(driver, f"Пауза «набора» {wait:.0f}с")
                logger.info(
                    "chat {}: typing delay {:.0f}s (planned={:.0f}s, inbound_at={})",
                    live.chat_id,
                    wait,
                    draft.delay_sec,
                    last_in.sent_at if last_in else None,
                )
                time.sleep(wait)

        if not chat_ui.composer_is_writable(driver):
            show_banner(driver, "Composer пропал — не вставляем")
            live.status = ChatThreadStatus.NEEDS_HUMAN
            live.bot_paused = True
            live.paused_reason = "composer_readonly"
            if cfg.persist and not cfg.dry_run:
                _persist(live)
            result.skipped += 1
            return live

        show_banner(driver, "Вставляю и отправляю ответ")
        logger.info("chat {} draft:\n{}", live.chat_id, draft.text)
        if cfg.manual_send:
            if not chat_ui.fill_message(driver, draft.text, pause_sec=pause):
                result.errors.append(f"{live.chat_id}: fill failed")
                live.status = ChatThreadStatus.ERROR
                chat_ui.dump_thread(
                    driver,
                    dumper=_RUN_DUMPER,
                    label=f"chat_{thread.chat_id}_fill_fail",
                    chat_id=thread.chat_id,
                )
                if cfg.persist:
                    _persist(live)
                return live
            chat_ui.wait_manual_continue(
                prompt=(
                    f"Чат {live.chat_id}: нажми «Отправить» в браузере "
                    f"(бот кнопку не жмёт)."
                )
            )
        else:
            if not chat_ui.send_message(driver, draft.text, pause_sec=pause):
                result.errors.append(f"{live.chat_id}: send failed")
                live.status = ChatThreadStatus.ERROR
                chat_ui.dump_thread(
                    driver,
                    dumper=_RUN_DUMPER,
                    label=f"chat_{thread.chat_id}_send_fail",
                    chat_id=thread.chat_id,
                )
                if cfg.persist:
                    _persist(live)
                return live

        time.sleep(pause)
        if not chat_ui.verify_outbound_delivered(
            driver, draft.text, chat_id=live.chat_id
        ):
            result.errors.append(f"{live.chat_id}: outbound not verified")
            live.status = ChatThreadStatus.ERROR
            chat_ui.dump_thread(
                driver,
                dumper=_RUN_DUMPER,
                label=f"chat_{thread.chat_id}_verify_fail",
                chat_id=thread.chat_id,
            )
            if cfg.persist:
                _persist(live)
            return live

        live = chat_ui.read_thread(driver, chat_id=live.chat_id)
        live.vacancy_hh_id = thread.vacancy_hh_id or live.vacancy_hh_id
        chat_ui.dump_thread(
            driver, dumper=_RUN_DUMPER, label=f"chat_{thread.chat_id}_sent",
            chat_id=thread.chat_id,
        )
        result.verified += 1
        result.sent += 1

        if live.needs_our_reply():
            live.status = ChatThreadStatus.AWAITING_US
            show_banner(driver, "OK send + новое входящее → awaiting_us")
            logger.info(
                "chat {}: sent OK, но уже есть новое IN → awaiting_us",
                live.chat_id,
            )
        else:
            live.status = ChatThreadStatus.AWAITING_THEM
            show_banner(driver, "OK → awaiting_them")
            logger.info("chat {}: status → awaiting_them (verified)", live.chat_id)

        if cfg.persist:
            _persist(live)
        return live

    except Exception as exc:
        logger.exception("chat {} handle failed", thread.chat_id)
        result.errors.append(f"{thread.chat_id}: {exc}")
        try:
            from hh import chat_ui as _cui

            _cui.dump_thread(
                driver,
                dumper=_RUN_DUMPER,
                label=f"chat_{thread.chat_id}_error",
                chat_id=thread.chat_id,
            )
        except Exception:
            pass
        return thread
    finally:
        # снять «непрочитанное» в списке HH
        try:
            chat_ui.scroll_thread_to_latest(
                driver, pause_sec=min(0.8, pause)
            )
        except Exception:  # noqa: BLE001
            pass

    return live



def _process_chat_id(
    driver: WebDriver,
    chat_id: str,
    *,
    result: ChatCycleResult,
    cfg: SweepConfig,
) -> None:
    card = ChatThread(chat_id=chat_id, url=f"https://hh.ru/chat/{chat_id}")
    card = _load_or_create_thread(card, result=result)
    result.seen = 1
    handled = _open_and_handle(driver, card, result=result, cfg=cfg)
    _wait_step_enter(_thread_after_handle(handled), cfg=cfg)


def process_thread(
    thread: ChatThread,
    *,
    dry_run: bool = False,
    resume_text: str = "",
    vacancy_description: str = "",
    company_profile: str = "",
    vacancy_title: str = "",
) -> ChatDraft:
    if thread.bot_paused:
        logger.info("chat {}: bot_paused — ждём их следующее сообщение", thread.chat_id)
        return ChatDraft(kind=ChatActionKind.SKIP, reason="bot_paused")

    if not thread.needs_our_reply():
        logger.info("chat {}: последнее сообщение наше — skip", thread.chat_id)
        return ChatDraft(kind=ChatActionKind.SKIP, reason="last_is_ours")

    draft = compose_reply(
        thread,
        resume_text=resume_text,
        vacancy_description=vacancy_description,
        company_profile=company_profile,
        vacancy_title=vacancy_title,
        dry_run=dry_run,
    )
    if draft.kind == ChatActionKind.REPLY:
        logger.info(
            "chat {}: reply delay={:.0f}s len={} ({})",
            thread.chat_id,
            draft.delay_sec,
            len(draft.text),
            draft.reason,
        )
    elif draft.kind == ChatActionKind.ESCALATE:
        logger.info(
            "chat {}: escalate → TG ({}) {}",
            thread.chat_id,
            (draft.escalate_reason or EscalateReason.OTHER).value,
            draft.reason,
        )
        thread.bot_paused = True
        esc = draft.escalate_reason or EscalateReason.OTHER
        thread.paused_reason = esc.value
        thread.status = (
            ChatThreadStatus.INTERVIEW
            if esc == EscalateReason.INTERVIEW
            else ChatThreadStatus.NEEDS_HUMAN
        )
    else:
        logger.info("chat {}: skip ({})", thread.chat_id, draft.reason)
    return draft
