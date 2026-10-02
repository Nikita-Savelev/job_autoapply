# Чат HH (chatik)

Автоответы в переписке на [hh.ru/chat](https://hh.ru/chat).

- Решения и этапы: [PLAN.md](PLAN.md)
## Запуск

Нужны Postgres, `.env` с LLM и залогиненный Chrome-профиль. Подробности: [../README.md](../README.md).

```bash
# непрочитанные, пауза 2 минуты, выход
HH_PAUSE_SEC=1 .venv/bin/python run_chat.py --unread --fast --debug

# догон до 5 чатов подряд уже в БД, затем мониторинг
HH_PAUSE_SEC=1 .venv/bin/python run_chat.py --fast --debug

# один конкретный чат
.venv/bin/python run_chat.py --once --chat-id 5649719078 --fast --debug

# только черновик в логе, без вставки в UI
.venv/bin/python run_chat.py --once --chat-id 5649719078 --dry-run --fast --debug
```

Бот сам жмёт «Отправить», затем проверяет, что исходящее появилось в треде. Ручная отправка: `--manual-send`. `--fast` убирает паузу «набора»; пауза между действиями — `HH_PAUSE_SEC`.

После отправки бот ждёт 5 секунд следующее входящее. Если оно пришло, отвечает сразу и снова ждёт 5 секунд.

- Selenium: `hh/chat_ui.py` (тот же Chrome, что отклики)
- БД: `chat/store.py` → таблицы `chat_threads` / `chat_messages` / `chat_actions`

## Триггер

Отвечаем, если **последнее сообщение в треде не наше** (даже без бейджа unread).

## Статусы

`new` → `awaiting_us` → (verify) → `awaiting_them` | `needs_human` | `closed` | `error`

`closed` + новое непрочитанное → снова `awaiting_us`.

## Отладка

Подсветка кликов: `HH_HIGHLIGHT=1`. Пауза между действиями: `HH_PAUSE_SEC`. Статус `awaiting_them` ставится только после подтверждённой отправки.

## Эскалация в Telegram

Собес / ссылка на тест / бот застрял → TG + `bot_paused` (бот молчит до их следующего сообщения).

## DOM

`tests_example/chat.html` — список. `tests_example/chat_thread.html` — открытый тред (пузыри + composer).

Ключевые селекторы (уже в `hh/selectors.py` / `chat/dom_parse.py`):

- сообщения: `data-qa=chatik-chat-message-<id>`, наши — `chat-bubble_outgoing` / `message_my`
- текст: `data-qa=chat-bubble-text`
- ввод: `chatik-message-input` → `textarea[data-qa=text-input]`; отправка — кнопка «Отправить»
- шапка: `participant-info-title`, `participant-info-details` → `/employer/<id>`

