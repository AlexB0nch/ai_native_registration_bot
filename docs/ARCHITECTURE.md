# Архитектура

## Схема

```
Telegram ──webhook──→ ┌──────────────────────── bot (один процесс) ────────────────────────┐
                      │ FastAPI                                                             │
alexshein.com/api/    │  POST /tg/webhook            → aiogram Dispatcher (FSM в Postgres)  │
register (forms/) ───→│  POST /api/site-registrations → приём заявок с сайта (X-Webhook-Secret)
                      │  GET  /health                                                       │
                      │ APScheduler (P1): приглашения, напоминания, письмо после практикума │
                      └──────────────┬──────────────────────────────────────────────────────┘
                                     │ SQLAlchemy 2 (async, psycopg 3)
                                     ▼
                               Postgres 16 (том Coolify)
                                     ▲
                       DeepSeek API (LLM_PROVIDER=deepseek | anthropic | stub)
```

Coolify: ресурс Docker Compose `docker-compose.coolify.yml`, сервисы `bot` и `db`.
Домен `https://reg.alexshein.com` назначен сервису `bot` (порт 8000), TLS — Traefik Coolify.
DNS: A-запись `reg` в Timeweb **напрямую на сервер Coolify**, без DDoS-Guard — иначе
защита может отбивать POST-запросы Telegram на вебхук.

## Стек

Python 3.12 · aiogram 3 · FastAPI + uvicorn · SQLAlchemy 2 (async) + Alembic · psycopg 3 ·
pydantic-settings · PyYAML · httpx · email-validator · phonenumbers · APScheduler 3 (P1) ·
pytest + pytest-asyncio + aiosqlite (тесты на SQLite).

## Структура репозитория

```
app/
  main.py              FastAPI: lifespan (миграции не здесь), /health, /tg/webhook, роутеры api/
  config.py            Settings (env), get_settings()
  facts.py             загрузка и проверка config/facts.yaml → Facts (pydantic), хелперы
  texts.py             загрузка config/texts.yaml, t(key, **kw) с подстановкой фактов
  db.py                engine, async_sessionmaker, get_session()
  models.py            все модели SQLAlchemy
  services/
    people.py          найти/создать человека, привязка Telegram, слияние с заявкой с сайта
    registrations.py   запись на событие без дублей, статистика, выгрузка CSV
    notify.py          сообщения владельцу (ADMIN_CHAT_IDS)
    ics.py             .ics для события
    events.py          синхронизация events из facts.yaml при старте
  bot/
    setup.py           build_bot(), build_dispatcher(): подключение роутеров в фиксированном порядке
    storage.py         FSM-хранилище aiogram в таблице fsm_states
    payload.py         разбор параметра /start
    keyboards.py       клавиатуры
    filters.py         IsAdmin
    handlers/
      start.py         /start, меню, /whoami, «Цены и формат», «Что будет на курсе»
      practicum.py     сценарий записи на практикум (TASK-BOT-001)
      admin.py         /stats, /export, /link, /set_link (TASK-BOT-002)
      questions.py     свободный текст, «Задать вопрос», ответы владельца (TASK-BOT-002, LLM-001)
  llm/
    base.py, deepseek.py, anthropic.py, stub.py   клиенты, общий JSON-контракт
    prompt.py          системный промпт: правила + knowledge/*.md + facts
    guard.py           проверка ответа: цены и даты только из facts, запрещённые слова, длина
    faq.py             быстрые ответы по ключевым словам из facts (без модели)
    service.py         answer_question(text) → Answer(text, answered, handoff, cta)
  api/
    site.py            POST /api/site-registrations (TASK-API-001)
alembic/               миграции
config/facts.yaml      факты — единственный источник дат, времени, цен, ссылок
config/texts.yaml      тексты бота
knowledge/*.md         база знаний (собирается скриптом, правится руками)
materials/             исходники: тексты лендинга, брифы, файлы курса
scripts/
  build_knowledge.py   materials/ → knowledge/
  setup_bot.py         описание, короткое описание, команды, вебхук (вызывается и при старте)
tests/
```

