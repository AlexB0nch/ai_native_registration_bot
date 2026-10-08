"""Общая инфраструктура тестов: поддельный Telegram, SQLite в памяти, фабрики апдейтов.

Быстрый старт (фикстуры — в tests/conftest.py):

    async def test_flow(tg, db):                      # tg: TgHarness, db: AsyncSession
        await tg.send("/start lp_opex_p1")             # сообщение от USER_ID
        assert "Здравствуйте" in tg.session.last_text()
        await tg.click(MENU_PRICES)                    # нажатие inline-кнопки
        await tg.click_button("Страница курса")        # по подписи кнопки из последнего сообщения
        assert tg.session.calls_of("sendMessage")[-1]["chat_id"] == USER_ID

Сеть не используется: все вызовы Bot API пишутся в `FakeSession.calls` как
`(api_method, payload)` и получают правдоподобный ответ (Message для send*, True для set*).
"""

from __future__ import annotations

import itertools
import time
import typing
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import TelegramMethod
from aiogram.types import Contact, Message, MessageId, Update, User, WebhookInfo
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.models import Base

BOT_ID = 42
BOT_TOKEN = f"{BOT_ID}:TEST-TOKEN-NOT-REAL"
BOT_USERNAME = "test_reg_bot"
USER_ID = 100001  # обычный пользователь
OTHER_USER_ID = 100002
ADMIN_ID = 900001  # входит в ADMIN_CHAT_IDS (см. conftest)
WEBHOOK_SECRET = "test-webhook-secret"
SITE_SECRET = "test-site-secret"

Responder = Callable[[TelegramMethod[Any]], Any]


# --- поддельная сессия Bot API ----------------------------------------------------------------


class FakeSession(BaseSession):
    """Сессия aiogram без сети: записывает вызовы и возвращает правдоподобные ответы.

    - `calls` — список `(api_method, payload)`, payload — dict полей метода;
    - `methods` — сами объекты методов (если нужен тип, например `InputFile` документа);
    - `respond(api_method, value | callable)` — свой ответ на метод;
    - `block_chat(chat_id)` — любой метод с этим chat_id падает `TelegramForbiddenError`
      («пользователь заблокировал бота»);
    - `fail(api_method, exc_factory)` — метод падает с заданным исключением.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.methods: list[TelegramMethod[Any]] = []
        self._responders: dict[str, Responder] = {}
        self._blocked: set[int] = set()
        self._message_ids = itertools.count(1000)

    # настройка поведения

    def respond(self, api_method: str, value: Any) -> None:
        self._responders[api_method] = value if callable(value) else (lambda _m, v=value: v)

    def fail(self, api_method: str, exc_factory: Callable[[TelegramMethod[Any]], Exception]) -> None:
        def _raise(method: TelegramMethod[Any]) -> Any:
            raise exc_factory(method)

        self._responders[api_method] = _raise

    def block_chat(self, chat_id: int) -> None:
        self._blocked.add(chat_id)

    def clear(self) -> None:
        self.calls.clear()
        self.methods.clear()

    # чтение записанного

    def calls_of(self, api_method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == api_method]

    def sent_messages(self, chat_id: int | None = None) -> list[dict[str, Any]]:
        return [p for p in self.calls_of("sendMessage") if chat_id is None or p.get("chat_id") == chat_id]

    def sent_texts(self, chat_id: int | None = None) -> list[str]:
        return [p.get("text", "") for p in self.sent_messages(chat_id)]

    def last_text(self, chat_id: int | None = None) -> str:
        texts = self.sent_texts(chat_id)
        if not texts:
            raise AssertionError(f"бот не отправил ни одного сообщения (chat_id={chat_id})")
        return texts[-1]

    def last_message(self, chat_id: int | None = None) -> dict[str, Any]:
        messages = self.sent_messages(chat_id)
        if not messages:
            raise AssertionError(f"бот не отправил ни одного сообщения (chat_id={chat_id})")
        return messages[-1]

    def method_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    # BaseSession

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        name = method.__api_method__
        payload = method.model_dump(exclude_none=True, warnings=False)
        self.calls.append((name, payload))
        self.methods.append(method)
        chat_id = payload.get("chat_id")
        if isinstance(chat_id, int) and chat_id in self._blocked:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if name in self._responders:
            return self._responders[name](method)
        return self._default_result(bot, method, payload)

    def _default_result(self, bot: Bot, method: TelegramMethod[Any], payload: dict[str, Any]) -> Any:
        returning = method.__returning__
        options = set(typing.get_args(returning)) or {returning}
        if Message in options:
            return Message.model_validate(self._message_dict(bot, payload), context={"bot": bot})
        if returning is bool or bool in options:
            return True
        if returning is MessageId:
            return MessageId(message_id=next(self._message_ids))
        if returning is User:
            return User(id=bot.id, is_bot=True, first_name="Test bot", username=BOT_USERNAME)
        if returning is WebhookInfo:
            return WebhookInfo(url="", has_custom_certificate=False, pending_update_count=0)
        if typing.get_origin(returning) is list:
            return []
        raise NotImplementedError(
            f"FakeSession: нет ответа по умолчанию для {method.__api_method__}; "
            f"задайте его через session.respond({method.__api_method__!r}, ...)"
        )

    def _message_dict(self, bot: Bot, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = payload.get("chat_id")
        message: dict[str, Any] = {
            "message_id": payload.get("message_id") or next(self._message_ids),
            "date": int(time.time()),
            "chat": {"id": chat_id if isinstance(chat_id, int) else 0, "type": "private"},
            "from": {"id": bot.id, "is_bot": True, "first_name": "Test bot", "username": BOT_USERNAME},
        }
        if "text" in payload:
            message["text"] = payload["text"]
        if "caption" in payload:
            message["caption"] = payload["caption"]
        if "document" in payload:
            document = payload["document"]
            message["document"] = {
                "file_id": "fake-file-id",
                "file_unique_id": "fake-file-unique-id",
                "file_name": getattr(document, "filename", None) or "file",
            }
        if isinstance(payload.get("reply_markup"), dict) and "inline_keyboard" in payload["reply_markup"]:
            message["reply_markup"] = payload["reply_markup"]
        return message

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncIterator[bytes]:
        yield b""


def make_bot(session: FakeSession | None = None) -> Bot:
    return Bot(token=BOT_TOKEN, session=session or FakeSession())


# --- кнопки -----------------------------------------------------------------------------------


def inline_buttons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Все inline-кнопки из payload отправленного сообщения (плоским списком)."""
    markup = payload.get("reply_markup") or {}
    rows = markup.get("inline_keyboard") or []
    return [button for row in rows for button in row]


