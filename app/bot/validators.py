"""Проверка и нормализация ввода пользователя: имя, почта, телефон, «похоже на вопрос».

Каждая функция возвращает нормализованное значение или `None`, если ввод не подходит.
"""

from __future__ import annotations

import re

import phonenumbers
from email_validator import EmailNotValidError, validate_email

NAME_MAX_LEN = 100
NAME_MAX_WORDS = 4
DEFAULT_PHONE_REGION = "RU"
QUESTION_MIN_WORDS = 4

# Ссылки в имени: схема, www., t.me/…, @username, «что-то.домен».
_LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|@\w|\b[\w-]+\.(ru|рф|com|org|net|io|me|info|biz|su|pro|online|site)\b)",
    re.IGNORECASE,
)
_PHONE_CHARS_RE = re.compile(r"^\+?[\d\s().\-]+$")


def normalize_name(text: str | None) -> str | None:
    """Имя: 1–100 символов, не больше четырёх слов, без ссылок и вопросительного знака.

    Лишние пробелы схлопываются.
    """
    value = " ".join((text or "").split())
    if not value or len(value) > NAME_MAX_LEN:
        return None
    if "?" in value or len(value.split()) > NAME_MAX_WORDS or _LINK_RE.search(value):
        return None
    return value


def normalize_email(text: str | None) -> str | None:
    """Почта по формату (`email-validator` без проверки домена в DNS), в нижнем регистре."""
    value = (text or "").strip()
    if not value or " " in value:
        return None
    try:
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


def normalize_phone(text: str | None, region: str = DEFAULT_PHONE_REGION) -> str | None:
    """Телефон → E.164 (`+79123456789`). Регион по умолчанию RU: `8XXXXXXXXXX` → `+7XXXXXXXXXX`."""
    value = (text or "").strip()
    if not value or not _PHONE_CHARS_RE.match(value):
        return None
    try:
        number = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_contact_phone(phone_number: str) -> str:
    """Телефон из контакта Telegram (бывает без `+`) → E.164; если не разобрался — `+` и цифры."""
    digits = re.sub(r"\D", "", phone_number)
    return normalize_phone(f"+{digits}") or f"+{digits}"


def looks_like_question(text: str | None) -> bool:
    """Текст похож на вопрос, а не на ответ на шаг: есть `?` или в нём не меньше четырёх слов."""
    value = (text or "").strip()
    return "?" in value or len(value.split()) >= QUESTION_MIN_WORDS
