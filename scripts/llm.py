# -*- coding: utf-8 -*-
"""
Управление движком llama-server и запросы к нему.

Движок работает отдельным процессом. Так сделано намеренно:
завершение процесса гарантированно освобождает видеопамять,
а внутри одного процесса Python она освобождается когда придётся.

Зависимостей нет — только стандартная библиотека.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8080


def _post(url, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def is_alive(port=DEFAULT_PORT):
    """Отвечает ли движок. Нужен, чтобы не поднимать второй экземпляр."""
    try:
        return _get(f"http://127.0.0.1:{port}/health").get("status") == "ok"
    except Exception:
        return False


def loaded_model_id(port=DEFAULT_PORT):
    """Имя модели, которую сейчас держит движок, или None."""
    try:
        data = _get(f"http://127.0.0.1:{port}/v1/models")
        items = data.get("data") or []
        if not items:
            return None
        return items[0].get("id") or items[0].get("model")
    except Exception:
        return None


def _model_matches(model_path, loaded_id):
    """Совпадает ли запущенная модель с запрошенным файлом GGUF."""
    if not loaded_id:
        return False
    want = Path(model_path)
    lid = str(loaded_id).replace("\\", "/")
    return want.name in lid or want.stem in lid or str(want).replace("\\", "/") in lid


def kill_server(port=DEFAULT_PORT, timeout=15):
    """
    Гасит чужой или застрявший llama-server на порту.
    Нужен при смене модели: иначе старый процесс отвечает под другим GGUF.
    """
    if not is_alive(port):
        return
    # Windows: по имени процесса. Порт один, второй экземпляр всё равно нельзя.
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "llama-server.exe"],
            capture_output=True, text=True, timeout=30)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not is_alive(port):
            return
        time.sleep(0.5)


def start_server(model_path, llama_dir, extra_dll_dir=None,
                 port=DEFAULT_PORT, n_ctx=8192, n_gpu_layers=99,
                 log_path=None, wait=180, jinja=False):
    """
    Запускает llama-server и ждёт готовности.
    Возвращает объект процесса или None, если движок уже был запущен
    с той же моделью.

    extra_dll_dir — папка с библиотеками NVIDIA. У нас это torch\\lib:
    без неё ggml-cuda.dll молча не грузится и карта не находится.

    jinja=True — шаблон чата через Jinja (нужен GigaChat и другим моделям
    со сложным chat_template в GGUF).
    """
    if is_alive(port):
        if _model_matches(model_path, loaded_model_id(port)):
            return None
        kill_server(port)

    exe = Path(llama_dir) / "llama-server.exe"
    if not exe.is_file():
        raise SystemExit(f"ОШИБКА: не найден {exe}")
    if not Path(model_path).is_file():
        raise SystemExit(f"ОШИБКА: не найден файл модели {model_path}")

    env = os.environ.copy()
    if extra_dll_dir:
        env["PATH"] = str(extra_dll_dir) + os.pathsep + env.get("PATH", "")

    cmd = [
        str(exe),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(n_ctx),
        "-ngl", str(n_gpu_layers),   # сколько слоёв считать на видеокарте
        "--no-warmup",
    ]
    if jinja:
        cmd.append("--jinja")

    log = open(log_path, "w", encoding="utf-8") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)

    t0 = time.time()
    while time.time() - t0 < wait:
        if proc.poll() is not None:
            hint = f" Смотри {log_path}" if log_path else ""
            raise SystemExit(f"ОШИБКА: движок завершился при запуске.{hint}")
        if is_alive(port):
            return proc
        time.sleep(1)

    proc.terminate()
    raise SystemExit(f"ОШИБКА: движок не ответил за {wait} с")


def stop_server(proc, timeout=15):
    """Останавливает движок. После этого видеопамять свободна полностью."""
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def strip_thinking(text):
    """
    Убирает блок размышлений Qwen3.

    Отключение через параметры запроса работает не во всех сборках,
    поэтому режем результат — это надёжно независимо от версии.
    """
    for open_tag, close_tag in (("<think>", "</think>"),
                                ("<thinking>", "</thinking>")):
        while open_tag in text and close_tag in text:
            a = text.index(open_tag)
            b = text.index(close_tag) + len(close_tag)
            text = text[:a] + text[b:]
        # незакрытый блок — обрезаем всё до конца
        if open_tag in text:
            text = text[:text.index(open_tag)]
    return text.strip()


def chat(messages, port=DEFAULT_PORT, temperature=0.0,
         max_tokens=1200, timeout=600, response_format=None):
    """
    Запрос к движку. Возвращает (текст_ответа, служебные_данные).

    temperature 0 — модель всегда выбирает самое вероятное продолжение.
    Для вопросов по документам нужна повторяемость, а не разнообразие:
    один и тот же вопрос должен давать один и тот же ответ.

    response_format — схема, которой обязан соответствовать ответ.
    Движок ограничивает выбор каждого следующего слова так, что выдать
    что-то за пределами схемы физически невозможно. Это не просьба
    «отвечай в формате», а запрет на всё остальное.
    """
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # просьба не включать режим размышления; часть сборок игнорирует
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if response_format:
        payload["response_format"] = response_format
    try:
        data = _post(f"http://127.0.0.1:{port}/v1/chat/completions",
                     payload, timeout=timeout)
    except urllib.error.URLError as e:
        raise SystemExit(f"ОШИБКА связи с движком на порту {port}: {e}")

    raw = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return strip_thinking(raw), usage
