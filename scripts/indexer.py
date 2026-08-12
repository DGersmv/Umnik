# -*- coding: utf-8 -*-
"""
Построение векторного индекса из letters.jsonl.

Запуск:
    python indexer.py <letters.jsonl> <папка_базы> [--batch N] [--recreate]

Одно письмо = одна точка в базе. Нарезка не нужна: самое длинное письмо
корпуса — 4426 символов, потолок модели — 8192 токена.

Повторный запуск не плодит дубликаты: идентификатор точки считается из
содержимого письма, запись идёт через upsert (перезапись по совпадению).
"""

import json
import sys
import time
import uuid
from pathlib import Path

MODEL_NAME = "deepvk/USER-bge-m3"
COLLECTION = "letters"


# ---------------------------------------------------------------- данные

def load_letters(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_text(letter):
    """
    Что именно кодируем. Тема несёт много смысла в коротком виде,
    поэтому идёт в текст вместе с телом, а не только в поля.
    """
    parts = []
    if letter.get("subject"):
        parts.append(f"Тема: {letter['subject']}")
    if letter.get("project_title"):
        parts.append(f"Проект: {letter['project_title']}")
    parts.append(letter.get("body", ""))
    return "\n\n".join(p for p in parts if p)


def point_id(letter):
    """Из sha1-строки парсера делаем UUID — Qdrant принимает только int или UUID."""
    return str(uuid.UUID(hex=letter["id"][:32]))


def payload(letter):
    """Поля, по которым фильтруем и которые показываем в выдаче."""
    date = letter.get("date") or ""
    ts = 0
    if date:
        from datetime import datetime
        ts = int(datetime.fromisoformat(date).timestamp())
    return {
        "file": letter.get("file", ""),
        "date": date,
        "date_ts": ts,
        "year_month": date[:7],
        "project": letter.get("project", ""),
        "project_title": letter.get("project_title", ""),
        "subject": letter.get("subject", ""),
        "from_name": letter.get("from_name", ""),
        "from_email": letter.get("from_email", ""),
        "to_names": [p.get("name", "") for p in letter.get("to", [])],
        "attachments": letter.get("attachments", []),
        "body": letter.get("body", ""),
    }


# ---------------------------------------------------------------- база

def open_db(db_path):
    from qdrant_client import QdrantClient
    Path(db_path).mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(db_path))


def ensure_collection(client, dim, recreate=False):
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    exists = client.collection_exists(COLLECTION)
    if exists and recreate:
        client.delete_collection(COLLECTION)
        exists = False

    if exists:
        info = client.get_collection(COLLECTION)
        have = info.config.params.vectors.size
        if have != dim:
            raise SystemExit(
                f"ОШИБКА: в базе коллекция на {have} измерений, модель даёт {dim}.\n"
                f"       Смена модели требует перестройки: запусти с ключом --recreate"
            )
        return False

    client.create_collection(
        COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    # Индексы по полям ускоряют фильтрацию только в серверном Qdrant.
    # В локальном режиме молча игнорируются — код оставлен на будущее.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for field, schema in (("project", PayloadSchemaType.KEYWORD),
                              ("date_ts", PayloadSchemaType.INTEGER),
                              ("year_month", PayloadSchemaType.KEYWORD),
                              ("from_email", PayloadSchemaType.KEYWORD)):
            try:
                client.create_payload_index(COLLECTION, field, field_schema=schema)
            except Exception:
                pass
    return True


# ---------------------------------------------------------------- запуск

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if len(args) < 2:
        print("Использование: python indexer.py <letters.jsonl> <папка_базы> "
              "[--batch N] [--recreate]")
        return 1

    jsonl, db_path = args[0], args[1]
    recreate = "--recreate" in flags
    batch = 16
    for i, a in enumerate(sys.argv):
        if a == "--batch" and i + 1 < len(sys.argv):
            batch = int(sys.argv[i + 1])

    if not Path(jsonl).is_file():
        print(f"ОШИБКА: файл не найден: {jsonl}")
        return 1

    letters = load_letters(jsonl)
    print(f"Писем на входе: {len(letters)}")

    ids = {point_id(x) for x in letters}
    if len(ids) != len(letters):
        print(f"ВНИМАНИЕ: одинаковых идентификаторов — "
              f"{len(letters) - len(ids)}. Такие письма перезапишут друг друга.")

    import torch
    from sentence_transformers import SentenceTransformer

    print(f"Загружаю модель {MODEL_NAME} ...")
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    dim = (model.get_embedding_dimension()
           if hasattr(model, "get_embedding_dimension")
           else model.get_sentence_embedding_dimension())
    print(f"  готово за {time.time() - t0:.1f} с; устройство {model.device}, "
          f"измерений {dim}, потолок {model.max_seq_length} токенов")

    texts = [build_text(x) for x in letters]

    # предупреждение об обрезке: письмо длиннее потолка потеряет хвост
    tok = model.tokenizer
    too_long = [(letters[i]["file"], n) for i, n in
                enumerate(len(tok.encode(t)) for t in texts)
                if n > model.max_seq_length]
    if too_long:
        print(f"ВНИМАНИЕ: {len(too_long)} писем длиннее потолка, хвост будет обрезан:")
        for fn, n in too_long[:5]:
            print(f"    {fn}: {n} токенов")

    print(f"Кодирую пачками по {batch} ...")
    t0 = time.time()
    vectors = model.encode(
        texts,
        batch_size=batch,
        normalize_embeddings=True,   # для косинусной меры
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    enc_time = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 1024 ** 2 if torch.cuda.is_available() else 0
    print(f"  готово за {enc_time:.1f} с "
          f"({len(letters) / max(enc_time, 0.01):.1f} писем/с); пик VRAM {peak:.0f} МБ")

    from qdrant_client.models import PointStruct
    client = open_db(db_path)
    created = ensure_collection(client, dim, recreate=recreate)
    print(f"Коллекция '{COLLECTION}': {'создана' if created else 'уже была, дописываю'}")

    points = [
        PointStruct(id=point_id(x), vector=v.tolist(), payload=payload(x))
        for x, v in zip(letters, vectors)
    ]
    for i in range(0, len(points), 64):
        client.upsert(COLLECTION, points=points[i:i + 64])

    total = client.count(COLLECTION).count
    print("=" * 60)
    print(f"  Записано точек : {len(points)}")
    print(f"  Всего в базе   : {total}")
    print(f"  Папка базы     : {Path(db_path).resolve()}")
    print("=" * 60)

    # быстрая проверка вменяемости: индекс не должен быть шумом
    q = "изменение объёма работ и пересчёт сметы"
    qv = model.encode([q], normalize_embeddings=True)[0]
    hits = client.query_points(COLLECTION, query=qv.tolist(), limit=3).points
    print(f"\nПробный запрос: «{q}»")
    for h in hits:
        print(f"  {h.score:.3f}  [{h.payload['project']}] "
              f"{h.payload['date'][:10]}  {h.payload['subject'][:55]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
