"""Рассылка участникам практикума — TASK-BOT-007.

`/broadcast_practicum <текст>` (только `ADMIN_CHAT_IDS`) → предпросмотр с числом получателей →
«Отправить N» → отправка в фоне → отчёт владельцу. Логика отправки — `app/services/broadcast.py`.

- роутер подключается раньше `admin.router`, поэтому команда работает и в режиме «Ответить на вопрос»
  (`answering:<id>`): состояние не трогается;
- текст ожидающей рассылки лежит в FSM-данных владельца (без смены состояния), в кнопке — только хеш;
- рассылка идёт фоновой задачей (ссылка на неё хранится в `_tasks`), одновременно — одна (`_lock`);
- владелец получает отчёт всегда, в том числе если задача упала или её отменили;
- текст рассылки, имена и telegram_id в лог не пишутся.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from app.bot.filters import IsAdmin
from app.bot.keyboards import BC_CANCEL, BC_SEND_PREFIX, broadcast_confirm_kb, parse_broadcast_send
from app.db import session_scope
from app.services.broadcast import (
    DEFAULT_EVENT_CODE,
    DEFAULT_PAUSE,
    BroadcastResult,
    broadcast_kind,
    plan_broadcast,
    send_broadcast,
    text_hash,
)
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_TEXT_CHARS = 4000  # лимит сообщения Telegram — 4096
PENDING_KEY = "broadcast_pending"  # FSM-данные владельца: {"hash": ..., "text": ...}
SEND_PAUSE = DEFAULT_PAUSE  # пауза между сообщениями; тесты ставят 0
REPORT_ATTEMPTS = 3
REPORT_RETRY_DELAY = 2.0  # пауза между попытками доставить отчёт владельцу

_lock = asyncio.Lock()
_tasks: set[asyncio.Task[None]] = set()  # сильные ссылки: иначе сборщик мусора может убить фоновую задачу


async def wait_idle() -> None:
    """Дождаться окончания всех фоновых рассылок (для тестов и корректного завершения)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
        await asyncio.sleep(0)


def _no_preview() -> LinkPreviewOptions:
    return LinkPreviewOptions(is_disabled=True)


# --- команда и предпросмотр -------------------------------------------------------------------


@router.message(Command("broadcast_practicum"))
async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext) -> None:
    if command.args is None:
        await message.answer(t("broadcast.usage"))
        return
    text = command.args.strip()
    if not text:
        await message.answer(t("broadcast.empty"))
        return
    if len(text) > MAX_TEXT_CHARS:
        await message.answer(t("broadcast.too_long", length=len(text), limit=MAX_TEXT_CHARS))
        return

    async with session_scope() as session:
        plan = await plan_broadcast(session, text, DEFAULT_EVENT_CODE)
    digest = text_hash(text)

    data = await state.get_data()
    if plan.to_send > 0:
        data[PENDING_KEY] = {"hash": digest, "text": text}
    else:
        data.pop(PENDING_KEY, None)  # новая команда заменяет прежнюю ожидающую рассылку
    await state.set_data(data)

    summary = t(
        "broadcast.preview",
        recipients=plan.to_send,
        already=plan.already,
        without_telegram=plan.without_telegram,
    )
    if plan.without_telegram:
        summary += "\n" + t("broadcast.preview_without_telegram")
    markup = None
    if plan.to_send > 0:
        markup = broadcast_confirm_kb(plan.to_send, digest)
    else:
        summary += "\n" + t("broadcast.preview_nobody")

    await message.answer(text, link_preview_options=_no_preview())  # ровно то, что уйдёт
    await message.answer(summary, reply_markup=markup)
    log.info("broadcast: preview hash=%s recipients=%s already=%s", digest, plan.to_send, plan.already)


# --- кнопки -----------------------------------------------------------------------------------


async def _remove_keyboard(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramAPIError):  # сообщение могло устареть или уже быть без кнопок
            await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == BC_CANCEL)
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if data.pop(PENDING_KEY, None) is not None:
        await state.set_data(data)
    await _remove_keyboard(callback)
    if isinstance(callback.message, Message):
        await callback.message.answer(t("broadcast.cancelled"))


@router.callback_query(F.data.startswith(BC_SEND_PREFIX))
async def on_send(callback: CallbackQuery, state: FSMContext) -> None:
    message = callback.message
    if not isinstance(message, Message) or callback.bot is None:
        await callback.answer()
        return
    # Проверка и захват блокировки идут без `await` между ними: две одновременные кнопки не пройдут обе.
    if _lock.locked():
        await callback.answer()
        await message.answer(t("broadcast.busy"))
        return
    await _lock.acquire()
    started = False
    try:
        await callback.answer()
        digest = parse_broadcast_send(callback.data)
        data = await state.get_data()
        pending = data.get(PENDING_KEY)
        text = pending.get("text") if isinstance(pending, dict) else None
        if digest is None or not isinstance(text, str) or pending.get("hash") != digest or text_hash(text) != digest:
            await _remove_keyboard(callback)
            await message.answer(t("broadcast.stale"))
            return
        data.pop(PENDING_KEY)
        await state.set_data(data)  # повторное нажатие уже ничего не найдёт
        await _remove_keyboard(callback)
        await message.answer(t("broadcast.started"))
        task = asyncio.create_task(_run_broadcast(callback.bot, message.chat.id, text), name="broadcast")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        started = True
    finally:
        if not started:
            _lock.release()


# --- фоновая отправка и отчёт -----------------------------------------------------------------


async def _deliver_report(bot: Bot, chat_id: int, text: str) -> None:
    for attempt in range(1, REPORT_ATTEMPTS + 1):
        try:
            await bot.send_message(chat_id, text)
            return
        except Exception as exc:
            log.warning("broadcast: отчёт не доставлен (попытка %s): %s", attempt, type(exc).__name__)
            if attempt < REPORT_ATTEMPTS and REPORT_RETRY_DELAY > 0:
                await asyncio.sleep(REPORT_RETRY_DELAY)


def _report_text(result: BroadcastResult, *, aborted: bool) -> str:
    counts = {"sent": result.sent, "blocked": result.blocked, "failed": result.failed, "skipped": result.skipped}
    if aborted:
        return t("broadcast.aborted", **counts) + "\n" + t("broadcast.aborted_retry")
    text = t("broadcast.report", **counts)
    if result.failed:
        text += "\n" + t("broadcast.report_retry")
    return text


async def _run_broadcast(bot: Bot, chat_id: int, text: str) -> None:
    """Отправить рассылку и прислать отчёт. Блокировка уже захвачена вызывающим и снимается здесь."""
    result = BroadcastResult()
    failure: BaseException | None = None
    try:
        try:
            await send_broadcast(
                bot, text, event_code=DEFAULT_EVENT_CODE, kind=broadcast_kind(text), pause=SEND_PAUSE, result=result
            )
        except BaseException as exc:  # в том числе отмена задачи при остановке сервиса
            failure = exc
            log.error(
                "broadcast: прервана hash=%s sent=%s blocked=%s failed=%s: %s",
                text_hash(text),
                result.sent,
                result.blocked,
                result.failed,
                type(exc).__name__,
            )
        await _deliver_report(bot, chat_id, _report_text(result, aborted=failure is not None))
    finally:
        _lock.release()
    if failure is not None and not isinstance(failure, Exception):
        raise failure
