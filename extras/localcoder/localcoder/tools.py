"""エージェントの道具。どれも作業フォルダ (workspace) の中だけで動く。

    list_files   フォルダの中身を見る
    read_file    ファイルを読む (行番号つき・範囲指定)
    write_file   ファイルを新しく書く / 丸ごと書き直す
    edit_file    ファイルの一部を置き換える (old と完全に一致した 1 か所だけ)
    search       正規表現でファイルの中を探す
    run          コマンドを実行する (PowerShell / sh。時間制限つき・出力は末尾を切り詰め)

作業フォルダの外 (.. や絶対パス) には触れない。コマンドの実行は、相棒が許したときだけ (Agent が確認する)。
"""

import fnmatch
import os
import re
import subprocess
import sys
import threading

MAX_READ = 60_000          # 一度に読ませる文字数の上限 (文脈を食いつぶさないように)
MAX_OUT = 12_000           # コマンドの出力は末尾をこれだけ返す
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist", ".mypy_cache"}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "作業フォルダの中のファイルとフォルダを一覧する",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "作業フォルダからの相対パス (省略で作業フォルダ)"},
            "pattern": {"type": "string", "description": "名前の絞り込み (例: *.py)"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "ファイルを行番号つきで読む",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start": {"type": "integer", "description": "最初の行 (1 始まり)"},
            "end": {"type": "integer", "description": "最後の行"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "ファイルを新しく書く、または丸ごと書き直す (フォルダは自動で作る)",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "ファイルの中の old と完全に一致する 1 か所を new に置き換える",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "search", "description": "正規表現でファイルの中身を探す (ファイル名:行番号: 行)",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "glob": {"type": "string", "description": "対象ファイル (例: *.py)"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "run", "description": "作業フォルダでコマンドを実行する (Windows は PowerShell)。テスト・実行・ビルドに使う",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer", "description": "秒 (既定 300)"}},
            "required": ["command"]}}},
]
NAMES = {s["function"]["name"] for s in SCHEMAS}


class ToolError(Exception):
    pass


