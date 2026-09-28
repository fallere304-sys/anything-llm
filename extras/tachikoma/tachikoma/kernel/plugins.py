"""プラグイン (カーネル): 自己進化が「新しい振る舞い」を書き足すための場所と、その狭い入口。

思考のコードの改良は、既存の関数を少しずつ変えることしかできない。まったく新しい振る舞い
(例: 目の端で知ったことを、覚えている誰かと結びつけて考え、相棒に伝える) は、既存の関数の小さな変更からは
生まれにくい。そこで自己進化は `evolvable/plugins/NAME.py` を新しく書ける。

プラグインは 2 つの関数を持てる:
    on_event(api, event)   新しい出来事ごとに呼ばれる (event: id, ts, source, kind, content, priority)
    on_tick(api)           毎 tick 呼ばれる

プラグインが外界と自分の中身に触れられるのは、下の PluginAPI の基本動作だけ。書き込めるのは
自分用のメモと、仮説・発話の追加だけ。推論 (LLM) は 1 tick に 1 回まで、会話中は使えない。
静的検査 (import の許可リスト・内部属性への接近の禁止・open/getattr などの禁止) に加えて、
実行時も組み込み関数を絞った環境で動かす。例外を繰り返すプラグインは止め、例外は記録して
「頑健さ」の進化の材料にする。

振る舞いの中身はここには書かない。何を書くかは自己進化が、選択圧
(自分から言ったことが相棒の役に立ったか・後で役立つ情報を拾えたか) の下で決める。
"""

import builtins
import importlib
import json
import os
import threading
import time
import traceback

from . import ROOT
from .guard import PLUGIN_ALLOWED, check_source

PLUGIN_DIR = os.path.join("evolvable", "plugins")
SAFE_BUILTINS = ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset", "int",
                 "isinstance", "len", "list", "map", "max", "min", "range", "reversed", "round", "set",
                 "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "KeyError", "IndexError",
                 "TypeError", "ZeroDivisionError", "True", "False", "None")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.split(".")[0]
    if level or not (name in PLUGIN_ALLOWED or top in PLUGIN_ALLOWED):
        raise ImportError(f"プラグインでは import できない: {name}")
    if top == "tachikoma":
        # `import tachikoma.text` だとパッケージ全体 (カーネルを含む) に手が届くので、from 形式だけ許す
        if not fromlist:
            raise ImportError("tachikoma.text は `from tachikoma.text import ...` の形でだけ使える")
        return importlib.import_module(name)
    return __import__(name, globals, locals, fromlist, level)


def safe_builtins():
    b = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    b["__import__"] = _safe_import
    return b


class PluginAPI:
    """プラグインが使える基本動作。どれも読み取りか、仮説・発話・自分用メモの追加だけ。

    now()                            いまの時刻 (秒)
    events(n=20, since_id=0)         最近の出来事 [{id, ts, source, kind, content, priority}]
    beliefs(limit=50)                いま持っている仮説・知識 [{id, statement, label, p, source, relevance, origin}]
    recall(query, k=5)               過去の出来事を言葉で検索 [文字列]
    interests(n=10)                  自分が興味を育てた話題 [語]
    wonder(statement, relevance)     確かめたい仮説を立てる (好奇心・深掘りの対象になる) → id
    focus(belief_id, relevance)      その仮説への注意の強さを変える (0-1)
    investigate(belief_id)           その仮説を調べる (推論を 1 回使う) → {verdict, gain}
    think(instruction, text)         推論を 1 回使って考える (自由な文章で返る)。使えなければ None
    search(where, query)             調べる: where は news / web / papers / workspace (読み取りのみ) → 文字列 or None
    watch(topic, days)               ニュースでその語をしばらく見張る
    say(text, importance)            相棒に伝えたいことを置く (0-1。言うかどうか・いつ言うかは発話の規則が決める)
    note(key, value=None)            自分用のメモ (value を渡すと保存、渡さないと読み出し)
    """

    def __init__(self, host, name):
        self._host, self._name = host, name

    @property
    def _a(self):
        return self._host.agent

    def now(self):
        return self._a.clock()

    def events(self, n=20, since_id=0):
        rows = self._a.memory.db.execute(
            "SELECT id, ts, source, kind, content, priority FROM events WHERE id>? AND source!='self'"
            " ORDER BY id DESC LIMIT ?", (since_id, max(1, min(int(n), 200)))).fetchall()
        return [dict(zip(("id", "ts", "source", "kind", "content", "priority"), tuple(r))) for r in reversed(rows)]

    def beliefs(self, limit=50):
        bs = sorted(self._a.memory.beliefs(), key=lambda b: -b.relevance)[: max(1, min(int(limit), 500))]
        return [{"id": b.id, "statement": b.statement, "label": b.label(), "p": round(b.p_now(), 3),
                 "source": b.source, "relevance": b.relevance, "origin": b.origin} for b in bs]

    def recall(self, query, k=5):
        return [f"({r['source']}/{r['kind']}) {r['content'][:300]}" for r in self._a.memory.search_events(query, k=k)]

    def interests(self, n=10):
        return self._a.selfm.top_interests(n)

    def wonder(self, statement, relevance=0.5):
        s = str(statement or "").strip()[:300]
        if not s:
            return None
        return self._a._hypothesis(s, 0.5, relevance=min(max(float(relevance), 0.0), 1.0), basis="plugin",
                                   origin=f"plugin/{self._name}")

    def focus(self, belief_id, relevance):
        self._a.memory.set_relevance(int(belief_id), min(max(float(relevance), 0.0), 1.0))

    def investigate(self, belief_id):
        if not self._host.take_llm():
            return None
        b = self._a.memory.get_belief(int(belief_id))
        if b is None:
            return None
        allowed = [p for p in self._a.allowed_probes() if p != "ask_user"]
        verdict, _, gain = self._a.investigate(b, 0.5, allowed=allowed)
        return {"verdict": verdict.get("verdict"), "gain": gain}

    def think(self, instruction, text, max_tokens=200):
        if not self._host.take_llm():
            return None
        return self._a.llm.chat(str(instruction)[:1500], str(text)[:4000], max_tokens=min(int(max_tokens), 400))

    def search(self, where, query):
        probe = {"news": "news_search", "web": "web_search", "papers": "research",
                 "workspace": "grep_workspace"}.get(where)
        if probe is None or probe not in self._a.allowed_probes():
            return None
        res = self._a.probes.run(probe, str(query)[:200])
        return res[0] if isinstance(res, tuple) else res

    def watch(self, topic, days=7):
        news = getattr(self._a.probes, "news", None)
        if news is not None:
            news.watch(str(topic)[:80], min(max(float(days), 0.1), 30))

    def say(self, text, importance=0.5):
        t = str(text or "").strip()[:400]
        if t:
            self._a.memory.add_utterance("plugin", t, min(max(float(importance), 0.0), 1.0))

    def note(self, key, value=None):
        k = f"{self._name}:{str(key)[:80]}"
        db = self._a.memory.db
        if value is None:
            row = db.execute("SELECT v FROM plugin_notes WHERE k=?", (k,)).fetchone()
            return json.loads(row[0]) if row else None
        db.execute("INSERT OR REPLACE INTO plugin_notes(k, v) VALUES (?, ?)", (k, json.dumps(value, ensure_ascii=False)[:20000]))
        db.commit()
        return value


