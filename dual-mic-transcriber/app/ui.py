"""Tkinter による3画面構成の UI。

  スタート画面 → (録音) → 処理画面 → 結果画面 → スタート画面

重い処理はすべてワーカースレッドで行い、UI は after() によるポーリングで
状態を反映する。これにより処理中もウィンドウがフリーズしない。
"""
from __future__ import annotations

import logging
import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import config
from .models import ModelManager
from .pipeline import PHASES, Pipeline, Result
from .recorder import DualRecorder, InputDevice, list_input_devices

log = logging.getLogger(__name__)

APP_TITLE = "2話者 分離文字起こし（概念実証）"


def _open_folder(path):
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror(APP_TITLE, f"フォルダを開けませんでした: {e}")


def _level_to_percent(rms: float) -> float:
    db = 20 * math.log10(rms + 1e-9)
    return max(0.0, min(100.0, (db + 60.0) / 60.0 * 100.0))


class App:
    def __init__(self):
        self.cfg = config.load_config()
        self.models = ModelManager(self.cfg)
        self.stop_event = threading.Event()

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("960x640")
        self.root.minsize(760, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_error
        self._setup_style()

        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.start = StartScreen(container, self)
        self.processing = ProcessingScreen(container, self)
        self.result = ResultScreen(container, self)
        for s in (self.start, self.processing, self.result):
            s.frame.grid(row=0, column=0, sticky="nsew")
        self.show_start()

        # 起動直後からモデルを先行ダウンロード（＝環境構築）
        threading.Thread(target=self.models.prefetch_all, args=(self.stop_event,), daemon=True).start()

    def _setup_style(self):
        families = set(tkfont.families(self.root))
        for fam in ("Yu Gothic UI", "Meiryo UI", "Meiryo", "MS UI Gothic"):
            if fam in families:
                for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                    tkfont.nametofont(name).configure(family=fam, size=10)
                break
        self.mono_family = next(
            (f for f in ("BIZ UDGothic", "MS Gothic", "Consolas") if f in families), "TkFixedFont"
        )
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=(tkfont.nametofont("TkDefaultFont").actual("family"), 15, "bold"))
        style.configure("Big.TButton", padding=(24, 10))
        style.configure("Warn.TLabel", foreground="#b35c00")

    def show_start(self):
        self.start.on_show()
        self.start.frame.tkraise()

    def show_processing(self, pipeline: Pipeline):
        self.processing.run(pipeline)
        self.processing.frame.tkraise()

    def show_result(self, res: Result):
        self.result.show(res)
        self.result.frame.tkraise()

    def _on_tk_error(self, exc, val, tb):
        log.error("Tk callback error", exc_info=(exc, val, tb))
        messagebox.showerror(APP_TITLE, f"予期しないエラーが発生しました:\n{val}\n\nログ: {config.logs_dir()}")

    def _on_close(self):
        if self.start.recorder is not None and self.start.recorder.recording:
            if not messagebox.askyesno(APP_TITLE, "録音中です。録音を停止して終了しますか？"):
                return
            try:
                self.start.recorder.stop()
            except Exception:
                log.exception("stop on close failed")
        if self.processing.pipeline is not None:
            self.processing.pipeline.stop()
        self.stop_event.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class StartScreen:
    def __init__(self, parent, app: App):
        self.app = app
        self.devices: list[InputDevice] = []
        self.recorder: DualRecorder | None = None
        self.session_dir = None

        f = self.frame = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 6))
        ttk.Label(
            f,
            text="マイク1を話者Aの近くに、マイク2を話者Bの近くに置いてください。",
        ).grid(row=1, column=0, columnspan=2, pady=(0, 12))

        self.combos: list[ttk.Combobox] = []
        self.meters: list[ttk.Progressbar] = []
        for col, title in enumerate(("マイク1（話者A側）", "マイク2（話者B側）")):
            lf = ttk.LabelFrame(f, text=title, padding=10)
            lf.grid(row=2, column=col, sticky="nsew", padx=6)
            lf.columnconfigure(0, weight=1)
            cb = ttk.Combobox(lf, state="readonly", width=48)
            cb.grid(row=0, column=0, sticky="ew")
            ttk.Label(lf, text="入力レベル").grid(row=1, column=0, sticky="w", pady=(8, 0))
            pb = ttk.Progressbar(lf, maximum=100, mode="determinate")
            pb.grid(row=2, column=0, sticky="ew")
            self.combos.append(cb)
            self.meters.append(pb)

        self.refresh_btn = ttk.Button(f, text="デバイス一覧を更新", command=self.refresh_devices)
        self.refresh_btn.grid(row=3, column=0, columnspan=2, pady=(8, 0))

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=2, pady=24)
        self.rec_btn = ttk.Button(btns, text="● 録音開始", style="Big.TButton", command=self.start_recording)
        self.stop_btn = ttk.Button(btns, text="■ 停止", style="Big.TButton", command=self.stop_recording)
        self.rec_btn.pack()

        self.elapsed_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.elapsed_var, font=(app.mono_family, 14)).grid(row=5, column=0, columnspan=2)

        f.rowconfigure(6, weight=1)
        self.model_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.model_var, foreground="#555").grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(
            f, text=f"録音・結果の保存先: {config.sessions_dir()}", foreground="#555", wraplength=900
        ).grid(row=8, column=0, columnspan=2, sticky="w")

        self._poll_models()

    def on_show(self):
        if not self.devices:
            self.refresh_devices()
        for pb in self.meters:
            pb["value"] = 0
        self.elapsed_var.set("")

    def refresh_devices(self):
        prev = [cb.get() for cb in self.combos]
        try:
            self.devices = list_input_devices()
        except Exception as e:
            log.exception("device enumeration failed")
            messagebox.showerror(APP_TITLE, f"マイク一覧を取得できませんでした: {e}")
            self.devices = []
        labels = [d.label for d in self.devices]
        for i, cb in enumerate(self.combos):
            cb["values"] = labels
            if prev[i] in labels:
                cb.set(prev[i])
            elif labels:
                cb.current(min(i, len(labels) - 1))
            else:
                cb.set("")
        if not labels:
            messagebox.showwarning(APP_TITLE, "入力デバイス（マイク）が見つかりません。")

    def _poll_models(self):
        st = self.app.models.status
        self.model_var.set(
            f"モデル準備状況  Whisper({self.app.cfg['whisper_model']}): {st['whisper']}   /   ローカルAI: {st['llm']}"
        )
        self.frame.after(1000, self._poll_models)

    def _selected(self) -> list[InputDevice] | None:
        idx = [cb.current() for cb in self.combos]
        if any(i < 0 for i in idx):
            messagebox.showwarning(APP_TITLE, "マイクを2つ選択してください。")
            return None
        d1, d2 = self.devices[idx[0]], self.devices[idx[1]]
        if d1.index == d2.index:
            messagebox.showwarning(APP_TITLE, "同じデバイスが選ばれています。異なる2つのマイクを選んでください。")
            return None
        if d1.name.strip()[:31] == d2.name.strip()[:31]:
            if not messagebox.askyesno(
                APP_TITLE,
                "2つのデバイス名が同じです（同じ物理マイクの別ドライバの可能性があります）。続行しますか？",
            ):
                return None
        return [d1, d2]

    def start_recording(self):
        sel = self._selected()
        if sel is None:
            return
        self.session_dir = config.sessions_dir() / datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            self.recorder = DualRecorder(sel[0], sel[1], self.session_dir)
            self.recorder.start()
        except Exception as e:
            log.exception("recording start failed")
            self.recorder = None
            messagebox.showerror(APP_TITLE, f"録音を開始できませんでした:\n{e}")
            return
        log.info("recording: mic1=%s mic2=%s dir=%s", sel[0].label, sel[1].label, self.session_dir)
        for cb in self.combos:
            cb.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        self.rec_btn.pack_forget()
        self.stop_btn.pack()
        self._poll_levels()

    def _poll_levels(self):
        rec = self.recorder
        if rec is None or not rec.recording:
            return
        for pb, lv in zip(self.meters, rec.levels()):
            pb["value"] = _level_to_percent(lv)
        sec = int(rec.elapsed())
        self.elapsed_var.set(f"録音中  {sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}")
        self.frame.after(100, self._poll_levels)

    def stop_recording(self):
        rec = self.recorder
        self.stop_btn.pack_forget()
        self.rec_btn.pack()
        for cb in self.combos:
            cb.configure(state="readonly")
        self.refresh_btn.configure(state="normal")
        if rec is None:
            return
        try:
            result = rec.stop()
        except Exception as e:
            log.exception("recording stop failed")
            messagebox.showerror(APP_TITLE, f"録音の停止・保存に失敗しました:\n{e}")
            self.recorder = None
            return
        self.recorder = None
        pipe = Pipeline(result, self.session_dir, self.app.cfg, self.app.models)
        self.app.show_processing(pipe)


