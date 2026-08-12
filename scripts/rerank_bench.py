# -*- coding: utf-8 -*-
"""
Замер реранкера на настоящих письмах.

Проба на коротких строках обманчива: там 20 токенов на пару,
а в настоящем письме 600-1800. Вычислений во столько же раз больше.
Здесь берутся реальные письма из базы и меряется, сколько это стоит.

Заодно видно, что реранкер меняет в порядке выдачи.

Запуск (из F:\\Umnik\\):
    py.bat rerank_bench.py "F:\\Umnik\\db"
    py.bat rerank_bench.py "F:\\Umnik\\db" --top 20 --maxlen 2048
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODEL_NAME = "deepvk/USER-bge-m3"
COLLECTION = "letters"

VOPROSY = [
    "Сколько фотографий в итоге у Arbor для каталога?",
    "Что решили про автоматическую подгрузку отзывов из Google?",
    "На сколько дней вперёд Medex отдаёт расписание?",
    "Когда Arbor утвердил техническое задание?",
]


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    args = []
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--top", "--maxlen"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if not args:
        print(__doc__)
        return 1
    db_path = args[0]
    top = int(get_flag("--top", 20))
    maxlen = int(get_flag("--maxlen", 2048))

    if not Path(db_path).is_dir():
        print(f"ОШИБКА: папка базы не найдена: {db_path}")
        return 1

    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(db_path))
    if not client.collection_exists(COLLECTION):
        print("ОШИБКА: коллекции нет")
        return 1

    from sentence_transformers import SentenceTransformer
    print("Загружаю эмбеддер (процессор) ...")
    emb = SentenceTransformer(MODEL_NAME, device="cpu")

    from rerank import Reranker
    print(f"Загружаю реранкер (процессор, окно {maxlen}) ...")
    t0 = time.time()
    rr = Reranker(device="cpu", max_length=maxlen)
    print(f"Загружен за {time.time() - t0:.1f} с\n")

    all_times, all_chars = [], []

    for q in VOPROSY:
        vec = emb.encode([q], normalize_embeddings=True)[0]
        hits = client.query_points(COLLECTION, query=vec.tolist(),
                                   limit=top, with_payload=True).points
        texts = [(h.payload.get("subject", "") + "\n" + h.payload.get("body", ""))
                 for h in hits]
        chars = sum(len(t) for t in texts)

        t0 = time.time()
        order = rr.rank(q, texts, top_n=len(texts))
        dt = time.time() - t0
        all_times.append(dt)
        all_chars.append(chars)

        print("─" * 74)
        print(f"ВОПРОС: {q}")
        print(f"  кандидатов {len(texts)}, знаков всего {chars}, "
              f"пересчёт {dt:.1f} с ({dt/len(texts)*1000:.0f} мс на письмо)")
        print("  было -> стало   оценка   письмо")
        for new_pos, (idx, score) in enumerate(order[:5], 1):
            p = hits[idx].payload
            move = idx + 1
            arrow = "  " if move == new_pos else ("^^" if move > new_pos else "vv")
            print(f"   {move:>2} -> {new_pos:<2} {arrow} {score:8.4f}   "
                  f"{p.get('date','')[:10]}  {p.get('file','')}")
        low = [s for _, s in order]
        print(f"  разброс оценок: {min(low):.4f} .. {max(low):.4f}")

    n = len(all_times)
    avg = sum(all_times) / n
    print("=" * 74)
    print(f"  Вопросов          : {n}")
    print(f"  Среднее на вопрос : {avg:.1f} с при {top} кандидатах")
    print(f"  Знаков в среднем  : {sum(all_chars)//n}")
    print(f"  На 10 кандидатах  : примерно {avg/top*10:.1f} с")
    print(f"  На 5 кандидатах   : примерно {avg/top*5:.1f} с")
    print("=" * 74)
    print("  Сейчас ответ занимает 3-5 с. Прибавляй эти секунды сверху")
    print("  и решай, сколько кандидатов имеет смысл пересчитывать.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
