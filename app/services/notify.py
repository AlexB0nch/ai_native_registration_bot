"""Сообщения владельцу и помощникам (`ADMIN_CHAT_IDS`)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from app.config import get_settings

log = logging.getLogger(__name__)


async def notify_admins(
    bot: Bot,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> list[Message]:
    """Отправить `text` всем чатам из `ADMIN_CHAT_IDS`.

    Возвращает отправленные сообщения (для сохранения `admin_chat_id`/`admin_message_id`).
    Ошибка отправки в один чат не мешает остальным; в лог — только chat_id и тип ошибки.
    """
    sent: list[Message] = []
    for chat_id in get_settings().admin_chat_ids:
        try:
            sent.append(await bot.send_message(chat_id, text, reply_markup=reply_markup))
        except Exception as exc:
            log.warning("notify_admins: не доставлено в чат %s: %s", chat_id, type(exc).__name__)
    return sent
