"""Приём заявки с сайта (договор — `materials/FORMS.md`, раздел «Формат заявки»).

`import_site_registration(session, payload)` кладёт заявку в те же `people` и `registrations`,
что и бот. Владельцу ничего не отправляет: о заявках с сайта ему пишет сервис форм.
`bind_site_registration(session, tg_user, event_code)` привязывает заявку к чату при `/start prk_web|crs_web`.
В лог — только `site_id` и `registration_id`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from aiogram.types import User as TgUser
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Person, Registration
from app.services.events import get_event, sync_events_from_facts
from app.services.people import (
    find_by_email,
    find_by_username,
    find_unbound_site_people,
    get_by_telegram_id,
    merge_people,
    normalize_email,
    normalize_username,
)

log = logging.getLogger(__name__)

SITE_CHANNEL = "site"
# Форма на сайте → событие и начальный статус записи.
FORM_EVENTS: dict[str, tuple[str, str]] = {
    "practicum": ("practicum", "registered"),
    "course": ("course", "applied"),
}
_MAX_SOURCE = 255
_MAX_UTM_VALUE = 255


class SiteUtm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    content: str | None = None

    @field_validator("source", "medium", "campaign", "content", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()[:_MAX_UTM_VALUE]
            return value or None
        return value


class SiteRegistrationIn(BaseModel):
    """Заявка с сайта. Лишние поля игнорируются; пустые строки считаются отсутствующими."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    created_at: datetime | None = None
    source: str | None = None
    form: Literal["practicum", "course"]
    name: str | None = Field(default=None, max_length=200)
    contact_raw: str | None = None
    telegram_username: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    role: Literal["consultant", "opex", "transformation", "other"] | None = None
    tariff: Literal["early", "standard", "pro"] | None = None
    task: str | None = None
    invoice: bool = False
    page: str | None = None
    utm: SiteUtm | None = None

    @field_validator("name", "contact_raw", "telegram_username", "email", "role", "tariff", "task", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("invoice", mode="before")
    @classmethod
    def _invoice_none(cls, value: Any) -> Any:
        return False if value is None else value

    @property
    def utm_dict(self) -> dict[str, str | None] | None:
        if self.utm is None:
            return None
        data = self.utm.model_dump()
        return data if any(data.values()) else None

    @property
    def utm_content(self) -> str | None:
        return self.utm.content if self.utm is not None else None


@dataclass(frozen=True)
class ImportResult:
    status: Literal["created", "merged", "duplicate"]
    registration_id: int


def _empty(value: Any) -> bool:
    return value in (None, "", {})


async def _event(session: AsyncSession, code: str) -> Event:
    event = await get_event(session, code)
    if event is None:  # events создаются при старте; на случай пустой базы — синхронизировать здесь
        event = (await sync_events_from_facts(session))[code]
    return event


async def _find_or_create_person(session: AsyncSession, data: SiteRegistrationIn) -> Person:
    username = normalize_username(data.telegram_username)
    email = normalize_email(data.email)
    person = await find_by_username(session, username)
    if person is None:
        person = await find_by_email(session, email)
    if person is None:
        source = f"site:{data.utm_content}" if data.utm_content else "site"
        person = Person(
            telegram_username=username,
            name=data.name,
            email=email,
            role=data.role,
            source=source[:_MAX_SOURCE],
            utm=data.utm_dict,
        )
        session.add(person)
        await session.flush()
        return person
    # Найден — дописать пустые поля, заполненные не трогать.
    for field, value in (
        ("name", data.name),
        ("email", email),
        ("role", data.role),
        ("telegram_username", username),
        ("utm", data.utm_dict),
    ):
        if _empty(getattr(person, field)) and not _empty(value):
            setattr(person, field, value)
    await session.flush()
    return person


async def import_site_registration(session: AsyncSession, data: SiteRegistrationIn) -> ImportResult:
    """Принять заявку с сайта без дублей. Коммит — на вызывающей стороне.

    - `site_id` уже есть → `duplicate`;
    - человек — по `telegram_username`, затем по `email`; не найден → новый (`source` = `site:<utm.content>`);
    - запись человек × событие уже есть (например, из бота) → не дублировать: дописать пустые
      `site_id` и (для курса) `tariff`, `task`, `invoice` → `merged`; иначе новая запись → `created`.
    """
    existing = await session.scalar(select(Registration).where(Registration.site_id == data.id))
    if existing is not None:
        log.info("site import: duplicate site_id=%s registration_id=%s", data.id, existing.id)
        return ImportResult("duplicate", existing.id)

    event_code, status = FORM_EVENTS[data.form]
    event = await _event(session, event_code)
    person = await _find_or_create_person(session, data)
    is_course = data.form == "course"

    registration = await session.scalar(
        select(Registration).where(Registration.person_id == person.id, Registration.event_id == event.id)
    )
    if registration is None:
        registration = Registration(
            person_id=person.id,
            event_id=event.id,
            channel=SITE_CHANNEL,
            status=status,
            tariff=data.tariff if is_course else None,
            task=data.task if is_course else None,
            invoice=bool(data.invoice) if is_course else False,
            source=data.utm_content,
            utm=data.utm_dict,
            site_id=data.id,
        )
        session.add(registration)
        await session.flush()
        log.info("site import: created site_id=%s registration_id=%s", data.id, registration.id)
        return ImportResult("created", registration.id)

    if registration.site_id is None:
        registration.site_id = data.id
    if registration.status == "cancelled":  # новая заявка после отмены — снова в работе
        registration.status = status
    if is_course:
        if _empty(registration.tariff) and data.tariff:
            registration.tariff = data.tariff
        if _empty(registration.task) and data.task:
            registration.task = data.task
        registration.invoice = bool(registration.invoice or data.invoice)
    if _empty(registration.utm) and data.utm_dict:
        registration.utm = data.utm_dict
    await session.flush()
    log.info("site import: merged site_id=%s registration_id=%s", data.id, registration.id)
    return ImportResult("merged", registration.id)


async def _has_site_registration(session: AsyncSession, person_id: int, event_code: str) -> bool:
    found = await session.scalar(
        select(Registration.id)
        .join(Event, Event.id == Registration.event_id)
        .where(
            Registration.person_id == person_id,
            Event.code == event_code,
            (Registration.channel == SITE_CHANNEL) | Registration.site_id.is_not(None),
        )
        .limit(1)
    )
    return found is not None


async def bind_site_registration(session: AsyncSession, tg_user: TgUser | Any, event_code: str) -> bool:
    """`/start prk_web|crs_web`: привязать заявку с сайта на `event_code` к этому Telegram.

    Ищутся люди без `telegram_id` с username отправителя и заявкой с сайта на событие.
    Если у отправителя уже есть строка в `people` (заходил в бот) — они сливаются в неё
    (`merge_people`), иначе первой найденной проставляется `telegram_id`.
    Если заявка с сайта уже лежит у самого отправителя (пришла, когда он был в боте), — тоже `True`.
    Коммит — на вызывающей стороне.
    """
    person = await get_by_telegram_id(session, tg_user.id)
    candidates = await find_unbound_site_people(session, getattr(tg_user, "username", None), event_code)
    if candidates:
        if person is None:
            person, *candidates = candidates
            person.telegram_id = tg_user.id
            person.tg_first_name = person.tg_first_name or getattr(tg_user, "first_name", None) or None
            await session.flush()
        for site_person in candidates:
            site_person_id = site_person.id
            person = await merge_people(session, person, site_person)
            log.info("site bind: merged person=%s into person=%s", site_person_id, person.id)
        log.info("site bind: person=%s event=%s bound", person.id, event_code)
        return True
    return person is not None and await _has_site_registration(session, person.id, event_code)
