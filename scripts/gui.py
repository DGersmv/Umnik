# -*- coding: utf-8 -*-
"""
Окно диалога с архивом писем.

Модели загружаются один раз и живут, пока окно открыто: в командной
строке каждый вопрос стоил 2-3 минуты на загрузку. При закрытии окна
движок останавливается, видеопамять освобождается полностью.

Буфер обмена работает при любой раскладке. Обычная привязка Tkinter
слушает символ: при русской раскладке Ctrl+V даёт «м», а не «v»,
и обработчик молчит. Здесь привязка идёт по коду клавиши.

Запуск:
    python gui.py
"""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, font as tkfont

sys.path.insert(0, str(Path(__file__).resolve().parent))

import facts
import llm
import verify
from answer import (BASE, GGUF, LLM_JINJA, LLM_KEY, LLAMA_DIR, TORCH_LIB, LOG,
                    MODEL_NAME, COLLECTION, CHARS_PER_TOKEN, SYSTEM,
                    build_prompt, ru_date, guess_project, project_filter,
                    facts_note, pick_subjects, FACTS)

DB_PATH = BASE / "db"
N_CTX = 8192
RESERVE_TOKENS = 2400   # ответ, указания и блок фактов

CTRL = 0x4          # признак зажатого Ctrl в поле state
VK_A, VK_C, VK_V, VK_X = 65, 67, 86, 88
NAV = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
       "Shift_L", "Shift_R", "Control_L", "Control_R", "Tab"}


# ---------------------------------------------------------------- буфер обмена

def _is_text(w):
    return isinstance(w, tk.Text)


def _select_all(w):
    if _is_text(w):
        w.tag_add("sel", "1.0", "end-1c")
        w.mark_set("insert", "1.0")
    else:
        w.select_range(0, "end")
        w.icursor("end")


def _copy(w):
    try:
        data = w.get("sel.first", "sel.last") if _is_text(w) else w.selection_get()
    except tk.TclError:
        data = w.get("1.0", "end-1c") if _is_text(w) else ""
    if data:
        w.clipboard_clear()
        w.clipboard_append(data)


def _paste(w):
    try:
        data = w.clipboard_get()
    except tk.TclError:
        return
    try:
        w.delete("sel.first", "sel.last")
    except tk.TclError:
        pass
    if not _is_text(w):
        data = " ".join(data.split())        # многострочное в одну строку
    w.insert("insert", data)


def _cut(w):
    _copy(w)
    try:
        w.delete("sel.first", "sel.last")
    except tk.TclError:
        pass


def setup_clipboard(w, readonly=False):
    """
    Привязка по коду клавиши, а не по символу — работает при любой раскладке.
    readonly=True: печатать нельзя, выделять и копировать можно.
    """
    def on_key(e):
        if e.state & CTRL:
            if e.keycode == VK_C:
                _copy(w)
                return "break"
            if e.keycode == VK_A:
                _select_all(w)
                return "break"
            if not readonly and e.keycode == VK_V:
                _paste(w)
                return "break"
            if not readonly and e.keycode == VK_X:
                _cut(w)
                return "break"
            return "break" if readonly else None
        if readonly and e.keysym not in NAV:
            return "break"
        return None

    w.bind("<Key>", on_key)

    menu = tk.Menu(w, tearoff=0)
    menu.add_command(label="Копировать", command=lambda: _copy(w))
    if not readonly:
        menu.add_command(label="Вставить", command=lambda: _paste(w))
        menu.add_command(label="Вырезать", command=lambda: _cut(w))
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=lambda: _select_all(w))

    def popup(e):
        w.focus_set()
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    w.bind("<Button-3>", popup)
    return w


