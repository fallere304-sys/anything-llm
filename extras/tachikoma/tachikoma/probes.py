"""情報収集行為 (プローブ) の実装。すべて読み取り専用。

任意のコマンド実行は意図的に持たない。自律エージェントが「調べる」だけなら、壊すものは何もない。
外に出るのは web_search の検索語だけで、個人情報らしき語は web.sanitize() が止める。
"""

import os

from .text import clip


class Probes:
    def __init__(self, cfg, memory, web=None, camera=None, llm=None):
        self.cfg = cfg
        self.memory = memory
        self.web, self.camera, self.llm = web, camera, llm

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
        if name not in ("search_memory", "grep_workspace", "read_file", "wait_observe", "web_search", "look"):
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

    def web_search(self, query):
        return self.web.search(query) if self.web is not None else None

    def look(self, query):
        """カメラの今の 1 枚を Gemma に見せ、問いに沿って説明させる (画像は保存しない)。"""
        if self.camera is None or self.llm is None:
            return None
        img = self.camera.snapshot_b64()
        if img is None:
            return None
        from . import prompts
        text = self.llm.chat(prompts.VISION_SYSTEM, f"確かめたい点: {query or '今の様子'}",
                             images=[img], max_tokens=150)
        return f"カメラ映像の説明: {text}" if text else None
