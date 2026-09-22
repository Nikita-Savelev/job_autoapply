"""Админка: отклики, скипы, проблемные, компании и все вакансии."""

from __future__ import annotations

import json

from django.contrib import admin
from django.db import models
from django.forms.widgets import Textarea
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from core.models import (
    AppliedVacancy,
    ChatAction,
    ChatMessage,
    ChatThread,
    Company,
    ProblemVacancy,
    SkippedVacancy,
    TestedVacancy,
    Vacancy,
)


def _format_test_qa_html(raw: str | None) -> str:
    """Человекочитаемый HTML блок вопрос → ответ."""
    if not raw or not str(raw).strip():
        return "—"
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return format_html(
            '<pre style="white-space:pre-wrap;max-width:900px">{}</pre>',
            raw,
        )
    if not isinstance(items, list) or not items:
        return "—"

    blocks: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", 0)) + 1
        kind = escape(str(item.get("kind") or ""))
        question = escape(str(item.get("question") or "").strip())
        selected = item.get("selected") or []
        if isinstance(selected, str):
            selected = [selected]
        text = str(item.get("text") or "").strip()
        use_custom = bool(item.get("use_custom"))

        answer_parts: list[str] = []
        if selected:
            for s in selected:
                answer_parts.append(f"• {escape(str(s))}")
        if text:
            prefix = "Свой вариант: " if use_custom else ""
            answer_parts.append(escape(prefix + text))
        answer_html = (
            "<br>".join(answer_parts) if answer_parts else "<em>нет ответа</em>"
        )
        blocks.append(
            f"<div style='margin:0 0 1.2em 0;padding:0.8em 1em;"
            f"border:1px solid #ddd;border-radius:6px;max-width:900px'>"
            f"<div style='color:#666;font-size:12px;margin-bottom:4px'>"
            f"Вопрос {idx} · {kind}</div>"
            f"<div style='font-weight:600;margin-bottom:8px'>{question}</div>"
            f"<div style='white-space:pre-wrap'>{answer_html}</div>"
            f"</div>"
        )
    return mark_safe("".join(blocks)) if blocks else "—"


