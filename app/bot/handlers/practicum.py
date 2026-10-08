"""Сценарий записи на практикум — TASK-BOT-001.

Вход: кнопка меню (`keyboards.MENU_PRACTICUM`), команда `/practicum`, `/start` с меткой
практикума (`start.py` вызывает `start_practicum`).

Шаги: имя → почта → телефон (можно пропустить) → согласие → запись и уведомление владельцу,
затем необязательный вопрос о роли. Введённое хранится в FSM data до конца сценария;
в `people` и `registrations` всё пишется одной транзакцией после «Согласен».
В логи — только внутренние id.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InaccessibleMessage,
    Message,
    ReplyKeyboardRemove,
    User,
)

from app.bot import keyboards as kb
from app.bot.handlers.questions import answer_question_inline
from app.bot.validators import (
    looks_like_question,
    normalize_contact_phone,
    normalize_email,
    normalize_name,
    normalize_phone,
)
from app.db import session_scope
from app.models import utcnow
from app.services.ics import build_ics
from app.services.notify import notify_admins
from app.services.people import get_by_telegram_id, get_or_create_by_telegram
from app.services.registrations import (
    count_registrations,
    get_active_registration,
    get_or_sync_event,
    register_for_event,
)
from app.texts import t

log = logging.getLogger(__name__)

router = Router(name="practicum")

# Ключ в FSM data, куда start.py кладёт метку `/start` (или None) перед вызовом start_practicum.
START_SOURCE_KEY = "start_source"

EVENT_CODE = "practicum"
ICS_FILENAME = "practicum.ics"
NO_VALUE = "—"

# Ключи FSM data сценария (значения — только строки/None/bool: хранилище пишет JSON).
NAME_KEY = "prk_name"
EMAIL_KEY = "prk_email"
PHONE_KEY = "prk_phone"
TG_NAME_KEY = "prk_tg_first_name"
EDIT_KEY = "prk_edit"


class PracticumForm(StatesGroup):
    name = State()
    email = State()
    phone = State()
    consent = State()


PREVIOUS_STEP: dict[str, State] = {
    PracticumForm.email.state: PracticumForm.name,
    PracticumForm.phone.state: PracticumForm.email,
    PracticumForm.consent.state: PracticumForm.phone,
}

NOT_COMMAND = ~F.text.startswith("/")

AnswerTarget = Message | InaccessibleMessage


# --- вход -------------------------------------------------------------------------------------


async def start_practicum(message: Message, state: FSMContext) -> None:
    """Начать сценарий записи на практикум.

    Вызывается из `start.py` для `/start` со сценарием `practicum`/`site_practicum` —
    уже после приветствия с меню. Метка `/start` лежит в `await state.get_data()`
    под ключом `START_SOURCE_KEY`.

    Если вызывать с `callback.message`, то `message.from_user` — это бот: id пользователя
    всегда берётся из `state.key.user_id`.
    """
    user = message.from_user
    tg_user = user if user is not None and user.id == state.key.user_id else None
    await _begin(message, state, tg_user)


async def _begin(target: AnswerTarget, state: FSMContext, tg_user: User | None, *, edit: bool = False) -> None:
    """Показать «вы уже записаны» или начать шаги (при `edit=True` — всегда шаги)."""
    data = await state.get_data()
    async with session_scope() as session:
        person = await get_by_telegram_id(session, state.key.user_id)
        if person is None and tg_user is not None:
            person = await get_or_create_by_telegram(session, tg_user, data.get(START_SOURCE_KEY))
        registered = person is not None and await get_active_registration(session, person, EVENT_CODE) is not None
        prefill: dict[str, Any] = {
            NAME_KEY: person.name if person else None,
            EMAIL_KEY: person.email if person else None,
            PHONE_KEY: person.phone if person else None,
            TG_NAME_KEY: (tg_user.first_name if tg_user else None) or (person.tg_first_name if person else None),
        }
        person_id = person.id if person else None

    if registered and not edit:
        log.info("practicum: already registered person=%s", person_id)
        if await _current_step(state) is not None:
            await state.set_state(None)
        await target.answer(t("practicum.already"), reply_markup=kb.practicum_already_kb())
        return

    log.info("practicum: flow started person=%s edit=%s", person_id, edit)
    await state.update_data({**prefill, EDIT_KEY: edit})
    await _ask(target, state, PracticumForm.name)


@router.message(Command("practicum"))
async def cmd_practicum(message: Message, state: FSMContext) -> None:
    await _begin(message, state, message.from_user)


@router.callback_query(F.data == kb.MENU_PRACTICUM)
async def on_menu_practicum(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is not None:
        await _begin(callback.message, state, callback.from_user)


@router.callback_query(F.data == kb.PRK_EDIT)
async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is not None:
        await _begin(callback.message, state, callback.from_user, edit=True)


# --- шаги -------------------------------------------------------------------------------------


async def _ask(target: AnswerTarget, state: FSMContext, step: State) -> None:
    """Перейти на шаг и задать его вопрос."""
    await state.set_state(step)
    data = await state.get_data()
    if step == PracticumForm.name:
        known = data.get(NAME_KEY) or data.get(TG_NAME_KEY)
        text = t("practicum.ask_name_known", name=known) if known else t("practicum.ask_name")
        await target.answer(text, reply_markup=kb.practicum_name_kb(known))
    elif step == PracticumForm.email:
        await target.answer(t("practicum.ask_email"), reply_markup=kb.practicum_email_kb(data.get(EMAIL_KEY)))
    elif step == PracticumForm.phone:
        await target.answer(t("practicum.ask_phone"), reply_markup=kb.practicum_phone_reply_kb(data.get(PHONE_KEY)))
        await target.answer(t("practicum.phone_nav"), reply_markup=kb.practicum_nav_kb())
    else:
        await target.answer(t("practicum.ask_consent"), reply_markup=kb.practicum_consent_kb())


async def _current_step(state: FSMContext) -> State | None:
    current = await state.get_state()
    for step in (PracticumForm.name, PracticumForm.email, PracticumForm.phone, PracticumForm.consent):
        if step.state == current:
            return step
    return None


async def _reject(message: Message, state: FSMContext, step: State, error_key: str) -> None:
    """Ввод не прошёл проверку: вопрос → ответ и повтор шага; иначе подсказка на том же шаге."""
    text = message.text or ""
    if looks_like_question(text):
        await answer_question_inline(message, text)
        await _ask(message, state, step)
        return
    data = await state.get_data()
    if step == PracticumForm.name:
        markup = kb.practicum_name_kb(data.get(NAME_KEY) or data.get(TG_NAME_KEY))
    elif step == PracticumForm.email:
        markup = kb.practicum_email_kb(data.get(EMAIL_KEY))
    else:  # телефон: reply-клавиатура шага остаётся на экране
        markup = kb.practicum_nav_kb()
    await message.answer(t(error_key), reply_markup=markup)


async def _cancel(target: AnswerTarget, state: FSMContext) -> None:
    """Выйти из сценария в меню; запись не создаётся, введённое забывается."""
    step = await _current_step(state)
    edit = bool((await state.get_data()).get(EDIT_KEY))
    await state.clear()
    text = t("practicum.edit_cancelled") if edit else t("practicum.cancelled")
    if step == PracticumForm.phone:
        await target.answer(text, reply_markup=ReplyKeyboardRemove())
        await target.answer(t("menu.title"), reply_markup=kb.main_menu())
    else:
        await target.answer(text, reply_markup=kb.main_menu())


@router.message(StateFilter(PracticumForm), Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await _cancel(message, state)


@router.callback_query(F.data == kb.PRK_CANCEL)
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    if await _current_step(state) is not None:
        await _cancel(callback.message, state)
    else:  # кнопка из старого сообщения: сценарий уже не идёт
        await callback.message.answer(t("menu.title"), reply_markup=kb.main_menu())


@router.callback_query(StateFilter(PracticumForm), F.data == kb.PRK_BACK)
async def on_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    current = await state.get_state()
    previous = PREVIOUS_STEP.get(current or "")
    if callback.message is None or previous is None:
        return
    if current == PracticumForm.phone.state:
        await callback.message.answer(t("practicum.back_to_email"), reply_markup=ReplyKeyboardRemove())
    await _ask(callback.message, state, previous)


# шаг 1 — имя


@router.callback_query(PracticumForm.name, F.data == kb.PRK_NAME_KEEP)
async def on_name_keep(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    data = await state.get_data()
    name = normalize_name(data.get(NAME_KEY)) or normalize_name(data.get(TG_NAME_KEY))
    if name is None:
        await _ask(callback.message, state, PracticumForm.name)
        return
    await state.update_data({NAME_KEY: name})
    await _ask(callback.message, state, PracticumForm.email)


@router.message(PracticumForm.name, F.text, NOT_COMMAND)
async def on_name(message: Message, state: FSMContext) -> None:
    name = normalize_name(message.text)
    if name is None:
        await _reject(message, state, PracticumForm.name, "practicum.name_invalid")
        return
    await state.update_data({NAME_KEY: name})
    await _ask(message, state, PracticumForm.email)


# шаг 2 — почта


@router.callback_query(PracticumForm.email, F.data == kb.PRK_EMAIL_KEEP)
async def on_email_keep(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    email = normalize_email((await state.get_data()).get(EMAIL_KEY))
    next_step = PracticumForm.phone if email else PracticumForm.email
    await _ask(callback.message, state, next_step)


@router.message(PracticumForm.email, F.text, NOT_COMMAND)
async def on_email(message: Message, state: FSMContext) -> None:
    email = normalize_email(message.text)
    if email is None:
        await _reject(message, state, PracticumForm.email, "practicum.email_invalid")
        return
    await state.update_data({EMAIL_KEY: email})
    await _ask(message, state, PracticumForm.phone)


# шаг 3 — телефон


async def _phone_done(message: Message, state: FSMContext, phone: str | None) -> None:
    await state.update_data({PHONE_KEY: phone})
    done_key = "practicum.phone_saved" if phone else "practicum.phone_skipped"
    await message.answer(t(done_key), reply_markup=ReplyKeyboardRemove())
    await _ask(message, state, PracticumForm.consent)


@router.message(PracticumForm.phone, F.contact)
async def on_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    if contact is None or message.from_user is None or contact.user_id != message.from_user.id:
        await message.answer(t("practicum.phone_not_yours"), reply_markup=kb.practicum_nav_kb())
        return
    await _phone_done(message, state, normalize_contact_phone(contact.phone_number))


@router.message(PracticumForm.phone, F.text, NOT_COMMAND)
async def on_phone(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == t("practicum.buttons.skip"):
        await _phone_done(message, state, None)
        return
    phone = normalize_phone(text)
    if phone is None:
        await _reject(message, state, PracticumForm.phone, "practicum.phone_invalid")
        return
    await _phone_done(message, state, phone)


# шаг 4 — согласие и запись


@router.callback_query(PracticumForm.consent, F.data == kb.PRK_AGREE)
async def on_agree(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    target = callback.message
    data = await state.get_data()
    name, email = data.get(NAME_KEY), data.get(EMAIL_KEY)
    if not name:
        await _ask(target, state, PracticumForm.name)
        return
    if not email:
        await _ask(target, state, PracticumForm.email)
        return
    phone = data.get(PHONE_KEY)

    async with session_scope() as session:
        person = await get_or_create_by_telegram(session, callback.from_user, data.get(START_SOURCE_KEY))
        person.name = name
        person.email = email
        if phone:
            person.phone = phone
        person.consent_at = utcnow()
        source = data.get(START_SOURCE_KEY) or person.source
        _registration, created = await register_for_event(session, person, EVENT_CODE, channel="bot", source=source)
        count = await count_registrations(session, EVENT_CODE)
        person_id = person.id
        username = person.telegram_username
        saved_phone = person.phone
        ask_role = person.role is None
    await state.clear()
    log.info("practicum: registration saved person=%s created=%s", person_id, created)

    await target.answer(t("practicum.done", name=name), reply_markup=kb.practicum_done_kb())
    if created:
        await notify_admins(
            callback.bot,
            t(
                "practicum.admin_new",
                name=name,
                email=email,
                phone=saved_phone or NO_VALUE,
                username=f"@{username}" if username else NO_VALUE,
                source=source or NO_VALUE,
                count=count,
            ),
        )
    if ask_role:
        await target.answer(t("practicum.ask_role"), reply_markup=kb.practicum_role_kb())


# прочее на шагах: стикеры, фото, контакт не на том шаге, текст на шаге согласия


@router.message(StateFilter(PracticumForm), NOT_COMMAND)
async def on_other_in_flow(message: Message, state: FSMContext) -> None:
    step = await _current_step(state)
    if step is None:
        return
    if message.text and looks_like_question(message.text):
        await answer_question_inline(message, message.text)
    await _ask(message, state, step)


# --- после записи -----------------------------------------------------------------------------


@router.callback_query(F.data == kb.PRK_ICS)
async def on_calendar(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    async with session_scope() as session:
        event = await get_or_sync_event(session, EVENT_CODE)
        content = build_ics(event)
    await callback.message.answer_document(
        BufferedInputFile(content, filename=ICS_FILENAME),
        caption=t("practicum.ics_caption"),
    )


@router.callback_query(F.data.startswith(kb.PRK_ROLE_PREFIX))
async def on_role(callback: CallbackQuery) -> None:
    await callback.answer()
    code = (callback.data or "").removeprefix(kb.PRK_ROLE_PREFIX)
    if code in kb.PRK_ROLE_CODES:
        async with session_scope() as session:
            person = await get_by_telegram_id(session, callback.from_user.id)
            if person is not None:
                person.role = code
                log.info("practicum: role saved person=%s", person.id)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        if code in kb.PRK_ROLE_CODES:
            await callback.message.answer(t("practicum.role_thanks"))
