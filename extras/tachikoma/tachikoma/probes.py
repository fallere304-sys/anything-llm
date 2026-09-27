"""情報収集行為 (プローブ) の実装。すべて読み取り専用。

コマンド実行や外部送信は意図的に持たない。自律エージェントが
「調べる」だけなら、壊すものは何もない。
"""

import os

from .text import clip


class Probes:
    def __init__(self, cfg, memory):
        self.cfg = cfg
        self.memory = memory

    def _allowed_path(self, path):
        path = os.path.abspath(path)
        for d in self.cfg["watch_dirs"]:
            try:
                if os.path.commonpath([path, d]) == d:
                    return True
            except ValueError:  # Windows: 別ドライブ
                continue
        return False

    def _iter_files(self):
        exts = tuple(self.cfg["watch_exts"])
        for root in self.cfg["watch_dirs"]:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv")]
                for fn in filenames:
                    if fn.endswith(exts):
                        yield os.path.join(dirpath, fn)

    def _read(self, path):
        try:
            if os.path.getsize(path) > self.cfg["max_file_bytes"]:
                return None
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def run(self, name, query):
        """根拠テキストを返す。見つからなければ None。"""
        if name not in ("search_memory", "grep_workspace", "read_file", "wait_observe"):
            return None
        return getattr(self, name)(query)

    def search_memory(self, query):
        rows = self.memory.search_events(query, k=4)
        if not rows:
            return None
        return "\n".join(f"- ({r['source']}) {clip(r['content'], 300)}" for r in rows)

    def grep_workspace(self, query, max_hits=6):
        """空白区切りのキーワードの半数以上を含む行を拾う (大文字小文字は無視)。"""
        terms = [t.lower() for t in query.split() if len(t) >= 2] or [query.lower()]
        hits = []
        for path in self._iter_files():
            text = self._read(path)
            if not text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                s = sum(t in low for t in terms) / len(terms)
                if s >= 0.5:
                    hits.append((s, f"{path}:{i}: {clip(line.strip(), 200)}"))
        if not hits:
            return None
        hits.sort(key=lambda h: -h[0])
        return "\n".join(h for _, h in hits[:max_hits])

    def read_file(self, query):
        path = query.strip().strip('"')
        if not os.path.isabs(path):
            for d in self.cfg["watch_dirs"]:
                if os.path.exists(os.path.join(d, path)):
                    path = os.path.join(d, path)
                    break
        if not self._allowed_path(path):
            return None
        text = self._read(path)
        return clip(text, 3000) if text else None

    def wait_observe(self, query):
        return None
