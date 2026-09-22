# Чат HH (chatik)

Автоответы в переписке на [hh.ru/chat](https://hh.ru/chat).

- Решения и этапы: [PLAN.md](PLAN.md)
## Запуск (тестовая версия)

Из `job_search/hh_autoapply/` (нужны Postgres, `.env` с LLM, залогиненный Chrome-профиль):

```bash
# один прогон списка (ручной «Отправить» + Enter в терминале)
.venv/bin/python run_chat.py --once --debug

# один конкретный чат, без длинной паузы набора
.venv/bin/python run_chat.py --once --chat-id 5649719078 --fast --debug

# только посмотреть LLM-ответ в логе, без вставки в UI
.venv/bin/python run_chat.py --once --chat-id 5649719078 --dry-run --fast --debug
```

Бот **не** жмёт «Отправить»: вставил текст → ты отправил в браузере → Enter в терминале → проверка пузыря → `awaiting_them` в БД.

- Selenium: `hh/chat_ui.py` (тот же Chrome, что отклики)
- БД: `chat/store.py` → таблицы `chat_threads` / `chat_messages` / `chat_actions`

## Триггер

Отвечаем, если **последнее сообщение в треде не наше** (даже без бейджа unread).

## Статусы

`new` → `awaiting_us` → (verify) → `awaiting_them` | `needs_human` | `closed` | `error`

`closed` + новое непрочитанное → снова `awaiting_us`.

## Отладка

Подсветка + пауза 2.5с. Отправить пока жмёт человек; Enter в терминале → проверка пузыря → только тогда `awaiting_them`.

## Эскалация в Telegram

Собес / ссылка на тест / бот застрял → TG + `bot_paused` (бот молчит до их следующего сообщения).

## DOM

`tests_example/chat.html` — список. `tests_example/chat_thread.html` — открытый тред (пузыри + composer).

Ключевые селекторы (уже в `hh/selectors.py` / `chat/dom_parse.py`):

- сообщения: `data-qa=chatik-chat-message-<id>`, наши — `chat-bubble_outgoing` / `message_my`
- текст: `data-qa=chat-bubble-text`
- ввод: `chatik-message-input` → `textarea[data-qa=text-input]` (отправка Enter)
- шапка: `participant-info-title`, `participant-info-details` → `/employer/<id>`