class ProcessingScreen:
    def __init__(self, parent, app: App):
        self.app = app
        self.pipeline: Pipeline | None = None
        f = self.frame = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="処理中", style="Title.TLabel").grid(row=0, column=0, pady=(0, 12))
        plist = ttk.Frame(f)
        plist.grid(row=1, column=0)
        self.phase_vars = []
        for i, name in enumerate(PHASES):
            v = tk.StringVar(value=f"　　{name}")
            ttk.Label(plist, textvariable=v).grid(row=i, column=0, sticky="w", pady=1)
            self.phase_vars.append(v)

        self.msg_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.msg_var, wraplength=760).grid(row=2, column=0, pady=(16, 4))
        self.bar = ttk.Progressbar(f, maximum=100, length=600)
        self.bar.grid(row=3, column=0, pady=4)
        ttk.Label(
            f,
            text="CPU のみで処理するため、録音時間の数倍の時間がかかることがあります。",
            foreground="#555",
        ).grid(row=4, column=0, pady=(4, 0))
        f.rowconfigure(5, weight=1)
        self.cancel_btn = ttk.Button(f, text="処理停止", command=self.cancel)
        self.cancel_btn.grid(row=6, column=0, pady=12)
        self._current = -1
        self._indeterminate = False

    def run(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self._current = -1
        for i, name in enumerate(PHASES):
            self.phase_vars[i].set(f"　　{name}")
        self.msg_var.set("")
        self._set_progress(None)
        self.cancel_btn.configure(state="normal", text="処理停止")
        pipeline.start()
        self._poll()

    def _set_progress(self, p):
        if p is None:
            if not self._indeterminate:
                self.bar.configure(mode="indeterminate")
                self.bar.start(15)
                self._indeterminate = True
        else:
            if self._indeterminate:
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self._indeterminate = False
            self.bar["value"] = p * 100

    def _set_phase(self, i):
        for j in range(len(PHASES)):
            mark = "✔ " if j < i else ("▶ " if j == i else "　　")
            self.phase_vars[j].set(f"{mark}{PHASES[j]}")
        self._current = i
        self.msg_var.set(PHASES[i])
        self._set_progress(None)

    def cancel(self):
        if self.pipeline is not None:
            self.pipeline.stop()
            self.cancel_btn.configure(state="disabled", text="停止中…（区切りの良いところで停止します）")

    def _finish(self):
        self._set_progress(0)
        self.pipeline = None

    def _poll(self):
        pipe = self.pipeline
        if pipe is None:
            return
        try:
            while True:
                ev = pipe.events.get_nowait()
                kind = ev[0]
                if kind == "phase":
                    self._set_phase(ev[1])
                elif kind == "progress":
                    self._set_progress(ev[1])
                    self.msg_var.set(ev[2])
                elif kind == "done":
                    self._finish()
                    self.app.show_result(ev[1])
                    return
                elif kind == "cancelled":
                    self._finish()
                    messagebox.showinfo(APP_TITLE, f"処理を停止しました。\n録音データは次の場所に保存されています:\n{pipe.session_dir}")
                    self.app.show_start()
                    return
                elif kind == "error":
                    self._finish()
                    messagebox.showerror(
                        APP_TITLE,
                        f"処理中にエラーが発生しました:\n{ev[1]}\n\n録音データ: {pipe.session_dir}\nログ: {config.logs_dir()}",
                    )
                    self.app.show_start()
                    return
        except queue.Empty:
            pass
        self.frame.after(100, self._poll)


class ResultScreen:
    TABS = (
        ("final_text", "完成版（AI補正）"),
        ("draft_text", "規則ベース統合"),
        ("mic1_text", "マイク1 話者付き"),
        ("mic2_text", "マイク2 話者付き"),
        ("timeline_text", "話者タイムライン"),
    )

    def __init__(self, parent, app: App):
        self.app = app
        self.session_dir = None
        f = self.frame = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        ttk.Label(f, text="文字起こし結果", style="Title.TLabel").grid(row=0, column=0, pady=(0, 6))
        self.warn_var = tk.StringVar(value="")
        self.warn_label = ttk.Label(f, textvariable=self.warn_var, style="Warn.TLabel", wraplength=900, justify="left")
        self.warn_label.grid(row=1, column=0, sticky="w")

        self.nb = ttk.Notebook(f)
        self.nb.grid(row=2, column=0, sticky="nsew", pady=6)
        self.texts: dict[str, ScrolledText] = {}
        for key, title in self.TABS:
            tab = ttk.Frame(self.nb)
            st = ScrolledText(tab, wrap="word", undo=True, font=(app.mono_family, 11))
            st.pack(fill="both", expand=True)
            st.bind("<Control-a>", lambda e, w=st: (w.tag_add("sel", "1.0", "end-1c"), "break")[1])
            self.nb.add(tab, text=title)
            self.texts[key] = st

        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, sticky="ew")
        ttk.Button(btns, text="表示中のタブを全てコピー", command=self.copy_current).pack(side="left")
        ttk.Button(btns, text="保存フォルダを開く", command=lambda: _open_folder(self.session_dir)).pack(side="left", padx=8)
        self.copied_var = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.copied_var, foreground="#2a7").pack(side="left", padx=8)
        ttk.Button(f, text="スタートに戻る", style="Big.TButton", command=self.app.show_start).grid(row=4, column=0, pady=(10, 0))

    def show(self, res: Result):
        self.session_dir = res.session_dir
        for key, _ in self.TABS:
            st = self.texts[key]
            st.delete("1.0", "end")
            st.insert("1.0", getattr(res, key) or "（なし）")
            st.edit_reset()
        self.warn_var.set("\n".join(f"⚠ {w}" for w in res.warnings))
        self.copied_var.set("")
        self.nb.select(0)

    def copy_current(self):
        idx = self.nb.index("current")
        key = self.TABS[idx][0]
        text = self.texts[key].get("1.0", "end-1c")
        root = self.app.root
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()  # 終了後もクリップボードに残るようにする
        self.copied_var.set(f"「{self.TABS[idx][1]}」をコピーしました")
