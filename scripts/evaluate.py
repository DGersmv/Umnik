# -*- coding: utf-8 -*-
"""
Оценка качества поиска по контрольным вопросам.

Считает не «похоже на правду», а число: на каком месте в выдаче
оказалось письмо, содержащее верный ответ.

Запуск:
    python evaluate.py <папка_базы> <voprosy.txt> [--top 5] [--cpu] [--save отчёт.txt]

Файл вопросов:
    вопрос || подстрока1 | подстрока2
Засчитано, если хотя бы одна подстрока найдена в теле хотя бы одного
из выданных писем.
"""

import re
import sys
from pathlib import Path

MODEL_NAME = "deepvk/USER-bge-m3"
COLLECTION = "letters"


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def norm(text):
    """
    Приводит текст к сравнимому виду:
    ё->е, длинные тире->дефис, склеивает пробелы внутри чисел (5 200 -> 5200).
    """
    t = str(text).lower().replace("ё", "е")
    for a in ("—", "–", "\u2011"):
        t = t.replace(a, "-")
    t = t.replace("\u00a0", " ")
    t = re.sub(r"(?<=\d)[\s.](?=\d{3}\b)", "", t)   # 5 200 / 5.200 -> 5200
    return re.sub(r"\s+", " ", t)


def load_questions(path):
    out = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "||" not in line:
            print(f"  пропущена строка без '||': {line[:60]}")
            continue
        q, ans = line.split("||", 1)
        variants = [norm(a).strip() for a in ans.split("|") if a.strip()]
        variants = [v for v in variants if v]
        if q.strip() and variants:
            out.append((q.strip(), variants))
    return out


def rank_of_answer(hits, variants):
    """Место первого письма, где нашёлся ответ. 0 — не нашлось нигде."""
    for i, h in enumerate(hits, 1):
        blob = norm(h.payload.get("subject", "") + " " + h.payload.get("body", ""))
        if any(v in blob for v in variants):
            return i
    return 0


def main():
    args = []
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--top", "--save"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if len(args) < 2:
        print(__doc__)
        return 1

    db_path, qfile = args[0], args[1]
    top = int(get_flag("--top", 5))
    device = "cpu" if "--cpu" in sys.argv else None

    for p in (db_path, qfile):
        if not Path(p).exists():
            print(f"ОШИБКА: не найдено: {p}")
            return 1

    questions = load_questions(qfile)
    if not questions:
        print("ОШИБКА: в файле нет ни одного вопроса")
        return 1

    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(db_path))
    if not client.collection_exists(COLLECTION):
        print("ОШИБКА: коллекции нет, сначала запусти indexer.py")
        return 1

    from sentence_transformers import SentenceTransformer
    print(f"Вопросов: {len(questions)}. Загружаю модель ...")
    model = SentenceTransformer(MODEL_NAME, device=device)
    print(f"Устройство: {model.device}\n")

    lines = []
    ranks = []
    for q, variants in questions:
        qv = model.encode([q], normalize_embeddings=True)[0]
        hits = client.query_points(COLLECTION, query=qv.tolist(),
                                   limit=top, with_payload=True).points
        r = rank_of_answer(hits, variants)
        ranks.append(r)
        mark = f"место {r}" if r else "НЕ НАЙДЕНО"
        flag = "  " if 0 < r <= 3 else ("~ " if r else "! ")
        lines.append(f"{flag}[{mark:>10}]  {q}")
        if not r:
            got = "; ".join(f"{h.payload['file']}" for h in hits[:3])
            lines.append(f"              выдано: {got}")

    n = len(ranks)
    hit1 = sum(1 for r in ranks if r == 1)
    hit3 = sum(1 for r in ranks if 0 < r <= 3)
    hit5 = sum(1 for r in ranks if 0 < r <= top)
    mrr = sum(1 / r for r in ranks if r) / n

    report = []
    report.append("=" * 72)
    report.append("  РЕЗУЛЬТАТ ПОИСКА ПО КОНТРОЛЬНЫМ ВОПРОСАМ")
    report.append("=" * 72)
    report.extend(lines)
    report.append("-" * 72)
    report.append(f"  Вопросов                : {n}")
    report.append(f"  Ответ на 1-м месте      : {hit1}  ({100*hit1/n:.0f}%)")
    report.append(f"  Ответ в первой тройке   : {hit3}  ({100*hit3/n:.0f}%)")
    report.append(f"  Ответ в первых {top}        : {hit5}  ({100*hit5/n:.0f}%)")
    report.append(f"  Не найдено вовсе        : {n - hit5}")
    report.append(f"  Средняя обратная позиция: {mrr:.3f}  (1.000 — всегда первым)")
    report.append("=" * 72)
    text = "\n".join(report)
    print(text)

    save = get_flag("--save")
    if save:
        Path(save).write_text(text + "\n", encoding="utf-8")
        print(f"\nОтчёт записан: {save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
