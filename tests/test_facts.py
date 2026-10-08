from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
import yaml

from app import facts as facts_mod
from app.facts import (
    NBSP,
    FactsError,
    all_dates,
    all_prices_rub,
    event_start_utc,
    format_date_ru,
    format_dates_list,
    format_price,
    format_slot,
    get_facts,
    load_facts,
    set_facts,
    tariff_available,
)
from app.texts import TextError, fact_placeholders, get_texts, t

MSK = facts_mod.ZoneInfo("Europe/Moscow")


def _raw_facts() -> dict:
    return yaml.safe_load(facts_mod.DEFAULT_FACTS_PATH.read_text(encoding="utf-8"))


def test_facts_yaml_loads_with_expected_values() -> None:
    facts = get_facts()
    assert facts.timezone == "Europe/Moscow"
    assert facts.practicum.date == date(2026, 10, 31)
    assert facts.practicum.start == time(15, 0)
    assert facts.practicum.platform == "Яндекс Телемост"
    assert facts.course.sessions == [date(2026, 11, d) for d in (7, 14, 21, 28)]
    assert facts.course.demo_day.date == date(2026, 12, 5)
    assert [(tr.code, tr.price_rub) for tr in facts.course.tariffs] == [
        ("early", 24900),
        ("standard", 29900),
        ("pro", 44900),
    ]
    assert facts.tariff("early").until == date(2026, 11, 2)
    assert facts.tariff("pro").max_seats == 3
    assert facts.links.gift_after_practicum.endswith("?start=after_prk")


def test_schema_error_is_clear(tmp_path: Path) -> None:
    raw = _raw_facts()
    raw["course"]["tariffs"][0]["price_rub"] = "дорого"
    del raw["practicum"]["date"]
    path = tmp_path / "facts.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(FactsError) as exc_info:
        load_facts(path)
    message = str(exc_info.value)
    assert "practicum.date" in message
    assert "course.tariffs.0.price_rub" in message


def test_unquoted_time_is_rejected() -> None:
    raw = _raw_facts()
    raw["practicum"]["start"] = 900  # так YAML 1.1 читает 15:00 без кавычек
    with pytest.raises(FactsError, match=r"practicum\.start"):
        load_facts(raw)


def test_unknown_field_is_rejected() -> None:
    raw = _raw_facts()
    raw["course"]["pricee"] = 1
    with pytest.raises(FactsError, match=r"course\.pricee"):
        load_facts(raw)


def test_missing_file() -> None:
    with pytest.raises(FactsError, match="не найден"):
        load_facts("/nonexistent/facts.yaml")


def test_event_start_utc() -> None:
    assert event_start_utc(date(2026, 10, 31), "15:00") == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert event_start_utc(date(2026, 10, 31), time(17, 0)) == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
    assert event_start_utc(date(2026, 10, 31), None) is None


def test_format_date_and_slot() -> None:
    assert format_date_ru(date(2026, 10, 31)) == "31 октября, суббота"
    assert format_date_ru(date(2026, 11, 2)) == "2 ноября, понедельник"
    assert format_slot(date(2026, 10, 31), time(15), time(17)) == "31 октября, суббота, 15:00–17:00 МСК"
    assert format_slot(date(2026, 11, 7), None, None) == "7 ноября, суббота, время уточняется"
    assert format_slot(date(2026, 11, 7), "09:30", None) == "7 ноября, суббота, 09:30 МСК"


def test_format_dates_list() -> None:
    sessions = [date(2026, 11, d) for d in (21, 7, 28, 14)]
    assert format_dates_list(sessions) == "7, 14, 21 и 28 ноября"
    assert format_dates_list([date(2026, 11, 7)]) == "7 ноября"
    assert format_dates_list([date(2026, 10, 31), date(2026, 11, 7)]) == "31 октября и 7 ноября"


def test_format_price() -> None:
    assert format_price(24900) == f"24{NBSP}900{NBSP}₽"
    assert format_price(900) == f"900{NBSP}₽"
    assert format_price(1_000_000) == f"1{NBSP}000{NBSP}000{NBSP}₽"


@pytest.mark.parametrize(
    ("moment_msk", "expected"),
    [
        (datetime(2026, 10, 9, 12, 0), True),
        (datetime(2026, 11, 2, 23, 59), True),
        (datetime(2026, 11, 3, 0, 0), False),
        (datetime(2026, 11, 10, 12, 0), False),
    ],
)
def test_early_bird_boundary(moment_msk: datetime, expected: bool) -> None:
    now = moment_msk.replace(tzinfo=MSK).astimezone(UTC)
    assert tariff_available("early", now) is expected


def test_early_bird_boundary_with_naive_utc() -> None:
    # 02.11 23:59 МСК = 02.11 20:59 UTC; naive datetime считается UTC
    assert tariff_available("early", datetime(2026, 11, 2, 20, 59)) is True
    assert tariff_available("early", datetime(2026, 11, 2, 21, 0)) is False


