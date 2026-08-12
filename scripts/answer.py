# -*- coding: utf-8 -*-
"""
Ответ на вопрос по архиву писем.

Порядок: найти письма -> собрать из них запрос -> получить ответ.

Эмбеддер работает на процессоре. Замеры показали: он занимает 2358 МБ
видеопамяти, языковая модель — несколько гигабайт плюс окно диалога.
Вместе в 8 ГБ не помещаются. Кодирование одной короткой фразы вопроса
на процессоре занимает доли секунды, поэтому видеопамять целиком
отдаётся языковой модели (по умолчанию GigaChat 3.1 Lightning;
запасной вариант — Qwen3-8B через --model qwen).

Запуск:
    python answer.py <папка_базы> "вопрос"
    python answer.py <папка_базы>              — диалоговый режим
    python answer.py <папка_базы> --file voprosy.txt

Ключи:
    --top N        сколько писем отдавать модели (по умолчанию 5)
    --project ARB  ограничить проектом
    --ctx N        размер окна диалога (по умолчанию 8192)
    --model NAME   qwen (по умолчанию) или gigachat
    --gpu          держать эмбеддер на видеокарте (не рекомендуется)
    --show-prompt  показать, что именно ушло в модель
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import llm
import rollup

MODEL_NAME = "deepvk/USER-bge-m3"
COLLECTION = "letters"

BASE = Path(__file__).resolve().parent.parent      # ...\Umnik
GGUF_DIR = BASE / "models" / "gguf"
# Ключ → (файл GGUF, нужен ли --jinja у llama-server).
# Qwen остаётся основной; GigaChat — для сравнения рядом.
LLM_MODELS = {
    "qwen": ("Qwen3-8B-Q4_K_M.gguf", False),
    "gigachat": ("GigaChat3.1-10B-A1.8B-q4_K_M.gguf", True),
}
DEFAULT_LLM = "gigachat"
LLAMA_DIR = BASE / "llama"
LOG = BASE / "logs" / "llama-server.log"
FACTS = BASE / "data" / "parsed" / "facts.jsonl"


def resolve_llm(name=None):
    """
    Возвращает (путь_к_gguf, jinja, ключ).
    Имя: аргумент, иначе UMNIK_MODEL, иначе qwen.
    """
    key = (name or os.environ.get("UMNIK_MODEL") or DEFAULT_LLM).strip().lower()
    if key not in LLM_MODELS:
        known = ", ".join(sorted(LLM_MODELS))
        raise SystemExit(f"ОШИБКА: неизвестная модель {key!r}. Доступны: {known}")
    filename, jinja = LLM_MODELS[key]
    path = GGUF_DIR / filename
    return path, jinja, key


GGUF, LLM_JINJA, LLM_KEY = resolve_llm()


def _find_torch_lib():
    """
    Папка с библиотеками NVIDIA внутри torch. Без неё ggml-cuda.dll
    молча не грузится и видеокарта не находится.
    Версия WinPython в путь не вписана: обновят сборку — всё продолжит работать.
    """
    for p in (BASE / "python").glob("*/python/Lib/site-packages/torch/lib"):
        if p.is_dir():
            return p
    return None


TORCH_LIB = _find_torch_lib()

# Слова, однозначно указывающие на проект. Только собственные имена
# и то, что не встречается у соседа: «сроки» или «бюджет» есть у обоих.
PROJECT_HINTS = {
    "ARB": ("arbor", "арбор", "столярн", "мастерск", "мебел", "марупе",
            "кухн", "шкаф", "лестниц"),
    "MDC": ("vitalis", "виталис", "медцентр", "медицинск", "клиник",
            "medex", "медекс", "пациент", "врач"),
}


def known_codes(facts_path=None):
    """Коды проектов: из справочника подсказок плюс из таблицы фактов."""
    codes = set(PROJECT_HINTS)
    p = Path(facts_path) if facts_path else FACTS
    if p.is_file():
        import json as _json
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                c = (_json.loads(line).get("project") or "").upper()
            except Exception:
                continue
            if 2 <= len(c) <= 6:
                codes.add(c)
    return codes


def guess_project(question, codes=None):
    """
    Код проекта по тексту вопроса или None, если непонятно.

    Код проекта, названный прямо («для MDC»), — сильнейший признак:
    он весит больше любого набора слов-примет. Без этого вопрос
    «надо ли арендовать хостинг для MDC» уходил без фильтра,
    и ответ приходил по письмам другого проекта.
    """
    q = question.lower().replace("ё", "е")
    score = {c: 0 for c in PROJECT_HINTS}

    for code, words in PROJECT_HINTS.items():
        score[code] += sum(1 for w in words if w in q)

    for code in (codes if codes is not None else known_codes()):
        c = code.lower()
        if not c:
            continue
        # код ищем как отдельное слово, чтобы «arb» не всплывал внутри чужих слов
        if re.search(r"(?<![a-zа-я])" + re.escape(c) + r"(?![a-zа-я])", q):
            score[code] = score.get(code, 0) + 5

    if not score:
        return None
    best = max(score, key=score.get)
    if score[best] == 0:
        return None
    if any(score[c] > 0 for c in score if c != best):
        return None          # упомянуты оба — фильтровать нельзя
    return best


def project_filter(code):
    if not code:
        return None
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    return Filter(must=[FieldCondition(key="project",
                                       match=MatchValue(value=code))])

# Оценка длины: для русского примерно 2,5 символа на токен.
# Занижено намеренно — лучше отдать письмо меньше, чем переполнить окно.
CHARS_PER_TOKEN = 2.5

SYSTEM = """Отвечай по приложенным письмам. Других источников у тебя нет.

