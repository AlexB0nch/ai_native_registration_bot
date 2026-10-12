"""Выгрузка записей в CSV для Excel (`/export`).

Формат: UTF-8 с BOM (Excel тогда читает кириллицу), разделитель `;`, строки через CRLF,
кавычки по правилам модуля `csv`. Время — по Москве. Значения, которые Excel принял бы
за формулу (начинаются с `= + - @`, а также с табуляции или перевода строки), получают
префикс `'` — защита от CSV-инъекций.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.facts import to_local
from app.models import Event, Person, Registration

COLUMNS = (
    "created_at_msk",
    "event",
    "channel",
    "status",
    "name",
    "email",
    "phone",
    "telegram_username",
    "telegram_id",
    "role",
    "source",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "tariff",
    "task",
    "invoice",
    "consent_at_msk",
)
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_cell(value: Any) -> str:
    """Значение ячейки строкой; потенциальная формула → `'` + значение."""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def format_msk(moment: datetime | None) -> str:
    return to_local(moment).strftime("%Y-%m-%d %H:%M") if moment else ""


def export_filename(now: datetime) -> str:
    """`registrations_YYYY-MM-DD.csv`, дата по Москве."""
    return f"registrations_{to_local(now):%Y-%m-%d}.csv"


def registration_row(registration: Registration, person: Person, event: Event) -> dict[str, Any]:
    utm = registration.utm or person.utm or {}
    return {
        "created_at_msk": format_msk(registration.created_at),
        "event": event.code,
        "channel": registration.channel,
        "status": registration.status,
        "name": person.name or person.tg_first_name,
        "email": person.email,
        "phone": person.phone,
        "telegram_username": person.telegram_username,
        "telegram_id": person.telegram_id,
        "role": person.role,
        "source": registration.source,
        **{key: utm.get(key) for key in UTM_KEYS},
        "tariff": registration.tariff,
        "task": registration.task,
        "invoice": "да" if registration.invoice else "нет",
        "consent_at_msk": format_msk(person.consent_at),
    }


def rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([escape_cell(row.get(column)) for column in COLUMNS])
    return buffer.getvalue().encode("utf-8-sig")


async def registration_rows(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(Registration, Person, Event)
        .join(Person, Person.id == Registration.person_id)
        .join(Event, Event.id == Registration.event_id)
        .order_by(Registration.created_at, Registration.id)
    )
    return [registration_row(*row) for row in (await session.execute(stmt)).all()]


async def build_registrations_csv(session: AsyncSession) -> tuple[bytes, int]:
    """CSV со всеми записями и число строк данных."""
    rows = await registration_rows(session)
    return rows_to_csv(rows), len(rows)
