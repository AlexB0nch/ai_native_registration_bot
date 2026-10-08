"""Общий контракт клиентов модели.

`generate(system, user)` возвращает сырой ответ модели — строку, в которой ожидается JSON
`{"text", "answered", "handoff", "cta"}` (разбирает `app/llm/service.py`). Ошибка сети
или протокола — `LLMError` (или исключение httpx); сервис ловит всё и передаёт вопрос владельцу.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

TEMPERATURE = 0.2
MAX_TOKENS = 800


class LLMError(RuntimeError):
    """Модель недоступна или ответила не по протоколу. Сообщение — без текста вопроса."""


class BaseLLMClient(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, system: str, user: str) -> str:
        """Системный промпт + сообщение пользователя → сырой ответ модели (JSON-строка)."""
