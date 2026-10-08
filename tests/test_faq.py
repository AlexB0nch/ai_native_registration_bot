"""TASK-LLM-001: быстрые ответы без модели (`app/llm/faq.py`, тексты `faq.*`) — A1–A4."""

from __future__ import annotations

import pytest

from app.facts import NBSP, get_facts, set_facts
from app.llm.faq import match_faq, wants_human
from app.llm.guard import check
from app.texts import raw, t

FAQ_KEYS = ("prices", "dates", "no_coding", "practicum", "unknown", "human", "handoff_more")


@pytest.mark.parametrize("key", FAQ_KEYS)
def test_faq_texts_fit_and_pass_guard(key: str) -> None:
    text = t(f"faq.{key}")
    assert len(text) <= 600, key
    assert check(text).ok, check(text).reasons


def test_faq_texts_are_placeholders_only() -> None:
    # литералы цен и дат запрещает общий тест текстов; здесь — что подстановки действительно есть
    assert "{price_early}" in raw("faq.prices")
    assert "{practicum_slot}" in raw("faq.dates")


@pytest.mark.parametrize(
    ("question", "key", "cta"),
    [
        ("Сколько стоит курс?", "faq.prices", "course_page"),
        ("Какая цена?", "faq.prices", "course_page"),
        ("Расскажите про тарифы", "faq.prices", "course_page"),
        ("Стоимость участия?", "faq.prices", "course_page"),
        ("Когда начало?", "faq.dates", "practicum"),
        ("Когда старт курса?", "faq.dates", "practicum"),
        ("Какое расписание занятий?", "faq.dates", "practicum"),
        ("Во сколько практикум?", "faq.dates", "practicum"),
        ("Нужно ли уметь программировать?", "faq.no_coding", "practicum"),
        ("Я не программист, справлюсь?", "faq.no_coding", "practicum"),
        ("Придётся писать код?", "faq.no_coding", "practicum"),
        ("Что будет на практикуме?", "faq.practicum", "practicum"),
        ("Расскажите про практикум", "faq.practicum", "practicum"),
    ],
)
def test_match(question: str, key: str, cta: str) -> None:
    answer = match_faq(question)
    assert answer is not None
    assert (answer.key, answer.cta) == (key, cta)
    assert answer.text == t(key)


@pytest.mark.parametrize(
    "question",
    [
        "Сколько стоят подписки на сервисы?",
        "Можно работать на данных клиента?",
        "Какие примеры MVP можно собрать?",
        "Можно ли вернуть деньги?",
        "Кто ведёт курс?",
        "",
        "   ",
    ],
)
def test_no_match(question: str) -> None:
    assert match_faq(question) is None


def test_a1_prices_from_facts() -> None:
    answer = match_faq("Сколько стоит курс?")
    assert answer is not None
    for price in ("24 900 ₽", "29 900 ₽", "44 900 ₽"):
        assert price.replace(" ", NBSP) in answer.text
    assert "2 ноября включительно" in answer.text

    facts = get_facts().model_copy(deep=True)
    facts.course.tariffs[1].price_rub = 31900
    set_facts(facts)
    changed = match_faq("Сколько стоит курс?")
    assert changed is not None
    assert f"31{NBSP}900{NBSP}₽" in changed.text
    assert f"29{NBSP}900{NBSP}₽" not in changed.text


def test_a2_dates() -> None:
    answer = match_faq("Когда начало?")
    assert answer is not None
    assert "7 ноября" in answer.text
    assert "31 октября" in answer.text
    assert "15:00–17:00 МСК" in answer.text
    assert "5 декабря" in answer.text


def test_a3_no_coding() -> None:
    answer = match_faq("Нужно ли уметь программировать?")
    assert answer is not None
    assert "не нужно" in answer.text
    assert "production" in answer.text


def test_a4_practicum_topic_and_points() -> None:
    answer = match_faq("Что будет на практикуме?")
    assert answer is not None
    assert get_facts().practicum.title in answer.text
    assert "Почему чат с ИИ ещё не система работы" in answer.text
    assert "управленческая история" in answer.text
    assert "бесплатно" in answer.text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Позовите человека", True),
        ("Можно связаться с Александром?", True),
        ("Хочу поговорить с человеком", True),
        ("Нужен оператор", True),
        ("Кто такой Александр Шеин?", False),
        ("Сколько человек в группе?", False),
        ("Что будет на курсе?", False),
    ],
)
def test_wants_human(text: str, expected: bool) -> None:
    assert wants_human(text) is expected
