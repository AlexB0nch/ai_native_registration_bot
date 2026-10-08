from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as app_db
from app.bot.handlers import questions
from app.main import create_app
from app.models import Event
from app.services.events import sync_events_from_facts
from tests.helpers import (
    ADMIN_ID,
    USER_ID,
    TgHarness,
    app_client,
    make_message_update,
    make_sqlite_engine,
    update_json,
    webhook_headers,
)


async def test_health_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_db_down(client: httpx.AsyncClient, tmp_path) -> None:
    broken = make_sqlite_engine(f"sqlite+aiosqlite:///{tmp_path}/missing/dir/x.db")
    app_db.set_engine(broken)
    response = await client.get("/health")
    assert response.status_code == 503
    await broken.dispose()


@pytest.mark.parametrize("secret", [None, "", "wrong-secret"])
async def test_webhook_rejects_bad_secret(client: httpx.AsyncClient, tg: TgHarness, secret: str | None) -> None:
    update = make_message_update("/start")
    response = await client.post("/tg/webhook", json=update_json(update), headers=webhook_headers(secret))
    assert response.status_code == 403
    assert tg.session.calls == []


async def test_webhook_with_secret_processes_update(client: httpx.AsyncClient, tg: TgHarness) -> None:
    update = make_message_update("/start lp_opex_p1", username="Irina_Ops")
    response = await client.post("/tg/webhook", json=update_json(update), headers=webhook_headers())
    assert response.status_code == 200
    assert tg.session.last_text(USER_ID).startswith("Здравствуйте!")


async def test_webhook_returns_200_on_handler_error(client: httpx.AsyncClient, tg: TgHarness, monkeypatch) -> None:
    async def boom(message, state) -> None:
        raise RuntimeError("handler failed")

    monkeypatch.setattr(questions, "handle_free_text", boom)
    update = make_message_update("просто текст")
    response = await client.post("/tg/webhook", json=update_json(update), headers=webhook_headers())
    assert response.status_code == 200


async def test_webhook_returns_200_on_garbage(client: httpx.AsyncClient) -> None:
    response = await client.post("/tg/webhook", json={"foo": "bar"}, headers=webhook_headers())
    assert response.status_code == 200
    response = await client.post(
        "/tg/webhook", content=b"not json", headers={**webhook_headers(), "Content-Type": "application/json"}
    )
    assert response.status_code == 200


async def test_lifespan_syncs_events(client: httpx.AsyncClient, db: AsyncSession) -> None:
    events = {e.code: e for e in (await db.scalars(select(Event))).all()}
    assert set(events) == {
        "practicum",
        "course",
        "course_1",
        "course_2",
        "course_3",
        "course_4",
        "demo_day",
        "waitlist",
    }
    assert events["practicum"].starts_at == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert events["practicum"].ends_at == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
    assert events["course"].starts_at == datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert events["course"].ends_at == datetime(2026, 11, 28, 14, 0, tzinfo=UTC)
    assert events["course_4"].starts_at == datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
    assert events["demo_day"].starts_at == datetime(2026, 12, 5, 12, 0, tzinfo=UTC)
    assert events["waitlist"].starts_at is None
    assert events["course_2"].title.endswith("занятие 2")


async def test_sync_keeps_owner_links(engine, db: AsyncSession) -> None:
    await sync_events_from_facts(db)
    await db.commit()
    practicum = await db.scalar(select(Event).where(Event.code == "practicum"))
    practicum.join_url = "https://telemost.yandex.ru/j/123"
    practicum.recording_url = "https://example.test/rec"
    practicum.title = "старое название"
    await db.commit()

    await sync_events_from_facts(db)
    await db.commit()
    db.expire_all()
    practicum = await db.scalar(select(Event).where(Event.code == "practicum"))
    assert practicum.join_url == "https://telemost.yandex.ru/j/123"
    assert practicum.recording_url == "https://example.test/rec"
    assert practicum.title.startswith("Как консультанту")
    assert len((await db.scalars(select(Event))).all()) == 8


async def test_setup_bot_on_start(tg: TgHarness, monkeypatch) -> None:
    monkeypatch.setenv("SETUP_BOT_ON_START", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    app = create_app(bot=tg.bot, dispatcher=tg.dp, dispose_db_on_shutdown=False)
    async with app_client(app) as client:
        assert (await client.get("/health")).status_code == 200

    webhook = tg.session.calls_of("setWebhook")
    assert len(webhook) == 1
    assert webhook[0]["url"] == "https://reg.example.test/tg/webhook"
    assert webhook[0]["secret_token"] == "test-webhook-secret"
    assert webhook[0]["allowed_updates"] == ["message", "callback_query"]

    assert len(tg.session.calls_of("setMyDescription")[0]["description"]) <= 512
    assert len(tg.session.calls_of("setMyShortDescription")[0]["short_description"]) <= 120

    commands = tg.session.calls_of("setMyCommands")
    public = [c["command"] for c in commands[0]["commands"]]
    assert public == ["start", "practicum", "course", "question"]
    assert commands[0]["scope"]["type"] == "default"
    admin = commands[1]
    assert admin["scope"] == {"type": "chat", "chat_id": ADMIN_ID}
    assert [c["command"] for c in admin["commands"]] == [*public, "stats", "export", "link", "whoami"]


async def test_setup_bot_failure_is_not_fatal(tg: TgHarness, monkeypatch) -> None:
    monkeypatch.setenv("SETUP_BOT_ON_START", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    tg.session.fail("setWebhook", lambda m: TelegramBadRequest(method=m, message="Bad Request: bad webhook"))
    app = create_app(bot=tg.bot, dispatcher=tg.dp, dispose_db_on_shutdown=False)
    async with app_client(app) as client:
        assert (await client.get("/health")).status_code == 200
    assert tg.session.calls_of("setMyCommands")
