"""Рассылка участникам события: аудитория, идемпотентная отправка, учёт в `deliveries`.

Без aiogram-хендлеров — вызывается командой владельца (`app/bot/handlers/broadcast.py`)
и может быть вызвана планировщиком (TASK-BOT-004).

- аудитория — люди с записью на событие не в статусе `cancelled` и с `telegram_id`, один человек — одно сообщение;
- `kind = "broadcast:" + sha256(text)[:8]`; уникальность `(person_id, event_id, kind)` в `deliveries`;
- `sent` и `blocked` при повторе того же текста пропускаются, `failed` — повторяются (строка обновляется);
- строка `deliveries` пишется сразу после каждой отправки, а не пачкой в конце;
- в лог — только счётчики, хеш текста и внутренние id; текст, имена, username и telegram_id не логируются.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Delivery, Event, Person, Registration, utcnow
from app.services.registrations import CANCELLED, get_or_sync_event

log = logging.getLogger(__name__)

DEFAULT_EVENT_CODE = "practicum"
DEFAULT_PAUSE = 1 / 25  # ≤ 25 сообщений в секунду (лимит Telegram — 30)
MAX_RETRY_AFTER = 3  # сколько раз ждём `retry_after` и повторяем
GENERIC_RETRY_DELAY = 1.0  # пауза перед единственным повтором при прочих ошибках
KIND_PREFIX = "broadcast:"

SENT, BLOCKED, FAILED = "sent", "blocked", "failed"
DONE_STATUSES = (SENT, BLOCKED)  # такие получатели при повторе того же текста пропускаются


async def _sleep(seconds: float) -> None:
    """Единственное место, где рассылка ждёт: тесты подменяют его."""
    await asyncio.sleep(seconds)


def text_hash(text: str) -> str:
    """Короткий хеш текста: идёт в `kind` и в `callback_data` кнопки."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def broadcast_kind(text: str) -> str:
    return f"{KIND_PREFIX}{text_hash(text)}"


@dataclass(frozen=True)
class Recipient:
    person_id: int
    telegram_id: int


@dataclass(frozen=True)
class Audience:
    recipients: list[Recipient]
    without_telegram: int  # записаны, но Telegram нет (пришли с сайта только с почтой)


@dataclass
class BroadcastResult:
    """Счётчики рассылки. Изменяемый: `send_broadcast(result=...)` наполняет его по ходу работы,
    поэтому при сбое посреди рассылки вызывающий код видит числа на момент сбоя."""

    sent: int = 0
    blocked: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class BroadcastPlan:
    """Что произойдёт, если отправить `text` сейчас (для предпросмотра)."""

    kind: str
    total: int  # всего получателей с Telegram
    already: int  # уже получили именно этот текст (sent / blocked)
    to_send: int  # total - already
    without_telegram: int


async def collect_audience(session: AsyncSession, event_code: str = DEFAULT_EVENT_CODE) -> Audience:
    rows = (
        await session.execute(
            select(Person.id, Person.telegram_id)
            .join(Registration, Registration.person_id == Person.id)
            .join(Event, Event.id == Registration.event_id)
            .where(Event.code == event_code, Registration.status != CANCELLED)
            .order_by(Person.id)
        )
    ).all()
    recipients: dict[int, Recipient] = {}
    without_telegram: set[int] = set()
    for person_id, telegram_id in rows:
        if telegram_id is None:
            without_telegram.add(person_id)
        else:
            recipients.setdefault(person_id, Recipient(person_id, telegram_id))
    return Audience(list(recipients.values()), len(without_telegram - recipients.keys()))


async def already_delivered(session: AsyncSession, event_id: int, kind: str) -> set[int]:
    """`people.id` тех, кому это сообщение уже ушло (`sent`) или не может уйти (`blocked`)."""
    rows = await session.scalars(
        select(Delivery.person_id).where(
            Delivery.event_id == event_id, Delivery.kind == kind, Delivery.status.in_(DONE_STATUSES)
        )
    )
    return set(rows.all())


