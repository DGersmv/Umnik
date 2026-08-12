# -*- coding: utf-8 -*-
"""
Механическая сверка ответа с письмами.

Указания моделью можно проигнорировать, а этой проверкой — нет.
Считаются две вещи:
  - каждое число из ответа ищется в текстах писем;
  - каждая цитата в кавычках ищется дословно.

Не найдено — значит взято не из писем. Это не доказательство лжи
(число могло быть посчитано), но повод посмотреть глазами.
"""

import re

NUM = re.compile(r"\d[\d\u00a0 .,]*\d|\d")
QUOTE = re.compile(r"[«\u201c\"]([^»\u201d\"]{12,})[»\u201d\"]")
# номера ссылок [3] и годы-однозначности проверять бессмысленно
SKIP = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}


def _norm(text):
    t = text.lower().replace("ё", "е").replace("\u00a0", " ")
    t = re.sub(r"(?<=\d)[\s.](?=\d{3}\b)", "", t)      # 5 200 -> 5200
    return re.sub(r"\s+", " ", t)


def _numbers(text):
    out = []
    for m in NUM.finditer(text):
        raw = m.group(0)
        # выкидываем номера ссылок вида [3]
        start = m.start()
        if start > 0 and text[start - 1] == "[":
            continue
        n = re.sub(r"[\s\u00a0.]", "", raw)
        if n and n not in SKIP:
            out.append((raw.strip(), n))
    return out


def check(answer, letters):
    """
    letters — список payload-словарей писем, отданных модели.
    Возвращает (список_проблем, сколько_проверено).
    """
    if "в приложенных письмах ответа нет" in _norm(answer):
        return [], 0

    blob = _norm(" ".join(
        (p.get("subject", "") + " " + p.get("body", "")) for p in letters))

    problems, checked = [], 0

    for raw, n in _numbers(answer):
        checked += 1
        if n not in blob.replace(" ", ""):
            problems.append(f"числа «{raw}» нет в письмах")

    for q in QUOTE.findall(answer):
        checked += 1
        if _norm(q) not in blob:
            short = q[:50] + ("…" if len(q) > 50 else "")
            problems.append(f"цитата «{short}» не найдена дословно")

    seen, uniq = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq, checked


def line(answer, letters):
    """Одна строка для показа под ответом."""
    problems, checked = check(answer, letters)
    if checked == 0:
        return ""
    if not problems:
        return f"Сверка: все проверяемые места ({checked}) найдены в письмах."
    return "ВНИМАНИЕ, сверка: " + "; ".join(problems)
