# RUNBOOK: бот @AI_native_reg_bot

Прод: `https://reg.alexshein.com`, ресурс Docker Compose в Coolify, ветка `main`.

## 1. Первый деплой (один раз)

1. **DNS (Timeweb).** Зона `alexshein.com` → A-запись `reg` → IP сервера Coolify.
   Напрямую, **не через DDoS-Guard**: защита может отбивать POST-запросы Telegram на вебхук.
   Проверка: `dig +short reg.alexshein.com` показывает IP сервера.
2. **Ресурс.** Coolify → New Resource → Docker Compose → GitHub `AlexB0nch/ai_native_registration_bot`,
   ветка `main`, compose-файл `docker-compose.coolify.yml`, base directory `/`. Auto Deploy — включить.
3. **Переменные** (Environment Variables), по `.env.example`:

   | Переменная | Значение |
   |---|---|
   | `BOT_TOKEN` | токен @AI_native_reg_bot из @BotFather |
   | `TELEGRAM_WEBHOOK_SECRET` | `openssl rand -hex 32` |
   | `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
   | `DATABASE_URL` | `postgresql+psycopg://bot:<POSTGRES_PASSWORD>@db:5432/ai_native_bot` |
   | `PUBLIC_BASE_URL` | `https://reg.alexshein.com` |
   | `ADMIN_CHAT_IDS` | пока пусто, см. шаг 6 |
   | `SITE_WEBHOOK_SECRET` | `openssl rand -hex 32`; то же значение — в `BOT_WEBHOOK_SECRET` сервиса форм сайта |
   | `LLM_PROVIDER` | `deepseek` |
   | `LLM_API_KEY` | ключ DeepSeek |
   | `PRIVACY_URL` | `https://alexshein.com/privacy` |

4. **Домен.** Сервис `bot` → Domains → `https://reg.alexshein.com`, порт `8000`. Другим сервисам домен не назначать.
5. **Deploy.** Дождаться healthy у `db` и `bot`. Проверка: `curl -fsS https://reg.alexshein.com/health` → `{"status":"ok"}`.
6. **Владелец.** Написать боту `/whoami` → вписать число в `ADMIN_CHAT_IDS` (несколько — через запятую) → Redeploy.
   После этого у владельца в меню команд появятся `/stats`, `/export`, `/link`.
7. **Вебхук.** Бот сам вызывает `setWebhook` при старте. Проверка:
   `curl -s "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"` — `url` = `https://reg.alexshein.com/tg/webhook`,
   `allowed_updates` = `["message","callback_query"]`, `last_error_message` пустой.
8. **Сервис форм сайта.** `BOT_WEBHOOK_URL=https://reg.alexshein.com/api/site-registrations`,
   `BOT_WEBHOOK_SECRET` = `SITE_WEBHOOK_SECRET` бота.

## 2. Поменять факты (даты, время, цены, ссылки)

1. Правка `config/facts.yaml` → PR → «ок, мёрджи» → Coolify передеплоит.
2. При старте файл проверяется по схеме: ошибка — контейнер не стартует, в логах причина; Coolify оставит старую версию.
3. Тексты бота и ответы на вопросы берут даты и цены только отсюда, править их ещё где-то не нужно.
4. Ссылка на оплату тарифа — `payment_url` у тарифа; приглашение в чат курса — `course.chat_invite_url`.

## 3. Обновить базу знаний

1. Положить новые материалы в `materials/` (тексты лендинга — копия `docs/ai-course/LANDING-COPY.md` из `alexshein.com`,
   файлы курса — в `materials/course/`).
2. `python scripts/build_knowledge.py` → проверить diff в `knowledge/` → PR.
3. Мелкие правки можно делать прямо в `knowledge/*.md`; следующая пересборка их перезапишет — переносите важное в `materials/`.

## 4. Ссылки на практикум

- `/link https://telemost.yandex.ru/...` (или `/set_link practicum <url>`) — ссылка на подключение.
  Задать **до 30 октября 15:00 МСК**, когда уходят приглашения (P1). `/link` без аргумента — показать текущие ссылки.
- `/set_link recording <url>` — запись практикума для сообщения после него.

## 5. Статистика и выгрузка

- `/stats` — всего записей, по дням, по меткам, по каналам (бот / сайт), вопросы без ответа.
- `/export` — CSV всех записей. Открывается в Excel двойным щелчком: UTF-8 с BOM, разделитель `;`.

## 6. Вопросы пользователей

Вопрос, на который у бота нет ответа, приходит владельцу с кнопкой «Ответить».
Нажать → написать ответ одним сообщением → бот отправит его пользователю от своего имени.
Можно и просто ответить реплаем на сообщение с вопросом.

## 7. Бот не отвечает

1. `curl -fsS https://reg.alexshein.com/health`. 503 — база недоступна: Coolify → `db` → логи.
2. `getWebhookInfo` (шаг 1.7): `last_error_message` подскажет — 403 (секрет не совпал: Redeploy пересоздаст вебхук),
   timeout / SSL (DNS, домен, DDoS-Guard).
3. Coolify → `bot` → Logs. Персональных данных в логах нет, только внутренние id.
4. Модель отвечает плохо или недоступна — `LLM_PROVIDER=stub` → Redeploy: бот отвечает заготовками по фактам,
   остальные вопросы передаёт владельцу.

## 8. Откат

Coolify → ресурс → Deployments → предыдущий успешный деплой → Rollback. В репозитории — `git revert` через PR.
База при откате кода не откатывается; миграции пишутся обратимыми.
