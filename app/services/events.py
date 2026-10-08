"""Синхронизация таблицы `events` с `config/facts.yaml` (при старте сервиса)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.facts import event_start_utc, get_facts
from app.models import Event
from app.texts import t

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventSpec:
    code: str
    title: str
    starts_at: datetime | None
    ends_at: datetime | None


def events_from_facts() -> list[EventSpec]:
    facts = get_facts()
    practicum, course = facts.practicum, facts.course
    sessions = course.sessions
    specs = [
        EventSpec(
            "practicum",
            practicum.title,
            event_start_utc(practicum.date, practicum.start),
            event_start_utc(practicum.date, practicum.end),
        ),
        EventSpec(
            "course",
            course.title,
            event_start_utc(sessions[0], course.start),
            event_start_utc(sessions[-1], course.end),
        ),
    ]
    for n, day in enumerate(sessions, start=1):
        specs.append(
            EventSpec(
                f"course_{n}",
                t("events.course_session", n=n),
                event_start_utc(day, course.start),
                event_start_utc(day, course.end),
            )
        )
    demo = course.demo_day
    specs.append(
        EventSpec(
            "demo_day",
            t("events.demo_day"),
            event_start_utc(demo.date, demo.start),
            event_start_utc(demo.date, demo.end),
        )
    )
    specs.append(EventSpec("waitlist", t("events.waitlist"), None, None))
    return specs


async def sync_events_from_facts(session: AsyncSession) -> dict[str, Event]:
    """Создать или обновить события по `code`. `join_url` и `recording_url` не трогаются.

    Возвращает события по коду. Коммит — на вызывающей стороне.
    """
    existing = {event.code: event for event in (await session.scalars(select(Event))).all()}
    result: dict[str, Event] = {}
    for spec in events_from_facts():
        event = existing.get(spec.code)
        if event is None:
            event = Event(code=spec.code, title=spec.title, starts_at=spec.starts_at, ends_at=spec.ends_at)
            session.add(event)
        else:
            event.title = spec.title
            event.starts_at = spec.starts_at
            event.ends_at = spec.ends_at
        result[spec.code] = event
    await session.flush()
    log.info("events synced: %s", ", ".join(sorted(result)))
    return result


async def get_event(session: AsyncSession, code: str) -> Event | None:
    return await session.scalar(select(Event).where(Event.code == code))
