"""LocalCoder の画面 (tkinter)。

    上: いまの状態 (「考えています… 12 秒 (最後に受け取ってから 2 秒)」) と、動いているモデル・GPU
    中: やりとり。モデル自身の考え (薄い字・表示を切り替えられる)・道具を使ったこと・結果・速さ
    下: 頼みを書く欄 (改行してよい。Ctrl+Enter で送る)・「止める」・「新しい話」・「作業フォルダ…」

止める: 返事の途中ならモデルへの接続を切り (llama-server はそこで作るのをやめる)、コマンドの実行中なら
そのコマンドを、そこから起きたプログラムごと止める。窓を閉じるとモデル (llama-server) も止める。
「最後に受け取ってから ○ 秒」が増え続け、モデルの状態に変化が無いときは、止めてやり直す目安になる。
"""

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import cli, models
from .agent import ModelError
from .tools import Tools

UI = "Meiryo UI" if os.name == "nt" else "TkDefaultFont"
MONO = "Consolas" if os.name == "nt" else "TkFixedFont"


class SetupDialog:
    """初回 (と --setup) の設定: 置き場所・モデル・.exe にする道具・共有 GPU メモリ。"""

    def __init__(self, root, cfg, drives):
        self.result = None
        self.drives = drives or [(os.path.expanduser("~"), 0.0, "")]
        w = self.win = tk.Toplevel(root)
        w.title("LocalCoder の準備")
        w.transient(root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="モデルを置く場所 (SSD がおすすめ。大きいモデルは 60〜70GB 使います)").grid(
            row=0, column=0, columnspan=3, sticky="w")
        names = [f"{r}   空き {gb:.0f} GB   {kind}" for r, gb, kind in self.drives]
        self.drive = ttk.Combobox(f, values=names, state="readonly", width=40)
        self.drive.current(0)
        self.drive.grid(row=1, column=0, columnspan=3, sticky="we", pady=(4, 2))
        self.home = tk.StringVar(value=cfg.get("home") or os.path.join(self.drives[0][0], "LocalCoder"))
        ttk.Entry(f, textvariable=self.home, width=48).grid(row=2, column=0, columnspan=2, sticky="we")
        ttk.Button(f, text="参照…", command=self._browse).grid(row=2, column=2, padx=(6, 0))
        self.drive.bind("<<ComboboxSelected>>", self._drive_changed)

        ttk.Label(f, text="考える力 (モデル)。大きいほど賢く、遅い").grid(row=3, column=0, columnspan=3,
                                                                    sticky="w", pady=(14, 2))
        self.model = tk.StringVar(value=cfg.get("model_key") or models.CATALOG[0]["key"])
        for i, m in enumerate(models.CATALOG):
            ttk.Radiobutton(f, text=f"{m['label']}  約 {m['gb']} GB", value=m["key"], variable=self.model).grid(
                row=4 + 2 * i, column=0, columnspan=3, sticky="w")
            ttk.Label(f, text="      " + m["note"], foreground="#777").grid(row=5 + 2 * i, column=0, columnspan=3,
                                                                          sticky="w")
        r = 5 + 2 * len(models.CATALOG)
        self.tools = tk.BooleanVar(value=cfg.get("toolchain", True))
        ttk.Checkbutton(f, text="プログラムを .exe にする道具 (Python・約 150MB) も用意する",
                        variable=self.tools).grid(row=r, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.gpu = tk.BooleanVar(value=cfg.get("gpu_memory", "shared") == "shared")
        ttk.Checkbutton(f, text="GPU に入りきらない分を共有 GPU メモリ (PC のメモリ) に置く (速くなるとは限らない)",
                        variable=self.gpu).grid(row=r + 1, column=0, columnspan=3, sticky="w")
        b = ttk.Frame(f)
        b.grid(row=r + 2, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(b, text="やめる", command=self._cancel).pack(side="right")
        ttk.Button(b, text="この設定で始める", command=self._ok).pack(side="right", padx=(0, 8))
        w.protocol("WM_DELETE_WINDOW", self._cancel)
        w.grab_set()

    def _drive_changed(self, _):
        self.home.set(os.path.join(self.drives[self.drive.current()][0], "LocalCoder"))

    def _browse(self):
        d = filedialog.askdirectory(parent=self.win, initialdir=os.path.dirname(self.home.get()) or None)
        if d:
            self.home.set(os.path.normpath(d))

    def _ok(self):
        home = self.home.get().strip()
        if not home:
            return
        self.result = {"home": os.path.normpath(home), "model_key": self.model.get(), "tools": self.tools.get(),
                       "gpu_memory": "shared" if self.gpu.get() else "normal"}
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class App:
    """画面。モデルとのやりとりは別の糸で進め、届いたものは queue 経由で画面の糸が書く。

    boot(app) は (cfg, srv, agent, tools) を返す (試験では差し替える)。"""

    def __init__(self, root, args, boot=None, autostart=True):
        self.root, self.args = root, args
        self.q = queue.Queue()
        self.cfg, self.srv, self.agent, self.tools = {}, None, None, None
        self.busy = self.closing = self.dead_shown = False
        self.t_start = self.t_last = 0.0
        self.chars, self.phase, self.last_kind = 0, "", None
        self.auto = bool(getattr(args, "yes", False))
        self.boot = boot or default_boot
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self._pump)
        root.after(500, self._tick)
        if autostart:
            root.after(100, self.start)

    # ------------------------------------------------------------ 画面を作る
    def _build(self):
        r = self.root
        r.title("LocalCoder")
        r.geometry("980x720")
        r.minsize(640, 480)
        top = ttk.Frame(r, padding=(12, 10, 12, 4))
        top.pack(side="top", fill="x")
        self.state_var = tk.StringVar(value="準備しています…")
        ttk.Label(top, textvariable=self.state_var, font=(UI, 12, "bold")).pack(side="top", anchor="w")
        self.info_var = tk.StringVar()
        ttk.Label(top, textvariable=self.info_var, foreground="#666").pack(side="top", anchor="w")
        self.bar = ttk.Progressbar(r, mode="indeterminate")
        self.bar.pack(side="top", fill="x", padx=12)

        opts = ttk.Frame(r, padding=(12, 0, 12, 8))
        opts.pack(side="bottom", fill="x")
        self.auto_var = tk.BooleanVar(value=self.auto)
        self.auto_var.trace_add("write", lambda *a: setattr(self, "auto", self.auto_var.get()))
        ttk.Checkbutton(opts, text="コマンドを確かめずに実行する", variable=self.auto_var).pack(side="left")
        self.think_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="モデルの考えを表示する", variable=self.think_var,
                        command=self._apply_think).pack(side="left", padx=14)
        self.ws_var = tk.StringVar()
        ttk.Label(opts, textvariable=self.ws_var, foreground="#666").pack(side="right")

        bottom = ttk.Frame(r, padding=(12, 4, 12, 6))
        bottom.pack(side="bottom", fill="x")
        self.input = tk.Text(bottom, height=4, wrap="word", font=(UI, 10), undo=True)
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Control-Return>", self._send_key)
        btns = ttk.Frame(bottom)
        btns.pack(side="left", fill="y", padx=(8, 0))
        self.send_btn = ttk.Button(btns, text="送る (Ctrl+Enter)", command=self.send, state="disabled")
        self.stop_btn = ttk.Button(btns, text="■ 止める", command=self.stop, state="disabled")
        self.new_btn = ttk.Button(btns, text="新しい話", command=self.new_talk, state="disabled")
        self.dir_btn = ttk.Button(btns, text="作業フォルダ…", command=self.choose_dir, state="disabled")
        for b in (self.send_btn, self.stop_btn, self.new_btn, self.dir_btn):
            b.pack(fill="x", pady=2)

        L = self.log = ScrolledText(r, wrap="word", font=(UI, 10), state="disabled", padx=12, pady=8,
                                    relief="flat", background="#fbfbfb")
        L.pack(side="top", fill="both", expand=True, padx=12, pady=(6, 4))
        L.tag_configure("you_h", foreground="#1a4d8f", font=(UI, 10, "bold"), spacing1=12)
        L.tag_configure("you", foreground="#1a4d8f", lmargin1=14, lmargin2=14)
        L.tag_configure("ai_h", foreground="#333333", font=(UI, 10, "bold"), spacing1=12)
        L.tag_configure("text", lmargin1=14, lmargin2=14)
        L.tag_configure("think", foreground="#8a8a8a", font=(UI, 9, "italic"), lmargin1=28, lmargin2=28)
        L.tag_configure("tool", foreground="#0b7a5c", font=(MONO, 9), lmargin1=14, lmargin2=28, spacing1=2)
        L.tag_configure("result", foreground="#777777", font=(MONO, 9), lmargin1=28, lmargin2=28)
        L.tag_configure("info", foreground="#999999", lmargin1=14, lmargin2=14)
        L.tag_configure("err", foreground="#b00020", lmargin1=14, lmargin2=14)

    def _apply_think(self):
        self.log.tag_configure("think", elide=not self.think_var.get())

    def _append(self, text, tag):
        L = self.log
        follow = L.yview()[1] > 0.98
        L.configure(state="normal")
        L.insert("end", text, tag)
        L.configure(state="disabled")
        if follow:
            L.see("end")

    def _newline(self):
        if self.log.get("end-2c", "end-1c") not in ("\n", ""):
            self._append("\n", "text")

    def _buttons(self):
        ready = self.agent is not None and not self.closing
        self.send_btn.configure(state="normal" if ready and not self.busy else "disabled")
        self.new_btn.configure(state="normal" if ready and not self.busy else "disabled")
        self.dir_btn.configure(state="normal" if ready and not self.busy else "disabled")
        self.stop_btn.configure(state="normal" if ready and self.busy else "disabled")
        if self.busy:
            self.bar.start(12)
        else:
            self.bar.stop()

    # ------------------------------------------------------------ 起動
    def start(self):
        self.bar.start(12)
        threading.Thread(target=self._boot_thread, daemon=True).start()

    def _boot_thread(self):
        try:
            cfg, srv, agent, tools = self.boot(self)
        except cli.StartError as e:
            self.q.put(("fatal", str(e)))
            return
        except SystemExit:
            self.q.put(("quit", None))
            return
        except Exception as e:  # noqa: BLE001
            self.q.put(("fatal", f"準備の途中で止まりました: {e!r}"))
            return
        self.q.put(("ready", (cfg, srv, agent, tools)))

    def ask_setup(self, cfg, drives):
        """別の糸から呼ぶ: 画面の糸で設定の窓を出し、答えを待つ。"""
        box, ev = {}, threading.Event()
        self.q.put(("setup", (cfg, drives, box, ev)))
        ev.wait()
        return box.get("result")

    def confirm(self, command):
        """別の糸から呼ぶ: コマンドを実行してよいか尋ねる。"""
        if self.auto:
            return True
        box, ev = {}, threading.Event()
        self.q.put(("confirm", (command, box, ev)))
        while not ev.wait(0.2):
            if self.agent is not None and self.agent.cancelled.is_set():
                return False
        return box.get("ok", False)

    def on_event(self, kind, data):
        """別の糸 (エージェント) から: そのまま queue へ。"""
        self.q.put((kind, data))

    def on_say(self, text, end):
        self.q.put(("say", (text, end)))

    # ------------------------------------------------------------ queue を画面に
    def _pump(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        except tk.TclError:
            return                       # 窓はもう閉じた
        try:
            self.root.after(50, self._pump)
        except tk.TclError:
            pass

    def _handle(self, kind, data):
        if kind in ("text", "think", "tool", "result", "progress", "speed"):
            self.t_last = time.time()
        if kind == "text":
            if not data or (not data.strip() and self.log.get("end-2c", "end-1c") == "\n"):
                return                       # 区切りの改行だけ: もう行の頭なら足さない
            if self.last_kind == "think":
                self._newline()
            self._append(data, "text")
            self.chars += len(data)
            self.phase = "返事を書いています"
        elif kind == "think":
            if self.last_kind != "think":
                self._newline()
                self._append("考え: ", "think")
            self._append(data, "think")
            self.chars += len(data)
            self.phase = "モデルが考えています"
        elif kind == "tool":
            self._newline()
            self._append(f"→ {data[0]} {data[1]}\n", "tool")
            self.phase = f"{data[0]} を実行しています"
        elif kind == "result":
            self._append(f"{data}\n", "result")
            self.phase = "結果を読んで、次を考えています"
        elif kind == "progress":
            self.phase = f"{data[0] or '道具'} を準備しています ({data[1]:,} 字)"
        elif kind == "speed":
            self._newline()
            self._append(f"({data[0]:.1f} トークン/秒・{data[1]:.0f} 秒)\n", "info")
        elif kind == "info":
            self._newline()
            self._append(f"{str(data).strip()}\n", "info")
        elif kind == "say":
            text, end = data
            if text.startswith("\r") or end == "":
                self.state_var.set(text.strip())            # 取得の進み具合: 状態の行に出す
            else:
                self._append(f"{text.strip()}\n", "info")
        elif kind == "error":
            self._newline()
            self._append(f"{data}\n", "err")
        elif kind == "fatal":
            self._append(f"{data}\n", "err")
            self.state_var.set("動かせませんでした")
            self.bar.stop()
            messagebox.showerror("LocalCoder", str(data), parent=self.root)
        elif kind == "setup":
            cfg, drives, box, ev = data
            dlg = SetupDialog(self.root, cfg, drives)
            self.root.wait_window(dlg.win)
            box["result"] = dlg.result
            ev.set()
        elif kind == "confirm":
            command, box, ev = data
            box["ok"] = messagebox.askyesno("コマンドの実行", f"このコマンドを実行してよいですか？\n\n{command}",
                                            parent=self.root)
            ev.set()
        elif kind == "ready":
            self.cfg, self.srv, self.agent, self.tools = data
            if self.closing:
                self.close()
                return
            self.state_var.set("待機中")
            name = os.path.basename(self.cfg.get("model") or self.args.model_file or "")
            gpu = f"{self.cfg.get('kind', '?')} 版" + ("・共有 GPU メモリ" if self.cfg.get("shared_now") else "")
            self.info_var.set(f"{name}  /  {gpu}")
            self.ws_var.set(f"作業フォルダ: {self.tools.root}")
            self._append("準備できました。下の欄に日本語で頼んでください (改行してよい。Ctrl+Enter で送る)。\n", "info")
            self._buttons()
            self.input.focus_set()
        elif kind == "done":
            took = time.time() - self.t_start
            self.busy = False
            self._buttons()
            self.state_var.set(f"待機中 (さっきの頼みは {took:.0f} 秒)")
        elif kind == "quit":
            self.root.destroy()
            return
        self.last_kind = kind if kind in ("text", "think", "tool", "result") else self.last_kind

    def _tick(self):
        if self.busy and not self.closing:
            now = time.time()
            s = f"{self.phase}… {now - self.t_start:.0f} 秒"
            since = now - self.t_last
            if since >= 3:
                s += f"  (最後に受け取ってから {since:.0f} 秒)"
            if self.chars:
                s += f"  受け取った文字 {self.chars:,}"
            self.state_var.set(s)
            proc = getattr(self.srv, "proc", None)
            if proc is not None and proc.poll() is not None and not self.dead_shown:
                self.dead_shown = True
                self._handle("error", "モデル (llama-server) が止まってしまいました。記録: "
                                      f"{getattr(self.srv, 'log_path', '')}  窓を閉じて起動し直してください。")
        try:
            self.root.after(500, self._tick)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 操作
    def _send_key(self, _event):
        self.send()
        return "break"

    def send(self, text=None):
        if self.busy or self.agent is None:
            return
        text = (text if text is not None else self.input.get("1.0", "end")).strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._newline()
        self._append("あなた\n", "you_h")
        self._append(text + "\n", "you")
        self._append("LocalCoder\n", "ai_h")
        self.busy, self.dead_shown = True, False
        self.t_start = self.t_last = time.time()
        self.chars, self.phase, self.last_kind = 0, "頼みを読んでいます", None
        self._buttons()
        threading.Thread(target=self._ask_thread, args=(text,), daemon=True).start()

    def _ask_thread(self, text):
        try:
            self.agent.ask(text)
        except ModelError as e:
            self.q.put(("error", f"モデルとのやりとりに失敗しました: {e}"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"思わぬ失敗: {e!r}"))
        finally:
            self.q.put(("done", None))

    def stop(self):
        if self.busy and self.agent is not None:
            self.agent.cancel()
            self.phase = "止めています"
            self.stop_btn.configure(state="disabled")

    def new_talk(self):
        if self.busy or self.agent is None:
            return
        self.agent.reset()
        self._newline()
        self._append("── 新しい話 ──\n", "info")

    def choose_dir(self):
        if self.busy or self.agent is None:
            return
        d = filedialog.askdirectory(parent=self.root, initialdir=self.tools.root)
        if not d:
            return
        self.tools = Tools(os.path.normpath(d), env=self.tools.env)
        self.agent.tools = self.tools
        self.agent.reset()
        self.ws_var.set(f"作業フォルダ: {self.tools.root}")
        self._newline()
        self._append(f"作業フォルダを {self.tools.root} にしました (話も切り替えました)\n", "info")

    def close(self):
        if self.busy and not self.closing and not messagebox.askyesno(
                "LocalCoder", "作業中です。止めて閉じますか？", parent=self.root):
            return
        self.closing = True
        self.state_var.set("終わっています… (モデルを止めています)")
        if self.agent is not None:
            self.agent.cancel()
        srv = self.srv

        def finish():
            if srv is not None:
                srv.stop()
            self.q.put(("quit", None))
        threading.Thread(target=finish, daemon=True).start()


def default_boot(app):
    """ふつうの起動: 設定 (初回は窓で選ぶ) → llama.cpp・モデル・道具をそろえる → モデルを動かす。"""
    args = app.args
    cli._hook = app.on_say
    cfg = cli.load_config(args)
    if cli.needs_setup(cfg, args):
        if args.model_key and cfg.get("home"):
            cli.apply_setup(cfg, cfg["home"], args.model_key, cfg.get("toolchain", True),
                            cfg.get("gpu_memory", "shared"), args.config)
        else:
            res = app.ask_setup(cfg, sorted(cli.drives(), key=lambda d: -d[1]))
            if res is None:
                raise SystemExit(0)
            cli.apply_setup(cfg, res["home"], res["model_key"], res["tools"], res["gpu_memory"], args.config)
    cli.finish_setup(cfg, args)

    def hold(srv):
        app.srv = srv              # 起動の途中で窓を閉じても、モデルを止められるように
    srv = cli.start_model(cfg, args, on_server=hold)
    agent, tools = cli.make_agent(cfg, args, srv, app.confirm, on_event=app.on_event)
    return cfg, srv, agent, tools


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in argv for a in ("--uninstall", "--task", "--cli")):
        if "--cli" in argv:
            argv.remove("--cli")
        return cli.main(argv)
    selftest = None
    if "--selftest" in argv:
        i = argv.index("--selftest")
        selftest = argv[i + 1]
        del argv[i:i + 2]
    args = cli.parser().parse_args(argv)
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # 高解像度の画面で文字がぼやけないように
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    if selftest:
        from .selftest import run
        return run(root, args, selftest)
    App(root, args)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
