from __future__ import annotations

import pytest

from app.bot.payload import StartPayload, parse_start_payload


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("prk", StartPayload("prk", "practicum")),
        ("prk_tg_channel", StartPayload("prk_tg_channel", "practicum")),
        ("lp_opex_p1", StartPayload("lp_opex_p1", "practicum")),
        ("prk_web", StartPayload("prk_web", "site_practicum")),
        ("crs", StartPayload("crs", "course")),
        ("crs_blog", StartPayload("crs_blog", "course")),
        ("crs_web", StartPayload("crs_web", "site_course")),
        ("wait", StartPayload("wait", "waitlist")),
        ("tgads_opex_w2", StartPayload("tgads_opex_w2", "menu")),
        ("PRK", StartPayload("PRK", "practicum")),
        ("prkx", StartPayload("prkx", "menu")),
        ("", StartPayload(None, "menu")),
        (None, StartPayload(None, "menu")),
        ("bad!payload", StartPayload(None, "menu")),
        ("a" * 64, StartPayload("a" * 64, "menu")),
        ("a" * 65, StartPayload(None, "menu")),
        ("прк", StartPayload(None, "menu")),
        ("prk web", StartPayload(None, "menu")),
    ],
)
def test_parse_start_payload(text: str | None, expected: StartPayload) -> None:
    assert parse_start_payload(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", StartPayload(None, "menu")),
        ("/start lp_opex_p1", StartPayload("lp_opex_p1", "practicum")),
        ("/start@test_reg_bot crs", StartPayload("crs", "course")),
        ("/start bad!payload", StartPayload(None, "menu")),
    ],
)
def test_parse_full_command_text(text: str, expected: StartPayload) -> None:
    assert parse_start_payload(text) == expected
