"""TASK-API-001: `/start prk_web` / `crs_web` — привязка заявки с сайта к чату."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import start as start_handlers
from app.models import Delivery, Event, Person, Question, Registration
from app.services.events import sync_events_from_facts
from app.services.people import merge_people
from app.services.site_import import SiteRegistrationIn, import_site_registration
from tests.helpers import ADMIN_ID, USER_ID, TgHarness
from tests.test_site_api import COURSE, EXAMPLE


async def setup_events(db: AsyncSession) -> dict[str, int]:
    events = await sync_events_from_facts(db)
    await db.commit()
    return {code: event.id for code, event in events.items()}


async def site_import(db: AsyncSession, body: dict) -> int:
    result = await import_site_registration(db, SiteRegistrationIn.model_validate(body))
    await db.commit()
    return result.registration_id


async def people(db: AsyncSession) -> list[Person]:
    stmt = select(Person).order_by(Person.id).execution_options(populate_existing=True)
    return list((await db.scalars(stmt)).all())


async def regs(db: AsyncSession) -> list[Registration]:
    stmt = select(Registration).order_by(Registration.id).execution_options(populate_existing=True)
    return list((await db.scalars(stmt)).all())


def assert_no_admin_messages(tg: TgHarness) -> None:
    assert tg.session.sent_messages(ADMIN_ID) == []


# --- A4: привязка ---------------------------------------------------------------------------------


async def test_prk_web_binds_site_application(tg: TgHarness, db: AsyncSession) -> None:
    await setup_events(db)
    reg_id = await site_import(db, EXAMPLE)

    await tg.send("/start prk_web", username="Irina_Ops", first_name="Ирина")

    texts = tg.session.sent_texts(USER_ID)
    assert texts[0].startswith("Здравствуйте!")
    assert texts[-1].startswith("Нашёл вашу заявку с сайта на практикум 31 октября, суббота, 15:00–17:00 МСК")
    assert "за день до начала пришлю сюда ссылку" in texts[-1]
    assert "Добавить в календарь" in str(tg.session.last_message(USER_ID))
    assert_no_admin_messages(tg)

    [person] = await people(db)
    assert person.telegram_id == USER_ID
    assert person.telegram_username == "irina_ops"
    assert (person.name, person.role, person.tg_first_name) == ("Ирина", "opex", "Ирина")
    assert person.source == "site:opex"  # метка первого входа — заявка с сайта
    [reg] = await regs(db)
    assert (reg.id, reg.person_id, reg.site_id, reg.channel) == (reg_id, person.id, EXAMPLE["id"], "site")

    # Повторный /start prk_web: заявка уже у этого человека — снова «нашёл», без дублей.
    await tg.send("/start prk_web", username="Irina_Ops")
    assert tg.session.last_text(USER_ID).startswith("Нашёл вашу заявку")
    assert len(await people(db)) == 1
    assert len(await regs(db)) == 1


async def test_prk_web_merges_with_existing_bot_person(tg: TgHarness, db: AsyncSession) -> None:
    events = await setup_events(db)
    # Человек заходил в бот раньше (без username), записался из бота и задал вопрос.
    bot_person = Person(telegram_id=USER_ID, tg_first_name="Ира", source="lp_opex_p1", phone="+79123456789")
    db.add(bot_person)
    await db.flush()
    bot_reg = Registration(person_id=bot_person.id, event_id=events["practicum"], channel="bot", source="lp_opex_p1")
    db.add_all([bot_reg, Question(person_id=bot_person.id, text="Вопрос")])
    await db.commit()
    # Потом оставил заявки на сайте: практикум (username) и курс (тот же username).
    await site_import(db, EXAMPLE)
    course_reg_id = await site_import(db, {**COURSE, "telegram_username": "irina_ops"})
    site_person = (await people(db))[-1]
    assert site_person.telegram_id is None
    db.add(Question(person_id=site_person.id, text="Вопрос с сайта"))
    await db.commit()

    await tg.send("/start prk_web", username="Irina_Ops")

    assert tg.session.last_text(USER_ID).startswith("Нашёл вашу заявку с сайта на практикум")
    assert_no_admin_messages(tg)
    [person] = await people(db)
    assert person.id == bot_person.id
    assert person.telegram_id == USER_ID
    assert person.telegram_username == "irina_ops"
    assert (person.name, person.role, person.email) == ("Ирина", "opex", "irina@example.ru")
    assert person.phone == "+79123456789"

    by_event = {reg.event_id: reg for reg in await regs(db)}
    assert len(by_event) == 2
    practicum = by_event[events["practicum"]]
    assert (practicum.id, practicum.channel, practicum.source, practicum.site_id) == (
        bot_reg.id,
        "bot",
        "lp_opex_p1",
        EXAMPLE["id"],
    )
    course = by_event[events["course"]]
    assert (course.id, course.person_id, course.tariff, course.invoice) == (course_reg_id, person.id, "pro", True)
    questions = (await db.scalars(select(Question).order_by(Question.id))).all()
    assert [q.person_id for q in questions] == [person.id, person.id]


async def test_crs_web_binds_course_application(tg: TgHarness, db: AsyncSession) -> None:
    await setup_events(db)
    await site_import(db, {**COURSE, "telegram_username": "irina_ops"})

    await tg.send("/start crs_web", username="irina_ops")

    assert tg.session.last_text(USER_ID).startswith("Нашёл вашу заявку с сайта на курс «")
    [person] = await people(db)
    assert person.telegram_id == USER_ID


# --- не нашлась — обычный сценарий ----------------------------------------------------------------


async def test_prk_web_without_username_starts_practicum(tg: TgHarness, db: AsyncSession, monkeypatch) -> None:
    await setup_events(db)
    await site_import(db, EXAMPLE)
    called: list[int] = []

    async def fake_start_practicum(message: Message, state: FSMContext) -> None:
        called.append(message.from_user.id)

    monkeypatch.setattr(start_handlers, "start_practicum", fake_start_practicum)
    await tg.send("/start prk_web")  # без username
    assert called == [USER_ID]
    assert not any("Нашёл" in text for text in tg.session.sent_texts(USER_ID))
    site_person = (await people(db))[0]
    assert site_person.telegram_id is None  # заявка не привязана

    await tg.send("/start prk_web", username="someone_else")
    assert called == [USER_ID, USER_ID]


async def test_other_form_not_bound(tg: TgHarness, db: AsyncSession, monkeypatch) -> None:
    await setup_events(db)
    await site_import(db, EXAMPLE)  # только практикум

    await tg.send("/start crs_web", username="Irina_Ops")
    assert tg.session.last_text(USER_ID).startswith("Запись на курс откроется скоро")
    assert len(await people(db)) == 2  # заявка на практикум не тронута
    assert all(p.telegram_id in (None, USER_ID) for p in await people(db))

    called: list[int] = []

    async def fake_start_practicum(message: Message, state: FSMContext) -> None:
        called.append(message.from_user.id)

    monkeypatch.setattr(start_handlers, "start_practicum", fake_start_practicum)
    await tg.send("/start prk_web", username="Irina_Ops")
    assert called == []  # практикум нашёлся и привязался
    assert len(await people(db)) == 1


async def test_crs_web_without_application_says_course_soon(tg: TgHarness, db: AsyncSession) -> None:
    await setup_events(db)
    await tg.send("/start crs_web", username="Irina_Ops")
    assert tg.session.last_text(USER_ID).startswith("Запись на курс откроется скоро")


# --- слияние: сервисная функция -------------------------------------------------------------------


async def test_merge_people_no_duplicates(db: AsyncSession) -> None:
    events = await setup_events(db)
    keep = Person(telegram_id=USER_ID, name="Ира")
    drop = Person(name="Ирина", email="irina@example.ru", source="site:opex")
    db.add_all([keep, drop])
    await db.flush()
    db.add_all(
        [
            Registration(person_id=keep.id, event_id=events["course"], channel="bot", status="applied"),
            Registration(
                person_id=drop.id,
                event_id=events["course"],
                channel="site",
                status="applied",
                tariff="early",
                task="Задача",
                invoice=True,
                site_id="S1",
            ),
            Delivery(person_id=keep.id, event_id=events["practicum"], kind="invite_24h", status="sent"),
            Delivery(person_id=drop.id, event_id=events["practicum"], kind="invite_24h", status="failed"),
            Delivery(person_id=drop.id, event_id=events["practicum"], kind="reminder_1h", status="failed"),
        ]
    )
    await db.commit()
    drop_id = drop.id

    merged = await merge_people(db, keep, drop)
    await db.commit()

    assert merged.id == keep.id
    assert await db.get(Person, drop_id) is None
    assert (merged.name, merged.email) == ("Ира", "irina@example.ru")
    [reg] = await regs(db)
    assert (reg.person_id, reg.channel, reg.tariff, reg.task, reg.invoice, reg.site_id) == (
        keep.id,
        "bot",
        "early",
        "Задача",
        True,
        "S1",
    )
    deliveries = (await db.scalars(select(Delivery).order_by(Delivery.id))).all()
    assert sorted((d.person_id, d.kind) for d in deliveries) == [(keep.id, "invite_24h"), (keep.id, "reminder_1h")]
    assert await db.scalar(select(func.count()).select_from(Event)) == len(events)
