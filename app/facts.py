"""Факты о практикуме и курсе из `config/facts.yaml` и хелперы форматирования.

Это единственный источник дат, времени, цен и ссылок. Ошибка схемы — исключение
`FactsError` при загрузке (на старте сервиса), чтобы бот не ушёл в прод с битыми фактами.

Время в YAML — строка `"HH:MM"` по часовому поясу `timezone` (Europe/Moscow).
Наружу время отдаётся в UTC (`event_start_utc`) или строкой с пометкой «МСК».
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator

DEFAULT_FACTS_PATH = Path(__file__).resolve().parent.parent / "config" / "facts.yaml"

NBSP = " "
TZ_LABEL = "МСК"

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class FactsError(ValueError):
    """`config/facts.yaml` не найден или не прошёл проверку схемы."""


def _parse_hhmm(value: Any) -> Any:
    if value is None or isinstance(value, time):
        return value
    if isinstance(value, str) and _HHMM.match(value.strip()):
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    raise ValueError(f'время должно быть строкой "HH:MM" в кавычках, получено {value!r}')


Clock = Annotated[time, BeforeValidator(_parse_hhmm)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Author(_Model):
    name: str
    name_genitive: str | None = None
    role: str
    telegram_url: str
    channel_url: str


class Links(_Model):
    course_page: str
    gift_bot: str
    gift_after_practicum: str


class Practicum(_Model):
    title: str
    date: date
    start: Clock
    end: Clock
    free: bool = True
    platform: str
    case: str


class Slot(_Model):
    date: date
    start: Clock | None = None
    end: Clock | None = None


class Tariff(_Model):
    code: str
    name: str
    price_rub: int = Field(gt=0)
    until: date | None = None
    max_seats: int | None = Field(default=None, gt=0)
    includes: list[str] = Field(default_factory=list)
    payment_url: str | None = None


class Course(_Model):
    title: str
    sessions: list[date] = Field(min_length=1)
    start: Clock | None = None
    end: Clock | None = None
    session_hours: int = Field(gt=0)
    demo_day: Slot
    format: list[str] = Field(default_factory=list)
    chat_invite_url: str | None = None
    tariffs: list[Tariff] = Field(min_length=1)

    @field_validator("sessions")
    @classmethod
    def _sorted_sessions(cls, value: list[date]) -> list[date]:
        return sorted(value)

    @field_validator("tariffs")
    @classmethod
    def _unique_codes(cls, value: list[Tariff]) -> list[Tariff]:
        codes = [tariff.code for tariff in value]
        if len(codes) != len(set(codes)):
            raise ValueError("коды тарифов должны быть уникальны")
        return value


class Facts(_Model):
    timezone: str = "Europe/Moscow"
    author: Author
    links: Links
    practicum: Practicum
    course: Course

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"неизвестный часовой пояс {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def tariff(self, code: str) -> Tariff:
        for tariff in self.course.tariffs:
            if tariff.code == code:
                return tariff
        raise KeyError(f"нет тарифа {code!r} в facts.yaml")


# --- загрузка ---------------------------------------------------------------------------------

_facts: Facts | None = None


def load_facts(source: str | Path | dict[str, Any] | None = None) -> Facts:
    """Прочитать и проверить факты. `source` — путь к YAML или уже разобранный словарь."""
    if isinstance(source, dict):
        raw: Any = source
        origin = "<dict>"
    else:
        path = Path(source or os.environ.get("FACTS_PATH") or DEFAULT_FACTS_PATH)
        origin = str(path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FactsError(f"файл фактов не найден: {origin}") from exc
        except yaml.YAMLError as exc:
            raise FactsError(f"{origin}: ошибка YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise FactsError(f"{origin}: ожидается словарь верхнего уровня")
    try:
        return Facts.model_validate(raw)
    except ValidationError as exc:
        lines = [f"{origin}: факты не прошли проверку схемы:"]
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            lines.append(f"  - {loc}: {err['msg']}")
        raise FactsError("\n".join(lines)) from exc


def get_facts() -> Facts:
    """Факты, загруженные один раз за жизнь процесса."""
    global _facts
    if _facts is None:
        _facts = load_facts()
    return _facts


def set_facts(facts: Facts | None) -> None:
    """Подменить факты (тесты) или сбросить кэш (`None` → перечитать файл при следующем вызове)."""
    global _facts
    _facts = facts


# --- время ------------------------------------------------------------------------------------


def _clock(value: time | str | None) -> time | None:
    if value is None or isinstance(value, time):
        return value
    return _parse_hhmm(value)


def event_start_utc(day: date, at: time | str | None) -> datetime | None:
    """Дата и время по часовому поясу фактов (МСК) → aware datetime в UTC.

    Подходит и для начала, и для конца события. `at=None` (время не задано) → `None`.
    """
    clock = _clock(at)
    if clock is None:
        return None
    local = datetime.combine(day, clock, tzinfo=get_facts().tz)
    return local.astimezone(UTC)


def to_local(moment: datetime) -> datetime:
    """UTC (или naive, считается UTC) → время по часовому поясу фактов."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(get_facts().tz)


