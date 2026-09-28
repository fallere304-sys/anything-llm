"""自己進化エンジン: 方向を決める → アイデアを出す → 新しさで選ぶ → 実装 → 隔離テスト
                     → 本番投入 → 試用期間 → 定着 / 撤回 → 成否から学ぶ (自己強化)。

考える力は CPU と RAM で動く (kernel/cpu_brain.py)。GPU は会話・耳・目のために空けておく。

変異の 3 つの階層 (上ほど安全で速く、下ほど大胆):
    param   … 性格・思考のパラメータ (evolvable/params.json)。範囲はカーネル (bounds.json) が決める
    prompt  … 思考の指示文 (evolvable/prompts/*.txt)。判定プロンプトは検証データで事前に採点
    code    … 思考のコード (agent.py 等)。コード生成モデルが書き、静的検査 (guard) と
              Docker 内の全テストに合格したものだけを本番に入れる

進化の方向はタチコマ自身が決める。利用者の判断基準を 6 つの目標にし、
    (いまの必要度) × (その目標の改良がこれまで成功した割合) + 探索の上乗せ
が最大の目標を選ぶ。必要度は実運用の指標 (fitness.window_stats) から計算する。

    頑健さ  実運用で起きた例外を直す (code)
    速さ    プロファイラが見つけた遅い関数を、振る舞いを変えずに速くする (code)
    知識    1Wh あたりに増える知識 (param / prompt / code)
    合理性  確信の較正 Brier (param / prompt)
    電力    平均消費電力 (param)
    関係    相手の評価 (param)

**新しいやり方を高く評価する**: アイデアを複数出させ、論文・ネットで見つからないもの・
自分が試したことの無いものを優先して試す (kernel/novelty.py)。成功したら新しさに応じて
大きな報酬、失敗しても新しいほど罰を軽くする。安全の関門は新しさで緩めない。

本番に入れた変更は試用期間 (canary) の実運用の数値で判定し、悪化していれば自動で元に戻す。
すべての変更は差分つきで記録され、/evolution で見られ、/revert で戻せ、/freeze で止められる。
カーネル (選択の環境・外界との境界) の変更は「提案」だけでき、人間の /approve が必要。
"""

import ast
import difflib
import json
import math
import os
import queue
import random
import shutil
import threading
import time

from . import ROOT, is_evolvable, rel
from .fitness import compare, improved, window_stats
from .guard import check_edits

