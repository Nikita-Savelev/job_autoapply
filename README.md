# job_autoapply — автоотклики через браузер (Selenium)

Автоматизация откликов на вакансии: поиск → матчинг (LLM) → отклик с сопроводительным письмом.  
План: [PLAN.md](PLAN.md). Контекст для агента: [AGENTS.md](AGENTS.md).

## Быстрый старт

```bash
cd job_autoapply   # или job_search/hh_autoapply в workspace MyProjects
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d
# .env: HH_SEARCH_URL, OPENAI_API_KEY (см. .env.example)
python login.py
```

### Отладка отклика (реальные клики + HTML/логи)

По умолчанию бот **откликается**. `--debug` только усиливает логи и дампы страниц.

```bash
python run.py --debug --limit 15 --apply-limit 1
```

### Только матчинг, без кликов

```bash
python run.py --dry-run --debug --limit 20
```

## Django-админка

Как у ботов: Django Admin поверх той же Postgres. Разделы:

- **Отклики** — applied (ссылка на hh, описание, сопроводительное и вся сохранённая инфа)
- **Скипнутые** — skipped + причина
- **Проблемные** — error / blocked
- **Компании** — кэш страниц работодателей (описание переиспользуется между вакансиями)
- **Все вакансии** — полный список со фильтром по статусу

Локально (Postgres уже на `:5433`):

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8005
# http://127.0.0.1:8005/admin/
```

Или вместе с Postgres:

```bash
docker compose up -d --build
# админка: http://127.0.0.1:8005/admin/
# суперюзера создать один раз: docker compose exec django_admin python manage.py createsuperuser
```

## Сценарии после «Откликнуться»

1. **Тест/опросник** — собираем вопросы → LLM-ответы (техника как Senior / HR по `job_search/context`) → письмо → «Откликнуться».
2. **Обязательное письмо** — модалка с textarea сразу; кнопка «Откликнуться» сначала disabled → ввод → отправка.
3. **Попап «отклик отправлен»** → «Приложить письмо» → textarea → «Отправить».
4. **«Отклик уже просмотрен работодателем»** → чат Chatik → «Добавить сопроводительное» → отправить.

Примеры вёрстки опросников храни локально в `tests_example/` (папка в `.gitignore`, в репозиторий не входит).

## Env

См. `.env.example`. Паузы: `HH_PAUSE_SEC=2.5`. Подсветка: `HH_HIGHLIGHT=1`. Картинки в Chrome отключены.

## Ограничения

Не коммитить `.env`, `.chrome_profile/`, `postgres_data/`, `debug_pages/`, `resume/resume.*`, `staticfiles/`.
