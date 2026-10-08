"""Фильтры aiogram."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config import get_settings


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().admin_chat_ids


class IsAdmin(BaseFilter):
    """Пропускает только пользователей из `ADMIN_CHAT_IDS`.

    Сравнивается id отправителя (в личном чате он равен chat_id из `/whoami`),
    поэтому участник группы, добавленной в ADMIN_CHAT_IDS, админом не становится.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return is_admin(user.id if user else None)
