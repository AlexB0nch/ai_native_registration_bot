"""TASK-LLM-001: сборка базы знаний `knowledge/*.md` из `materials/` (A7)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from app.facts import MONTHS_GENITIVE
from scripts import build_knowledge as bk

ROOT = Path(__file__).resolve().parent.parent
MATERIALS = ROOT / "materials"
MONTHS = "|".join(MONTHS_GENITIVE)
EXPECTED = {
    "practicum.md",
    "program.md",
    "results.md",
    "format.md",
    "audience.md",
    "boundaries.md",
    "author.md",
    "faq.md",
}


@pytest.fixture
def materials(tmp_path: Path) -> Path:
    """Копия materials/ без файлов курса (как сейчас в репозитории)."""
    target = tmp_path / "materials"
    (target / "course").mkdir(parents=True)
    shutil.copy(MATERIALS / bk.LANDING_FILE, target / bk.LANDING_FILE)
    return target


def _assert_clean(name: str, content: str) -> None:
    assert "[УТОЧНИТЬ" not in content, name
    assert "[ПРОВЕРИТЬ" not in content, name
    assert "[НУЖЕН ПРИМЕР" not in content, name
    assert not re.search(r"\d[\d\s  ]*\s?(₽|руб)", content), f"сумма в ₽ в {name}"
    assert "zoom" not in content.lower(), name
    assert not re.search(rf"\d{{1,2}}\s+({MONTHS})", content), f"дата в {name}"
    assert not re.search(r"\d{1,2}:\d{2}", content), f"время в {name}"
    for layout in ("Надзаголовок", "Форма №", "Под формой", "Кнопка", ".jpg", "public/assets", "**H2:**", "**Лид:**"):
        assert layout not in content, f"«{layout}» в {name}"


def test_build_from_landing_only(materials: Path, tmp_path: Path) -> None:
    out = tmp_path / "knowledge"
    bk.write(bk.build(materials), out)
    files = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.md")}
    assert set(files) == EXPECTED
    for name, content in files.items():
        _assert_clean(name, content)
        assert f"> {bk.GENERATED_MARK} из materials/LANDING-COPY.md" in content.splitlines()[2], name
        assert "правки руками допустимы" in content, name


def test_content_is_kept(materials: Path) -> None:
    files = bk.build(materials)
    assert "Почему чат с ИИ ещё не система работы" in files["practicum.md"]
    assert "Неделя 1 · AI-native консультант" in files["program.md"]
    assert "Демо-день" in files["program.md"]
    assert "Программировать уметь не нужно" in files["audience.md"]
    assert "production" in files["boundaries.md"]
    assert "Александр Шеин" in files["author.md"]
    assert "AI-A3 Assistant" in files["results.md"]
    assert "Telegram-чат" in files["format.md"]
    # ответ FAQ с пометкой вырезан вместе с вопросом
    faq = files["faq.md"]
    assert "Я не программист. Справлюсь?" in faq
    assert "вернуть деньги" not in faq
    assert "документ об окончании" not in faq
    assert "пропущу занятие" not in faq
    # предложение с пометкой вырезано, остальной ответ остался
    assert "Минимум: одна AI-модель с подпиской" in faq
    assert "В стоимость курса подписки не входят" not in faq
    # пункт практикума с пометкой удалён, нумерация сплошная
    practicum = files["practicum.md"]
    assert "Живая демонстрация прототипа" not in practicum
    assert "5. Как устроен курс" in practicum
    assert "6." not in practicum


def test_no_client_names_and_service_notes(materials: Path) -> None:
    author = bk.build(materials)["author.md"]
    assert "Работал с" not in author
    assert "подтверждено владельцем" not in author
    assert "По желанию владельца" not in author
    assert "Этот сайт" not in author


def test_deterministic(materials: Path, tmp_path: Path) -> None:
    first = bk.build(materials)
    second = bk.build(materials)
    assert first == second
    out = tmp_path / "k"
    bk.write(first, out)
    before = {p.name: p.read_bytes() for p in out.glob("*.md")}
    bk.write(bk.build(materials), out)
    assert {p.name: p.read_bytes() for p in out.glob("*.md")} == before


def test_committed_knowledge_matches_materials() -> None:
    """knowledge/ в репозитории собрана из текущих materials/ и тоже чистая."""
    knowledge = ROOT / "knowledge"
    names = {p.name for p in knowledge.glob("*.md")}
    assert names >= EXPECTED
    for path in knowledge.glob("*.md"):
        _assert_clean(path.name, path.read_text(encoding="utf-8"))


COURSE_FILE = """\
# Курс: ИИ и вайбкодинг для консультанта

## Позиционирование

Курс для консультантов.

## Программа курса

### Модуль 1. Основы

Постановка задачи модели. Стоимость участия 24 900 ₽ обсуждается отдельно.
Проверка источников [УТОЧНИТЬ у автора].

### Модуль 2. Вайбкодинг

Спецификация и репозиторий, занятие 14 ноября в 15:00–17:00 МСК.

## Цены и тарифы

Early Bird 24 900 ₽.

## Что не обещать

Не обещать проценты.

## Инструменты

- GitHub
- Docker
"""


def test_build_with_course_files(materials: Path, tmp_path: Path) -> None:
    (materials / "course" / "ai_vibecoding_opex_consultant_course.md").write_text(COURSE_FILE, encoding="utf-8")
    files = bk.build(materials)
    assert set(files) == EXPECTED | {"course.md"}
    course = files["course.md"]
    _assert_clean("course.md", course)
    assert "materials/course/ai_vibecoding_opex_consultant_course.md" in course
    assert "Модуль 1. Основы" in course
    assert "Постановка задачи модели." in course
    assert "Спецификация и репозиторий" in course
    assert "- Docker" in course
    assert "Что не обещать" not in course
    assert "Цены и тарифы" not in course
    assert "Позиционирование" not in course

    # файлы курса убрали — сгенерированный course.md удаляется при записи
    out = tmp_path / "knowledge"
    bk.write(files, out)
    assert (out / "course.md").exists()
    (materials / "course" / "ai_vibecoding_opex_consultant_course.md").unlink()
    bk.write(bk.build(materials), out)
    assert not (out / "course.md").exists()


def test_cli(materials: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli"
    assert bk.main(["--materials", str(materials), "--out", str(out)]) == 0
    assert (out / "faq.md").exists()
    assert "faq.md" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Занятия по субботам, 15:00–17:00 МСК.", "Занятия по субботам."),
        ("4 субботы: 7, 14, 21 и 28 ноября, 15:00–17:00 МСК", "4 субботы"),
        ("5 декабря, 15:00–17:00 МСК, через неделю после занятия", "Через неделю после занятия"),
        ("Встреча в ту же сетку, 15:00–17:00 МСК, через неделю", "Встреча в ту же сетку, через неделю"),
        ("Это стоит 24 900 ₽. А это нет.", "А это нет."),
        ("Где: онлайн, Zoom. Ссылка в чате [УТОЧНИТЬ платформу]", "Где: онлайн."),
        ("Только пометка [ПРОВЕРИТЬ]", ""),
    ],
)
def test_clean_text(source: str, expected: str) -> None:
    assert bk.clean_text(source) == expected


def test_scrub_heading() -> None:
    assert bk._scrub_heading("Неделя 1 · 7 ноября · AI-native консультант") == "Неделя 1 · AI-native консультант"
    assert bk._scrub_heading("5 декабря · демо-день") == "Демо-день"
