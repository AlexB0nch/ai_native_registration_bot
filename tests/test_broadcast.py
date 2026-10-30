"""TASK-BOT-007: рассылка участникам практикума командой владельца."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import broadcast as handler
from app.models import Delivery, Event, Person, Registration
from app.services import broadcast as service
from app.services.events import sync_events_from_facts
from tests.helpers import (
    ADMIN_ID,
    BOT_ID,
    OTHER_USER_ID,
    TgHarness,
    button_texts,
    find_button,
)

FALLBACK = "Выберите действие в меню или нажмите «Задать вопрос»."
TEXT = (
    "Напоминаю: завтра в 15:00 по Москве бесплатный практикум.\n\n"
    "Событие в календаре: https://calendar.yandex.ru/event/1?applyToFuture=0&event_date=x\n\n"
    "Добавьте его в календарь."
)
OTHER_TEXT = "Ссылка на подключение появится здесь за час до начала."
COMMAND = "/broadcast_practicum"

# Telegram id получателей: не должны попадать в логи.
TG_IDS = {"a": 7100001, "b": 7100002, "c": 7100003}


@pytest.fixture(autouse=True)
async def fast_broadcast(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Без пауз; свежая блокировка в цикле теста; по окончании ждём фоновую задачу."""
    monkeypatch.setattr(handler, "SEND_PAUSE", 0)
    monkeypatch.setattr(handler, "REPORT_RETRY_DELAY", 0)
    monkeypatch.setattr(handler, "_lock", asyncio.Lock())
    yield
    await handler.wait_idle()