SCHEMA = """
CREATE TABLE IF NOT EXISTS evolutions (
    id INTEGER PRIMARY KEY, ts REAL, level TEXT, target TEXT, rationale TEXT, diff TEXT,
    status TEXT, deployed REAL, decided REAL, note TEXT, edits TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""
EXCLUDE = {"__pycache__", ".git", "finetune_runs", "study", "lab", "evolution", "anchors", ".venv", "venv",
           "llama.cpp", ".gradle", ".cxx", "build"}
PROMPT_TARGETS = ("APPRAISE_SYSTEM", "PLAN_SYSTEM", "JUDGE_SYSTEM", "DIGEST_SYSTEM",
                  "INQUIRY_SYSTEM", "WONDER_SYSTEM")

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "edits": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "search": {"type": "string"},
                           "replace": {"type": "string"}},
            "required": ["file", "search", "replace"]}},
    },
    "required": ["rationale", "edits"],
}
PROMPT_SCHEMA = {"type": "object", "properties": {"rationale": {"type": "string"}, "prompt": {"type": "string"}},
                 "required": ["rationale", "prompt"]}
IDEAS_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
                       "description": {"type": "string"}},
        "required": ["name", "keywords", "description"]}}},
    "required": ["ideas"],
}
IDEAS_SYSTEM = (
    "あなたは自分自身 (相棒AI『タチコマ』) を改良する方法を考える研究者です。目標に対して、"
    "性質の異なる改良アイデアを 3 つ出します。少なくとも 1 つは、教科書や既存の定番には無い、"
    "自分で考えた新しいやり方にすること。keywords はそのやり方を論文・ネットで検索するための英語の語 (3〜6 個)。"
)

GOALS = {
    "robustness": {"label": "頑健さ", "metric": "errors_per_kstep", "levels": ("code",)},
    "speed": {"label": "速さ", "metric": "step_p90", "levels": ("code",)},
    "knowledge": {"label": "知識の効率", "metric": "knowledge_per_wh", "levels": ("param", "prompt", "code"),
                  "params": ("curiosity_threshold", "challenge_share", "wonder_interval_s", "max_probe_attempts",
                             "relevance_half_life_s"),
                  "prompts": ("PLAN_SYSTEM", "INQUIRY_SYSTEM", "WONDER_SYSTEM"),
                  "files": ("tachikoma/curiosity.py",)},
    "rationality": {"label": "合理性", "metric": "brier", "levels": ("param", "prompt"),
                    "params": ("persona_skepticism", "challenge_share"), "prompts": ("JUDGE_SYSTEM", "APPRAISE_SYSTEM")},
    "power": {"label": "省電力", "metric": "watts_mean", "levels": ("param",),
              "params": ("idle_rest_value", "wonder_interval_s")},
    "rapport": {"label": "関係", "metric": "good_ratio", "levels": ("param",),
                "params": ("speak_threshold", "min_speak_interval_s", "persona_playfulness", "chat_temperature")},
    # 何を先に考えるか (情報の優先度) の当たり具合。周辺の情報をどれだけ拾うかも含めて進化させる
    "foresight": {"label": "先見", "metric": "foresight_auc", "levels": ("param", "code", "plugin"),
                  "params": ("peripheral_share", "peripheral_weight", "peripheral_relevance", "novelty_threshold",
                             "dig_threshold", "dig_leave_ratio"),
                  "files": ("tachikoma/curiosity.py", "tachikoma/inquiry.py"),
                  "pressure": "周りで拾った情報のうち、後で相棒の役に立つものほど先に・深く考えられるようになる"},
    # 自分から (話しかけられていないのに) 言ったことが、相棒の役に立つ。どう振る舞えばそうなるかは書かない
    "initiative": {"label": "自発性", "metric": "initiative", "levels": ("param", "code", "plugin"),
                   "params": ("speak_threshold", "min_speak_interval_s", "peripheral_share", "dig_threshold",
                              "dig_leave_ratio"),
                   "files": ("tachikoma/inquiry.py", "tachikoma/curiosity.py"),
                   "pressure": "話しかけられていないのに自分から言ったことに、相棒が反応してくれる (役に立つ・"
                               "関心を持たれる) ことが増える。うるさがられてはいけない"},
}
PLUGIN_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "rationale": {"type": "string"},
                                                  "code": {"type": "string"}},
                 "required": ["name", "rationale", "code"]}
PLUGIN_SYSTEM = (
    "あなたは Python の熟練者で、自分自身 (相棒AI『タチコマ』) に新しい振る舞いをプラグインとして書き足します。"
    "\n- ファイル 1 つ。on_event(api, event) と on_tick(api) の少なくとも片方を定義する"
    "\n- 外界と自分の中身には api の基本動作でだけ触れる。import できるのは re, math, json, statistics, collections,"
    " itertools, functools, datetime, time, random, string, unicodedata と `from tachikoma.text import ...` だけ"
    "\n- _ で始まる名前への接近、open / getattr / type などは使えない"
    "\n- 推論 (api.think / api.investigate) は 1 tick に 1 回しか使えず、使えないときは None が返る。必ず None を想定する"
    "\n- 毎 tick 呼ばれるので軽く。重い処理は api.note に状態を持って少しずつ進める"
    "\n- name は英小文字と _ の 3〜30 文字"
)
MIGRATIONS = (("goal", "TEXT"), ("novelty", "REAL"), ("approach", "TEXT"))

CODER_SYSTEM = (
    "あなたは Python の熟練者で、自分自身 (相棒AI『タチコマ』) のソースコードを改良します。"
    "与えられた目標を満たす、最小で安全な変更を search/replace で書きます。\n"
    "- search は対象ファイルの中でちょうど1箇所に一致する、元のコードそのままの断片\n"
    "- 振る舞いを変える場合は目標の範囲だけ。既存のテストが通ること\n"
    "- subprocess・ネットワーク・ファイル書き込み・eval などは使えない (外界にはカーネル経由でのみ触れる)\n"
    "- 自信が無ければ edits を空にしてよい (知ったかぶりの変更より、何もしない方が良い)"
)


def load_bounds():
    with open(os.path.join(os.path.dirname(__file__), "bounds.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def apply_params(cfg, root=ROOT):
    """起動時: 進化したパラメータを、範囲内かつ利用者が明示していないキーにだけ適用する。"""
    path = os.path.join(root, "evolvable", "params.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        params = json.load(f)
    bounds, applied = load_bounds(), {}
    for k, v in params.items():
        if k in bounds and k not in cfg.get("_user_keys", ()):
            lo, hi = bounds[k]
            v = min(max(v, lo), hi)
            cfg[k] = int(round(v)) if isinstance(lo, int) and isinstance(hi, int) else v
            applied[k] = cfg[k]
    return applied


def apply_prompts(root=ROOT):
    """起動時: 自己進化で書き直された指示文 (evolvable/prompts/NAME.txt) を反映する。"""
    from .. import prompts
    d = os.path.join(root, "evolvable", "prompts")
    applied = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            name = fn[:-4]
            if fn.endswith(".txt") and name in PROMPT_TARGETS and isinstance(getattr(prompts, name, None), str):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    setattr(prompts, name, text)
                    applied.append(name)
    return applied


def function_source(source, line):
    """line を含む最も内側の関数の定義を返す (コード生成モデルに渡す範囲を絞る)。"""
    tree = ast.parse(source)
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno:
            if best is None or node.lineno >= best.lineno:
                best = node
    if best is None:
        return None
    lines = source.splitlines()
    return "\n".join(lines[best.lineno - 1: best.end_lineno])


class Evolution:
    def __init__(self, cfg, memory, brain, sandbox, metrics, root=ROOT, clock=time.time, rng=None,
                 novelty=None):
        self.cfg, self.memory, self.brain = cfg, memory, brain
        self.sandbox, self.metrics, self.novelty = sandbox, metrics, novelty
        self.root, self.clock = os.path.abspath(root), clock
        self.rng = rng or random.Random()
        self.db = memory.db
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(evolutions)")}
        for name, decl in MIGRATIONS:
            if name not in cols:
                self.db.execute(f"ALTER TABLE evolutions ADD COLUMN {name} {decl}")
        self.dir = os.path.abspath(cfg["evolution_dir"])
        os.makedirs(self.dir, exist_ok=True)
        self.busy, self.stage = False, ""
        self.restart_requested = False
        self._results = queue.Queue()
        self._thread = None
        self._abort = threading.Event()
        self.hotspots = []          # runtime のプロファイラが入れる [(relpath, func, 秒)]

    # ------------------------------------------------------------ 状態
    def _kv(self, k, v=None):
        if v is None:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))
        self.db.commit()

    @property
    def frozen(self):
        return bool(self._kv("evolution_frozen"))

    def freeze(self, on=True):
        self._kv("evolution_frozen", bool(on))

    def canary(self):
        return self.db.execute("SELECT * FROM evolutions WHERE status='canary' ORDER BY id DESC LIMIT 1").fetchone()

    def ready(self, now=None):
        now = now or self.clock()
        if not self.cfg["evolution_enabled"] or self.frozen or self.busy or self.canary():
            return False
        return now - (self._kv("evolution_last") or 0) >= self.cfg["evolution_interval_s"]

    def history(self, n=10):
        return self.db.execute("SELECT * FROM evolutions ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def brain_ok(self):
        return self.brain is not None and self.brain.available()

    # ------------------------------------------------------------ 方向を決める (自己強化)
    def stats(self):
        st = self._kv("evolution_stats") or {}
        for g in GOALS:
            st.setdefault(g, {"a": 1.0, "b": 1.0, "n": 0})
        return st

    def _open_error(self):
        for e in self.metrics.open_errors():
            r = rel(e["file"], self.root) if e["file"] else ""
            if is_evolvable(r):
                return {"file": r, "line": e["line"], "func": e["func"], "message": e["message"], "trace": e["trace"]}
        return None

    def _hotspot(self):
        for r, func, sec in self.hotspots:
            if is_evolvable(r) and sec > self.cfg["hotspot_min_s"]:
                return {"file": r, "func": func, "seconds": sec}
        return None

    def needs(self, s):
        """各目標の「いまの必要度」(0-1)。実運用の指標から。"""
        n = {}
        err = self._open_error()
        n["robustness"] = min(1.0, 0.6 + 0.1 * s["errors_per_kstep"]) if err else 0.0
        hot = self._hotspot()
        n["speed"] = min(1.0, hot["seconds"] / (4 * self.cfg["hotspot_min_s"])) if hot else 0.0
        n["knowledge"] = 0.4                                   # 知識は常に伸ばす余地がある
        n["rationality"] = 0.2 if s["brier"] is None else min(1.0, max(0.0, (s["brier"] - 0.1) / 0.2))
        n["power"] = 0.1 if s["watts_mean"] is None else min(1.0, max(0.0, (s["watts_mean"] - 40) / 80))
        n["rapport"] = 0.1 if s["good_ratio"] is None else 1.0 - s["good_ratio"]
        # 自発性: 伸ばす余地は常にある。自分から言ったことが役に立っていないほど必要
        prec = s.get("initiative_precision")
        n["initiative"] = 0.4 if prec is None else min(1.0, 0.3 + 0.7 * (1.0 - prec))
        auc = s.get("foresight_auc")
        n["foresight"] = 0.0 if auc is None else min(1.0, max(0.0, (0.85 - auc) / 0.35))
        return n

    def feasible_levels(self, goal):
        levels = []
        for lv in GOALS[goal]["levels"]:
            if lv == "param" and GOALS[goal].get("params"):
                levels.append(lv)
            elif lv == "prompt" and self.brain_ok():
                levels.append(lv)
            elif lv in ("code", "plugin") and self.brain_ok() and self.sandbox is not None and self.sandbox.usable():
                levels.append(lv)
        return levels

    def choose(self):
        """(目標, 階層, 理由)。必要度 × 成功しやすさ + 探索。"""
        now = self.clock()
        s = window_stats(self.metrics, self.db, now - self.cfg["evolution_window_h"] * 3600, now)
        need, st = self.needs(s), self.stats()
        total = sum(v["n"] for v in st.values()) + 1
        best = None
        for g, spec in GOALS.items():
            levels = self.feasible_levels(g)
            if not levels or need[g] <= 0:
                continue
            p = st[g]["a"] / (st[g]["a"] + st[g]["b"])
            score = need[g] * p + self.cfg["evolution_explore"] * math.sqrt(math.log(total + 1) / (st[g]["n"] + 1))
            if best is None or score > best[0]:
                best = (score, g, levels, p)
        if best is None:
            return None, None, "いま改良すべき目標が無い"
        score, g, levels, p = best
        # 大胆な階層 (code > prompt > param) ほど新しいやり方を試せるので、使えるなら優先的に選ぶ
        level = levels[-1] if self.rng.random() < self.cfg["evolution_bold"] else self.rng.choice(levels)
        why = (f"{GOALS[g]['label']}を伸ばしたい (必要度 {need[g]:.2f} × 成功しやすさ {p:.2f})"
               f" → {level} で試す")
        return g, level, why

    def reward(self, goal, outcome, novelty):
        """自己強化: 結果を目標ごとの成功率に反映する。新しい挑戦ほど成功の報酬は大きく、失敗の罰は軽い。"""
        st = self.stats()
        a = st[goal]
        nv = novelty or 0.0
        if outcome == "success":
            a["a"] += 1.0 + self.cfg["novelty_bonus"] * nv
        elif outcome == "neutral":
            a["b"] += 0.5 * (1.0 - 0.5 * nv)
        else:
            a["b"] += 1.0 - 0.5 * nv
        a["n"] += 1
        self._kv("evolution_stats", st)

    # ------------------------------------------------------------ 開始
    def start(self):
        goal, level, why = self.choose()
        self._kv("evolution_last", self.clock())
        if goal is None:
            return None
        target = {"robustness": self._open_error(), "speed": self._hotspot()}.get(goal)
        cur = self.db.execute("INSERT INTO evolutions(ts, level, target, status, goal, rationale) VALUES (?,?,?,?,?,?)",
                              (self.clock(), level, json.dumps(target, ensure_ascii=False) if target else "",
                               "testing", goal, why))
        self.db.commit()
        eid = cur.lastrowid
        if level == "param":
            return self._finish(eid, self._mutate_params(goal))
        self.busy, self.stage = True, level
        self._abort.clear()
        fn = {"code": self._mutate_code, "plugin": self._mutate_plugin}.get(level, self._mutate_prompt)
        self._thread = threading.Thread(target=lambda: self._results.put((eid, fn(goal, target))), daemon=True)
        self._thread.start()
        return f"自己改良を考え中: {why}"

    def _past_approaches(self, limit=200):
        rows = self.db.execute("SELECT approach FROM evolutions WHERE approach IS NOT NULL ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r[0]).get("description", ""))
            except (ValueError, AttributeError):
                pass
        return out

    def _pick_idea(self, ideas):
        """新しさで選ぶ: ネット・論文で見つからない、自分も試したことの無いやり方を優先。"""
        past = self._past_approaches()
        scored = []
        for idea in ideas:
            if self.novelty is not None:
                sc = self.novelty.score(idea, past)
            else:
                from .novelty import NoveltyJudge
                sc = {"novelty": NoveltyJudge.archive_novelty(idea.get("description", ""), past),
                      "web": None, "archive": None, "similar": []}
            scored.append((sc["novelty"], idea, sc))
        scored.sort(key=lambda x: -x[0])
        return scored[0]

    # ------------------------------------------------------------ 変異: param
    def _mutate_params(self, goal):
        bounds = load_bounds()
        keys = [k for k in GOALS[goal].get("params", ()) if k in bounds and k not in self.cfg.get("_user_keys", ())]
        if not keys:
            return {"ok": False, "note": "動かしてよいパラメータが無い", "goal": goal}
        past = self._past_approaches()
        cands = []
        for _ in range(6):   # 候補を複数作り、これまで試していない領域のものを選ぶ
            new = {}
            for k in self.rng.sample(keys, min(2, len(keys))):
                lo, hi = bounds[k]
                cur = self.cfg.get(k, (lo + hi) / 2)
                v = min(max(cur + self.rng.gauss(0, 0.2 * (hi - lo)), lo), hi)
                new[k] = int(round(v)) if isinstance(lo, int) and isinstance(hi, int) else round(v, 4)
            desc = " ".join(f"{k}={v}" for k, v in sorted(new.items()))
            from .novelty import NoveltyJudge
            cands.append((NoveltyJudge.archive_novelty(desc, past), new, desc))
        nov, new, desc = max(cands, key=lambda c: c[0])
        path = "evolvable/params.json"
        params = json.loads(self._read(path) or "{}")
        params.update(new)
        return {"ok": True, "sources": {path: json.dumps(params, ensure_ascii=False, indent=2) + "\n"},
                "rationale": "試したことの少ない組み合わせ: " + ", ".join(f"{k} {self.cfg.get(k)}→{v}" for k, v in new.items()),
                "hot": {"params": new}, "goal": goal, "novelty": nov,
                "approach": {"name": "param", "description": desc, "novelty": {"archive": nov}}}

    # ------------------------------------------------------------ 変異: prompt
    def _mutate_prompt(self, goal, target=None):
        from .. import prompts
        name = self.rng.choice(GOALS[goal]["prompts"])
        cur = getattr(prompts, name, None)
        if not cur:
            return {"ok": False, "note": f"{name} が無い", "goal": goal}
        try:
            self.brain.use("ja")
            ideas = self.brain.chat(IDEAS_SYSTEM, f"# 目標\n{GOALS[goal]['label']}を上げる\n\n# 改良する指示文 ({name})\n{cur}",
                                    schema=IDEAS_SCHEMA, max_tokens=700, temperature=0.9).get("ideas") or []
            if not ideas:
                return {"ok": False, "note": "アイデアが出なかった", "goal": goal}
            nov, idea, sc = self._pick_idea(ideas)
            out = self.brain.chat(
                "あなたは自分 (AI) の思考の指示文を改良します。意味と出力形式を保ったまま、与えられたやり方で書き直します。",
                f"# やり方\n{idea['name']}: {idea['description']}\n\n# 現在の指示文 ({name})\n{cur}",
                schema=PROMPT_SCHEMA, max_tokens=900, temperature=0.6)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"生成失敗: {e}", "goal": goal}
        text = (out.get("prompt") or "").strip()
        if not (40 <= len(text) <= 3000) or text == cur.strip():
            return {"ok": False, "note": "書き直しが短すぎる/長すぎる/変化なし", "goal": goal, "novelty": nov,
                    "approach": dict(idea, novelty=sc)}
        return {"ok": True, "sources": {f"evolvable/prompts/{name}.txt": text + "\n"},
                "rationale": f"{idea['name']}: {out.get('rationale', '')}", "hot": {"prompt": (name, text)},
                "goal": goal, "novelty": nov, "approach": dict(idea, novelty=sc)}

    # ------------------------------------------------------------ 変異: code
    def _read(self, relpath, root=None):
        p = os.path.join(root or self.root, relpath)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    def _code_target(self, goal, target):
        """改良する関数を決める: 例外の場所 / 遅い関数 / 目標に関係するファイルの一部。"""
        if target and target.get("file"):
            src = self._read(target["file"])
            if src is None:
                return None, None, None
            func = function_source(src, target["line"]) if target.get("line") else None
            if func is None and target.get("func"):
                for node in ast.walk(ast.parse(src)):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target["func"]:
                        func = "\n".join(src.splitlines()[node.lineno - 1: node.end_lineno])
                        break
            return target["file"], func or src[:4000], src
        files = tuple(GOALS[goal].get("files") or ())
        if "plugin" in GOALS[goal]["levels"]:
            files += tuple(self._plugin_files())          # 自分で書いたプラグインも改良の対象
        if not files:
            return None, None, None
        path = self.rng.choice(files)
        src = self._read(path)
        funcs = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)]
        node = self.rng.choice(funcs)
        return path, "\n".join(src.splitlines()[node.lineno - 1: node.end_lineno]), src

    def _plugin_files(self):
        d = os.path.join(self.root, "evolvable", "plugins")
        return sorted(f"evolvable/plugins/{f}" for f in os.listdir(d) if f.endswith(".py")) if os.path.isdir(d) else []

    def _observations(self):
        """新しい振る舞いを考える材料: 何が役に立ち、何が役に立たなかったか (カーネルの記録から)。"""
        lines = []
        rows = self.db.execute("SELECT text, engaged, annoyed FROM spoken WHERE proactive=1 ORDER BY id DESC LIMIT 12"
                               ).fetchall() if self._has("spoken") else []
        if rows:
            lines.append("# 自分から言ったことと、相棒の反応 (新しい順)")
            lines += [f"- {'反応あり' if r[1] else ('うるさがられた' if r[2] else '反応なし')}: {r[0][:80]}" for r in rows]
        if self._has("info_items"):
            st = self.db.execute("SELECT bucket, COUNT(*), SUM(used_ts IS NOT NULL) FROM info_items GROUP BY bucket"
                                 " ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
            if st:
                lines.append("# 拾った情報の出どころと、後で相棒の役に立った数")
                lines += [f"- {b}: {n} 件中 {u or 0} 件" for b, n, u in st]
        return "\n".join(lines) or "(まだ記録が少ない)"

    def _has(self, table):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def _mutate_plugin(self, goal, target=None):
        """まったく新しい振る舞いを、プラグインとして書き足す。何を書くかは選択圧と観察から自分で考える。"""
        from .plugins import PluginAPI
        existing = [f"- {p}" for p in self._plugin_files()] or ["(まだ無い)"]
        ctx = (f"# 目標\n{GOALS[goal]['label']}: {GOALS[goal].get('pressure', '')}"
               f"\n\n# プラグインが使える基本動作\n{PluginAPI.__doc__}"
               f"\n\n# 今あるプラグイン\n" + "\n".join(existing) + f"\n\n{self._observations()}")
        try:
            self.stage = "アイデア出し (CPU)"
            self.brain.use("code")
            ideas = self.brain.chat(IDEAS_SYSTEM, ctx, schema=IDEAS_SCHEMA, max_tokens=700, temperature=0.9).get("ideas") or []
            if not ideas:
                return {"ok": False, "note": "アイデアが出なかった", "goal": goal}
            self.stage = "新しさの評価 (論文・ネット検索)"
            nov, idea, sc = self._pick_idea(ideas)
            self.stage = "実装 (CPU)"
            out = self.brain.chat(PLUGIN_SYSTEM, f"{ctx}\n\n# 採用したやり方\n{idea['name']}: {idea['description']}",
                                  schema=PLUGIN_SCHEMA, max_tokens=2000, temperature=0.2)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"プラグイン生成失敗: {e}", "goal": goal}
        base = {"goal": goal, "novelty": nov, "approach": dict(idea, novelty=sc)}
        name = "".join(c for c in (out.get("name") or "").lower() if c.isalnum() or c == "_")[:30]
        code = out.get("code") or ""
        if len(name) < 3 or not code.strip():
            return dict(base, ok=False, note="プラグインの名前か中身が無い")
        path = f"evolvable/plugins/{name}.py"
        if self._read(path) is not None:
            return dict(base, ok=False, note=f"{path} は既にある (改良は code の階層で行う)")
        edits = [{"file": path, "search": "", "replace": code}]
        errors, sources = check_edits(edits, self._read)
        if not errors and "def on_event" not in code and "def on_tick" not in code:
            errors = ["on_event か on_tick を定義していない"]
        if errors:
            return dict(base, ok=False, note="静的検査で不合格: " + "; ".join(errors[:5]), edits=edits)
        ok, output = self._sandbox_test(sources)
        if not ok:
            return dict(base, ok=False, note="テスト不合格:\n" + output[-1200:], edits=edits)
        return dict(base, ok=True, sources=sources, rationale=f"新しい振る舞い {name}: {out.get('rationale', '')}",
                    restart=True, edits=edits)

    def _sandbox_test(self, sources):
        """変更を当てた写しで、全テスト (プラグインの動作確認を含む) を隔離環境で走らせる。"""
        self.stage = "サンドボックスでテスト (CPU・RAM 上限つき)"
        work = os.path.join(self.dir, "work")
        if os.path.exists(work):
            shutil.rmtree(work)
        shutil.copytree(self.root, work, ignore=lambda d, names: [n for n in names if n in EXCLUDE
                                                                   or n.endswith((".db", ".pyc"))])
        for rp, text in sources.items():
            fp = os.path.join(work, rp)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
        ok, output = self.sandbox.run_tests(work, timeout=self.cfg["sandbox_timeout_s"])
        shutil.rmtree(work, ignore_errors=True)
        return ok, output

    def _mutate_code(self, goal, target=None):
        path, func, src = self._code_target(goal, target)
        if path is None:
            return {"ok": False, "note": "改良する対象が見つからない", "goal": goal}
        if goal == "robustness":
            task = (f"# 目標\n実運用で次の例外が起きた。原因を直す。\n{target['message']}\n\n"
                    f"# トレースバック (末尾)\n{target['trace'][-1500:]}")
        elif goal == "speed":
            task = (f"# 目標\nこの関数は思考ループ 1 回で {target['seconds']:.3f} 秒使っている。"
                    "入出力と副作用を一切変えずに速くする。")
        else:
            task = (f"# 目標\n{GOALS[goal]['label']}を上げる (指標: {GOALS[goal]['metric']})"
                    + (f"\n{GOALS[goal]['pressure']}" if GOALS[goal].get("pressure") else ""))
        ctx = f"{task}\n\n# 対象ファイル: {path}\n# 対象の関数\n{func}"
        try:
            self.stage = "アイデア出し (CPU)"
            self.brain.use("code")
            ideas = self.brain.chat(IDEAS_SYSTEM, ctx, schema=IDEAS_SCHEMA, max_tokens=700, temperature=0.9).get("ideas") or []
            if not ideas:
                return {"ok": False, "note": "アイデアが出なかった", "goal": goal}
            self.stage = "新しさの評価 (論文・ネット検索)"
            nov, idea, sc = self._pick_idea(ideas)
            self.stage = "実装 (CPU)"
            out = self.brain.chat(CODER_SYSTEM, f"{ctx}\n\n# 採用したやり方\n{idea['name']}: {idea['description']}",
                                  schema=PATCH_SCHEMA, max_tokens=1500, temperature=0.2)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"コード生成失敗: {e}", "goal": goal}
        base = {"goal": goal, "novelty": nov, "approach": dict(idea, novelty=sc)}
        edits = out.get("edits") or []
        if not edits:
            return dict(base, ok=False, note="『自信なし』と判断 (変更なし)")
        errors, sources = check_edits(edits, self._read)
        if errors:
            return dict(base, ok=False, note="静的検査で不合格: " + "; ".join(errors[:5]), edits=edits)
        ok, output = self._sandbox_test(sources)
        if not ok:
            return dict(base, ok=False, note="テスト不合格:\n" + output[-1200:], edits=edits)
        return dict(base, ok=True, sources=sources, rationale=f"{idea['name']}: {out.get('rationale', '')}",
                    restart=True, edits=edits, target=target)

    # ------------------------------------------------------------ 本番投入
    def _deploy(self, eid, sources):
        hist = os.path.join(self.dir, "history", str(eid))
        diffs = []
        for path, text in sources.items():
            live = os.path.join(self.root, path)
            before = self._read(path)
            os.makedirs(os.path.join(hist, "before", os.path.dirname(path)), exist_ok=True)
            if before is None:
                open(os.path.join(hist, "before", path + ".__new__"), "w").close()
            else:
                shutil.copy2(live, os.path.join(hist, "before", path))
            os.makedirs(os.path.dirname(live), exist_ok=True)
            with open(live, "w", encoding="utf-8") as f:
                f.write(text)
            diffs.append("".join(difflib.unified_diff((before or "").splitlines(True), text.splitlines(True),
                                                      f"a/{path}", f"b/{path}")))
        return "\n".join(diffs)

    def _finish(self, eid, r):
        now = self.clock()
        approach = json.dumps(r.get("approach"), ensure_ascii=False) if r.get("approach") else None
        if not r.get("ok"):
            self.db.execute("UPDATE evolutions SET status='rejected', decided=?, note=?, edits=?, novelty=?, approach=?"
                            " WHERE id=?", (now, r.get("note", ""), json.dumps(r.get("edits"), ensure_ascii=False),
                                           r.get("novelty"), approach, eid))
            self.db.commit()
            if r.get("goal"):
                self.reward(r["goal"], "failure", r.get("novelty"))
            return f"自己改良 #{eid} は不採用: {r.get('note', '')[:200]}"
        diff = self._deploy(eid, r["sources"])
        self.db.execute("UPDATE evolutions SET status='canary', deployed=?, rationale=?, diff=?, edits=?, novelty=?,"
                        " approach=? WHERE id=?",
                        (now, r.get("rationale", ""), diff, json.dumps(r.get("edits"), ensure_ascii=False),
                         r.get("novelty"), approach, eid))
        self.db.commit()
        hot = r.get("hot") or {}
        for k, v in hot.get("params", {}).items():
            self.cfg[k] = v
        if "prompt" in hot:
            from .. import prompts
            setattr(prompts, hot["prompt"][0], hot["prompt"][1])
        if r.get("goal") == "robustness" and r.get("target"):
            t = r["target"]
            self.metrics.mark_errors_handled_like(t["file"].split("/")[-1], t["line"])
        if r.get("restart"):
            self.restart_requested = True
        return f"自己改良 #{eid} を試用中: {r.get('rationale', '')[:200]}"

    def poll(self):
        """メインスレッドで毎 tick 呼ぶ。完了した変異の反映と、試用期間の判定。"""
        msgs = []
        try:
            eid, r = self._results.get_nowait()
            self.busy, self.stage = False, ""
            msgs.append(self._finish(eid, r))
        except queue.Empty:
            pass
        c = self.canary()
        if c is not None:
            now = self.clock()
            span = self.cfg["canary_hours"] * 3600
            if now - c["deployed"] >= span:
                before = window_stats(self.metrics, self.db, c["deployed"] - span, c["deployed"])
                after = window_stats(self.metrics, self.db, c["deployed"], now)
                verdict, why = compare(before, after, self.cfg)
                goal = c["goal"] or "knowledge"
                if verdict == "keep":
                    verdict, why = self._target_verdict(c, goal, before, after)
                if verdict == "keep":
                    self.db.execute("UPDATE evolutions SET status='kept', decided=?, note=? WHERE id=?", (now, why, c["id"]))
                    self.db.commit()
                    self.reward(goal, "success", c["novelty"])
                    msgs.append(self._celebrate(c, why))
                elif verdict == "revert":
                    self.reward(goal, "failure", c["novelty"])
                    msgs.append(self.revert(c["id"], why))
                elif verdict == "neutral" or now - c["deployed"] > span * 3:
                    self.reward(goal, "neutral", c["novelty"])
                    msgs.append(self.revert(c["id"], why if verdict == "neutral" else "試用期間に十分なデータが集まらなかった"))
        return [m for m in msgs if m]

    def _target_verdict(self, c, goal, before, after):
        """害が無いことに加えて、狙った指標が実際に良くなったか。"""
        if goal == "robustness":
            t = json.loads(c["target"]) if c["target"] else None
            if t:
                n = self.db.execute("SELECT COUNT(*) FROM errors WHERE (file LIKE ? OR file LIKE ?) AND line=? AND ts>=?",
                                    ("%/" + t["file"].split("/")[-1], "%\\" + t["file"].split("/")[-1], t["line"],
                                     c["deployed"])).fetchone()[0]
                return ("keep", "直した例外が再発していない") if n == 0 else ("revert", "同じ例外が再発した")
        ok = improved(GOALS[goal]["metric"], before, after, self.cfg["evolution_min_gain"])
        if ok:
            return "keep", f"{GOALS[goal]['label']}の指標 {GOALS[goal]['metric']} が改善した"
        if ok is None:
            return "neutral", f"{GOALS[goal]['metric']} を測れず、効果を確認できない"
        return "neutral", f"{GOALS[goal]['metric']} が改善しなかった (害は無いが効果も無いので戻す)"

    def _celebrate(self, c, why):
        msg = f"自己改良 #{c['id']} が定着: {why}"
        try:
            ap = json.loads(c["approach"]) if c["approach"] else {}
        except ValueError:
            ap = {}
        web = (ap.get("novelty") or {}).get("web")
        if web is not None and web >= self.cfg["discovery_web_novelty"]:
            found = self._kv("discoveries") or []
            found.append({"id": c["id"], "ts": self.clock(), "name": ap.get("name"), "description": ap.get("description"),
                          "goal": c["goal"], "why": why})
            self._kv("discoveries", found[-100:])
            msg += (f" ／ ねえねえ、これ、論文にもネットにも見当たらなかったやり方「{ap.get('name')}」なんだよ！"
                    "(見つからなかっただけで、本当に新しいかはまだわからないけどね)")
        return msg

    def revert(self, eid, why="手動"):
        row = self.db.execute("SELECT * FROM evolutions WHERE id=?", (eid,)).fetchone()
        if row is None or row["status"] not in ("canary", "kept"):
            return f"#{eid} は戻せる状態ではない"
        hist = os.path.join(self.dir, "history", str(eid), "before")
        for dirpath, _, files in os.walk(hist):
            for fn in files:
                src = os.path.join(dirpath, fn)
                path = os.path.relpath(src, hist)
                if fn.endswith(".__new__"):
                    live = os.path.join(self.root, path[: -len(".__new__")])
                    if os.path.exists(live):
                        os.remove(live)
                else:
                    shutil.copy2(src, os.path.join(self.root, path))
        self.db.execute("UPDATE evolutions SET status='reverted', decided=?, note=? WHERE id=?",
                        (self.clock(), why, eid))
        self.db.commit()
        if row["level"] == "param":
            apply_params(self.cfg, self.root)
        else:
            self.restart_requested = True
        return f"自己改良 #{eid} を元に戻した: {why}"

    # ------------------------------------------------------------ カーネルへの提案
    def propose_kernel(self, rationale, edits):
        cur = self.db.execute("INSERT INTO evolutions(ts, level, target, rationale, status, edits) VALUES (?,?,?,?,?,?)",
                              (self.clock(), "kernel", "", rationale, "pending_approval",
                               json.dumps(edits, ensure_ascii=False)))
        self.db.commit()
        return cur.lastrowid

    def approve(self, eid):
        """人間が承認したカーネル変更を適用する (構文検査とテストは通す)。"""
        row = self.db.execute("SELECT * FROM evolutions WHERE id=? AND status='pending_approval'", (eid,)).fetchone()
        if row is None:
            return f"#{eid} は承認待ちではない"
        sources = {}
        for e in json.loads(row["edits"] or "[]"):
            src = sources.get(e["file"]) or self._read(e["file"]) or ""
            if e["search"] and src.count(e["search"]) != 1:
                return f"#{eid}: {e['file']} に置換元が一意に見つからない"
            sources[e["file"]] = src.replace(e["search"], e["replace"]) if e["search"] else e["replace"]
        for path, text in sources.items():
            if path.endswith(".py"):
                try:
                    ast.parse(text)
                except SyntaxError as err:
                    return f"#{eid}: 構文エラー {err}"
        self.db.execute("UPDATE evolutions SET level='kernel-approved' WHERE id=?", (eid,))
        return self._finish(eid, {"ok": True, "sources": sources, "rationale": row["rationale"], "restart": True})


def emergency_revert(db, cfg, root=ROOT):
    """supervisor 用: 起動直後に落ち続けるとき、最新の未定着の変更を戻す。"""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT id FROM evolutions WHERE status IN ('canary','kept') AND level!='param'"
                       " ORDER BY deployed DESC LIMIT 1").fetchone()
    if row is None:
        return None
    from ..memory import Memory
    mem = Memory.__new__(Memory)
    mem.db, mem.clock = conn, time.time
    from .metrics import Metrics
    ev = Evolution(cfg, mem, None, None, Metrics(conn), root=root)   # 撤回だけなので CPU の脳もサンドボックスも不要
    return ev.revert(row["id"], "起動直後に落ち続けたため自動で撤回")
