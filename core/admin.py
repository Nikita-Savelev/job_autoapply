"""Админка: отклики, скипы, проблемные, компании и все вакансии."""

from __future__ import annotations

from django.contrib import admin
from django.db import models
from django.forms.widgets import Textarea
from django.utils.html import format_html

from core.models import (
    AppliedVacancy,
    Company,
    ProblemVacancy,
    SkippedVacancy,
    Vacancy,
)


class VacancyAdminBase(admin.ModelAdmin):
    list_display = (
        "hh_id",
        "title_short",
        "company",
        "status",
        "match_score",
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
        "hh_link",
        "has_letter",
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
            "Служебное",
            {
                "classes": ("collapse",),
                "fields": ("raw_json", "error_message", "created_at", "updated_at"),
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
        "hh_link",
        "updated_at",
    )
    list_filter = ("status",)

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


@admin.register(Vacancy)
class VacancyAdmin(VacancyAdminBase):
    """Полный список (new / applied / skipped / error / blocked)."""
