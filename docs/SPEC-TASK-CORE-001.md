# SPEC: TASK-CORE-001 — основа сервиса

**TASK-ID:** TASK-CORE-001
**Branch:** feature/core
**Status:** Ready for implementation
**Owner:** Claude-in-worktree

## Goal

Рабочий каркас бота: контейнер для Coolify, CI, конфиг, факты и тексты, все таблицы,
вебхук Telegram, `/start` с метками, меню и `/whoami`. После этой задачи следующие
задачи (BOT-001, BOT-002, LLM-001, API-001) идут параллельно, каждая в своём модуле.

## Context

- Устройство, структура папок, таблицы и метки: [ARCHITECTURE.md](ARCHITECTURE.md). Следовать ему буквально.
- Правила: [CLAUDE.md](../CLAUDE.md). Решения: [PLAN.md](PLAN.md).
- Образцы из pppp (`AlexB0nch/pppp`, читать при необходимости): `app/core/config.py`,
  `docker-compose.coolify.yml` (обязательные переменные через `${VAR:?}`), `Dockerfile`.

## Files

```
requirements.txt, requirements-dev.txt, pyproject.toml (ruff + pytest), Dockerfile,
docker-compose.yml (локально: bot + db), docker-compose.coolify.yml, .env.example,
.github/workflows/ci.yml, alembic.ini, alembic/env.py, alembic/script.py.mako,
alembic/versions/0001_initial.py,
app/__init__.py, app/main.py, app/config.py, app/facts.py, app/texts.py, app/db.py, app/models.py,
app/services/__init__.py, app/services/people.py, app/services/notify.py, app/services/events.py,
app/bot/__init__.py, app/bot/setup.py, app/bot/storage.py, app/bot/payload.py,
app/bot/keyboards.py, app/bot/filters.py,
app/bot/handlers/__init__.py, app/bot/handlers/start.py,
app/bot/handlers/practicum.py, app/bot/handlers/admin.py, app/bot/handlers/questions.py (заглушки),
app/api/__init__.py, app/api/site.py (заглушка), app/llm/__init__.py,
config/facts.yaml, config/texts.yaml, scripts/setup_bot.py,
tests/conftest.py, tests/helpers.py, tests/test_facts.py, tests/test_payload.py,
tests/test_start.py, tests/test_webhook.py, tests/test_storage.py, tests/test_migrations.py,
README.md (раздел «Локальный запуск»), tasks/TASK-CORE-001.md
```

## Требования

### Окружение (`app/config.py`, `.env.example`)

`BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `PUBLIC_BASE_URL` (`https://reg.alexshein.com`),
`DATABASE_URL` (`postgresql+psycopg://bot:<pw>@db:5432/ai_native_bot`), `POSTGRES_PASSWORD`,
`ADMIN_CHAT_IDS` (через запятую → `list[int]`), `PRIVACY_URL` (по умолчанию `https://alexshein.com/privacy`),
`SITE_WEBHOOK_SECRET`, `LLM_PROVIDER` (`deepseek`|`anthropic`|`stub`, по умолчанию `stub`),
`LLM_API_KEY`, `LLM_BASE_URL` (`https://api.deepseek.com`), `LLM_MODEL` (`deepseek-chat`),
`LLM_TIMEOUT_SECONDS` (30), `SETUP_BOT_ON_START` (true), `LOG_LEVEL` (INFO).
Все — в `environment:` сервиса `bot` в `docker-compose.coolify.yml`; `BOT_TOKEN`, `DATABASE_URL`,
`POSTGRES_PASSWORD`, `TELEGRAM_WEBHOOK_SECRET` — обязательные (`${VAR:?...}`).

### Контейнер и Coolify

- `Dockerfile`: `python:3.12-slim`, непривилегированный пользователь, команда
  `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers`.
- `docker-compose.coolify.yml`: `bot` (build, `expose: 8000`, healthcheck `GET /health`,
  `depends_on: db: service_healthy`) и `db` (`postgres:16-alpine`, том `postgres_data`,
  healthcheck `pg_isready`). Без `ports:`, без `env_file`. Домен назначается в Coolify сервису `bot`.
- `docker-compose.yml` — то же для локального запуска с `env_file: .env` и `ports: 127.0.0.1:8000:8000`.

### CI (`.github/workflows/ci.yml`, на `pull_request` и `push` в `main`)

1. `ruff check .`
2. `pytest -q` (SQLite, без сети).
3. Миграции на Postgres (service container `postgres:16`): `alembic upgrade head && alembic downgrade base && alembic upgrade head`.
4. `docker compose -f docker-compose.coolify.yml config -q` с фиктивными обязательными переменными.
5. Поиск секретов в diff: токен Telegram `[0-9]{8,10}:[A-Za-z0-9_-]{35}`, `sk-[A-Za-z0-9]{20,}`, файл `.env` в индексе — ошибка.