class VacancyAdminBase(admin.ModelAdmin):
    list_display = (
        "hh_id",
        "title_short",
        "company",
        "status",
        "match_score",
        "has_test_qa",
        "hh_link",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = (
        "hh_id",
        "title",
        "company",
        "company_hh_id",
        "skip_reason",
        "error_message",
    )
    readonly_fields = (
        "hh_id",
        "created_at",
        "updated_at",
        "hh_link_detail",
        "company_link_detail",
        "test_qa_display",
    )
    ordering = ("-updated_at",)
    formfield_overrides = {
        models.TextField: {"widget": Textarea(attrs={"rows": 8, "cols": 100})},
    }

    fieldsets = (
        (
            "Вакансия",
            {
                "fields": (
                    "hh_id",
                    "title",
                    "company",
                    "company_hh_id",
                    "company_link_detail",
                    "salary",
                    "status",
                    "match_score",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Тексты",
            {
                "fields": (
                    "snippet",
                    "description",
                    "cover_letter",
                    "skip_reason",
                    "error_message",
                )
            },
        ),
        (
            "Опросник работодателя",
            {
                "fields": ("test_qa_display", "test_qa"),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("raw_json", "created_at", "updated_at"),
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Название")
    def title_short(self, obj: Vacancy) -> str:
        title = obj.title or ""
        return title if len(title) <= 70 else title[:67] + "…"

    @admin.display(description="Тест", boolean=True)
    def has_test_qa(self, obj: Vacancy) -> bool:
        return bool(obj.test_qa and str(obj.test_qa).strip())

    @admin.display(description="Вопросы и ответы")
    def test_qa_display(self, obj: Vacancy) -> str:
        return _format_test_qa_html(obj.test_qa)

    @admin.display(description="Ссылка")
    def hh_link(self, obj: Vacancy) -> str:
        if not obj.url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">hh.ru</a>',
            obj.url,
        )

    @admin.display(description="Открыть на hh.ru")
    def hh_link_detail(self, obj: Vacancy) -> str:
        if not obj.url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.url,
            obj.url,
        )

    @admin.display(description="Страница компании")
    def company_link_detail(self, obj: Vacancy) -> str:
        if not obj.company_hh_id:
            return "—"
        url = f"https://hh.ru/employer/{obj.company_hh_id}"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            url,
            url,
        )


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("hh_id", "name", "site_short", "hh_link", "updated_at")
    search_fields = ("hh_id", "name", "industries", "description")
    readonly_fields = ("hh_id", "created_at", "updated_at", "hh_link_detail")
    ordering = ("-updated_at",)
    formfield_overrides = {
        models.TextField: {"widget": Textarea(attrs={"rows": 8, "cols": 100})},
    }
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "hh_id",
                    "name",
                    "hh_link_detail",
                    "url",
                    "site",
                    "industries",
                    "description",
                    "extra",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Сайт")
    def site_short(self, obj: Company) -> str:
        site = obj.site or ""
        return site if len(site) <= 40 else site[:37] + "…"

    @admin.display(description="HH")
    def hh_link(self, obj: Company) -> str:
        if not obj.url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">открыть</a>',
            obj.url,
        )

    @admin.display(description="Открыть на hh.ru")
    def hh_link_detail(self, obj: Company) -> str:
        if not obj.url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.url,
            obj.url,
        )


@admin.register(AppliedVacancy)
class AppliedVacancyAdmin(VacancyAdminBase):
    list_display = (
        "hh_id",
        "title_short",
        "company",
        "match_score",
        "has_letter",
        "has_test_qa",
        "hh_link",
        "applied_at",
        "updated_at",
    )
    list_filter = ()
    fieldsets = (
        (
            "Вакансия",
            {
                "fields": (
                    "hh_id",
                    "title",
                    "company",
                    "company_hh_id",
                    "company_link_detail",
                    "salary",
                    "status",
                    "match_score",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Сопроводительное и описание",
            {
                "fields": (
                    "cover_letter",
                    "description",
                    "snippet",
                )
            },
        ),
        (
            "Опросник работодателя",
            {
                "fields": ("test_qa_display", "test_qa"),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": (
                    "raw_json",
                    "error_message",
                    "created_at",
                    "applied_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(status=Vacancy.Status.APPLIED)
        )

    @admin.display(description="Письмо", boolean=True)
    def has_letter(self, obj: Vacancy) -> bool:
        return bool(obj.cover_letter and obj.cover_letter.strip())


@admin.register(SkippedVacancy)
class SkippedVacancyAdmin(VacancyAdminBase):
    list_display = (
        "hh_id",
        "title_short",
        "company",
        "match_score",
        "skip_reason_short",
        "hh_link",
        "updated_at",
    )
    list_filter = ()
    fieldsets = (
        (
            "Вакансия",
            {
                "fields": (
                    "hh_id",
                    "title",
                    "company",
                    "company_hh_id",
                    "company_link_detail",
                    "salary",
                    "status",
                    "match_score",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Почему скип",
            {"fields": ("skip_reason", "snippet", "description")},
        ),
        (
            "Опросник работодателя",
            {
                "fields": ("test_qa_display", "test_qa"),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("cover_letter", "raw_json", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(status=Vacancy.Status.SKIPPED)
        )

    @admin.display(description="Причина")
    def skip_reason_short(self, obj: Vacancy) -> str:
        reason = obj.skip_reason or ""
        return reason if len(reason) <= 60 else reason[:57] + "…"


@admin.register(ProblemVacancy)
class ProblemVacancyAdmin(VacancyAdminBase):
    list_display = (
        "hh_id",
        "title_short",
        "company",
        "status",
        "error_short",
        "has_test_qa",
        "hh_link",
        "updated_at",
    )
    list_filter = ("status",)
    fieldsets = (
        (
            "Вакансия",
            {
                "fields": (
                    "hh_id",
                    "title",
                    "company",
                    "company_hh_id",
                    "company_link_detail",
                    "salary",
                    "status",
                    "match_score",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Проблема",
            {"fields": ("error_message", "skip_reason", "cover_letter")},
        ),
        (
            "Опросник работодателя",
            {
                "fields": ("test_qa_display", "test_qa"),
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": (
                    "snippet",
                    "description",
                    "raw_json",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(status__in=[Vacancy.Status.ERROR, Vacancy.Status.BLOCKED])
        )

    @admin.display(description="Ошибка")
    def error_short(self, obj: Vacancy) -> str:
        msg = obj.error_message or ""
        return msg if len(msg) <= 60 else msg[:57] + "…"


@admin.register(TestedVacancy)
class TestedVacancyAdmin(VacancyAdminBase):
    """Все вакансии с сохранённым опросником — проверить ответы."""

    list_display = (
        "hh_id",
        "title_short",
        "company",
        "status",
        "qa_count",
        "hh_link",
        "updated_at",
    )
    list_filter = ("status",)
    fieldsets = (
        (
            "Вакансия",
            {
                "fields": (
                    "hh_id",
                    "title",
                    "company",
                    "company_hh_id",
                    "company_link_detail",
                    "salary",
                    "status",
                    "match_score",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Опросник: вопросы и ответы",
            {
                "fields": ("test_qa_display", "test_qa"),
            },
        ),
        (
            "Сопроводительное и описание",
            {
                "fields": (
                    "cover_letter",
                    "description",
                    "snippet",
                    "skip_reason",
                    "error_message",
                )
            },
        ),
        (
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("raw_json", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return (
            qs.exclude(test_qa__isnull=True)
            .exclude(test_qa="")
            .extra(where=["btrim(test_qa) <> ''"])
        )

    @admin.display(description="Вопросов")
    def qa_count(self, obj: Vacancy) -> int | str:
        raw = obj.test_qa
        if not raw:
            return 0
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "?"
        if not isinstance(items, list):
            return "?"
        return len(items)


@admin.register(Vacancy)
class VacancyAdmin(VacancyAdminBase):
    """Полный список (new / applied / skipped / error / blocked)."""


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    fields = ("direction", "text_full", "sent_at", "author_label", "external_id")
    readonly_fields = fields
    ordering = ("id",)
    show_change_link = True

    @admin.display(description="Текст")
    def text_full(self, obj: ChatMessage) -> str:
        raw = (obj.text or "").strip()
        if not raw:
            return "—"
        return format_html(
            '<pre style="white-space:pre-wrap;max-width:720px;margin:0;'
            'font-size:12px;line-height:1.35">{}</pre>',
            raw,
        )

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class ChatActionInline(admin.TabularInline):
    model = ChatAction
    extra = 0
    can_delete = False
    fields = ("kind", "status", "delay_sec", "escalate_reason", "created_at")
    readonly_fields = fields
    ordering = ("-id",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = (
        "chat_id",
        "title_short",
        "company_name",
        "status",
        "bot_paused",
        "vacancy_hh_id",
        "hh_link",
        "updated_at",
    )
    list_filter = ("status", "bot_paused", "source")
    search_fields = (
        "chat_id",
        "title",
        "company_name",
        "company_hh_id",
        "vacancy_hh_id",
        "last_preview",
        "paused_reason",
    )
    readonly_fields = (
        "chat_id",
        "created_at",
        "updated_at",
        "hh_link_detail",
        "vacancy_link_detail",
        "company_link_detail",
        "preview_display",
    )
    ordering = ("-updated_at",)
    inlines = (ChatMessageInline, ChatActionInline)
    formfield_overrides = {
        models.TextField: {"widget": Textarea(attrs={"rows": 4, "cols": 100})},
    }
    fieldsets = (
        (
            "Чат",
            {
                "fields": (
                    "chat_id",
                    "title",
                    "subtitle",
                    "status",
                    "source",
                    "hh_link_detail",
                    "url",
                )
            },
        ),
        (
            "Связи",
            {
                "fields": (
                    "company_name",
                    "company_hh_id",
                    "company_link_detail",
                    "vacancy_hh_id",
                    "vacancy_link_detail",
                )
            },
        ),
        (
            "Пауза бота",
            {"fields": ("bot_paused", "paused_reason")},
        ),
        (
            "Превью / даты",
            {
                "fields": (
                    "preview_display",
                    "last_preview",
                    "last_inbound_at",
                    "last_outbound_at",
                    "last_inbound_hash",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Название")
    def title_short(self, obj: ChatThread) -> str:
        t = (obj.title or obj.chat_id or "").strip()
        return t if len(t) <= 50 else t[:47] + "…"

    @admin.display(description="HH")
    def hh_link(self, obj: ChatThread):
        url = obj.url or f"https://hh.ru/chat/{obj.chat_id}"
        return format_html('<a href="{}" target="_blank" rel="noopener">чат</a>', url)

    @admin.display(description="Ссылка на чат")
    def hh_link_detail(self, obj: ChatThread):
        url = obj.url or f"https://hh.ru/chat/{obj.chat_id}"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    @admin.display(description="Вакансия")
    def vacancy_link_detail(self, obj: ChatThread):
        if not obj.vacancy_hh_id:
            return "—"
        return format_html(
            '<a href="/admin/core/vacancy/{}/change/">{}</a>',
            obj.vacancy_hh_id,
            obj.vacancy_hh_id,
        )

    @admin.display(description="Компания")
    def company_link_detail(self, obj: ChatThread):
        if not obj.company_hh_id:
            return "—"
        return format_html(
            '<a href="/admin/core/company/{}/change/">{}</a>',
            obj.company_hh_id,
            obj.company_hh_id,
        )

    @admin.display(description="Превью")
    def preview_display(self, obj: ChatThread) -> str:
        raw = (obj.last_preview or "").strip()
        if not raw:
            return "—"
        return format_html(
            '<pre style="white-space:pre-wrap;max-width:900px">{}</pre>',
            raw,
        )


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chat_id_display",
        "direction",
        "text_short",
        "sent_at",
        "created_at",
    )
    list_filter = ("direction",)
    search_fields = ("text", "chat__chat_id", "chat__title", "external_id")
    readonly_fields = (
        "id",
        "chat",
        "direction",
        "text",
        "sent_at",
        "author_label",
        "external_id",
        "our_action",
        "created_at",
    )
    ordering = ("-id",)

    @admin.display(description="Чат", ordering="chat_id")
    def chat_id_display(self, obj: ChatMessage) -> str:
        return obj.chat_id

    @admin.display(description="Текст")
    def text_short(self, obj: ChatMessage) -> str:
        t = (obj.text or "").strip()
        return t if len(t) <= 80 else t[:77] + "…"


@admin.register(ChatAction)
class ChatActionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "chat_id_display",
        "kind",
        "status",
        "delay_sec",
        "escalate_reason",
        "created_at",
    )
    list_filter = ("kind", "status")
    search_fields = ("chat__chat_id", "draft", "error_message", "escalate_reason")
    readonly_fields = (
        "id",
        "chat",
        "kind",
        "draft",
        "delay_sec",
        "escalate_reason",
        "status",
        "error_message",
        "created_at",
    )
    ordering = ("-id",)

    @admin.display(description="Чат", ordering="chat_id")
    def chat_id_display(self, obj: ChatAction) -> str:
        return obj.chat_id
