"""TASK-LLM-001: проверка ответа модели (`app/llm/guard.py`)."""

from __future__ import annotations

import pytest

from app.facts import get_facts, set_facts
from app.llm.guard import MAX_LEN, check, truncate

NBSP = " "
NNBSP = " "


@pytest.mark.parametrize(
    "text",
    [
        "Early Bird стоит 24 900 ₽.",
        f"Early Bird стоит 24{NBSP}900{NBSP}₽.",
        f"Standard — 29{NNBSP}900{NNBSP}₽.",
        "Pro — 44900 руб.",
        "Pro — 44 900 рублей.",
        "Standard — 29,9 тыс. ₽.",
        "Курс начинается 7 ноября, занятия 7, 14, 21 и 28 ноября, 15:00–17:00 МСК.",
        "Практикум 31 октября, демо-день 5 декабря, Early Bird до 2 ноября включительно.",
        "Курс идёт 7–28 ноября.",
        "Практикум 31.10, старт 07.11.2026.",
        "Подробности на странице https://alexshein.com/ai-native.",
        "Напишите Александру: https://t.me/Alex81Shein",
        "Занятия по 2 часа, 4 субботы, Pro — не более 3 мест.",
    ],
)
def test_valid_answers_pass(text: str) -> None:
    result = check(text)
    assert result.ok, result.reasons
    assert result.text == text


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Курс стоит 19 900 ₽.", "price:19900"),
        (f"Курс стоит 19{NBSP}900{NBSP}₽.", "price:19900"),
        (f"Курс стоит 19{NNBSP}900{NNBSP}₽.", "price:19900"),
        ("Курс стоит 19900 руб.", "price:19900"),
        ("Курс стоит 20 тыс. руб.", "price:20000"),
        ("Практикум 30 октября.", "date:30.10"),
        ("Занятия 7, 15 и 21 ноября.", "date:15.11"),
        ("Старт 01.12.", "date:01.12"),
        ("Начало в 18:00 МСК.", "time:18:00"),
        ("Подробнее: https://example.com/course", "url"),
        ("Это уникальный курс.", "word:уникальн"),
        ("Успейте записаться!", "word:успейте"),
        ("Мы гарантируем результат.", "word:гарантир"),
        ("Экономия 30% времени.", "word:%"),
        ("Это революционный подход.", "word:революцион"),
        ("", "empty"),
    ],
)
def test_invalid_answers_rejected(text: str, reason: str) -> None:
    result = check(text)
    assert not result.ok
    assert reason in result.reasons


def test_prices_follow_facts() -> None:
    facts = get_facts().model_copy(deep=True)
    facts.course.tariffs[0].price_rub = 19900
    set_facts(facts)
    assert check("Early Bird — 19 900 ₽.").ok
    assert not check("Early Bird — 24 900 ₽.").ok


def test_dates_follow_facts() -> None:
    facts = get_facts().model_copy(deep=True)
    facts.practicum.date = facts.practicum.date.replace(day=24)
    set_facts(facts)
    assert check("Практикум 24 октября.").ok
    assert not check("Практикум 31 октября.").ok


def test_long_answer_truncated_by_sentence() -> None:
    sentence = "Курс помогает собрать систему работы консультанта. "
    text = sentence * 20
    result = check(text)
    assert result.ok
    assert len(result.text) <= MAX_LEN
    assert result.text.endswith(".")
    assert result.text.startswith("Курс помогает")


def test_truncate_without_sentences() -> None:
    text = "слово " * 200
    out = truncate(text)
    assert len(out) <= MAX_LEN
    assert out.endswith("…")
    assert truncate("коротко") == "коротко"
