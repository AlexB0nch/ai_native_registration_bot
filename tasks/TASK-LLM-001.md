# TASK-LLM-001: Ответы на вопросы по базе знаний

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-LLM-001 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-08 |
| **Updated** | 2026-10-08 |
| **SPEC** | [docs/SPEC-TASK-LLM-001.md](../docs/SPEC-TASK-LLM-001.md) |
| **Branch** | `feature/llm-answers` |
| **PR** | — (ветка запушена, PR открывает Orchestrator) |
| **Merged SHA** | — |

## Что сделано

- `scripts/build_knowledge.py` → `knowledge/*.md` (8 файлов из `LANDING-COPY.md`; `course.md` появится,
  когда владелец загрузит файлы в `materials/course/`). Вырезаются пометки `[УТОЧНИТЬ…]` и т. п.,
  суммы в ₽, Zoom, указания для макета, даты и время (их источник — `facts.yaml`).
- `app/llm/`: `base`, `deepseek`, `anthropic` (httpx, без SDK), `stub`, `prompt`, `guard`, `faq`, `service`.
- `answer_question_inline` в `app/bot/handlers/questions.py`: ответ + кнопка по `cta`, при `handoff` — `escalate(...)`.
- Тексты `faq.*` в `config/texts.yaml`.
- Тесты: `test_build_knowledge.py`, `test_guard.py`, `test_faq.py`, `test_prompt.py`, `test_llm_service.py`.

## Отступления от SPEC

- `tests/test_start.py`: одна строка — тест заглушки CORE отправлял «Сколько стоит курс?» и ждал
  `fallback_text`; теперь на это отвечает FAQ, в тесте вместо вопроса — неизвестная команда `/unknown`.
- Незнакомая команда (`/…`) и пустой текст не идут ни в модель, ни владельцу: `fallback_text` + меню.
- В `author.md` не попала строка «Работал с …» (клиенты из опыта ведущего): бот о клиентах не рассказывает.

## История статусов

- 2026-10-08: Ready — SPEC готов.
- 2026-10-08: In Review — реализация в `feature/llm-answers`, `ruff check .` и `pytest -q` зелёные.
