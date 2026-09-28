"""Android 版タチコマの起動。Kotlin の常駐サービスが、モデルを読み込んだあとに別スレッドで run() を呼ぶ。

PC 版の __main__ と同じ部品 (思考・記憶・学習データ・独りの時間・先見・自発性・プラグイン・自己進化) を組み、
推論・入出力・学習の計算だけを端末用に差し替える。
"""

import json
import os
import time

from .. import config
from ..agent import Tachikoma
from ..dataset import TrainingData
from ..kernel import egress, runtime
from ..kernel.evolve import Evolution, apply_params, apply_prompts
from ..kernel.foresight import Foresight
from ..kernel.initiative import Initiative
from ..kernel.metrics import Metrics
from ..kernel.plugins import PluginHost
from ..memory import Memory
from .device import AndroidPower, AndroidProbes, BridgeSensor, Inbox, Output
from .learner import AndroidLearner
from .llm import LocalLLM

# 端末 (RAM 4GB・CPU・電池) 向けの既定値。config.json で上書きできる
ANDROID_DEFAULTS = {
    "num_ctx": 2048,
    "android_max_tokens": 384,        # 1 回の推論で作る長さの上限 (CPU なので短めに)
    "android_max_string": 300,        # JSON の文字列の長さの上限 (小型モデルが書き続けて切れるのを防ぐ)
    "android_battery_min": 30,        # 充電していないとき、これ未満 (%) なら背景思考をしない
    "gpu_duty_cycle": 0.3,            # 背景思考が CPU を使ってよい時間の割合 (発熱と電池のため低め)
    "tick_seconds": 2.0,
    "voice": True,                    # 声の入力 (話しかけ判定に呼びかけ語を使う)。声の出力はしない
    "tts": "none",
    "study": False, "eye": False, "camera": False, "ui": False,
    "active_window": False, "background_windows": False, "clipboard": False,
    "watch_dirs": [], "terminal_logs": [],
    "web": True, "news": True,
    "finetune_enabled": False, "asr_finetune_enabled": False, "ocr_finetune_enabled": False,
    "hf_base_model": "SakanaAI/TinySwallow-1.5B-Instruct",
    "model_prefix": "tachikoma",
    "evolution_enabled": True,        # CPU の脳も Docker も無いので、パラメータの調整だけが働く
    "plugins": True,
    "inquiry_on_chat": False,         # 返事の前の点検は推論 1 回ぶん遅くなるので、端末では既定で切る
    "verbose": True,
}


