"""Тексты бота из `config/texts.yaml`.

`t("menu.practicum")` возвращает строку с подставленными фактами (`fact_placeholders()`)
и переданными именованными аргументами. Неизвестный ключ или незаполненная подстановка —
исключение `TextError` (ловится тестами, а не пользователем).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.facts import (
    format_date_ru,
    format_date_short,
    format_dates_list,
    format_price,
    format_slot,
    format_time_range,
    get_facts,
)

DEFAULT_TEXTS_PATH = Path(__file__).resolve().parent.parent / "config" / "texts.yaml"

BULLET = "•"


class TextError(KeyError):
    """Нет такого ключа в texts.yaml или не хватает значения для подстановки."""


_texts: dict[str, Any] | None = None


def load_texts(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path or os.environ.get("TEXTS_PATH") or DEFAULT_TEXTS_PATH)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TextError(f"{path}: ожидается словарь верхнего уровня")
    return data


def get_texts() -> dict[str, Any]:
    global _texts
    if _texts is None:
        _texts = load_texts()
    return _texts


def set_texts(texts: dict[str, Any] | None) -> None:
    """Подменить тексты (тесты) или сбросить кэш (`None`)."""
    global _texts
    _texts = texts


def _bullets(items: list[str]) -> str:
    return "\n".join(f"{BULLET} {item}" for item in items)


def fact_placeholders() -> dict[str, str]:
    """Все подстановки из фактов (и PRIVACY_URL из окружения), уже отформатированные по-русски."""
    facts = get_facts()
    practicum, course = facts.practicum, facts.course
    values: dict[str, str] = {
        "author_name": facts.author.name,
        "author_name_genitive": facts.author.name_genitive or facts.author.name,
        "author_role": facts.author.role,
        "author_telegram": facts.author.telegram_url,
        "channel_url": facts.author.channel_url,
        "practicum_title": practicum.title,
        "practicum_slot": format_slot(practicum.date, practicum.start, practicum.end),
        "practicum_date": format_date_ru(practicum.date),
        "practicum_date_short": format_date_short(practicum.date),
        "practicum_time": format_time_range(practicum.start, practicum.end),
        "platform": practicum.platform,
        "practicum_case": practicum.case,
        "course_title": course.title,
        "course_dates": format_dates_list(course.sessions),
        "course_time": format_time_range(course.start, course.end),
        "course_start_date": format_date_short(course.sessions[0]),
        "sessions_count": str(len(course.sessions)),
        "session_hours": str(course.session_hours),
        "demo_day_slot": format_slot(course.demo_day.date, course.demo_day.start, course.demo_day.end),
        "demo_day_date_short": format_date_short(course.demo_day.date),
        "course_format": ", ".join(course.format),
        "course_format_list": _bullets(course.format),
        "course_page": facts.links.course_page,
        "gift_bot": facts.links.gift_bot,
        "gift_after_practicum": facts.links.gift_after_practicum,
        "privacy_url": get_settings().privacy_url,
    }
    for tariff in course.tariffs:
        values[f"price_{tariff.code}"] = format_price(tariff.price_rub)
        values[f"{tariff.code}_name"] = tariff.name
        values[f"{tariff.code}_includes"] = _bullets(tariff.includes)
        if tariff.until is not None:
            values[f"{tariff.code}_until"] = f"{format_date_short(tariff.until)} включительно"
        if tariff.max_seats is not None:
            values[f"{tariff.code}_max_seats"] = str(tariff.max_seats)
    return values


def raw(key: str) -> Any:
    """Значение по ключу без подстановок (строка, список или раздел)."""
    node: Any = get_texts()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise TextError(f"нет текста {key!r} в config/texts.yaml")
        node = node[part]
    return node


def has(key: str) -> bool:
    try:
        raw(key)
    except TextError:
        return False
    return True


def t(key: str, **kwargs: Any) -> str:
    """Текст по ключу с подстановкой фактов и `kwargs` (kwargs важнее фактов)."""
    template = raw(key)
    if not isinstance(template, str):
        raise TextError(f"{key!r} — не строка, а {type(template).__name__}")
    values = {**fact_placeholders(), **{k: "" if v is None else v for k, v in kwargs.items()}}
    try:
        return template.format_map(values)
    except KeyError as exc:
        raise TextError(f"{key!r}: нет значения для подстановки {exc}") from exc
