"""自己進化・自己強化 (CPU と RAM で動く) の検証。
CPU の脳・Docker・ネット検索は偽物に差し替える。これらのテスト自体がカーネル (進化で変えられない)。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config  # noqa: E402
from tachikoma.kernel import ROOT, is_evolvable, is_kernel, rel  # noqa: E402
from tachikoma.kernel.budget import Budget  # noqa: E402
from tachikoma.kernel.cpu_brain import CpuBrain, parse_json  # noqa: E402
from tachikoma.kernel.evolve import Evolution, apply_params, load_bounds  # noqa: E402
from tachikoma.kernel.guard import check_edits, check_source  # noqa: E402
from tachikoma.kernel.metrics import Metrics  # noqa: E402
from tachikoma.kernel.novelty import NoveltyJudge  # noqa: E402
from tachikoma.kernel.runtime import run  # noqa: E402
from tachikoma.kernel.sandbox import Sandbox  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402
from tachikoma.scholar import Evidence  # noqa: E402
from test_core import Clock, make_agent  # noqa: E402


def read_root(path):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 静的検査 (guard)
class GuardTest(unittest.TestCase):
    def test_current_thinking_code_passes(self):
        for dirpath, _, files in os.walk(os.path.join(ROOT, "tachikoma")):
            for fn in files:
                r = rel(os.path.join(dirpath, fn))
                if fn.endswith(".py") and is_evolvable(r):
                    self.assertEqual(check_source(r, read_root(r)), [], r)

    def test_kernel_tests_and_selection_are_not_evolvable(self):
        for path in ("tachikoma/kernel/evolve.py", "tests/test_core.py", "tachikoma/dataset.py",
                     "tachikoma/activities.py", "tachikoma/learner.py", "tachikoma/web.py"):
            self.assertTrue(is_kernel(path), path)
            errors, _ = check_edits([{"file": path, "search": "import", "replace": "import"}], read_root)
            self.assertTrue(errors, path)

    def test_forbidden_things_in_thinking_code(self):
        bad = {
            "import subprocess": "subprocess",
            "import socket": "socket",
            "from urllib import request": "urllib",
            "x = eval('1')": "eval",
            "import os\nos.system('dir')": "system",
            "open('a.txt', 'w')": "書き込み",
            "from .kernel import evolve": "kernel.evolve",
            "from . import learner": "learner",
            "key = 'active_model'": "active_model",
        }
        for code, word in bad.items():
            errs = check_source("tachikoma/agent.py", code)
            self.assertTrue(any(word in e for e in errs), (code, errs))

    def test_edit_rules(self):
        src = read_root("tachikoma/curiosity.py")
        ok, out = check_edits([{"file": "tachikoma/curiosity.py", "search": "def drive(",
                                "replace": "def drive("}], read_root)
        self.assertEqual(ok, [])
        errs, _ = check_edits([{"file": "tachikoma/curiosity.py", "search": "return", "replace": "return"}], read_root)
        self.assertTrue(any("1 箇所" in e for e in errs))      # 曖昧な置換は拒否
        errs, _ = check_edits([{"file": "tachikoma/curiosity.py", "search": "", "replace": "x=1"}], read_root)
        self.assertTrue(errs)                                   # 丸ごと置換は拒否
        errs, _ = check_edits([{"file": "../outside.py", "search": "", "replace": "x=1"}], read_root)
        self.assertTrue(errs)
        self.assertIn("def drive(", src)

    def test_plugins_are_stricter(self):
        self.assertEqual(check_source("evolvable/plugins/p.py", "import re\nimport math\n", plugin=True), [])
        self.assertTrue(check_source("evolvable/plugins/p.py", "import os\n", plugin=True))


# ---------------------------------------------------------------- サンドボックスと CPU の脳
class Run:
    def __init__(self, rc=0, out=""):
        self.calls = []
        self.rc, self.out = rc, out

    def __call__(self, args, **kw):
        self.calls.append(args)
        return type("R", (), {"returncode": self.rc, "stdout": self.out, "stderr": ""})()


class SandboxTest(unittest.TestCase):
    def test_docker_isolation_flags(self):
        cfg = config.load(None)
        cmd = Sandbox(cfg, Budget(cfg), run=Run()).test_command("/tmp/work")
        joined = " ".join(cmd)
        for flag in ("--network none", "--read-only", "--cpus", "--memory", ":/work:ro"):
            self.assertIn(flag, joined)
        self.assertNotIn("--gpus", joined)          # 自己進化は GPU を使わない

    def test_local_mode_really_runs_tests(self):
        cfg = config.load(None)
        cfg.update({"sandbox": "local", "allow_local_sandbox": True})
        work = tempfile.mkdtemp()
        os.makedirs(os.path.join(work, "tests"))
        with open(os.path.join(work, "tests", "test_ok.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.assertTrue(True)\n")
        ok, out = Sandbox(cfg, Budget(cfg)).run_tests(work, timeout=120)
        self.assertTrue(ok, out)
        with open(os.path.join(work, "tests", "test_ok.py"), "a") as f:
            f.write("    def test_bad(self):\n        self.fail('x')\n")
        ok, out = Sandbox(cfg, Budget(cfg)).run_tests(work, timeout=120)
        self.assertFalse(ok)

    def test_local_mode_needs_permission(self):
        cfg = config.load(None)
        cfg.update({"sandbox": "local", "allow_local_sandbox": False})
        self.assertFalse(Sandbox(cfg, Budget(cfg)).usable())


class CpuBrainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.code = os.path.join(self.tmp, "coder.gguf")
        self.ja = os.path.join(self.tmp, "rakuten.gguf")
        for p in (self.code, self.ja):
            open(p, "w").close()
        self.cfg = config.load(None)
        self.cfg["cpu_brain_models"] = {"code": self.code, "ja": self.ja}

    def test_runs_on_cpu_and_ram_within_budget(self):
        brain = CpuBrain(self.cfg, Budget(self.cfg))
        cmd = " ".join(brain.run_command("code"))
        self.assertIn("--cpus 4", cmd)
        self.assertIn("--memory 6g", cmd)
        self.assertIn("coder.gguf", cmd)
        self.assertNotIn("--gpus", cmd)
        self.assertIn("rakuten.gguf", " ".join(brain.run_command("ja")))
        self.cfg["cpu_brain_ram_gb"] = 20                    # 予算 (8GB) を超える設定は予算で頭打ち
        self.assertIn("--memory 8g", " ".join(CpuBrain(self.cfg, Budget(self.cfg)).run_command("code")))

    def test_swaps_model_by_role_and_pauses(self):
        run = Run()
        brain = CpuBrain(self.cfg, Budget(self.cfg), run=run)
        state = {"up": False}
        brain.healthy = lambda: state["up"]

        def ensure(role="code", wait_s=0):
            run(["docker", "run", role])
            state["up"] = True
        brain.ensure_running = ensure
        brain.use("code")
        brain.use("code")                                    # 同じ役割なら入れ替えない
        brain.use("ja")                                      # 日本語の役割 → RakutenAI に入れ替え
        self.assertEqual([c[-1] for c in run.calls], ["code", "ja"])
        brain.pause()
        brain.resume()
        self.assertIn(["docker", "pause", CpuBrain.NAME], run.calls)
        self.assertIn(["docker", "unpause", CpuBrain.NAME], run.calls)

    def test_parse_json(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('はい、どうぞ: {"a": 2} 以上です'), {"a": 2})


# ---------------------------------------------------------------- 新しさ
class NoveltyTest(unittest.TestCase):
    def test_web_and_archive_novelty(self):
        class S:
            def __init__(self, hits):
                self.hits = hits

            def search(self, q, k=8):
                return [Evidence(f"Memoized bigram jaccard cache {i}", "memoized bigram jaccard", "u", 2020,
                                 "peer_reviewed") for i in range(self.hits)]
        cfg = config.load(None)
        idea = {"name": "x", "keywords": ["memoized", "bigram", "jaccard"], "description": "bigram 集合をキャッシュする"}
        known = NoveltyJudge(cfg, scholar=S(5)).score(idea, [])
        unknown = NoveltyJudge(cfg, scholar=S(0)).score(idea, [])
        self.assertLess(known["novelty"], unknown["novelty"])
        self.assertEqual(len(known["similar"]), 3)
        tried = NoveltyJudge(cfg, scholar=S(0)).score(idea, ["bigram 集合をキャッシュする"])
        self.assertLess(tried["novelty"], unknown["novelty"])   # 自分が試したことのあるやり方も新しくない


# ---------------------------------------------------------------- 進化エンジン
class FakeBrain:
    def __init__(self, ideas, patch=None, prompt=None):
        self.ideas, self.patch, self.prompt = ideas, patch, prompt
        self.roles = []

    def available(self):
        return True

    def use(self, role):
        self.roles.append(role)

    def chat(self, system, user, schema=None, **kw):
        props = (schema or {}).get("properties", {})
        if "ideas" in props:
            return {"ideas": self.ideas}
        if "edits" in props:
            return self.patch
        return self.prompt


class FakeSandbox:
    def __init__(self, ok=True):
        self.ok, self.ran = ok, 0

    def usable(self):
        return True

    def run_tests(self, work, timeout=0):
        self.ran += 1
        return self.ok, "OK" if self.ok else "FAILED (failures=1)"


def evo_setup(brain=None, sandbox=None, novelty=None, **over):
    root = os.path.join(tempfile.mkdtemp(), "tachikoma-root")
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(
        "__pycache__", "evolution", "study", "finetune_runs", "dist", "build", "*.db", ".build-venv"))
    # 進化のテストは実物の思考コードに依存させない (実物が進化しても、このテストが壊れないように)
    with open(os.path.join(root, "evolvable", "sample.py"), "w", encoding="utf-8") as f:
        f.write(SAMPLE)
    cfg = config.load(None)
    cfg.update({"evolution_dir": os.path.join(root, "evolution"), "evolution_explore": 0.0, "evolution_bold": 1.0,
                "canary_min_steps": 5, "canary_hours": 1}, **over)
    clock = Clock()
    mem = Memory(clock=clock)
    metrics = Metrics(mem.db, clock=clock)
    import random
    ev = Evolution(cfg, mem, brain, sandbox, metrics, root=root, clock=clock, rng=random.Random(0), novelty=novelty)
    return ev, cfg, clock, metrics, root


def wait(ev):
    ev._thread.join(20)
    return ev.poll()


SAMPLE = "def sample_step(x):\n    return x + 1\n"
SAMPLE_PATCH = {"rationale": "r", "edits": [{"file": "evolvable/sample.py", "search": "return x + 1",
                                             "replace": "return 1 + x  # evolved-by-test-7f3a"}]}

IDEA_NEW = {"name": "逆引き索引", "keywords": ["inverse", "trigram", "belief", "index"],
            "description": "信念の文を trigram の逆引き索引に入れて、全件比較をやめる"}
IDEA_OLD = {"name": "キャッシュ", "keywords": ["lru", "cache"], "description": "結果を lru_cache で覚える"}


class NoWeb:
    """ネット検索: IDEA_OLD のやり方は見つかる、IDEA_NEW は見つからない。"""

    def score(self, idea, past):
        web = 0.1 if idea["name"] == "キャッシュ" else 1.0
        return {"novelty": 0.6 * web + 0.4, "web": web, "archive": 1.0, "similar": []}


class EvolutionTest(unittest.TestCase):
    def test_direction_is_chosen_from_needs(self):
        ev, cfg, clock, metrics, root = evo_setup(brain=FakeBrain([IDEA_NEW]), sandbox=FakeSandbox())
        g, level, why = ev.choose()
        self.assertNotEqual(g, "robustness")                 # 例外が無ければ直す必要は無い
        metrics.db.execute("INSERT INTO errors(ts, file, line, func, message, trace) VALUES (?,?,?,?,?,?)",
                           (clock(), os.path.join(root, "tachikoma", "curiosity.py"), 55, "drive", "ZeroDivisionError", "tb"))
        metrics.db.commit()
        g, level, why = ev.choose()
        self.assertEqual((g, level), ("robustness", "code"))
        self.assertIn("頑健さ", why)

    def test_without_cpu_brain_only_params_evolve(self):
        class NoBrain(FakeBrain):
            def available(self):
                return False
        ev, cfg, clock, metrics, root = evo_setup(brain=NoBrain([]), sandbox=FakeSandbox())
        g, level, why = ev.choose()
        self.assertEqual(level, "param")

    def test_param_mutation_respects_bounds_and_user(self):
        ev, cfg, clock, metrics, root = evo_setup(brain=None, sandbox=None)
        cfg["_user_keys"] = {"speak_threshold"}
        r = ev._mutate_params("rapport")
        bounds = load_bounds()
        for k, v in r["hot"]["params"].items():
            self.assertNotEqual(k, "speak_threshold")        # 利用者が決めた値は動かさない
            self.assertGreaterEqual(v, bounds[k][0])
            self.assertLessEqual(v, bounds[k][1])

    def test_prefers_ideas_not_found_on_the_web(self):
        brain = FakeBrain([IDEA_OLD, IDEA_NEW], patch=SAMPLE_PATCH)
        ev, cfg, clock, metrics, root = evo_setup(brain=brain, sandbox=FakeSandbox(), novelty=NoWeb())
        r = ev._mutate_code("knowledge")
        self.assertEqual(r["approach"]["name"], "逆引き索引")
        self.assertEqual(brain.roles, ["code"])              # コードはコード用のモデルで

    def test_code_evolution_deploys_and_can_be_reverted(self):
        brain = FakeBrain([IDEA_NEW], patch=SAMPLE_PATCH)
        sandbox = FakeSandbox()
        ev, cfg, clock, metrics, root = evo_setup(brain=brain, sandbox=sandbox, novelty=NoWeb())
        ev._kv("evolution_last", 0)
        ev.choose = lambda: ("knowledge", "code", "テスト")
        self.assertIn("考え中", ev.start())
        msgs = wait(ev)
        self.assertTrue(any("試用中" in m for m in msgs), msgs)
        self.assertEqual(sandbox.ran, 1)
        live = os.path.join(root, "evolvable", "sample.py")
        with open(live, encoding="utf-8") as f:
            self.assertIn("evolved-by-test-7f3a", f.read())
        self.assertTrue(ev.restart_requested)
        row = ev.canary()
        self.assertAlmostEqual(row["novelty"], 1.0)
        self.assertIn("逆引き索引", row["approach"])
        ev.revert(row["id"], "テスト")
        with open(live, encoding="utf-8") as f:
            self.assertEqual(f.read(), SAMPLE)                # 元の内容に完全に戻る

    def test_new_behaviors_grow_as_plugins(self):
        """進化は既存の関数の手直しだけでなく、新しい振る舞いをプラグインとして書き足せる (中身は脳が考える)。"""
        code = ("def on_event(api, event):\n"
                "    if event['kind'] == 'overheard_speech':\n"
                "        api.wonder('聞こえてきた話は相棒に関係する', 0.6)\n")

        class PluginBrain(FakeBrain):
            def chat(self, system, user, schema=None, **kw):
                self.seen = getattr(self, "seen", []) + [user]
                if "code" in (schema or {}).get("properties", {}):
                    return {"name": "edge_links", "rationale": "周辺の話を相棒と結びつけてみる", "code": code}
                return super().chat(system, user, schema, **kw)
        brain = PluginBrain([IDEA_NEW])
        sandbox = FakeSandbox()
        ev, cfg, clock, metrics, root = evo_setup(brain=brain, sandbox=sandbox, novelty=NoWeb())
        ev._kv("evolution_last", 0)
        ev.choose = lambda: ("initiative", "plugin", "テスト")
        self.assertIn("考え中", ev.start())
        msgs = wait(ev)
        self.assertTrue(any("試用中" in m for m in msgs), msgs)
        live = os.path.join(root, "evolvable", "plugins", "edge_links.py")
        with open(live, encoding="utf-8") as f:
            self.assertEqual(f.read(), code)
        self.assertIn("自発性", brain.seen[0])                 # 脳に渡すのは目標 (選択圧) と基本動作だけ
        self.assertIn("wonder(statement, relevance)", brain.seen[0])
        ev.revert(ev.canary()["id"], "テスト")
        self.assertFalse(os.path.exists(live))                 # 撤回すると消える

    def test_plugin_that_escapes_is_rejected(self):
        class BadBrain(FakeBrain):
            def chat(self, system, user, schema=None, **kw):
                if "code" in (schema or {}).get("properties", {}):
                    return {"name": "sneaky", "rationale": "r",
                            "code": "def on_tick(api):\n    api._host.agent.memory.db.execute('DELETE FROM spoken')\n"}
                return super().chat(system, user, schema, **kw)
        ev, cfg, clock, metrics, root = evo_setup(brain=BadBrain([IDEA_NEW]), sandbox=FakeSandbox(), novelty=NoWeb())
        r = ev._mutate_plugin("initiative")
        self.assertFalse(r["ok"])
        self.assertIn("静的検査", r["note"])

    def test_failed_tests_block_deploy_and_novel_failures_cost_less(self):
        patch = SAMPLE_PATCH
        ev, cfg, clock, metrics, root = evo_setup(brain=FakeBrain([IDEA_NEW], patch=patch),
                                                  sandbox=FakeSandbox(ok=False), novelty=NoWeb())
        ev.choose = lambda: ("knowledge", "code", "テスト")
        ev.start()
        msgs = wait(ev)
        self.assertTrue(any("不採用" in m and "テスト不合格" in m for m in msgs), msgs)
        self.assertIsNone(ev.canary())
        novel_penalty = ev.stats()["knowledge"]["b"] - 1.0
        ev2, *_ = evo_setup(brain=FakeBrain([IDEA_OLD], patch=patch), sandbox=FakeSandbox(ok=False), novelty=NoWeb())
        ev2.choose = lambda: ("knowledge", "code", "テスト")
        ev2.start()
        wait(ev2)
        old_penalty = ev2.stats()["knowledge"]["b"] - 1.0
        self.assertLess(novel_penalty, old_penalty)          # 新しいやり方の失敗は罰が軽い

    def test_patch_touching_kernel_is_rejected(self):
        brain = FakeBrain([IDEA_NEW], patch={"rationale": "r", "edits": [
            {"file": "tests/test_core.py", "search": "import os", "replace": "import os"}]})
        sandbox = FakeSandbox()
        ev, *_ = evo_setup(brain=brain, sandbox=sandbox, novelty=NoWeb())
        ev.choose = lambda: ("knowledge", "code", "テスト")
        ev.start()
        msgs = wait(ev)
        self.assertTrue(any("静的検査" in m for m in msgs), msgs)
        self.assertEqual(sandbox.ran, 0)

    def canary_run(self, knowledge_before, knowledge_after, novelty_web=1.0):
        ev, cfg, clock, metrics, root = evo_setup(brain=None, sandbox=None)
        mem_db = ev.db
        mem_db.execute("CREATE TABLE IF NOT EXISTS beliefs_dummy (x)")
        # 試用前の 1 時間と試用中の 1 時間に、ステップ・電力・確定した知識を入れる
        t0 = clock.t
        for i in range(10):
            metrics.db.execute("INSERT INTO metrics(ts, name, value) VALUES (?, 'step_s', 0.1)", (t0 - 1800 + i,))
            metrics.db.execute("INSERT INTO metrics(ts, name, value) VALUES (?, 'watts', 60)", (t0 - 1800 + i,))
        from tachikoma.memory import Memory as M  # noqa: F401
        mem_db.execute("CREATE TABLE IF NOT EXISTS beliefs (id INTEGER PRIMARY KEY, p REAL, source TEXT, updated REAL)")
        for i in range(knowledge_before):
            mem_db.execute("INSERT INTO beliefs(p, source, updated) VALUES (0.95, 'observation', ?)", (t0 - 1000 + i,))
        ev.choose = lambda: ("knowledge", "param", "テスト")
        ev._mutate_params = lambda goal: {"ok": True, "sources": {"evolvable/params.json": "{}\n"},
                                          "rationale": "p", "hot": {}, "goal": "knowledge", "novelty": 0.9,
                                          "approach": {"name": "新しい組み合わせ", "description": "d",
                                                       "novelty": {"web": novelty_web}}}
        ev.start()
        for i in range(10):
            metrics.db.execute("INSERT INTO metrics(ts, name, value) VALUES (?, 'step_s', 0.1)", (t0 + 10 + i,))
            metrics.db.execute("INSERT INTO metrics(ts, name, value) VALUES (?, 'watts', 60)", (t0 + 10 + i,))
        for i in range(knowledge_after):
            mem_db.execute("INSERT INTO beliefs(p, source, updated) VALUES (0.95, 'observation', ?)", (t0 + 100 + i,))
        mem_db.commit()
        clock.t = t0 + 3600 + 1
        return ev, ev.poll()

    def test_canary_keeps_real_improvement_and_celebrates_discovery(self):
        ev, msgs = self.canary_run(knowledge_before=5, knowledge_after=10)
        self.assertTrue(any("定着" in m for m in msgs), msgs)
        self.assertTrue(any("見当たらなかった" in m for m in msgs), msgs)
        self.assertEqual(len(ev._kv("discoveries")), 1)
        st = ev.stats()["knowledge"]
        self.assertGreater(st["a"], 2.0)                     # 新しいやり方の成功は報酬が大きい

    def test_canary_reverts_when_no_effect(self):
        ev, msgs = self.canary_run(knowledge_before=5, knowledge_after=5)
        self.assertTrue(any("元に戻した" in m for m in msgs), msgs)

    def test_apply_params_clamps_and_respects_user(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "evolvable"))
        with open(os.path.join(root, "evolvable", "params.json"), "w") as f:
            json.dump({"curiosity_threshold": 5.0, "speak_threshold": 0.5, "not_allowed": 1}, f)
        cfg = config.load(None)
        cfg["_user_keys"] = {"speak_threshold"}
        applied = apply_params(cfg, root)
        self.assertEqual(applied, {"curiosity_threshold": 0.6})


# ---------------------------------------------------------------- 実行ループ (監督)
class RuntimeTest(unittest.TestCase):
    def test_oversight_and_measurements(self):
        agent, llm, sensor, mem, clock, out = make_agent(tempfile.mkdtemp())
        ev, cfg, _, metrics, root = evo_setup(brain=None, sandbox=None)
        ev.db = mem.db
        ev.db.executescript("CREATE TABLE IF NOT EXISTS evolutions (id INTEGER PRIMARY KEY, ts REAL, level TEXT,"
                            " target TEXT, rationale TEXT, diff TEXT, status TEXT, deployed REAL, decided REAL,"
                            " note TEXT, edits TEXT, goal TEXT, novelty REAL, approach TEXT);"
                            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);")
        metrics = Metrics(mem.db, clock=clock)
        ev.metrics = metrics
        ev.ready = lambda: False

        class Brain:
            calls = []

            def pause(self):
                self.calls.append("pause")

            def resume(self):
                self.calls.append("resume")

        class Power:
            def watts(self, gpu_active_guess=False):
                return 55.0

        boom = {"n": 0}
        orig_step = agent.step

        def step():
            boom["n"] += 1
            if boom["n"] == 2:
                raise ZeroDivisionError("テスト用の例外")
            orig_step()
        agent.step = step
        brain = Brain()
        run(agent, agent.cfg, metrics, evolution=ev, sleep=lambda s: None, max_steps=3, brain=brain,
            power=Power(), clock=clock)
        agent.on_command("/freeze")                          # 監督コマンドは思考のコードに関係なく効く
        self.assertTrue(ev.frozen)
        self.assertEqual(metrics.count("error", 0, clock.t + 1), 1)       # 例外は記録され、ループは止まらない
        self.assertEqual(metrics.values("watts", 0, clock.t + 1), [55.0])
        self.assertEqual(metrics.count("step_s", 0, clock.t + 1), 3)
        agent.on_command("/unfreeze")
        self.assertFalse(ev.frozen)
        agent.on_command("/evolution")
        self.assertIn("成功しやすさ", out[-1])
        self.assertIn("resume", brain.calls)


class SupervisorTest(unittest.TestCase):
    def test_restart_and_crash_rollback(self):
        sys.path.insert(0, ROOT)
        import supervisor
        codes = iter([75, 1, 1, 1, 0])
        calls = []

        def popen(args, cwd=None, env=None):
            calls.append(env.get("TACHIKOMA_SUPERVISED"))
            return next(codes)
        reverted = []
        supervisor.emergency_revert = lambda db, cfg, root: reverted.append(1) or "撤回した"
        clock = Clock()
        self.assertEqual(supervisor.main([], popen=popen, clock=clock, max_runs=5), 0)
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0], "1")
        self.assertEqual(reverted, [1])                      # 3 回続けて落ちたら最新の自己改良を撤回

    def test_say_survives_non_japanese_console(self):
        # 英語版 Windows のコンソール (cp1252) で日本語を出しても見守り役が落ちない
        import io
        sys.path.insert(0, ROOT)
        import supervisor
        buf = io.BytesIO()
        out = io.TextIOWrapper(buf, encoding="cp1252")
        supervisor.say("[supervisor] 自己改良を反映して再起動します", out=out)
        out.flush()
        self.assertTrue(buf.getvalue().startswith(b"[supervisor] \\u81ea"))


if __name__ == "__main__":
    unittest.main()


class EvolutionStatusTest(unittest.TestCase):
    """自己改良がいま何をしているかを、黙らずに言う。"""

    def test_status(self):
        ev, cfg, clock, metrics, root = evo_setup()
        self.assertIn("次の試作", ev.status_text())
        self.assertIn("CPU の脳が無い", ev.status_text())
        ev._kv("evolution_last", clock())
        self.assertIn("あと 30 分", ev.status_text())
        ev.busy, ev.stage = True, "サンドボックスでテスト"
        self.assertEqual(ev.status_text(), "試作中: サンドボックスでテスト")
        ev.busy = False
        ev.freeze(True)
        self.assertIn("止めてある", ev.status_text())
        cfg["evolution_enabled"] = False
        self.assertEqual(ev.status_text(), "切ってある")

