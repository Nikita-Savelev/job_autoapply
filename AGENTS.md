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
python run_daily.py --period 0 --apply-limit 200   # широкий Python, весь сайт
python run_daily.py --period 7 --apply-limit 200   # daily: за неделю
python run.py --apply-limit 50                     # HH_SEARCH_URL из .env
python run.py --dry-run --apply-limit 100
python run_chat.py --loop --debug                  # мониторинг чатов (3 стр.)
python run_chat.py --once --force --debug          # полный прогон списка (редко)
python manage.py runserver 8005
```

Ежедневный режим: `run_daily.py` — text=Python, без area, 50/стр.
`--period 0` = вся история; `--period 7` = свежие за неделю.

## Модули

| Путь | Назначение |
|------|------------|
| `hh/` | Selenium: поиск, вакансия, отклик, опросник, капча, **чат**, селекторы |
| `chat/` | Автоответы в переписке HH (chatik): classify / compose / delay — [chat/PLAN.md](chat/PLAN.md) |
| `notify/` | Telegram-уведомления (токен из `finance_bot/.env`) |
| `pipeline/` | scrape → match → apply |
| `matcher/` | LLM batch suitable/reject + ответы на опросник |
| `cover_letter/` | Генерация писем |
| `db/` | Postgres store |
| `core/` | Django models / admin |