def button_texts(payload: dict[str, Any]) -> list[str]:
    markup = payload.get("reply_markup") or {}
    rows = markup.get("inline_keyboard") or markup.get("keyboard") or []
    return [button["text"] for row in rows for button in row]


def find_button(payload: dict[str, Any], text_contains: str) -> dict[str, Any]:
    for button in inline_buttons(payload):
        if text_contains in button["text"]:
            return button
    raise AssertionError(f"нет кнопки с «{text_contains}» среди {button_texts(payload)}")


# --- фабрики апдейтов -------------------------------------------------------------------------

_update_ids = itertools.count(1)
_message_ids = itertools.count(1)


def make_user(user_id: int = USER_ID, username: str | None = None, first_name: str = "Ирина") -> dict[str, Any]:
    user: dict[str, Any] = {"id": user_id, "is_bot": False, "first_name": first_name, "language_code": "ru"}
    if username:
        user["username"] = username
    return user


def make_message_update(
    text: str | None = None,
    user_id: int = USER_ID,
    username: str | None = None,
    contact: Contact | dict[str, Any] | None = None,
    *,
    first_name: str = "Ирина",
    chat_id: int | None = None,
    reply_to_message_id: int | None = None,
) -> Update:
    """Входящее сообщение (текст и/или контакт) в личном чате пользователя.

    `contact`: `Contact` или dict, например `{"phone_number": "+79123456789", "first_name": "Ирина",
    "user_id": USER_ID}`. `reply_to_message_id` — ответ реплаем на сообщение бота.
    """
    user = make_user(user_id, username, first_name)
    message: dict[str, Any] = {
        "message_id": next(_message_ids),
        "date": int(time.time()),
        "chat": {"id": chat_id if chat_id is not None else user_id, "type": "private"},
        "from": user,
    }
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            command = text.split(maxsplit=1)[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
    if contact is not None:
        message["contact"] = contact.model_dump(exclude_none=True) if isinstance(contact, Contact) else contact
    if reply_to_message_id is not None:
        message["reply_to_message"] = {
            "message_id": reply_to_message_id,
            "date": int(time.time()),
            "chat": message["chat"],
            "from": {"id": BOT_ID, "is_bot": True, "first_name": "Test bot", "username": BOT_USERNAME},
            "text": "…",
        }
    return Update.model_validate({"update_id": next(_update_ids), "message": message})


def make_callback_update(
    data: str,
    user_id: int = USER_ID,
    *,
    username: str | None = None,
    first_name: str = "Ирина",
    message_id: int | None = None,
    message_text: str = "…",
    chat_id: int | None = None,
) -> Update:
    """Нажатие inline-кнопки с `callback_data=data` под сообщением бота."""
    user = make_user(user_id, username, first_name)
    message = {
        "message_id": message_id or next(_message_ids),
        "date": int(time.time()),
        "chat": {"id": chat_id if chat_id is not None else user_id, "type": "private"},
        "from": {"id": BOT_ID, "is_bot": True, "first_name": "Test bot", "username": BOT_USERNAME},
        "text": message_text,
    }
    callback = {
        "id": str(next(_update_ids)),
        "from": user,
        "chat_instance": "test-chat-instance",
        "message": message,
        "data": data,
    }
    return Update.model_validate({"update_id": next(_update_ids), "callback_query": callback})


# --- прогон апдейтов --------------------------------------------------------------------------


@dataclass
class TgHarness:
    """Бот с поддельной сессией + диспетчер приложения (роутеры и хранилище FSM в тестовой базе)."""

    bot: Bot
    dp: Dispatcher
    session: FakeSession

    async def feed(self, update: Update) -> Any:
        """Прогнать апдейт через диспетчер; исключения хендлеров не глушатся."""
        return await self.dp.feed_update(self.bot, update)

    async def send(self, text: str | None = None, user_id: int = USER_ID, **kwargs: Any) -> Any:
        return await self.feed(make_message_update(text, user_id, **kwargs))

    async def click(self, data: str, user_id: int = USER_ID, **kwargs: Any) -> Any:
        return await self.feed(make_callback_update(data, user_id, **kwargs))

    async def click_button(self, text_contains: str, user_id: int = USER_ID, **kwargs: Any) -> Any:
        """Нажать inline-кнопку по подписи из последнего сообщения бота этому пользователю."""
        button = find_button(self.session.last_message(user_id), text_contains)
        if "callback_data" not in button:
            raise AssertionError(f"кнопка «{button['text']}» — ссылка, а не callback")
        return await self.click(button["callback_data"], user_id, **kwargs)


_current: TgHarness | None = None


def set_current_harness(harness: TgHarness | None) -> None:
    global _current
    _current = harness


async def feed(update: Update) -> Any:
    """Прогнать апдейт через текущий `tg` (фикстура conftest)."""
    if _current is None:
        raise RuntimeError("feed(): нет активного TgHarness — подключите фикстуру `tg`")
    return await _current.feed(update)


def build_harness() -> TgHarness:
    from app.bot.setup import build_dispatcher

    session = FakeSession()
    return TgHarness(bot=make_bot(session), dp=build_dispatcher(), session=session)


# --- база -------------------------------------------------------------------------------------


def make_sqlite_engine(url: str = "sqlite+aiosqlite://") -> AsyncEngine:
    """SQLite в памяти, одно соединение на все сессии (StaticPool), внешние ключи включены."""
    engine = create_async_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_postgres_engine(url: str) -> AsyncEngine:
    """Для прогона тестов на Postgres: `TEST_DATABASE_URL=postgresql+psycopg://… pytest -q`."""
    return create_async_engine(url, poolclass=NullPool)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# --- HTTP -------------------------------------------------------------------------------------


@asynccontextmanager
async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx-клиент к приложению с запущенным lifespan (в том же event loop, что и тест)."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def webhook_headers(secret: str | None = WEBHOOK_SECRET) -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}


def update_json(update: Update) -> dict[str, Any]:
    """Апдейт в том виде, в каком его присылает Telegram (для POST /tg/webhook)."""
    return update.model_dump(mode="json", by_alias=True, exclude_none=True)


def texts_contain(texts: Iterable[str], fragment: str) -> bool:
    return any(fragment in text for text in texts)