# --- форматирование ---------------------------------------------------------------------------


def format_date_short(day: date) -> str:
    """«31 октября»."""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


def format_date_ru(day: date) -> str:
    """«31 октября, суббота»."""
    return f"{format_date_short(day)}, {WEEKDAYS[day.weekday()]}"


def format_time_range(start: time | str | None, end: time | str | None) -> str:
    """«15:00–17:00 МСК»; без начала — «время уточняется»."""
    start_clock, end_clock = _clock(start), _clock(end)
    if start_clock is None:
        return "время уточняется"
    if end_clock is None:
        return f"{start_clock:%H:%M} {TZ_LABEL}"
    return f"{start_clock:%H:%M}–{end_clock:%H:%M} {TZ_LABEL}"


def format_slot(day: date, start: time | str | None, end: time | str | None) -> str:
    """«31 октября, суббота, 15:00–17:00 МСК» или «31 октября, суббота, время уточняется»."""
    return f"{format_date_ru(day)}, {format_time_range(start, end)}"


def format_dates_list(days: Iterable[date]) -> str:
    """«7, 14, 21 и 28 ноября»; даты разных месяцев — «31 октября и 7 ноября»."""
    ordered = sorted(days)
    if not ordered:
        return ""
    if len({(d.year, d.month) for d in ordered}) == 1:
        numbers = [str(d.day) for d in ordered]
        head = ", ".join(numbers[:-1])
        joined = f"{head} и {numbers[-1]}" if head else numbers[-1]
        return f"{joined} {MONTHS_GENITIVE[ordered[0].month - 1]}"
    parts = [format_date_short(d) for d in ordered]
    return f"{', '.join(parts[:-1])} и {parts[-1]}"


def format_price(rub: int) -> str:
    """«24 900 ₽» с неразрывными пробелами."""
    return f"{rub:,}".replace(",", NBSP) + f"{NBSP}₽"


# --- тарифы и проверки ------------------------------------------------------------------------


def tariff_available(code: str, now: datetime, taken_seats: int = 0) -> bool:
    """Можно ли сейчас выбрать тариф.

    - тариф с `until` доступен до конца этого дня по МСК включительно;
    - тариф с `max_seats` доступен, пока `taken_seats < max_seats`.
    Неизвестный код — `KeyError`.
    """
    tariff = get_facts().tariff(code)
    if tariff.until is not None and to_local(now).date() > tariff.until:
        return False
    return not (tariff.max_seats is not None and taken_seats >= tariff.max_seats)


def all_prices_rub() -> set[int]:
    """Все суммы в рублях, которые бот вправе называть (для проверки ответов модели)."""
    return {tariff.price_rub for tariff in get_facts().course.tariffs}


def all_dates() -> set[date]:
    """Все даты из фактов: практикум, занятия, демо-день, сроки тарифов."""
    facts = get_facts()
    days = {facts.practicum.date, facts.course.demo_day.date, *facts.course.sessions}
    days.update(tariff.until for tariff in facts.course.tariffs if tariff.until is not None)
    return days
