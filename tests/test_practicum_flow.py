from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendDocument
from aiogram.types import BufferedInputFile, Message, User
from icalendar import Calendar
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards
from app.bot.handlers import practicum as practicum_handlers
from app.bot.handlers.practicum import PracticumForm
from app.models import Person, Registration
from app.services.people import get_or_create_by_telegram
from app.services.registrations import count_registrations, register_for_event
from app.texts import t
from tests.helpers import (
    ADMIN_ID,
    BOT_ID,
    OTHER_USER_ID,
    USER_ID,
    TgHarness,
    button_texts,
    make_user,
)

SLOT = "31 октября, суббота, 15:00–17:00 МСК"
USERNAME = "Irina_Ops"


@pytest.fixture
def questions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Перехват `answer_question_inline` (реальные ответы — TASK-LLM-001)."""
    asked: list[str] = []

    async def fake_answer(message: Message, text: str) -> None:
        asked.append(text)
        await message.answer("ОТВЕТ НА ВОПРОС")

    monkeypatch.setattr(practicum_handlers, "answer_question_inline", fake_answer)
    return asked


def _key(user_id: int = USER_ID) -> StorageKey:
    return StorageKey(bot_id=BOT_ID, chat_id=user_id, user_id=user_id)


async def _state(tg: TgHarness, user_id: int = USER_ID) -> str | None:
    return await tg.dp.storage.get_state(_key(user_id))


async def _person(db: AsyncSession, telegram_id: int = USER_ID) -> Person:
    db.expire_all()
    person = await db.scalar(select(Person).where(Person.telegram_id == telegram_id))
    assert person is not None
    return person


async def _registrations(db: AsyncSession) -> list[Registration]:
    db.expire_all()
    return list((await db.scalars(select(Registration))).all())


def _admin_texts(tg: TgHarness) -> list[str]:
    return tg.session.sent_texts(ADMIN_ID)


async def _register(tg: TgHarness, user_id: int = USER_ID, email: str = "Irina@Example.COM") -> None:
    """Полный сценарий: кнопка меню → «Да, Имя» → почта → «Пропустить» → «Согласен»."""
    await tg.click(keyboards.MENU_PRACTICUM, user_id)
    await tg.click_button("Да, ", user_id)
    await tg.send(email, user_id)
    await tg.send("Пропустить", user_id)
    await tg.click_button("Согласен", user_id)


# --- A1: полный сценарий --------------------------------------------------------------------


async def test_full_flow_from_landing_label(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start lp_opex_p1", username=USERNAME)
    # /start с меткой практикума сразу открывает первый шаг
    assert tg.session.last_text(USER_ID) == "Как к вам обращаться? Нажмите «Да, Ирина» или напишите, как вас зовут."
    assert await _state(tg) == PracticumForm.name.state

    await tg.click(keyboards.MENU_PRACTICUM, username=USERNAME)
    name_prompt = tg.session.last_message(USER_ID)
    assert button_texts(name_prompt) == ["Да, Ирина", "Отмена"]
    await tg.click_button("Да, Ирина", username=USERNAME)
    assert tg.session.last_text(USER_ID) == "Ваша почта — на случай, если Telegram подведёт."
    assert button_texts(tg.session.last_message(USER_ID)) == ["Назад", "Отмена"]

    await tg.send("Irina@Example.COM", username=USERNAME)
    phone_prompt, phone_nav = tg.session.sent_messages(USER_ID)[-2:]
    keyboard = phone_prompt["reply_markup"]["keyboard"]
    assert keyboard[0][0] == {"text": "Поделиться контактом", "request_contact": True}
    assert button_texts(phone_prompt) == ["Поделиться контактом", "Пропустить"]
    assert button_texts(phone_nav) == ["Назад", "Отмена"]

    await tg.send("Пропустить", username=USERNAME)
    skipped, consent = tg.session.sent_messages(USER_ID)[-2:]
    assert skipped["reply_markup"] == {"remove_keyboard": True}
    assert consent["text"] == (
        "Нажимая «Согласен», вы соглашаетесь на обработку персональных данных: https://example.test/privacy"
    )
    assert button_texts(consent) == ["Согласен", "Назад", "Отмена"]
    assert not _admin_texts(tg)

    await tg.click_button("Согласен", username=USERNAME)

    registrations = await _registrations(db)
    assert len(registrations) == 1
    registration = registrations[0]
    assert registration.source == "lp_opex_p1"
    assert registration.channel == "bot"
    assert registration.status == "registered"
    person_id = registration.person_id

    person = await _person(db)
    assert person.id == person_id
    assert person.name == "Ирина"
    assert person.email == "irina@example.com"
    assert person.phone is None
    assert person.consent_at is not None
    assert person.source == "lp_opex_p1"

    done, role_question = tg.session.sent_messages(USER_ID)[-2:]
    assert done["text"] == (
        f"Готово, Ирина! Вы записаны на практикум {SLOT}, Яндекс Телемост.\n"
        "За день до начала пришлю ссылку на подключение, за час — напоминание."
    )
    assert button_texts(done) == ["Добавить в календарь", "Что будет на курсе", "Цены и формат"]
    assert role_question["text"] == "Необязательный вопрос: чем вы занимаетесь?"
    assert button_texts(role_question) == [
        "Независимый консультант",
        "Lean/OpEx в компании",
        "Руководитель трансформации или PMO",
        "Другое",
        "Пропустить",
    ]

    assert _admin_texts(tg) == [
        "Новая запись на практикум: Ирина, irina@example.com, —, @irina_ops, источник: lp_opex_p1. "
        "Всего записались: 1."
    ]
    assert await _state(tg) is None


async def test_role_answer_saved(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await _register(tg)
    await tg.click_button("Lean/OpEx")
    assert (await _person(db)).role == "opex"
    assert tg.session.calls_of("editMessageReplyMarkup")
    assert tg.session.last_text(USER_ID) == "Спасибо!"


async def test_role_skip_keeps_role_empty(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await _register(tg)
    await tg.click(keyboards.PRK_ROLE_PREFIX + keyboards.PRK_ROLE_SKIP)
    assert (await _person(db)).role is None
    assert tg.session.calls_of("editMessageReplyMarkup")


async def test_practicum_command_for_new_user(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/practicum", first_name="Олег")
    assert tg.session.last_text(USER_ID).startswith("Как к вам обращаться? Нажмите «Да, Олег»")
    await tg.send("Олег Иванов")
    await tg.send("oleg@example.com")
    await tg.send("+7 (912) 345-67-89")
    saved, _consent = tg.session.sent_messages(USER_ID)[-2:]
    assert saved["text"] == "Телефон сохранён."
    assert saved["reply_markup"] == {"remove_keyboard": True}
    await tg.click_button("Согласен")
    person = await _person(db)
    assert (person.name, person.phone, person.source) == ("Олег Иванов", "+79123456789", None)
    registration = (await _registrations(db))[0]
    assert registration.source is None
    assert "+79123456789" in _admin_texts(tg)[0]
    assert "источник: —" in _admin_texts(tg)[0]


async def test_source_comes_from_people_when_fsm_has_none(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start tgads_opex_w2")
    await tg.dp.storage.set_data(_key(), {})
    await _register(tg)
    assert (await _registrations(db))[0].source == "tgads_opex_w2"


# --- A2: без дублей -------------------------------------------------------------------------


async def test_second_pass_does_not_duplicate(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start lp_opex_p1", username="irina_ops")
    await _register(tg)
    first = (await _registrations(db))[0]
    created_at = first.created_at
    assert len(_admin_texts(tg)) == 1

    # повторный вход — «вы уже записаны»
    await tg.send("/start prk_tg2")
    already = tg.session.last_message(USER_ID)
    assert already["text"] == f"Вы уже записаны на практикум {SLOT}. Ссылку пришлю за день до начала."
    assert button_texts(already) == ["Изменить данные", "Добавить в календарь", "В меню"]
    assert await _state(tg) is None

    await tg.click(keyboards.MENU_PRACTICUM)
    assert tg.session.last_text(USER_ID).startswith("Вы уже записаны")

    # «Изменить данные» — те же шаги, та же запись
    await tg.click_button("Изменить данные")
    assert tg.session.last_text(USER_ID).startswith("Как к вам обращаться? Нажмите «Да, Ирина»")
    await tg.send("Ира")
    assert button_texts(tg.session.last_message(USER_ID)) == ["Оставить irina@example.com", "Назад", "Отмена"]
    await tg.click_button("Оставить")
    await tg.send("8 912 345 67 89")
    await tg.click_button("Согласен")

    registrations = await _registrations(db)
    assert len(registrations) == 1
    assert registrations[0].id == first.id
    assert registrations[0].created_at == created_at
    assert registrations[0].source == "lp_opex_p1"
    person = await _person(db)
    assert (person.name, person.email, person.phone) == ("Ира", "irina@example.com", "+79123456789")
    assert len(_admin_texts(tg)) == 1
    assert tg.session.sent_texts(USER_ID)[-2].startswith("Готово, Ира!")


async def test_two_people_counted(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await _register(tg)
    await tg.send("/start", user_id=OTHER_USER_ID, first_name="Павел")
    await _register(tg, OTHER_USER_ID, email="pavel@example.com")
    admin = _admin_texts(tg)
    assert len(admin) == 2
    assert admin[1].startswith("Новая запись на практикум: ")
    assert "pavel@example.com" in admin[1]
    assert admin[1].endswith("Всего записались: 2.")


async def test_register_for_event_service(db: AsyncSession) -> None:
    person = await get_or_create_by_telegram(db, User.model_validate(make_user(USER_ID, "irina")), "lp_a")
    registration, created = await register_for_event(db, person, "practicum", channel="bot", source="lp_a")
    assert created is True
    await db.commit()
    created_at = registration.created_at

    again, created = await register_for_event(db, person, "practicum", channel="site", source="other")
    assert created is False
    assert again.id == registration.id
    assert (again.source, again.channel, again.created_at) == ("lp_a", "bot", created_at)
    assert await count_registrations(db, "practicum") == 1
    assert await count_registrations(db, "course") == 0

    again.status = "cancelled"
    await db.flush()
    assert await count_registrations(db, "practicum") == 0
    restored, created = await register_for_event(db, person, "practicum", channel="bot", source=None)
    assert created is False
    assert restored.status == "registered"
    assert await db.scalar(select(func.count()).select_from(Registration)) == 1


# --- A3: «Назад» и «Отмена» -----------------------------------------------------------------


async def test_back_from_email_keeps_name(tg: TgHarness) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.send("Ирина Петрова")
    assert await _state(tg) == PracticumForm.email.state
    await tg.click_button("Назад")
    assert await _state(tg) == PracticumForm.name.state
    name_prompt = tg.session.last_message(USER_ID)
    assert name_prompt["text"] == "Как к вам обращаться? Нажмите «Да, Ирина Петрова» или напишите, как вас зовут."
    await tg.click_button("Да, Ирина Петрова")
    assert await _state(tg) == PracticumForm.email.state


async def test_back_from_phone_and_consent(tg: TgHarness) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("irina@example.com")
    await tg.click(keyboards.PRK_BACK)
    back_note, email_prompt = tg.session.sent_messages(USER_ID)[-2:]
    assert back_note["reply_markup"] == {"remove_keyboard": True}
    assert button_texts(email_prompt)[0] == "Оставить irina@example.com"
    assert await _state(tg) == PracticumForm.email.state
    await tg.click_button("Оставить")
    await tg.send("89123456789")
    assert await _state(tg) == PracticumForm.consent.state
    await tg.click_button("Назад")
    assert await _state(tg) == PracticumForm.phone.state
    phone_prompt = tg.session.sent_messages(USER_ID)[-2]
    assert button_texts(phone_prompt) == ["Поделиться контактом", "+79123456789", "Пропустить"]


async def test_cancel_exits_without_registration(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start lp_opex_p1")
    await tg.click_button("Да, Ирина")
    await tg.send("irina@example.com")
    await tg.click(keyboards.PRK_CANCEL)
    assert await _state(tg) is None
    cancelled, menu = tg.session.sent_messages(USER_ID)[-2:]
    assert cancelled["text"].startswith("Запись отменена")
    assert cancelled["reply_markup"] == {"remove_keyboard": True}
    assert button_texts(menu)[0] == "Записаться на практикум 31 октября"
    assert await _registrations(db) == []
    assert (await _person(db)).email is None
    assert not _admin_texts(tg)


async def test_cancel_on_name_step_and_cancel_command(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Отмена")
    assert await _state(tg) is None
    message = tg.session.last_message(USER_ID)
    assert message["text"].startswith("Запись отменена")
    assert button_texts(message)[0] == "Записаться на практикум 31 октября"

    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("/cancel")
    assert await _state(tg) is None
    assert tg.session.last_text(USER_ID).startswith("Запись отменена")
    assert await _registrations(db) == []


async def test_start_interrupts_flow(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("/start")
    assert await _state(tg) is None
    assert tg.session.last_text(USER_ID).startswith("Здравствуйте!")
    await tg.send("irina@example.com")  # уже вне сценария
    assert await _registrations(db) == []


async def test_cancel_edit_keeps_registration(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/start")
    await _register(tg)
    await tg.click(keyboards.PRK_EDIT)
    await tg.click(keyboards.PRK_CANCEL)
    assert tg.session.last_text(USER_ID) == "Изменения не сохранены. Вы по-прежнему записаны на практикум 31 октября."
    assert len(await _registrations(db)) == 1


# --- A4: ошибки и вопросы вместо ответа -----------------------------------------------------


async def test_invalid_email_hint_same_step(tg: TgHarness, questions: list[str]) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("irina@mail")
    message = tg.session.last_message(USER_ID)
    assert message["text"] == "Похоже, в адресе опечатка. Пример: name@company.ru"
    assert button_texts(message) == ["Назад", "Отмена"]
    assert await _state(tg) == PracticumForm.email.state
    assert questions == []


async def test_question_on_email_step(tg: TgHarness, questions: list[str]) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    tg.session.clear()
    await tg.send("А сколько стоит курс?")
    assert questions == ["А сколько стоит курс?"]
    assert tg.session.sent_texts(USER_ID) == ["ОТВЕТ НА ВОПРОС", "Ваша почта — на случай, если Telegram подведёт."]
    assert await _state(tg) == PracticumForm.email.state
    await tg.send("irina@example.com")
    assert await _state(tg) == PracticumForm.phone.state


async def test_question_on_name_and_phone_steps(tg: TgHarness, questions: list[str]) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.send("Нужно ли уметь программировать?")
    assert tg.session.last_text(USER_ID).startswith("Как к вам обращаться?")
    assert await _state(tg) == PracticumForm.name.state
    await tg.send("https://example.com")
    assert tg.session.last_text(USER_ID) == "Напишите, пожалуйста, только имя — до 100 символов, без ссылок."
    await tg.send("Ирина")
    await tg.send("irina@example.com")
    await tg.send("а запись практикума будет потом")
    assert tg.session.last_text(USER_ID) == "Можно вернуться к почте или отменить запись."
    assert await _state(tg) == PracticumForm.phone.state
    assert questions == ["Нужно ли уметь программировать?", "а запись практикума будет потом"]


async def test_other_messages_repeat_step(tg: TgHarness, questions: list[str]) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("irina@example.com")
    await tg.send("Пропустить")
    await tg.send("ок")  # текст вместо кнопки «Согласен»
    assert tg.session.last_text(USER_ID).startswith("Нажимая «Согласен»")
    assert await _state(tg) == PracticumForm.consent.state
    assert questions == []


# --- A5: телефон и контакт ------------------------------------------------------------------


async def _to_phone_step(tg: TgHarness) -> None:
    await tg.send("/start")
    await tg.click(keyboards.MENU_PRACTICUM)
    await tg.click_button("Да, ")
    await tg.send("irina@example.com")


async def test_invalid_phone(tg: TgHarness) -> None:
    await _to_phone_step(tg)
    await tg.send("12345")
    message = tg.session.last_message(USER_ID)
    assert message["text"].startswith("Не получилось разобрать номер.")
    assert button_texts(message) == ["Назад", "Отмена"]
    assert await _state(tg) == PracticumForm.phone.state


async def test_foreign_contact_rejected_own_accepted(tg: TgHarness, db: AsyncSession) -> None:
    await _to_phone_step(tg)
    await tg.send(None, contact={"phone_number": "+79990000000", "first_name": "Павел", "user_id": OTHER_USER_ID})
    assert tg.session.last_text(USER_ID).startswith("Это не ваш контакт.")
    assert await _state(tg) == PracticumForm.phone.state
    await tg.send(None, contact={"phone_number": "+79990000000", "first_name": "Без id"})
    assert tg.session.last_text(USER_ID).startswith("Это не ваш контакт.")

    await tg.send(None, contact={"phone_number": "79123456789", "first_name": "Ирина", "user_id": USER_ID})
    assert await _state(tg) == PracticumForm.consent.state
    await tg.click_button("Согласен")
    assert (await _person(db)).phone == "+79123456789"


# --- .ics -----------------------------------------------------------------------------------


async def test_calendar_button_sends_ics(tg: TgHarness) -> None:
    await tg.send("/start")
    await _register(tg)
    tg.session.clear()
    await tg.click(keyboards.PRK_ICS)
    documents = [m for m in tg.session.methods if isinstance(m, SendDocument)]
    assert len(documents) == 1
    document = documents[0].document
    assert isinstance(document, BufferedInputFile)
    assert document.filename == "practicum.ics"
    assert documents[0].chat_id == USER_ID
    vevent = next(iter(Calendar.from_ical(document.data).walk("VEVENT")))
    assert vevent.decoded("DTSTART") == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert vevent.decoded("DTEND") == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)


# --- A7: тексты -----------------------------------------------------------------------------


def test_practicum_texts_use_facts() -> None:
    assert t("practicum.done", name="Ирина").startswith(f"Готово, Ирина! Вы записаны на практикум {SLOT}")
    assert t("practicum.already") == f"Вы уже записаны на практикум {SLOT}. Ссылку пришлю за день до начала."
    assert t("practicum.buttons.name_yes", name="Ирина") == "Да, Ирина"
