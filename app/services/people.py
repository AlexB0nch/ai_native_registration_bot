"""Люди: поиск и создание по Telegram.

Функции поиска по username/почте и слияния с заявкой с сайта добавит TASK-API-001.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person


def normalize_username(username: str | None) -> str | None:
    """`@Irina_Ops` → `irina_ops`; пустое → None."""
    if not username:
        return None
    value = username.strip().lstrip("@").lower()
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
