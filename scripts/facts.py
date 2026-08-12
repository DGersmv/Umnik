# -*- coding: utf-8 -*-
"""
Ответы, которые считаются по базе, а не читаются языковой моделью.

Зачем отдельный модуль: вопросы «кто участвует», «сколько писем»,
«за какой период», «какие темы» требуют взгляда на весь архив.
Поиск по смыслу отдаёт модели пять писем из тысяч, и правильного
ответа в них просто нет. Такие вопросы решаются подсчётом по полям.

Языковая модель здесь не участвует вовсе: цифры точные, не пересказанные.
"""

from collections import Counter, defaultdict
from datetime import datetime

COLLECTION = "letters"


def _all_payloads(client, project=None, batch=256):
    """Читает все записи коллекции постранично."""
    flt = None
    if project:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        flt = Filter(must=[FieldCondition(key="project",
                                          match=MatchValue(value=project.upper()))])
    out, offset = [], None
    while True:
        points, offset = client.scroll(COLLECTION, scroll_filter=flt,
                                       limit=batch, offset=offset,
                                       with_payload=True, with_vectors=False)
        out.extend(p.payload for p in points)
        if offset is None or not points:
            break
    return out


def _ru(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except Exception:
        return (iso or "")[:10]


def participants(payloads):
    """Кто сколько писем отправил и в скольких участвовал как получатель."""
    sent, got, mail = Counter(), Counter(), {}
    for p in payloads:
        name = (p.get("from_name") or "").strip()
        if name:
            sent[name] += 1
            if p.get("from_email"):
                mail[name] = p["from_email"]
        for to in p.get("to_names") or []:
            to = (to or "").strip()
            if to:
                got[to] += 1
    people = set(sent) | set(got)
    rows = [(n, mail.get(n, ""), sent[n], got[n]) for n in people]
    return sorted(rows, key=lambda r: -(r[2] + r[3]))


def period(payloads):
    dates = sorted(p.get("date", "") for p in payloads if p.get("date"))
    return (_ru(dates[0]), _ru(dates[-1])) if dates else ("—", "—")


def by_month(payloads):
    c = Counter(p.get("year_month", "") for p in payloads if p.get("year_month"))
    return sorted(c.items())


def by_project(payloads):
    return Counter(p.get("project") or "(без кода)" for p in payloads).most_common()


def attachments(payloads):
    """Все упомянутые вложения и в скольких письмах встречаются."""
    c = Counter()
    for p in payloads:
        for a in p.get("attachments") or []:
            c[a.strip()] += 1
    return c.most_common()


def pairs(payloads):
    """Кто с кем переписывается: пары отправитель -> получатель."""
    c = Counter()
    for p in payloads:
        frm = (p.get("from_name") or "").strip()
        for to in p.get("to_names") or []:
            to = (to or "").strip()
            if frm and to:
                c[(frm, to)] += 1
    return c.most_common()


def summary(client, project=None):
    """Готовый текст сводки. Без модели: все числа посчитаны, не пересказаны."""
    pl = _all_payloads(client, project)
    if not pl:
        return f"В базе нет писем" + (f" по проекту {project}." if project else ".")

    a, b = period(pl)
    head = f"Проект {project.upper()}" if project else "Весь архив"
    lines = [f"{head}: писем {len(pl)}, период с {a} по {b}", ""]

    if not project:
        lines.append("По проектам:")
        for code, n in by_project(pl):
            lines.append(f"   {code}: {n}")
        lines.append("")

    lines.append("Участники (отправлено / получено):")
    for name, email, s, g in participants(pl):
        tail = f"  <{email}>" if email else ""
        lines.append(f"   {name:22} {s:>4} / {g:<4}{tail}")

    top = pairs(pl)[:8]
    if top:
        lines += ["", "Основные направления переписки:"]
        for (frm, to), n in top:
            lines.append(f"   {frm} -> {to}: {n}")

    months = by_month(pl)
    if len(months) > 1:
        lines += ["", "По месяцам:"]
        for m, n in months:
            lines.append(f"   {m}: {n}")

    att = attachments(pl)
    if att:
        lines += ["", f"Вложения, упомянутые в письмах ({len(att)} имён):"]
        for name, n in att[:12]:
            lines.append(f"   {name}  x{n}")
        if len(att) > 12:
            lines.append(f"   ... и ещё {len(att) - 12}")

    return "\n".join(lines)
