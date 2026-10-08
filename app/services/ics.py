"""Файл календаря (.ics, RFC 5545) для события.

Без внешних зависимостей: строки с CRLF, экранирование текста и перенос длинных строк
(не длиннее 75 октетов) по RFC 5545.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import Event, utcnow
from app.texts import t

UID_DOMAIN = "reg.alexshein.com"
PRODID = "-//alexshein.com//AI native registration bot//RU"
CRLF = "\r\n"
MAX_LINE_OCTETS = 75


def escape_text(value: str) -> str:
    """Экранирование значения TEXT: `\\`, `;`, `,`, перевод строки."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Перенос строки длиннее 75 октетов: продолжение начинается с пробела, UTF-8 не рвётся."""
    parts: list[str] = []
    current = ""
    current_len = 0
    for char in line:
        size = len(char.encode("utf-8"))
        if current_len + size > MAX_LINE_OCTETS:
            parts.append(current)
            current, current_len = " ", 1
        current += char
        current_len += size
    parts.append(current)
    return CRLF.join(parts)


def format_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def event_summary(event: Event) -> str:
    """«Практикум: …» для практикума, название события — для остальных."""
    return t("practicum.ics.summary", title=event.title) if event.code == "practicum" else event.title


def event_description(event: Event) -> str:
    key = "practicum.ics.description" if event.code == "practicum" else "practicum.ics.default_description"
    return t(key, title=event.title)


def build_ics(event: Event, now: datetime | None = None) -> bytes:
    """VCALENDAR с одним VEVENT. `UID` стабилен (`<code>@reg.alexshein.com`), время — в UTC.

    Событие без времени начала — `ValueError`.
    """
    if event.starts_at is None:
        raise ValueError(f"у события {event.code!r} нет времени начала")
    ends_at = event.ends_at or event.starts_at
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event.code}@{UID_DOMAIN}",
        f"DTSTAMP:{format_utc(now or utcnow())}",
        f"DTSTART:{format_utc(event.starts_at)}",
        f"DTEND:{format_utc(ends_at)}",
        f"SUMMARY:{escape_text(event_summary(event))}",
        f"DESCRIPTION:{escape_text(event_description(event))}",
    ]
    if event.join_url:
        lines.append(f"URL:{event.join_url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return (CRLF.join(fold_line(line) for line in lines) + CRLF).encode("utf-8")
