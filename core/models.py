"""Django-модели поверх таблиц vacancies / companies (создаёт VacancyStore)."""

from __future__ import annotations

from django.db import models


class Company(models.Model):
    """Кэш страницы работодателя hh.ru."""

    hh_id = models.CharField("HH employer id", primary_key=True, max_length=64)
    name = models.TextField("Название")
    url = models.TextField("URL")
    description = models.TextField("Описание", blank=True, null=True)
    industries = models.TextField("Отрасли", blank=True, null=True)
    site = models.TextField("Сайт", blank=True, null=True)
    extra = models.TextField("Дополнительно", blank=True, null=True)
    created_at = models.DateTimeField("Создано")
    updated_at = models.DateTimeField("Обновлено")

    class Meta:
        managed = False
        db_table = "companies"
        verbose_name = "Компания"
        verbose_name_plural = "Компании"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.hh_id})"


class Vacancy(models.Model):
    """Все вакансии из автооткликов."""

    class Status(models.TextChoices):
        NEW = "new", "Новая"
        SKIPPED = "skipped", "Скип"
        APPLIED = "applied", "Отклик"
        ERROR = "error", "Ошибка"
        BLOCKED = "blocked", "Блок (тест)"

    hh_id = models.CharField("HH id", primary_key=True, max_length=64)
    title = models.TextField("Название")
    url = models.TextField("URL")
    status = models.CharField(
        "Статус",
        max_length=32,
        choices=Status.choices,
        db_index=True,
    )
    company = models.TextField("Компания", blank=True, null=True)
    company_hh_id = models.CharField(
        "HH id компании", max_length=64, blank=True, null=True, db_index=True
    )
    salary = models.TextField("Зарплата", blank=True, null=True)
    snippet = models.TextField("Сниппет с выдачи", blank=True, null=True)
    description = models.TextField("Описание вакансии", blank=True, null=True)
    match_score = models.FloatField("Match score", blank=True, null=True)
    skip_reason = models.TextField("Причина скипа", blank=True, null=True)
    cover_letter = models.TextField("Сопроводительное", blank=True, null=True)
    test_qa = models.TextField("Опросник (Q&A)", blank=True, null=True)
    error_message = models.TextField("Ошибка", blank=True, null=True)
    raw_json = models.TextField("Raw JSON", blank=True, null=True)
    created_at = models.DateTimeField("Создано")
    updated_at = models.DateTimeField("Обновлено")

    class Meta:
        managed = False
        db_table = "vacancies"
        verbose_name = "Вакансия"
        verbose_name_plural = "Все вакансии"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        company = f" — {self.company}" if self.company else ""
        return f"{self.title}{company} ({self.hh_id})"


class AppliedVacancy(Vacancy):
    """Только успешные отклики."""

    class Meta:
        proxy = True
        verbose_name = "Отклик"
        verbose_name_plural = "Отклики"
        ordering = ["-updated_at"]


class SkippedVacancy(Vacancy):
    """Вакансии, которые скипнули (матчер / правила)."""

    class Meta:
        proxy = True
        verbose_name = "Скипнутая вакансия"
        verbose_name_plural = "Скипнутые"
        ordering = ["-updated_at"]


class ProblemVacancy(Vacancy):
    """Ошибки и блокировки (тест при отклике)."""

    class Meta:
        proxy = True
        verbose_name = "Проблемная вакансия"
        verbose_name_plural = "Проблемные"
        ordering = ["-updated_at"]
