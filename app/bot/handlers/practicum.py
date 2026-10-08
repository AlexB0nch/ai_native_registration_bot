"""Сценарий записи на практикум — TASK-BOT-001.

Заглушка CORE: пустой `router` и точка входа `start_practicum`. TASK-BOT-001 добавляет
сюда хендлеры кнопки меню (`keyboards.MENU_PRACTICUM`), команды `/practicum` и шагов FSM.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router(name="practicum")

# Ключ в FSM data, куда start.py кладёт метку `/start` (или None) перед вызовом start_practicum.
START_SOURCE_KEY = "start_source"


async def start_practicum(message: Message, state: FSMContext) -> None:
    """Начать сценарий записи на практикум.

    Вызывается из `start.py` для `/start` со сценарием `practicum`/`site_practicum` —
    уже после приветствия с меню. Метка `/start` лежит в `await state.get_data()`
    под ключом `START_SOURCE_KEY`.

    Внимание: если вызывать из callback-хендлера с `callback.message`, то
    `message.from_user` — это бот; id пользователя берите из `state.key.user_id`.

    Заглушка: ничего не делает — пользователь уже видит приветствие и меню.
    """
    return None
