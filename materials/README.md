# Исходные материалы

Отсюда `scripts/build_knowledge.py` собирает базу знаний `knowledge/`. Сами файлы бот не читает.

| Файл | Откуда | Что |
|---|---|---|
| `COURSE-DECISIONS.md` | `alexshein.com`, `docs/ai-course/README.md` (ветка `claude/blissful-cori-7ta2h5`, 08.10.2026) | факты и журнал решений владельца |
| `LANDING-COPY.md` | там же | текст лендинга: программа, формат, кому подходит, честные границы, FAQ |
| `BOT-BRIEF.md` | там же | бриф бота |
| `FORMS.md` | там же | договор с сервисом форм сайта |
| `PLACEMENT.md` | там же | метки, режимы страницы, сроки |
| `course/ai_vibecoding_opex_consultant_course.md` | Dropbox владельца | программа, формат, результаты, артефакты, инструменты |
| `course/marketing-campaign-ru.md` | Dropbox владельца | позиционирование, сегменты, тарифы, «что не обещать» |
| `course/claude-code-briefs-ru.md` | Dropbox владельца | тон и запреты для текстов |

**Три файла из Dropbox владелец кладёт в `materials/course/` сам** (через GitHub → Add file → Upload files):
у Claude Code нет доступа к его компьютеру. Пока их нет, база знаний собирается из `LANDING-COPY.md`.

Если тексты в `alexshein.com` поменялись — скопировать заново и пересобрать базу (см. `docs/RUNBOOK.md`).
Даты, время и цены отсюда **не** берутся: их источник — `config/facts.yaml`.
