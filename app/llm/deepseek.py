"""DeepSeek (OpenAI-совместимый `/chat/completions`) через httpx.

По образцу `app/integrations/llm/deepseek.py` из AlexB0nch/pppp: JSON-режим
(`response_format: json_object`), `temperature` 0.2, таймаут из `LLM_TIMEOUT_SECONDS`.
Ключ — только из окружения (`LLM_API_KEY`).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.llm.base import MAX_TOKENS, TEMPERATURE, BaseLLMClient, LLMError

DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekLLMClient(BaseLLMClient):
    name = "deepseek"

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
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code != 200:
            raise LLMError(f"DeepSeek HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("DeepSeek: неожиданный формат ответа") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("DeepSeek: пустой ответ")
        return content
