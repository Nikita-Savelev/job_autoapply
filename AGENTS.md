# job_autoapply

Selenium-автоотклики на вакансии: сбор с поиска → Postgres → LLM-матчинг → отклик с сопроводительным.

Локальный путь в workspace: `job_search/hh_autoapply/` (имя папки историческое; репозиторий — `job_autoapply`).

## Стек

- Python 3, Selenium (Chrome + сохранённый профиль)
- PostgreSQL (Docker Compose, порт 5433)
- Django Admin (`:8005`) поверх тех же таблиц
- OpenAI-совместимый API для матчинга и писем

## Документация

| Файл | Содержание |
|------|------------|
| [README.md](README.md) | Быстрый старт, CLI, сценарии отклика |
| [PLAN.md](PLAN.md) | Архитектура, статусы, этапы |
| [cover_letter/](cover_letter/) | Структура и тон сопроводительных |
| [.env.example](.env.example) | Переменные окружения |
| [tests_example/](tests_example/) | HTML-примеры экранов с тестом при отклике |

## Как работать с агентом

1. Перед правками читай этот файл и `README.md`.
2. Меняй только файлы внутри этого проекта.
3. Секреты — только в `.env` (не коммитить).
4. Не коммитить `.chrome_profile/`, `postgres_data/`, `debug_pages/`, `resume/resume.*`.
5. Коммиты — по явной просьбе; сообщения на русском, без упоминаний Cursor/AI.

## Основные команды

```bash
docker compose up -d
python login.py
python run.py --limit 50 --apply-limit 10
python run.py --dry-run --limit 20
python manage.py runserver 8005
```

## Модули

| Путь | Назначение |
|------|------------|
| `hh/` | Selenium: поиск, вакансия, отклик, селекторы |
| `pipeline/` | scrape → match → apply |
| `matcher/` | LLM batch suitable/reject |
| `cover_letter/` | Генерация писем |
| `db/` | Postgres store |
| `core/` | Django models / admin |
| `tests_example/` | Эталоны UI тестов (для будущей автоматизации) |
