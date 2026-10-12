"""Вопросы владельцу: запись в `questions`, пересылка в `ADMIN_CHAT_IDS`, доставка ответа.

Несколько чатов владельца. `notify_admins` шлёт вопрос в каждый чат из `ADMIN_CHAT_IDS`,
а в строке `questions` есть место только для одного сообщения: туда пишется **первое**
доставленное (`admin_chat_id`, `admin_message_id`). Ответ реплаем находится по этой паре,
а для остальных чатов — по кнопке «Ответить» (`q:answer:<id>`) в сообщении, на которое
ответили (Telegram присылает её в `reply_to_message.reply_markup`), см. `app/bot/filters.py`.
Так обходимся без миграции.

В лог — только внутренние id (без имени, username и текста вопроса).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import owner_answer_kb, question_answer_kb
from app.db import session_scope
from app.models import Person, Question, utcnow
from app.services.notify import notify_admins
from app.texts import t

log = logging.getLogger(__name__)

# Лимит сообщения Telegram — 4096 символов; оставляем место под шапку и ответ бота.
MAX_QUESTION_CHARS = 3300
MAX_BOT_ANSWER_CHARS = 600


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def person_display_name(person: Person) -> str:
    """Имя из анкеты, иначе имя в Telegram, иначе «без имени»."""
    return person.name or person.tg_first_name or t("questions.no_name")


def admin_question_text(question: Question, person: Person) -> str:
    """«Вопрос от Ирина (@irina_ops, lp_opex):\\n{text}» (+ ответ бота, если был)."""
    parts = []
    if person.telegram_username:
        parts.append(f"@{person.telegram_username}")
    parts.append(person.source or t("questions.no_source"))
    text = t(
        "questions.to_admin",
        name=person_display_name(person),
        details=f" ({', '.join(parts)})",
        text=_clip(question.text, MAX_QUESTION_CHARS),
    )
    if question.bot_answer:
        text += t("questions.to_admin_bot_answer", bot_answer=_clip(question.bot_answer, MAX_BOT_ANSWER_CHARS))
    return text


async def create_question(
    session: AsyncSession,
    person: Person,
    text: str,
    bot_answer: str | None = None,
) -> Question:
    """Записать вопрос (flush, без commit — коммит на вызывающей стороне)."""
    question = Question(person_id=person.id, text=text, bot_answer=bot_answer)
    session.add(question)
    await session.flush()
    return question


async def forward_to_admins(bot: Bot, question: Question, person: Person) -> list[Message]:
    """Отправить вопрос владельцу с кнопкой «Ответить» и сохранить первое отправленное сообщение.

    `question` уже должен быть в базе (есть `id`). Возвращает все отправленные сообщения.
    """
    sent = await notify_admins(bot, admin_question_text(question, person), reply_markup=question_answer_kb(question.id))
    if sent:
        first = sent[0]
        question.admin_chat_id = first.chat.id
        question.admin_message_id = first.message_id
        async with session_scope() as session:
            await session.execute(
                update(Question)
                .where(Question.id == question.id)
                .values(admin_chat_id=first.chat.id, admin_message_id=first.message_id)
            )
    else:
        log.warning("question %s: не доставлен ни в один чат владельца", question.id)
    return sent


async def get_question_with_person(session: AsyncSession, question_id: int) -> tuple[Question, Person] | None:
    row = (
        await session.execute(
            select(Question, Person).join(Person, Person.id == Question.person_id).where(Question.id == question_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def find_question_by_admin_message(session: AsyncSession, chat_id: int, message_id: int) -> Question | None:
    return await session.scalar(
        select(Question)
        .where(Question.admin_chat_id == chat_id, Question.admin_message_id == message_id)
        .order_by(Question.id.desc())
        .limit(1)
    )


async def count_unanswered(session: AsyncSession) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(Question).where(Question.answered_at.is_(None))) or 0
    )


class DeliveryResult(StrEnum):
    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"
    NO_CHAT = "no_chat"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class AnswerDelivery:
    result: DeliveryResult
    additional: bool = False  # ответ ушёл как дополнение к прежнему


async def deliver_answer(bot: Bot, question_id: int, text: str) -> AnswerDelivery:
    """Отправить ответ владельца автору вопроса и сохранить `answer_text`, `answered_at`.

    Повторный ответ на уже отвеченный вопрос уходит как дополнение: текст дописывается
    к `answer_text`, `answered_at` остаётся временем первого ответа. Если сообщение
    не доставлено, в базе ничего не меняется.
    """
    async with session_scope() as session:
        found = await get_question_with_person(session, question_id)
    if found is None:
        return AnswerDelivery(DeliveryResult.NOT_FOUND)
    question, person = found
    additional = question.answer_text is not None
    if person.telegram_id is None:
        return AnswerDelivery(DeliveryResult.NO_CHAT, additional)

    key = "questions.user_answer_more" if additional else "questions.user_answer"
    try:
        await bot.send_message(person.telegram_id, t(key, text=text), reply_markup=owner_answer_kb())
    except TelegramForbiddenError:
        log.info("question %s: ответ не доставлен, person %s заблокировал бота", question_id, person.id)
        return AnswerDelivery(DeliveryResult.BLOCKED, additional)
    except TelegramAPIError as exc:
        log.warning("question %s: ответ не доставлен: %s", question_id, type(exc).__name__)
        return AnswerDelivery(DeliveryResult.FAILED, additional)

    async with session_scope() as session:
        stored = await session.get(Question, question_id)
        if stored is not None:
            stored.answer_text = f"{stored.answer_text}\n\n{text}" if stored.answer_text else text
            if stored.answered_at is None:
                stored.answered_at = utcnow()
    log.info("question %s: ответ доставлен person %s (дополнение: %s)", question_id, person.id, additional)
    return AnswerDelivery(DeliveryResult.SENT, additional)
