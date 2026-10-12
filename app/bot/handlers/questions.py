"""Свободный текст и вопросы — TASK-BOT-002 (передача владельцу) и TASK-LLM-001 (ответы).

Роутер подключается последним и ловит всё, что не обработали admin → start → practicum.

- «Задать вопрос» (`keyboards.MENU_QUESTION`) и `/question` → «Напишите вопрос одним сообщением.»
  и состояние `QuestionFlow.waiting` (перебивает состояние другого сценария, чтобы следующий
  текст пришёл сюда) → следующий текст → `answer_question_inline`.
- Текст вне сценария → `handle_free_text` → `answer_question_inline`.
- Неизвестная команда (`/что-то`, в том числе команды владельца от не-админа), сообщение
  без текста, неизвестная кнопка → `fallback_text` + меню.

Точки входа для других задач:
- `answer_question_inline(message, text)` — LLM-001 заменяет только её тело;
- `escalate(message, person, text, bot_answer=None)` — создать вопрос и переслать владельцу.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import MENU_QUESTION, main_menu
from app.db import session_scope
from app.models import Person, Question
from app.services.people import get_or_create_by_telegram
from app.services.questions import create_question, forward_to_admins
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="questions")


class QuestionFlow(StatesGroup):
    waiting = State()  # нажал «Задать вопрос», ждём текст


async def answer_question_inline(message: Message, text: str) -> None:
    """Ответить на вопрос пользователя прямо в текущем чате.

    Точка входа для TASK-BOT-002 и TASK-LLM-001; её же вызывает сценарий практикума,
    когда на шаге пришёл вопрос вместо ответа (после неё сценарий повторяет свой шаг),
    поэтому состояние FSM здесь не трогается.
    TASK-LLM-001: быстрый ответ или модель (`app/llm/service.py`) + кнопка по `cta`;
    без точного ответа (`handoff`) — вопрос владельцу через `escalate`.
    """
    from app.db import session_scope
    from app.llm.service import answer_question, cta_keyboard
    from app.services.people import get_or_create_by_telegram

    if message.from_user is None or message.from_user.is_bot:
        # Сообщение от имени бота (например, `callback.message`) — вопроса нет, некого и не о чем спрашивать.
        await message.answer(t("fallback_text"), reply_markup=main_menu())
        return

    answer = await answer_question(text)
    await message.answer(answer.text, reply_markup=cta_keyboard(answer.cta))
    if answer.handoff and message.from_user is not None:
        try:
            async with session_scope() as session:
                person = await get_or_create_by_telegram(session, message.from_user, None)
            await escalate(message, person, text, bot_answer=answer.text if answer.answered else None)
        except Exception:
            log.exception("escalate: не удалось передать вопрос владельцу")


async def escalate(
    message: Message,
    person: Person | None,
    text: str,
    bot_answer: str | None = None,
) -> Question | None:
    """Создать вопрос (`questions`) и переслать владельцу с кнопкой «Ответить».

    `person` — автор вопроса; `None` → найти или создать по `message.from_user`.
    `bot_answer` — что бот уже ответил сам (LLM-001), владелец увидит это под вопросом.
    Пользователю ничего не отправляет — это делает вызывающий код.
    Возвращает вопрос или `None`, если автора не определить (нет `from_user` или это бот).
    """
    user = message.from_user
    if person is None and (user is None or user.is_bot):
        return None
    async with session_scope() as session:
        if person is None:
            person = await get_or_create_by_telegram(session, user, None)
        question = await create_question(session, person, text, bot_answer)
    sent = await forward_to_admins(message.bot, question, person) if message.bot else []
    log.info("question %s from person %s forwarded to %s admin chat(s)", question.id, person.id, len(sent))
    return question


async def handle_free_text(message: Message, state: FSMContext) -> None:
    """Текст вне сценария (и ответ на «Напишите вопрос…»)."""
    if await state.get_state() == QuestionFlow.waiting.state:
        await state.set_state(None)
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.answer(t("fallback_text"), reply_markup=main_menu())
        return
    await answer_question_inline(message, text)


async def ask_question(message: Message, state: FSMContext) -> None:
    await state.set_state(QuestionFlow.waiting)
    await message.answer(t("questions.ask"))


@router.message(Command("question"))
async def cmd_question(message: Message, state: FSMContext) -> None:
    await ask_question(message, state)


@router.callback_query(F.data == MENU_QUESTION)
async def on_menu_question(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await ask_question(callback.message, state)


@router.message(F.text)
async def on_free_text(message: Message, state: FSMContext) -> None:
    await handle_free_text(message, state)


@router.message(QuestionFlow.waiting)
async def on_question_not_text(message: Message) -> None:
    await message.answer(t("questions.text_only"))


@router.message()
async def on_other_message(message: Message) -> None:
    await message.answer(t("fallback_text"), reply_markup=main_menu())


@router.callback_query()
async def on_unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(t("fallback_text"), reply_markup=main_menu())
