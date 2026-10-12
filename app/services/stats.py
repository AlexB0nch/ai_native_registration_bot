"""Статистика для `/stats`: записи на практикум, заявки на курс, вопросы без ответа.

Считается в Python по выборке записей (их сотни), поэтому одинаково работает на Postgres
и на SQLite. Дни — по московскому времени (`to_local`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.facts import get_facts, to_local
from app.models import Event, Registration, utcnow
from app.services.questions import count_unanswered
from app.texts import has, t

DAYS = 14
TOP_SOURCES = 15
CANCELLED = "cancelled"


@dataclass
class Stats:
    practicum_total: int = 0  # без отменённых
    practicum_cancelled: int = 0
    by_day: list[tuple[date, int]] = field(default_factory=list)  # DAYS дней по МСК, по возрастанию
    by_source: list[tuple[str | None, int]] = field(default_factory=list)  # топ TOP_SOURCES
    other_sources: int = 0  # записей из источников вне топа
    by_channel: dict[str, int] = field(default_factory=dict)
    course_total: int = 0
    course_by_tariff: list[tuple[str | None, int]] = field(default_factory=list)
    course_by_status: list[tuple[str, int]] = field(default_factory=list)
    unanswered: int = 0


def _ranked(counter: Counter) -> list:
    return sorted(counter.items(), key=lambda item: (-item[1], str(item[0] or "")))


async def _registrations(session: AsyncSession, event_code: str) -> list[Registration]:
    stmt = (
        select(Registration)
        .join(Event, Event.id == Registration.event_id)
        .where(Event.code == event_code)
        .order_by(Registration.id)
    )
    return list((await session.scalars(stmt)).all())


async def collect_stats(session: AsyncSession, now: datetime | None = None) -> Stats:
    now = now or utcnow()
    stats = Stats()

    practicum = await _registrations(session, "practicum")
    active = [r for r in practicum if r.status != CANCELLED]
    stats.practicum_total = len(active)
    stats.practicum_cancelled = len(practicum) - len(active)

    today = to_local(now).date()
    days = [today - timedelta(days=offset) for offset in range(DAYS - 1, -1, -1)]
    per_day = Counter(to_local(r.created_at).date() for r in active)
    stats.by_day = [(day, per_day.get(day, 0)) for day in days]

    sources = _ranked(Counter(r.source or None for r in active))
    stats.by_source = sources[:TOP_SOURCES]
    stats.other_sources = sum(count for _, count in sources[TOP_SOURCES:])

    channels = Counter(r.channel for r in active)
    stats.by_channel = {"bot": channels.pop("bot", 0), "site": channels.pop("site", 0), **dict(_ranked(channels))}

    course = await _registrations(session, "course")
    stats.course_total = len(course)
    stats.course_by_tariff = _ranked(Counter(r.tariff or None for r in course))
    stats.course_by_status = _ranked(Counter(r.status for r in course))

    stats.unanswered = await count_unanswered(session)
    return stats


# --- текст для владельца ----------------------------------------------------------------------


def _label(prefix: str, code: str) -> str:
    key = f"{prefix}.{code}"
    return t(key) if has(key) else code


def _tariff_label(code: str | None) -> str:
    if not code:
        return t("admin.no_tariff")
    for tariff in get_facts().course.tariffs:
        if tariff.code == code:
            return tariff.name
    return code


def _inline(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"{label} — {count}" for label, count in pairs) or t("admin.none")


def _lines(pairs: list[tuple[str, int]]) -> str:
    return "\n".join(f"{label} — {count}" for label, count in pairs) or t("admin.none")


def render_stats(stats: Stats) -> str:
    sources = [(source or t("admin.no_source"), count) for source, count in stats.by_source]
    if stats.other_sources:
        sources_text = _lines(sources) + "\n" + t("admin.stats_other_sources", n=stats.other_sources)
    else:
        sources_text = _lines(sources)

    if stats.course_total:
        course = t(
            "admin.stats_course",
            total=stats.course_total,
            by_tariff=_inline([(_tariff_label(code), n) for code, n in stats.course_by_tariff]),
            by_status=_inline([(_label("admin.statuses", code), n) for code, n in stats.course_by_status]),
        )
    else:
        course = t("admin.stats_course_empty")

    return t(
        "admin.stats",
        practicum_total=stats.practicum_total,
        practicum_cancelled=t("admin.stats_cancelled", n=stats.practicum_cancelled)
        if stats.practicum_cancelled
        else "",
        by_channel=_inline([(_label("admin.channels", code), n) for code, n in stats.by_channel.items()]),
        days=DAYS,
        by_day=_lines([(f"{day:%d.%m}", n) for day, n in stats.by_day]),
        by_source=sources_text,
        course=course,
        unanswered=stats.unanswered,
    )