def test_pro_seats_and_standard() -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert tariff_available("pro", now, taken_seats=0) is True
    assert tariff_available("pro", now, taken_seats=2) is True
    assert tariff_available("pro", now, taken_seats=3) is False
    assert tariff_available("standard", now, taken_seats=100) is True
    with pytest.raises(KeyError):
        tariff_available("vip", now)


def test_all_prices_and_dates() -> None:
    assert all_prices_rub() == {24900, 29900, 44900}
    dates = all_dates()
    assert date(2026, 10, 31) in dates
    assert date(2026, 12, 5) in dates
    assert date(2026, 11, 2) in dates
    assert {date(2026, 11, d) for d in (7, 14, 21, 28)} <= dates


def test_set_facts_changes_texts() -> None:
    facts = get_facts().model_copy(deep=True)
    facts.course.tariffs[0].price_rub = 19900
    set_facts(facts)
    assert f"19{NBSP}900{NBSP}₽" in t("prices")
    assert 19900 in all_prices_rub()


# --- тексты ---------------------------------------------------------------------------------

MONTHS = "|".join(facts_mod.MONTHS_GENITIVE)


def _walk(node: object, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for key, value in node.items():
            out += _walk(value, f"{prefix}.{key}" if prefix else str(key))
        return out
    if isinstance(node, list):
        return [item for i, value in enumerate(node) for item in _walk(value, f"{prefix}.{i}")]
    return [(prefix, str(node))]


def test_texts_have_no_literal_prices_or_dates() -> None:
    for key, value in _walk(get_texts()):
        assert not re.search(r"\d[\d\s ]*\s*(₽|руб)", value), f"цена литералом в {key}"
        assert not re.search(rf"\d{{1,2}}\s+({MONTHS})", value), f"дата литералом в {key}"
        assert not re.search(r"\b20\d\d\b", value), f"год литералом в {key}"


FORBIDDEN = ("уникальн", "успейте", "прорыв", "революцион", "вебинар", "разбор", "стать разработчиком")


def test_texts_tone() -> None:
    for key, value in _walk(get_texts()):
        low = value.lower()
        for word in FORBIDDEN:
            assert word not in low, f"«{word}» в {key}"


def test_all_texts_render() -> None:
    kwargs = {"chat_id": 1, "n": 1}
    # подстановки сценария практикума (TASK-BOT-001)
    kwargs |= {"name": "x", "email": "x", "phone": "x", "username": "x", "source": "x", "count": 1, "title": "x"}
    for key, value in _walk(get_texts()):
        if isinstance(value, str):
            rendered = t(key, **kwargs)
            assert "{" not in rendered, key
            emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", rendered)
            assert len(emoji) <= 1, f"больше одного эмодзи в {key}"


def test_texts_contents() -> None:
    welcome = t("welcome")
    assert welcome.startswith("Здравствуйте! Я бот Александра Шеина, консультанта по операционным трансформациям.")
    assert "практикум 31 октября" in welcome
    assert "«ИИ и вайбкодинг для консультанта по операционным преобразованиям»" in welcome
    assert t("menu.practicum") == "Записаться на практикум 31 октября"

    about = t("course_about")
    assert "7, 14, 21 и 28 ноября, 15:00–17:00 МСК" in about
    assert "4 занятия по 2 часа" in about
    assert "5 декабря, суббота, 15:00–17:00 МСК" in about

    prices = t("prices")
    for price in ("24 900 ₽", "29 900 ₽", "44 900 ₽"):
        assert price.replace(" ", NBSP) in prices
    assert "2 ноября включительно" in prices
    assert "Не более 3 мест" in prices
    assert "Оплата через ЮKassa после подтверждения участия" in prices

    assert t("fallback_text") == "Выберите действие в меню или нажмите «Задать вопрос»."
    assert len(t("description")) <= 512
    assert len(t("short_description")) <= 120


def test_unknown_text_key_and_placeholder() -> None:
    with pytest.raises(TextError):
        t("no.such.key")
    with pytest.raises(TextError):
        t("whoami")  # нет chat_id
    assert t("whoami", chat_id=5) == "Ваш chat_id: 5"


def test_placeholders_cover_spec() -> None:
    values = fact_placeholders()
    expected = {
        "practicum_slot": "31 октября, суббота, 15:00–17:00 МСК",
        "practicum_date_short": "31 октября",
        "platform": "Яндекс Телемост",
        "session_hours": "2",
        "course_dates": "7, 14, 21 и 28 ноября",
        "course_time": "15:00–17:00 МСК",
        "demo_day_slot": "5 декабря, суббота, 15:00–17:00 МСК",
        "early_until": "2 ноября включительно",
        "course_page": "https://alexshein.com/ai-native",
        "author_name": "Александр Шеин",
    }
    for key, value in expected.items():
        assert values[key] == value, key
    for key in ("practicum_title", "course_title", "price_early", "price_standard", "price_pro"):
        assert values[key]


def test_course_time_unknown() -> None:
    facts = get_facts().model_copy(deep=True)
    facts.course.start = None
    set_facts(facts)
    assert fact_placeholders()["course_time"] == "время уточняется"
