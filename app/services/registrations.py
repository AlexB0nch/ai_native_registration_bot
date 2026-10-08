"""Записи на события: без дублей (один человек × событие = одна строка), подсчёт.

Коммит — на вызывающей стороне (`session_scope()`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Person, Registration, utcnow
from app.services.events import get_event, sync_events_from_facts

CANCELLED = "cancelled"
REGISTERED = "registered"


class UnknownEventError(LookupError):
    """События с таким `code` нет ни в базе, ни в facts.yaml."""


async def get_or_sync_event(session: AsyncSession, code: str) -> Event:
    """Событие по коду; если таблица `events` ещё не синхронизирована — синхронизировать."""
    event = await get_event(session, code)
    if event is None:
        await sync_events_from_facts(session)
        event = await get_event(session, code)
    if event is None:
        raise UnknownEventError(code)
    return event


async def get_registration(session: AsyncSession, person_id: int, event_id: int) -> Registration | None:
    return await session.scalar(
        select(Registration).where(Registration.person_id == person_id, Registration.event_id == event_id)
    )


async def get_active_registration(session: AsyncSession, person: Person, event_code: str) -> Registration | None:
    """Запись человека на событие, если она есть и не отменена."""
    return await session.scalar(
        select(Registration)
        .join(Event, Registration.event_id == Event.id)
        .where(
            Registration.person_id == person.id,
            Event.code == event_code,
            Registration.status != CANCELLED,
        )
    )


async def register_for_event(
    session: AsyncSession,
    person: Person,
    event_code: str,
    channel: str,
    source: str | None,
    utm: dict[str, Any] | None = None,
) -> tuple[Registration, bool]:
    """Записать человека на событие. Возвращает `(запись, created)`.

    Повторный вызов не создаёт вторую строку: существующая запись остаётся, `created_at`,
    `source` и `channel` не меняются; отменённая запись снова становится `registered`,
    `utm` дописывается, если его не было. `created=True` — только при вставке новой строки.
    """
    event = await get_or_sync_event(session, event_code)
    if person.id is None:
        await session.flush()

    registration = await get_registration(session, person.id, event.id)
    if registration is None:
        registration = Registration(
            person_id=person.id,
            event_id=event.id,
            channel=channel,
            status=REGISTERED,
            source=source,
            utm=utm,
        )
        try:
            async with session.begin_nested():
                session.add(registration)
        except IntegrityError:
            # Параллельный запрос успел вставить ту же пару (person, event) — берём её.
            registration = await get_registration(session, person.id, event.id)
            if registration is None:
                raise
        else:
            return registration, True

    if registration.status == CANCELLED:
        registration.status = REGISTERED
    if utm and not registration.utm:
        registration.utm = utm
    registration.updated_at = utcnow()
    await session.flush()
    return registration, False


async def count_registrations(session: AsyncSession, event_code: str) -> int:
    """Сколько человек записано на событие (без отменённых)."""
    total = await session.scalar(
        select(func.count(Registration.id))
        .join(Event, Registration.event_id == Event.id)
        .where(Event.code == event_code, Registration.status != CANCELLED)
    )
    return int(total or 0)