async def plan_broadcast(session: AsyncSession, text: str, event_code: str = DEFAULT_EVENT_CODE) -> BroadcastPlan:
    kind = broadcast_kind(text)
    event = await get_or_sync_event(session, event_code)
    audience = await collect_audience(session, event_code)
    done = await already_delivered(session, event.id, kind)
    already = sum(1 for r in audience.recipients if r.person_id in done)
    total = len(audience.recipients)
    return BroadcastPlan(kind, total, already, total - already, audience.without_telegram)


async def _send_one(bot: Bot, chat_id: int, text: str, pause: float) -> tuple[str, str | None]:
    """Отправить одно сообщение. Возвращает `(статус, тип ошибки)`; исключения наружу не выходят
    (кроме отмены задачи)."""
    retry_after_left = MAX_RETRY_AFTER
    generic_retry_left = 1
    while True:
        try:
            await bot.send_message(chat_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True))
            return SENT, None
        except TelegramForbiddenError as exc:
            return BLOCKED, type(exc).__name__
        except TelegramRetryAfter as exc:
            if retry_after_left <= 0:
                return FAILED, type(exc).__name__
            retry_after_left -= 1
            await _sleep(exc.retry_after)
        except Exception as exc:
            if generic_retry_left <= 0:
                return FAILED, type(exc).__name__
            generic_retry_left -= 1
            if pause > 0:
                await _sleep(GENERIC_RETRY_DELAY)


async def _record(event_id: int, person_id: int, kind: str, status: str, error: str | None) -> None:
    """Записать итог отправки сразу же и в своей транзакции; существующая строка обновляется."""
    for attempt in (1, 2):
        try:
            async with session_scope() as session:
                delivery = await session.scalar(
                    select(Delivery).where(
                        Delivery.person_id == person_id, Delivery.event_id == event_id, Delivery.kind == kind
                    )
                )
                if delivery is None:
                    delivery = Delivery(person_id=person_id, event_id=event_id, kind=kind)
                    session.add(delivery)
                delivery.status = status
                delivery.sent_at = utcnow()
                delivery.error = error
            return
        except IntegrityError:
            if attempt == 2:  # параллельная вставка той же строки: второй проход её обновит
                raise


async def send_broadcast(
    bot: Bot,
    text: str,
    *,
    event_code: str = DEFAULT_EVENT_CODE,
    kind: str | None = None,
    pause: float = DEFAULT_PAUSE,
    result: BroadcastResult | None = None,
) -> BroadcastResult:
    """Разослать `text` участникам события; безопасно запускать повторно с тем же текстом.

    Текст уходит как есть: без `parse_mode`, превью ссылок выключено. `result` — необязательный
    объект счётчиков, который наполняется по ходу работы (виден вызывающему при исключении).
    """
    result = result if result is not None else BroadcastResult()
    kind = kind or broadcast_kind(text)
    async with session_scope() as session:
        event = await get_or_sync_event(session, event_code)
        audience = await collect_audience(session, event_code)
        done = await already_delivered(session, event.id, kind)
    event_id = event.id

    for recipient in audience.recipients:
        if recipient.person_id in done:
            result.skipped += 1
            continue
        status, error = await _send_one(bot, recipient.telegram_id, text, pause)
        await _record(event_id, recipient.person_id, kind, status, error)
        if status == SENT:
            result.sent += 1
        elif status == BLOCKED:
            result.blocked += 1
            log.info("broadcast %s: person %s заблокировал бота", kind, recipient.person_id)
        else:
            result.failed += 1
            log.warning("broadcast %s: person %s не доставлено: %s", kind, recipient.person_id, error)
        if pause > 0:
            await _sleep(pause)

    log.info(
        "broadcast: hash=%s recipients=%s sent=%s blocked=%s failed=%s skipped=%s",
        kind.removeprefix(KIND_PREFIX),
        len(audience.recipients),
        result.sent,
        result.blocked,
        result.failed,
        result.skipped,
    )
    return result
