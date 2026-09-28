"""自己進化が書き足したプラグイン (evolvable/plugins/) の動作確認。隔離環境の全テストに含まれるので、
新しいプラグインは、ここで例外・止まり・禁止事項が無いことを確かめられてから本番に入る。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma.kernel import ROOT  # noqa: E402
from tachikoma.kernel.guard import check_source  # noqa: E402
from tachikoma.kernel.metrics import Metrics  # noqa: E402
from tachikoma.kernel.plugins import PLUGIN_DIR, PluginHost  # noqa: E402
from test_core import FakeLLM, make_agent  # noqa: E402

EVENTS = [
    ("voice", "overheard_speech", "テレビ: 北海道で大規模な停電が発生しました"),
    ("news", "news", "新しい彗星が肉眼で見えるかもしれない — 国立天文台"),
    ("screen", "background_window", "背後のウィンドウ: YouTube - 深海魚の生態"),
    ("camera", "glance", "机の奥のテレビに天気予報が映っている"),
    ("user", "user_message", "今日は何してたの？"),
    ("files", "file_changed", "app.py を編集\n+ print('hello')"),
    ("window", "window_focus", "前面ウィンドウ: Visual Studio Code"),
    ("voice", "overheard_speech", ""),
]


class AnyLLM(FakeLLM):
    """どんな問い合わせにも、形だけは正しい応答を返す。"""

    def chat(self, system, user, schema=None, **kw):
        self.calls.append((system, user))
        props = (schema or {}).get("properties", {})
        if "claims" in props:
            return {"situation": "", "claims": [], "remark": "", "remark_importance": "none", "question": ""}
        if "probe" in props:
            return {"probe": "wait_observe", "query": ""}
        if "verdict" in props:
            return {"verdict": "irrelevant", "reason": ""}
        if "premises" in props:
            return {"premises": [], "unknowns": [], "answerable": True}
        if "hypotheses" in props:
            return {"hypotheses": []}
        return "うん。"


def plugin_files(root=ROOT):
    d = os.path.join(root, PLUGIN_DIR)
    return sorted(f for f in os.listdir(d) if f.endswith(".py")) if os.path.isdir(d) else []


class LivePluginsTest(unittest.TestCase):
    def test_every_plugin_passes_the_guard(self):
        for fn in plugin_files():
            with open(os.path.join(ROOT, PLUGIN_DIR, fn), encoding="utf-8") as f:
                self.assertEqual(check_source(f"evolvable/plugins/{fn}", f.read(), plugin=True), [], fn)

    def test_every_plugin_runs_without_errors(self):
        names = [f[:-3] for f in plugin_files()]
        if not names:
            self.skipTest("プラグインはまだ無い")
        with tempfile.TemporaryDirectory() as tmp:
            agent, llm, sensor, mem, clock, out = make_agent(tmp)
            agent.llm = AnyLLM(clock)
            metrics = Metrics(mem.db)
            host = PluginHost(dict(agent.cfg, plugin_max_errors=1, plugin_timeout_s=2.0), agent, metrics)
            self.assertEqual(sorted(host.load()), sorted(n for n in names), "読み込めないプラグインがある")
            host.tick()
            for i, (src, kind, content) in enumerate(EVENTS * 3):
                agent.memory.add_event(src, kind, content, 1.0, priority=0.5)
                clock.t += 30
                host.tick()
            self.assertEqual(sorted(host.plugins), sorted(names), "例外か時間切れで止まったプラグインがある")
            self.assertEqual(metrics.count("error", 0, float("inf")), 0)


if __name__ == "__main__":
    unittest.main()
