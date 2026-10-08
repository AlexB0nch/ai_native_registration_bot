# Дев-флоу

## Жизненный цикл задачи

1. Orchestrator пишет `docs/SPEC-<TASK-ID>.md` и `tasks/<TASK-ID>.md`, обновляет `tasks/INDEX.md`.
2. SPEC попадает в `main` (PR `chore/...`), после «ок, мёрджи» Owner'а.
3. Orchestrator запускает Implementer'а (Claude Code в отдельном worktree) против SPEC.
4. Implementer открывает PR из ветки `Branch:` в `main`.
5. CI (`.github/workflows/ci.yml`): ruff, pytest, проверка compose, поиск секретов.
6. Orchestrator ревьюит по чеклисту ниже и пишет комментарий «Orchestrator review: ✅» или список правок.
7. Owner пишет «ок, мёрджи» → Orchestrator мёрджит (squash).
8. Coolify передеплоит `main`. Orchestrator делает smoke-проверку и пишет отчёт.

Задачи с жёстким сроком можно вести стопкой: следующая ветка начинается от предыдущей,
PR открывается в предыдущую ветку и перенацеливается на `main`, когда та смёрджена.

## Ветки и коммиты

| Префикс | Для чего |
|---|---|
| `feature/` | задача по SPEC |
| `fix/`, `hotfix/` | исправления |
| `chore/` | SPEC, документация, без кода |

Коммиты: `<type>(<scope>): <описание>`, где `type` — `feat`, `fix`, `docs`, `test`, `chore`, `ci`, `refactor`.

## Чеклист ревью

**Соответствие SPEC**
- [ ] Ветка = `Branch:`; изменены только файлы из `Files:` (+ карточка задачи).
- [ ] Все Acceptance Criteria выполнены; Constraints не нарушены; Out of Scope не тронут.

**Данные и безопасность**
- [ ] Нет секретов в diff (`grep -iE 'token|secret|api_key|password'` — только имена переменных).
- [ ] Персональные данные не попадают в логи.
- [ ] Цены, даты и ссылки берутся из `config/facts.yaml`, в коде и текстах их нет.
- [ ] Каждый админский хендлер проверяет `ADMIN_CHAT_IDS`.
- [ ] Миграция Alembic обратима (`downgrade` есть).

**Качество**
- [ ] `ruff check .` и `pytest -q` зелёные, CI зелёный.
- [ ] Новые тексты — в `config/texts.yaml`, тон по `CLAUDE.md`.
- [ ] Нет отладочных `print`, мёртвого кода, неиспользуемых импортов.

**Деплой**
- [ ] Новые переменные окружения есть в `.env.example` **и** в `environment:` сервиса `bot`
      в `docker-compose.coolify.yml`.
- [ ] Процесс остаётся один (`--workers 1`).

## Откат

Coolify → ресурс → Deployments → предыдущий успешный деплой → Rollback.
В репозитории — `git revert` через PR.
