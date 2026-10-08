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


# --- TASK-BOT-002: клавиатуры владельца и вопросов --------------------------------------------
