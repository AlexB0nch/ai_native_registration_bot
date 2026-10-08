"""Команды владельца: /stats, /export, /link, /set_link — TASK-BOT-002.

Заглушка CORE: роутер без хендлеров. Фильтр `IsAdmin` уже стоит на уровне роутера:
сообщения и нажатия не-админов сюда не попадают и идут дальше (start → practicum → questions).
"""

from __future__ import annotations

from aiogram import Router

from app.bot.filters import IsAdmin

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