class PluginHost:
    def __init__(self, cfg, agent, metrics=None, root=ROOT, log=None):
        self.cfg, self.agent, self.metrics, self.root = cfg, agent, metrics, root
        self.log = log or (lambda m: None)
        self.plugins = {}                 # name -> {"ns": 名前空間, "errors": 回数, "api": PluginAPI}
        self._llm_left = 0
        self._last_event = None
        agent.memory.db.execute("CREATE TABLE IF NOT EXISTS plugin_notes (k TEXT PRIMARY KEY, v TEXT)")
        agent.memory.db.commit()

    def load(self):
        d = os.path.join(self.root, PLUGIN_DIR)
        if not os.path.isdir(d):
            return []
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            rel = f"evolvable/plugins/{fn}"
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                src = f.read()
            errs = check_source(rel, src, plugin=True)
            if errs:
                self.log(f"プラグイン {fn} は検査で不合格なので読み込まない: {errs[0]}")
                continue
            ns = {"__builtins__": safe_builtins(), "__name__": f"tachikoma_plugin_{fn[:-3]}"}
            try:
                exec(compile(src, os.path.join(d, fn), "exec"), ns)   # noqa: S102 — 検査済み・組み込み関数を絞った環境
            except Exception as e:  # noqa: BLE001
                self._error(fn[:-3], e)
                continue
            if not (callable(ns.get("on_event")) or callable(ns.get("on_tick"))):
                continue
            self.plugins[fn[:-3]] = {"ns": ns, "errors": 0, "api": PluginAPI(self, fn[:-3])}
        if self.plugins:
            self.log(f"プラグイン: {', '.join(self.plugins)}")
        return list(self.plugins)

    def take_llm(self):
        if self._llm_left <= 0:
            return False
        self._llm_left -= 1
        return True

    def _error(self, name, e):
        if self.metrics is not None:
            self.metrics.record_error(e, traceback.extract_tb(e.__traceback__), traceback.format_exc())
        p = self.plugins.get(name)
        if p is not None:
            p["errors"] += 1
            if p["errors"] >= self.cfg["plugin_max_errors"]:
                self.plugins.pop(name)
                self.log(f"プラグイン {name} は例外が続いたので止めた")

    def tick(self):
        if not self.plugins:
            return
        a = self.agent
        free = a.llm.gate.can_run_background() and not a.attention.conversing()
        self._llm_left = 1 if free else 0
        db = a.memory.db
        if self._last_event is None:
            row = db.execute("SELECT MAX(id) FROM events").fetchone()
            self._last_event = row[0] or 0
        rows = db.execute("SELECT id, ts, source, kind, content, priority FROM events WHERE id>? AND source!='self'"
                          " ORDER BY id LIMIT 50", (self._last_event,)).fetchall()
        events = [dict(zip(("id", "ts", "source", "kind", "content", "priority"), tuple(r))) for r in rows]
        if rows:
            self._last_event = rows[-1][0]
        t0 = time.perf_counter()
        for name, p in list(self.plugins.items()):
            if not self._run(name, p, events):
                self.plugins.pop(name, None)
                self.log(f"プラグイン {name} は時間内に終わらなかったので止めた")
                if self.metrics is not None:
                    self.metrics.record("plugin_timeout", 1.0)
        if self.metrics is not None:
            self.metrics.record("plugin_s", time.perf_counter() - t0)

    def _run(self, name, p, events):
        """1 つのプラグインを見張り付きで動かす。制限時間を超えたら False (本体は待たずに進む)。"""
        def body():
            try:
                if callable(p["ns"].get("on_event")):
                    for ev in events:
                        p["ns"]["on_event"](p["api"], dict(ev))
                if callable(p["ns"].get("on_tick")):
                    p["ns"]["on_tick"](p["api"])
            except Exception as e:  # noqa: BLE001 — プラグインの失敗で本体を止めない
                self._error(name, e)
        th = threading.Thread(target=body, daemon=True, name=f"plugin-{name}")
        th.start()
        th.join(self.cfg["plugin_timeout_s"])
        return not th.is_alive()
