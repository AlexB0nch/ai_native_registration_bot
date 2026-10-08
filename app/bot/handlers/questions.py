"""Свободный текст и вопросы — TASK-BOT-002 (передача владельцу) и TASK-LLM-001 (ответы).

Роутер подключается последним и ловит всё, что не обработали admin → start → practicum.

Заглушка CORE: любой текст вне сценария → `fallback_text` + меню; нажатие неизвестной
кнопки → снять «часики» и показать то же.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="questions")


async def answer_question_inline(message: Message, text: str) -> None:
    """Ответить на вопрос пользователя прямо в текущем чате.

    Точка входа для TASK-BOT-002 и TASK-LLM-001; её же вызывает сценарий практикума,
    когда на шаге пришёл вопрос вместо ответа (после неё сценарий повторяет свой шаг).
    TASK-LLM-001: быстрый ответ или модель (`app/llm/service.py`) + кнопка по `cta`;
    без точного ответа (`handoff`) — вопрос владельцу через `escalate`.
    """
    from app.db import session_scope
    from app.llm.service import answer_question, cta_keyboard
    from app.services.people import get_or_create_by_telegram

    answer = await answer_question(text)
    await message.answer(answer.text, reply_markup=cta_keyboard(answer.cta))
    if answer.handoff and message.from_user is not None:
        try:
            async with session_scope() as session:
                person = await get_or_create_by_telegram(session, message.from_user, None)
            await escalate(message, person, text, bot_answer=answer.text if answer.answered else None)
        except Exception:
            log.exception("escalate: не удалось передать вопрос владельцу")


async def escalate(message: Message, person: Any, text: str, bot_answer: str | None = None) -> None:
    """Создать вопрос и переслать владельцу с кнопкой «Ответить» — TASK-BOT-002.

    Заглушка: только запись в лог (без ПД).
    """
    log.info("escalate (stub): person=%s", getattr(person, "id", None))


async def handle_free_text(message: Message, state: FSMContext) -> None:
    """Текст вне сценария."""
    await answer_question_inline(message, message.text or "")


@router.message(F.text)
async def on_free_text(message: Message, state: FSMContext) -> None:
    await handle_free_text(message, state)


@router.message()
async def on_other_message(message: Message) -> None:
    await message.answer(t("fallback_text"), reply_markup=main_menu())


@router.callback_query()
async def on_unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(t("fallback_text"), reply_markup=main_menu())
