# -*- coding: utf-8 -*-
"""
Сбор черновика справочника предметов.

Для незнакомой области справочник не сочиняют, а вычитывают из писем:
  1. извлечение прогоняется БЕЗ справочника (ключ --svobodno),
     модель пишет предметы своими словами;
  2. этот скрипт сводит близкие написания в группы эмбеддером
     («фото для каталога» и «снимки портфолио» — одно и то же);
  3. раскладывает группы по проектам по числам: тема почти только
     в одном проекте — в его раздел, размазана — в общий;
  4. человек правит черновик. Это легче, чем сочинять список:
     надо не придумать темы, а решить, совпадают ли две строки.

Запуск:
    python predmety_sbor.py <facts.jsonl> [чернов.txt] [--porog 0.86] [--min 2]

Ключи:
    --porog  близость, при которой написания считаются одним (0..1)
    --min    сколько раз тема должна встретиться, чтобы попасть в черновик
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

MODEL_NAME = "deepvk/USER-bge-m3"


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def load(path):
    """Возвращает счётчик написаний и распределение по проектам."""
    counts = Counter()
    projects = defaultdict(Counter)
    display = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (r.get("subject_key") or "").strip()
        if not key:
            continue
        counts[key] += 1
        projects[key][(r.get("project") or "").upper()] += 1
        display.setdefault(key, r.get("subject", key))
    return counts, projects, display


def cluster(names, vectors, porog):
    """
    Сведение синонимов. Идём от самых частых: каждое следующее написание
    либо примыкает к готовой группе, либо заводит свою. Имя группы —
    самое частое написание в ней.
    """
    heads, groups = [], []
    for i, name in enumerate(names):
        best, best_score = -1, -1.0
        for hi, h in enumerate(heads):
            s = float(vectors[i] @ vectors[h])
            if s > best_score:
                best, best_score = hi, s
        if best >= 0 and best_score >= porog:
            groups[best].append(i)
        else:
            heads.append(i)
            groups.append([i])
    return heads, groups


def main():
    args = []
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--porog", "--min"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if not args:
        print(__doc__)
        return 1
    src = args[0]
    dst = args[1] if len(args) > 1 else str(Path(src).parent / "predmety-chernovik.txt")
    porog = float(get_flag("--porog", 0.86))
    minimum = int(get_flag("--min", 2))

    if not Path(src).is_file():
        print(f"ОШИБКА: не найден {src}")
        return 1

    counts, projects, display = load(src)
    if not counts:
        print("В файле нет предметов.")
        return 1

    total = sum(counts.values())
    ordered = [k for k, _ in counts.most_common()]
    print(f"Написаний всего: {len(ordered)}, фактов: {total}")

    from sentence_transformers import SentenceTransformer
    print("Загружаю эмбеддер (процессор) ...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    vecs = model.encode(ordered, normalize_embeddings=True, show_progress_bar=False)

    heads, groups = cluster(ordered, vecs, porog)
    print(f"После сведения синонимов: {len(heads)} тем "
          f"(порог близости {porog})\n")

    rows = []
    for hi, members in zip(heads, groups):
        name = display.get(ordered[hi], ordered[hi])
        n = sum(counts[ordered[m]] for m in members)
        pc = Counter()
        for m in members:
            pc.update(projects[ordered[m]])
        variants = [ordered[m] for m in members if m != hi]
        rows.append((name, n, pc, variants))
    rows.sort(key=lambda r: -r[1])

    obshee, po_proektam, redkie = [], defaultdict(list), []
    for name, n, pc, variants in rows:
        if n < minimum:
            redkie.append((name, n))
            continue
        top_proj, top_n = pc.most_common(1)[0]
        # тема считается «своей» для проекта, если 80% упоминаний оттуда
        if top_proj and top_n / max(n, 1) >= 0.8:
            po_proektam[top_proj].append((name, n, variants))
        else:
            obshee.append((name, n, variants))

    out = ["# Черновик справочника предметов, собран автоматически.",
           f"# Исходник: {src}",
           f"# Написаний было {len(ordered)}, после сведения синонимов {len(heads)}.",
           "#",
           "# ЧТО СДЕЛАТЬ РУКАМИ:",
           "#   - объединить строки, которые значат одно и то же;",
           "#   - выбросить лишнее, дописать пропущенное;",
           "#   - убрать числа в скобках, они справочные;",
           "#   - сохранить как predmety.txt и перегнать извлечение.",
           "#",
           "# В скобках — сколько раз встретилось. Через «~» — что сведено в эту тему.",
           ""]

    def block(items):
        for name, n, variants in items:
            tail = "   ~ " + ", ".join(variants[:4]) if variants else ""
            out.append(f"{name}                    # ({n}){tail}".replace(
                "                    #", " " * max(1, 34 - len(name)) + "#"))

    out.append("[ОБЩЕЕ]")
    block(obshee)
    out.append("прочее")
    out.append("")

    for proj in sorted(po_proektam):
        out.append(f"[{proj}]")
        block(po_proektam[proj])
        out.append("")

    if redkie:
        out.append(f"# Отброшено как редкое (реже {minimum} раз): {len(redkie)}")
        for name, n in redkie[:30]:
            out.append(f"#   {name} ({n})")

    Path(dst).write_text("\n".join(out), encoding="utf-8")

    odinochki = sum(1 for _, n, _, _ in rows if n == 1)
    print("=" * 66)
    print(f"  Тем в общем разделе : {len(obshee)}")
    for proj in sorted(po_proektam):
        print(f"  Тем в разделе {proj:<6}: {len(po_proektam[proj])}")
    print(f"  Отброшено редких    : {len(redkie)}")
    print(f"  Тем-одиночек        : {odinochki} из {len(rows)}")
    print("=" * 66)
    if odinochki > len(rows) * 0.6:
        print("  ВНИМАНИЕ: больше половины тем встретились один раз.")
        print("  Устойчивых предметов в переписке нет — группировать будет нечего.")
        print("  Похоже, переписка не проектная, а разовая. Тогда предмет стоит")
        print("  строить не по содержанию, а по контрагенту: одна тема = один клиент.")
    print(f"\nЧерновик записан: {dst}")
    print("Проверь его глазами, поправь и сохрани как predmety.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
