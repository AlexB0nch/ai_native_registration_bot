from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards
from app.bot.handlers import start as start_handlers
from app.bot.handlers.practicum import START_SOURCE_KEY
from app.models import Person
from app.services.notify import notify_admins
from tests.helpers import (
    ADMIN_ID,
    BOT_ID,
    OTHER_USER_ID,
    USER_ID,
    TgHarness,
    button_texts,
    feed,
    make_message_update,
)


async def _person(db: AsyncSession, telegram_id: int = USER_ID) -> Person:
    db.expire_all()
    person = await db.scalar(select(Person).where(Person.telegram_id == telegram_id))
    assert person is not None
    return person


async def test_start_creates_person_with_source_once(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start lp_opex_p1", username="Irina_Ops", first_name="Ирина")
    person = await _person(db)
    assert person.source == "lp_opex_p1"
    assert person.telegram_username == "irina_ops"
    assert person.tg_first_name == "Ирина"
    created_at = person.created_at

    await tg.send("/start crs", username="Irina_New", first_name="Ира")
    person = await _person(db)
    assert person.source == "lp_opex_p1"
    assert person.created_at == created_at
    assert person.telegram_username == "irina_new"
    assert person.tg_first_name == "Ира"
    assert await db.scalar(select(func.count()).select_from(Person)) == 1


async def test_start_without_label_has_no_source(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    assert (await _person(db)).source is None
    await tg.send("/start bad!payload", user_id=OTHER_USER_ID)
    assert (await _person(db, OTHER_USER_ID)).source is None


async def test_start_sends_welcome_with_menu(tg: TgHarness) -> None:
    await tg.send("/start")
    message = tg.session.last_message(USER_ID)
    assert message["text"].startswith("Здравствуйте!")
    assert button_texts(message) == [
        "Записаться на практикум 31 октября",
        "Что будет на курсе",
        "Цены и формат",
        "Задать вопрос",
    ]


async def test_course_label_says_course_opens_soon(tg: TgHarness) -> None:
    await tg.send("/start crs_tg")
    texts = tg.session.sent_texts(USER_ID)
    assert len(texts) == 2
    assert "Запись на курс откроется скоро" in texts[-1]


async def test_practicum_label_calls_entry_point(tg: TgHarness, monkeypatch) -> None:
    called: list[tuple[int, dict]] = []

    async def fake_start_practicum(message: Message, state: FSMContext) -> None:
        called.append((message.from_user.id, await state.get_data()))

    monkeypatch.setattr(start_handlers, "start_practicum", fake_start_practicum)
    await tg.send("/start lp_opex_p1")
    await tg.send("/start prk_web")
    await tg.send("/start tgads_opex_w2")
    assert called == [
        (USER_ID, {START_SOURCE_KEY: "lp_opex_p1"}),
        (USER_ID, {START_SOURCE_KEY: "prk_web"}),
    ]


async def test_start_clears_fsm_state(tg: TgHarness) -> None:
    key = StorageKey(bot_id=BOT_ID, chat_id=USER_ID, user_id=USER_ID)
    await tg.dp.storage.set_state(key, "SomeFlow:step")
    await tg.send("/start")
    assert await tg.dp.storage.get_state(key) is None


async def test_whoami(tg: TgHarness) -> None:
    await feed(make_message_update("/whoami", user_id=OTHER_USER_ID))
    assert tg.session.last_text(OTHER_USER_ID) == f"Ваш chat_id: {OTHER_USER_ID}"


async def test_menu_course_and_prices(tg: TgHarness) -> None:
    await tg.click(keyboards.MENU_COURSE)
    assert tg.session.calls_of("answerCallbackQuery")
    about = tg.session.last_message(USER_ID)
    assert about["text"].startswith("Курс «")
    assert "Страница курса" in button_texts(about)
    assert any(b.get("url") == "https://alexshein.com/ai-native" for b in about["reply_markup"]["inline_keyboard"][0])

    await tg.click(keyboards.MENU_PRICES)
    prices = tg.session.last_message(USER_ID)
    assert prices["text"].startswith("Тарифы курса")
    assert button_texts(prices) == ["Страница курса", "Записаться на практикум 31 октября"]

    await tg.send("/course")
    assert tg.session.last_text(USER_ID).startswith("Курс «")


async def test_main_menu_button_clears_state(tg: TgHarness) -> None:
    key = StorageKey(bot_id=BOT_ID, chat_id=USER_ID, user_id=USER_ID)
    await tg.dp.storage.set_state(key, "SomeFlow:step")
    await tg.click(keyboards.MENU_MAIN)
    assert await tg.dp.storage.get_state(key) is None
    assert tg.session.last_text(USER_ID) == "Выберите, что вас интересует."


async def test_free_text_and_unknown_button_get_fallback(tg: TgHarness) -> None:
    await tg.send("Сколько стоит курс?")
    assert tg.session.last_text(USER_ID) == "Выберите действие в меню или нажмите «Задать вопрос»."
    tg.session.clear()
    await tg.click("unknown:button")
    assert tg.session.calls_of("answerCallbackQuery")
    assert tg.session.last_text(USER_ID).startswith("Выберите действие")
    tg.session.clear()
    await tg.send(None, contact={"phone_number": "+79123456789", "first_name": "Ира", "user_id": USER_ID})
    assert tg.session.last_text(USER_ID).startswith("Выберите действие")


async def test_admin_commands_from_non_admin_fall_through(tg: TgHarness) -> None:
    await tg.send("/stats", user_id=OTHER_USER_ID)
    assert tg.session.last_text(OTHER_USER_ID).startswith("Выберите действие")


async def test_notify_admins_survives_errors(tg: TgHarness, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_CHAT_IDS", f"{ADMIN_ID},777")
    from app.config import get_settings

    get_settings.cache_clear()
    tg.session.block_chat(777)
    sent = await notify_admins(tg.bot, "Новая запись")
    assert [m.chat.id for m in sent] == [ADMIN_ID]
    assert [p["chat_id"] for p in tg.session.sent_messages()] == [ADMIN_ID, 777]


async def test_click_button_by_label(tg: TgHarness) -> None:
    await tg.send("/start")
    await tg.click_button("Цены и формат")
    assert tg.session.last_text(USER_ID).startswith("Тарифы курса")
    await tg.click_button("Записаться на практикум")
    assert tg.session.last_text(USER_ID).startswith("Как к вам обращаться?")
