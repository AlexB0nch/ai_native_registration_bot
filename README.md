# ai_native_registration_bot

Telegram-бот [@AI_native_reg_bot](https://t.me/AI_native_reg_bot): запись на бесплатный
практикум и на курс «ИИ и вайбкодинг для консультанта по операционным преобразованиям»,
ответы на вопросы о курсе, приглашения и напоминания, уведомления владельцу.

Правила работы с репозиторием — [CLAUDE.md](CLAUDE.md). Устройство — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Задачи — [tasks/INDEX.md](tasks/INDEX.md).

## Локальный запуск

### В Docker (бот + Postgres)

```bash
cp .env.example .env          # заполнить BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, POSTGRES_PASSWORD
docker compose up --build
curl localhost:8000/health    # {"status":"ok"}
```

Контейнер сам применяет миграции (`alembic upgrade head`) и запускает один процесс uvicorn.
`DATABASE_URL` для локального compose собирается из `POSTGRES_PASSWORD`.
Без публичного адреса Telegram до вебхука не достучится — для проверки сценариев
используйте тесты, а для живой проверки — `SETUP_BOT_ON_START=false` и тестового бота с туннелем.

### Без Docker (разработка и тесты)

Python 3.12.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q                     # SQLite в памяти, без сети
```

Тесты на Postgres (как в проде), если он есть под рукой:

```bash
TEST_DATABASE_URL=postgresql+psycopg://bot:bot@localhost:5432/ai_native_test pytest -q
DATABASE_URL=postgresql+psycopg://bot:bot@localhost:5432/ai_native_bot \
  sh -c 'alembic upgrade head && alembic downgrade base && alembic upgrade head'
```

Запуск сервера: `alembic upgrade head && uvicorn app.main:app --port 8000 --workers 1`.
Настроить вебхук, описание и команды бота вручную: `python scripts/setup_bot.py`
(`--info` — только показать `getWebhookInfo`).

### Где что менять

| Что | Где |
|---|---|
| Даты, время, цены, ссылки | `config/facts.yaml` (проверяется при старте) |
| Тексты бота | `config/texts.yaml` (цены и даты — только подстановками `{price_early}`, `{practicum_slot}`…) |
| Переменные окружения | `.env.example` и `environment:` в `docker-compose.coolify.yml` |
| Таблицы | `app/models.py` + новая миграция в `alembic/versions/` |

### Тесты для новых сценариев

`tests/helpers.py` и фикстуры из `tests/conftest.py`:

```python
async def test_example(tg, db):
    await tg.send("/start lp_opex_p1")  # сообщение от USER_ID
    await tg.click_button("Цены и формат")  # inline-кнопка из последнего ответа
    assert "Тарифы" in tg.session.last_text()  # что бот отправил
    tg.session.calls_of("sendMessage")  # все вызовы Bot API без сети
```