class Tools:
    def __init__(self, workspace, env=None, shell=None):
        self.root = os.path.abspath(workspace)
        os.makedirs(self.root, exist_ok=True)
        self.env = env
        self.shell = shell or (["powershell", "-NoProfile", "-NonInteractive", "-Command"] if os.name == "nt"
                               else ["sh", "-c"])
        self.cancelled = threading.Event()
        self._proc = None
        self._lock = threading.Lock()

    def cancel(self):
        """実行中のコマンドを (そこから起きたプログラムごと) 止める。"""
        self.cancelled.set()
        with self._lock:
            p = self._proc
        if p is not None:
            _kill_tree(p)

    # ------------------------------------------------------------ 場所
    def path(self, rel):
        rel = (rel or ".").strip().strip('"').replace("\\", "/")
        full = os.path.abspath(os.path.join(self.root, rel))
        root = os.path.normcase(self.root)
        if os.path.normcase(full) != root and not os.path.normcase(full).startswith(root.rstrip("\\/") + os.sep):
            raise ToolError(f"作業フォルダの外には触れません: {rel}")
        return full

    def rel(self, full):
        return os.path.relpath(full, self.root).replace("\\", "/")

    # ------------------------------------------------------------ 道具
    def call(self, name, args):
        if name not in NAMES:
            raise ToolError(f"そんな道具はありません: {name} (使えるのは {', '.join(sorted(NAMES))})")
        if not isinstance(args, dict):
            raise ToolError("引数は JSON のオブジェクトで渡してください")
        return getattr(self, name)(**{k: v for k, v in args.items() if v is not None})

    def list_files(self, path=".", pattern=None, limit=400):
        base = self.path(path)
        if not os.path.isdir(base):
            raise ToolError(f"フォルダがありません: {path}")
        out = []
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in sorted(files):
                if pattern and not fnmatch.fnmatch(f, pattern):
                    continue
                full = os.path.join(d, f)
                out.append(f"{self.rel(full)}  ({os.path.getsize(full)} バイト)")
                if len(out) >= limit:
                    return "\n".join(out + [f"… (最初の {limit} 件だけ)"])
        return "\n".join(out) or "(空)"

    def read_file(self, path, start=None, end=None):
        full = self.path(path)
        if not os.path.isfile(full):
            raise ToolError(f"ファイルがありません: {path}")
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        s = max(1, int(start or 1))
        e = min(len(lines), int(end or len(lines)))
        body = "\n".join(f"{i:5}  {lines[i - 1]}" for i in range(s, e + 1))
        if len(body) > MAX_READ:
            body = body[:MAX_READ] + f"\n… (長いので途中まで。start と end で続きを読める。全 {len(lines)} 行)"
        return body or "(空のファイル)"

    def write_file(self, path, content):
        full = self.path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"書きました: {self.rel(full)} ({len(content.splitlines())} 行)"

    def edit_file(self, path, old, new):
        full = self.path(path)
        if not os.path.isfile(full):
            raise ToolError(f"ファイルがありません: {path}")
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
        n = text.count(old)
        if old == "" or n == 0:
            # 改行の違い (CRLF / LF) だけなら合わせて探す
            alt = old.replace("\r\n", "\n").replace("\n", "\r\n")
            if old and text.count(alt) == 1:
                old, new, n = alt, new.replace("\r\n", "\n").replace("\n", "\r\n"), 1
            else:
                raise ToolError("old と一致する箇所がありません。read_file で今の中身を確かめてから、そのまま写してください")
        if n > 1:
            raise ToolError(f"old と一致する箇所が {n} か所あります。前後の行も含めて、1 か所に決まるようにしてください")
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new, 1))
        return f"直しました: {self.rel(full)}"

    def search(self, pattern, glob=None, limit=200):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"正規表現が読めません: {e}")
        hits = []
        for d, dirs, files in os.walk(self.root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in sorted(files):
                if glob and not fnmatch.fnmatch(f, glob):
                    continue
                full = os.path.join(d, f)
                try:
                    with open(full, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if rx.search(line):
                                hits.append(f"{self.rel(full)}:{i}: {line.rstrip()[:200]}")
                                if len(hits) >= limit:
                                    return "\n".join(hits + ["… (多いので途中まで)"])
                except (UnicodeDecodeError, OSError):
                    continue
        return "\n".join(hits) or "(見つかりませんでした)"

    def run(self, command, timeout=300):
        if self.shell and self.shell[0].lower().startswith("powershell"):
            # PowerShell は既定では出力を古い文字コードで流す (日本語が ??? になる)。UTF-8 で受け渡す
            command = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                       "$OutputEncoding = [System.Text.Encoding]::UTF8; " + command)
        try:
            p = subprocess.Popen(self.shell + [command], cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                 start_new_session=os.name != "nt")
        except OSError as e:
            return f"エラー: コマンドを始められません: {e}"
        with self._lock:
            self._proc = p
        try:
            stdout, stderr = p.communicate(timeout=max(5, min(int(timeout), 3600)))
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            p.communicate()
            return f"(時間切れ: {timeout} 秒で止めました)"
        finally:
            with self._lock:
                self._proc = None
        if self.cancelled.is_set():
            return "(相棒が「止める」を押したので、途中で止めました)"
        out = _decode(stdout) + (("\n[stderr]\n" + _decode(stderr)) if stderr else "")
        if len(out) > MAX_OUT:
            out = "… (前半は省略)\n" + out[-MAX_OUT:]
        return f"(終了コード {p.returncode})\n{out.strip()}"


def _kill_tree(p):
    """コマンドと、そこから起きたプログラムをまとめて止める。"""
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        import signal
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()


def _decode(b):
    for enc in ("utf-8-sig", "cp932" if sys.platform == "win32" else "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")
