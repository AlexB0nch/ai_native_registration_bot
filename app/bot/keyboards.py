"""Клавиатуры бота. Подписи кнопок — из `config/texts.yaml`.

Главное меню — inline-кнопки с `callback_data` из констант ниже:
- `MENU_PRACTICUM` обрабатывает practicum.py (TASK-BOT-001),
- `MENU_QUESTION` — questions.py (TASK-BOT-002),
- `MENU_COURSE`, `MENU_PRICES`, `MENU_MAIN` — start.py.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.facts import get_facts
from app.texts import t

MENU_PRACTICUM = "menu:practicum"
MENU_COURSE = "menu:course"
MENU_PRICES = "menu:prices"
MENU_QUESTION = "menu:question"
MENU_MAIN = "menu:main"


def _button(text_key: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t(text_key), callback_data=callback_data)


def course_page_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("buttons.course_page"), url=get_facts().links.course_page)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("menu.practicum", MENU_PRACTICUM)],
            [_button("menu.course", MENU_COURSE)],
            [_button("menu.prices", MENU_PRICES)],
            [_button("menu.question", MENU_QUESTION)],
        ]
    )


def course_about_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [course_page_button()],
            [_button("buttons.practicum", MENU_PRACTICUM)],
        ]
    )


def prices_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [course_page_button()],
            [_button("buttons.practicum", MENU_PRACTICUM)],
        ]
    )


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("buttons.menu", MENU_MAIN)]])


# --- TASK-BOT-001: клавиатуры сценария практикума ---------------------------------------------

# Импорт здесь, а не в шапке: разделы модуля правят параллельные задачи, так меньше конфликтов.
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup  # noqa: E402

PRK_NAME_KEEP = "prk:name_keep"
PRK_EMAIL_KEEP = "prk:email_keep"
PRK_BACK = "prk:back"
PRK_CANCEL = "prk:cancel"
PRK_AGREE = "prk:agree"
PRK_EDIT = "prk:edit"
PRK_ICS = "prk:ics"
PRK_ROLE_PREFIX = "prk:role:"
PRK_ROLE_SKIP = "skip"
PRK_ROLE_CODES = ("consultant", "opex", "transformation", "other")


def _practicum_nav_row(back: bool = True) -> list[InlineKeyboardButton]:
    row = [_button("practicum.buttons.back", PRK_BACK)] if back else []
    return [*row, _button("practicum.buttons.cancel", PRK_CANCEL)]


def practicum_name_kb(known_name: str | None) -> InlineKeyboardMarkup:
    """Шаг 1: «Да, {имя}» (если имя известно) и «Отмена» — предыдущего шага нет."""
    rows: list[list[InlineKeyboardButton]] = []
    if known_name:
        rows.append(
            [InlineKeyboardButton(text=t("practicum.buttons.name_yes", name=known_name), callback_data=PRK_NAME_KEEP)]
        )
    rows.append(_practicum_nav_row(back=False))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def practicum_email_kb(saved_email: str | None) -> InlineKeyboardMarkup:
    """Шаг 2: «Оставить {почта}» (если почта уже введена), «Назад», «Отмена»."""
    rows: list[list[InlineKeyboardButton]] = []
    if saved_email:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("practicum.buttons.email_keep", email=saved_email), callback_data=PRK_EMAIL_KEEP
                )
            ]
        )
    rows.append(_practicum_nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def practicum_phone_reply_kb(saved_phone: str | None) -> ReplyKeyboardMarkup:
    """Шаг 3 (reply-клавиатура): «Поделиться контактом», сохранённый номер, «Пропустить»."""
    rows = [[KeyboardButton(text=t("practicum.buttons.share_contact"), request_contact=True)]]
    if saved_phone:
        rows.append([KeyboardButton(text=saved_phone)])
    rows.append([KeyboardButton(text=t("practicum.buttons.skip"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def practicum_nav_kb() -> InlineKeyboardMarkup:
    """«Назад» и «Отмена» — для шага телефона (его reply-клавиатура не может нести inline-кнопки)."""
    return InlineKeyboardMarkup(inline_keyboard=[_practicum_nav_row()])


def practicum_consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button("practicum.buttons.agree", PRK_AGREE)], _practicum_nav_row()]
    )


def practicum_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("practicum.buttons.calendar", PRK_ICS)],
            [_button("menu.course", MENU_COURSE)],
            [_button("menu.prices", MENU_PRICES)],
        ]
    )


def practicum_already_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("practicum.buttons.edit", PRK_EDIT)],
            [_button("practicum.buttons.calendar", PRK_ICS)],
            [_button("buttons.menu", MENU_MAIN)],
        ]
    )


def practicum_role_kb() -> InlineKeyboardMarkup:
    rows = [[_button(f"practicum.roles.{code}", PRK_ROLE_PREFIX + code)] for code in PRK_ROLE_CODES]
    rows.append([_button("practicum.roles.skip", PRK_ROLE_PREFIX + PRK_ROLE_SKIP)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- TASK-BOT-002: клавиатуры владельца и вопросов --------------------------------------------

# Кнопка «Ответить» под вопросом у владельца: `q:answer:<id>`; «Отмена» ответа: `q:cancel`.
Q_ANSWER_PREFIX = "q:answer:"
Q_CANCEL = "q:cancel"


def question_answer_kb(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button("questions.buttons.answer", f"{Q_ANSWER_PREFIX}{question_id}")]]
    )


def parse_question_answer(data: str | None) -> int | None:
    """`q:answer:17` → 17; всё остальное → None."""
    if not data or not data.startswith(Q_ANSWER_PREFIX):
        return None
    tail = data.removeprefix(Q_ANSWER_PREFIX)
    return int(tail) if tail.isdigit() else None


def answer_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("questions.buttons.cancel", Q_CANCEL)]])


def owner_answer_kb() -> InlineKeyboardMarkup:
    """Под ответом владельца пользователю — «Записаться на практикум»."""
    return InlineKeyboardMarkup(inline_keyboard=[[_button("buttons.practicum", MENU_PRACTICUM)]])