class Umnik:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.model = None
        self.client = None
        self.proc = None
        self.hits = []
        self.busy = False

        root.title("Умник — поиск по переписке")
        root.geometry("1000x780")
        root.minsize(780, 580)

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self._init_worker, daemon=True).start()
        root.after(100, self._poll)

    # ---------------------------------------------------------------- окно

    def _build_ui(self):
        tkfont.nametofont("TkDefaultFont").configure(size=10)
        self.mono = tkfont.Font(family="Consolas", size=10)
        self.body = tkfont.Font(family="Segoe UI", size=11)

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(8, 4))
        self.status = ttk.Label(top, text="Запускаюсь ...", foreground="#8a6d00")
        self.status.pack(side="left")

        ask = ttk.Frame(self.root)
        ask.pack(fill="x", padx=10, pady=4)
        ttk.Label(ask, text="Вопрос:").pack(side="left")
        self.entry = tk.Entry(ask, font=self.body, relief="solid", borderwidth=1)
        self.entry.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        self.entry.bind("<Return>", lambda e: self.ask())
        self.entry.bind("<Escape>", lambda e: self.entry.delete(0, "end"))
        setup_clipboard(self.entry)
        self.btn = ttk.Button(ask, text="Спросить", command=self.ask, state="disabled")
        self.btn.pack(side="left")
        self.btn_sum = ttk.Button(ask, text="Сводка", command=self.show_summary,
                                  state="disabled")
        self.btn_sum.pack(side="left", padx=(6, 0))

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Label(opts, text="Проект:").pack(side="left")
        self.project = ttk.Combobox(opts, values=["все", "ARB", "MDC"],
                                    width=6, state="readonly")
        self.project.current(0)
        self.project.pack(side="left", padx=(4, 16))
        ttk.Label(opts, text="Писем модели:").pack(side="left")
        self.topn = ttk.Spinbox(opts, from_=3, to=10, width=4)
        self.topn.set(5)
        self.topn.pack(side="left", padx=4)
        ttk.Label(opts,
                  text="   Ctrl+V вставить · Ctrl+C копировать · "
                       "правая кнопка мыши — меню",
                  foreground="#888").pack(side="left")

        head = ttk.Frame(self.root)
        head.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(head, text="Ответ:").pack(side="left")
        ttk.Button(head, text="Копировать ответ",
                   command=self.copy_answer).pack(side="right")

        wrap = ttk.Frame(self.root)
        wrap.pack(fill="both", expand=True, padx=10, pady=(2, 6))
        self.out = tk.Text(wrap, wrap="word", font=self.body, height=14,
                           relief="solid", borderwidth=1, padx=10, pady=8)
        sb = ttk.Scrollbar(wrap, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        self.out.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        setup_clipboard(self.out, readonly=True)

        ttk.Label(self.root,
                  text="Письма, по которым отвечено "
                       "(двойной щелчок — открыть целиком):"
                  ).pack(anchor="w", padx=10)
        lw = ttk.Frame(self.root)
        lw.pack(fill="both", padx=10, pady=(2, 4))
        self.srcs = tk.Listbox(lw, height=7, font=self.mono,
                               relief="solid", borderwidth=1, activestyle="none")
        sb2 = ttk.Scrollbar(lw, command=self.srcs.yview)
        self.srcs.configure(yscrollcommand=sb2.set)
        self.srcs.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.srcs.bind("<Double-Button-1>", self.show_letter)

        self.checkbar = ttk.Label(self.root, text="", foreground="#666")
        self.checkbar.pack(anchor="w", padx=10, pady=(2, 0))
        self.foot = ttk.Label(self.root, text="", foreground="#888")
        self.foot.pack(anchor="w", padx=10, pady=(0, 8))

    def _say(self, text, color="#666"):
        self.status.configure(text=text, foreground=color)

    def _write(self, text):
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)

    def copy_answer(self):
        text = self.out.get("1.0", "end-1c").strip()
        if not text:
            return
        lines = [text, "", "Источники:"]
        for i, h in enumerate(self.hits, 1):
            p = h.payload
            lines.append(f"[{i}] {ru_date(p.get('date',''))}  "
                         f"{p.get('project','')}  {p.get('file','')}")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self._say("Ответ вместе со списком писем скопирован.", "#1d7a4c")

    def show_summary(self):
        """
        Сводка считается по базе, без языковой модели.

        Вопросы «кто участвует», «сколько писем», «за какой период»
        требуют взгляда на весь архив. Поиск отдаёт модели пять писем —
        правильного ответа в них нет по определению.
        """
        if self.client is None:
            return
        proj = self.project.get()
        code = None if proj == "все" else proj
        try:
            text = facts.summary(self.client, code)
        except Exception as e:
            text = f"ОШИБКА: {type(e).__name__}: {e}"
        self._write(text)
        self.srcs.delete(0, "end")
        self.hits = []
        self.checkbar.configure(
            text="Сводка посчитана по базе напрямую, модель не участвовала.",
            foreground="#1d7a4c")
        self.foot.configure(text="")

    # ---------------------------------------------------------------- загрузка

    def _init_worker(self):
        try:
            self.q.put(("status", "Открываю базу ..."))
            from qdrant_client import QdrantClient
            self.client = QdrantClient(path=str(DB_PATH))
            if not self.client.collection_exists(COLLECTION):
                self.q.put(("fatal", f"В базе нет коллекции '{COLLECTION}'.\n"
                                     f"Сначала запусти indexer.py"))
                return
            total = self.client.count(COLLECTION).count

            self.q.put(("status", "Загружаю эмбеддер на процессор (20-30 с) ..."))
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(MODEL_NAME, device="cpu")

            self.q.put(("status",
                        f"Запускаю движок {LLM_KEY} ({GGUF.name}, до 2 мин) ..."))
            LOG.parent.mkdir(parents=True, exist_ok=True)
            was_alive = llm.is_alive()
            self.proc = llm.start_server(GGUF, LLAMA_DIR, extra_dll_dir=TORCH_LIB,
                                         n_ctx=N_CTX, log_path=str(LOG),
                                         jinja=LLM_JINJA)
            tail = ""
            if was_alive or self.proc is None:
                # Движок запустил не мы, значит и остановить не сможем:
                # он останется в памяти и после закрытия окна.
                tail = ("  ВНИМАНИЕ: движок был запущен раньше и останется "
                        "в памяти после закрытия. Освободить: "
                        "Stop-Process -Name llama-server -Force")
            self.q.put(("ready",
                        f"Готово. Писем в базе: {total}. Движок работает.{tail}"))
        except SystemExit as e:
            self.q.put(("fatal", str(e)))
        except Exception as e:
            self.q.put(("fatal", f"{type(e).__name__}: {e}"))

    # ---------------------------------------------------------------- вопрос

    def ask(self):
        if self.busy or self.model is None:
            return
        question = self.entry.get().strip()
        if not question:
            return
        self.busy = True
        self.btn.configure(state="disabled")
        self._say("Ищу письма и думаю ...", "#8a6d00")
        self._write("")
        self.srcs.delete(0, "end")
        self.foot.configure(text="")
        self.checkbar.configure(text="")
        threading.Thread(target=self._ask_worker, args=(question,),
                         daemon=True).start()

    def _ask_worker(self, question):
        try:
            top = int(self.topn.get())
            proj = self.project.get()
            auto = None
            if proj != "все":
                flt = project_filter(proj)
            else:
                auto = guess_project(question)      # проект по тексту вопроса
                flt = project_filter(auto)

            vec = self.model.encode([question], normalize_embeddings=True)[0]
            hits = self.client.query_points(COLLECTION, query=vec.tolist(),
                                            limit=top, query_filter=flt,
                                            with_payload=True).points
            if not hits:
                self.q.put(("answer",
                            "Поиск ничего не нашёл. Сними фильтр по проекту.",
                            [], "", ""))
                return

            budget = int((N_CTX - RESERVE_TOKENS) * CHARS_PER_TOKEN)
            subs = pick_subjects(question, self.model, FACTS)
            prompt, shown, dropped, finfo = build_prompt(
                question, hits, budget, subjects=subs,
                project=(None if proj == "все" else proj) or auto)
            text, usage = llm.chat([{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": prompt}])

            check = verify.line(text, [h.payload for h in shown])
            note = (f"токенов: запрос {usage.get('prompt_tokens','?')}, "
                    f"ответ {usage.get('completion_tokens','?')} из {N_CTX}"
                    f"   |   {facts_note(finfo)}")
            if auto:
                note += f"   |   проект определён по вопросу: {auto}"
            if dropped:
                note += f"   |   отброшено писем по нехватке окна: {dropped}"
            self.q.put(("answer", text, shown, note, check))
        except Exception as e:
            self.q.put(("answer", f"ОШИБКА: {type(e).__name__}: {e}", [], "", ""))

    # ---------------------------------------------------------------- письмо

    def show_letter(self, _event=None):
        sel = self.srcs.curselection()
        if not sel or sel[0] >= len(self.hits):
            return
        p = self.hits[sel[0]].payload
        w = tk.Toplevel(self.root)
        w.title(p.get("file", "письмо"))
        w.geometry("840x660")

        wrap = ttk.Frame(w)
        t = tk.Text(wrap, wrap="word", font=self.body, padx=12, pady=10,
                    relief="solid", borderwidth=1)

        bar = ttk.Frame(w)
        bar.pack(fill="x", padx=10, pady=6)
        ttk.Label(bar, text="Ctrl+C копирует выделенное").pack(side="left")
        ttk.Button(bar, text="Копировать письмо",
                   command=lambda: self._copy_letter(t)).pack(side="right")

        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        sb = ttk.Scrollbar(wrap, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        head = (f"Дата:     {ru_date(p.get('date',''))}\n"
                f"Проект:   {p.get('project','')}\n"
                f"От:       {p.get('from_name','')} <{p.get('from_email','')}>\n"
                f"Кому:     {', '.join(p.get('to_names') or [])}\n"
                f"Тема:     {p.get('subject','')}\n"
                f"Вложения: {', '.join(p.get('attachments') or []) or '—'}\n"
                + "-" * 70 + "\n\n")
        t.insert("1.0", head + p.get("body", ""))
        setup_clipboard(t, readonly=True)
        w.bind("<Escape>", lambda e: w.destroy())

    def _copy_letter(self, widget):
        self.root.clipboard_clear()
        self.root.clipboard_append(widget.get("1.0", "end-1c"))

    # ---------------------------------------------------------------- обмен

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._say(msg[1], "#8a6d00")
                elif kind == "ready":
                    self._say(msg[1], "#1d7a4c")
                    self.btn.configure(state="normal")
                    self.btn_sum.configure(state="normal")
                    self.entry.focus_set()
                elif kind == "fatal":
                    self._say("Не удалось запуститься", "#a33")
                    self._write(msg[1])
                elif kind == "answer":
                    _, text, shown, note, check = msg
                    self._write(text)
                    self.hits = list(shown)
                    for i, h in enumerate(shown, 1):
                        p = h.payload
                        self.srcs.insert("end",
                                         f"[{i}] {ru_date(p.get('date',''))}  "
                                         f"{p.get('project','')}  {p.get('file','')}")
                    self.foot.configure(text=note)
                    if check.startswith("ВНИМАНИЕ"):
                        self.checkbar.configure(text=check, foreground="#a33")
                    else:
                        self.checkbar.configure(text=check, foreground="#1d7a4c")
                    self._say("Готово. Движок работает.", "#1d7a4c")
                    self.btn.configure(state="normal")
                    self.busy = False
                    self.entry.focus_set()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def on_close(self):
        self._say("Останавливаю движок ...", "#8a6d00")
        self.root.update_idletasks()
        try:
            llm.stop_server(self.proc)
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Umnik(root)
    root.mainloop()
