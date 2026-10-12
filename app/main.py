"""FastAPI: `/health`, `/tg/webhook`, роутеры `app/api/`.

Миграции здесь не запускаются — это делает команда контейнера (`alembic upgrade head`).
Процесс один (`uvicorn --workers 1`).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text

from app.api import site
from app.bot.setup import build_bot, build_dispatcher, setup_bot
from app.config import get_settings
from app.db import dispose_engine, get_engine, session_scope
from app.facts import get_facts
from app.services.events import sync_events_from_facts

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/tg/webhook"
SETUP_TIMEOUT_SECONDS = 30
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 — имя заголовка, не секрет


def _configure_logging() -> None:
    level = get_settings().log_level.upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(level)


def create_app(
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    *,
    dispose_db_on_shutdown: bool = True,
) -> FastAPI:
    """Фабрика приложения. Тесты передают `bot` с поддельной сессией и свой `dispatcher`."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        get_facts()  # битые факты — падение на старте, а не в разговоре

        try:
            async with session_scope() as session:
                await sync_events_from_facts(session)
        except Exception:
            log.exception("startup: синхронизация events не выполнена")

        app.state.bot = bot
        if app.state.bot is None and settings.bot_token:
            app.state.bot = build_bot(settings.bot_token)
        app.state.dp = dispatcher or build_dispatcher()

        if app.state.bot is None:
            log.warning("startup: BOT_TOKEN не задан, вебхук Telegram отключён")
        elif settings.setup_bot_on_start:
            try:
                # Telegram недоступен — не держим старт дольше healthcheck'а Coolify.
                await asyncio.wait_for(setup_bot(app.state.bot, settings), timeout=SETUP_TIMEOUT_SECONDS)
            except Exception:
                log.exception("startup: настройка бота не выполнена")

        try:
            yield
        finally:
            if bot is None and app.state.bot is not None:
                await app.state.bot.session.close()
            if dispose_db_on_shutdown:
                await dispose_engine()

    app = FastAPI(title="AI-native registration bot", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("health: база недоступна: %s", type(exc).__name__)
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(request: Request) -> JSONResponse:
        expected = get_settings().telegram_webhook_secret
        received = request.headers.get(SECRET_HEADER) or ""
        if not expected or not hmac.compare_digest(received.encode(), expected.encode()):
            return JSONResponse({"ok": False}, status_code=403)

        bot_: Bot | None = request.app.state.bot
        dp: Dispatcher = request.app.state.dp
        if bot_ is None:
            return JSONResponse({"ok": False}, status_code=503)

        update_id: Any = None
        try:
            payload = await request.json()
            update_id = payload.get("update_id") if isinstance(payload, dict) else None
            update = Update.model_validate(payload, context={"bot": bot_})
            result = await dp.feed_webhook_update(bot_, update)
            if isinstance(result, TelegramMethod):
                await bot_(result)
        except ValidationError:
            log.warning("webhook: некорректный update id=%s", update_id)
        except Exception:
            # Всегда 200, иначе Telegram будет повторять тот же update бесконечно.
            log.exception("webhook: ошибка обработки update id=%s", update_id)
        return JSONResponse({"ok": True})

    app.include_router(site.router)
    return app


_configure_logging()
app = create_app()
