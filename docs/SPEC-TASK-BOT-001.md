# SPEC: TASK-BOT-001 — запись на практикум

**TASK-ID:** TASK-BOT-001
**Branch:** feature/practicum-flow
**Status:** Ready for implementation (после TASK-CORE-001)
**Owner:** Claude-in-worktree

## Goal

Человек записывается на практикум 31 октября в 4 шага с телефона, без дублей;
владелец сразу получает уведомление.

## Files

```
app/bot/handlers/practicum.py, app/services/registrations.py, app/services/ics.py,
app/bot/validators.py, config/texts.yaml (раздел practicum.*), app/bot/keyboards.py (добавить клавиатуры),
tests/test_practicum_flow.py, tests/test_validators.py, tests/test_ics.py, tasks/TASK-BOT-001.md
```

## Сценарий

Вход: кнопка меню «Записаться на практикум…», команда `/practicum`, `/start` со сценарием
`practicum` (метки `prk*`, `lp_*`) — через `start_practicum(message, state)`.

Если человек уже записан (`registrations` с `event=practicum`, статус не `cancelled`) →
«Вы уже записаны на практикум {practicum_slot}. Ссылку пришлю за день до начала.»
+ кнопки «Изменить данные» (проходит шаги заново и обновляет ту же запись), «Добавить в календарь», «В меню».

1. **Имя.** «Как к вам обращаться?» Если в профиле есть имя — кнопка «Да, {tg_first_name}»
   (inline) и подсказка «или напишите, как вас зовут». Ввод: 1–100 символов, без ссылок.
2. **Почта.** «Ваша почта — на случай, если Telegram подведёт.» Проверка формата
   (`email-validator`, `check_deliverability=False`), хранить в нижнем регистре.
   Ошибка → «Похоже, в адресе опечатка. Пример: name@company.ru».
3. **Телефон.** Reply-клавиатура: «Поделиться контактом» (`request_contact`) и «Пропустить».
   Ручной ввод нормализуется в E.164 (`phonenumbers`, регион по умолчанию RU; `8XXXXXXXXXX` → `+7…`).
   Контакт принимается, только если `contact.user_id == from_user.id`. После шага reply-клавиатуру убрать.
4. **Согласие.** «Нажимая «Согласен», вы соглашаетесь на обработку персональных данных: {privacy_url}»
   + кнопка «Согласен». Сохранить `consent_at`.
5. **Подтверждение.** «Готово, {name}! Вы записаны на практикум {practicum_slot}, {platform}.
   За день до начала пришлю ссылку на подключение, за час — напоминание.»
   Кнопки: «Добавить в календарь» (отправляет файл `practicum.ics`), «Что будет на курсе», «Цены и формат».
   Затем отдельным сообщением необязательный вопрос «Чем вы занимаетесь?» с кнопками
   «Независимый консультант» / «Lean/OpEx в компании» / «Руководитель трансформации или PMO» / «Другое» / «Пропустить»
   → `people.role` (`consultant`/`opex`/`transformation`/`other`).

На шагах 1–4: inline-кнопки «Назад» (к предыдущему шагу, введённые данные сохраняются) и «Отмена»
(выход в меню, запись не создаётся). Команды `/start`, `/cancel` на любом шаге прерывают сценарий.

**Вопрос вместо ответа.** Если на шаге имени, почты или телефона пришёл текст, который не прошёл проверку
и выглядит как вопрос (есть `?` или ≥ 4 слов), бот вызывает `answer_question_inline(message, text)`
из `app/bot/handlers/questions.py` (в CORE — заглушка, отвечает `fallback_text`; реальные ответы — LLM-001),
затем повторяет вопрос текущего шага. Состояние не теряется.

## Запись

`app/services/registrations.py`:
- `register_for_event(session, person, event_code, channel, source, utm=None) -> (Registration, created: bool)` —
  один человек × событие = одна строка (`unique(person_id, event_id)`); повтор обновляет контакты, но не `created_at` и не `source`.
- `count_registrations(session, event_code) -> int`.

Контакты (`name`, `email`, `phone`, `consent_at`) пишутся в `people` в конце сценария одной транзакцией.

## Уведомление владельцу

Только при `created=True`: «Новая запись на практикум: {name}, {email}, {phone|—}, @{username|—},
источник: {source|—}. Всего записались: {count}.» через `notify_admins`.

## .ics

`app/services/ics.py`: `build_ics(event) -> bytes` — VCALENDAR/VEVENT, `UID` стабильный
(`<event.code>@reg.alexshein.com`), `DTSTART`/`DTEND` в UTC, `SUMMARY` «Практикум: …»,
`DESCRIPTION` со ссылкой на страницу курса, `URL` = `join_url`, если задан; CRLF, экранирование по RFC 5545.

## Acceptance Criteria

- A1. Полный сценарий в тесте: `/start lp_opex_p1` → кнопка практикума → «Да, Имя» → почта → «Пропустить» → «Согласен» → запись с `source=lp_opex_p1`, `channel=bot`, уведомление владельцу с «Всего записались: 1».
- A2. Повторное прохождение тем же `user_id` не создаёт вторую запись и не шлёт второе уведомление.
- A3. «Назад» с шага почты возвращает к имени; «Отмена» выходит без записи.
- A4. Неверная почта → подсказка и тот же шаг; вопрос «А сколько стоит курс?» на шаге почты → ответ + повтор шага.
- A5. Телефоны: `+7 (912) 345-67-89`, `89123456789`, `8 912 345 67 89` → `+79123456789`; `12345` → ошибка; чужой контакт отклоняется.
- A6. .ics открывается парсером (`icalendar` в dev-зависимостях): время 12:00–14:00 UTC 31.10.2026.
- A7. Все тексты — в `config/texts.yaml`; даты через подстановки.

## Constraints

ПД не в логах. Не трогать другие хендлеры, кроме точки входа в `practicum.py`.

## Out of Scope

Рассылки и напоминания (BOT-004), курс (BOT-003), ответы модели (LLM-001).

## Rollback plan

Revert PR.
