# -*- coding: utf-8 -*-
"""
Извлечение фактов из писем.

Один проход модели по каждому письму. На выходе таблица строк вида
«тип / предмет / значение / статус» с привязкой к письму и дате.

Зачем: поиск по смыслу отдаёт пять писем из тысяч, и на вопрос
«сколько фотографий в итоге» правильного ответа в них может не быть.
Таблица фактов позволяет сгруппировать по предмету, отсортировать
по дате и взять последнее — это и есть текущее состояние.

Запуск:
    python extractor.py <letters.jsonl> <facts.jsonl> [--limit N] [--ctx 8192] [--model qwen|gigachat] [--svobodno]

Повторный запуск не переделывает уже разобранные письма: читает,
что есть в facts.jsonl, и продолжает с места остановки. Для прогона
на год это обязательное свойство — он идёт часами и может прерваться.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm
from answer import LLAMA_DIR, TORCH_LIB, LOG, resolve_llm

TYPES = ["задача", "решение", "деньги", "срок", "риск"]
STATUSES = ["обсуждается", "принято", "отклонено", "отложено", "выполнено"]

# Ключи латиницей: с ними ограничение по схеме работает надёжнее.
# Значения по-русски — это содержание, его читает человек.
SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": TYPES},
                    "subject": {"type": "string", "maxLength": 60},
                    "value": {"type": "string", "maxLength": 200},
                    "status": {"type": "string", "enum": STATUSES},
                    "who": {"type": "string", "maxLength": 40},
                    "deadline": {"type": "string", "maxLength": 12},
                },
                "required": ["type", "subject", "value", "status"],
            },
        }
    },
    "required": ["facts"],
}

SYSTEM = f"""Ты выписываешь факты из деловой переписки веб-студии. Только то, что прямо написано в письме.

ТИПЫ
задача  — что конкретно нужно сделать
решение — что решили по объёму работ, функциям, подходу
деньги  — суммы, оценки, сметы, доплаты
срок    — даты запуска, этапов, сдачи
риск    — ограничение, проблема, угроза сроку или бюджету

СТАТУСЫ
обсуждается — предложено, согласия пока нет
принято     — согласовано
отклонено   — отказались
отложено    — перенесено на потом
выполнено   — сделано

ПРЕДМЕТ (subject) — САМОЕ ВАЖНОЕ ПОЛЕ
Это название объекта работы: «интернет-магазин», «онлайн-запись», «фото для каталога», «техническое задание», «дизайн-концепция», «каталог и фильтры», «интеграция с Medex».
Существительное, 2-4 слова. Одна и та же тема в разных письмах должна получать ОДНО И ТО ЖЕ название — по нему потом собирается история.
Не годятся: «сайт для arbor», «проект», «сроки», «решения по сайту», «тексты и материалы» — это не объекты, а рубрики.

ОСТАЛЬНЫЕ ПОЛЯ
value    — суть: сумма с валютой, дата, короткая формулировка. Не повторяй в нём предмет.
who      — кто должен это сделать, если названо. Автор письма сам по себе не ответственный. Не ясно — пустая строка.
deadline — дата ДД.ММ.ГГГГ или пустая строка.

СКОЛЬКО ФАКТОВ
Обычно 1-3 на письмо. Пять — потолок, а не норма. Пустой список — нормальный ответ.
Лучше три точных факта, чем пять с добавкой.

НЕ ВЫПИСЫВАЙ
Общие намерения и цели: «сделать сайт», «показать, что умеем», «работать вместе».
Вежливость, приветствия, благодарности, подписи.
Пересказ вопроса собеседника без ответа на него.
Один факт дважды разными словами.

ПРИМЕР
Письмо: «Марина, посчитал магазин — 7150 EUR, это Фаза 2, старт в сентябре.
Фотографии пришлите к среде. Спасибо за быстрый ответ!»

Правильно, три факта:
  деньги | интернет-магазин  | 7150 EUR                  | обсуждается
  срок   | интернет-магазин  | Фаза 2, старт в сентябре  | обсуждается
  задача | фото для каталога | прислать к среде          | обсуждается

Неправильно:
  задача | сотрудничество    | работать вместе   — общее намерение
  задача | письмо            | поблагодарить     — вежливость
  задача | магазин           | сделать магазин   — значение повторяет предмет
  деньги | стоимость         | 7150 EUR          — предмет-рубрика вместо объекта

