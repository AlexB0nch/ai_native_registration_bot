# SPEC: TASK-BOT-002 — команды владельца и вопросы владельцу

**TASK-ID:** TASK-BOT-002
**Branch:** feature/admin-and-questions
**Status:** Ready for implementation (после TASK-CORE-001)
**Owner:** Claude-in-worktree

## Goal

Владелец видит статистику, выгружает записи в Excel, задаёт ссылку на практикум и отвечает
на вопросы, на которые бот не знает ответа.

## Files

```
app/bot/handlers/admin.py, app/bot/handlers/questions.py, app/services/questions.py,
app/services/stats.py, app/services/export.py, app/bot/filters.py, app/bot/keyboards.py (добавить),
config/texts.yaml (разделы admin.*, questions.*),
tests/test_admin.py, tests/test_export.py, tests/test_questions_relay.py, tasks/TASK-BOT-002.md
```

## Команды владельца

Только для `ADMIN_CHAT_IDS` (фильтр `IsAdmin`); остальным команды не отвечают (проходят дальше как текст).

- `/stats`: всего на практикум; по дням за последние 14 дней (дата МСК → число); по источникам
  (`registrations.source`, пустой → «без метки»), топ-15; по каналам `bot`/`site`; заявки на курс
  по тарифам и статусам (если есть); вопросы без ответа.
- `/export`: CSV-файл `registrations_YYYY-MM-DD.csv`, UTF-8 **с BOM**, разделитель `;`, CRLF,
  все поля в кавычках при необходимости (`csv` модуль). Колонки: `created_at_msk`, `event`, `channel`, `status`,
  `name`, `email`, `phone`, `telegram_username`, `telegram_id`, `role`, `source`, `utm_source`, `utm_medium`,
  `utm_campaign`, `utm_content`, `tariff`, `task`, `invoice`, `consent_at_msk`. Значения, начинающиеся
  с `= + - @`, экранировать префиксом `'` (защита от CSV-инъекций в Excel).
- `/link <url>` и `/set_link practicum <url>` → `events.join_url` практикума; `/set_link recording <url>` →
  `events.recording_url` практикума. Принимать только `https://`. Ответ: «Ссылка сохранена: …».
  Без аргумента — показать текущие ссылки.

## Вопросы владельцу

`app/services/questions.py`:
- `create_question(session, person, text, bot_answer=None) -> Question`;
- `forward_to_admins(bot, question, person)` — сообщение «Вопрос от {name|tg_first_name} (@username, источник):
  {text}» + inline-кнопка «Ответить» (`q:answer:<id>`); сохранить `admin_chat_id`, `admin_message_id`.

`app/bot/handlers/questions.py`:
- «Задать вопрос» / `/question` → «Напишите вопрос одним сообщением.» → следующий текст обрабатывается
  `answer_question_inline`.
- `answer_question_inline(message, text)` — **точка входа для LLM-001**. В этой задаче: если в тексте есть
  просьба позвать человека («человек», «оператор», «Александр», «позовите», «связаться») или любой другой
  вопрос — создать вопрос, переслать владельцу, ответить пользователю «Передал вопрос Александру,
  он ответит здесь же.» Функция `handle_free_text(message, state)` — текст вне сценария → `answer_question_inline`.
  Функция `escalate(message, person, text, bot_answer)` — создать вопрос и переслать, её же вызывает LLM-001.
- Владелец нажимает «Ответить» → «Напишите ответ для {name}.» (состояние владельца `answering:<id>`, кнопка «Отмена»).
  Следующее сообщение владельца уходит пользователю: «Ответ Александра:\n{text}» + кнопка «Записаться на практикум»;
  `answer_text`, `answered_at` сохраняются; владельцу — «Отправлено». Если пользователь заблокировал бота —
  сообщить владельцу «Не доставлено: пользователь заблокировал бота».
- Также работает ответ владельца реплаем на сообщение с вопросом (поиск по `admin_message_id`).
- Повторное «Ответить» на уже отвеченный вопрос — разрешено, отправляется как дополнение.

## Acceptance Criteria

- A1. `/stats`, `/export`, `/link` от не-админа не выполняются (тест).
- A2. CSV из теста: начинается с `﻿`, разделитель `;`, кириллица корректна, ячейка `=1+1` экранирована.
- A3. `/link https://telemost.yandex.ru/j/123` сохраняет ссылку; `/link http://x` — отказ.
- A4. Вопрос пользователя → сообщение владельцу с кнопкой → ответ владельца → пользователь получает «Ответ Александра: …», `answered_at` заполнен (тест).
- A5. Ответ реплаем работает так же (тест).

## Constraints

ПД не в логах. Не трогать `practicum.py`, `start.py`, `api/`, `llm/`.

## Out of Scope

Ответы модели (LLM-001), `/broadcast_practicum` (BOT-004).

## Rollback plan

Revert PR.
