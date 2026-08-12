# -*- coding: utf-8 -*-
"""
Разбор писем тестового корпуса (.txt с шапкой) в единый формат.

Запуск:
    python parser.py <папка_с_письмами> [файл_результата.jsonl]

Модуль, а не скрипт: функции возвращают значения и ничего не печатают.
Печатает только блок __main__ внизу.
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- константы

SEP = re.compile(r"^={10,}\s*$")          # строка-разделитель из "="
FIELD = re.compile(r"^([А-Яа-яЁё\w][^:]{0,20}):\s*(.*)$")
ADDR = re.compile(r"^\s*(.*?)\s*<([^>]+)>\s*$")
EMPTY = {"", "-", "—", "–", "нет", "отсутствуют"}

# признаки процитированного текста в теле
QUOTE_MARKS = (
    re.compile(r"^>", re.M),
    re.compile(r"^-{2,}\s*Исходное сообщение", re.M | re.I),
    re.compile(r"^-{2,}\s*Original Message", re.M | re.I),
    re.compile(r"^\d{2}\.\d{2}\.\d{4}.{0,40}(писал|wrote)", re.M | re.I),
)

# русское имя поля -> английский ключ
KEYS = {
    "от": "from", "кому": "to", "копия": "cc", "скрытая копия": "bcc",
    "дата": "date", "тема": "subject", "проект": "project",
    "вложения": "attachments", "вложение": "attachments",
}


# ---------------------------------------------------------------- утилиты

def _read(path: Path) -> str:
    """Читает файл. UTF-8 основной, cp1251 как запасной вариант."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _is_empty(value: str) -> bool:
    return value.strip().lower() in EMPTY


def _people(value: str) -> list:
    """'Имя <a@b.lv>, Имя2 <c@d.lv>' -> [{'name':..,'email':..}, ...]"""
    if _is_empty(value):
        return []
    out = []
    for part in re.split(r"[,;]\s*(?![^<]*>)", value):
        part = part.strip()
        if not part:
            continue
        m = ADDR.match(part)
        if m:
            out.append({"name": m.group(1).strip(), "email": m.group(2).strip().lower()})
        elif "@" in part:
            out.append({"name": "", "email": part.lower()})
        else:
            out.append({"name": part, "email": ""})
    return out


def _files(value: str) -> list:
    if _is_empty(value):
        return []
    return [p.strip() for p in re.split(r"[,;]\s*", value) if p.strip()]


def _date(value: str):
    """'01.06.2026 09:14' -> ISO. Возвращает (iso, ошибка)."""
    v = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).isoformat(), None
        except ValueError:
            continue
    return None, f"дата не разобрана: {v!r}"


