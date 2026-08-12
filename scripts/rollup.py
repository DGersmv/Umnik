# -*- coding: utf-8 -*-
"""
Свёртка фактов: что происходило по каждой теме и чем закончилось.

Ради этого всё и затевалось. Факты группируются по предмету,
сортируются по дате — и цепочка вида 180 -> 214 -> 176 становится
видна целиком. Поиск в этом не участвует, промахнуться негде.

Запуск:
    python rollup.py <facts.jsonl>                 все темы кратко
    python rollup.py <facts.jsonl> --project ARB   по одному проекту
    python rollup.py <facts.jsonl> --subject фото  по одной теме, подробно
    python rollup.py <facts.jsonl> --type деньги   только один тип
    python rollup.py <facts.jsonl> --full          вся история по всем темам
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Статусы, после которых обсуждение считается закрытым
FINAL = {"принято", "отклонено", "выполнено"}


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def ru(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m")
    except Exception:
        return (iso or "")[:10]


def load(path, project=None, subject=None, ftype=None):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if project and (r.get("project") or "").upper() != project.upper():
            continue
        if subject and subject.lower() not in (r.get("subject_key") or ""):
            continue
        if ftype and r.get("type") != ftype:
            continue
        rows.append(r)
    return sorted(rows, key=lambda r: (r.get("date") or ""))


def group(rows):
    """По (проект, предмет). Внутри — по дате."""
    g = defaultdict(list)
    for r in rows:
        g[(r.get("project", ""), r.get("subject", ""))].append(r)
    return dict(sorted(g.items(), key=lambda kv: (kv[0][0], -len(kv[1]))))


def changed(facts):
    """
    Типы, где значение менялось со временем.
    Именно здесь живут ловушки вроде 180 -> 214 -> 176.
    """
    by_type = defaultdict(list)
    for f in facts:
        by_type[f["type"]].append(f)
    out = {}
    for t, items in by_type.items():
        vals = [i["value"] for i in items]
        if len(items) > 1 and len(set(vals)) > 1:
            out[t] = items
    return out


def state(facts):
    """Текущее состояние: по каждому типу — последний факт."""
    last = {}
    for f in facts:
        last[f["type"]] = f
    return last


# ---------------------------------------------------------------- для ответов

def subjects_of_files(facts_path, files):
    """
    Темы, затронутые в этих письмах. Нужно, чтобы от найденных поиском
    писем перейти к их темам, а от тем — ко всей истории по ним.
    """
    path = Path(facts_path)
    if not path.is_file() or not files:
        return set()
    want = set(files)
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("file") in want and r.get("subject_key"):
            out.add(r["subject_key"])
    return out


def facts_block(facts_path, subjects, projects=None, max_chars=2500,
                max_lines_per_subject=12):
    """
    Готовый кусок текста для запроса к модели: цепочки фактов
    по указанным темам, по возрастанию даты.

    Зачем: поиск отдаёт пять писем из тысяч, и последнее звено цепочки
    в них может не попасть. Таблица фактов собрана по всему архиву,
    поэтому «180 -> 214 -> 176» видна целиком независимо от поиска.
    """
    path = Path(facts_path)
    if not path.is_file() or not subjects:
        return ""
    subs_order = [s.strip().lower().replace("ё", "е") for s in subjects if s]
    subs = set(subs_order)
    projs = {p.upper() for p in (projects or []) if p}

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (r.get("subject_key") or "").lower() not in subs:
            continue
        if projs and (r.get("project") or "").upper() not in projs:
            continue
        rows.append(r)
    if not rows:
        return ""

    g = defaultdict(list)
    for r in sorted(rows, key=lambda r: r.get("date") or ""):
        g[(r.get("project", ""), r.get("subject", ""))].append(r)

    # Порядок тем — как их передали: подбор по смыслу вернул самую
    # подходящую первой. Сортировать по числу фактов нельзя: многолюдные
    # темы съедят объём, и нужная не поместится. Эта ошибка уже была.
    rank = {s: i for i, s in enumerate(subs_order)} if subs_order else {}

    def key(kv):
        (_, subj), items = kv
        k = subj.strip().lower().replace("ё", "е")
        return (rank.get(k, 10**6), -len(items))

    out, spent = [], 0
    for (proj, subj), items in sorted(g.items(), key=key):
        head = f"[{proj}] {subj}"
        lines = [head]
        shown_items = items
        if len(items) > max_lines_per_subject:
            cut = len(items) - max_lines_per_subject
            shown_items = items[cut:]
            lines.append(f"   (ранее ещё {cut} записей)")
        for f in shown_items:
            who = f", {f['who']}" if f.get("who") else ""
            lines.append(f"   {ru(f['date'])}  {f['type']}, {f['status']}: "
                         f"{f['value']}{who}")
        chunk = "\n".join(lines)
        if spent + len(chunk) > max_chars and out:
            break
        out.append(chunk)
        spent += len(chunk)

    return ("ФАКТЫ ПО ТЕМАМ, собранные из всего архива (по возрастанию даты, "
            "последняя строка темы — действующее значение):\n\n"
            + "\n\n".join(out))


def main():
    args = []
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--project", "--subject", "--type"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if not args:
        print(__doc__)
        return 1
    path = args[0]
    if not Path(path).is_file():
        print(f"ОШИБКА: не найден {path}")
        return 1

    subject = get_flag("--subject")
    full = "--full" in sys.argv or bool(subject)
    rows = load(path, get_flag("--project"), subject, get_flag("--type"))
    if not rows:
        print("Под эти условия ничего не подошло.")
        return 0

    groups = group(rows)
    print("=" * 78)
    print(f"  Фактов: {len(rows)}   тем: {len(groups)}")
    print("=" * 78)

    total_changed = 0
    for (proj, subj), facts in groups.items():
        ch = changed(facts)
        total_changed += len(ch)
        a, b = ru(facts[0]["date"]), ru(facts[-1]["date"])
        period = a if a == b else f"{a}-{b}"
        mark = "  ИЗМЕНЕНИЯ" if ch else ""
        print(f"\n[{proj}] {subj}   ({len(facts)} фактов, {period}){mark}")

        if full:
            for f in facts:
                who = f"  ({f['who']})" if f.get("who") else ""
                dl = f"  срок {f['deadline']}" if f.get("deadline") else ""
                flag = " <-- менялось" if f["type"] in ch else ""
                print(f"    {ru(f['date'])}  {f['type']:8} {f['status']:12} "
                      f"{f['value'][:60]}{who}{dl}{flag}")
                print(f"              {f['file']}")
        else:
            for t, f in sorted(state(facts).items()):
                tail = f"   (менялось {len(ch[t])} раз)" if t in ch else ""
                print(f"    {f['type']:8} {f['status']:12} {f['value'][:58]}{tail}")

    print("\n" + "-" * 78)
    print(f"  Тем со сменой значений: {total_changed}")
    if not full:
        print("  Подробная история: ключ --full или --subject <часть названия>")

    open_items = [f for f in rows
                  if f["type"] == "задача" and f["status"] not in FINAL]
    if open_items:
        print(f"\n  Незакрытых задач: {len(open_items)}")
        for f in open_items[-10:]:
            who = f" — {f['who']}" if f.get("who") else ""
            print(f"    {ru(f['date'])} [{f['project']}] {f['subject']}: "
                  f"{f['value'][:48]}{who}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