ПРАВИЛА
Ничего не додумывай, не бери из общих знаний. Числа и даты — как в письме."""


def load_subjects(path):
    """
    Справочник по разделам: [ОБЩЕЕ], [ARB], [MDC].
    Возвращает {раздел: [темы]} или None.
    """
    if not path or not Path(path).is_file():
        return None
    sections, cur = {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip().upper()
            sections.setdefault(cur, [])
        elif cur:
            sections[cur].append(s)
    return sections or None


def subjects_for(sections, project):
    """
    Темы для конкретного письма: общие плюс раздел его проекта.
    Так у столярной мастерской не заводятся «карточки врачей».
    """
    if not sections:
        return None
    out = list(sections.get("ОБЩЕЕ", []))
    out += sections.get((project or "").upper(), [])
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq or None


def make_schema(subjects=None):
    """
    Схема ответа. Если справочник задан, предмет становится выбором
    из списка — как поле-справочник в CRM. Свободный ввод запрещён
    на уровне движка, а не просьбой в указаниях.
    """
    import copy
    s = copy.deepcopy(SCHEMA)
    if subjects:
        s["properties"]["facts"]["items"]["properties"]["subject"] = {
            "type": "string", "enum": subjects}
    return s


def get_flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def build_prompt(letter):
    return (f"Письмо написано {letter.get('date','')[:10]}, "
            f"проект {letter.get('project','')}, "
            f"от {letter.get('from_name','')}.\n"
            f"Тема: {letter.get('subject','')}\n\n"
            f"{letter.get('body','')}")


def parse(raw):
    """Разбирает ответ модели. Схема гарантирует форму, но не наличие ответа."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt
    a, b = txt.find("{"), txt.rfind("}")
    if a < 0 or b < a:
        return None
    try:
        return json.loads(txt[a:b + 1]).get("facts", [])
    except json.JSONDecodeError:
        return None


def clean(fact, letter):
    """Приводит факт к единому виду и привязывает к письму."""
    t = (fact.get("type") or "").strip().lower()
    s = (fact.get("status") or "").strip().lower()
    if t not in TYPES or s not in STATUSES:
        return None
    subject = " ".join((fact.get("subject") or "").split())[:60]
    value = " ".join((fact.get("value") or "").split())[:200]
    if not subject or not value:
        return None
    return {
        "type": t,
        "subject": subject,
        "subject_key": subject.lower().replace("ё", "е"),
        "value": value,
        "status": s,
        "who": " ".join((fact.get("who") or "").split())[:40],
        "deadline": " ".join((fact.get("deadline") or "").split())[:12],
        "project": letter.get("project", ""),
        "date": letter.get("date", ""),
        "letter_id": letter.get("id", ""),
        "file": letter.get("file", ""),
        "from_name": letter.get("from_name", ""),
    }


def done_ids(path):
    """Письма, уже разобранные в прошлые запуски."""
    p = Path(path)
    if not p.is_file():
        return set()
    out = set()
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.add(json.loads(line)["letter_id"])
            except Exception:
                continue
    return out


