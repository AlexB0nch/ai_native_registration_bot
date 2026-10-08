"""TASK-BOT-002: вопросы пользователя → владельцу → ответ владельца пользователю."""

from __future__ import annotations

import logging

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards
from app.bot.handlers.questions import answer_question_inline, escalate
from app.models import Person, Question
from tests.helpers import (
    ADMIN_ID,
    BOT_ID,
    OTHER_USER_ID,
    USER_ID,
    TgHarness,
    button_texts,
    find_button,
    make_message_update,
)

ADMIN2_ID = 900002
QUESTION = "Можно ли прийти на практикум без ноутбука?"


@pytest.fixture(autouse=True)
def answer_is_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Здесь проверяется пересылка владельцу, а не ответы: любой вопрос — «не знаю, передал».

    Быстрые ответы и модель проверяются в tests/test_faq.py и tests/test_llm_service.py.
    """
    from app.llm import service
    from app.texts import t

    async def fake_answer(text: str) -> service.Answer:
        return service.Answer(t("questions.escalated"), answered=False, handoff=True)

    monkeypatch.setattr(service, "answer_question", fake_answer)


def admin_key(admin_id: int = ADMIN_ID) -> StorageKey:
    return StorageKey(bot_id=BOT_ID, chat_id=admin_id, user_id=admin_id)


async def last_question(db: AsyncSession) -> Question:
    db.expire_all()
    question = await db.scalar(select(Question).order_by(Question.id.desc()).limit(1))
    assert question is not None
    return question


async def ask(tg: TgHarness, text: str = QUESTION) -> None:
    await tg.send("/start lp_opex", username="Irina_Ops")
    await tg.send("/question", username="Irina_Ops")
    await tg.send(text, username="Irina_Ops")


@pytest.fixture
def two_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ADMIN_CHAT_IDS", f"{ADMIN_ID},{ADMIN2_ID}")
    get_settings.cache_clear()


async def test_question_button_relay_and_answer(tg: TgHarness, db: AsyncSession) -> None:
    """A4: вопрос → владельцу с кнопкой → «Ответить» → ответ уходит пользователю, answered_at заполнен."""
    await tg.send("/start lp_opex", username="Irina_Ops")
    await tg.click(keyboards.MENU_QUESTION, username="Irina_Ops")
    assert tg.session.last_text(USER_ID) == "Напишите вопрос одним сообщением."
    await tg.send(QUESTION, username="Irina_Ops")
    assert tg.session.last_text(USER_ID) == "Передал вопрос Александру, он ответит здесь же."

    question = await last_question(db)
    assert question.text == QUESTION
    assert question.answered_at is None
    to_admin = tg.session.last_message(ADMIN_ID)
    assert to_admin["text"] == f"Вопрос от Ирина (@irina_ops, lp_opex):\n{QUESTION}"
    button = find_button(to_admin, "Ответить")
    assert button["callback_data"] == f"q:answer:{question.id}"
    assert question.admin_chat_id == ADMIN_ID
    assert question.admin_message_id is not None

    await tg.click(button["callback_data"], user_id=ADMIN_ID)
    prompt = tg.session.last_message(ADMIN_ID)
    assert prompt["text"] == "Напишите ответ для Ирина."
    assert button_texts(prompt) == ["Отмена"]
    assert await tg.dp.storage.get_state(admin_key()) == f"answering:{question.id}"

    await tg.send("Можно, хватит телефона.", user_id=ADMIN_ID)
    to_user = tg.session.last_message(USER_ID)
    assert to_user["text"] == "Ответ Александра:\nМожно, хватит телефона."
    assert button_texts(to_user) == ["Записаться на практикум 31 октября"]
    assert find_button(to_user, "Записаться")["callback_data"] == keyboards.MENU_PRACTICUM
    assert tg.session.last_text(ADMIN_ID) == "Отправлено."
    assert await tg.dp.storage.get_state(admin_key()) is None

    question = await last_question(db)
    assert question.answer_text == "Можно, хватит телефона."
    assert question.answered_at is not None

    # следующий текст владельца уже не уходит пользователю
    sent_to_user = len(tg.session.sent_messages(USER_ID))
    await tg.send("/stats", user_id=ADMIN_ID)
    assert len(tg.session.sent_messages(USER_ID)) == sent_to_user


async def test_reply_to_question_message(tg: TgHarness, db: AsyncSession) -> None:
    """A5: ответ реплаем на сообщение с вопросом."""
    await ask(tg)
    question = await last_question(db)
    await tg.send("Ответ реплаем", user_id=ADMIN_ID, reply_to_message_id=question.admin_message_id)
    assert tg.session.last_text(USER_ID) == "Ответ Александра:\nОтвет реплаем"
    assert tg.session.last_text(ADMIN_ID) == "Отправлено."
    question = await last_question(db)
    assert question.answered_at is not None
    assert question.answer_text == "Ответ реплаем"


async def test_reply_to_other_message_is_not_an_answer(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    tg.session.clear()
    await tg.send("Просто реплай", user_id=ADMIN_ID, reply_to_message_id=question.admin_message_id + 999)
    assert tg.session.sent_messages(USER_ID) == []
    assert (await db.get(Question, question.id)).answered_at is None


async def test_reply_from_second_admin_uses_button_in_replied_message(
    tg: TgHarness, db: AsyncSession, two_admins: None
) -> None:
    await ask(tg)
    question = await last_question(db)
    copy = tg.session.last_message(ADMIN2_ID)
    assert find_button(copy, "Ответить")["callback_data"] == f"q:answer:{question.id}"
    assert question.admin_chat_id == ADMIN_ID  # хранится первое пересланное сообщение

    raw = make_message_update("Ответ второго админа", user_id=ADMIN2_ID, reply_to_message_id=777).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    raw["message"]["reply_to_message"]["reply_markup"] = copy["reply_markup"]
    await tg.feed(Update.model_validate(raw))
    assert tg.session.last_text(USER_ID) == "Ответ Александра:\nОтвет второго админа"
    assert tg.session.last_text(ADMIN2_ID) == "Отправлено."


async def test_second_answer_is_sent_as_addition(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    await tg.send("Первый ответ", user_id=ADMIN_ID)
    first_answered_at = (await last_question(db)).answered_at

    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    assert "дополнение" in tg.session.last_text(ADMIN_ID)
    await tg.send("Ещё одно", user_id=ADMIN_ID)
    assert tg.session.last_text(USER_ID) == "Дополнение от Александра:\nЕщё одно"
    question = await last_question(db)
    assert question.answer_text == "Первый ответ\n\nЕщё одно"
    assert question.answered_at == first_answered_at


async def test_blocked_user_is_reported_to_admin(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    tg.session.block_chat(USER_ID)
    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    await tg.send("Ответ", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Не доставлено: пользователь заблокировал бота"
    question = await last_question(db)
    assert question.answered_at is None
    assert question.answer_text is None
    assert await tg.dp.storage.get_state(admin_key()) is None


async def test_cancel_answer(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    await tg.click_button("Отмена", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Ответ отменён."
    assert await tg.dp.storage.get_state(admin_key()) is None
    sent_to_user = len(tg.session.sent_messages(USER_ID))
    await tg.send("Этот текст не ответ", user_id=ADMIN_ID)
    assert len(tg.session.sent_messages(USER_ID)) == sent_to_user


async def test_answering_state_does_not_intercept_commands(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    await tg.send("/stats", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID).startswith("Статистика записей")
    assert await tg.dp.storage.get_state(admin_key()) == f"answering:{question.id}"
    await tg.send(None, user_id=ADMIN_ID, contact={"phone_number": "+79123456789", "first_name": "A"})
    assert tg.session.last_text(ADMIN_ID).startswith("Ответ отправляется только текстом")
    await tg.send("Ответ после статистики", user_id=ADMIN_ID)
    assert tg.session.last_text(USER_ID) == "Ответ Александра:\nОтвет после статистики"


async def test_answer_state_is_per_admin(tg: TgHarness, db: AsyncSession, two_admins: None) -> None:
    await ask(tg)
    question = await last_question(db)
    await tg.click(f"q:answer:{question.id}", user_id=ADMIN_ID)
    assert await tg.dp.storage.get_state(admin_key(ADMIN2_ID)) is None
    sent_to_user = len(tg.session.sent_messages(USER_ID))
    await tg.send("Текст второго админа", user_id=ADMIN2_ID)
    assert len(tg.session.sent_messages(USER_ID)) == sent_to_user  # не ответ на вопрос


async def test_unknown_question_id(tg: TgHarness) -> None:
    await tg.click("q:answer:9999", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Вопрос не найден."
    assert await tg.dp.storage.get_state(admin_key()) is None


async def test_non_admin_cannot_answer(tg: TgHarness, db: AsyncSession) -> None:
    await ask(tg)
    question = await last_question(db)
    await tg.click(f"q:answer:{question.id}", user_id=OTHER_USER_ID)
    assert tg.session.last_text(OTHER_USER_ID).startswith("Выберите действие")
    other_key = StorageKey(bot_id=BOT_ID, chat_id=OTHER_USER_ID, user_id=OTHER_USER_ID)
    assert await tg.dp.storage.get_state(other_key) is None


async def test_free_text_without_start_is_escalated(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("Позовите человека, пожалуйста", first_name="Пётр")
    assert tg.session.last_text(USER_ID) == "Передал вопрос Александру, он ответит здесь же."
    assert tg.session.last_text(ADMIN_ID) == "Вопрос от Пётр (без метки):\nПозовите человека, пожалуйста"
    person = await db.scalar(select(Person).where(Person.telegram_id == USER_ID))
    assert person is not None
    person_id = person.id
    assert (await last_question(db)).person_id == person_id


async def test_commands_and_non_text_are_not_escalated(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/stats", user_id=OTHER_USER_ID)
    await tg.send("/question")
    await tg.send(None, contact={"phone_number": "+79123456789", "first_name": "Ира", "user_id": USER_ID})
    assert tg.session.last_text(USER_ID) == "Напишите вопрос текстом, одним сообщением."
    await tg.send("/nonsense")
    assert tg.session.last_text(USER_ID).startswith("Выберите действие")
    assert tg.session.sent_messages(ADMIN_ID) == []
    assert await db.scalar(select(func.count()).select_from(Question)) == 0


async def test_question_state_overrides_other_flow(tg: TgHarness, db: AsyncSession) -> None:
    key = StorageKey(bot_id=BOT_ID, chat_id=USER_ID, user_id=USER_ID)
    await tg.dp.storage.set_state(key, "SomeFlow:step")
    await tg.send("/question")
    await tg.send(QUESTION)
    assert tg.session.last_text(USER_ID) == "Передал вопрос Александру, он ответит здесь же."
    assert await tg.dp.storage.get_state(key) is None


async def test_escalate_with_bot_answer(tg: TgHarness, db: AsyncSession) -> None:
    """Контракт для LLM-001: escalate(message, person, text, bot_answer)."""
    message = make_message_update("Есть ли рассрочка?", username="irina").message.as_(tg.bot)
    question = await escalate(message, None, "Есть ли рассрочка?", bot_answer="Точного ответа у меня нет.")
    assert question is not None
    stored = await db.get(Question, question.id)
    assert stored.bot_answer == "Точного ответа у меня нет."
    assert stored.admin_message_id is not None
    assert tg.session.last_text(ADMIN_ID).endswith("Ответ бота: Точного ответа у меня нет.")
    assert tg.session.sent_messages(USER_ID) == []  # пользователю пишет вызывающий код


async def test_answer_inline_ignores_bot_authored_message(tg: TgHarness, db: AsyncSession) -> None:
    message = make_message_update("…", user_id=BOT_ID).message
    raw = message.model_dump(by_alias=True, exclude_none=True)
    raw["from"]["is_bot"] = True
    bot_message = type(message).model_validate(raw).as_(tg.bot)
    await answer_question_inline(bot_message, "вопрос")
    assert await db.scalar(select(func.count()).select_from(Question)) == 0
    assert tg.session.last_text().startswith("Выберите действие")


async def test_no_personal_data_in_logs(tg: TgHarness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    await ask(tg, text="Секретный вопрос про проект")
    await tg.click("q:answer:1", user_id=ADMIN_ID)
    await tg.send("Секретный ответ", user_id=ADMIN_ID)
    log_text = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app"))
    for fragment in ("Секретный", "Ирина", "irina"):
        assert fragment.lower() not in log_text.lower()
