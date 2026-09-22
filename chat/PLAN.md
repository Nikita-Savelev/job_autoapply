# Модуль чата HH (chatik)

Автоответы в переписке на [hh.ru/chat](https://hh.ru/chat). Тот же Chrome / окно, что и отклики; ориентир по DOM — `tests_example/chat.html` (список) + селекторы chatik из писем.

## Зафиксированные решения

| Тема | Решение |
|------|---------|
| Триггер ответа | Последнее сообщение в треде **не наше** (не только unread). Прочитали и ушли без ответа → всё равно в очереди |
| Холодный оффер / скрининг | Авто: тот же канон ответов + чуть энтузиазма, 1–2 коротких встречных вопроса |
| Зов на собес / ссылка на тест / «бот не тянет» | Telegram → ты отвечаешь руками → бот **молчит до следующего их сообщения** (`bot_paused`) |
| Процесс | Один браузер/профиль; чат — соседняя вкладка/URL в том же Chrome, не отдельный демон с другим профилем |
| Delay | `base + len/cps`, clamp **15–180 с**. Если с последнего сообщения HR уже прошло ≥ этой паузы — **ждём 0** (отправляем сразу). |
| БД | Все диалоги + сообщения + статусы; в контексте треда — вакансия, компания, резюме |

## Поток: мониторинг (по умолчанию)

Рабочий режим: `python run_chat.py --loop` (или `--once`).

Список чатов подгружается **лениво**. Monitor смотрит только **верх** — первые N viewport-страниц (по умолчанию **3**).

```
open https://hh.ru/chat
собрать карточки с первых N стр. (scroll вниз N−1 раз)

для каждой карточки сверху вниз:
  превью совпадает с БД? → skip (не открываем)
  новый чат ИЛИ превью изменилось →
      открыть тред → sync
      bot_paused? → skip
      последнее наше? → awaiting_them
      последнее их? → classify → compose → sleep(typing delay) → send

конец прохода → sleep HH_CHAT_SWEEP_SLEEP_SEC (~60 с в monitor)
```

Триггер ответа: **новый чат** или **смена `last_message_preview`** в списке (не unread-бейдж).
Задержка ответа — как раньше: `base + len/cps`, clamp 15–180 с; если с inbound уже прошло ≥ паузы — ждём 0.

| Режим | CLI | Что делает |
|-------|-----|------------|
| **monitor** | по умолчанию (`--pages 3`) | первые N стр.; open только new / preview change |
| **force sweep** | `--force` | весь список; streak/status-скипы выкл.; превью без изменений всё равно skip |
| **один тред** | `--chat-id` | точечно |

Полный force — редко (после долгого простоя / аудит). Для «зависших» awaiting_us — `run_chat_problems.py`.

## Поток: один тред (после открытия)

```
classify
  ├─ screening / вопросы / cold  → compose → delay(15..180) → send → awaiting_them
  ├─ escalate (собес / тест / TG-увод / stuck) → TG + bot_paused + needs_human
  └─ отказ работодателя → closed / короткий ack (опц.)
```

## Статусы чата (предложение)

| status | Когда | Sweep |
|--------|--------|--------|
| `new` | Только увидели в списке / ещё без sync | открыть, синхронизировать |
| `awaiting_us` | Последнее сообщение их, нужен ответ | compose → fill → manual send → verify |
| `awaiting_them` | Наше исходящее **подтверждено в DOM** | считает streak стопа (×3) |
| `needs_human` | Эскалация + `bot_paused` | skip до нового inbound |
| `closed` | Отказ / диалог закрыт | **новое непрочитанное → `awaiting_us`** |
| `error` | fill/verify/UI fail | ретрай позже; **не** ставим `awaiting_them` |

Важно: `awaiting_them` в БД только после verify, что пузырь исходящий появился.

## Отладка (как в apply)

- `HH_HIGHLIGHT=1` — подсветка кликов + баннер
- `HH_PAUSE_SEC=2.5` — пауза после действий
- **Пока не жмём «Отправить»**: бот только вставляет текст → ты жмёшь send в UI → Enter в терминале → verify → статус

## Escalate → Telegram (бот стоп до их следующего сообщения)

Срабатывает и шлёт TG, выставляет `bot_paused` + `needs_human`:

- явное приглашение на собеседование / выбор слота / календарь;
- просьба перейти по ссылке и пройти тестовое / форму / задание вне чата;
- бот не уверен / LLM fail / странный UI / капча;
- всё, что политика пометит как `needs_human`.

Пока `bot_paused`: цикл **не пишет** в тред. Сброс паузы: появилось **новое входящее** после паузы (ты уже ответил или они написали снова) → можно снова авто.

## Контекст на каждый тред

При compose в промпт:

1. история сообщений (последние N);
2. резюме (`resume/`);
3. вакансия (из `vacancies`, если сматчили `vacancy_hh_id`);
4. компания (из `companies` / кэш);
5. канон `job_search/context/` + contacts (ЗП, офис, РФ, GitHub…).

Тон для cold/invite-screening: те же факты, чуть теплее согласие, 1–2 уточняющих вопроса (формат, стек, этап).

## БД (черновик таблиц)

```
chat_threads
  chat_id PK
  title, subtitle, url
  source: response | inbound | unknown
  status: new | awaiting_us | awaiting_them | needs_human | closed | error
  bot_paused BOOLEAN          -- молчать до нового inbound (обычно с needs_human)
  paused_reason TEXT
  vacancy_hh_id → vacancies
  company_hh_id → companies
  company_name, last_inbound_at, last_outbound_at
  last_inbound_hash           -- dedup / детект «новое после паузы»
  created_at, updated_at

chat_messages
  id, chat_id FK
  direction: in | out
  text, sent_at
  external_id?, author_label?
  our_action: auto | human | none

chat_actions
  id, chat_id
  kind: reply | skip | escalate
  draft, delay_sec, status, error, created_at
```

Django admin — вкладка «Чаты» (этап после sync UI).

## Selenium

- Список: `data-qa^=chatik-open-chat-`, `chat-cell-title/subtitle/...` — уже в `chat.html`.
- Пузыри + инпут: зафиксированы по `tests_example/chat_thread.html`
  (`chat-bubble_outgoing` / `incoming`, `chatik-chat-message-<id>`, composer
  `chatik-message-input` + Enter). Парсер: `chat/dom_parse.py`.

## Слои кода

| Путь | Роль |
|------|------|
| `chat/models.py` | dataclass + статусы / pause |
| `chat/classify.py` | intent + escalate triggers |
| `chat/compose.py` | LLM (канон + mild enthusiasm для cold) |
| `chat/delay.py` | 15–180 с |
| `chat/pipeline.py` | оркестрация + уважение `bot_paused` |
| `chat/store.py` | Postgres upsert тредов/сообщений |
| `hh/chat_ui.py` | DOM |
| `notify/telegram.py` | escalate |
| `run_chat.py` | CLI; позже — вызов из общего runner |

## Этапы

1. Каркас + решения в PLAN (сейчас).
2. Таблицы `chat_*` + sync заглушка.
3. Список тредов + эвристика «последнее не наше» + дамп открытого треда.
4. Compose dry-run в БД.
5. Send + delay + TG escalate + `bot_paused`.
6. Админка чатов.
