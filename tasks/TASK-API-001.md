# TASK-API-001: Заявки с сайта

| Поле | Значение |
|---|---|
| **TASK-ID** | TASK-API-001 |
| **Status** | In Review |
| **Owner (Implementer)** | Claude-in-worktree |
| **Created** | 2026-10-08 |
| **Updated** | 2026-10-08 |
| **SPEC** | [docs/SPEC-TASK-API-001.md](../docs/SPEC-TASK-API-001.md) |
| **Branch** | `feature/site-registrations` |
| **PR** | — |
| **Merged SHA** | — |

## История статусов

- 2026-10-08: Ready — SPEC готов.
- 2026-10-08: In Review — `POST /api/site-registrations` и привязка `/start prk_web|crs_web` готовы;
  `ruff check .` и `pytest -q` зелёные (SQLite). PR не открыт — ждёт Orchestrator'а.

## Для сервиса форм

- `BOT_WEBHOOK_URL` = `https://reg.alexshein.com/api/site-registrations`, метод `POST`,
  `Content-Type: application/json`, тело — заявка по «Формату заявки» из `materials/FORMS.md`.
- `X-Webhook-Secret` = `BOT_WEBHOOK_SECRET` сервиса форм = `SITE_WEBHOOK_SECRET` бота (одно значение).
- Ответы: `200 {"status":"created"|"merged"|"duplicate","registration_id":N}`; `401` — нет или неверный
  секрет; `503` — у бота не задан `SITE_WEBHOOK_SECRET`; `422` — тело не по договору. Повтор той же
  заявки (тот же `id`) безопасен — `duplicate`.

## Решения при реализации

- `register_for_event` из TASK-BOT-001 ещё не в `feature/core` — минимальная запись без дублей
  сделана в `app/services/site_import.py`. После мерджа BOT-001 её можно заменить вызовом
  `register_for_event`, если семантика совпадёт.
- Секрет проверяется до разбора тела: без секрета и с битым телом — `401`, а не `422`.
- `duplicate` возвращает и `registration_id` (в SPEC — только `status`).
- При слиянии `source` и `created_at` берутся у более ранней строки: метка первого входа —
  заявка с сайта (`site:<utm.content>`), а не `prk_web` из только что созданной бот-строки.
- `/start prk_web|crs_web` считает заявку найденной и тогда, когда она уже лежит у самого
  отправителя (пришла с сайта, когда он уже был в боте с этим username).
- Повторная заявка на курс от того же человека дописывает только пустые `tariff`/`task`
  (`invoice` — если отмечен хоть раз); запись в статусе `cancelled` возвращается в `registered`/`applied`.
