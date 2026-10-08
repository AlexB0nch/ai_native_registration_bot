"""Фильтры aiogram."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import parse_question_answer
from app.config import get_settings
from app.db import session_scope
from app.services.questions import find_question_by_admin_message


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().admin_chat_ids


class IsAdmin(BaseFilter):
    """Пропускает только пользователей из `ADMIN_CHAT_IDS`.

    Сравнивается id отправителя (в личном чате он равен chat_id из `/whoami`),
    поэтому участник группы, добавленной в ADMIN_CHAT_IDS, админом не становится.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return is_admin(user.id if user else None)


# --- ответ владельца на вопрос (TASK-BOT-002) -------------------------------------------------

# Состояние FSM владельца, пока он пишет ответ: `answering:<question_id>`. Ключ FSM — чат
# и пользователь владельца, поэтому у каждого админа своё состояние.
ANSWERING_PREFIX = "answering:"


def answering_state(question_id: int) -> str:
    return f"{ANSWERING_PREFIX}{question_id}"


def parse_answering_state(raw_state: str | None) -> int | None:
    if not raw_state or not raw_state.startswith(ANSWERING_PREFIX):
        return None
    tail = raw_state.removeprefix(ANSWERING_PREFIX)
    return int(tail) if tail.isdigit() else None


class AnsweringQuestion(BaseFilter):
    """Владелец в состоянии `answering:<id>` → в хендлер приходит `question_id`."""

    async def __call__(self, message: Message, raw_state: str | None = None) -> bool | dict[str, Any]:
        question_id = parse_answering_state(raw_state)
        return {"question_id": question_id} if question_id is not None else False


class ReplyToQuestion(BaseFilter):
    """Сообщение — реплай на пересланный владельцу вопрос → в хендлер приходит `question_id`.

    Сначала вопрос ищется по `(admin_chat_id, admin_message_id)` — так сохраняется первое
    из пересланных сообщений. Если админов несколько и ответили на копию в другом чате,
    id берётся из кнопки «Ответить» (`q:answer:<id>`) в сообщении бота, на которое ответили.
    """

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        reply = message.reply_to_message
        if reply is None:
            return False
        async with session_scope() as session:
            question = await find_question_by_admin_message(session, message.chat.id, reply.message_id)
        if question is not None:
            return {"question_id": question.id}
        bot_id = message.bot.id if message.bot else None
        if reply.from_user is None or reply.from_user.id != bot_id or reply.reply_markup is None:
            return False
        for row in reply.reply_markup.inline_keyboard:
            for button in row:
                question_id = parse_question_answer(button.callback_data)
                if question_id is not None:
                    return {"question_id": question_id}
        return False
