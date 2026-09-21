"""PostgreSQL-хранилище вакансий и компаний."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from loguru import logger
from psycopg.rows import dict_row

from db.models import Company, Vacancy, VacancyStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VacancyStore:
    def __init__(self, conninfo: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """conninfo — kwargs для psycopg.connect (dbname, user, password, host, port)."""
        params = dict(conninfo or {})
        params.update(kwargs)
        self._conn = psycopg.connect(**params, row_factory=dict_row)
        self._conn.autocommit = False
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    hh_id          TEXT PRIMARY KEY,
                    name           TEXT NOT NULL,
                    url            TEXT NOT NULL,
                    description    TEXT,
                    industries     TEXT,
                    site           TEXT,
                    extra          TEXT,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vacancies (
                    hh_id          TEXT PRIMARY KEY,
                    title          TEXT NOT NULL,
                    url            TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    company        TEXT,
                    company_hh_id  TEXT,
                    salary         TEXT,
                    snippet        TEXT,
                    description    TEXT,
                    match_score    DOUBLE PRECISION,
                    skip_reason    TEXT,
                    cover_letter   TEXT,
                    error_message  TEXT,
                    raw_json       TEXT,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "ALTER TABLE vacancies ADD COLUMN IF NOT EXISTS company_hh_id TEXT"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vacancies_status ON vacancies(status)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vacancies_company "
                "ON vacancies(company_hh_id)"
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VacancyStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def upsert_from_search(self, vacancy: Vacancy) -> Vacancy:
        """Вставить новую или обновить метаданные выдачи, не затирая applied/skipped."""
        existing = self.get(vacancy.hh_id)
        now = _utc_now()
        if existing is None:
            vacancy.created_at = now
            vacancy.updated_at = now
            self._insert(vacancy)
            return vacancy

        if existing.status != VacancyStatus.NEW:
            return existing

        existing.title = vacancy.title
        existing.url = vacancy.url
        existing.company = vacancy.company or existing.company
        existing.company_hh_id = vacancy.company_hh_id or existing.company_hh_id
        existing.salary = vacancy.salary or existing.salary
        existing.snippet = vacancy.snippet or existing.snippet
        existing.raw_json = vacancy.raw_json or existing.raw_json
        existing.updated_at = now
        self._update(existing)
        return existing

    def get(self, hh_id: str) -> Vacancy | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM vacancies WHERE hh_id = %s", (hh_id,))
            row = cur.fetchone()
        return self._row_to_vacancy(row) if row else None

    def list_by_status(self, *statuses: VacancyStatus) -> list[Vacancy]:
        if not statuses:
            return []
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM vacancies WHERE status = ANY(%s) "
                "ORDER BY created_at ASC",
                ([s.value for s in statuses],),
            )
            rows = cur.fetchall()
        return [self._row_to_vacancy(r) for r in rows]

    def mark(
        self,
        hh_id: str,
        status: VacancyStatus,
        *,
        skip_reason: str | None = None,
        cover_letter: str | None = None,
        description: str | None = None,
        match_score: float | None = None,
        error_message: str | None = None,
        company_hh_id: str | None = None,
        company: str | None = None,
    ) -> Vacancy | None:
        vac = self.get(hh_id)
        if vac is None:
            return None
        vac.status = status
        vac.updated_at = _utc_now()
        if skip_reason is not None:
            vac.skip_reason = skip_reason
        if cover_letter is not None:
            vac.cover_letter = cover_letter
        if description is not None:
            vac.description = description
        if match_score is not None:
            vac.match_score = match_score
        if error_message is not None:
            vac.error_message = error_message
        if company_hh_id is not None:
            vac.company_hh_id = company_hh_id
        if company is not None:
            vac.company = company
        self._update(vac)
        return vac

    def get_company(self, hh_id: str) -> Company | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM companies WHERE hh_id = %s", (hh_id,))
            row = cur.fetchone()
        return self._row_to_company(row) if row else None

    def upsert_company(self, company: Company) -> Company:
        """Сохранить/обновить компанию. Пустые поля не затирают уже заполненные."""
        existing = self.get_company(company.hh_id)
        now = _utc_now()
        if existing is None:
            company.created_at = now
            company.updated_at = now
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO companies (
                        hh_id, name, url, description, industries, site, extra,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company.hh_id,
                        company.name,
                        company.url,
                        company.description,
                        company.industries,
                        company.site,
                        company.extra,
                        company.created_at,
                        company.updated_at,
                    ),
                )
            self._conn.commit()
            logger.info(
                "company saved {} {!r} desc_len={}",
                company.hh_id,
                company.name,
                len(company.description or ""),
            )
            return company

        existing.name = company.name or existing.name
        existing.url = company.url or existing.url
        if company.description:
            existing.description = company.description
        if company.industries:
            existing.industries = company.industries
        if company.site:
            existing.site = company.site
        if company.extra:
            existing.extra = company.extra
        existing.updated_at = now
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies SET
                    name = %s, url = %s, description = %s, industries = %s,
                    site = %s, extra = %s, updated_at = %s
                WHERE hh_id = %s
                """,
                (
                    existing.name,
                    existing.url,
                    existing.description,
                    existing.industries,
                    existing.site,
                    existing.extra,
                    existing.updated_at,
                    existing.hh_id,
                ),
            )
        self._conn.commit()
        logger.info(
            "company updated {} {!r} desc_len={}",
            existing.hh_id,
            existing.name,
            len(existing.description or ""),
        )
        return existing

    def counts(self) -> dict[str, int]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*)::int AS n FROM vacancies GROUP BY status"
            )
            return {row["status"]: int(row["n"]) for row in cur.fetchall()}

    def _insert(self, v: Vacancy) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vacancies (
                    hh_id, title, url, status, company, company_hh_id, salary,
                    snippet, description, match_score, skip_reason, cover_letter,
                    error_message, raw_json, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    v.hh_id,
                    v.title,
                    v.url,
                    v.status.value,
                    v.company,
                    v.company_hh_id,
                    v.salary,
                    v.snippet,
                    v.description,
                    v.match_score,
                    v.skip_reason,
                    v.cover_letter,
                    v.error_message,
                    v.raw_json,
                    v.created_at,
                    v.updated_at,
                ),
            )
        self._conn.commit()

    def _update(self, v: Vacancy) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vacancies SET
                    title = %s, url = %s, status = %s, company = %s,
                    company_hh_id = %s, salary = %s, snippet = %s,
                    description = %s, match_score = %s, skip_reason = %s,
                    cover_letter = %s, error_message = %s, raw_json = %s,
                    updated_at = %s
                WHERE hh_id = %s
                """,
                (
                    v.title,
                    v.url,
                    v.status.value,
                    v.company,
                    v.company_hh_id,
                    v.salary,
                    v.snippet,
                    v.description,
                    v.match_score,
                    v.skip_reason,
                    v.cover_letter,
                    v.error_message,
                    v.raw_json,
                    v.updated_at,
                    v.hh_id,
                ),
            )
        self._conn.commit()

    @staticmethod
    def _row_to_vacancy(row: dict[str, Any]) -> Vacancy:
        created = row["created_at"]
        updated = row["updated_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return Vacancy(
            hh_id=row["hh_id"],
            title=row["title"],
            url=row["url"],
            status=VacancyStatus(row["status"]),
            company=row["company"],
            company_hh_id=row.get("company_hh_id"),
            salary=row["salary"],
            snippet=row["snippet"],
            description=row["description"],
            match_score=row["match_score"],
            skip_reason=row["skip_reason"],
            cover_letter=row["cover_letter"],
            error_message=row["error_message"],
            raw_json=row["raw_json"],
            created_at=created,
            updated_at=updated,
        )

    @staticmethod
    def _row_to_company(row: dict[str, Any]) -> Company:
        created = row["created_at"]
        updated = row["updated_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return Company(
            hh_id=row["hh_id"],
            name=row["name"],
            url=row["url"],
            description=row["description"],
            industries=row["industries"],
            site=row["site"],
            extra=row["extra"],
            created_at=created,
            updated_at=updated,
        )
