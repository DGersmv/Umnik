# -*- coding: utf-8 -*-
"""
Поиск по базе писем. Без языковой модели — показывает найденные письма,
а не ответ. Это контрольная точка: если поиск достаёт не те письма,
никакая Qwen поверх не спасёт, она уверенно соврёт по неверным исходникам.

Запуск:
    python search.py <папка_базы>                     — диалоговый режим
    python search.py <папка_базы> "вопрос"            — разовый запрос
    python search.py <папка_базы> --file voprosy.txt  — пачкой из файла

Ключи:
    --top N          сколько показывать (по умолчанию 5)
    --project ARB    только по одному проекту
    --month 2026-06  только за месяц
    --cpu            держать модель на процессоре (когда VRAM нужна другому)
    --full           показывать тело письма целиком, а не начало
"""

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


def build_filter(project=None, month=None):
    if not project and not month:
        return None
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    must = []
    if project:
        must.append(FieldCondition(key="project", match=MatchValue(value=project.upper())))
    if month:
        must.append(FieldCondition(key="year_month", match=MatchValue(value=month)))
    return Filter(must=must)


def search(client, model, question, top=5, flt=None):
    qv = model.encode([question], normalize_embeddings=True)[0]
    return client.query_points(
        COLLECTION, query=qv.tolist(), limit=top, query_filter=flt, with_payload=True
    ).points


def show(hits, question, full=False):
    print()
    print("─" * 72)
    print(f"ВОПРОС: {question}")
    print("─" * 72)
    if not hits:
        print("  ничего не найдено (проверь фильтры)")
        return
    for n, h in enumerate(hits, 1):
        p = h.payload
        att = f"  вложения: {', '.join(p['attachments'])}" if p.get("attachments") else ""
        print(f"\n{n}. [{h.score:.3f}] {p['date'][:10]}  {p['project']}  {p['file']}")
        print(f"   Тема: {p['subject']}")
        print(f"   От: {p['from_name']} → {', '.join(p.get('to_names') or [])}{att}")
        body = p.get("body", "")
        if full:
            print("   " + body.replace("\n", "\n   "))
        else:
            snippet = " ".join(body.split())[:220]
            print(f"   {snippet}...")
    scores = [h.score for h in hits]
    print(f"\n   разброс оценок: {min(scores):.3f} .. {max(scores):.3f} "
          f"(важен порядок, не абсолютное значение)")


def main():
    args = []
    skip = False
    for i, a in enumerate(sys.argv[1:], 1):
        if skip:
            skip = False
            continue
        if a in ("--top", "--project", "--month", "--file"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if not args:
        print(__doc__)
        return 1

    db_path = args[0]
    if not Path(db_path).is_dir():
        print(f"ОШИБКА: папка базы не найдена: {db_path}")
        return 1

    top = int(get_flag("--top", 5))
    flt = build_filter(get_flag("--project"), get_flag("--month"))
    full = "--full" in sys.argv
    device = "cpu" if "--cpu" in sys.argv else None

    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(db_path))
    if not client.collection_exists(COLLECTION):
        print(f"ОШИБКА: в базе нет коллекции '{COLLECTION}'. Сначала запусти indexer.py")
        return 1
    total = client.count(COLLECTION).count

    from sentence_transformers import SentenceTransformer
    print(f"Загружаю модель ... (в базе {total} писем)")
    model = SentenceTransformer(MODEL_NAME, device=device)
    print(f"Готово. Устройство: {model.device}")

    # пачкой из файла
    qfile = get_flag("--file")
    if qfile:
        questions = [q.strip() for q in Path(qfile).read_text(encoding="utf-8").splitlines()
                     if q.strip() and not q.startswith("#")]
        for q in questions:
            show(search(client, model, q, top, flt), q, full)
        print(f"\nПроверено вопросов: {len(questions)}")
        return 0

    # разовый запрос
    if len(args) > 1:
        q = " ".join(args[1:])
        show(search(client, model, q, top, flt), q, full)
        return 0

    # диалоговый режим: модель загружена один раз, вопросов сколько угодно
    print("Вводи вопросы. Пустая строка или 'выход' — закончить.\n")
    while True:
        try:
            q = input("вопрос> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q or q.lower() in ("выход", "exit", "quit"):
            break
        show(search(client, model, q, top, flt), q, full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
