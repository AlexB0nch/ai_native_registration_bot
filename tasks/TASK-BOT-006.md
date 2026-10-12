# TASK-BOT-006: Подарок за регистрацию — секретная фраза для @Alex_shein_gift_bot

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-BOT-006 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-12 |
| **SPEC** | [docs/SPEC-TASK-BOT-006.md](../docs/SPEC-TASK-BOT-006.md) |
| **Branch** | `feature/registration-gift` |
| **PR** | ветка `feature/registration-gift` запушена, PR открывает Orchestrator |

## История статусов

- 2026-10-12: Ready — решение владельца: за регистрацию выдаётся фраза «25_задач» для @Alex_shein_gift_bot.
- 2026-10-12: уточнение владельца — подарок только за регистрацию на практикум; при `/start crs_web` (заявка на курс) его нет. SPEC обновлён.
- 2026-10-12: In Review — реализация в `feature/registration-gift`: факт `gift.registration_phrase`, текст `practicum.gift`
  с кнопкой «Забрать подарок», выдача после записи, «Вы уже записаны» и `/start prk_web`; фраза не в промпте, guard её отклоняет.
