"""Проверка ответа модели перед отправкой пользователю.

`check(text)` → `GuardResult(ok, reasons, text)`:
- каждая сумма в рублях («24 900 ₽», «24 900 ₽», «24900 руб.», «25 тыс. руб.») — из `all_prices_rub()`;
- каждая дата («31 октября», «7, 14 и 21 ноября», «7–28 ноября», «31.10») — из `all_dates()`;
- каждое время «ЧЧ:ММ» — из фактов (начало и конец практикума, занятий, демо-дня);
- каждая ссылка — из фактов;
- нет запрещённых слов (тон из CLAUDE.md);
- длина ≤ 600 знаков: длиннее — обрезается по предложению, `ok` от этого не меняется.

Причины (`reasons`) — короткие коды без текста вопроса: их можно писать в лог.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time

from app.facts import MONTHS_GENITIVE, all_dates, all_prices_rub, get_facts

MAX_LEN = 600

_SPACE = r"[   ]"  # обычный, неразрывный и узкий неразрывный пробелы
_NUMBER = rf"\d{{1,3}}(?:{_SPACE}\d{{3}})+|\d+"
PRICE_RE = re.compile(
    rf"(?<![\d.,])(?P<num>{_NUMBER})(?:[.,](?P<frac>\d+))?\s*(?P<thousand>тыс\.?|тысяч\w*|к\b)?\s*"
    r"(?:₽|руб|р\.|rub\b)",
    re.IGNORECASE,
)
_MONTHS = "|".join(MONTHS_GENITIVE)
DATE_RE = re.compile(
    rf"(?<!\d)(?P<days>\d{{1,2}}(?:(?:,\s*|\s+и\s+|[–-])\d{{1,2}})*)\s+(?P<month>{_MONTHS})(?:\s+(?P<year>20\d\d))?",
    re.IGNORECASE,
)
NUMERIC_DATE_RE = re.compile(r"(?<![\d.])(?P<day>\d{1,2})\.(?P<month>\d{2})(?:\.(?P<year>\d{4}|\d{2}))?(?![\d])")
TIME_RE = re.compile(r"(?<!\d)(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>«»\"')\]]+", re.IGNORECASE)
FORBIDDEN = ("уникальн", "успейте", "прорыв", "революцион", "гарантир", "вебинар", "%")


@dataclass
class GuardResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    text: str = ""


def _amount(match: re.Match[str]) -> float:
    value = float(re.sub(_SPACE, "", match.group("num")))
    if match.group("frac"):
        value += float("0." + match.group("frac"))
    if match.group("thousand"):
        value *= 1000
    return round(value, 2)


def _check_prices(text: str) -> list[str]:
    allowed = all_prices_rub()
    reasons = []
    for match in PRICE_RE.finditer(text):
        amount = _amount(match)
        if amount not in allowed:
            reasons.append(f"price:{amount:g}")
    return reasons


def _check_dates(text: str) -> list[str]:
    known = all_dates()
    known_days = {(d.day, d.month) for d in known}
    known_full = {(d.day, d.month, d.year) for d in known}
    reasons = []
    for match in DATE_RE.finditer(text):
        month = MONTHS_GENITIVE.index(match.group("month").lower()) + 1
        year = int(match.group("year")) if match.group("year") else None
        for day_str in re.split(r",\s*|\s+и\s+|[–-]", match.group("days")):
            day = int(day_str)
            ok = (day, month, year) in known_full if year else (day, month) in known_days
            if not ok:
                reasons.append(f"date:{day:02d}.{month:02d}")
    for match in NUMERIC_DATE_RE.finditer(text):
        day, month = int(match.group("day")), int(match.group("month"))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        if (day, month) not in known_days:
            reasons.append(f"date:{day:02d}.{month:02d}")
    return reasons


def _allowed_times() -> set[time]:
    facts = get_facts()
    values = {
        facts.practicum.start,
        facts.practicum.end,
        facts.course.start,
        facts.course.end,
        facts.course.demo_day.start,
        facts.course.demo_day.end,
    }
    return {value for value in values if value is not None}


def _check_times(text: str) -> list[str]:
    allowed = _allowed_times()
    return [
        f"time:{match.group(0)}"
        for match in TIME_RE.finditer(text)
        if time(int(match.group("h")), int(match.group("m"))) not in allowed
    ]


def _allowed_urls() -> set[str]:
    facts = get_facts()
    urls = {
        facts.author.telegram_url,
        facts.author.channel_url,
        facts.links.course_page,
        facts.links.gift_bot,
        facts.links.gift_after_practicum,
    }
    if facts.course.chat_invite_url:
        urls.add(facts.course.chat_invite_url)
    return {url.rstrip("/").lower() for url in urls}


def _check_urls(text: str) -> list[str]:
    allowed = _allowed_urls()
    return ["url" for match in URL_RE.finditer(text) if match.group(0).rstrip(".,;:!?/").lower() not in allowed]


def _check_words(text: str) -> list[str]:
    low = text.lower()
    return [f"word:{word}" for word in FORBIDDEN if word in low]


def truncate(text: str, limit: int = MAX_LEN) -> str:
    """Обрезать по последнему целому предложению; без предложений — по слову с «…»."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?…](?=\s|$)", cut)]
    if ends and ends[-1] >= limit // 3:
        return cut[: ends[-1]].strip()
    space = cut.rfind(" ", 0, limit - 1)
    return (cut[:space] if space > 0 else cut[: limit - 1]).rstrip(" ,;:—-") + "…"


def check(text: str) -> GuardResult:
    text = (text or "").strip()
    reasons: list[str] = []
    if not text:
        reasons.append("empty")
    reasons += _check_prices(text)
    reasons += _check_dates(text)
    reasons += _check_times(text)
    reasons += _check_urls(text)
    reasons += _check_words(text)
    return GuardResult(ok=not reasons, reasons=reasons, text=truncate(text))
