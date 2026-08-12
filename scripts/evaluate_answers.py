# -*- coding: utf-8 -*-
"""
Оценка ответов модели по контрольным вопросам.

Считает не «ответила или нет», а четыре разных исхода. Разница между
ними принципиальная: отказ при отсутствии данных — правильное поведение,
отказ при наличии данных — потеря, выдумка — брак.

  ВЕРНО     ответ содержит эталон
  ОТКАЗ ОК  данных в найденных письмах не было, модель честно отказалась
  ОТКАЗ ЗРЯ данные были, но модель не решилась ответить
  ВЫДУМКА   данных не было, а модель ответила
  ОШИБКА    данные были, ответ не тот

Запуск:
    python evaluate_answers.py <папка_базы> <voprosy.txt> [--top 5] [--model qwen|gigachat] [--save отчёт.txt]
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm
import verify
from answer import (BASE, LLAMA_DIR, TORCH_LIB, LOG,
                    MODEL_NAME, COLLECTION, CHARS_PER_TOKEN, SYSTEM,
                    build_prompt, guess_project, project_filter, facts_note,
                    pick_subjects, FACTS, resolve_llm)
from evaluate import norm, load_questions

N_CTX = 8192
RESERVE_TOKENS = 2400   # ответ, указания и блок фактов
REFUSAL = "в приложенных письмах ответа нет"


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


ANS_LINE = re.compile(r"^\s*ОТВЕТ:\s*(.*)$", re.M)
NEXT_LABEL = re.compile(r"^\s*(ИСТОЧНИК|ЦИТАТА|РАНЬШЕ БЫЛО)\s*:", re.M)
QUOTE_LINE = re.compile(r"^\s*(?:ЦИТАТА\s*:|\[\d+\]\s*[«\"])", re.M)


def split_answer(text):
    """
    Делит вывод модели на сам ответ и подтверждающую часть.

    Оценивать надо ответ. Иначе верная цитата прикрывает неверный ответ:
    «2900 EUR [5] «допсоглашение на 2 860 EUR»» засчиталось бы как верное,
    хотя названо не то число.
    """
    m = ANS_LINE.search(text)
    if m:
        tail = text[m.start(1):]
        nxt = NEXT_LABEL.search(tail)
        head = tail[:nxt.start()] if nxt else tail
        return head.strip(), text[m.end():].strip()

    m = QUOTE_LINE.search(text)          # старый формат, без меток
    if not m:
        return text.strip(), ""
    return text[:m.start()].strip(), text[m.start():].strip()


def classify(answer, letters, variants):
    """
    Возвращает (метка, пояснение).
    letters — payload писем, которые реально ушли в модель.
    """
    head, quotes = split_answer(answer)
    a = norm(head)
    blob = norm(" ".join(p.get("subject", "") + " " + p.get("body", "")
                         for p in letters))
    available = any(v in blob for v in variants)     # был ли ответ в письмах
    refused = REFUSAL in norm(head) or REFUSAL in norm(answer)
    correct = any(v in a for v in variants)

    if refused:
        return ("ОТКАЗ ОК", "") if not available else ("ОТКАЗ ЗРЯ", "данные были в письмах")
    if correct:
        return "ВЕРНО", ""
    # ответ не тот, но в цитате нужное есть — модель себе же противоречит
    if any(v in norm(quotes) for v in variants):
        return "ОШИБКА", "в ответе одно, в цитате другое"
    if not available:
        return "ВЫДУМКА", "ответа в письмах не было, но модель ответила"
    return "ОШИБКА", "данные были, ответ не тот"


def main():
    args, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--top", "--save", "--model"):
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
    gguf, use_jinja, llm_key = resolve_llm(get_flag("--model"))
    for p in (db_path, qfile):
        if not Path(p).exists():
            print(f"ОШИБКА: не найдено: {p}")
            return 1
    if not gguf.is_file():
        print(f"ОШИБКА: нет файла модели {gguf}")
        return 1

    questions = load_questions(qfile)
    print(f"Вопросов: {len(questions)}; модель: {llm_key} ({gguf.name})")

    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(db_path))
    if not client.collection_exists(COLLECTION):
        print("ОШИБКА: коллекции нет, сначала indexer.py")
        return 1

    from sentence_transformers import SentenceTransformer
    print("Загружаю эмбеддер на процессор ...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")

    print(f"Запускаю движок {llm_key} ...")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    proc = llm.start_server(gguf, LLAMA_DIR, extra_dll_dir=TORCH_LIB,
                            n_ctx=N_CTX, log_path=str(LOG), jinja=use_jinja)
    print("Готово. Пошли вопросы.\n")

    budget = int((N_CTX - RESERVE_TOKENS) * CHARS_PER_TOKEN)
    rows, tally, flagged = [], {}, 0
    t_start = time.time()

    try:
        for i, (q, variants) in enumerate(questions, 1):
            vec = model.encode([q], normalize_embeddings=True)[0]
            proj = guess_project(q)
            hits = client.query_points(
                COLLECTION, query=vec.tolist(), limit=top,
                query_filter=project_filter(proj),
                with_payload=True).points
            subs = pick_subjects(q, model, FACTS)
            prompt, shown, _, finfo = build_prompt(q, hits, budget, subjects=subs, project=proj)
            payloads = [h.payload for h in shown]

            t0 = time.time()
            text, _usage = llm.chat([{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": prompt}])
            dt = time.time() - t0

            label, why = classify(text, payloads, variants)
            tally[label] = tally.get(label, 0) + 1

            problems, _ = verify.check(text, payloads)
            if problems:
                flagged += 1

            mark = {"ВЕРНО": "  ", "ОТКАЗ ОК": "~ ",
                    "ОТКАЗ ЗРЯ": "! ", "ОШИБКА": "! ", "ВЫДУМКА": "!!"}[label]
            rows.append(f"{mark}[{label:>9}] {q}")
            if why:
                rows.append(f"             {why}")
            if problems:
                rows.append(f"             сверка: {'; '.join(problems)}")
            head, _ = split_answer(text)
            first = " ".join((head or text).split())[:110]
            rows.append(f"             -> {first}")
            rows.append(f"             {facts_note(finfo)}")

            print(f"{i:>2}/{len(questions)} [{label:>9}] {dt:4.1f} с  {q[:52]}")
    finally:
        print("\nОстанавливаю движок ...")
        llm.stop_server(proc)

    n = len(questions)
    good = tally.get("ВЕРНО", 0)
    rep = ["=" * 74,
           "  ОТВЕТЫ ПО КОНТРОЛЬНЫМ ВОПРОСАМ",
           "=" * 74, *rows, "-" * 74,
           f"  Модель            : {llm_key} ({gguf.name})",
           f"  Вопросов          : {n}",
           f"  ВЕРНО             : {good}  ({100*good/n:.0f}%)",
           f"  ОТКАЗ ОК          : {tally.get('ОТКАЗ ОК',0)}   (поиск не дал данных, модель честна)",
           f"  ОТКАЗ ЗРЯ         : {tally.get('ОТКАЗ ЗРЯ',0)}   (данные были — потеря на модели)",
           f"  ОШИБКА            : {tally.get('ОШИБКА',0)}   (данные были, ответ не тот)",
           f"  ВЫДУМКА           : {tally.get('ВЫДУМКА',0)}   (данных не было, а ответ дан)",
           f"  Помечено сверкой  : {flagged}",
           f"  Всего времени     : {time.time()-t_start:.0f} с",
           "=" * 74]
    text = "\n".join(rep)
    print("\n" + text)

    save = get_flag("--save")
    if save:
        Path(save).write_text(text + "\n", encoding="utf-8")
        print(f"\nОтчёт записан: {save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
