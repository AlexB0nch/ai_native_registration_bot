"""Ответ на вопрос пользователя: просьба позвать человека → быстрый ответ → модель → проверка.

`answer_question(text)` никогда не бросает исключений: модель упала, не уложилась в
`LLM_TIMEOUT_SECONDS`, вернула не-JSON или ответ не прошёл `guard.check` — пользователь
получает честное «уточню у Александра» (`faq.unknown`), а вопрос уходит владельцу (`handoff=True`).

Текст вопроса и ответа в лог не пишется: только источник ответа и коды причин.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import keyboards
from app.config import get_settings
from app.llm import guard
from app.llm.base import BaseLLMClient
from app.llm.faq import Cta, match_faq, wants_human
from app.llm.prompt import CTA_VALUES, build_system_prompt, build_user_message
from app.llm.stub import StubLLMClient
from app.texts import t

log = logging.getLogger(__name__)

DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com"

# кнопка под ответом: модель выбирает из Cta, «menu» — только для не-вопросов (пусто, команда)
AnswerCta = Literal["practicum", "course_page", "menu", "none"]


@dataclass(frozen=True)
class Answer:
    text: str
    answered: bool
    handoff: bool
    cta: AnswerCta = "none"
    source: str = "llm"  # faq | human | llm | fallback | empty — для логов и тестов


@dataclass(frozen=True)
class ModelReply:
    text: str
    answered: bool
    handoff: bool
    cta: Cta


def get_llm_client() -> BaseLLMClient:
    """Клиент по `LLM_PROVIDER`. Без ключа — заглушка (без сети), вопросы уходят владельцу."""
    settings = get_settings()
    provider = settings.llm_provider
    if provider == "stub":
        return StubLLMClient()
    if not settings.llm_api_key:
        log.warning("LLM_PROVIDER=%s, но LLM_API_KEY не задан — работаю как stub", provider)
        return StubLLMClient()
    if provider == "anthropic":
        from app.llm.anthropic import DEFAULT_BASE_URL, AnthropicLLMClient

        base_url = settings.llm_base_url
        if base_url.rstrip("/") == DEEPSEEK_DEFAULT_URL:
            base_url = DEFAULT_BASE_URL
        return AnthropicLLMClient(
            settings.llm_api_key, settings.llm_model, base_url, timeout=settings.llm_timeout_seconds
        )
    from app.llm.deepseek import DeepSeekLLMClient

    return DeepSeekLLMClient(
        settings.llm_api_key, settings.llm_model, settings.llm_base_url, timeout=settings.llm_timeout_seconds
    )


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def parse_reply(raw: str) -> ModelReply | None:
    """JSON `{"text","answered","handoff","cta"}` → `ModelReply`; не по контракту → `None`.

    JSON может быть обёрнут в ```json …``` или окружён текстом — берётся первый объект `{…}`.
    """
    if not isinstance(raw, str):
        return None
    candidates = [raw.strip()]
    found = re.search(r"\{.*\}", raw, re.DOTALL)
    if found:
        candidates.append(found.group(0))
    data: Any = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    answered = _as_bool(data.get("answered"))
    handoff = _as_bool(data.get("handoff", False))
    if not isinstance(text, str) or answered is None or handoff is None:
        return None
    cta = data.get("cta")
    return ModelReply(text=text.strip(), answered=answered, handoff=handoff, cta=cta if cta in CTA_VALUES else "none")


def _unknown(source: str = "fallback") -> Answer:
    return Answer(t("faq.unknown"), answered=False, handoff=True, cta="none", source=source)


async def _ask_model(client: BaseLLMClient, question: str) -> str | None:
    timeout = get_settings().llm_timeout_seconds
    try:
        return await asyncio.wait_for(client.generate(build_system_prompt(), build_user_message(question)), timeout)
    except TimeoutError:
        log.warning("llm: таймаут %ss (provider=%s)", timeout, client.name)
    except Exception as exc:  # бот не должен замолчать из-за модели
        log.warning("llm: ошибка %s (provider=%s)", type(exc).__name__, client.name)
    return None


async def answer_question(text: str, client: BaseLLMClient | None = None) -> Answer:
    """Ответ на вопрос. Порядок: просьба позвать человека → быстрый ответ → модель → guard."""
    question = (text or "").strip()
    if not question or question.startswith("/"):
        # пустое сообщение или незнакомая команда — не вопрос: меню, без модели и без владельца
        return Answer(t("fallback_text"), answered=False, handoff=False, cta="menu", source="empty")
    if wants_human(question):
        log.info("question: source=human")
        return Answer(t("faq.human"), answered=False, handoff=True, cta="none", source="human")
    faq = match_faq(question)
    if faq is not None:
        log.info("question: source=faq key=%s", faq.key)
        return Answer(faq.text, answered=True, handoff=False, cta=faq.cta, source="faq")

    client = client or get_llm_client()
    raw = await _ask_model(client, question)
    if raw is None:
        return _unknown()
    reply = parse_reply(raw)
    if reply is None:
        log.warning("llm: ответ не по контракту JSON (provider=%s)", client.name)
        return _unknown()
    if not reply.answered:
        log.info("question: source=llm answered=false")
        return _unknown("llm")
    result = guard.check(reply.text)
    if not result.ok:
        log.warning("llm: guard отклонил ответ: %s", ",".join(sorted(set(result.reasons))))
        return _unknown("guard")
    answer_text = result.text
    if reply.handoff:
        answer_text = f"{answer_text}\n\n{t('faq.handoff_more')}"
    log.info("question: source=llm answered=true handoff=%s cta=%s", reply.handoff, reply.cta)
    return Answer(answer_text, answered=True, handoff=reply.handoff, cta=reply.cta, source="llm")


def cta_keyboard(cta: str) -> InlineKeyboardMarkup | None:
    """Кнопка под ответом: практикум → сценарий записи, страница курса → ссылка, menu → главное меню."""
    if cta == "menu":
        return keyboards.main_menu()
    if cta == "practicum":
        button = InlineKeyboardButton(text=t("buttons.practicum"), callback_data=keyboards.MENU_PRACTICUM)
    elif cta == "course_page":
        button = keyboards.course_page_button()
    else:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[button]])
