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
    Заглушка: `fallback_text` + меню.
    """
    await message.answer(t("fallback_text"), reply_markup=main_menu())


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
