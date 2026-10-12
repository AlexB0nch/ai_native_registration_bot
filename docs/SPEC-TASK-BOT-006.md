# SPEC: TASK-BOT-006 — подарок за регистрацию: секретная фраза для @Alex_shein_gift_bot

**TASK-ID:** TASK-BOT-006
**Branch:** feature/registration-gift
**Status:** Ready for implementation
**Owner:** Claude-in-worktree

## Goal

За регистрацию человек получает подарок: бот говорит, что нужно написать боту подарков
[@Alex_shein_gift_bot](https://t.me/Alex_shein_gift_bot) секретную фразу `25_задач`.
Решение владельца от 12.10.2026.

## Files

```
config/facts.yaml, app/facts.py, config/texts.yaml, app/bot/keyboards.py,
app/bot/handlers/practicum.py, app/bot/handlers/start.py, app/llm/prompt.py, app/llm/guard.py,
tests/test_practicum_flow.py, tests/test_site_binding.py, tests/test_facts.py, tests/test_guard.py,
tests/test_prompt.py, tasks/TASK-BOT-006.md
```

## Требования

1. **Факты.** В `config/facts.yaml` новый блок `gift: {registration_phrase: "25_задач"}`; ссылка на бота —
   существующий `links.gift_bot`. Схема в `app/facts.py`, фраза — непустая строка. Подстановки
   `{gift_phrase}` и `{gift_bot_username}` (`@Alex_shein_gift_bot`, из ссылки) в `fact_placeholders()`.
2. **Текст** `practicum.gift` в `config/texts.yaml`, на «вы», спокойно, без «успейте» и т. п., например:
   «Подарок за регистрацию: откройте {gift_bot_username} и отправьте ему секретную фразу {gift_phrase} —
   бот пришлёт материалы.» Фраза должна копироваться одним касанием: оформить её как `<code>`
   (HTML) **только если** бот уже шлёт сообщения с `parse_mode=HTML`; иначе — обычным текстом в кавычках «».
   Проверить, что подчёркивание в `25_задач` не ломает разметку при текущем `parse_mode`.
   Кнопка-ссылка «Забрать подарок» → `links.gift_bot`.
3. **Где выдаётся** — отдельным сообщением сразу после:
   - подтверждения новой записи на практикум (после `practicum.done`, до вопроса о роли);
   - ответа «Вы уже записаны…» (напоминание);
   - найденной заявки с сайта на практикум (`/start prk_web`, ветка `site.found_practicum`).

   Подарок — **только за регистрацию на практикум** (уточнение владельца от 12.10.2026): при `/start crs_web`
   с найденной заявкой на курс (`site.found_course`) сообщения о подарке нет.
   При «Изменить данные» без новой записи — не дублировать (подарок уже выдан при записи; достаточно «Вы уже записаны»).
4. **Модель не выдаёт фразу.** Фраза **не** попадает в системный промпт (блок ФАКТЫ). `guard.py` отклоняет
   ответ модели, если в нём есть фраза (без учёта регистра, `_`/пробел/дефис взаимозаменяемы) —
   иначе её получит любой, кто спросит, не записываясь. Быстрые ответы (`faq.*`) фразу тоже не содержат.

## Acceptance Criteria

- A1. Полный сценарий записи → среди сообщений пользователю есть фраза `25_задач`, `@Alex_shein_gift_bot`
  и кнопка с URL `https://t.me/Alex_shein_gift_bot` (тест).
- A2. «Вы уже записаны» → напоминание о подарке (тест).
- A3. `/start prk_web` с найденной заявкой → сообщение о подарке; `/start crs_web` с найденной заявкой на курс →
  сообщения о подарке нет (тест).
- A4. Смена `gift.registration_phrase` в фактах меняет текст (тест).
- A5. Системный промпт не содержит фразу; ответ модели с «25 задач» / «25_задач» отклоняется guard'ом (тест).
- A6. `ruff check .` и `pytest -q` зелёные.

## Constraints

Фраза — только в `facts.yaml`; в коде, текстах и тестах — через подстановку/факты (в тестах можно сравнивать с
`get_facts().gift.registration_phrase`). ПД не в логах.

## Out of Scope

Логика самого @Alex_shein_gift_bot.

## Rollback plan

Revert PR.
