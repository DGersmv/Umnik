# -*- coding: utf-8 -*-
"""
Сверка результата парсера с эталонным реестром INDEX-perepiski.csv.

Запуск:
    python check_index.py <letters.jsonl> <INDEX-perepiski.csv>

Проверяет не «отработало без падений», а «извлекло правильно»:
дату, время, код проекта, отправителя, получателя, тему, вложения.
"""

import csv
import json
import sys
from pathlib import Path


def load_letters(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            out[r["file"]] = r
    return out


def clean_key(k):
    """Убирает BOM и пробелы из имени колонки."""
    if k is None:
        return ""
    return k.replace("\ufeff", "").replace("\\xef\\xbb\\xbf", "").strip()


def load_index(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if not reader.fieldnames:
            raise SystemExit("ОШИБКА: INDEX пуст или не читается")
        rows = [{clean_key(k): v for k, v in row.items()} for row in reader]

    need = ["Файл", "Дата", "Время", "Код", "От", "Кому", "Тема письма"]
    have = set(rows[0].keys()) if rows else set()
    lost = [c for c in need if c not in have]
    if lost:
        raise SystemExit(
            f"ОШИБКА: в INDEX нет колонок {lost}\n"
            f"       найдены: {sorted(have)}"
        )
    return rows


def norm(s):
    """Приводит строку к сравнимому виду: тире, кавычки, пробелы."""
    if s is None:
        return ""
    s = str(s).strip()
    for a, b in (("—", "-"), ("–", "-"), ("\u00a0", " "), ("«", '"'), ("»", '"')):
        s = s.replace(a, b)
    return " ".join(s.split()).lower()


def is_empty(s):
    return norm(s) in ("", "-", "нет")


def names(people):
    return [p["name"] for p in people if p.get("name")]


def main(jsonl_path, index_path):
    letters = load_letters(jsonl_path)
    rows = load_index(index_path)

    print("=" * 64)
    print(f"  Писем разобрано : {len(letters)}")
    print(f"  Строк в INDEX   : {len(rows)}")

    missing = [r["Файл"] for r in rows if r["Файл"] not in letters]
    extra = [f for f in letters if f not in {r["Файл"] for r in rows}]
    if missing:
        print(f"  НЕТ В РАЗБОРЕ   : {len(missing)} -> {missing[:5]}")
    if extra:
        print(f"  ЛИШНИЕ ФАЙЛЫ    : {len(extra)} -> {extra[:5]}")

    problems = {}
    checked = 0

    for r in rows:
        fn = r["Файл"]
        L = letters.get(fn)
        if not L:
            continue
        checked += 1
        bad = []

        # дата и время: 01.06.2026 + 09:14  ->  2026-06-01T09:14:00
        want = f"{r['Дата']} {r['Время']}".strip()
        got = L["date"] or ""
        if got:
            d, t = got.split("T")
            y, m, dd = d.split("-")
            got_fmt = f"{dd}.{m}.{y} {t[:5]}"
        else:
            got_fmt = ""
        if norm(want) != norm(got_fmt):
            bad.append(f"дата: INDEX={want!r} разбор={got_fmt!r}")

        if norm(r["Код"]) != norm(L["project"]):
            bad.append(f"проект: INDEX={r['Код']!r} разбор={L['project']!r}")

        if norm(r["От"]) not in norm(L["from_name"]):
            bad.append(f"от: INDEX={r['От']!r} разбор={L['from_name']!r}")

        got_to = norm(", ".join(names(L["to"]) + names(L["cc"])))
        for who in [w for w in r["Кому"].split(",") if w.strip()]:
            if norm(who) not in got_to:
                bad.append(f"кому: {who.strip()!r} нет среди {got_to!r}")

        if norm(r["Тема письма"]) != norm(L["subject"]):
            bad.append(f"тема: INDEX={r['Тема письма']!r} разбор={L['subject']!r}")

        idx_att = is_empty(r.get("Вложения"))
        got_att = not L["attachments"]
        if idx_att != got_att:
            bad.append(f"вложения: INDEX={r.get('Вложения')!r} разбор={L['attachments']}")

        if bad:
            problems[fn] = bad

    print(f"  Сверено         : {checked}")
    print(f"  Расхождений     : {len(problems)}")
    print("=" * 64)

    if problems:
        for fn, bad in list(problems.items())[:15]:
            print(f"\n  {fn}")
            for b in bad:
                print(f"      {b}")
        if len(problems) > 15:
            print(f"\n  ... и ещё {len(problems) - 15} файлов")
    else:
        print("  СВЕРКА ПРОЙДЕНА: все поля совпали с эталоном")
    print()
    return 1 if (problems or missing or extra) else 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python check_index.py <letters.jsonl> <INDEX-perepiski.csv>")
        sys.exit(1)
    for p in sys.argv[1:3]:
        if not Path(p).is_file():
            print(f"ОШИБКА: файл не найден: {p}")
            sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
