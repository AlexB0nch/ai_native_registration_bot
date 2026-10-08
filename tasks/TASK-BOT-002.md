# TASK-BOT-002: Команды владельца и вопросы владельцу

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-BOT-002 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-08 |
| **Updated** | 2026-10-08 |
| **SPEC** | [docs/SPEC-TASK-BOT-002.md](../docs/SPEC-TASK-BOT-002.md) |
| **Branch** | `feature/admin-and-questions` |
| **PR** | — |
| **Merged SHA** | — |

## История статусов

- 2026-10-08: Ready — SPEC готов.
- 2026-10-08: In Review — реализация в ветке `feature/admin-and-questions` (PR открывает Orchestrator).

## Заметки реализации

- Несколько чатов в `ADMIN_CHAT_IDS`: в `questions.admin_chat_id/admin_message_id` хранится первое
  пересланное сообщение; ответ реплаем на копию в другом чате находится по кнопке `q:answer:<id>`
  в `reply_to_message.reply_markup` (без миграции).
- Состояние владельца — сырая строка FSM `answering:<id>` (ключ — чат владельца); команды (`/…`)
  в этом состоянии не перехватываются.
- Повторный ответ уходит как «Дополнение от Александра», `answer_text` дописывается,
  `answered_at` — время первого ответа. Недоставленный ответ в базе не сохраняется.
- Команды и тексты, начинающиеся с `/`, от не-админа не пересылаются владельцу как вопрос — ответ `fallback_text`.
- Вне `Files:` поправлены два существующих теста: `tests/test_start.py` (свободный текст теперь
  уходит владельцу) и `tests/test_facts.py` (подстановки разделов `admin.*`, `questions.*`).
