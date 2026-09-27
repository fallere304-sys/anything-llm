"""感覚器: ユーザーのアウトプットを LLM を使わずに観測する層。

ここは毎 tick 走るので CPU で軽く済むものだけを置く。
キー入力そのものは取らない (キーロガーにしない)。取るのは
「ユーザーが外に出したもの」= 保存したファイル・端末の出力・
コピーしたもの・前面ウィンドウ名・チャットでの発言。
"""

import difflib
import os
import queue
import sys
import threading

from .text import clip


class Sensor:
    name = "sensor"

    def poll(self):
        """[(kind, content), ...] を返す。"""
        return []


class FileWatchSensor(Sensor):
    """監視フォルダ内の保存を検出し、変更差分を出来事にする (mtime ポーリング)。"""
    name = "files"

    def __init__(self, dirs, exts, max_bytes, scan_every=3):
        self.dirs, self.exts, self.max_bytes = dirs, tuple(exts), max_bytes
        self.scan_every = scan_every
        self._n = 0
        self._mtimes, self._cache = {}, {}
        self._scan(initial=True)

    def _files(self):
        for root in self.dirs:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv")]
                for fn in filenames:
                    if fn.endswith(self.exts):
                        yield os.path.join(dirpath, fn)

    def _read(self, path):
        try:
            if os.path.getsize(path) > self.max_bytes:
                return None
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def _scan(self, initial=False):
        out = []
        for path in self._files():
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if self._mtimes.get(path) == m:
                continue
            self._mtimes[path] = m
            text = self._read(path)
            if text is None:
                continue
            old = self._cache.get(path)
            self._cache[path] = text
            if initial:
                continue
            if old is None:
                out.append(("file_created", f"{path} を作成\n{clip(text, 800)}"))
            else:
                diff = [l for l in difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="", n=1)
                        if not l.startswith(("---", "+++"))]
                if diff:
                    out.append(("file_changed", f"{path} を編集\n" + clip("\n".join(diff), 1200)))
        return out

    def poll(self):
        self._n += 1
        if self._n % self.scan_every:
            return []
        return self._scan()


class LogTailSensor(Sensor):
    """端末ログ (PowerShell の Start-Transcript 等) の追記分を読む。"""
    name = "terminal"

    def __init__(self, paths):
        self._pos = {}
        for p in paths:
            try:
                self._pos[p] = os.path.getsize(p)
            except OSError:
                self._pos[p] = 0

    def poll(self):
        out = []
        for p, pos in self._pos.items():
            try:
                size = os.path.getsize(p)
                if size < pos:
                    pos = 0
                if size == pos:
                    continue
                with open(p, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                self._pos[p] = size
            except OSError:
                continue
            for enc in ("utf-8", "utf-16", "cp932"):
                try:
                    text = chunk.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = chunk.decode("utf-8", "replace")
            text = text.strip()
            if text:
                out.append(("terminal_output", clip(text[-2000:], 2000)))
        return out


class ActiveWindowSensor(Sensor):
    """前面ウィンドウのタイトル (= いま何をしているかの粗い手がかり)。Windows のみ。"""
    name = "window"

    def __init__(self):
        self._last = None
        self._ok = sys.platform == "win32"
        if self._ok:
            import ctypes
            self._user32 = ctypes.windll.user32
            self._buf = ctypes.create_unicode_buffer(512)

    def poll(self):
        if not self._ok:
            return []
        hwnd = self._user32.GetForegroundWindow()
        self._user32.GetWindowTextW(hwnd, self._buf, 512)
        title = self._buf.value
        if title and title != self._last:
            self._last = title
            return [("window_focus", f"前面ウィンドウ: {title}")]
        return []


class ClipboardSensor(Sensor):
    name = "clipboard"

    def __init__(self):
        self._last = None
        try:
            import tkinter
            self._tk = tkinter.Tk()
            self._tk.withdraw()
        except Exception:
            self._tk = None

    def poll(self):
        if self._tk is None:
            return []
        try:
            text = self._tk.clipboard_get()
        except Exception:
            return []
        if text and text != self._last:
            first = self._last is None
            self._last = text
            if not first:
                return [("clipboard_copy", "コピー: " + clip(text, 800))]
        return []


class ConsoleInput(Sensor):
    """ユーザーからの直接の話しかけ。別スレッドで stdin を読む。"""
    name = "user"

    def __init__(self):
        self._q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in sys.stdin:
            line = line.strip()
            if line:
                self._q.put(line)

    def poll(self):
        out = []
        while not self._q.empty():
            out.append(("user_message", self._q.get_nowait()))
        return out


def idle_seconds():
    """最後の入力からの秒数 (発話タイミングの判断用)。Windows 以外は None。"""
    if sys.platform != "win32":
        return None
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def build(cfg, with_console=True):
    sensors = []
    if cfg["watch_dirs"]:
        sensors.append(FileWatchSensor(cfg["watch_dirs"], cfg["watch_exts"], cfg["max_file_bytes"]))
    if cfg["terminal_logs"]:
        sensors.append(LogTailSensor(cfg["terminal_logs"]))
    if cfg["active_window"]:
        sensors.append(ActiveWindowSensor())
    if cfg["clipboard"]:
        sensors.append(ClipboardSensor())
    if with_console:
        sensors.append(ConsoleInput())
    return sensors
