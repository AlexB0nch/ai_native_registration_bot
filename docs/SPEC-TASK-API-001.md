# SPEC: TASK-API-001 — заявки с сайта

**TASK-ID:** TASK-API-001
**Branch:** feature/site-registrations
**Status:** Ready for implementation (после TASK-CORE-001)
**Owner:** Claude-in-worktree

## Goal

Заявки из двух форм на `alexshein.com/ai-native` попадают в ту же базу, что и записи из бота,
а человек, перешедший со страницы «Спасибо» (`/start prk_web` / `crs_web`), привязывается к своей заявке.

## Context

Договор описан в `materials/FORMS.md`, раздел «Формат заявки». Менять его можно только вместе
с сервисом форм сайта. Уведомления владельцу о заявках с сайта шлёт сервис форм — бот их не дублирует.

## Files

```
app/api/site.py, app/services/site_import.py, app/services/people.py (функции поиска и слияния),
app/bot/handlers/start.py (только ветки site_practicum/site_course), config/texts.yaml (раздел site.*),
tests/test_site_api.py, tests/test_site_binding.py, tasks/TASK-API-001.md
```

## API

`POST /api/site-registrations`, `Content-Type: application/json`, заголовок `X-Webhook-Secret`
= `SITE_WEBHOOK_SECRET` (сравнение `hmac.compare_digest`; не задан на сервере → 503; не совпал → 401).
Тело — JSON из FORMS.md (pydantic, лишние поля игнорировать): `id`, `created_at`, `source`, `form`
(`practicum`|`course`), `name`, `contact_raw`, `telegram_username`, `email`, `role`, `tariff`, `task`,
`invoice`, `page`, `utm{source,medium,campaign,content}`.

Обработка (`site_import.import_site_registration`):
1. Идемпотентность: `registrations.site_id == id` уже есть → `200 {"status":"duplicate"}`.
2. Найти человека: по `telegram_username` (lower), иначе по `email` (lower). Нет — создать
   (`source` = `site:<utm.content>` или `site`, `utm`, `name`, `role`).
   Найден — дописать пустые поля (`email`, `role`, `name`), существующие не затирать.
3. Событие: `practicum` или `course`. Запись `register_for_event` (если BOT-001 ещё не смёрджен —
   своя минимальная функция в `site_import.py`), `channel=site`, `status` = `registered` (практикум)
   или `applied` (курс), `tariff`, `task`, `invoice`, `utm`, `site_id`, `source` = `utm.content`.
   Если запись человек × событие уже есть (например, из бота) — не дублировать, дописать `site_id`, если пуст,
   и `tariff/task/invoice` для курса.
4. Ответ `200 {"status":"created"|"merged", "registration_id": N}`; ошибка валидации → 422.

## Привязка в боте

`/start prk_web` / `crs_web`: найти человека без `telegram_id` по `telegram_username` = username отправителя
(lower) с записью `channel=site` на соответствующее событие.
- Нашёлся → проставить `telegram_id`; если уже есть человек с этим `telegram_id` (заходил в бот раньше) —
  слить: записи и вопросы перенести в бот-запись (без дублей по событию), пустые поля дописать, лишнюю строку удалить.
  Ответ: «Нашёл вашу заявку с сайта{ на практикум {practicum_slot}| на курс}. Всё в порядке: {что дальше}.»
  Для практикума — «За день до начала пришлю сюда ссылку на подключение» + «Добавить в календарь» (если BOT-001 есть).
- Не нашёлся (нет username или другая форма) → обычный сценарий: `prk_web` → практикум, `crs_web` → меню с текстом
  про курс (сценарий курса — BOT-003).

## Acceptance Criteria

- A1. Без заголовка / с неверным секретом → 401; без `SITE_WEBHOOK_SECRET` на сервере → 503 (тест).
- A2. Пример из FORMS.md → `created`; тот же `id` повторно → `duplicate`, строк не прибавилось (тест).
- A3. Заявка с сайта и запись из бота того же человека (по username) на практикум → одна строка `registrations` (тест).
- A4. `/start prk_web` от `@Irina_Ops` находит заявку `irina_ops` и привязывает `telegram_id`; при наличии
  бот-записи — слияние без дублей (тест).
- A5. Ни одного уведомления владельцу при приёме заявки с сайта (тест).

## Constraints

ПД не в логах (логировать `site_id` и `registration_id`). Не трогать практикум и админку.

## Out of Scope

Сценарий курса, письма на почту, изменения в сервисе форм сайта.

## Rollback plan

Revert PR; сервис форм продолжает писать заявки в свой файл и не зависит от ответа бота.