### Факты (`config/facts.yaml` → `app/facts.py`)

Модель pydantic, загрузка один раз (`get_facts()`), ошибка схемы = падение на старте.
Значения (все из `materials/COURSE-DECISIONS.md` и решений владельца):

```yaml
timezone: Europe/Moscow
author: {name: Александр Шеин, role: консультант и советник CEO по операционным трансформациям,
         telegram_url: https://t.me/Alex81Shein, channel_url: https://t.me/lean4rus}
links: {course_page: https://alexshein.com/ai-native, gift_bot: https://t.me/Alex_shein_gift_bot,
        gift_after_practicum: https://t.me/Alex_shein_gift_bot?start=after_prk}
practicum:
  title: Как консультанту за 90 минут превратить материалы диагностики в управленческую историю и прототип клиентского инструмента
  date: 2026-10-31
  start: "15:00"
  end: "17:00"
  free: true
  platform: Яндекс Телемост
  case: обезличенная трёхдневная диагностика на стройплощадке (сентябрь 2026)
course:
  title: ИИ и вайбкодинг для консультанта по операционным преобразованиям
  sessions: [2026-11-07, 2026-11-14, 2026-11-21, 2026-11-28]
  start: "15:00"        # null → «время уточняется»
  end: "17:00"
  session_hours: 2
  demo_day: {date: 2026-12-05, start: "15:00", end: "17:00"}
  format: [онлайн, практические задания между занятиями, библиотека шаблонов и промптов,
           Telegram-чат курса, записи занятий, итоговый MVP]
  chat_invite_url: null
  tariffs:
    - {code: early, name: Early Bird, price_rub: 24900, until: 2026-11-02,
       includes: [4 живых занятия и демо-день, записи занятий, шаблоны и библиотека промптов, Telegram-чат курса],
       payment_url: null}
    - {code: standard, name: Standard, price_rub: 29900,
       includes: [всё из Early Bird, проверка итогового MVP по чек-листу с письменной обратной связью], payment_url: null}
    - {code: pro, name: Pro, price_rub: 44900, max_seats: 3,
       includes: [всё из Standard, индивидуальный разбор MVP 60–90 минут], payment_url: null}
```

Хелперы в `facts.py`: `event_start_utc(date, time)`, `format_date_ru(date)` → «31 октября, суббота»,
`format_slot(date, start, end)` → «31 октября, суббота, 15:00–17:00 МСК» (при `start=null` → «…, время уточняется»),
`format_price(rub)` → «24 900 ₽» (неразрывный пробел), `tariff_available(code, now, taken_seats)`
(Early Bird — до конца 02.11 по МСК включительно; Pro — пока `taken_seats < max_seats`),
`all_prices_rub()` и `all_dates()` — для проверки ответов модели (LLM-001).

### Тексты (`config/texts.yaml` → `app/texts.py`)

`t(key, **kw)` подставляет `{practicum_slot}`, `{practicum_date_short}` («31 октября»), `{platform}`, `{session_hours}`, `{practicum_title}`, `{course_title}`,
`{course_dates}` («7, 14, 21 и 28 ноября»), `{course_time}`, `{demo_day_slot}`, `{price_early}`,
`{price_standard}`, `{price_pro}`, `{early_until}` («2 ноября включительно»), `{course_page}`,
`{author_name}` из фактов плюс переданные `kw`. Неизвестный ключ — исключение в тестах.

Тексты этой задачи (тон по CLAUDE.md, на «вы», ≤ 1 эмодзи):

- `welcome`: «Здравствуйте! Я бот {author_name}, консультанта по операционным трансформациям.
  Запишу вас на бесплатный практикум {practicum_date_short}, расскажу о курсе «{course_title}»
  и передам Александру ваш вопрос.»
- кнопки меню: «Записаться на практикум {practicum_date_short}», «Что будет на курсе»,
  «Цены и формат», «Задать вопрос».
- `course_about`: 4 занятия по {session_hours} часа по субботам {course_dates}, {course_time};
  демо-день {demo_day_slot}; что делаем (кратко по `materials/LANDING-COPY.md`, разделы 5–6) + кнопка «Страница курса».
- `prices`: три тарифа с ценой и составом, срок Early Bird, «Pro — не более 3 мест»,
  «Оплата через ЮKassa после подтверждения участия», формат курса + кнопки «Страница курса», «Записаться на практикум».
  Никакого дефицита и сроков сверх фактов.
- `description` (≤ 512) и `short_description` (≤ 120) бота; описания команд.
- `fallback_text`: «Выберите действие в меню или нажмите «Задать вопрос».» (до BOT-002).

### Таблицы и миграция

Все таблицы из ARCHITECTURE.md одной миграцией `0001_initial` (с `downgrade`). Модели в `app/models.py`.

### Бот

