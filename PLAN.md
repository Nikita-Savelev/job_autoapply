# План автооткликов (job_autoapply)

Цель: по готовой ссылке поиска hh.ru обходить вакансии, фильтровать по названию/роли, скрывать нерелевантные и откликаться на подходящие с сопроводительным письмом.

## Поток (happy path)

```
SEARCH_URL (фильтры уже в URL)
        │
        ▼
  scrape список карточек  ──►  PostgreSQL
        │                      (+ debug: HTML + DEBUG-логи)
        ▼
  один LLM-запрос: резюме + все названия страницы
        │
        ▼
  для каждой:
        ├─ reject ──► hide + skipped
        └─ suitable ──► отклик + applied
```

## Модули

| Модуль | Ответственность |
|--------|-----------------|
| `config.py` | `SEARCH_URL`, роль, Postgres, LLM, путь к резюме |
| `browser.py` / `login.py` | Chrome + сохранённый профиль |
| `db/` | PostgreSQL: вакансии, статусы, письмо, причины skip |
| `debug/` | HTML-дампы страниц |
| `logging_setup.py` | В `--debug` — DEBUG в консоль + `run.log` |
| `matcher/` | Один LLM-запрос: резюме + все названия → suitable/reject |
| `resume/` | Локальная копия резюме для промпта |
| `hh/` | Selenium: search, vacancy, actions, selectors |
| `cover_letter/` | Шаблон письма |
| `pipeline/` | scrape → LLM batch → skip/apply |
| `run.py` | CLI: `python run.py [--debug] [--live]` |

## Статусы вакансии в БД

| status | Смысл |
|--------|--------|
| `new` | Собрана со страницы поиска, ещё не решали |
| `skipped` | Не подошла по матчеру / вручную; на сайте скрыта |
| `applied` | Отклик отправлен |
| `error` | Сбой UI/парсинга (можно ретраить) |
| `blocked` | Уже откликались ранее / нет кнопки отклика / капча |

## Этапы реализации

1. **Фундамент** (сейчас) — каркас модулей, схема БД, matcher на правилах, stubs Selenium, CLI dry-run.
2. **Сбор списка** — рабочие селекторы поиска, пагинация, upsert в БД.
3. **Hide / skip** — «исключить из поиска» на карточке + запись причины.
4. **Отклик** — парсинг страницы вакансии, форма отклика, вставка письма.
5. **Письма** — шаблоны из `context/` / файла; позже A/B (P3).
6. **Усиление matcher** — reject team lead / другой стек; опционально смотреть сниппет описания.

## Риски

- Лимиты и антибот hh.ru — паузы, не headless сначала, не гонять сотни подряд без присмотра.
- Селекторы UI меняются — держать их в `hh/selectors.py`.
- Сессия в `.chrome_profile/` — не коммитить; при logout снова `login.py`.

## Письма

Канон структуры: `cover_letter/structure.md` (из гайда рекрутеров).  
Генерация: LLM под вакансию + компанию + описание + резюме (`cover_letter/generate.py`).

После клика «Откликнуться»:

1. **Тест** — HH просит заполнить опросник/тест → статус `blocked`, HTML-дамп, автоматизация позже.
2. **Письмо** (реализовано) — попап «отклик отправлен» → `vacancy-response-letter-toggle` → textarea `vacancy-response-popup-form-letter-input` → submit `vacancy-response-letter-submit`.

Паузы между действиями: `HH_PAUSE_SEC` (по умолчанию 2.5). Лимит откликов: `--apply-limit 1`.

При `python run.py --debug` каждый `driver.get` пишется в:

```
debug_pages/<YYYYMMDD_HHMMSS>/
  0001_search.html
  0001_search.meta.txt    # url, label, title
  index.tsv
```

Если scrape вернул 0 карточек или нет описания вакансии — дополнительный дамп с меткой `*_empty_*` / `*_no_description`.  
По этим файлам правим `hh/selectors.py`.

## Конфиг (ожидаемые env)

```bash
# hh_autoapply/.env
HH_SEARCH_URL=https://hh.ru/search/vacancy?...
HH_TARGET_ROLE=Python Backend Developer
HH_RESUME_PATH=resume/resume.txt
HH_PAUSE_SEC=2.5

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=https://api.openai.com/v1

PG_HOST=127.0.0.1
PG_PORT=5433
PG_NAME=hh_autoapply
PG_USER=hh_autoapply
PG_PASSWORD=hh_autoapply
```

Postgres: `docker compose up -d` в `hh_autoapply/` (порт **5433**).
