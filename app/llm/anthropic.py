"""Anthropic Messages API через httpx, без SDK.

`LLM_BASE_URL` = `https://api.anthropic.com` (если оставлен адрес DeepSeek по умолчанию,
сервис подставляет этот), заголовки `x-api-key` и `anthropic-version`. Модель — `LLM_MODEL`.
JSON-режима у API нет: формат ответа задаёт системный промпт, сервис достаёт JSON из текста.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.llm.base import MAX_TOKENS, TEMPERATURE, BaseLLMClient, LLMError

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicLLMClient(BaseLLMClient):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("LLM_API_KEY не задан")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def generate(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/v1/messages", headers=headers, json=payload)
        if response.status_code != 200:
            raise LLMError(f"Anthropic HTTP {response.status_code}")
        try:
            blocks = response.json()["content"]
            text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMError("Anthropic: неожиданный формат ответа") from exc
        if not text.strip():
            raise LLMError("Anthropic: пустой ответ")
        return text
