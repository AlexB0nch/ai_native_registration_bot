# SPEC: TASK-LLM-001 — ответы на вопросы по базе знаний

**TASK-ID:** TASK-LLM-001
**Branch:** feature/llm-answers
**Status:** Ready for implementation (после TASK-CORE-001 и TASK-BOT-002)
**Owner:** Claude-in-worktree

## Goal

Бот отвечает на вопросы о курсе и практикуме по фактам и базе знаний, ничего не выдумывает,
а на что не знает ответа — честно говорит и передаёт вопрос владельцу.

## Files

```
scripts/build_knowledge.py, knowledge/*.md, app/llm/base.py, app/llm/deepseek.py, app/llm/anthropic.py,
app/llm/stub.py, app/llm/prompt.py, app/llm/guard.py, app/llm/faq.py, app/llm/service.py,
app/bot/handlers/questions.py (только тело answer_question_inline), config/texts.yaml (раздел faq.*),
requirements.txt (если нужен anthropic SDK — лучше httpx без SDK),
tests/test_faq.py, tests/test_guard.py, tests/test_prompt.py, tests/test_llm_service.py,
tests/test_build_knowledge.py, tasks/TASK-LLM-001.md
```

## База знаний

`scripts/build_knowledge.py` читает `materials/` и пишет `knowledge/`:
- из `materials/LANDING-COPY.md` по заголовкам внутри раздела «3. Текст по секциям»: `practicum.md` (раздел «2. Бесплатный практикум»),
  `program.md` («5. Программа курса»), `results.md` («6. Итоговые артефакты», «7. Примеры MVP»),
  `format.md` («8. Формат и нагрузка»), `audience.md` («9. Кому подходит и кому нет»),
  `boundaries.md` («10. Честные границы»), `author.md` («11. Ведущий», «4. Блок ведущего»),
  `faq.md` («14. Вопросы»);
- из файлов курса в `materials/course/*.md`, если они есть (`ai_vibecoding_opex_consultant_course.md`,
  `marketing-campaign-ru.md`, `claude-code-briefs-ru.md`) — разделы о программе, формате, результатах,
  инструментах; «что не обещать» и тон из брифов идут в правила промпта, а не в базу;
- вырезать: строки с ценами в ₽, пометки `[УТОЧНИТЬ…]`, `[ПРОВЕРИТЬ]`, `[НУЖЕН ПРИМЕР АВТОРА]`
  (утверждение с такой пометкой удаляется целиком; если удалён ответ FAQ — удалить и вопрос),
  упоминания Zoom (платформа практикума — из фактов), служебные указания для макета;
- в шапке каждого файла: «Собрано scripts/build_knowledge.py из …, правки руками допустимы».
Скрипт детерминирован (повторный запуск без изменений источников даёт тот же результат).

## Быстрые ответы (`app/llm/faq.py`)

Без модели, по ключевым словам (нижний регистр, корни): цена/стоимость/сколько стоит/тариф →
`faq.prices`; когда/дата/начало/старт/расписание/время → `faq.dates`; программировать/код/не программист/
разработчик → `faq.no_coding`; практикум/что будет на практикуме → `faq.practicum`. Тексты — в `texts.yaml`
через подстановки фактов, ≤ 600 знаков, с кнопкой «Записаться на практикум» или «Страница курса».

## Модель

- Общий контракт `BaseLLMClient.generate(system: str, user: str) -> str` (JSON-строка).
  `DeepSeekLLMClient` — по образцу `app/integrations/llm/deepseek.py` из pppp (`/chat/completions`,
  `response_format: json_object`, `temperature` 0.2); `AnthropicLLMClient` — Messages API через httpx
  (`LLM_BASE_URL` = `https://api.anthropic.com`, заголовки `x-api-key`, `anthropic-version`); `StubLLMClient`
  всегда возвращает `answered=false`. Выбор — `LLM_PROVIDER`.
- `prompt.py`: системный промпт на русском: роль (бот записи на курс {author}), правила тона и запреты
  из CLAUDE.md, «отвечай только по базе знаний и фактам ниже; если ответа нет — answered=false»,
  «цены, даты, время — только из блока ФАКТЫ», формат JSON
  `{"text","answered","handoff","cta"}`, затем блок ФАКТЫ (сгенерирован из `facts.yaml` хелперами форматирования)
  и `knowledge/*.md` с лимитом символов (как `build_knowledge_prompt_from_docs` в pppp, лимит 24 000).
- `guard.py`: `check(text) -> GuardResult(ok, reasons)`: каждая сумма (`\d[\d\s ]*\s?(₽|руб)`) ∈ `all_prices_rub()`;
  каждая дата «\d{1,2} (января|…|декабря)» ∈ `all_dates()`; нет запрещённых слов (`уникальн`, `успейте`,
  `прорыв`, `революцион`, `гарантир`, `%`); длина ≤ 600 (иначе обрезать по предложению, ok сохраняется).
- `service.py`: `answer_question(text) -> Answer(text, answered, handoff, cta)`:
  faq → модель → guard. Модель упала, вернула не-JSON или guard не прошёл → `Answer(texts.faq.unknown, answered=False, handoff=True)`.
- `answer_question_inline` (из BOT-002): ответ пользователю + кнопка по `cta`; при `handoff` или `!answered` —
  `escalate(...)` из BOT-002 и текст «Точного ответа у меня нет — передал вопрос Александру, он ответит здесь же.»

## Acceptance Criteria

- A1. «Сколько стоит курс?» → ответ с 24 900, 29 900, 44 900 ₽ из facts; если поменять цену в facts — ответ меняется (тест).
- A2. «Когда начало?» → 7 ноября и практикум 31 октября, время 15:00–17:00 МСК (тест).
- A3. «Нужно ли уметь программировать?» → «не нужно» + честная граница про production (тест).
- A4. «Что будет на практикуме?» → тема и пункты из базы знаний (тест с заглушкой модели, проверяется faq).
- A5. Модель вернула «курс стоит 19 900 ₽» → guard отклоняет, пользователь получает честный ответ, владелец — вопрос (тест).
- A6. Модель недоступна (исключение/таймаут) → то же, бот не молчит (тест).
- A7. `python scripts/build_knowledge.py` создаёт `knowledge/*.md` без `[УТОЧНИТЬ` и без сумм в ₽ (тест).

## Constraints

Ключ модели — только из env. Текст вопросов не логировать. В промпте нет литералов цен и дат вне блока ФАКТЫ.

## Out of Scope

История диалога в промпте (каждый вопрос отвечается отдельно), обучение на ответах владельца.

## Rollback plan

`LLM_PROVIDER=stub` в Coolify — бот отвечает быстрыми ответами и передаёт остальное владельцу. Код — revert PR.
