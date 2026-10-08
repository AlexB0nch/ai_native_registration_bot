"""Приём заявок с сайта: `POST /api/site-registrations` — TASK-API-001.

Заглушка CORE: пустой роутер, уже подключён в `app/main.py` (без префикса —
путь эндпоинта задаётся целиком в декораторе).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["site"])
