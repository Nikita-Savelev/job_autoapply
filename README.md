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

По умолчанию бот **откликается**. Единственный лимит — `--apply-limit`
(страницы и матчинг без ограничений). `--debug` усиливает логи и дампы.

```bash
python run.py --debug --apply-limit 1
```

### Только матчинг, без кликов

```bash
python run.py --dry-run --apply-limit 50
```

### Ежедневный широкий поиск (рекомендуемый режим)

Python по всему сайту (без региона), 100 вакансий на странице.
Уже виденные/откликнутые в БД пропускаются — можно гонять каждый день.

Первый полный прогон (вся история)::

```bash
python run_daily.py --period 0 --apply-limit 200
```

Потом ежедневно только свежие (за неделю)::

```bash
python run_daily.py --period 7 --apply-limit 200
```

### Очередь нескольких узких поисков (общий лимит откликов)

Все URL из `probe_search_urls.py` по урожайности, каждая выдача до конца:

```bash
python run_queue.py --apply-limit 200
```

### Повтор проблемных (error / blocked)

```bash
python retry_problems.py
python retry_problems.py --status blocked --limit 5
```

### Опросник при отклике (live / review)

По умолчанию — **реальный отклик** (ответы LLM + финальная «Откликнуться»):

```bash
python review_tests.py
```

Только заполнить форму без submit (ручная проверка):

```bash
python review_tests.py --review
```

В обычном `run.py` опросник тоже проходит с submit (как live).

## Django-админка

Как у ботов: Django Admin поверх той же Postgres. Разделы:

- **Отклики** — applied (ссылка на hh, описание, сопроводительное и вся сохранённая инфа)
- **Скипнутые** — skipped + причина
- **Проблемные** — error / blocked
- **Тесты** — вакансии с сохранённым опросником (Q&A) для проверки ответов
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

Капча hh.ru: бот детектит `[data-qa=account-captcha-*]`, шлёт уведомление в Telegram (chat id захардкожен) и **ждёт**, пока ты решишь капчу в Chrome. Токен бота читается из `finance_bot/.env` (`BOT_TOKEN`), в этот проект не копируется.

Картинки вакансий/CDN по-прежнему режутся (CDP), но `/captcha/picture` грузится — иначе капчу не видно.

## Ограничения

Не коммитить `.env`, `.chrome_profile/`, `postgres_data/`, `debug_pages/`, `resume/resume.*`, `staticfiles/`.
