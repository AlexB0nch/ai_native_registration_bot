"""Заглушка модели (`LLM_PROVIDER=stub`): без сети, на любой вопрос «не знаю» → вопрос владельцу."""

from __future__ import annotations

import json

from app.llm.base import BaseLLMClient


class StubLLMClient(BaseLLMClient):
    name = "stub"

    async def generate(self, system: str, user: str) -> str:
        return json.dumps({"text": "", "answered": False, "handoff": True, "cta": "none"})
