# -*- coding: utf-8 -*-
"""
Проверка установки. Запускается первой на новой машине.

Ничего не меняет и не качает: только смотрит, что на месте.
По каждому пункту — ГОДНО или НЕТ с указанием, что делать.

Запуск:
    PROVERKA.bat
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OK, BAD, WARN = "ГОДНО", "НЕТ  ", "ЧТО-ТО"
problems = []


def say(status, name, detail="", fix=""):
    print(f"  [{status}] {name}")
    if detail:
        print(f"          {detail}")
    if status == BAD:
        problems.append((name, fix))
        if fix:
            print(f"          ЧТО ДЕЛАТЬ: {fix}")


def check_path():
    p = str(ROOT)
    bad = []
    if " " in p:
        bad.append("в пути есть пробелы")
    if any(ord(c) > 127 for c in p):
        bad.append("в пути есть кириллица")
    if len(p) > 60:
        bad.append(f"путь длинный ({len(p)} знаков)")
    if bad:
        say(BAD, "Расположение папки", p + " — " + "; ".join(bad),
            "перенеси папку в корень диска: D:\\Umnik или E:\\Umnik")
    else:
        say(OK, "Расположение папки", p)


def check_driver():
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=20)
    except Exception:
        say(BAD, "Драйвер NVIDIA", "команда nvidia-smi не найдена",
            "поставить драйвер с nvidia.com — нужны права администратора")
        return
    if r.returncode != 0:
        say(BAD, "Драйвер NVIDIA", "nvidia-smi вернул ошибку",
            "переустановить драйвер NVIDIA")
        return
    line = next((l for l in r.stdout.splitlines() if "MiB /" in l), "")
    mem = line.split("|")[2].strip() if line.count("|") > 2 else "?"
    ver = next((l.split("Version:")[1].split()[0]
                for l in r.stdout.splitlines() if "CUDA Version" in l), "?")
    say(OK, "Драйвер NVIDIA", f"память {mem}, поддержка CUDA до {ver}")


def check_python():
    v = sys.version_info
    say(OK if v[:2] >= (3, 10) else BAD, "Python",
        f"{v.major}.{v.minor}.{v.micro}", "нужен Python 3.10 и новее")
    try:
        import tkinter  # noqa: F401
        say(OK, "Tkinter (окна)", "есть")
    except Exception:
        say(BAD, "Tkinter (окна)", "не установлен",
            "сборка Python не та, нужна WinPython dot")


def check_torch():
    try:
        import torch
    except Exception as e:
        say(BAD, "PyTorch", str(e)[:70], "папка python\\ повреждена, перекопируй")
        return None
    if not torch.cuda.is_available():
        say(BAD, "PyTorch с видеокартой", f"версия {torch.__version__}, карта не видна",
            "стоит сборка без CUDA либо драйвер не подходит")
        return torch
    free, total = torch.cuda.mem_get_info()
    say(OK, "PyTorch с видеокартой",
        f"{torch.cuda.get_device_name(0)}, свободно "
        f"{free/1024**2:.0f} из {total/1024**2:.0f} МБ")
    if free / 1024 ** 2 < 6000:
        say(WARN, "Свободная видеопамять",
            f"{free/1024**2:.0f} МБ — маловато, закрой браузер перед работой")
    return torch


def check_models():
    hub = ROOT / "models" / "hub"
    emb = list(hub.glob("models--*bge-m3*")) if hub.is_dir() else []
    if emb:
        size = sum(f.stat().st_size for f in emb[0].rglob("*") if f.is_file())
        say(OK, "Эмбеддер BGE-m3", f"{emb[0].name}, {size/1024**3:.2f} ГБ")
    else:
        say(BAD, "Эмбеддер BGE-m3", f"не найден в {hub}",
            "скопируй папку models\\hub целиком")

    gguf_dir = ROOT / "models" / "gguf"
    ggufs = sorted(gguf_dir.glob("*.gguf")) if gguf_dir.is_dir() else []
    if not ggufs:
        say(BAD, "Языковая модель (GGUF)", "не найдена в models\\gguf",
            "нужен хотя бы Qwen3-8B-Q4_K_M.gguf")
    else:
        for g in ggufs:
            sz = g.stat().st_size / 1024 ** 3
            # Qwen ~4.7 ГБ, GigaChat Q4 ~6.5 ГБ — оба должны быть больше 4 ГБ
            ok = sz > 4
            say(OK if ok else BAD, "GGUF", f"{g.name}, {sz:.2f} ГБ",
                "файл недокачан" if not ok else None)


def check_llama():
    d = ROOT / "llama"
    exe = d / "llama-server.exe"
    if not exe.is_file():
        say(BAD, "Движок llama.cpp", "нет llama-server.exe",
            "скопируй папку llama\\ целиком")
        return
    cuda_dll = (d / "ggml-cuda.dll").is_file()
    say(OK if cuda_dll else BAD, "Движок llama.cpp",
        "llama-server.exe и ggml-cuda.dll на месте" if cuda_dll
        else "нет ggml-cuda.dll — сборка без видеокарты",
        "скачать сборку llama-*-bin-win-cuda-12.4-x64.zip")

    libs = list((ROOT / "python").glob("*/python/Lib/site-packages/torch/lib"))
    if libs and (libs[0] / "cudart64_12.dll").is_file():
        say(OK, "Библиотеки CUDA", f"берутся из torch: {libs[0].name}")
    else:
        say(BAD, "Библиотеки CUDA", "cudart64_12.dll не найден",
            "нужен архив cudart-llama-bin-win-cuda-12.4-x64.zip в папку llama\\")

    env = os.environ.copy()
    if libs:
        env["PATH"] = str(libs[0]) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run([str(d / "llama-cli.exe"), "--list-devices"],
                           capture_output=True, text=True, timeout=60, env=env)
        found = "CUDA0" in r.stdout
        say(OK if found else BAD, "Видеокарта в движке",
            [l.strip() for l in r.stdout.splitlines() if "CUDA0" in l][0]
            if found else "устройств CUDA не найдено",
            "проверь драйвер и что сборка llama.cpp под CUDA 12")
    except Exception as e:
        say(BAD, "Видеокарта в движке", str(e)[:70], "движок не запускается")


def check_data():
    try:
        import warnings
        warnings.simplefilter("ignore")
        from qdrant_client import QdrantClient
        c = QdrantClient(path=str(ROOT / "db"))
        if c.collection_exists("letters"):
            n = c.count("letters").count
            say(OK, "База писем", f"{n} писем в {ROOT / 'db'}")
        else:
            say(WARN, "База писем", "пуста — запусти indexer.py после разбора почты")
        c.close()
    except Exception as e:
        say(BAD, "База писем", str(e)[:70], "проверь папку db\\")

    f = ROOT / "data" / "parsed" / "facts.jsonl"
    if f.is_file():
        n = sum(1 for _ in f.open(encoding="utf-8"))
        say(OK, "Таблица фактов", f"{n} фактов")
    else:
        say(WARN, "Таблица фактов", "нет — запусти fakty.bat после разбора почты")


def main():
    print("=" * 70)
    print(f"  ПРОВЕРКА УСТАНОВКИ:  {ROOT}")
    print("=" * 70)
    check_path()
    check_driver()
    check_python()
    check_torch()
    check_models()
    check_llama()
    check_data()
    print("=" * 70)
    if problems:
        print(f"  НЕ ГОТОВО. Помех: {len(problems)}")
        for name, fix in problems:
            print(f"    - {name}" + (f": {fix}" if fix else ""))
        print("\n  Запускать umnik.bat пока рано.")
        return 1
    print("  ВСЁ НА МЕСТЕ. Запускай umnik.bat")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
