"""Разбор параметра `/start` (deep link `t.me/<bot>?start=<метка>`).

Таблица меток — docs/ARCHITECTURE.md, раздел «Метки /start».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Scenario = Literal["practicum", "course", "site_practicum", "site_course", "waitlist", "menu"]

PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class StartPayload:
    raw: str | None  # допустимая метка как есть (сохраняется в people.source / registrations.source)
    scenario: Scenario


def _scenario(label: str) -> Scenario:
    low = label.lower()
    if low == "prk_web":
        return "site_practicum"
    if low == "crs_web":
        return "site_course"
    if low == "prk" or low.startswith(("prk_", "lp_")):
        return "practicum"
    if low == "crs" or low.startswith("crs_"):
        return "course"
    if low == "wait":
        return "waitlist"
    return "menu"


def parse_start_payload(text: str | None) -> StartPayload:
    """Метка из `CommandObject.args` (или полного текста `/start <метка>`) → сценарий.

    Недопустимые символы или длина > 64 → `raw=None, scenario="menu"`.
    """
    label = (text or "").strip()
    if label.startswith("/start"):
        rest = label.removeprefix("/start")
        if rest.startswith("@"):  # «/start@bot_name метка»
            rest = rest.partition(" ")[2]
        label = rest.strip()
    if not label or not PAYLOAD_RE.match(label):
        return StartPayload(raw=None, scenario="menu")
    return StartPayload(raw=label, scenario=_scenario(label))
