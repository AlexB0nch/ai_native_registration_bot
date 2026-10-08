"""TASK-BOT-002: команды владельца /stats, /export, /link, /set_link."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Person, Question, Registration, utcnow
from app.services.events import get_event, sync_events_from_facts
from app.services.stats import collect_stats, render_stats
from tests.helpers import ADMIN_ID, OTHER_USER_ID, TgHarness

FALLBACK = "Выберите действие в меню или нажмите «Задать вопрос»."


async def seed_events(db: AsyncSession) -> dict[str, Event]:
    events = await sync_events_from_facts(db)
    await db.commit()
    return events


async def add_registration(
    db: AsyncSession,
    event: Event,
    n: int,
    *,
    created_at: datetime,
    source: str | None = None,
    channel: str = "bot",
    status: str = "registered",
    tariff: str | None = None,
) -> Registration:
    person = Person(telegram_id=200000 + n, tg_first_name=f"Участник {n}")
    db.add(person)
    await db.flush()
    registration = Registration(
        person_id=person.id,
        event_id=event.id,
        channel=channel,
        status=status,
        source=source,
        tariff=tariff,
        created_at=created_at,
    )
    db.add(registration)
    await db.flush()
    return registration


async def practicum_links(db: AsyncSession) -> tuple[str | None, str | None]:
    db.expire_all()
    event = await get_event(db, "practicum")
    assert event is not None
    return event.join_url, event.recording_url


# --- A1: не-админ -----------------------------------------------------------------------------


async def test_admin_commands_ignored_for_non_admin(tg: TgHarness, db: AsyncSession) -> None:
    await seed_events(db)
    for command in ("/stats", "/export", "/link https://telemost.yandex.ru/j/1", "/set_link recording https://x.ru"):
        tg.session.clear()
        await tg.send(command, user_id=OTHER_USER_ID)
        assert tg.session.sent_texts(OTHER_USER_ID) == [FALLBACK], command
        assert tg.session.calls_of("sendDocument") == []
        assert tg.session.sent_messages(ADMIN_ID) == []  # команда не ушла владельцу как вопрос
    assert await practicum_links(db) == (None, None)


# --- /stats -----------------------------------------------------------------------------------


async def test_stats_empty_database(tg: TgHarness) -> None:
    await tg.send("/stats", user_id=ADMIN_ID)
    text = tg.session.last_text(ADMIN_ID)
    assert text.startswith("Статистика записей")
    assert "Практикум: 0\n" in text
    assert "По каналам: бот — 0, сайт — 0" in text
    assert "Заявки на курс: пока нет" in text
    assert "Вопросы без ответа: 0" in text


async def test_collect_stats(db: AsyncSession) -> None:
    events = await seed_events(db)
    practicum, course = events["practicum"], events["course"]
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)  # 15:00 МСК
    # 7 октября 22:30 UTC = 8 октября 01:30 МСК
    await add_registration(db, practicum, 1, created_at=datetime(2026, 10, 7, 22, 30, tzinfo=UTC), source="lp_a")
    await add_registration(db, practicum, 2, created_at=datetime(2026, 10, 7, 20, 0, tzinfo=UTC), source="lp_a")
    await add_registration(db, practicum, 3, created_at=now - timedelta(hours=1), source="", channel="site")
    await add_registration(db, practicum, 4, created_at=now - timedelta(days=30), source=None)
    await add_registration(db, practicum, 5, created_at=now, source="lp_b", status="cancelled")
    await add_registration(db, course, 6, created_at=now, tariff="early", status="applied")
    await add_registration(db, course, 7, created_at=now, tariff="early", status="paid")
    await add_registration(db, course, 8, created_at=now, tariff=None, status="applied")
    person = await db.get(Person, 1)
    db.add(Question(person_id=person.id, text="?"))
    db.add(Question(person_id=person.id, text="?", answer_text="!", answered_at=now))
    await db.commit()

    stats = await collect_stats(db, now)
    assert stats.practicum_total == 4
    assert stats.practicum_cancelled == 1
    assert len(stats.by_day) == 14
    assert stats.by_day[-1] == (datetime(2026, 10, 8).date(), 2)
    assert stats.by_day[-2] == (datetime(2026, 10, 7).date(), 1)
    assert stats.by_day[0][0] == datetime(2026, 9, 25).date()
    assert stats.by_source == [(None, 2), ("lp_a", 2)]
    assert stats.by_channel == {"bot": 3, "site": 1}
    assert stats.course_total == 3
    assert stats.course_by_tariff == [("early", 2), (None, 1)]
    assert stats.course_by_status == [("applied", 2), ("paid", 1)]
    assert stats.unanswered == 1

    text = render_stats(stats)
    assert "Практикум: 4 (и ещё 1 отменили)" in text
    assert "08.10 — 2\n" in text
    assert "07.10 — 1\n" in text
    assert "без метки — 2\nlp_a — 2" in text
    assert "По каналам: бот — 3, сайт — 1" in text
    assert (
        "Заявки на курс: 3\nпо тарифам: Early Bird — 2, без тарифа — 1\nпо статусам: заявка — 2, оплачена — 1" in text
    )
    assert "Вопросы без ответа: 1" in text


async def test_stats_top_sources(db: AsyncSession) -> None:
    events = await seed_events(db)
    now = utcnow()
    for n in range(17):
        await add_registration(db, events["practicum"], n, created_at=now, source=f"src_{n:02d}")
    await db.commit()
    stats = await collect_stats(db, now)
    assert len(stats.by_source) == 15
    assert stats.other_sources == 2
    assert "другие — 2" in render_stats(stats)


async def test_stats_command_counts_recent_registration(tg: TgHarness, db: AsyncSession) -> None:
    events = await seed_events(db)
    await add_registration(db, events["practicum"], 1, created_at=utcnow(), source="prk")
    await db.commit()
    await tg.send("/stats", user_id=ADMIN_ID)
    text = tg.session.last_text(ADMIN_ID)
    assert "Практикум: 1\n" in text
    assert "prk — 1" in text


# --- /link, /set_link (A3) --------------------------------------------------------------------


async def test_link_saves_https_url(tg: TgHarness, db: AsyncSession) -> None:
    await seed_events(db)
    await tg.send("/link https://telemost.yandex.ru/j/123", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Ссылка сохранена: https://telemost.yandex.ru/j/123"
    assert await practicum_links(db) == ("https://telemost.yandex.ru/j/123", None)


async def test_link_rejects_non_https(tg: TgHarness, db: AsyncSession) -> None:
    await seed_events(db)
    for bad in ("/link http://x", "/link telemost.yandex.ru/j/1", "/link https://", "/link https://a b"):
        await tg.send(bad, user_id=ADMIN_ID)
        assert tg.session.last_text(ADMIN_ID).startswith("Нужна ссылка, начинающаяся с https://"), bad
    assert await practicum_links(db) == (None, None)


async def test_set_link_practicum_and_recording(tg: TgHarness, db: AsyncSession) -> None:
    await seed_events(db)
    await tg.send("/set_link practicum https://telemost.yandex.ru/j/1", user_id=ADMIN_ID)
    await tg.send("/set_link recording https://disk.yandex.ru/i/rec", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID) == "Ссылка сохранена: https://disk.yandex.ru/i/rec"
    assert await practicum_links(db) == ("https://telemost.yandex.ru/j/1", "https://disk.yandex.ru/i/rec")

    await tg.send("/set_link recording http://disk.yandex.ru/i/rec", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID).startswith("Нужна ссылка")
    await tg.send("/set_link webinar https://x.ru", user_id=ADMIN_ID)
    assert tg.session.last_text(ADMIN_ID).startswith("Формат:")
    assert await practicum_links(db) == ("https://telemost.yandex.ru/j/1", "https://disk.yandex.ru/i/rec")


async def test_link_without_argument_shows_current(tg: TgHarness, db: AsyncSession) -> None:
    await seed_events(db)
    await tg.send("/link", user_id=ADMIN_ID)
    text = tg.session.last_text(ADMIN_ID)
    assert "Трансляция: не задана" in text
    assert "Запись: не задана" in text
    await tg.send("/link https://telemost.yandex.ru/j/9", user_id=ADMIN_ID)
    await tg.send("/set_link", user_id=ADMIN_ID)
    assert "Трансляция: https://telemost.yandex.ru/j/9" in tg.session.last_text(ADMIN_ID)


async def test_link_creates_practicum_event_if_missing(tg: TgHarness, db: AsyncSession) -> None:
    await tg.send("/link https://telemost.yandex.ru/j/5", user_id=ADMIN_ID)
    assert await practicum_links(db) == ("https://telemost.yandex.ru/j/5", None)