Ответ всегда состоит из строк с метками. Никакого текста вне этих строк.

ОТВЕТ: одно-два предложения прямо на вопрос
ИСТОЧНИК: [номер письма]
ЦИТАТА: «дословный фрагмент из этого письма»

Пример.
Вопрос: Когда согласовали смету и на какую сумму?
ОТВЕТ: Смету согласовали 5 марта 2026 года на 12 400 EUR без НДС.
ИСТОЧНИК: [2]
ЦИТАТА: «Смету на 12 400 EUR без НДС утверждаем, дата 05.03.2026»

Если значение со временем менялось, добавь ещё одну строку:
РАНЬШЕ БЫЛО: прежнее значение, письмо [1] от 01.03.2026

Если темы вопроса в письмах нет совсем, весь ответ — одна строка:
ОТВЕТ: В приложенных письмах ответа нет.

Правила:
- Перед письмами может идти блок ФАКТЫ ПО ТЕМАМ. Он собран по всему архиву, а письма — только выборка. Если факт из этого блока и письмо расходятся, верь блоку: там последняя строка темы и есть действующее значение.
- В строке ОТВЕТ пиши сам ответ словами. Номера писем ставь только в строке ИСТОЧНИК.
- Числа, даты и суммы переноси из письма без изменений. Число в строке ОТВЕТ должно совпадать с числом в строке ЦИТАТА.
- Дата в шапке письма — это когда письмо написано. Она не является ответом на вопрос «когда что-то произошло, решили, утвердили». Такую дату ищи в тексте письма. Если в тексте её нет, ответ по этому письму дать нельзя.
- Отвечай, даже если в письме сказано другими словами, чем в вопросе. Пересказывать можно.
- Не добавляй ничего сверх спрошенного. Не повторяй эти правила в ответе.
- В шапке каждого письма указан код проекта. Если вопрос про конкретный проект, а письма относятся к другому, отвечать по ним нельзя: пиши «В приложенных письмах ответа нет». Похожий вопрос, решённый в другом проекте, ответом не является."""


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


_SUBJ_CACHE = {"файл": None, "темы": None, "векторы": None}


def all_subjects(facts_path):
    """Список тем, встречающихся в таблице фактов."""
    import json as _json
    p = Path(facts_path)
    if not p.is_file():
        return []
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = _json.loads(line)
        except Exception:
            continue
        k = r.get("subject_key")
        if k:
            out.setdefault(k, r.get("subject", k))
    return sorted(out)


def pick_subjects(question, model, facts_path, top_k=3):
    """
    Темы, подходящие к вопросу, — по близости названий темы и вопроса.

    Раньше темы брались от найденных писем: пять писем давали до двадцати
    тем, блок обрезался по объёму, и самые многочисленные темы вытесняли
    нужную. Теперь «сколько фотографий» напрямую притягивает
    «фото для каталога». Тем три десятка, счёт мгновенный.
    """
    subs = all_subjects(facts_path)
    if not subs or model is None:
        return []
    if _SUBJ_CACHE["файл"] != str(facts_path) or _SUBJ_CACHE["темы"] != subs:
        _SUBJ_CACHE["файл"] = str(facts_path)
        _SUBJ_CACHE["темы"] = subs
        _SUBJ_CACHE["векторы"] = model.encode(subs, normalize_embeddings=True)
    qv = model.encode([question], normalize_embeddings=True)[0]
    scores = _SUBJ_CACHE["векторы"] @ qv
    order = sorted(range(len(subs)), key=lambda i: -float(scores[i]))
    return [subs[i] for i in order[:top_k]]


def ru_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except Exception:
        return iso[:10]


def build_prompt(question, letters, budget_chars, subjects=None, project=None):
    """
    Собирает текст для модели.

    Два разных порядка, и путать их нельзя:
    отбор — по оценке поиска (при нехватке окна выбывают наименее
    подходящие), показ модели — по возрастанию даты (чтобы работало
    правило «позднее отменяет раннее»).

    Возвращает (текст, список_вошедших, сколько_отброшено).
    """
    chosen, spent = [], 0
    for h in letters:                       # уже отсортированы по оценке
        size = len(h.payload.get("body", "")) + 120   # 120 — на строку-шапку
        if spent + size > budget_chars and chosen:
            continue
        chosen.append(h)
        spent += size

    ordered = sorted(chosen, key=lambda h: h.payload.get("date", ""))
    parts = []
    for n, h in enumerate(ordered, 1):
        p = h.payload
        head = (f"[{n}] письмо написано {ru_date(p.get('date', ''))} | "
                f"{p.get('project', '')} | "
                f"от: {p.get('from_name', '')} | тема: {p.get('subject', '')}")
        parts.append(head + "\n" + p.get("body", "").strip())

    sep = "\n\n" + ("-" * 60) + "\n\n"
    body = ("ПИСЬМА ИЗ АРХИВА (по возрастанию даты):\n\n" + sep.join(parts))

    # Цепочки фактов по темам этих писем. Собраны по всему архиву,
    # поэтому последнее звено видно, даже если его письмо не нашлось поиском.
    # Диагностика возвращается наружу: молчаливый сбой здесь выглядел бы
    # как «модель не справилась», хотя блок просто не подмешался.
    info = {"файл": str(FACTS), "есть": FACTS.is_file(),
            "темы": [], "строк": 0, "знаков": 0, "ошибка": ""}
    facts_head = ""
    try:
        # проект берём определённый по вопросу; иначе — по найденным письмам
        projs = {project} if project else {h.payload.get("project", "") for h in ordered}
        if subjects:
            subs = list(subjects)          # порядок важен: первым идёт ближайшее к вопросу
        else:   # запасной путь: темы найденных писем
            subs = sorted(rollup.subjects_of_files(
                FACTS, [h.payload.get("file", "") for h in ordered]))
        info["темы"] = list(subs)
        facts_head = rollup.facts_block(FACTS, subs, projs)
        info["знаков"] = len(facts_head)
        info["строк"] = facts_head.count("\n   ") if facts_head else 0
    except Exception as e:
        info["ошибка"] = f"{type(e).__name__}: {e}"

    if facts_head:
        body = facts_head + "\n\n" + ("=" * 60) + "\n\n" + body

    text = body + f"\n\n{'=' * 60}\n\nВОПРОС: {question}"
    return text, ordered, len(letters) - len(ordered), info


def facts_note(info):
    """Одна строка о том, подмешался ли блок фактов."""
    if info.get("ошибка"):
        return "ФАКТЫ: сбой — " + info["ошибка"]
    if not info.get("есть"):
        return f"ФАКТЫ: файла нет ({info.get('файл','')}) — запусти extractor.py"
    if not info.get("темы"):
        return "ФАКТЫ: у найденных писем нет извлечённых фактов"
    if not info.get("знаков"):
        return f"ФАКТЫ: темы найдены ({', '.join(info['темы'])}), но блок пуст"
    return (f"ФАКТЫ: подмешано {info['строк']} строк по темам: "
            f"{', '.join(info['темы'])}")


def main():
    args, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--top", "--project", "--ctx", "--file", "--model"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if not args:
        print(__doc__)
        return 1

    db_path = args[0]
    top = int(get_flag("--top", 5))
    n_ctx = int(get_flag("--ctx", 8192))
    project = get_flag("--project")
    show_prompt = "--show-prompt" in sys.argv
    device = None if "--gpu" in sys.argv else "cpu"
    gguf, use_jinja, llm_key = resolve_llm(get_flag("--model"))

    if not Path(db_path).is_dir():
        print(f"ОШИБКА: папка базы не найдена: {db_path}")
        return 1
    if not gguf.is_file():
        print(f"ОШИБКА: нет файла модели {gguf}")
        return 1

    # окно делим: под письма отдаём то, что останется после ответа и указаний
    reserve_tokens = 2400          # ответ, указания и блок фактов
    budget_chars = int((n_ctx - reserve_tokens) * CHARS_PER_TOKEN)

    from qdrant_client import QdrantClient
    client = QdrantClient(path=str(db_path))
    if not client.collection_exists(COLLECTION):
        print("ОШИБКА: коллекции нет, сначала запусти indexer.py")
        return 1

    flt = None
    if project:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        flt = Filter(must=[FieldCondition(key="project",
                                          match=MatchValue(value=project.upper()))])

    from sentence_transformers import SentenceTransformer
    print(f"Загружаю эмбеддер на {device or 'видеокарту'} ...")
    model = SentenceTransformer(MODEL_NAME, device=device)

    print(f"Запускаю движок {llm_key} ({gguf.name}, первый раз — до минуты) ...")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    proc = llm.start_server(gguf, LLAMA_DIR, extra_dll_dir=TORCH_LIB,
                            n_ctx=n_ctx, log_path=str(LOG), jinja=use_jinja)
    print("Движок готов.\n" if proc else "Движок уже был запущен.\n")

    def ask(question):
        f = flt
        auto = None
        if f is None:
            auto = guess_project(question)
            f = project_filter(auto)
        vec = model.encode([question], normalize_embeddings=True)[0]
        hits = client.query_points(COLLECTION, query=vec.tolist(),
                                   limit=top, query_filter=f,
                                   with_payload=True).points
        if not hits:
            print("Поиск ничего не нашёл — проверь фильтры.")
            return

        subs = pick_subjects(question, model, FACTS)
        prompt, shown, dropped, finfo = build_prompt(
            question, hits, budget_chars, subjects=subs, project=(project or auto))
        if show_prompt:
            print("─" * 72)
            print(prompt)
            print("─" * 72)

        text, usage = llm.chat([{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}])

        print("═" * 72)
        print(f"ВОПРОС: {question}")
        print("═" * 72)
        print(text)
        print("─" * 72)
        for i, h in enumerate(shown, 1):
            p = h.payload
            print(f"  [{i}] {ru_date(p.get('date',''))}  {p.get('project','')}  "
                  f"{p.get('file','')}")
        print("  " + facts_note(finfo))
        note = f", отброшено по нехватке окна: {dropped}" if dropped else ""
        auto_note = f", проект определён по вопросу: {auto}" if auto else ""
        print(f"  писем отдано модели: {len(shown)}{note}{auto_note}")
        if usage:
            print(f"  токенов: запрос {usage.get('prompt_tokens','?')}, "
                  f"ответ {usage.get('completion_tokens','?')} из {n_ctx}")
        print()

    try:
        qfile = get_flag("--file")
        if qfile:
            for line in Path(qfile).read_text(encoding="utf-8").splitlines():
                q = line.split("||")[0].strip()
                if q and not q.startswith("#"):
                    ask(q)
        elif len(args) > 1:
            ask(" ".join(args[1:]))
        else:
            print("Вводи вопросы. Пустая строка — выход.\n")
            while True:
                try:
                    q = input("вопрос> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not q or q.lower() in ("выход", "exit", "quit"):
                    break
                ask(q)
    finally:
        print("Останавливаю движок ...")
        llm.stop_server(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
