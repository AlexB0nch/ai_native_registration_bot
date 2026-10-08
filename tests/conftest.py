"""Фикстуры тестов. Реализация — в tests/helpers.py.

- окружение (autouse): тестовые переменные, кэши настроек, фактов и текстов сбрасываются;
- `engine`: SQLite в памяти со всеми таблицами, подставлен в `app.db`;
- `db`: `AsyncSession` к этой базе (для проверок и подготовки данных; не забывайте commit);
- `tg`: `TgHarness` — бот с `FakeSession` + диспетчер приложения; `tg.send(...)`, `tg.click(...)`;
- `app`, `client`: FastAPI-приложение с тем же ботом и httpx-клиент с запущенным lifespan.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests import helpers

TEST_ENV = {
    "BOT_TOKEN": helpers.BOT_TOKEN,
    "TELEGRAM_WEBHOOK_SECRET": helpers.WEBHOOK_SECRET,
    "PUBLIC_BASE_URL": "https://reg.example.test",
    "DATABASE_URL": "sqlite+aiosqlite://",
    "ADMIN_CHAT_IDS": str(helpers.ADMIN_ID),
    "PRIVACY_URL": "https://example.test/privacy",
    "SITE_WEBHOOK_SECRET": helpers.SITE_SECRET,
    "LLM_PROVIDER": "stub",
    "LLM_API_KEY": "",
    "SETUP_BOT_ON_START": "false",
    "LOG_LEVEL": "INFO",
}

# До импорта app.main (он создаёт приложение при импорте) — тестовое окружение.
os.environ.update(TEST_ENV)


def _reset_caches() -> None:
    from app.config import get_settings
    from app.facts import set_facts
    from app.texts import set_texts

    get_settings.cache_clear()
    set_facts(None)
    set_texts(None)


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """SQLite в памяти; с TEST_DATABASE_URL (postgresql+psycopg://…) — та же схема в Postgres."""
    from app import db as app_db

    pg_url = os.environ.get("TEST_DATABASE_URL")
    eng = helpers.make_postgres_engine(pg_url) if pg_url else helpers.make_sqlite_engine()
    await helpers.create_schema(eng)
    app_db.set_engine(eng)
    try:
        yield eng
    finally:
        app_db.set_engine(None)
        if pg_url:
            await helpers.drop_schema(eng)
        await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def tg(engine: AsyncEngine) -> AsyncIterator[helpers.TgHarness]:
    harness = helpers.build_harness()
    helpers.set_current_harness(harness)
    try:
        yield harness
    finally:
        helpers.set_current_harness(None)
        await harness.bot.session.close()


@pytest.fixture
def app(tg: helpers.TgHarness) -> FastAPI:
    from app.main import create_app

    return create_app(bot=tg.bot, dispatcher=tg.dp, dispose_db_on_shutdown=False)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with helpers.app_client(app) as http:
        yield http
