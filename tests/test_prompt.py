"""TASK-LLM-001: системный промпт (`app/llm/prompt.py`)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.facts import MONTHS_GENITIVE, NBSP, get_facts, set_facts
from app.llm.prompt import (
    FACTS_HEADER,
    KNOWLEDGE_HEADER,
    KNOWLEDGE_LIMIT,
    KnowledgeDoc,
    build_system_prompt,
    build_user_message,
    facts_block,
    knowledge_block,
    load_knowledge,
)

MONTHS = "|".join(MONTHS_GENITIVE)
PRICE_RE = re.compile(r"\d[\d\s  ]*\s?(₽|руб)")
DATE_RE = re.compile(rf"\d{{1,2}}\s+({MONTHS})")


def _split(prompt: str) -> tuple[str, str, str]:
    rules, rest = prompt.split(FACTS_HEADER, 1)
    facts, knowledge = rest.split(KNOWLEDGE_HEADER, 1)
    return rules, facts, knowledge


def test_prompt_structure_and_rules() -> None:
    prompt = build_system_prompt()
    rules, _facts, knowledge = _split(prompt)
    assert "Александр Шеин" in rules
    assert "на «вы»" in rules
    assert "только «практикум»" in rules
    assert "answered=false" in rules
    for field in ('"text"', '"answered"', '"handoff"', '"cta"', '"practicum"', '"course_page"', '"none"'):
        assert field in rules, field
    assert "JSON" in rules
    assert "только из блока ФАКТЫ" in rules
    assert "Почему чат с ИИ ещё не система работы" in knowledge
    assert "Собрано scripts/build_knowledge.py" not in knowledge


def test_no_prices_or_dates_outside_facts_block() -> None:
    rules, facts, knowledge = _split(build_system_prompt())
    for name, part in (("rules", rules), ("knowledge", knowledge)):
        assert not PRICE_RE.search(part), name
        assert not DATE_RE.search(part), name
        assert not re.search(r"\d{1,2}:\d{2}", part), name
    # а в блоке ФАКТЫ они есть
    assert PRICE_RE.search(facts)
    assert DATE_RE.search(facts)


def test_facts_block_from_facts_yaml() -> None:
    block = facts_block()
    assert block.startswith(FACTS_HEADER)
    assert "31 октября, суббота, 15:00–17:00 МСК" in block
    assert "Яндекс Телемост" in block
    assert "7 ноября, суббота, 15:00–17:00 МСК" in block
    assert "28 ноября, суббота, 15:00–17:00 МСК" in block
    assert "5 декабря, суббота, 15:00–17:00 МСК" in block
    for price in ("24 900 ₽", "29 900 ₽", "44 900 ₽"):
        assert price.replace(" ", NBSP) in block
    assert "до 2 ноября включительно" in block
    assert "не более 3 мест" in block
    assert "https://alexshein.com/ai-native" in block


def test_facts_block_follows_facts() -> None:
    facts = get_facts().model_copy(deep=True)
    facts.course.tariffs[2].price_rub = 49900
    set_facts(facts)
    assert f"49{NBSP}900{NBSP}₽" in facts_block()


def test_knowledge_order_and_limit(tmp_path: Path) -> None:
    (tmp_path / "author.md").write_text("# Ведущий\n\n> Собрано scripts/build_knowledge.py из x\n\nАвтор.", "utf-8")
    (tmp_path / "faq.md").write_text("# Вопросы\n\nОтвет.", "utf-8")
    (tmp_path / "zzz.md").write_text("# Другое\n\nТекст.", "utf-8")
    docs = load_knowledge(tmp_path)
    assert [d.name for d in docs] == ["faq.md", "author.md", "zzz.md"]
    assert "Собрано" not in docs[1].content

    big = [KnowledgeDoc("a.md", "А" * 300), KnowledgeDoc("b.md", ("Б" * 290 + "\n\n") * 5)]
    block = knowledge_block(big, limit=1000)
    assert len(block) <= 1000
    assert block.startswith("А" * 300)
    assert "Б" in block  # второй документ обрезан по абзацу, а не выброшен

    full = knowledge_block(load_knowledge())
    assert 0 < len(full) <= KNOWLEDGE_LIMIT


def test_missing_knowledge_dir(tmp_path: Path) -> None:
    assert load_knowledge(tmp_path / "nope") == []
    prompt = build_system_prompt(knowledge_dir=tmp_path / "nope")
    assert prompt.rstrip().endswith("(пусто)")


def test_user_message_is_json_and_limited() -> None:
    message = build_user_message("  Сколько длится курс?  ")
    assert json.loads(message) == {"вопрос_пользователя": "Сколько длится курс?"}
    long = json.loads(build_user_message("а" * 5000))["вопрос_пользователя"]
    assert len(long) == 2000
