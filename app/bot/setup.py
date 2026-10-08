"""Сборка бота и диспетчера, настройка бота в Telegram (вебхук, описание, команды)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from app.bot.handlers import admin, practicum, questions, start
from app.bot.storage import DbStorage
from app.config import Settings, get_settings
from app.texts import t

log = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "callback_query"]
PUBLIC_COMMANDS = ("start", "practicum", "course", "question")
ADMIN_COMMANDS = ("stats", "export", "link", "whoami")


def build_bot(token: str | None = None, session: BaseSession | None = None) -> Bot:
    token = token or get_settings().bot_token
    if not token:
        raise RuntimeError("BOT_TOKEN не задан")
    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )


def _routers() -> list[Router]:
    # Порядок важен: admin → start → practicum (FSM) → questions (catch-all).
    return [admin.router, start.router, practicum.router, questions.router]


def _attach(dp: Dispatcher, router: Router) -> None:
    """Подключить модульный роутер, отцепив его от прежнего диспетчера (тесты пересоздают dp)."""
    parent = router.parent_router
    if parent is not None:
        parent.sub_routers.remove(router)
        router._parent_router = None
    dp.include_router(router)


def build_dispatcher(storage: BaseStorage | None = None) -> Dispatcher:
    dp = Dispatcher(storage=storage or DbStorage())
    for router in _routers():
        _attach(dp, router)
    return dp


def _commands(names: tuple[str, ...]) -> list[BotCommand]:
    return [BotCommand(command=name, description=t(f"commands.{name}")) for name in names]


async def setup_bot(bot: Bot, settings: Settings | None = None) -> dict[str, bool]:
    """Вебхук, описание, короткое описание, команды (общие и для админов).

    Каждый шаг независим: ошибка пишется в лог и не прерывает остальные и запуск сервиса.
    Возвращает результат по шагам.
    """
    settings = settings or get_settings()
    results: dict[str, bool] = {}

    async def step(name: str, coro: Awaitable[object]) -> None:
        try:
            await coro
            results[name] = True
        except Exception as exc:
            results[name] = False
            log.warning("setup_bot: шаг %s не выполнен: %s: %s", name, type(exc).__name__, exc)

    if settings.public_base_url and settings.telegram_webhook_secret:
        await step(
            "webhook",
            bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.telegram_webhook_secret,
                allowed_updates=ALLOWED_UPDATES,
                drop_pending_updates=False,
            ),
        )
    else:
        results["webhook"] = False
        log.warning("setup_bot: PUBLIC_BASE_URL или TELEGRAM_WEBHOOK_SECRET не заданы, вебхук не ставится")

    await step("description", bot.set_my_description(description=t("description")))
    await step("short_description", bot.set_my_short_description(short_description=t("short_description")))
    await step(
        "commands",
        bot.set_my_commands(commands=_commands(PUBLIC_COMMANDS), scope=BotCommandScopeDefault()),
    )
    for chat_id in settings.admin_chat_ids:
        await step(
            f"admin_commands:{chat_id}",
            bot.set_my_commands(
                commands=_commands(PUBLIC_COMMANDS + ADMIN_COMMANDS),
                scope=BotCommandScopeChat(chat_id=chat_id),
            ),
        )
    return results
