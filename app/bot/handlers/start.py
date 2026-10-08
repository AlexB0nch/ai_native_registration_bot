"""`/start` с метками, главное меню, «Что будет на курсе», «Цены и формат», `/whoami`."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.practicum import START_SOURCE_KEY, start_practicum
from app.bot.keyboards import (
    MENU_COURSE,
    MENU_MAIN,
    MENU_PRICES,
    course_about_kb,
    main_menu,
    practicum_done_kb,
    prices_kb,
)
from app.bot.payload import StartPayload, parse_start_payload
from app.db import session_scope
from app.services.people import get_or_create_by_telegram
from app.services.site_import import bind_site_registration
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    """`/start [метка]`: человек в базе, приветствие с меню, затем сценарий по метке.

    `/start` прерывает любой сценарий (состояние FSM очищается).
    """
    payload = parse_start_payload(command.args)
    await state.clear()
    if message.from_user is None:
        return
    async with session_scope() as session:
        person = await get_or_create_by_telegram(session, message.from_user, payload.raw)
        person_id = person.id
    log.info("start: person=%s scenario=%s", person_id, payload.scenario)
    await state.update_data({START_SOURCE_KEY: payload.raw})
    await message.answer(t("welcome"), reply_markup=main_menu())
    await route_scenario(message, state, payload)


async def route_scenario(message: Message, state: FSMContext, payload: StartPayload) -> None:
    if payload.scenario == "practicum":
        await start_practicum(message, state)
    elif payload.scenario == "site_practicum":
        await handle_site_practicum(message, state, payload)
    elif payload.scenario == "site_course":
        await handle_site_course(message, state, payload)
    elif payload.scenario == "course":
        await message.answer(t("course_soon"))
    # waitlist (TASK-BOT-005) и menu — только приветствие с меню


async def _bind_site(message: Message, event_code: str) -> bool:
    """Найти заявку с сайта по username отправителя и привязать к этому чату."""
    if message.from_user is None:
        return False
    async with session_scope() as session:
        return await bind_site_registration(session, message.from_user, event_code)


async def handle_site_practicum(message: Message, state: FSMContext, payload: StartPayload) -> None:
    """`/start prk_web` — привязка заявки с сайта (TASK-API-001); не нашлась — сценарий практикума."""
    if await _bind_site(message, "practicum"):
        await message.answer(t("site.found_practicum"), reply_markup=practicum_done_kb())
        return
    await start_practicum(message, state)


async def handle_site_course(message: Message, state: FSMContext, payload: StartPayload) -> None:
    """`/start crs_web` — привязка заявки с сайта (TASK-API-001); не нашлась — «запись на курс откроется скоро»."""
    if await _bind_site(message, "course"):
        await message.answer(t("site.found_course"))
        return
    await message.answer(t("course_soon"))


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(t("whoami", chat_id=message.chat.id))


@router.message(Command("course"))
async def cmd_course(message: Message) -> None:
    await message.answer(t("course_about"), reply_markup=course_about_kb())


@router.callback_query(F.data == MENU_COURSE)
async def on_course(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(t("course_about"), reply_markup=course_about_kb())


@router.callback_query(F.data == MENU_PRICES)
async def on_prices(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(t("prices"), reply_markup=prices_kb())


@router.callback_query(F.data == MENU_MAIN)
async def on_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """«В меню» выходит из любого сценария."""
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(t("menu.title"), reply_markup=main_menu())
