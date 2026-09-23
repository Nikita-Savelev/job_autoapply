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
        INACTIVE = "inactive", "Неактивна"

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
    applied_at = models.DateTimeField("Отклик отправлен", blank=True, null=True)
    hidden_at = models.DateTimeField("Скрыта на HH", blank=True, null=True)

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


class TestedVacancy(Vacancy):
    """Вакансии, у которых сохранён опросник (Q&A) — для проверки ответов."""

    class Meta:
        proxy = True
        verbose_name = "Вакансия с тестом"
        verbose_name_plural = "Тесты"
        ordering = ["-updated_at"]


class ChatThread(models.Model):
    """Диалог hh.ru/chat (таблица chat_threads)."""

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        AWAITING_US = "awaiting_us", "Ждём наш ответ"
        AWAITING_THEM = "awaiting_them", "Ждём HR"
        NEEDS_HUMAN = "needs_human", "Нужен человек"
        REJECTED = "rejected", "Отказ"
        INTERVIEW = "interview", "Собеседование"
        CLOSED = "closed", "Закрыт"
        ERROR = "error", "Ошибка"

    class Source(models.TextChoices):
        RESPONSE = "response", "После отклика"
        INBOUND = "inbound", "Входящий"
        UNKNOWN = "unknown", "Неизвестно"

    chat_id = models.CharField("Chat id", primary_key=True, max_length=64)
    title = models.TextField("Заголовок", blank=True, default="")
    subtitle = models.TextField("Подзаголовок", blank=True, null=True)
    url = models.TextField("URL", blank=True, default="")
    source = models.CharField(
        "Источник",
        max_length=32,
        choices=Source.choices,
        default=Source.UNKNOWN,
    )
    status = models.CharField(
        "Статус",
        max_length=32,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    bot_paused = models.BooleanField("Бот на паузе", default=False)
    paused_reason = models.TextField("Причина паузы", blank=True, null=True)
    vacancy_hh_id = models.CharField(
        "Вакансия HH id", max_length=64, blank=True, null=True, db_index=True
    )
    company_hh_id = models.CharField(
        "Компания HH id", max_length=64, blank=True, null=True, db_index=True
    )
    company_name = models.TextField("Компания", blank=True, null=True)
    last_inbound_at = models.DateTimeField("Последнее входящее", blank=True, null=True)
    last_outbound_at = models.DateTimeField(
        "Последнее исходящее", blank=True, null=True
    )
    last_inbound_hash = models.CharField(
        "Hash входящего", max_length=64, blank=True, null=True
    )
    last_preview = models.TextField("Превью", blank=True, null=True)
    created_at = models.DateTimeField("Создано")
    updated_at = models.DateTimeField("Обновлено")

    class Meta:
        managed = False
        db_table = "chat_threads"
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.title or self.chat_id} [{self.status}]"


class ChatMessage(models.Model):
    """Сообщение в чате."""

    class Direction(models.TextChoices):
        IN = "in", "Входящее"
        OUT = "out", "Исходящее"

    id = models.BigAutoField(primary_key=True)
    chat = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        db_column="chat_id",
        related_name="messages",
        verbose_name="Чат",
    )
    direction = models.CharField(
        "Направление", max_length=8, choices=Direction.choices
    )
    text = models.TextField("Текст")
    sent_at = models.DateTimeField("Отправлено", blank=True, null=True)
    author_label = models.TextField("Автор", blank=True, null=True)
    external_id = models.CharField(
        "External id", max_length=64, blank=True, null=True
    )
    our_action = models.CharField(
        "Наше действие", max_length=32, blank=True, null=True
    )
    created_at = models.DateTimeField("Создано")

    class Meta:
        managed = False
        db_table = "chat_messages"
        verbose_name = "Сообщение чата"
        verbose_name_plural = "Сообщения чатов"
        ordering = ["id"]

    def __str__(self) -> str:
        preview = (self.text or "")[:40]
        return f"{self.direction}: {preview}"


class ChatAction(models.Model):
    """Запланированное / выполненное действие бота в чате."""

    id = models.BigAutoField(primary_key=True)
    chat = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        db_column="chat_id",
        related_name="actions",
        verbose_name="Чат",
    )
    kind = models.CharField("Тип", max_length=32)
    draft = models.TextField("Черновик", blank=True, null=True)
    delay_sec = models.FloatField("Delay сек", blank=True, null=True)
    escalate_reason = models.TextField("Причина эскалации", blank=True, null=True)
    status = models.CharField("Статус", max_length=32, default="planned")
    error_message = models.TextField("Ошибка", blank=True, null=True)
    created_at = models.DateTimeField("Создано")

    class Meta:
        managed = False
        db_table = "chat_actions"
        verbose_name = "Действие чата"
        verbose_name_plural = "Действия чатов"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.kind} [{self.status}]"