@pytest.fixture(autouse=True)
def answer_is_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение не-админа уходит «как обычный текст» — в тестах без модели."""
    from app.llm import service as llm_service
    from app.texts import t

    async def fake_answer(text: str) -> llm_service.Answer:
        return llm_service.Answer(t("questions.escalated"), answered=False, handoff=True)

    monkeypatch.setattr(llm_service, "answer_question", fake_answer)


# --- данные и помощники -----------------------------------------------------------------------


async def add_person(
    db: AsyncSession,
    event: Event,
    telegram_id: int | None,
    *,
    status: str = "registered",
    name: str = "Участник",
    username: str | None = None,
) -> Person:
    person = Person(telegram_id=telegram_id, name=name, tg_first_name=name, telegram_username=username)
    db.add(person)
    await db.flush()
    db.add(Registration(person_id=person.id, event_id=event.id, channel="bot", status=status))
    await db.flush()
    return person


async def seed_audience(db: AsyncSession) -> dict[str, Person]:
    """Трое получателей, один отменил запись, один без Telegram, один записан на другое событие."""
    events = await sync_events_from_facts(db)
    practicum = events["practicum"]
    people = {
        "a": await add_person(db, practicum, TG_IDS["a"], name="Ирина Лисицына", username="irina_lisitsyna"),
        "b": await add_person(db, practicum, TG_IDS["b"], name="Борис Орлов", username="boris_orlov"),
        "c": await add_person(db, practicum, TG_IDS["c"], status="confirmed", name="Вера Седова"),
        "cancelled": await add_person(db, practicum, 7100004, status="cancelled"),
        "no_tg": await add_person(db, practicum, None, name="Без Телеграма"),
        "course_only": await add_person(db, events["course"], 7100005),
    }
    await db.commit()
    return people


async def deliveries(db: AsyncSession, kind: str | None = None) -> dict[int, Delivery]:
    query = select(Delivery).execution_options(populate_existing=True)
    if kind is not None:
        query = query.where(Delivery.kind == kind)
    return {d.person_id: d for d in (await db.scalars(query)).all()}


async def count_deliveries(db: AsyncSession) -> int:
    return len((await db.scalars(select(Delivery).execution_options(populate_existing=True))).all())


def assert_plain_without_preview(payload: dict[str, Any]) -> None:
    """Без разметки и с отключённым превью ссылок (остальные поля `link_preview_options` — значения по умолчанию)."""
    assert payload["link_preview_options"]["is_disabled"] is True
    assert not isinstance(payload.get("parse_mode"), str)


async def preview(tg: TgHarness, text: str = TEXT) -> None:
    await tg.send(f"{COMMAND} {text}", user_id=ADMIN_ID)


async def confirm(tg: TgHarness) -> None:
    await tg.click_button("Отправить", user_id=ADMIN_ID)
    await handler.wait_idle()


async def broadcast(tg: TgHarness, text: str = TEXT) -> None:
    await preview(tg, text)
    await confirm(tg)


def texts_to(tg: TgHarness, person: Person) -> list[str]:
    return tg.session.sent_texts(person.telegram_id)


def fail_chat(
    tg: TgHarness,
    chat_id: int,
    exc_factory: Callable[[Any], Exception],
    times: int | None = None,
) -> None:
    """sendMessage в этот чат падает (`times` раз или всегда), остальные чаты работают как обычно."""
    left = {"n": times}

    def responder(method: Any) -> Any:
        payload = method.model_dump(exclude_none=True, warnings=False)
        if payload.get("chat_id") == chat_id and (left["n"] is None or left["n"] > 0):
            if left["n"] is not None:
                left["n"] -= 1
            raise exc_factory(method)
        return tg.session._default_result(tg.bot, method, payload)

    tg.session.respond("sendMessage", responder)


def admin_key() -> StorageKey:
    return StorageKey(bot_id=BOT_ID, chat_id=ADMIN_ID, user_id=ADMIN_ID)


# --- A1: не-админ -----------------------------------------------------------------------------


async def test_command_ignored_for_non_admin(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await tg.send(f"{COMMAND} {TEXT}", user_id=OTHER_USER_ID)
    assert tg.session.sent_texts(OTHER_USER_ID) == [FALLBACK]  # ушло дальше как обычный текст
    assert tg.session.sent_messages(ADMIN_ID) == []
    assert await deliveries(db) == {}


async def test_buttons_ignored_for_non_admin(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await tg.click(f"{handler.BC_SEND_PREFIX}{service.text_hash(TEXT)}", user_id=OTHER_USER_ID)
    assert tg.session.sent_texts(OTHER_USER_ID) == [FALLBACK]
    assert await deliveries(db) == {}


# --- A2: предпросмотр -------------------------------------------------------------------------


async def test_preview_shows_exact_text_and_counts(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await preview(tg)

    first, summary = tg.session.sent_messages(ADMIN_ID)
    assert first["text"] == TEXT  # ровно то, что уйдёт
    assert_plain_without_preview(first)
    assert "reply_markup" not in first

    assert "Получателей: 3. Уже получили это сообщение: 0. Без Telegram: 1." in summary["text"]
    assert "Без Telegram бот не напишет" in summary["text"]
    assert button_texts(summary) == ["Отправить 3", "Отмена"]
    assert find_button(summary, "Отправить")["callback_data"] == f"bc:send:{service.text_hash(TEXT)}"
    assert find_button(summary, "Отмена")["callback_data"] == "bc:cancel"
    assert await deliveries(db) == {}  # предпросмотр ничего не отправляет и не пишет
    for tid in TG_IDS.values():
        assert tg.session.sent_messages(tid) == []


async def test_preview_without_recipients_has_no_send_button(tg: TgHarness, db: AsyncSession) -> None:
    await sync_events_from_facts(db)
    await db.commit()
    await preview(tg)
    summary = tg.session.last_message(ADMIN_ID)
    assert "Получателей: 0." in summary["text"]
    assert "Отправлять некому." in summary["text"]
    assert "reply_markup" not in summary
    assert "Без Telegram бот не напишет" not in summary["text"]


async def test_command_works_in_answering_state(tg: TgHarness, db: AsyncSession) -> None:
    """Команда не перехватывается режимом «Ответить на вопрос» и не сбрасывает его."""
    await seed_audience(db)
    await tg.dp.storage.set_state(admin_key(), "answering:5")
    await preview(tg)
    assert tg.session.sent_texts(ADMIN_ID)[0] == TEXT
    assert button_texts(tg.session.last_message(ADMIN_ID)) == ["Отправить 3", "Отмена"]
    assert await tg.dp.storage.get_state(admin_key()) == "answering:5"
    await confirm(tg)
    assert await tg.dp.storage.get_state(admin_key()) == "answering:5"
    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка завершена: отправлено 3,")


# --- A3: отправка -----------------------------------------------------------------------------


async def test_send_to_all_recipients_and_report(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    await preview(tg)
    send_data = find_button(tg.session.last_message(ADMIN_ID), "Отправить")["callback_data"]
    tg.session.clear()
    await tg.click(send_data, user_id=ADMIN_ID)

    # сразу: кнопки убраны, владелец получил «Рассылка идёт…»
    assert tg.session.calls_of("editMessageReplyMarkup")
    assert tg.session.last_text(ADMIN_ID) == "Рассылка идёт… Пришлю отчёт, когда закончу."
    await handler.wait_idle()

    for key in ("a", "b", "c"):
        messages = tg.session.sent_messages(people[key].telegram_id)
        assert len(messages) == 1, key
        assert messages[0]["text"] == TEXT
        assert_plain_without_preview(messages[0])
    for key in ("cancelled", "no_tg", "course_only"):
        person = people[key]
        assert person.telegram_id is None or tg.session.sent_messages(person.telegram_id) == []

    kind = service.broadcast_kind(TEXT)
    assert kind.startswith("broadcast:") and len(kind) <= 64
    rows = await deliveries(db, kind)
    assert set(rows) == {people[k].id for k in ("a", "b", "c")}
    assert all(row.status == "sent" and row.sent_at is not None and row.error is None for row in rows.values())
    assert len({row.event_id for row in rows.values()}) == 1

    assert tg.session.last_text(ADMIN_ID) == (
        "Рассылка завершена: отправлено 3, заблокировали бота 0, ошибок 0, пропущено (уже получили) 0."
    )


async def test_send_buttons_removed_and_pending_cleared(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await broadcast(tg)
    assert (await tg.dp.storage.get_data(admin_key())) == {}


# --- A4: идемпотентность ----------------------------------------------------------------------


async def test_same_text_is_not_sent_twice_new_text_is(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    await broadcast(tg)
    tg.session.clear()

    await preview(tg)
    summary = tg.session.last_message(ADMIN_ID)
    assert "Получателей: 0. Уже получили это сообщение: 3. Без Telegram: 1." in summary["text"]
    assert "reply_markup" not in summary
    for key in ("a", "b", "c"):
        assert tg.session.sent_messages(people[key].telegram_id) == []

    # прямой повторный вызов сервиса тоже никому не отправляет
    result = await service.send_broadcast(tg.bot, TEXT, pause=0)
    assert (result.sent, result.blocked, result.failed, result.skipped) == (0, 0, 0, 3)
    for key in ("a", "b", "c"):
        assert tg.session.sent_messages(people[key].telegram_id) == []

    # новый текст — новый kind, уходит всем
    await broadcast(tg, OTHER_TEXT)
    for key in ("a", "b", "c"):
        assert texts_to(tg, people[key]) == [OTHER_TEXT]
    assert await count_deliveries(db) == 6
    assert len(await deliveries(db, service.broadcast_kind(OTHER_TEXT))) == 3


async def test_new_registrant_gets_only_missing_message(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    await broadcast(tg)
    practicum = (await db.scalars(select(Event).where(Event.code == "practicum"))).one()
    late = await add_person(db, practicum, 7100009)
    await db.commit()
    tg.session.clear()

    await preview(tg)
    assert "Получателей: 1. Уже получили это сообщение: 3." in tg.session.last_text(ADMIN_ID)
    await confirm(tg)
    assert texts_to(tg, late) == [TEXT]
    assert tg.session.sent_messages(people["a"].telegram_id) == []
    assert tg.session.last_text(ADMIN_ID) == (
        "Рассылка завершена: отправлено 1, заблокировали бота 0, ошибок 0, пропущено (уже получили) 3."
    )


# --- A5: заблокировали бота -------------------------------------------------------------------


async def test_blocked_user_marked_and_not_retried(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    tg.session.block_chat(TG_IDS["b"])
    await broadcast(tg)

    rows = await deliveries(db, service.broadcast_kind(TEXT))
    assert rows[people["a"].id].status == "sent"
    assert rows[people["b"].id].status == "blocked"
    assert rows[people["c"].id].status == "sent"
    assert len(texts_to(tg, people["a"])) == 1 and len(texts_to(tg, people["c"])) == 1
    assert tg.session.last_text(ADMIN_ID) == (
        "Рассылка завершена: отправлено 2, заблокировали бота 1, ошибок 0, пропущено (уже получили) 0."
    )
    assert "Повторите команду" not in tg.session.last_text(ADMIN_ID)

    # заблокированному при повторе того же текста не пишем снова
    tg.session.clear()
    await preview(tg)
    assert "Получателей: 0. Уже получили это сообщение: 3." in tg.session.last_text(ADMIN_ID)


# --- A6: сбой отправки ------------------------------------------------------------------------


async def test_failed_delivery_is_retried_on_next_run(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    fail_chat(tg, TG_IDS["b"], lambda m: TelegramBadRequest(method=m, message="Bad Request: chat not found"))
    await broadcast(tg)

    kind = service.broadcast_kind(TEXT)
    rows = await deliveries(db, kind)
    assert rows[people["b"].id].status == "failed"
    assert rows[people["b"].id].error == "TelegramBadRequest"
    assert rows[people["a"].id].status == rows[people["c"].id].status == "sent"
    # одна попытка + один повтор
    assert len(tg.session.sent_messages(TG_IDS["b"])) == 2
    report = tg.session.last_text(ADMIN_ID)
    assert report.startswith(
        "Рассылка завершена: отправлено 2, заблокировали бота 0, ошибок 1, пропущено (уже получили) 0."
    )
    assert report.endswith("Повторите команду с тем же текстом: отправятся только неполученные.")

    # Telegram снова работает: повтор команды досылает только тому, у кого была ошибка
    tg.session.respond("sendMessage", lambda m: tg.session._default_result(tg.bot, m, m.model_dump(exclude_none=True)))
    tg.session.clear()
    await preview(tg)
    assert "Получателей: 1. Уже получили это сообщение: 2." in tg.session.last_text(ADMIN_ID)
    await confirm(tg)
    assert texts_to(tg, people["b"]) == [TEXT]
    assert tg.session.sent_messages(TG_IDS["a"]) == [] and tg.session.sent_messages(TG_IDS["c"]) == []
    rows = await deliveries(db, kind)
    assert len(rows) == 3  # строка обновилась, а не добавилась
    assert rows[people["b"].id].status == "sent"
    assert rows[people["b"].id].error is None
    assert tg.session.last_text(ADMIN_ID) == (
        "Рассылка завершена: отправлено 1, заблокировали бота 0, ошибок 0, пропущено (уже получили) 2."
    )


async def test_one_network_error_is_retried_once(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    fail_chat(tg, TG_IDS["a"], lambda m: TelegramNetworkError(method=m, message="timeout"), times=1)
    await broadcast(tg)
    assert len(tg.session.sent_messages(TG_IDS["a"])) == 2  # первая попытка упала, повтор дошёл
    assert (await deliveries(db))[people["a"].id].status == "sent"
    assert "ошибок 0" in tg.session.last_text(ADMIN_ID)


# --- A7: RetryAfter ---------------------------------------------------------------------------


async def test_retry_after_waits_and_retries(tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    people = await seed_audience(db)
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(service, "_sleep", fake_sleep)
    fail_chat(tg, TG_IDS["a"], lambda m: TelegramRetryAfter(method=m, message="Flood", retry_after=7), times=2)
    await broadcast(tg)

    assert waited == [7, 7]
    assert len(tg.session.sent_messages(TG_IDS["a"])) == 3
    assert (await deliveries(db))[people["a"].id].status == "sent"
    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка завершена: отправлено 3,")


async def test_retry_after_gives_up_after_three_waits(
    tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = await seed_audience(db)
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(service, "_sleep", fake_sleep)
    fail_chat(tg, TG_IDS["a"], lambda m: TelegramRetryAfter(method=m, message="Flood", retry_after=1))
    await broadcast(tg)

    assert waited == [1, 1, 1]
    assert len(tg.session.sent_messages(TG_IDS["a"])) == 4  # первая попытка и три повтора
    assert (await deliveries(db))[people["a"].id].status == "failed"
    assert "ошибок 1" in tg.session.last_text(ADMIN_ID)


async def test_pause_between_messages(tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed_audience(db)
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(service, "_sleep", fake_sleep)
    await service.send_broadcast(tg.bot, TEXT)  # пауза по умолчанию
    assert waited == [service.DEFAULT_PAUSE] * 3
    assert pytest.approx(1 / 25) == service.DEFAULT_PAUSE


# --- A8: устаревшие и повторные кнопки --------------------------------------------------------


async def test_stale_button_after_new_command(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await preview(tg, TEXT)
    old_button = find_button(tg.session.last_message(ADMIN_ID), "Отправить")["callback_data"]
    await preview(tg, OTHER_TEXT)
    tg.session.clear()

    await tg.click(old_button, user_id=ADMIN_ID)
    await handler.wait_idle()
    assert tg.session.last_text(ADMIN_ID) == "Рассылка устарела, отправьте команду ещё раз."
    for tid in TG_IDS.values():
        assert tg.session.sent_messages(tid) == []
    assert await deliveries(db) == {}

    # кнопка нового предпросмотра работает
    await tg.click(f"bc:send:{service.text_hash(OTHER_TEXT)}", user_id=ADMIN_ID)
    await handler.wait_idle()
    assert tg.session.sent_texts(TG_IDS["a"]) == [OTHER_TEXT]


async def test_stale_button_without_pending_data(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await tg.click(f"bc:send:{service.text_hash(TEXT)}", user_id=ADMIN_ID)  # бот перезапускался / данных нет
    assert tg.session.last_text(ADMIN_ID) == "Рассылка устарела, отправьте команду ещё раз."
    for tid in TG_IDS.values():
        assert tg.session.sent_messages(tid) == []
    assert await deliveries(db) == {}
    # блокировка не залипла: обычная рассылка после этого проходит
    await broadcast(tg)
    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка завершена: отправлено 3,")


async def test_double_send_click_does_not_duplicate(tg: TgHarness, db: AsyncSession) -> None:
    people = await seed_audience(db)
    await preview(tg)
    button = find_button(tg.session.last_message(ADMIN_ID), "Отправить")["callback_data"]

    await asyncio.gather(tg.click(button, user_id=ADMIN_ID), tg.click(button, user_id=ADMIN_ID))
    await handler.wait_idle()
    await tg.click(button, user_id=ADMIN_ID)  # и ещё раз после отчёта
    await handler.wait_idle()

    for key in ("a", "b", "c"):
        assert texts_to(tg, people[key]) == [TEXT], key
    texts = tg.session.sent_texts(ADMIN_ID)
    assert texts.count("Рассылка идёт… Пришлю отчёт, когда закончу.") == 1
    assert sum(text.startswith("Рассылка завершена") for text in texts) == 1
    assert "Рассылка уже идёт, дождитесь отчёта." in texts
    assert len(await deliveries(db)) == 3


async def test_second_broadcast_while_running_is_refused(
    tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = await seed_audience(db)
    gate = asyncio.Event()
    real_send = service.send_broadcast

    async def slow_send(*args: Any, **kwargs: Any) -> service.BroadcastResult:
        await gate.wait()
        return await real_send(*args, **kwargs)

    monkeypatch.setattr(handler, "send_broadcast", slow_send)
    await preview(tg, TEXT)
    await tg.click_button("Отправить", user_id=ADMIN_ID)
    await preview(tg, OTHER_TEXT)
    await tg.click_button("Отправить", user_id=ADMIN_ID)  # первая рассылка ещё идёт
    assert tg.session.last_text(ADMIN_ID) == "Рассылка уже идёт, дождитесь отчёта."
    assert tg.session.sent_messages(TG_IDS["a"]) == []

    gate.set()
    await handler.wait_idle()
    assert texts_to(tg, people["a"]) == [TEXT]  # вторая не стартовала
    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка завершена: отправлено 3,")


async def test_cancel_button(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await preview(tg)
    send_data = find_button(tg.session.last_message(ADMIN_ID), "Отправить")["callback_data"]
    await tg.click_button("Отмена", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Рассылка отменена, ничего не отправлено."
    assert tg.session.calls_of("editMessageReplyMarkup")
    await tg.click(send_data, user_id=ADMIN_ID)  # после отмены кнопка уже не работает
    assert tg.session.last_text(ADMIN_ID) == "Рассылка устарела, отправьте команду ещё раз."
    for tid in TG_IDS.values():
        assert tg.session.sent_messages(tid) == []


# --- A9: отказы -------------------------------------------------------------------------------


async def test_command_without_text_shows_usage(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await tg.send(COMMAND, user_id=ADMIN_ID)
    text = tg.session.last_text(ADMIN_ID)
    assert "/broadcast_practicum текст сообщения" in text
    assert "Отправить" in text  # объясняет, что отправка — после кнопки
    assert len(tg.session.sent_messages(ADMIN_ID)) == 1
    assert await tg.dp.storage.get_data(admin_key()) == {}


async def test_blank_text_is_rejected(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await tg.send(f"{COMMAND}   ", user_id=ADMIN_ID)
    texts = tg.session.sent_texts(ADMIN_ID)
    assert len(texts) == 1 and "reply_markup" not in tg.session.last_message(ADMIN_ID)
    assert "/broadcast_practicum" in texts[0]
    assert await deliveries(db) == {}


async def test_too_long_text_is_rejected_and_limit_is_inclusive(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await preview(tg, "я" * 4001)
    assert tg.session.last_text(ADMIN_ID) == (
        "Текст слишком длинный: 4001 знаков, максимум 4000. Сократите его и отправьте команду ещё раз."
    )
    assert len(tg.session.sent_messages(ADMIN_ID)) == 1
    assert await tg.dp.storage.get_data(admin_key()) == {}

    tg.session.clear()
    await preview(tg, "я" * 4000)
    assert tg.session.sent_texts(ADMIN_ID)[0] == "я" * 4000
    assert button_texts(tg.session.last_message(ADMIN_ID)) == ["Отправить 3", "Отмена"]


async def test_multiline_text_is_kept_as_is(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    text = "Первая строка\n\n  Вторая, с отступом\nhttps://example.test/a?b=1&c=2"
    await tg.send(f"{COMMAND} {text}", user_id=ADMIN_ID)
    assert tg.session.sent_texts(ADMIN_ID)[0] == text


# --- отчёт при сбое ---------------------------------------------------------------------------


async def test_report_is_sent_when_broadcast_crashes(
    tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_audience(db)

    async def crashing_send(bot: Any, text: str, *, result: service.BroadcastResult, **kwargs: Any) -> Any:
        result.sent, result.blocked, result.failed, result.skipped = 2, 1, 1, 4
        raise RuntimeError("boom")

    monkeypatch.setattr(handler, "send_broadcast", crashing_send)
    await broadcast(tg)

    report = tg.session.last_text(ADMIN_ID)
    assert report.startswith("Рассылка прервана из-за сбоя: отправлено 2, заблокировали бота 1, ошибок 1, пропущено")
    assert "(уже получили) 4." in report
    assert "Повторите команду с тем же текстом" in report
    assert not handler._lock.locked()  # блокировка снята, можно запускать снова


async def test_report_is_sent_when_task_is_cancelled(
    tg: TgHarness, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_audience(db)
    started = asyncio.Event()

    async def hanging_send(bot: Any, text: str, *, result: service.BroadcastResult, **kwargs: Any) -> Any:
        result.sent = 1
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(handler, "send_broadcast", hanging_send)
    await preview(tg)
    await tg.click_button("Отправить", user_id=ADMIN_ID)
    await started.wait()
    (task,) = handler._tasks
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка прервана из-за сбоя: отправлено 1,")
    assert not handler._lock.locked()


async def test_report_delivery_is_retried(tg: TgHarness, db: AsyncSession) -> None:
    await seed_audience(db)
    await preview(tg)
    # первые две попытки доставить отчёт не доходят (сеть), третья проходит
    left = {"n": 2}

    def flaky(method: Any) -> Any:
        payload = method.model_dump(exclude_none=True, warnings=False)
        if payload["chat_id"] == ADMIN_ID and payload["text"].startswith("Рассылка завершена") and left["n"] > 0:
            left["n"] -= 1
            raise TelegramNetworkError(method=method, message="timeout")
        return tg.session._default_result(tg.bot, method, payload)

    tg.session.respond("sendMessage", flaky)
    await confirm(tg)
    assert tg.session.last_text(ADMIN_ID).startswith("Рассылка завершена: отправлено 3,")
    assert left["n"] == 0


# --- A10: логи без ПД и текста ----------------------------------------------------------------


async def test_logs_have_no_text_names_or_telegram_ids(
    tg: TgHarness, db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    people = await seed_audience(db)
    tg.session.block_chat(TG_IDS["b"])
    fail_chat(tg, TG_IDS["c"], lambda m: TelegramBadRequest(method=m, message="Bad Request: chat not found"))
    secret_text = "СЕКРЕТНЫЙ-ТЕКСТ-РАССЫЛКИ https://calendar.yandex.ru/event/427535833220686"

    with caplog.at_level(logging.INFO):  # уровень сервиса по умолчанию (LOG_LEVEL=INFO)
        await broadcast(tg, secret_text)
        await preview(tg, "Другой текст с меткой МЕТКА-ТЕКСТА")

    log_text = "\n".join(f"{record.name} {record.getMessage()} {record.exc_text or ''}" for record in caplog.records)
    forbidden = [
        "СЕКРЕТНЫЙ-ТЕКСТ",
        "427535833220686",
        "МЕТКА-ТЕКСТА",
        "Ирина",
        "Лисицына",
        "Борис",
        "Орлов",
        "Вера",
        "irina_lisitsyna",
        "boris_orlov",
        *(str(tid) for tid in TG_IDS.values()),
    ]
    for fragment in forbidden:
        assert fragment not in log_text, fragment
    assert people["a"].id  # id в логах допустимы
    # а полезная строка итога есть: только счётчики и хеш
    digest = service.text_hash(secret_text)
    assert f"broadcast: hash={digest} recipients=3 sent=1 blocked=1 failed=1 skipped=0" in log_text


# --- сервис -----------------------------------------------------------------------------------


async def test_collect_audience(db: AsyncSession) -> None:
    people = await seed_audience(db)
    audience = await service.collect_audience(db, "practicum")
    assert [r.person_id for r in audience.recipients] == [people[k].id for k in ("a", "b", "c")]
    assert [r.telegram_id for r in audience.recipients] == list(TG_IDS.values())
    assert audience.without_telegram == 1  # отменённая запись и другое событие не считаются

    course = await service.collect_audience(db, "course")
    assert [r.person_id for r in course.recipients] == [people["course_only"].id]


def test_hash_and_kind() -> None:
    digest = service.text_hash(TEXT)
    assert len(digest) == 8 and digest == service.text_hash(TEXT)
    assert digest != service.text_hash(TEXT + " ")
    assert service.broadcast_kind(TEXT) == f"broadcast:{digest}"
    assert len(service.broadcast_kind("я" * 4000)) <= 64


def test_router_order_puts_broadcast_before_admin() -> None:
    from app.bot.setup import _routers

    names = [router.name for router in _routers()]
    assert names.index("broadcast") < names.index("admin")


def runbook_example() -> str:
    from pathlib import Path

    runbook = (Path(__file__).resolve().parent.parent / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    return runbook.split("```\n" + COMMAND + " ", 1)[1].split("\n```", 1)[0]


async def test_runbook_example_text_is_accepted(tg: TgHarness, db: AsyncSession) -> None:
    """Пример из docs/RUNBOOK.md проходит проверки и показывается в предпросмотре целиком."""
    block = runbook_example()
    assert "https://calendar.yandex.ru/event/427535833220686?" in block
    await seed_audience(db)
    await tg.send(f"{COMMAND} {block}", user_id=ADMIN_ID)
    assert tg.session.sent_texts(ADMIN_ID)[0] == block.strip()
    assert button_texts(tg.session.last_message(ADMIN_ID)) == ["Отправить 3", "Отмена"]
