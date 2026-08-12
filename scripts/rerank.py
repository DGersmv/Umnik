# -*- coding: utf-8 -*-
"""
Реранкер: переупорядочивает найденные куски по релевантности вопросу.

Отличие от эмбеддера: эмбеддер кодирует вопрос и текст по отдельности,
реранкер подаёт их в модель вместе одной парой и выдаёт одну оценку.
Точнее, но считать заранее нельзя — только в момент запроса.

Запуск самопроверки (из F:\\Umnik\\):
    py.bat rerank.py

Использование как модуля:
    from rerank import Reranker
    rr = Reranker()
    top = rr.rank(vopros, kuski, top_n=5)      # [(индекс, оценка), ...]
"""

import os
import time
from pathlib import Path

# --- Портативность -------------------------------------------------------
# Корень проекта: файл лежит в F:\Umnik\scripts\, значит parent.parent = F:\Umnik\
ROOT = Path(__file__).resolve().parent.parent
HF_CACHE = ROOT / "models" / "hf"
HF_CACHE.mkdir(parents=True, exist_ok=True)

# Переменные ставятся ДО импорта sentence_transformers,
# иначе библиотека успеет прочитать путь по умолчанию из профиля пользователя.
os.environ.setdefault("HF_HOME", str(HF_CACHE))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from sentence_transformers import CrossEncoder  # noqa: E402

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

# Длина пары "вопрос + кусок" в токенах. Что длиннее — обрезается с конца.
# Если письмо длиннее окна, факт из его конца реранкер не увидит вообще.
MAX_LEN = 1024


class Reranker:
    def __init__(self, model_name=MODEL_NAME, device="cpu", max_length=MAX_LEN):
        self.model = CrossEncoder(
            model_name,
            device=device,
            max_length=max_length,
            cache_folder=str(HF_CACHE),
        )

    def score(self, query, texts):
        """Оценки для каждого куска в исходном порядке. Шкала 0..1."""
        if not texts:
            return []
        pairs = [(query, t) for t in texts]
        # activation_fn по умолчанию приводит логиты сигмоидой к 0..1
        return [float(x) for x in self.model.predict(pairs, show_progress_bar=False)]

    def rank(self, query, texts, top_n=5, threshold=None):
        """
        Возвращает [(индекс_в_исходном_списке, оценка), ...] по убыванию.
        threshold — если задан, куски ниже порога отбрасываются совсем.
        """
        scores = self.score(query, texts)
        pairs = sorted(enumerate(scores), key=lambda p: p[1], reverse=True)
        if threshold is not None:
            pairs = [p for p in pairs if p[1] >= threshold]
        return pairs[:top_n]


# --- Самопроверка --------------------------------------------------------

PROBA_VOPROS = "Сколько времени Medex отдаёт расписание вперёд?"

PROBA_KUSKI = [
    # 0 — прямой ответ
    "Ограничение Medex: горизонт расписания 14 дней, дальше API не отдаёт.",
    # 1 — сосед-обманка: та же тема, другое число, ответа нет
    "Сроки запуска сайта переносим, горизонт планирования у нас 30 дней.",
    # 2 — про Medex, но не про горизонт
    "Интеграция с Medex требует отдельного ключа доступа от клиники.",
    # 3 — постороннее
    "Фотографии для каталога пришлите к среде, нужно 176 кадров.",
]


def main():
    print(f"Папка моделей : {HF_CACHE}")
    print(f"Модель        : {MODEL_NAME}")
    print("Загружаю (первый раз качает ~1,1 ГБ, это может занять минуты)...")

    t0 = time.time()
    rr = Reranker()
    print(f"Загружена за {time.time() - t0:.1f} с\n")

    t0 = time.time()
    scores = rr.score(PROBA_VOPROS, PROBA_KUSKI)
    dt = time.time() - t0

    print(f"Вопрос: {PROBA_VOPROS}\n")
    for i, s in sorted(enumerate(scores), key=lambda p: p[1], reverse=True):
        print(f"  {s:.4f}  [{i}]  {PROBA_KUSKI[i][:64]}")

    print(f"\n4 пары посчитаны за {dt:.2f} с  "
          f"({dt / len(PROBA_KUSKI) * 1000:.0f} мс на пару)")
    print(f"Прикидка на 30 кандидатов: {dt / len(PROBA_KUSKI) * 30:.1f} с")

    ok = scores.index(max(scores)) == 0
    print("\nРЕЗУЛЬТАТ:", "ГОДНО — прямой ответ на первом месте" if ok
          else "ПЛОХО — первым встал не кусок [0], разбираемся")


if __name__ == "__main__":
    main()