def _project_code(value: str):
    """'ARB — Сайт SIA \"Arbor\"' -> ('ARB', 'Сайт SIA \"Arbor\"')"""
    if _is_empty(value):
        return "", ""
    m = re.match(r"^\s*([A-Z]{2,5})\b[\s—–-]*(.*)$", value.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "", value.strip()


def _split(text: str):
    """Делит файл на шапку и тело по строкам из '='."""
    lines = text.split("\n")
    seps = [i for i, ln in enumerate(lines) if SEP.match(ln)]
    if len(seps) >= 2:
        return lines[seps[0] + 1:seps[1]], "\n".join(lines[seps[1] + 1:]).strip()
    if len(seps) == 1:
        return lines[:seps[0]], "\n".join(lines[seps[0] + 1:]).strip()
    # шапки нет — ищем первую пустую строку
    for i, ln in enumerate(lines):
        if not ln.strip():
            return lines[:i], "\n".join(lines[i + 1:]).strip()
    return [], text.strip()


def _headers(lines) -> dict:
    """Разбирает строки шапки. Продолжения (без 'Поле:') клеятся к предыдущему."""
    head, last = {}, None
    for ln in lines:
        if not ln.strip():
            continue
        m = FIELD.match(ln)
        if m and m.group(1).strip().lower() in KEYS:
            last = KEYS[m.group(1).strip().lower()]
            head[last] = m.group(2).strip()
        elif last:
            head[last] = (head[last] + " " + ln.strip()).strip()
    return head


def _has_quote(body: str) -> bool:
    return any(rx.search(body) for rx in QUOTE_MARKS)


# ---------------------------------------------------------------- разбор

def parse_file(path) -> dict:
    """Разбирает одно письмо. Всегда возвращает словарь; ошибки в поле errors."""
    path = Path(path)
    text = _read(path)
    head_lines, body = _split(text)
    h = _headers(head_lines)
    errors = []

    iso, err = _date(h.get("date", "")) if h.get("date") else (None, "нет поля Дата")
    if err:
        errors.append(err)

    frm = _people(h.get("from", ""))
    if not frm:
        errors.append("не разобрано поле От")
    if not body:
        errors.append("пустое тело письма")

    code, title = _project_code(h.get("project", ""))
    subject = h.get("subject", "").strip()

    # ключ повторности: у .eml это Message-ID, здесь его нет — считаем сами
    key = f"{iso or path.name}|{frm[0]['email'] if frm else ''}|{subject}"
    msg_id = hashlib.sha1(key.encode("utf-8")).hexdigest()

    return {
        "id": msg_id,
        "source": "synthetic-txt",
        "file": path.name,
        "path": str(path),
        "date": iso,
        "from_name": frm[0]["name"] if frm else "",
        "from_email": frm[0]["email"] if frm else "",
        "to": _people(h.get("to", "")),
        "cc": _people(h.get("cc", "")),
        "subject": subject,
        "project": code,
        "project_title": title,
        "attachments": _files(h.get("attachments", "")),
        "body": body,
        "body_chars": len(body),
        "has_quote": _has_quote(body),
        "errors": errors,
    }


def parse_folder(folder, pattern="*.txt") -> list:
    """Разбирает папку. Возвращает список писем, отсортированный по дате."""
    files = sorted(p for p in Path(folder).glob(pattern) if p.is_file())
    letters = [parse_file(p) for p in files]
    return sorted(letters, key=lambda x: (x["date"] or "", x["file"]))


def save_jsonl(letters, out_path) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in letters:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(letters)


def stats(letters) -> dict:
    """Сводка для контроля качества разбора."""
    ok = [x for x in letters if not x["errors"]]
    bad = [x for x in letters if x["errors"]]
    dates = sorted(x["date"] for x in letters if x["date"])
    projects = {}
    for x in letters:
        projects[x["project"] or "(пусто)"] = projects.get(x["project"] or "(пусто)", 0) + 1
    sizes = sorted(x["body_chars"] for x in letters) or [0]
    return {
        "всего": len(letters),
        "без ошибок": len(ok),
        "с ошибками": len(bad),
        "период": (dates[0][:10], dates[-1][:10]) if dates else ("—", "—"),
        "проекты": projects,
        "с цитатами": sum(1 for x in letters if x["has_quote"]),
        "с вложениями": sum(1 for x in letters if x["attachments"]),
        "уникальных id": len({x["id"] for x in letters}),
        "тело мин/медиана/макс": (sizes[0], sizes[len(sizes) // 2], sizes[-1]),
        "ошибки": [(x["file"], x["errors"]) for x in bad],
    }


# ---------------------------------------------------------------- запуск

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python parser.py <папка_с_письмами> [результат.jsonl]")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.is_dir():
        print(f"ОШИБКА: папка не найдена: {src}")
        sys.exit(1)

    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / "letters.jsonl"

    letters = parse_folder(src)
    if not letters:
        print(f"ОШИБКА: в папке {src} нет файлов .txt")
        sys.exit(1)

    s = stats(letters)
    print("=" * 60)
    print(f"  Разобрано писем : {s['всего']}")
    print(f"  Без ошибок      : {s['без ошибок']}")
    print(f"  С ошибками      : {s['с ошибками']}")
    print(f"  Период          : {s['период'][0]} .. {s['период'][1]}")
    print(f"  Проекты         : {s['проекты']}")
    print(f"  С цитатами      : {s['с цитатами']}")
    print(f"  С вложениями    : {s['с вложениями']}")
    print(f"  Уникальных id   : {s['уникальных id']}  (должно совпасть с числом писем)")
    print(f"  Тело, символов  : мин {s['тело мин/медиана/макс'][0]}, "
          f"медиана {s['тело мин/медиана/макс'][1]}, макс {s['тело мин/медиана/макс'][2]}")
    if s["ошибки"]:
        print("-" * 60)
        print("  ПРОБЛЕМНЫЕ ФАЙЛЫ:")
        for name, errs in s["ошибки"]:
            print(f"    {name}: {'; '.join(errs)}")
    print("=" * 60)

    n = save_jsonl(letters, dst)
    print(f"Записано {n} писем в {dst}")
