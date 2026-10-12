"""Приём заявок с сайта: `POST /api/site-registrations` — TASK-API-001.

Договор — `materials/FORMS.md`, раздел «Формат заявки». Сервис форм присылает заявку JSON-ом
с заголовком `X-Webhook-Secret`. Ответы:

- `200 {"status": "created"|"merged"|"duplicate", "registration_id": N}`;
- `401` — нет заголовка или секрет не совпал; `503` — `SITE_WEBHOOK_SECRET` не задан на сервере;
- `422` — тело не по договору.

Секрет проверяется до разбора тела, чтобы без него нельзя было узнать формат.
Владельцу бот ничего не пишет — это делает сервис форм. В лог — только `site_id` и `registration_id`.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.services.site_import import SiteRegistrationIn, import_site_registration

log = logging.getLogger(__name__)

router = APIRouter(tags=["site"])

SITE_REGISTRATIONS_PATH = "/api/site-registrations"
SITE_SECRET_HEADER = "X-Webhook-Secret"  # noqa: S105 — имя заголовка, не секрет


def _check_secret(request: Request) -> JSONResponse | None:
    expected = get_settings().site_webhook_secret
    if not expected:
        log.warning("site import: SITE_WEBHOOK_SECRET не задан, заявка отклонена")
        return JSONResponse({"detail": "site webhook is not configured"}, status_code=503)
    received = request.headers.get(SITE_SECRET_HEADER) or ""
    if not hmac.compare_digest(received.encode(), expected.encode()):
        return JSONResponse({"detail": "invalid secret"}, status_code=401)
    return None


def _invalid(errors: list) -> JSONResponse:
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


@router.post(SITE_REGISTRATIONS_PATH)
async def site_registration(request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:  # noqa: B008
    denied = _check_secret(request)
    if denied is not None:
        return denied

    try:
        body = await request.json()
    except ValueError:  # не JSON или не UTF-8
        return _invalid([{"type": "json_invalid", "loc": ["body"], "msg": "invalid JSON"}])
    try:
        data = SiteRegistrationIn.model_validate(body)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        log.warning("site import: заявка не по договору, поля: %s", [list(e["loc"]) for e in errors])
        return _invalid(errors)

    try:
        result = await import_site_registration(session, data)
        await session.commit()
    except IntegrityError:
        # Гонка: та же заявка (или запись из бота) появилась параллельно — повторить на свежих данных.
        await session.rollback()
        log.info("site import: конфликт при записи site_id=%s, повтор", data.id)
        result = await import_site_registration(session, data)
        await session.commit()
    return JSONResponse({"status": result.status, "registration_id": result.registration_id})
