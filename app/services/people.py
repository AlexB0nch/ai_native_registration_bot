"""Люди: поиск и создание по Telegram, поиск по username/почте, слияние с заявкой с сайта."""

from __future__ import annotations

from typing import Any

from aiogram.types import User as TgUser
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Delivery, Event, Person, Question, Registration


def normalize_username(username: str | None) -> str | None:
    """`@Irina_Ops` → `irina_ops`; пустое → None."""
    if not username:
        return None
    value = username.strip().lstrip("@").lower()
    return value or None


def normalize_email(email: str | None) -> str | None:
    """`Irina@Example.RU ` → `irina@example.ru`; пустое → None."""
    if not email:
        return None
    value = email.strip().lower()
    return value or None


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Person | None:
    return await session.scalar(select(Person).where(Person.telegram_id == telegram_id))


async def get_or_create_by_telegram(session: AsyncSession, tg_user: TgUser | Any, source: str | None) -> Person:
    """Найти человека по `telegram_id` или создать.

    `source` записывается только при создании (метка первого входа); `source` и `created_at`
    потом не меняются. `telegram_username` (в нижнем регистре) и `tg_first_name` обновляются
    при каждом обращении. Коммит — на вызывающей стороне (`session_scope()`).
    """
    person = await get_by_telegram_id(session, tg_user.id)
    username = normalize_username(getattr(tg_user, "username", None))
    first_name = getattr(tg_user, "first_name", None) or None
    if person is None:
        person = Person(
            telegram_id=tg_user.id,
            telegram_username=username,
            tg_first_name=first_name,
            source=source,
        )
        session.add(person)
        await session.flush()
        return person
    if person.telegram_username != username:
        person.telegram_username = username
    if first_name and person.tg_first_name != first_name:
        person.tg_first_name = first_name
    await session.flush()
    return person


async def find_by_username(session: AsyncSession, username: str | None) -> Person | None:
    """Человек по `telegram_username` (без `@`, регистр не важен).

    Если таких несколько (устаревший username у одного из них), сначала — привязанный
    к Telegram, затем самый ранний.
    """
    value = normalize_username(username)
    if value is None:
        return None
    stmt = (
        select(Person)
        .where(Person.telegram_username == value)
        .order_by(Person.telegram_id.is_(None), Person.id)
        .limit(1)
    )
    return await session.scalar(stmt)


async def find_by_email(session: AsyncSession, email: str | None) -> Person | None:
    """Человек по почте (в нижнем регистре); сначала привязанный к Telegram, затем самый ранний."""
    value = normalize_email(email)
    if value is None:
        return None
    stmt = select(Person).where(Person.email == value).order_by(Person.telegram_id.is_(None), Person.id).limit(1)
    return await session.scalar(stmt)


async def find_unbound_site_people(session: AsyncSession, username: str | None, event_code: str) -> list[Person]:
    """Люди без `telegram_id` с этим username и заявкой с сайта на событие `event_code`.

    Заявка с сайта — запись с `channel=site` или с заполненным `site_id`.
    """
    value = normalize_username(username)
    if value is None:
        return []
    site_people = (
        select(Registration.person_id)
        .join(Event, Event.id == Registration.event_id)
        .where(Event.code == event_code, (Registration.channel == "site") | Registration.site_id.is_not(None))
    )
    stmt = (
        select(Person)
        .where(Person.telegram_id.is_(None), Person.telegram_username == value, Person.id.in_(site_people))
        .order_by(Person.id)
    )
    return list((await session.scalars(stmt)).all())


# Поля человека, которые при слиянии дописываются, если у оставляемой строки они пустые.
_MERGE_PERSON_FIELDS = ("telegram_username", "tg_first_name", "name", "email", "phone", "role", "consent_at", "utm")
# Поля записи на событие, которые при слиянии дописываются, если пусты.
_MERGE_REGISTRATION_FIELDS = ("tariff", "task", "source", "utm")


async def merge_people(session: AsyncSession, keep: Person, drop: Person) -> Person:
    """Слить `drop` в `keep` и удалить `drop`. Коммит — на вызывающей стороне.

    - записи на события переносятся; если у обоих есть запись на одно событие, остаётся запись
      `keep`, а в неё дописываются пустые `site_id`, `tariff`, `task`, `source`, `utm`
      (`invoice` — если отмечен хотя бы в одной);
    - вопросы и рассылки переносятся (рассылка, которая уже есть у `keep`, не дублируется);
    - пустые поля `keep` дописываются из `drop`, заполненные не меняются; `source` и `created_at` —
      от более ранней строки (метка первого входа);
    - `telegram_id` переносится, только если у `keep` его нет.
    """
    if keep.id == drop.id:
        return keep
    keep_regs = {
        reg.event_id: reg
        for reg in (await session.scalars(select(Registration).where(Registration.person_id == keep.id))).all()
    }
    drop_regs = (await session.scalars(select(Registration).where(Registration.person_id == drop.id))).all()
    for reg in drop_regs:
        target = keep_regs.get(reg.event_id)
        if target is None:
            reg.person_id = keep.id
            continue
        site_id = reg.site_id
        values = {field: getattr(reg, field) for field in _MERGE_REGISTRATION_FIELDS}
        invoice = reg.invoice
        await session.delete(reg)
        await session.flush()  # освободить unique(site_id) до записи в target
        if target.site_id is None and site_id is not None:
            target.site_id = site_id
        for field, value in values.items():
            if getattr(target, field) in (None, "", {}) and value not in (None, "", {}):
                setattr(target, field, value)
        target.invoice = bool(target.invoice or invoice)
    await session.flush()

    await session.execute(
        update(Question).where(Question.person_id == drop.id).values(person_id=keep.id),
        execution_options={"synchronize_session": False},
    )
    keep_kinds = {
        (event_id, kind)
        for event_id, kind in (
            await session.execute(select(Delivery.event_id, Delivery.kind).where(Delivery.person_id == keep.id))
        ).all()
    }
    for delivery in (await session.scalars(select(Delivery).where(Delivery.person_id == drop.id))).all():
        if (delivery.event_id, delivery.kind) in keep_kinds:
            await session.delete(delivery)
        else:
            delivery.person_id = keep.id

    for field in _MERGE_PERSON_FIELDS:
        value = getattr(drop, field)
        if getattr(keep, field) in (None, "", {}) and value not in (None, "", {}):
            setattr(keep, field, value)
    if drop.created_at is not None and keep.created_at is not None and drop.created_at < keep.created_at:
        if drop.source:
            keep.source = drop.source
        keep.created_at = drop.created_at
    elif not keep.source and drop.source:
        keep.source = drop.source
    telegram_id = drop.telegram_id if keep.telegram_id is None else None

    drop_id = drop.id
    await session.flush()
    await session.execute(
        delete(Person).where(Person.id == drop_id), execution_options={"synchronize_session": False}
    )
    session.expunge(drop)
    if telegram_id is not None:
        keep.telegram_id = telegram_id
    await session.flush()
    await session.refresh(keep)
    return keep
