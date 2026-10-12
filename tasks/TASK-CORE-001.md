# TASK-CORE-001: Основа сервиса

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-CORE-001 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-08 |
| **Updated** | 2026-10-08 |
| **SPEC** | [docs/SPEC-TASK-CORE-001.md](../docs/SPEC-TASK-CORE-001.md) |
| **Branch** | `feature/core` |
| **PR** | — (открывает Orchestrator) |
| **Merged SHA** | — |

## История статусов

- 2026-10-08: Ready — SPEC готов.
- 2026-10-08: In Review — каркас готов: `ruff check .` и `pytest -q` зелёные; миграции
  `upgrade → downgrade → upgrade` проверены на локальном Postgres 16, тесты прогнаны и на SQLite, и на Postgres.

## Точки входа для параллельных задач

- TASK-BOT-001: `app/bot/handlers/practicum.py` — `router` и `start_practicum(message, state)`;
  метка `/start` в FSM data под ключом `START_SOURCE_KEY`; кнопка меню — `keyboards.MENU_PRACTICUM`.
- TASK-BOT-002: `app/bot/handlers/admin.py` — `router` с фильтром `IsAdmin`;
  `app/bot/handlers/questions.py` — `answer_question_inline`, `handle_free_text`, `escalate`;
  кнопка «Задать вопрос» — `keyboards.MENU_QUESTION`; `notify_admins` возвращает отправленные сообщения.
- TASK-API-001: `app/api/site.py` — `router` (подключён без префикса);
  `app/bot/handlers/start.py` — `handle_site_practicum`, `handle_site_course`.
- TASK-LLM-001: `app/llm/`, `app/facts.py` — `all_prices_rub()`, `all_dates()`, хелперы форматирования.
- Тесты: фикстуры `tg`, `db`, `engine`, `app`, `client`; `tests/helpers.py` — `FakeSession`,
  `make_message_update`, `make_callback_update`, `feed`, `TgHarness.send/click/click_button`.