- `app/bot/storage.py`: `BaseStorage` aiogram 3 поверх таблицы `fsm_states` (set/get state, set/get data, close).
- `app/bot/payload.py`: `parse_start_payload(text) -> StartPayload(raw: str|None, scenario: Literal["practicum","course","site_practicum","site_course","waitlist","menu"])`
  по таблице меток из ARCHITECTURE.md; недопустимые символы или длина > 64 → `raw=None, scenario="menu"`.
- `app/services/people.py`: `get_or_create_by_telegram(session, tg_user, source)` — создаёт человека
  с `source` при первом контакте; `source` и `created_at` потом не меняются; обновляет `telegram_username`
  (lower) и `tg_first_name`.
- `app/services/events.py`: `sync_events_from_facts(session)` — upsert `practicum`, `course`,
  `course_1..4`, `demo_day`, `waitlist` по `code`; не трогает `join_url`, `recording_url`.
- `app/services/notify.py`: `notify_admins(bot, text, reply_markup=None)` — всем `ADMIN_CHAT_IDS`, ошибки логируются без ПД.
- `handlers/start.py`: `/start [payload]` → человек + приветствие + меню. Сценарий из метки передаётся
  дальше: для `practicum`/`site_practicum` — отправить событие `StartScenario` в роутер практикума
  (функция `start_practicum(message, state)` из `practicum.py`; в заглушке — просто меню).
  Кнопки «Что будет на курсе» и «Цены и формат» → тексты `course_about` и `prices`.
  `/whoami` → «Ваш chat_id: N» (для всех).
- `handlers/practicum.py`, `admin.py`, `questions.py`, `api/site.py` — заглушки с пустыми `router`
  и функциями-точками входа, чтобы следующие задачи меняли только свои файлы. `questions.py` в заглушке:
  любой текст вне сценария → `fallback_text` + меню.
- `app/bot/setup.py`: роутеры в порядке admin → start → practicum → questions.
- `scripts/setup_bot.py` (и вызов из lifespan при `SETUP_BOT_ON_START=true` и заданном `BOT_TOKEN`):
  `setWebhook(url=PUBLIC_BASE_URL + "/tg/webhook", secret_token=TELEGRAM_WEBHOOK_SECRET,
  allowed_updates=["message","callback_query"])`, `setMyDescription`, `setMyShortDescription`,
  `setMyCommands` (общие: `start`, `practicum`, `course`, `question`; для каждого чата из `ADMIN_CHAT_IDS`
  через `BotCommandScopeChat` добавить `stats`, `export`, `link`, `whoami`). Ошибка — лог, не падение.

### HTTP (`app/main.py`)

- `GET /health` → `{"status":"ok"}`; 503, если БД недоступна.
- `POST /tg/webhook`: заголовок `X-Telegram-Bot-Api-Secret-Token` ≠ секрет → 403; иначе
  `dp.feed_webhook_update`, всегда 200 (исключения логируются, чтобы Telegram не ретраил бесконечно).
- В lifespan: `sync_events_from_facts`, настройка бота.

## Acceptance Criteria

- A1. `pytest -q` и `ruff check .` зелёные; CI в PR зелёный (все 5 шагов).
- A2. `docker compose up --build` поднимает `bot` и `db`; `curl localhost:8000/health` → 200.
- A3. `/start lp_opex_p1` от нового пользователя создаёт `people` с `source=lp_opex_p1`; повторный `/start crs` source не меняет (тест).
- A4. Метки: `prk`, `lp_opex_p1`, `prk_web`, `crs`, `crs_web`, `wait`, `tgads_opex_w2`, пусто, `bad!payload`, 65 символов → ожидаемые сценарии (тест).
- A5. Вебхук без секрета или с неверным → 403; с верным → 200 и ответ бота (тест с поддельной сессией бота).
- A6. FSM-состояние переживает пересоздание диспетчера (тест хранилища).
- A7. `facts.yaml` с ошибкой схемы → понятное исключение; `format_slot`, `format_price`, `tariff_available` покрыты тестами (граница Early Bird: 02.11 23:59 МСК — доступен, 03.11 00:00 МСК — нет).
- A8. В текстах нет цен и дат литералами — только подстановки (тест: в `texts.yaml` нет `₽` с цифрами и названий месяцев с числами).

## Tests

`tests/helpers.py` — общая инфраструктура для следующих задач: SQLite in-memory движок,
поддельная сессия aiogram, записывающая вызовы методов Bot API (`calls: list[(method, payload)]`),
фабрики `make_message_update(text, user_id, username=None, contact=None)` и
`make_callback_update(data, user_id)`, функция `feed(update)`.

## Constraints

- Не писать ПД в логи. Не хранить токен в коде. Один процесс.
- Цены и даты — только из `facts.yaml`.

## Out of Scope

Сценарий практикума, админ-команды, ответы на вопросы, приём заявок с сайта, рассылки.

## Rollback plan

Первый деплой; откат — остановить ресурс в Coolify.
