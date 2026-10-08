"""Собрать базу знаний `knowledge/*.md` из `materials/`.

    python scripts/build_knowledge.py                    # materials/ → knowledge/
    python scripts/build_knowledge.py --materials M --out K

Источники:
- `materials/LANDING-COPY.md`, раздел «3. Текст по секциям» (и «4. Блок ведущего») —
  по файлу на тему (см. `LANDING_TARGETS`);
- `materials/course/*.md` (если владелец их загрузил) — разделы о программе, формате,
  результатах и инструментах → `knowledge/course.md`. Тон и «что не обещать» в базу не идут:
  они уже в правилах промпта (`app/llm/prompt.py`).

Что вырезается: утверждения с пометками `[УТОЧНИТЬ…]`, `[ПРОВЕРИТЬ]`, `[НУЖЕН ПРИМЕР АВТОРА]`
(если вырезан ответ из FAQ — уходит и вопрос), суммы в ₽, упоминания Zoom, служебные указания
для макета (фото, формы, кнопки, надзаголовки), даты и время (их источник — `config/facts.yaml`,
модель получает их блоком ФАКТЫ).

Скрипт детерминирован: повторный запуск на тех же источниках даёт те же файлы.
После сборки файлы можно править руками (следующая сборка правки перезапишет).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MATERIALS = ROOT / "materials"
DEFAULT_OUT = ROOT / "knowledge"

LANDING_FILE = "LANDING-COPY.md"
LANDING_TEXT_SECTION = "3. Текст по секциям"

# файл базы → (заголовок, разделы лендинга: начало заголовка раздела)
LANDING_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "practicum.md": ("Бесплатный практикум", ("2. Бесплатный практикум",)),
    "program.md": ("Программа курса", ("5. Программа курса",)),
    "results.md": ("Итоговые артефакты и примеры MVP", ("6. Итоговые артефакты", "7. Примеры MVP")),
    "format.md": ("Формат и нагрузка", ("8. Формат и нагрузка",)),
    "audience.md": ("Кому подходит курс и кому нет", ("9. Кому подходит",)),
    "boundaries.md": ("Честные границы", ("10. Честные границы",)),
    "author.md": ("Ведущий", ("11. Ведущий", "4. Блок ведущего")),
    "faq.md": ("Частые вопросы", ("14. Вопросы",)),
}

COURSE_FILES = (
    "ai_vibecoding_opex_consultant_course.md",
    "marketing-campaign-ru.md",
    "claude-code-briefs-ru.md",
)
COURSE_TARGET = "course.md"

GENERATED_MARK = "Собрано scripts/build_knowledge.py"

MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
WEEKDAYS = "понедельник|вторник|среда|четверг|пятница|суббота|воскресенье"
DAY_LIST = r"\d{1,2}(?:\s*[–-]\s*\d{1,2})?(?:,\s*\d{1,2})*(?:\s+и\s+\d{1,2})?"
DATE_RE = re.compile(rf"{DAY_LIST}\s+(?:{MONTHS})(?:\s+20\d\d(?:\s+года)?)?(?:,\s*(?:{WEEKDAYS}))?")
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}(?:\s+(?:МСК|по московскому времени))?")
PRICE_RE = re.compile(r"\d[\d\s  ]*\s?(?:₽|руб)", re.IGNORECASE)
MARKER_RE = re.compile(r"\[(?:УТОЧНИТЬ|ПРОВЕРИТЬ|НУЖЕН ПРИМЕР АВТОРА)")
ZOOM_RE = re.compile(r",?\s*\bZoom\b\.?", re.IGNORECASE)

# Служебные указания для макета и пометки для владельца — блок удаляется целиком.
LAYOUT_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r"^\*\*(?:Надзаголовок|Фото|Кнопка|Подпись под кнопкой|Ссылка-якорь|Под формой|Под карточками)\b",
        r"^\*\*Форма № \d",
        r"\.jpg\b|public/assets",
        r"^\*Мобильная версия",
        r"^Все материалы получены",
        r"^По желанию владельца",
        # клиентов из опыта ведущего в базу не берём: бот о них не рассказывает
        r"^Работал с ",
    )
)
# Подписи полей макета перед текстом: «**Лид:** …» → «…».
LABEL_RE = re.compile(r"^\*\*(?:H1|Лид|Абзац|Карточки|Сноска|Строка внизу|Строка):\*\*\s*")
HEADING_LABEL_RE = re.compile(r"^\*\*(H2|H3):\*\*\s*")
# Точечные правки формулировок для бота (детерминированные).
REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\(обезличено, подтверждено владельцем; "), "(обезличено; "),
    (re.compile(r"^Этот сайт:"), "Сайт alexshein.com:"),
)


# --- разбор markdown на блоки ------------------------------------------------------------------


@dataclass
class Block:
    kind: str  # heading | para | item | row | quote | rule
    text: str
    level: int = 0  # для heading — число #
    ordered: bool = False  # для item — нумерованный пункт
    lines: list[str] = field(default_factory=list)  # для para — исходные строки


_ITEM_RE = re.compile(r"^(\s*)(?:[-*]|(\d+)\.)\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(Block("heading", heading.group(2).strip(), level=len(heading.group(1))))
            i += 1
            continue
        if re.fullmatch(r"-{3,}|\*{3,}", line.strip()):
            blocks.append(Block("rule", ""))
            i += 1
            continue
        if line.lstrip().startswith("|"):
            blocks.append(Block("row", line.strip()))
            i += 1
            continue
        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(Block("quote", " ".join(part for part in quote if part)))
            continue
        item = _ITEM_RE.match(line)
        if item:
            parts = [item.group(3).strip()]
            i += 1
            while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
                if _ITEM_RE.match(lines[i]):
                    break
                parts.append(lines[i].strip())
                i += 1
            blocks.append(Block("item", " ".join(parts), ordered=item.group(2) is not None))
            continue
        para: list[str] = []
        while i < len(lines):
            current = lines[i].rstrip()
            if not current.strip() or _HEADING_RE.match(current) or current.lstrip().startswith(("|", ">")):
                break
            if para and _ITEM_RE.match(current):
                break
            para.append(current.strip())
            i += 1
        blocks.append(Block("para", " ".join(para), lines=para))
    return blocks


def _section(blocks: list[Block], level: int, title_re: str) -> list[Block]:
    """Блоки под заголовком уровня `level`, чей текст совпадает с `title_re`, до следующего такого же."""
    pattern = re.compile(title_re)
    out: list[Block] | None = None
    for block in blocks:
        if block.kind == "heading" and block.level <= level:
            if out is not None:
                break
            if block.level == level and pattern.match(block.text):
                out = []
            continue
        if out is not None:
            out.append(block)
    return out or []


# --- чистка текста -----------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """Разбить на предложения; квадратные скобки пометок не режутся пополам."""
    sentences: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char in ".!?" and depth == 0:
            rest = text[index + 1 :]
            nxt = rest.lstrip()
            if rest[:1].isspace() and nxt[:1] and (nxt[0].isupper() or nxt[0] in '«[*"('):
                sentences.append(text[start : index + 1].strip())
                start = index + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _scrub_dates(text: str) -> str:
    """Убрать даты и время: их источник — facts.yaml (блок ФАКТЫ в промпте)."""
    starts_with_date = bool(DATE_RE.match(text))
    text = re.sub(r"[,:]?\s*" + TIME_RE.pattern, "", text)

    def _drop_date(match: re.Match[str]) -> str:
        pre, post = match.group("pre"), match.group("post")
        if pre.strip():  # перед датой «,» или «:»
            return post or ""
        return pre

    text = re.sub(rf"(?P<pre>[,:]\s*|\s+|^)(?:{DATE_RE.pattern})(?P<post>,\s*)?", _drop_date, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    if starts_with_date and text[:1].islower():
        text = text[0].upper() + text[1:]
    return text


def _scrub_heading(text: str) -> str:
    """«Неделя 1 · 7 ноября · Тема» → «Неделя 1 · Тема»; «5 декабря · демо-день» → «Демо-день»."""
    if "·" not in text:
        return _scrub_dates(text)
    parts = [part.strip() for part in text.split("·")]
    kept = [
        part
        for part in parts
        if not (DATE_RE.fullmatch(part) or TIME_RE.fullmatch(part) or re.fullmatch(WEEKDAYS, part))
    ]
    result = " · ".join(kept)
    return result[:1].upper() + result[1:]


def clean_text(text: str) -> str:
    """Вычистить утверждения с пометками и ценами, Zoom, даты и время. Пустая строка — блок удалить."""
    kept = [s for s in split_sentences(text) if not MARKER_RE.search(s) and not PRICE_RE.search(s)]
    result = " ".join(kept)
    result = ZOOM_RE.sub(lambda m: "." if m.group(0).endswith(".") else "", result)
    result = _scrub_dates(result)
    for pattern, replacement in REPLACEMENTS:
        result = pattern.sub(replacement, result)
    result = result.strip()
    if not re.search(r"[A-Za-zА-Яа-яЁё]", result):
        return ""
    return result


def _is_layout(text: str) -> bool:
    return any(pattern.search(text) for pattern in LAYOUT_RES)


def _row_cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def render_blocks(blocks: Iterable[Block], heading_shift: int = 0) -> list[str]:
    """Блоки → строки markdown после чистки. Пустые абзацы и вырезанные блоки пропускаются."""
    out: list[str] = []
    drop_list = False  # убран абзац «…:», за которым идёт его список
    number = 0
    last_item = False  # пункты списка идут подряд, без пустой строки

    def emit(chunk: str, *, list_item: bool = False) -> None:
        nonlocal last_item
        if out and not (list_item and last_item):
            out.append("")
        out.append(chunk)
        last_item = list_item

    for block in blocks:
        if block.kind != "item":
            number = 0
        if block.kind in ("rule",):
            continue
        if block.kind == "heading":
            drop_list = False
            text = _scrub_heading(block.text)
            text = re.sub(r"^\d+\.\s+", "", text)
            if text:
                emit("#" * max(2, block.level - heading_shift) + " " + text)
            continue
        if block.kind == "item":
            if drop_list or _is_layout(block.text):
                continue
            text = clean_text(block.text)
            if not text:
                continue
            if block.ordered:
                number += 1
                emit(f"{number}. {text}", list_item=True)
            else:
                emit(f"- {text}", list_item=True)
            continue
        drop_list = False
        if block.kind == "row":
            cells = _row_cells(block.text)
            if all(not cell or re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            if _is_layout(" ".join(cells)):
                continue
            cleaned = [clean_text(cell) for cell in cells]
            if len(cells) == 2:
                if not cleaned[0] or not cleaned[1]:
                    continue
                emit(f"- {cleaned[0]}: {cleaned[1]}", list_item=True)
            else:
                values = [cell for cell in cleaned if cell]
                if values:
                    emit("- " + " | ".join(values), list_item=True)
            continue
        if block.kind == "quote":
            text = clean_text(block.text)
            if text:
                emit(text)
            continue
        # абзац
        if _is_layout(block.text):
            drop_list = block.text.rstrip().endswith(":")
            continue
        heading_label = HEADING_LABEL_RE.match(block.text)
        if heading_label:
            level = 2 if heading_label.group(1) == "H2" else 3
            text = _scrub_dates(block.text[heading_label.end() :])
            if text:
                emit("#" * level + " " + text)
            continue
        lines = block.lines or [block.text]
        first = lines[0]
        if len(lines) > 1 and re.fullmatch(r"\*\*[^*]+\*\*", first):
            # вопрос FAQ (жирная строка) + ответ: без ответа вопрос не нужен
            answer = clean_text(" ".join(lines[1:]))
            if answer:
                emit(f"{first}\n{answer}")
            continue
        text = clean_text(LABEL_RE.sub("", block.text))
        if text:
            emit(text)
    return out


# --- сборка ------------------------------------------------------------------------------------


def _header(title: str, sources: str) -> str:
    return f"# {title}\n\n> {GENERATED_MARK} из {sources}, правки руками допустимы.\n"


def build_landing(landing_text: str) -> dict[str, str]:
    blocks = parse_blocks(landing_text)
    text_section = _section(blocks, 2, re.escape(LANDING_TEXT_SECTION))
    result: dict[str, str] = {}
    for filename, (title, prefixes) in LANDING_TARGETS.items():
        body: list[str] = []
        names: list[str] = []
        for prefix in prefixes:
            # разделы внутри «3. Текст по секциям» (###), «4. Блок ведущего» — верхнего уровня (##)
            heading = None
            sub: list[Block] = []
            for scope, level in ((text_section, 3), (blocks, 2)):
                heading = next(
                    (b.text for b in scope if b.kind == "heading" and b.level == level and b.text.startswith(prefix)),
                    None,
                )
                if heading is not None:
                    sub = _section(scope, level, re.escape(heading))
                    break
            if heading is None:
                continue
            names.append(f"«{heading}»")
            rendered = render_blocks(sub, heading_shift=1)
            if rendered:
                if body:
                    body.append("")
                body.extend(rendered)
        sources = f"materials/{LANDING_FILE} ({', '.join(names)})"
        content = _header(title, sources)
        if body:
            content += "\n" + "\n".join(body) + "\n"
        result[filename] = content
    return result


COURSE_INCLUDE = re.compile(
    r"программ|модул|заняти|недел|урок|формат|нагрузк|результат|артефакт|итог|инструмент|стек|mvp",
    re.IGNORECASE,
)
COURSE_EXCLUDE = re.compile(
    r"цен|тариф|стоимост|оплат|скидк|не обеща|тон\b|тон |стил|запрет|бюджет|медиаплан|kpi|реклам|воронк|промпт для",
    re.IGNORECASE,
)


def select_course_sections(blocks: list[Block]) -> list[Block]:
    """Разделы о программе, формате, результатах, инструментах (уровень заголовка ≥ 2)."""
    out: list[Block] = []
    taking_level: int | None = None
    skipping_level: int | None = None
    for block in blocks:
        if block.kind == "heading":
            if skipping_level is not None and block.level > skipping_level:
                continue
            skipping_level = None
            if taking_level is not None and block.level <= taking_level:
                taking_level = None
            if COURSE_EXCLUDE.search(block.text):
                skipping_level = block.level
                continue
            if taking_level is None and block.level >= 2 and COURSE_INCLUDE.search(block.text):
                taking_level = block.level
            if taking_level is not None:
                out.append(block)
            continue
        if skipping_level is None and taking_level is not None:
            out.append(block)
    return out


def build_course(course_dir: Path) -> str | None:
    present = [name for name in COURSE_FILES if (course_dir / name).is_file()]
    if not present:
        return None
    body: list[str] = []
    for name in present:
        blocks = parse_blocks((course_dir / name).read_text(encoding="utf-8"))
        selected = select_course_sections(blocks)
        if not selected:
            continue
        min_level = min((b.level for b in selected if b.kind == "heading"), default=2)
        rendered = render_blocks(selected, heading_shift=min_level - 3)
        if rendered:
            if body:
                body.append("")
            body.append(f"## Из {name}")
            body.append("")
            body.extend(rendered)
    sources = ", ".join(f"materials/course/{name}" for name in present)
    content = _header("Материалы курса", sources)
    if body:
        content += "\n" + "\n".join(body) + "\n"
    return content


def build(materials: Path = DEFAULT_MATERIALS) -> dict[str, str]:
    """Имя файла базы → содержимое."""
    result = build_landing((materials / LANDING_FILE).read_text(encoding="utf-8"))
    course = build_course(materials / "course")
    if course is not None:
        result[COURSE_TARGET] = course
    return dict(sorted(result.items()))


def write(files: dict[str, str], out: Path = DEFAULT_OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in files.items():
        path = out / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    # сгенерированный ранее файл, источника которого больше нет (course.md без materials/course)
    for path in sorted(out.glob("*.md")):
        if path.name not in files and GENERATED_MARK in path.read_text(encoding="utf-8")[:500]:
            path.unlink()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--materials", type=Path, default=DEFAULT_MATERIALS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    for path in write(build(args.materials), args.out):
        print(f"{path} ({path.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
