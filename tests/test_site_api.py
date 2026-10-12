"""TASK-API-001: `POST /api/site-registrations` — приём заявок с сайта."""

from __future__ import annotations

import copy
import logging
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Person, Registration
from tests.helpers import SITE_SECRET, USER_ID, TgHarness

URL = "/api/site-registrations"

# Пример из materials/FORMS.md, раздел «Формат заявки».
EXAMPLE: dict[str, Any] = {
    "id": "01JABCDEF0000000000000000",
    "created_at": "2026-10-20T12:34:56+03:00",
    "source": "site",
    "form": "practicum",
    "name": "Ирина",
    "contact_raw": "@irina_ops",
    "telegram_username": "irina_ops",
    "email": None,
    "role": "opex",
    "tariff": None,
    "task": None,
    "invoice": False,
    "page": "https://alexshein.com/ai-native?utm_source=tgads&utm_content=opex",
    "utm": {"source": "tgads", "medium": None, "campaign": None, "content": "opex"},
}

COURSE: dict[str, Any] = {
    **copy.deepcopy(EXAMPLE),
    "id": "01JCOURSE00000000000000000",
    "form": "course",
    "contact_raw": "irina@example.ru",
    "telegram_username": None,
    "email": "Irina@Example.RU",
    "tariff": "pro",
    "task": "Прототип трекера инициатив",
    "invoice": True,
}


def headers(secret: str | None = SITE_SECRET) -> dict[str, str]:
    return {"X-Webhook-Secret": secret} if secret is not None else {}


async def post(client: httpx.AsyncClient, body: Any, secret: str | None = SITE_SECRET) -> httpx.Response:
    return await client.post(URL, json=body, headers=headers(secret))


async def count(db: AsyncSession, model: type) -> int:
    return await db.scalar(select(func.count()).select_from(model))


async def registrations(db: AsyncSession) -> list[Registration]:
    db.expire_all()
    return list((await db.scalars(select(Registration).order_by(Registration.id))).all())


async def event_id(db: AsyncSession, code: str) -> int:
    return await db.scalar(select(Event.id).where(Event.code == code))


# --- A1: секрет ----------------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [None, "", "wrong-secret"])
async def test_rejects_missing_or_wrong_secret(client: httpx.AsyncClient, db: AsyncSession, secret: str | None) -> None:
    response = await post(client, EXAMPLE, secret)
    assert response.status_code == 401
    assert await count(db, Registration) == 0


