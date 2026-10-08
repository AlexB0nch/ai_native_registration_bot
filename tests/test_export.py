"""TASK-BOT-002: выгрузка записей в CSV (/export)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from aiogram.methods import SendDocument
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Registration
from app.services.events import sync_events_from_facts
from app.services.export import COLUMNS, escape_cell, export_filename, rows_to_csv
from tests.helpers import ADMIN_ID, OTHER_USER_ID, TgHarness

BOM = "﻿".encode()


def parse(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=";"))


def test_escape_cell() -> None:
    assert escape_cell("=1+1") == "'=1+1"
    assert escape_cell("+79123456789") == "'+79123456789"
    assert escape_cell("-2") == "'-2"
    assert escape_cell("@cmd") == "'@cmd"
    assert escape_cell("\t=1") == "'\t=1"
    assert escape_cell("Ирина") == "Ирина"
    assert escape_cell("a=b") == "a=b"
    assert escape_cell(None) == ""
    assert escape_cell(123) == "123"


def test_rows_to_csv_format() -> None:
    """A2: BOM, `;`, CRLF, кириллица, `=1+1` экранирована."""
    data = rows_to_csv([{"name": "Ирина Смирнова", "task": "=1+1", "source": "a;b", "role": 'say "hi"'}])
    assert data.startswith(BOM)
    text = data.decode("utf-8-sig")
    assert text.startswith("created_at_msk;event;channel;status;name;")
    assert text.endswith("\r\n")
    assert text.count("\r\n") == 2
    assert "Ирина Смирнова" in text
    rows = parse(data)
    assert rows[0] == list(COLUMNS)
    row = dict(zip(COLUMNS, rows[1], strict=True))
    assert row["name"] == "Ирина Смирнова"
    assert row["task"] == "'=1+1"
    assert row["source"] == "a;b"
    assert row["role"] == 'say "hi"'


def test_export_filename_uses_moscow_date() -> None:
    assert export_filename(datetime(2026, 10, 8, 20, 59, tzinfo=UTC)) == "registrations_2026-10-08.csv"
    assert export_filename(datetime(2026, 10, 8, 21, 0, tzinfo=UTC)) == "registrations_2026-10-09.csv"


async def test_export_command(tg: TgHarness, db: AsyncSession) -> None:
    events = await sync_events_from_facts(db)
    person = Person(
        telegram_id=555,
        telegram_username="irina_ops",
        tg_first_name="Ира",
        name="Ирина Смирнова",
        email="irina@example.com",
        phone="+79123456789",
        role="opex",
        consent_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
        source="lp_opex",
    )
    db.add(person)
    await db.flush()
    db.add(
        Registration(
            person_id=person.id,
            event_id=events["practicum"].id,
            channel="site",
            source="prk_web",
            utm={"utm_source": "tg", "utm_campaign": '=HYPERLINK("x")'},
            task="Собрать отчёт по диагностике",
            created_at=datetime(2026, 10, 7, 21, 30, tzinfo=UTC),
        )
    )
    db.add(
        Registration(
            person_id=person.id,
            event_id=events["course"].id,
            channel="bot",
            status="applied",
            tariff="early",
            invoice=True,
            created_at=datetime(2026, 10, 8, 9, 0, tzinfo=UTC),
        )
    )
    await db.commit()

    await tg.send("/export", user_id=ADMIN_ID)
    calls = tg.session.calls_of("sendDocument")
    assert len(calls) == 1
    assert calls[0]["chat_id"] == ADMIN_ID
    assert calls[0]["caption"].startswith("Записей: 2.")
    method = next(m for m in tg.session.methods if isinstance(m, SendDocument))
    document = method.document
    assert isinstance(document, BufferedInputFile)
    assert document.filename.startswith("registrations_") and document.filename.endswith(".csv")
    assert document.data.startswith(BOM)

    rows = parse(document.data)
    assert rows[0] == list(COLUMNS)
    practicum, course = (dict(zip(COLUMNS, row, strict=True)) for row in rows[1:])
    assert practicum == {
        "created_at_msk": "2026-10-08 00:30",
        "event": "practicum",
        "channel": "site",
        "status": "registered",
        "name": "Ирина Смирнова",
        "email": "irina@example.com",
        "phone": "'+79123456789",
        "telegram_username": "irina_ops",
        "telegram_id": "555",
        "role": "opex",
        "source": "prk_web",
        "utm_source": "tg",
        "utm_medium": "",
        "utm_campaign": '\'=HYPERLINK("x")',
        "utm_content": "",
        "tariff": "",
        "task": "Собрать отчёт по диагностике",
        "invoice": "нет",
        "consent_at_msk": "2026-10-07 12:00",
    }
    assert course["event"] == "course"
    assert course["tariff"] == "early"
    assert course["status"] == "applied"
    assert course["invoice"] == "да"
    assert course["created_at_msk"] == "2026-10-08 12:00"


async def test_export_empty_and_non_admin(tg: TgHarness) -> None:
    await tg.send("/export", user_id=OTHER_USER_ID)
    assert tg.session.calls_of("sendDocument") == []
    await tg.send("/export", user_id=ADMIN_ID)
    method = next(m for m in tg.session.methods if isinstance(m, SendDocument))
    assert parse(method.document.data) == [list(COLUMNS)]
