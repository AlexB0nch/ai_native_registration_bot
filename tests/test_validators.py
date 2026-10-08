from __future__ import annotations

import pytest

from app.bot.validators import (
    looks_like_question,
    normalize_contact_phone,
    normalize_email,
    normalize_name,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 (912) 345-67-89", "+79123456789"),
        ("89123456789", "+79123456789"),
        ("8 912 345 67 89", "+79123456789"),
        ("+79123456789", "+79123456789"),
        ("9123456789", "+79123456789"),
        ("  +7 912 345-67-89  ", "+79123456789"),
    ],
)
def test_phone_normalized_to_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "", "   ", "телефон", "+7 912 abc 67 89", "8 800", None])
def test_phone_rejected(raw: str | None) -> None:
    assert normalize_phone(raw) is None


def test_contact_phone_without_plus() -> None:
    assert normalize_contact_phone("79123456789") == "+79123456789"
    assert normalize_contact_phone("+7 912 345 67 89") == "+79123456789"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Irina@Example.COM", "irina@example.com"),
        ("  name@company.ru ", "name@company.ru"),
        ("i.petrova+prk@mail.example.org", "i.petrova+prk@mail.example.org"),
    ],
)
def test_email_normalized(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["name@", "name.company.ru", "name@company", "na me@company.ru", "", None])
def test_email_rejected(raw: str | None) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ирина", "Ирина"),
        ("  Ирина   Петрова ", "Ирина Петрова"),
        ("Анна-Мария", "Анна-Мария"),
        ("x" * 100, "x" * 100),
    ],
)
def test_name_accepted(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "x" * 101,
        "https://example.com",
        "Ирина www.site.ru",
        "Ирина t.me/irina",
        "Ирина @irina",
        "mysite.ru",
        "А сколько стоит курс?",
        "хочу узнать подробнее про курс",
        None,
    ],
)
def test_name_rejected(raw: str | None) -> None:
    assert normalize_name(raw) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("А сколько стоит курс?", True),
        ("сколько?", True),
        ("хочу узнать подробнее про курс", True),
        ("Ирина", False),
        ("name@company", False),
        ("8 912 345", False),
    ],
)
def test_looks_like_question(text: str, expected: bool) -> None:
    assert looks_like_question(text) is expected
