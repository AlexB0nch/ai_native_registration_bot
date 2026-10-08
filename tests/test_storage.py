from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.bot.setup import build_dispatcher
from app.bot.storage import DbStorage
from app.models import FsmState
from tests.helpers import BOT_ID, USER_ID, make_bot


class Flow(StatesGroup):
    name = State()
    email = State()


KEY = StorageKey(bot_id=BOT_ID, chat_id=USER_ID, user_id=USER_ID)


async def test_state_and_data_roundtrip(engine: AsyncEngine) -> None:
    storage = DbStorage()
    assert await storage.get_state(KEY) is None
    assert await storage.get_data(KEY) == {}

    await storage.set_state(KEY, Flow.name)
    assert await storage.get_state(KEY) == "Flow:name"
    await storage.set_data(KEY, {"name": "Ирина", "n": 1})
    assert await storage.get_data(KEY) == {"name": "Ирина", "n": 1}
    assert await storage.update_data(KEY, {"email": "a@b.ru"}) == {"name": "Ирина", "n": 1, "email": "a@b.ru"}

    await storage.set_state(KEY, "Flow:email")
    assert await storage.get_state(KEY) == "Flow:email"
    assert await storage.get_data(KEY) == {"name": "Ирина", "n": 1, "email": "a@b.ru"}


async def test_state_survives_dispatcher_rebuild(engine: AsyncEngine) -> None:
    bot = make_bot()
    dp1 = build_dispatcher()
    ctx1 = FSMContext(storage=dp1.storage, key=KEY)
    await ctx1.set_state(Flow.email)
    await ctx1.update_data(name="Ирина")
    await dp1.storage.close()

    dp2 = build_dispatcher()  # новый диспетчер и новое хранилище — как после рестарта
    assert dp2.storage is not dp1.storage
    ctx2 = FSMContext(storage=dp2.storage, key=KEY)
    assert await ctx2.get_state() == Flow.email.state
    assert await ctx2.get_data() == {"name": "Ирина"}
    await bot.session.close()


async def test_keys_are_isolated(engine: AsyncEngine) -> None:
    storage = DbStorage()
    other = StorageKey(bot_id=BOT_ID, chat_id=USER_ID + 1, user_id=USER_ID + 1)
    await storage.set_state(KEY, "A:a")
    await storage.set_state(other, "B:b")
    assert await storage.get_state(KEY) == "A:a"
    assert await storage.get_state(other) == "B:b"


async def test_clear_removes_row(engine: AsyncEngine, db: AsyncSession) -> None:
    storage = DbStorage()
    ctx = FSMContext(storage=storage, key=KEY)
    await ctx.set_state(Flow.name)
    await ctx.update_data(x=1)
    assert await db.scalar(select(func.count()).select_from(FsmState)) == 1
    await ctx.clear()
    assert await ctx.get_state() is None
    assert await ctx.get_data() == {}
    assert await db.scalar(select(func.count()).select_from(FsmState)) == 0
