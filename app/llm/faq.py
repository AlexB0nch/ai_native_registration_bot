"""Быстрые ответы без модели: цены, даты, «нужно ли программировать», практикум.

Совпадение — по корням слов в нижнем регистре (ё → е). Тексты — `faq.*` в `config/texts.yaml`,
с подстановкой фактов, поэтому ответы на вопросы о ценах и датах всегда совпадают с `facts.yaml`.
Порядок проверки важен: «Сколько стоит практикум?» — это цены (там сказано, что практикум бесплатный),
«Когда практикум?» — даты.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.texts import t

Cta = Literal["practicum", "course_page", "none"]


@dataclass(frozen=True)
class FaqRule:
    key: str  # ключ текста в texts.yaml
    cta: Cta
    pattern: re.Pattern[str]
    exclude: re.Pattern[str] | None = None


@dataclass(frozen=True)
class FaqAnswer:
    key: str
    text: str
    cta: Cta


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


RULES: tuple[FaqRule, ...] = (
    FaqRule(
        "faq.prices",
        "course_page",
        _re(r"\bцен[аыуеой]?\b|\bстоимост|сколько\s+стоит|\bстоит\s+курс|\bпочем|\bтариф|\bпрайс"),
        # «сколько стоят подписки/сервисы» — это не цена курса, отвечает модель по базе знаний
        exclude=_re(r"подписк|сервис|сервер|нейросет|модел|chatgpt|claude|возврат|верн"),
    ),
    FaqRule(
        "faq.dates",
        "practicum",
        _re(
            r"\bкогда\b|\bдат[аыуе]?\b|\bначал[оаеу]\b|\bначина|\bстарт|\bрасписани|во\s+сколько"
            r"|в\s+какое\s+время|какого\s+числа|\bпо\s+каким\s+дням"
        ),
        exclude=_re(r"ответ|запис[ьи]\s+(?:будет|занят|практикум)|доступ|оплат|деньг"),
    ),
    FaqRule(
        "faq.no_coding",
        "practicum",
        _re(r"программир|программист|\bкод\b|\bкода\b|\bкодить|\bкодинг|разработчик|\bнавык\w*\s+програм"),
    ),
    FaqRule(
        "faq.practicum",
        "practicum",
        _re(r"практикум"),
    ),
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def match_faq(text: str) -> FaqAnswer | None:
    """Быстрый ответ на вопрос или `None` (тогда отвечает модель)."""
    norm = _normalize(text)
    if not norm:
        return None
    for rule in RULES:
        if rule.pattern.search(norm) and not (rule.exclude and rule.exclude.search(norm)):
            return FaqAnswer(rule.key, t(rule.key), rule.cta)
    return None


HUMAN_RE = _re(
    r"\bоператор|\bменеджер|\bпозов|\bпозвать|\bсвяза(?:ть|ться)|\bсвяжи|\bпоговорить|\bсоедини|\bпередай"
    r"|(?:живо\w*|реальн\w*|нужен|с)\s+человек"
)


def wants_human(text: str) -> bool:
    """Просьба позвать человека («позовите», «оператор», «связаться», «с человеком»).

    Такой вопрос сразу уходит владельцу, модель не нужна. Само имя «Александр» не считается
    просьбой: «Кто такой Александр Шеин?» — обычный вопрос о ведущем.
    """
    return bool(HUMAN_RE.search(_normalize(text)))
