# TASK-BOT-007: Команда рассылки участникам практикума

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-BOT-007 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-30 |
| **SPEC** | [docs/SPEC-TASK-BOT-007.md](../docs/SPEC-TASK-BOT-007.md) |
| **Branch** | `feature/broadcast` |
| **PR** | — |

## История статусов

- 2026-10-30: Ready — владелец просит рассылать сообщения участникам командой боту, а не скриптом.
- 2026-10-30: In Review — реализовано в `feature/broadcast`: `app/services/broadcast.py`, `app/bot/handlers/broadcast.py`, тексты, RUNBOOK, тесты (ruff, pytest на SQLite и Postgres зелёные).