def load_config(home, model_name):
    path = os.path.join(home, "config.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ANDROID_DEFAULTS, f, ensure_ascii=False, indent=2)
    cfg = config.load(path)
    for k, v in ANDROID_DEFAULTS.items():
        if k not in cfg["_user_keys"]:            # 相棒が config.json で決めた値が優先。それ以外は端末向けの既定値
            cfg[k] = v
    cfg["model"] = model_name
    cfg["db_path"] = os.path.join(home, "tachikoma.db")
    cfg["adapters_dir"] = os.path.join(home, "adapters")
    cfg["evolution_dir"] = os.path.join(home, "evolution")
    cfg["finetune_dir"] = os.path.join(home, "finetune_runs")
    return cfg


class App:
    """組み立てと、操作 (書き出し・取り込み・停止) の受け付け。"""

    def __init__(self, bridge, engine, home, model_name, clock=time.time):
        self.bridge, self.home, self.clock = bridge, home, clock
        self.cfg = cfg = load_config(home, model_name)
        applied = apply_params(cfg, home)
        apply_prompts(home)
        self.memory = Memory(cfg["db_path"])
        self.privacy = egress.install(cfg, self.memory.db, log_path=os.path.join(home, "egress.log"))
        self.data = TrainingData(self.memory)
        battery_ok = lambda: bool(bridge.batteryOk(int(cfg["android_battery_min"])))   # noqa: E731
        self.llm = LocalLLM(cfg, engine, power_ok=battery_ok)
        self.learner = AndroidLearner(cfg, self.data, self.llm)
        self.llm.model = self.learner.active_model()      # 採用済みのアダプタがあれば当てる
        inbox = Inbox(bridge)
        senses = [BridgeSensor(n, inbox) for n in ("user", "voice", "camera")] + [_AppSensor(self, inbox)]
        web = scholar = news = None
        if cfg["web"]:
            from ..scholar import Scholar
            from ..web import WebSearch
            web, scholar = WebSearch(cfg), Scholar(cfg)
        if cfg["news"]:
            from ..news import NewsFeed, NewsSensor
            news = NewsFeed(cfg)
            senses.append(NewsSensor(cfg, news, self.memory.db))
        from ..activities import ReadingActivity
        from ..idle import IdleScheduler
        self.power = AndroidPower(bridge)
        idle = IdleScheduler(cfg, self.memory, self.power)
        probes = AndroidProbes(cfg, self.memory, bridge, web=web, llm=self.llm, scholar=scholar, news=news)
        self.agent = Tachikoma(cfg, self.llm, self.memory, senses, probes, out=Output(bridge),
                               idle_fn=lambda: float(bridge.idleSeconds()), data=self.data,
                               learner=self.learner, idle=idle, power=self.power)
        idle.register("reading", ReadingActivity(self.agent))
        self._foreground_replies()
        self.metrics = Metrics(self.memory.db)
        self.foresight = Foresight(cfg, self.memory.db)
        self.agent.usefulness = self.foresight.usefulness
        self.agent.foresight_rates = self.foresight.rates
        Initiative(cfg, self.memory.db).attach(self.agent)
        self.plugins = PluginHost(cfg, self.agent, self.metrics, root=home, log=self.agent.log) if cfg["plugins"] else None
        if self.plugins is not None:
            self.plugins.load()
        self.evolution = Evolution(cfg, self.memory, None, None, self.metrics, root=home) \
            if cfg["evolution_enabled"] else None
        if applied:
            self.agent.log(f"自己進化したパラメータを反映: {applied}")
        self.stopped = False

    def _foreground_replies(self):
        """返事を作っている間の推論は打ち切らせない (背景の推論だけが相棒の話しかけで打ち切られる)。"""
        orig = self.agent.on_user_message

        def wrapped(*a, **kw):
            self.llm.foreground += 1
            try:
                return orig(*a, **kw)
            finally:
                self.llm.foreground -= 1
        self.agent.on_user_message = wrapped

    def handle(self, kind, text, meta):
        """操作 (チャットのコマンドではなく、画面のボタン) を処理する。"""
        if kind == "export":
            path, n, h = self.learner.export(os.path.join(self.home, "exports"))
            self.bridge.offerFile(path, "学習データ")
            self.agent.say(f"学習データを書き出したよ (学習 {n} 件・検証 {h} 件)。PC のタチコマで学習してね。")
        elif kind == "import_adapter":
            self.agent.say(self.learner.import_adapter(meta.get("path") or text) or "いま別の学習を検証中なんだ。")
        elif kind == "stop":
            self.stopped = True

    def run(self, max_steps=None, sleep=time.sleep):
        self.agent.say("起動したよ。見てるし、聞いてる！")
        while not self.stopped:
            code = runtime.run(self.agent, self.cfg, self.metrics, evolution=self.evolution, power=self.power,
                               foresight=self.foresight, plugins=self.plugins, privacy=self.privacy, sleep=sleep,
                               max_steps=max_steps, stop=lambda: self.stopped)
            if max_steps is not None or code != runtime.RESTART_CODE:
                break
            self.agent.log("自己改良を反映しました")
        self.agent.say("おやすみなさい。")


class _AppSensor:
    """画面の操作 (source=app) は思考に渡さず、App が処理する。"""
    name = "app"

    def __init__(self, app, inbox):
        self.app, self.inbox = app, inbox

    def poll(self):
        for kind, text, meta in self.inbox.take("app"):
            try:
                self.app.handle(kind, text, meta)
            except Exception as e:  # noqa: BLE001 — 操作の失敗で本体を止めない
                self.app.agent.say(f"(うまくいかなかった: {e})")
        return []


def run(bridge, engine, home, model_name):
    """Kotlin から呼ばれる入口。止めるまで戻らない。"""
    App(bridge, engine, home, model_name).run()