## Порядок роутеров aiogram

`admin` → `start` → `practicum` (FSM) → `questions` (catch-all для текста вне сценария).
Каждый новый модуль добавляет свой `router` в `bot/setup.py` одной строкой.

## Данные

Все `id` — `bigint` автоинкремент; время — `timestamptz` в UTC; JSON — `sa.JSON`
(совместимо с SQLite в тестах).

| Таблица | Поля | Ограничения |
|---|---|---|
| `people` | `telegram_id` (bigint, null), `telegram_username` (lower, null), `tg_first_name`, `name`, `email` (lower), `phone` (E.164), `role` (`consultant`/`opex`/`transformation`/`other`/null), `consent_at`, `source` (метка первого входа, не перезаписывается), `utm` (JSON), `created_at`, `updated_at` | `unique(telegram_id)`; индексы по `telegram_username`, `email`, `phone` |
| `events` | `code` (`practicum`, `course`, `demo_day`, `course_1`…`course_4`, `waitlist`), `title`, `starts_at`, `ends_at`, `join_url`, `recording_url` | `unique(code)`; строки создаются/обновляются из `facts.yaml` при старте, `join_url`/`recording_url` задаёт владелец командой и синхронизация их не трогает |
| `registrations` | `person_id`, `event_id`, `channel` (`bot`/`site`), `status` (`registered`, `applied`, `confirmed`, `rejected`, `paid`, `cancelled`), `tariff`, `task`, `invoice`, `source`, `utm` (JSON), `site_id` (id заявки с сайта), `created_at`, `updated_at` | `unique(person_id, event_id)`, `unique(site_id)` |
| `deliveries` | `person_id`, `event_id`, `kind` (`invite_24h`, `reminder_1h`, `after_event`, `broadcast:<n>`), `status` (`sent`/`failed`/`blocked`), `sent_at`, `error` | `unique(person_id, event_id, kind)` — идемпотентность рассылок |
| `questions` | `person_id`, `text`, `bot_answer`, `created_at`, `admin_chat_id`, `admin_message_id`, `answer_text`, `answered_at` | — |
| `fsm_states` | `key` (pk, строка из StorageKey), `state`, `data` (JSON), `updated_at` | — |

## Метки `/start`

Допустимы `A–Z a–z 0–9 _ -`, до 64 символов; иначе метка игнорируется.

| Метка | Сценарий |
|---|---|
| `prk`, `prk_*`, `lp_*` | практикум |
| `prk_web`, `crs_web` | найти заявку с сайта по username и привязать чат (TASK-API-001), иначе практикум / курс |
| `crs`, `crs_*` | курс (P1; до него — меню с пометкой «запись на курс откроется скоро») |
| `wait` | лист ожидания (P1) |
| любая другая или без метки | приветствие и меню |

Метка сохраняется в `people.source` только при первом контакте и в `registrations.source`
при создании записи.

## Ответы на вопросы

1. `faq.py`: вопросы о цене, датах и времени, «нужно ли программировать», «что на практикуме»
   отвечаются шаблонами из `texts.yaml` с подстановкой фактов, без модели.
2. Иначе `llm/service.py`: системный промпт = правила тона и запреты + `knowledge/*.md` + блок
   фактов из `facts.yaml`. Модель возвращает JSON
   `{"text": str, "answered": bool, "handoff": bool, "cta": "practicum"|"course_page"|"none"}`.
3. `guard.py` проверяет ответ: каждая сумма в ₽ и каждая дата должны быть в фактах, нет
   запрещённых слов, длина ≤ 600 знаков. Не прошёл → честный ответ «уточню у Александра» + handoff.
4. `answered=false` или `handoff=true` → запись в `questions` и сообщение владельцу с кнопкой
   «Ответить»; ответ владельца уходит пользователю от имени бота.
5. Модель недоступна → то же, что handoff. Бот никогда не молчит.
