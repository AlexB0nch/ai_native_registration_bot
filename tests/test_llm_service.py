"""TASK-LLM-001: сервис ответов, клиенты модели, ответ в чате (A1–A6). Сеть не используется."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest

from app.bot import keyboards
from app.bot.handlers import questions
from app.config import get_settings
from app.facts import NBSP
from app.llm import service
from app.llm.anthropic import AnthropicLLMClient
from app.llm.base import BaseLLMClient, LLMError
from app.llm.deepseek import DeepSeekLLMClient
from app.llm.service import answer_question, get_llm_client, parse_reply
from app.llm.stub import StubLLMClient
from app.texts import t
from tests.helpers import USER_ID, TgHarness, inline_buttons

SECRET_QUESTION = "Можно ли оплатить курс от юрлица Ромашка, мой телефон +79990001122?"


class FakeLLM(BaseLLMClient):
    name = "fake"

    def __init__(self, reply: str | Exception | None = None, delay: float = 0) -> None:
        self.reply = reply
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reply, Exception):
            raise self.reply
        assert self.reply is not None, "модель не должна вызываться"
        return self.reply


def model_json(text: str, answered: bool = True, handoff: bool = False, cta: str = "none") -> str:
    return json.dumps({"text": text, "answered": answered, "handoff": handoff, "cta": cta}, ensure_ascii=False)


# --- сервис ------------------------------------------------------------------------------------


async def test_faq_does_not_call_model() -> None:
    llm = FakeLLM(None)
    answer = await answer_question("Сколько стоит курс?", client=llm)
    assert answer.source == "faq"
    assert answer.answered and not answer.handoff
    assert answer.cta == "course_page"
    assert f"24{NBSP}900{NBSP}₽" in answer.text
    assert llm.calls == []


async def test_human_request_goes_to_owner_without_model() -> None:
    llm = FakeLLM(None)
    answer = await answer_question("Позовите человека, пожалуйста", client=llm)
    assert (answer.answered, answer.handoff, answer.source) == (False, True, "human")
    assert answer.text == t("faq.human")
    assert llm.calls == []


async def test_model_answer_passes() -> None:
    llm = FakeLLM(model_json("Да, можно взять свой рабочий кейс или общий учебный.", cta="practicum"))
    answer = await answer_question("Можно взять свой кейс?", client=llm)
    assert answer == service.Answer(
        "Да, можно взять свой рабочий кейс или общий учебный.", True, False, "practicum", "llm"
    )
    system, user = llm.calls[0]
    assert "=== ФАКТЫ ===" in system
    assert json.loads(user) == {"вопрос_пользователя": "Можно взять свой кейс?"}


async def test_model_partial_answer_with_handoff() -> None:
    llm = FakeLLM(model_json("Записи занятий будут.", handoff=True))
    answer = await answer_question("Сколько хранятся записи?", client=llm)
    assert answer.answered and answer.handoff
    assert answer.text.startswith("Записи занятий будут.")
    assert t("faq.handoff_more") in answer.text


async def test_a5_guard_rejects_wrong_price() -> None:
    llm = FakeLLM(model_json("Курс стоит 19 900 ₽, приходите.", cta="course_page"))
    answer = await answer_question("А есть скидка для студентов?", client=llm)
    assert (answer.answered, answer.handoff, answer.source) == (False, True, "guard")
    assert answer.text == t("faq.unknown")
    assert "19" not in answer.text


async def test_model_says_unknown() -> None:
    answer = await answer_question("Будет ли сертификат?", client=FakeLLM(model_json("", answered=False)))
    assert (answer.answered, answer.handoff) == (False, True)
    assert answer.text == t("faq.unknown")


@pytest.mark.parametrize(
    "reply",
    [
        RuntimeError("boom"),
        httpx.ConnectError("no route"),
        httpx.ReadTimeout("slow"),
        LLMError("HTTP 500"),
    ],
)
async def test_a6_model_failure(reply: Exception) -> None:
    answer = await answer_question("Будет ли сертификат?", client=FakeLLM(reply))
    assert (answer.answered, answer.handoff, answer.text) == (False, True, t("faq.unknown"))


async def test_a6_model_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    answer = await answer_question("Будет ли сертификат?", client=FakeLLM(model_json("Да."), delay=1))
    assert (answer.answered, answer.handoff, answer.text) == (False, True, t("faq.unknown"))


@pytest.mark.parametrize("raw", ["не JSON", "[]", '{"text": "x"}', '{"text": 1, "answered": true}', ""])
async def test_non_contract_reply(raw: str) -> None:
    answer = await answer_question("Будет ли сертификат?", client=FakeLLM(raw))
    assert (answer.answered, answer.handoff, answer.text) == (False, True, t("faq.unknown"))


def test_parse_reply_variants() -> None:
    wrapped = "```json\n" + model_json("Да.", cta="course_page") + "\n```"
    assert parse_reply(wrapped) == service.ModelReply("Да.", True, False, "course_page")
    odd = parse_reply('{"text": "Да.", "answered": "true", "handoff": "false", "cta": "telegram"}')
    assert odd == service.ModelReply("Да.", True, False, "none")


@pytest.mark.parametrize("text", ["   ", "/unknown", "/stats"])
async def test_empty_question_or_command(text: str) -> None:
    llm = FakeLLM(None)
    answer = await answer_question(text, client=llm)
    assert (answer.answered, answer.handoff, answer.cta) == (False, False, "menu")
    assert answer.text == t("fallback_text")
    assert llm.calls == []


async def test_stub_provider_is_default_and_offline() -> None:
    client = get_llm_client()
    assert isinstance(client, StubLLMClient)
    answer = await answer_question("Будет ли сертификат?")
    assert (answer.answered, answer.handoff, answer.text) == (False, True, t("faq.unknown"))


async def test_question_text_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await answer_question(SECRET_QUESTION, client=FakeLLM(model_json("Курс стоит 1 ₽.")))
    await answer_question(SECRET_QUESTION, client=FakeLLM(RuntimeError(SECRET_QUESTION)))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Ромашка" not in logged
    assert "+7999" not in logged


# --- выбор клиента -----------------------------------------------------------------------------


def _configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_get_client_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, LLM_PROVIDER="deepseek", LLM_API_KEY="test-key", LLM_TIMEOUT_SECONDS="7")
    client = get_llm_client()
    assert isinstance(client, DeepSeekLLMClient)
    assert client.base_url == "https://api.deepseek.com"
    assert client.timeout == 7


def test_get_client_anthropic_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, LLM_PROVIDER="anthropic", LLM_API_KEY="test-key", LLM_MODEL="test-model")
    client = get_llm_client()
    assert isinstance(client, AnthropicLLMClient)
    assert client.base_url == "https://api.anthropic.com"
    assert client.model == "test-model"


def test_get_client_without_key_is_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, LLM_PROVIDER="deepseek", LLM_API_KEY="")
    assert isinstance(get_llm_client(), StubLLMClient)


# --- HTTP-клиенты (httpx.MockTransport) --------------------------------------------------------


async def test_deepseek_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": model_json("Ответ.")}}]})

    client = DeepSeekLLMClient("test-key", "deepseek-chat", transport=httpx.MockTransport(handler))
    raw = await client.generate("SYSTEM", "USER")
    assert json.loads(raw)["text"] == "Ответ."
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}]


async def test_deepseek_http_error_and_bad_body() -> None:
    error = DeepSeekLLMClient("k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="oops")))
    with pytest.raises(LLMError, match="500"):
        await error.generate("s", "u")
    bad = DeepSeekLLMClient("k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"x": 1})))
    with pytest.raises(LLMError):
        await bad.generate("s", "u")
    with pytest.raises(LLMError):
        DeepSeekLLMClient("", "m")


async def test_deepseek_timeout_through_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = DeepSeekLLMClient("k", "m", transport=httpx.MockTransport(handler))
    answer = await answer_question("Будет ли сертификат?", client=client)
    assert (answer.answered, answer.handoff, answer.text) == (False, True, t("faq.unknown"))


async def test_anthropic_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": model_json("Ответ.")}]})

    client = AnthropicLLMClient("test-key", "test-model", transport=httpx.MockTransport(handler))
    raw = await client.generate("SYSTEM", "USER")
    assert json.loads(raw)["text"] == "Ответ."
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["headers"]["anthropic-version"]
    body = seen["body"]
    assert body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["model"] == "test-model"

    failing = AnthropicLLMClient("k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(LLMError, match="429"):
        await failing.generate("s", "u")


# --- ответ в чате (answer_question_inline) ------------------------------------------------------


@pytest.fixture
def escalations(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_escalate(message: Any, person: Any, text: str, bot_answer: str | None = None) -> None:
        calls.append({"person": person, "text": text, "bot_answer": bot_answer})

    monkeypatch.setattr(questions, "escalate", fake_escalate)
    return calls


def _use_llm(monkeypatch: pytest.MonkeyPatch, llm: BaseLLMClient) -> None:
    monkeypatch.setattr(service, "get_llm_client", lambda: llm)


async def test_chat_faq_prices_with_course_page_button(tg: TgHarness, escalations: list[dict[str, Any]]) -> None:
    await tg.send("Сколько стоит курс?")
    message = tg.session.last_message(USER_ID)
    assert f"44{NBSP}900{NBSP}₽" in message["text"]
    buttons = inline_buttons(message)
    assert buttons[0]["url"] == "https://alexshein.com/ai-native"
    assert escalations == []


async def test_chat_faq_dates_with_practicum_button(tg: TgHarness, escalations: list[dict[str, Any]]) -> None:
    await tg.send("Когда начало?")
    message = tg.session.last_message(USER_ID)
    assert "7 ноября" in message["text"]
    assert "31 октября" in message["text"]
    assert inline_buttons(message)[0]["callback_data"] == keyboards.MENU_PRACTICUM
    assert escalations == []


async def test_chat_a5_wrong_price_goes_to_owner(
    tg: TgHarness, escalations: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_llm(monkeypatch, FakeLLM(model_json("Курс стоит 19 900 ₽.")))
    await tg.send("А для студентов дешевле?")
    message = tg.session.last_message(USER_ID)
    assert message["text"] == "Точного ответа у меня нет — передал вопрос Александру, он ответит здесь же."
    assert "reply_markup" not in message
    assert len(escalations) == 1
    assert escalations[0]["text"] == "А для студентов дешевле?"
    assert escalations[0]["bot_answer"] is None
    assert escalations[0]["person"].telegram_id == USER_ID


async def test_chat_a6_model_down(
    tg: TgHarness, escalations: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_llm(monkeypatch, FakeLLM(httpx.ConnectError("down")))
    await tg.send("Будет ли сертификат?")
    assert tg.session.last_text(USER_ID) == t("faq.unknown")
    assert len(escalations) == 1


async def test_chat_model_answer_with_cta(
    tg: TgHarness, escalations: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_llm(monkeypatch, FakeLLM(model_json("Да, есть общий учебный кейс потока.", cta="practicum")))
    await tg.send("У меня нет своего проекта, что делать?")
    message = tg.session.last_message(USER_ID)
    assert message["text"] == "Да, есть общий учебный кейс потока."
    assert inline_buttons(message)[0]["callback_data"] == keyboards.MENU_PRACTICUM
    assert escalations == []


async def test_chat_partial_answer_escalates_with_bot_answer(
    tg: TgHarness, escalations: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_llm(monkeypatch, FakeLLM(model_json("Записи занятий будут.", handoff=True)))
    await tg.send("Сколько хранятся записи?")
    assert len(escalations) == 1
    assert escalations[0]["bot_answer"].startswith("Записи занятий будут.")


async def test_chat_escalate_failure_does_not_break(tg: TgHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(questions, "escalate", broken)
    await tg.send("Позовите человека")
    assert tg.session.last_text(USER_ID) == t("faq.human")