async def test_secret_checked_before_body(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 401


async def test_503_when_secret_not_configured(client: httpx.AsyncClient, db: AsyncSession, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SITE_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    response = await post(client, EXAMPLE)
    assert response.status_code == 503
    assert await count(db, Registration) == 0


# --- A2: пример из FORMS.md, идемпотентность ------------------------------------------------------


async def test_example_created_then_duplicate(client: httpx.AsyncClient, db: AsyncSession) -> None:
    response = await post(client, EXAMPLE)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"

    [reg] = await registrations(db)
    assert body["registration_id"] == reg.id
    assert reg.event_id == await event_id(db, "practicum")
    assert (reg.channel, reg.status, reg.site_id, reg.source) == ("site", "registered", EXAMPLE["id"], "opex")
    assert reg.utm == EXAMPLE["utm"]
    assert (reg.tariff, reg.task, reg.invoice) == (None, None, False)

    person = await db.get(Person, reg.person_id)
    assert person.telegram_id is None
    assert person.telegram_username == "irina_ops"
    assert (person.name, person.role, person.email) == ("Ирина", "opex", None)
    assert person.source == "site:opex"
    assert person.utm == EXAMPLE["utm"]

    again = await post(client, EXAMPLE)
    assert again.status_code == 200
    assert again.json() == {"status": "duplicate", "registration_id": reg.id}
    assert await count(db, Registration) == 1
    assert await count(db, Person) == 1


async def test_course_application(client: httpx.AsyncClient, db: AsyncSession) -> None:
    response = await post(client, COURSE)
    assert response.json()["status"] == "created"
    [reg] = await registrations(db)
    assert reg.event_id == await event_id(db, "course")
    assert (reg.status, reg.tariff, reg.task, reg.invoice) == ("applied", "pro", COURSE["task"], True)
    person = await db.get(Person, reg.person_id)
    assert person.email == "irina@example.ru"
    assert person.telegram_username is None


async def test_same_person_by_username_and_email(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await post(client, EXAMPLE)
    # Курс с тем же username (в другом регистре) — тот же человек, почта дописывается.
    course = {**COURSE, "telegram_username": "Irina_Ops"}
    assert (await post(client, course)).json()["status"] == "created"
    assert await count(db, Person) == 1
    db.expire_all()
    person = await db.scalar(select(Person))
    assert person.email == "irina@example.ru"
    # Третья заявка только с почтой находит того же человека.
    third = {**COURSE, "id": "01JTHIRD000000000000000000", "telegram_username": None, "email": "IRINA@example.ru"}
    assert (await post(client, third)).json()["status"] == "merged"
    assert await count(db, Person) == 1
    assert await count(db, Registration) == 2


async def test_existing_fields_not_overwritten(client: httpx.AsyncClient, db: AsyncSession) -> None:
    db.add(Person(telegram_id=USER_ID, telegram_username="irina_ops", name="Ира", role="consultant", source="prk"))
    await db.commit()
    await post(client, {**EXAMPLE, "email": "irina@example.ru"})
    db.expire_all()
    person = await db.scalar(select(Person))
    assert (person.name, person.role, person.source) == ("Ира", "consultant", "prk")
    assert person.email == "irina@example.ru"


async def test_extra_fields_ignored_and_blanks_are_none(client: httpx.AsyncClient, db: AsyncSession) -> None:
    body = {**EXAMPLE, "website": "", "something_new": {"a": 1}, "email": "", "utm": None}
    response = await post(client, body)
    assert response.status_code == 200
    [reg] = await registrations(db)
    person = await db.get(Person, reg.person_id)
    assert person.email is None
    assert person.source == "site"
    assert reg.source is None


@pytest.mark.parametrize(
    "patch",
    [
        {"form": "webinar"},
        {"id": None},
        {"id": ""},
        {"role": "ceo"},
        {"tariff": "vip"},
        {"invoice": "maybe"},
    ],
)
async def test_validation_error_422(client: httpx.AsyncClient, db: AsyncSession, patch: dict[str, Any]) -> None:
    response = await post(client, {**EXAMPLE, **patch})
    assert response.status_code == 422
    assert await count(db, Registration) == 0


async def test_missing_form_and_bad_json_422(client: httpx.AsyncClient) -> None:
    body = {k: v for k, v in EXAMPLE.items() if k != "form"}
    assert (await post(client, body)).status_code == 422
    assert (await post(client, ["not", "an", "object"])).status_code == 422
    response = await client.post(
        URL, content=b"{broken", headers={**headers(), "Content-Type": "application/json"}
    )
    assert response.status_code == 422


# --- A3: заявка с сайта и запись из бота — одна строка ---------------------------------------------


async def test_site_application_merges_into_bot_registration(client: httpx.AsyncClient, db: AsyncSession) -> None:
    person = Person(telegram_id=USER_ID, telegram_username="irina_ops", source="lp_opex_p1")
    db.add(person)
    await db.flush()
    bot_reg = Registration(
        person_id=person.id, event_id=await event_id(db, "practicum"), channel="bot", source="lp_opex_p1"
    )
    db.add(bot_reg)
    await db.commit()

    response = await post(client, EXAMPLE)
    assert response.json() == {"status": "merged", "registration_id": bot_reg.id}
    [reg] = await registrations(db)
    assert reg.id == bot_reg.id
    assert (reg.channel, reg.status, reg.source, reg.site_id) == ("bot", "registered", "lp_opex_p1", EXAMPLE["id"])
    assert await count(db, Person) == 1

    assert (await post(client, EXAMPLE)).json() == {"status": "duplicate", "registration_id": bot_reg.id}
    assert await count(db, Registration) == 1


async def test_course_merge_fills_tariff_task_invoice(client: httpx.AsyncClient, db: AsyncSession) -> None:
    person = Person(telegram_id=USER_ID, email="irina@example.ru")
    db.add(person)
    await db.flush()
    db.add(Registration(person_id=person.id, event_id=await event_id(db, "course"), channel="bot", status="applied"))
    await db.commit()

    assert (await post(client, COURSE)).json()["status"] == "merged"
    [reg] = await registrations(db)
    assert (reg.channel, reg.tariff, reg.task, reg.invoice, reg.site_id) == (
        "bot",
        "pro",
        COURSE["task"],
        True,
        COURSE["id"],
    )


# --- A5: владельцу ничего не уходит, ПД не в логах --------------------------------------------------


async def test_no_owner_notification_and_no_pd_in_logs(
    client: httpx.AsyncClient, tg: TgHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    assert (await post(client, EXAMPLE)).status_code == 200
    assert (await post(client, COURSE)).status_code == 200
    assert (await post(client, EXAMPLE)).status_code == 200
    assert (await post(client, {**EXAMPLE, "id": "01JBAD", "role": "ceo"})).status_code == 422
    assert tg.session.calls == []
    log_text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app")).lower()
    assert EXAMPLE["id"].lower() in log_text
    for pd in ("ирина", "irina_ops", "irina@example.ru", "трекера"):
        assert pd not in log_text
