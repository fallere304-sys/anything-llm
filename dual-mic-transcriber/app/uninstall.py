"""DualMicTranscriber のアンインストーラ。

本体がPCに残すものを列挙し、ユーザーが選んだものを削除する:

  1. 本体 exe（起動時に install_info.json へ記録された場所 ＋ アンインストーラと同じフォルダ）
  2. %LOCALAPPDATA%\\DualMicTranscriber   … モデル(数GB)・設定・ログ・HFキャッシュ
  3. %TEMP%\\_MEI*                         … 本体が異常終了したときに残る一時展開フォルダ
  4. ドキュメント\\DualMicTranscriber      … 録音・文字起こし結果（ユーザーデータ。既定は削除しない）
  5. このアンインストーラ自身

レジストリ・スタートメニュー・環境変数には何も書き込んでいないため、上記で全てである。
誤削除を防ぐため、削除前に各パスが「このアプリのもの」であることを名前で再確認する。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config

MAIN_EXE = "DualMicTranscriber.exe"
UNINSTALL_EXE = "DualMicTranscriber_Uninstall.exe"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


@dataclass
class Target:
    key: str
    label: str
    path: Path
    is_dir: bool
    default: bool
    note: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_dir() if self.is_dir else self.path.is_file()

    def size(self) -> int:
        if not self.exists:
            return 0
        if not self.is_dir:
            return self.path.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(self.path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def self_exe() -> Path | None:
    return Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None


def _recorded_exe_paths() -> list[Path]:
    try:
        info = json.loads(config.install_info_path().read_text(encoding="utf-8"))
        return [Path(p) for p in info.get("exe_paths", [])]
    except Exception:
        return []


def _is_our_mei_dir(d: Path) -> bool:
    """PyInstaller の一時展開フォルダのうち、本体のもの（Whisper と llama.cpp を含む）だけを対象にする。"""
    return d.is_dir() and (d / "faster_whisper").is_dir() and (d / "llama_cpp").is_dir()


def find_targets(me: Path | None = None) -> list[Target]:
    targets: list[Target] = []

    exes: list[Path] = []
    candidates = _recorded_exe_paths()
    if me is not None:
        candidates.append(me.parent / MAIN_EXE)
    for p in candidates:
        try:
            p = p.resolve()
        except OSError:
            continue
        if p.name.lower() == MAIN_EXE.lower() and p.is_file() and p not in exes:
            exes.append(p)
    for i, p in enumerate(exes):
        targets.append(Target(f"exe{i}", "アプリ本体", p, is_dir=False, default=True))

    targets.append(
        Target(
            "appdata",
            "モデル・設定・ログ",
            config.app_dir(create=False),
            is_dir=True,
            default=True,
            note="再インストール時はモデルを再ダウンロードします",
        )
    )

    mine = getattr(sys, "_MEIPASS", None)
    for d in sorted(Path(tempfile.gettempdir()).glob("_MEI*")):
        if mine and Path(mine).resolve() == d.resolve():
            continue
        if _is_our_mei_dir(d):
            targets.append(Target(f"mei:{d.name}", "一時展開フォルダの残り", d, is_dir=True, default=True))

    targets.append(
        Target(
            "sessions",
            "録音・文字起こし結果",
            config.sessions_dir(create=False),
            is_dir=True,
            default=False,
            note="あなたの録音データです。削除すると元に戻せません",
        )
    )

    if me is not None:
        targets.append(Target("self", "このアンインストーラ", me, is_dir=False, default=True))
    return targets


def _is_safe(t: Target) -> bool:
    """削除直前の安全確認。想定外のパスは絶対に消さない。"""
    p = t.path
    if t.key.startswith("exe"):
        return not t.is_dir and p.name.lower() == MAIN_EXE.lower()
    if t.key == "self":
        return not t.is_dir and p.suffix.lower() == ".exe" and "uninstall" in p.name.lower()
    if t.key in ("appdata", "sessions"):
        return t.is_dir and p.name == config.APP_NAME and len(p.parts) >= 3
    if t.key.startswith("mei:"):
        return (
            p.name.startswith("_MEI")
            and p.parent.resolve() == Path(tempfile.gettempdir()).resolve()
            and _is_our_mei_dir(p)
        )
    return False


def _onerror(func, path, _exc):
    # 読み取り専用属性のファイルは属性を外して再試行する
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        raise


def delete_target(t: Target) -> str | None:
    """削除する。成功なら None、失敗ならエラーメッセージを返す。"""
    if not t.exists:
        return None
    if not _is_safe(t):
        return "安全確認に失敗したため削除しませんでした"
    if t.key == "self":
        return None  # 実行中の自分は消せないので、終了後に schedule_self_delete で消す
    try:
        if t.is_dir:
            if sys.version_info >= (3, 12):
                shutil.rmtree(t.path, onexc=_onerror)
            else:
                shutil.rmtree(t.path, onerror=_onerror)
        else:
            try:
                t.path.unlink()
            except PermissionError:
                os.chmod(t.path, stat.S_IWRITE)
                t.path.unlink()
        return None
    except Exception as e:
        return str(e)


def schedule_self_delete(path: Path):
    """プロセス終了の数秒後に cmd で自分自身の exe を削除する。"""
    if sys.platform != "win32":
        return
    cmd = f'ping 127.0.0.1 -n 4 >nul & del /f /q "{path}"'
    subprocess.Popen(
        ["cmd", "/c", cmd],
        creationflags=_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


def is_app_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {MAIN_EXE}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
            timeout=15,
        ).stdout
        return MAIN_EXE.lower() in out.lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def run_gui():
    import threading
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    title = "DualMicTranscriber のアンインストール"
    me = self_exe()
    targets = find_targets(me)

    root = tk.Tk()
    root.title(title)
    height = min(240 + 72 * len(targets), root.winfo_screenheight() - 80)
    root.geometry(f"820x{height}")
    root.minsize(640, 420)
    families = set(tkfont.families(root))
    for fam in ("Yu Gothic UI", "Meiryo UI", "Meiryo"):
        if fam in families:
            for name in ("TkDefaultFont", "TkTextFont"):
                tkfont.nametofont(name).configure(family=fam, size=10)
            break

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text=title, font=(tkfont.nametofont("TkDefaultFont").actual("family"), 14, "bold")).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(
        frame,
        text="削除する項目を選んでください。このアプリはレジストリやスタートメニューには何も書き込んでいません。",
        wraplength=760,
    ).grid(row=1, column=0, sticky="w", pady=(4, 10))

    list_frame = ttk.Frame(frame)
    list_frame.grid(row=2, column=0, sticky="nsew")
    list_frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)

    vars_: dict[str, tk.BooleanVar] = {}
    size_vars: dict[str, tk.StringVar] = {}
    for i, t in enumerate(targets):
        v = tk.BooleanVar(value=t.default and t.exists)
        vars_[t.key] = v
        cb = ttk.Checkbutton(list_frame, text=t.label, variable=v)
        cb.grid(row=i * 2, column=0, sticky="w", pady=(6, 0))
        if not t.exists:
            cb.state(["disabled"])
        sv = tk.StringVar(value="計算中…" if t.exists else "見つかりません")
        size_vars[t.key] = sv
        ttk.Label(list_frame, textvariable=sv, width=14, anchor="e").grid(row=i * 2, column=1, sticky="e")
        detail = str(t.path) + (f"\n※ {t.note}" if t.note else "")
        ttk.Label(
            list_frame,
            text=detail,
            foreground="#b00020" if t.key == "sessions" else "#555",
            wraplength=640,
            justify="left",
        ).grid(row=i * 2 + 1, column=0, columnspan=2, sticky="w", padx=(24, 0))

    def compute_sizes():
        for t in targets:
            if t.exists:
                s = fmt_size(t.size())
                root.after(0, lambda k=t.key, s=s: size_vars[k].set(s))

    threading.Thread(target=compute_sizes, daemon=True).start()

    status = tk.StringVar(value="")
    ttk.Label(frame, textvariable=status, wraplength=760, justify="left").grid(row=3, column=0, sticky="w", pady=6)
    btns = ttk.Frame(frame)
    btns.grid(row=4, column=0, sticky="e")
    state = {"delete_self": False}

    def on_close():
        if state["delete_self"] and me is not None:
            schedule_self_delete(me)
        root.destroy()

    def do_uninstall():
        chosen = [t for t in targets if vars_[t.key].get() and t.exists]
        if not chosen:
            messagebox.showinfo(title, "削除する項目が選ばれていません。")
            return
        if is_app_running():
            messagebox.showwarning(title, "DualMicTranscriber が実行中です。アプリを閉じてから、もう一度実行してください。")
            return
        lines = "\n".join(f"・{t.label}: {t.path}" for t in chosen)
        warn = ""
        if any(t.key == "sessions" for t in chosen):
            warn = "\n\n【注意】録音・文字起こし結果も削除されます。元に戻せません。"
        if not messagebox.askyesno(title, f"次の項目を削除します。よろしいですか？\n\n{lines}{warn}", icon="warning"):
            return
        uninstall_btn.state(["disabled"])
        cancel_btn.state(["disabled"])
        status.set("削除中…")

        def work():
            results = []
            for t in chosen:
                root.after(0, lambda t=t: status.set(f"削除中: {t.path}"))
                results.append((t, delete_target(t)))
            root.after(0, lambda: finish(results))

        threading.Thread(target=work, daemon=True).start()

    def finish(results):
        failed = [(t, e) for t, e in results if e]
        state["delete_self"] = any(t.key == "self" and not e for t, e in results)
        for t, e in results:
            size_vars[t.key].set("削除失敗" if e else ("閉じた後に削除" if t.key == "self" else "削除済み"))
        if failed:
            msg = "\n".join(f"✖ {t.path}\n   {e}" for t, e in failed)
            status.set(f"一部の項目を削除できませんでした:\n{msg}")
            messagebox.showwarning(title, f"一部の項目を削除できませんでした。\n\n{msg}")
        else:
            extra = "（このアンインストーラは閉じた数秒後に削除されます）" if state["delete_self"] else ""
            status.set(f"アンインストールが完了しました。{extra}")
        close_btn.pack(side="right")

    uninstall_btn = ttk.Button(btns, text="アンインストール", command=do_uninstall)
    cancel_btn = ttk.Button(btns, text="キャンセル", command=root.destroy)
    close_btn = ttk.Button(btns, text="閉じる", command=on_close)
    cancel_btn.pack(side="right", padx=(8, 0))
    uninstall_btn.pack(side="right")
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():
    # CI 用: 削除対象の一覧をファイルに書いて終了する（何も削除しない）
    if len(sys.argv) >= 3 and sys.argv[1] == "--list":
        lines = [
            f"{t.key}\t{'exists' if t.exists else 'missing'}\tdefault={t.default}\t{t.path}"
            for t in find_targets(self_exe())
        ]
        Path(sys.argv[2]).write_text("\n".join(lines), encoding="utf-8")
        return
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    run_gui()
