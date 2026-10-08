# SPEC: TASK-INFRA-001 — деплой на Coolify и runbook

**TASK-ID:** TASK-INFRA-001
**Branch:** chore/runbook
**Status:** Ready (после TASK-CORE-001)
**Owner:** Orchestrator (документы) + Owner (действия в Coolify и Timeweb)

## Goal

Бот работает на `https://reg.alexshein.com`, отвечает в Telegram; владелец знает, как менять
факты, базу знаний, ссылки и делать выгрузку.

## Files

`README.md`, `docs/RUNBOOK.md`, `tasks/TASK-INFRA-001.md`.

## Шаги (Owner, по RUNBOOK)

1. Timeweb → DNS `alexshein.com` → A-запись `reg` → IP сервера Coolify (напрямую, без DDoS-Guard).
2. Coolify → New Resource → Docker Compose → GitHub `AlexB0nch/ai_native_registration_bot`, ветка `main`,
   файл `docker-compose.coolify.yml`, Auto Deploy включён.
3. Environment Variables по `.env.example` (секреты генерируются `openssl rand -hex 32`).
4. Сервис `bot` → Domains → `https://reg.alexshein.com`, порт 8000.
5. Deploy → `curl -fsS https://reg.alexshein.com/health`.
6. Написать боту `/whoami` → chat_id в `ADMIN_CHAT_IDS` → Redeploy.
7. `getWebhookInfo`: URL `https://reg.alexshein.com/tg/webhook`, `allowed_updates` = message, callback_query, ошибок нет.

## RUNBOOK (разделы)

Как поменять факты (`config/facts.yaml` → PR → деплой); как обновить базу знаний
(`materials/` → `python scripts/build_knowledge.py` → PR); как задать ссылку (`/link`, `/set_link recording`);
выгрузка (`/export`, открыть в Excel); статистика (`/stats`); бот не отвечает (health, логи Coolify,
`getWebhookInfo`, `LLM_PROVIDER=stub`); откат.

## Acceptance Criteria

- A1. Критерии приёмки P0 из задачи владельца пройдены вручную с телефона и с компьютера, отчёт в чате.
- A2. RUNBOOK покрывает все разделы выше.

## Rollback plan

Coolify → Deployments → Rollback.
