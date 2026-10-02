# job_autoapply — автоотклики через браузер (Selenium)

Автоматизация откликов на вакансии: поиск → матчинг (LLM) → отклик с сопроводительным письмом.  
План: [PLAN.md](PLAN.md). Контекст для агента: [AGENTS.md](AGENTS.md).

## Что нужно

- Python 3.12
- Google Chrome
- Docker (Postgres на порту 5433)
- Ключ OpenAI-совместимого API и текст резюме локально (`resume/resume.txt`, в git не входит)

## Быстрый старт

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни OPENAI_API_KEY, контакты и при необходимости HH_TG_BOT_TOKEN
docker compose up -d postgres
python login.py
```

`login.py` открывает Chrome. Войди в hh.ru и нажми Enter в терминале: сессия сохранится в `.chrome_profile/` (в git не входит).

### Отладка отклика (реальные клики + HTML/логи)

По умолчанию бот **откликается**. Лимиты:
- `--apply-limit` — сколько за этот прогон;
- **`HH_DAILY_APPLY_LIMIT=200`** — жёсткий потолок за календарный день
  (Europe/Moscow) по полю `applied_at` в БД; CLI его не обходит.
(страницы и матчинг без ограничений). `--debug` усиливает логи и дампы.

```bash
python run.py --debug --apply-limit 1
```

### Только матчинг, без кликов

```bash
python run.py --dry-run --apply-limit 50
```

### Ежедневный широкий поиск (рекомендуемый режим)

Python по всему сайту (без региона), 50 вакансий на странице.
Уже виденные и откликнутые в БД пропускаются.

Вся история публикаций:

```bash
python run_daily.py --period 0 --apply-limit 200
```

Только свежие за неделю. Без `--debug` пауза между действиями равна 0; окно Chrome — `--foreground`:

```bash
python run_daily.py --period 7 --apply-limit 200 --foreground
```

### Чат

Тот же Chrome-профиль. Два процесса сразу запускать нельзя: профиль один.

Непрочитанные, пауза 2 минуты, затем выход и закрытие окна:

```bash
HH_PAUSE_SEC=1 python run_chat.py --unread --fast --debug
```

Догон ленты до 5 чатов подряд, которые уже есть в БД, затем мониторинг:

```bash
HH_PAUSE_SEC=1 python run_chat.py --fast --debug
```

Отклики и чат в одном окне: после каждой поисковой ссылки — непрочитанные, раз за полный круг ссылок — все чаты за сегодня.

```bash
HH_PAUSE_SEC=1 python run_mix.py --fast --debug
```

После своего сообщения бот ждёт 5 секунд. Если за это время пришёл следующий вопрос, отвечает сразу и снова ждёт 5 секунд. Сообщение в чат отправляется само, без Enter в терминале. `--fast` отключает паузу «набора».

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

Капча hh.ru: бот детектит `[data-qa=account-captcha-*]`, шлёт картинку в Telegram и ждёт ответ текстом. Текст вставляется в поле и отправляется. Неудачных попыток подряд — до трёх. Токен бота — `HH_TG_BOT_TOKEN` в `.env` этого проекта. Пока ждём ответ на капчу, `getUpdates` занимает этого бота.

Картинки вакансий/CDN по-прежнему режутся (CDP), но `/captcha/picture` грузится — иначе капчу не видно.

## Ограничения

Не коммитить `.env`, `.chrome_profile/`, `postgres_data/`, `debug_pages/`, `resume/resume.*`, `staticfiles/`.