def main():
    args = []
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ("--limit", "--ctx", "--predmety", "--model"):
            skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)

    if len(args) < 2:
        print(__doc__)
        return 1

    src, dst = args[0], args[1]
    limit = int(get_flag("--limit", 0))
    n_ctx = int(get_flag("--ctx", 8192))
    subj_path = get_flag("--predmety", str(Path(__file__).resolve().parent / "predmety.txt"))
    # --svobodno: разведочный проход без справочника. Модель пишет предметы
    # своими словами, а predmety_sbor.py потом сводит синонимы в черновик.
    subjects = None if "--svobodno" in sys.argv else load_subjects(subj_path)
    gguf, use_jinja, llm_key = resolve_llm(get_flag("--model"))

    if not Path(src).is_file():
        print(f"ОШИБКА: не найден {src}")
        return 1
    if not gguf.is_file():
        print(f"ОШИБКА: нет файла модели {gguf}")
        return 1

    letters = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    already = done_ids(dst)
    todo = [x for x in letters if x.get("id") not in already]
    if limit:
        todo = todo[:limit]

    if subjects:
        parts = ", ".join(f"{k}:{len(v)}" for k, v in subjects.items())
        print(f"Справочник: {subj_path}  ({parts})")
    else:
        print("Справочник не используется — предмет пишется свободно. "
              "Это разведочный проход: потом predmety_sbor.py соберёт черновик.")
    print(f"Писем всего: {len(letters)}; уже разобрано: {len(already)}; "
          f"в работе: {len(todo)}; модель: {llm_key}")
    if not todo:
        print("Нечего делать.")
        return 0

    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"Запускаю движок {llm_key} (окно {n_ctx}) ...")
    proc = llm.start_server(gguf, LLAMA_DIR, extra_dll_dir=TORCH_LIB,
                            n_ctx=n_ctx, log_path=str(LOG), jinja=use_jinja)
    print("Готово.\n")

    def make_system(subs):
        if not subs:
            return SYSTEM
        return (SYSTEM + "\n\nСПРАВОЧНИК ПРЕДМЕТОВ\n"
                "Поле subject выбирается ТОЛЬКО из этого списка:\n"
                + "\n".join("  " + s for s in subs)
                + "\nНичего похожего в списке нет — ставь «прочее». "
                  "Свои формулировки не придумывай.\n"
                  "Факты в одном письме могут относиться к РАЗНЫМ предметам: "
                  "выбирай для каждого свой, а не один на всё письмо.")

    def make_fmt(subs):
        return {"type": "json_schema",
                "json_schema": {"name": "facts", "schema": make_schema(subs)}}

    Path(dst).parent.mkdir(parents=True, exist_ok=True)

    total, bad, empty, tight, retried = 0, 0, 0, 0, 0
    t0 = time.time()
    try:
        with open(dst, "a", encoding="utf-8") as out:
            for i, letter in enumerate(todo, 1):
                t1 = time.time()
                subs = subjects_for(subjects, letter.get("project"))
                try:
                    raw, usage = llm.chat(
                        [{"role": "system", "content": make_system(subs)},
                         {"role": "user", "content": build_prompt(letter)}],
                        max_tokens=600, response_format=make_fmt(subs))
                except SystemExit as e:
                    print(f"\nСвязь с движком потеряна: {e}")
                    break

                # Переполнение окна выглядит как мгновенный пустой ответ.
                # Без этой проверки письмо молча теряется.
                pt = usage.get("prompt_tokens", 0)
                if pt and pt + 600 > n_ctx:
                    tight += 1
                    print(f"{i:>4}/{len(todo)}  ОКНО МАЛО: запрос {pt} + ответ 600 "
                          f"> {n_ctx}  {letter.get('file','')}")
                    continue

                items = parse(raw)

                # Пустой массив с пробелами внутри — не решение «фактов нет»,
                # а зацикливание: при нулевой температуре модель раз за разом
                # выбирает пробел. Повтор с ненулевой температурой разрывает цикл.
                if items is not None and not items:
                    try:
                        raw2, usage2 = llm.chat(
                            [{"role": "system", "content": make_system(subs)},
                             {"role": "user", "content": build_prompt(letter)}],
                            max_tokens=600, temperature=0.4,
                            response_format=make_fmt(subs))
                        items2 = parse(raw2)
                        if items2:
                            items, raw, usage = items2, raw2, usage2
                            retried += 1
                    except SystemExit:
                        pass

                if items is None:
                    bad += 1
                    print(f"{i:>4}/{len(todo)}  РАЗБОР НЕ УДАЛСЯ  {letter.get('file','')}")
                    continue
                rows = [r for r in (clean(f, letter) for f in items) if r]
                if not rows:
                    empty += 1
                    # Пустой ответ — самая опасная тишина: счётчики выглядят
                    # прилично, а содержимое письма потеряно. Пишем, что
                    # именно ответила модель, чтобы не гадать.
                    with open(str(dst) + ".pustye.log", "a", encoding="utf-8") as lg:
                        lg.write(f"--- {letter.get('file','')} "
                                 f"(тело {len(letter.get('body',''))} знаков, "
                                 f"запрос {usage.get('prompt_tokens','?')} токенов) ---\n")
                        lg.write(raw.strip()[:600] + "\n\n")
                for r in rows:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                out.flush()
                total += len(rows)
                print(f"{i:>4}/{len(todo)}  фактов {len(rows):>2}  "
                      f"{time.time()-t1:4.1f} с  {letter.get('file','')}")
    finally:
        print("\nОстанавливаю движок ...")
        llm.stop_server(proc)

    dt = time.time() - t0
    done = i if todo else 0

    # Сводка по предметам: ради группировки всё и затевалось,
    # поэтому дробление предметов надо видеть сразу, а не потом.
    from collections import Counter
    subs, types_c, per_letter = Counter(), Counter(), Counter()
    if Path(dst).is_file():
        with open(dst, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                subs[r.get("subject_key", "")] += 1
                types_c[r.get("type", "")] += 1
                per_letter[r.get("letter_id", "")] += 1

    print("=" * 62)
    print(f"  Предметов разных   : {len(subs)} на {len(per_letter)} писем")
    if per_letter:
        vals = sorted(per_letter.values())
        print(f"  Фактов на письмо   : мин {vals[0]}, медиана "
              f"{vals[len(vals)//2]}, макс {vals[-1]}")
    print(f"  По типам           : {dict(types_c.most_common())}")
    print("  Частые предметы    :")
    for name, n in subs.most_common(10):
        print(f"      {n:>3}  {name}")
    odin = sum(1 for n in subs.values() if n == 1)
    print(f"  Предметов-одиночек : {odin} из {len(subs)}  "
          f"(много — значит дробятся, группировать будет нечего)")
    print("-" * 62)
    print(f"  Обработано писем   : {done}")
    print(f"  Извлечено фактов   : {total}")
    print(f"  Пустых писем       : {empty}")
    print(f"  Сбоев разбора      : {bad}")
    if retried:
        print(f"  Спасено повтором   : {retried}  (первый заход дал пустоту)")
    if tight:
        print(f"  Не влезло в окно   : {tight}  (увеличь --ctx и запусти снова)")
    print(f"  Время              : {dt:.0f} с ({dt/max(done,1):.1f} с на письмо)")
    print(f"  Прикидка на 10 000 : {dt/max(done,1)*10000/3600:.1f} ч")
    print(f"  Файл               : {dst}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
