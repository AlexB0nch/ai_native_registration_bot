from __future__ import annotations

from datetime import UTC, datetime

import pytest
from icalendar import Calendar

from app.facts import event_start_utc, get_facts
from app.models import Event
from app.services.ics import MAX_LINE_OCTETS, build_ics, escape_text, fold_line


def _practicum(join_url: str | None = None) -> Event:
    practicum = get_facts().practicum
    return Event(
        code="practicum",
        title=practicum.title,
        starts_at=event_start_utc(practicum.date, practicum.start),
        ends_at=event_start_utc(practicum.date, practicum.end),
        join_url=join_url,
    )


def _vevent(content: bytes):
    calendar = Calendar.from_ical(content)
    events = list(calendar.walk("VEVENT"))
    assert len(events) == 1
    return events[0]


def test_ics_parses_with_utc_time() -> None:
    content = build_ics(_practicum())
    vevent = _vevent(content)
    assert vevent.decoded("DTSTART") == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert vevent.decoded("DTEND") == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
    assert str(vevent["UID"]) == "practicum@reg.alexshein.com"
    assert str(vevent["SUMMARY"]) == f"Практикум: {get_facts().practicum.title}"
    description = str(vevent["DESCRIPTION"])
    assert "https://alexshein.com/ai-native" in description
    assert "31 октября, суббота, 15:00–17:00 МСК" in description
    assert "URL" not in vevent


def test_ics_join_url_and_stable_uid() -> None:
    first = _vevent(build_ics(_practicum(join_url="https://telemost.example/j/123")))
    second = _vevent(build_ics(_practicum()))
    assert str(first["URL"]) == "https://telemost.example/j/123"
    assert first["UID"] == second["UID"]


def test_ics_format_crlf_and_folding() -> None:
    content = build_ics(_practicum(), now=datetime(2026, 10, 8, 9, 30, tzinfo=UTC))
    assert content.endswith(b"\r\n")
    lines = content.split(b"\r\n")[:-1]
    assert lines[0] == b"BEGIN:VCALENDAR"
    assert lines[-1] == b"END:VCALENDAR"
    assert b"DTSTAMP:20261008T093000Z" in lines
    assert b"DTSTART:20261031T120000Z" in lines
    assert all(len(line) <= MAX_LINE_OCTETS for line in lines)
    assert b"\n" not in content.replace(b"\r\n", b"")
    content.decode("utf-8")  # перенос не разрывает многобайтные символы


def test_escape_and_fold_roundtrip() -> None:
    assert escape_text("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    event = _practicum()
    event.title = "Тест; с запятой, обратной \\ чертой и\nпереводом строки " + "длинно " * 20
    vevent = _vevent(build_ics(event))
    assert str(vevent["SUMMARY"]) == f"Практикум: {event.title}"
    folded = fold_line("X" * 200)
    assert all(len(part.encode()) <= MAX_LINE_OCTETS for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == "X" * 200


def test_ics_without_start_fails() -> None:
    with pytest.raises(ValueError):
        build_ics(Event(code="waitlist", title="Лист ожидания"))


def test_other_event_uses_title() -> None:
    event = Event(
        code="demo_day",
        title="Курс: демо-день",
        starts_at=datetime(2026, 12, 5, 12, tzinfo=UTC),
        ends_at=datetime(2026, 12, 5, 14, tzinfo=UTC),
    )
    vevent = _vevent(build_ics(event))
    assert str(vevent["SUMMARY"]) == "Курс: демо-день"
    assert str(vevent["UID"]) == "demo_day@reg.alexshein.com"
