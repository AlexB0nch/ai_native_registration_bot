"""Команды владельца и ответы на вопросы — TASK-BOT-002.

Фильтр `IsAdmin` стоит на уровне роутера: сообщения и нажатия не-админов сюда не попадают
и идут дальше (start → practicum → questions).

- `/stats` — статистика записей (`app/services/stats.py`);
- `/export` — CSV со всеми записями (`app/services/export.py`);
- `/link <url>`, `/set_link practicum|recording <url>` — ссылки практикума в `events`;
- «Ответить» под вопросом → состояние `answering:<id>` (своё у каждого админа) → следующий
  текст владельца уходит автору вопроса. Ответ реплаем на сообщение с вопросом — так же.
  Команды (`/stats` и т.п.) в состоянии ответа не перехватываются.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import AnsweringQuestion, IsAdmin, ReplyToQuestion, answering_state, parse_answering_state
from app.bot.keyboards import Q_ANSWER_PREFIX, Q_CANCEL, answer_cancel_kb, parse_question_answer
from app.db import session_scope
from app.models import Event, utcnow
from app.services.events import get_event, sync_events_from_facts
from app.services.export import build_registrations_csv, export_filename
from app.services.questions import (
    DeliveryResult,
    deliver_answer,
    get_question_with_person,
    person_display_name,
)
from app.services.stats import collect_stats, render_stats
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

NOT_COMMAND = ~F.text.startswith("/")
LINK_FIELDS = {"practicum": "join_url", "recording": "recording_url"}
MAX_URL_LENGTH = 1000  # events.join_url / recording_url — String(1000)


# --- /stats, /export --------------------------------------------------------------------------


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    async with session_scope() as session:
        stats = await collect_stats(session, utcnow())
    await message.answer(render_stats(stats))


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    async with session_scope() as session:
        data, count = await build_registrations_csv(session)
    document = BufferedInputFile(data, filename=export_filename(utcnow()))
    await message.answer_document(document, caption=t("admin.export_caption", count=count))
    log.info("export: %s rows to admin %s", count, message.chat.id)


# --- /link, /set_link -------------------------------------------------------------------------


def is_https_url(value: str) -> bool:
    if not value or len(value) > MAX_URL_LENGTH or any(ch.isspace() for ch in value):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.netloc)


async def _practicum_event(session: AsyncSession) -> Event:
    event = await get_event(session, "practicum")
    if event is None:  # события создаются при старте; на всякий случай — синхронизация из фактов
        event = (await sync_events_from_facts(session))["practicum"]
    return event


async def _show_links(message: Message) -> None:
    async with session_scope() as session:
        event = await _practicum_event(session)
        join_url, recording_url = event.join_url, event.recording_url
    missing = t("admin.link_missing")
    await message.answer(t("admin.links", join_url=join_url or missing, recording_url=recording_url or missing))


async def _save_link(message: Message, field: str, url: str) -> None:
    if not is_https_url(url):
        await message.answer(t("admin.link_bad"))
        return
    async with session_scope() as session:
        event = await _practicum_event(session)
        setattr(event, field, url)
    log.info("practicum %s updated by admin %s", field, message.chat.id)
    await message.answer(t("admin.link_saved", url=url))


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args:
        await _show_links(message)
    elif len(args) == 1:
        await _save_link(message, "join_url", args[0])
    else:
        await message.answer(t("admin.link_bad"))


@router.message(Command("set_link"))
async def cmd_set_link(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args:
        await _show_links(message)
        return
    field = LINK_FIELDS.get(args[0].lower())
    if field is None or len(args) != 2:
        await message.answer(t("admin.set_link_usage"))
        return
    await _save_link(message, field, args[1])


# --- ответы на вопросы ------------------------------------------------------------------------


@router.callback_query(F.data.startswith(Q_ANSWER_PREFIX))
async def on_answer_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    question_id = parse_question_answer(callback.data)
    found = None
    if question_id is not None:
        async with session_scope() as session:
            found = await get_question_with_person(session, question_id)
    if found is None:
        await callback.message.answer(t("questions.not_found"))
        return
    question, person = found
    await state.set_state(answering_state(question.id))
    key = "questions.admin_prompt_again" if question.answer_text else "questions.admin_prompt"
    await callback.message.answer(t(key, name=person_display_name(person)), reply_markup=answer_cancel_kb())


@router.callback_query(F.data == Q_CANCEL)
async def on_answer_cancel(callback: CallbackQuery, state: FSMContext, raw_state: str | None = None) -> None:
    await callback.answer()
    if parse_answering_state(raw_state) is not None:
        await state.set_state(None)
    if isinstance(callback.message, Message):
        await callback.message.answer(t("questions.admin_cancelled"))


async def _deliver(message: Message, question_id: int, text: str) -> None:
    if message.bot is None:
        return
    delivery = await deliver_answer(message.bot, question_id, text)
    key = {
        DeliveryResult.SENT: "questions.sent",
        DeliveryResult.BLOCKED: "questions.blocked",
        DeliveryResult.FAILED: "questions.failed",
        DeliveryResult.NO_CHAT: "questions.no_chat",
        DeliveryResult.NOT_FOUND: "questions.not_found",
    }[delivery.result]
    await message.answer(t(key))


@router.message(ReplyToQuestion(), F.text, NOT_COMMAND)
async def on_reply_answer(message: Message, state: FSMContext, question_id: int, raw_state: str | None = None) -> None:
    """Ответ реплаем на сообщение с вопросом. Если владелец как раз отвечал на него — состояние снимается."""
    if parse_answering_state(raw_state) == question_id:
        await state.set_state(None)
    await _deliver(message, question_id, message.text or "")


@router.message(AnsweringQuestion(), F.text, NOT_COMMAND)
async def on_answer_text(message: Message, state: FSMContext, question_id: int) -> None:
    await state.set_state(None)
    await _deliver(message, question_id, message.text or "")


@router.message(AnsweringQuestion(), ~F.text)
async def on_answer_not_text(message: Message) -> None:
    await message.answer(t("questions.admin_text_only"), reply_markup=answer_cancel_kb())
