"""端末との入出力。Kotlin 側の Bridge (窓口) を、PC 版と同じ形の感覚器・調べ方・電力計・出力に見せる。

Bridge (Kotlin の object Bridge) が持つもの:
    poll() -> str | None        溜まった入力 (JSON の配列)。{"source", "kind", "text", "meta"}
    say(text) / log(text)       出力は文字だけ (チャット画面と通知)
    state(json)                 画面の状態表示 (注意・活動・モデル)
    idleSeconds()               画面が消えている / 触られていない秒数
    batteryOk(minPercent) / batteryWatts()   充電中か電池が minPercent 以上なら背景思考してよい
    cameraOn() / describeCamera(timeoutMs) -> str | None   カメラに映るもの (ML Kit の物の名前と文字)
    offerFile(path, title)      書き出したファイルを相棒に渡す (保存先を選んでもらう)

入力の source ごとに感覚器を分ける (user = 文字入力, voice = 音声認識, camera = カメラ, app = 操作)。
思考のコードからは、PC 版の感覚器と区別がつかない。
"""

import json
from collections import defaultdict, deque

from ..probes import Probes


class Inbox:
    """Bridge から 1 回まとめて受け取り、source ごとに配る。"""

    def __init__(self, bridge):
        self.bridge = bridge
        self.queues = defaultdict(deque)

    def pull(self):
        raw = self.bridge.poll()
        if not raw:
            return
        try:
            items = json.loads(raw)
        except ValueError:
            return
        for it in items if isinstance(items, list) else [items]:
            if isinstance(it, dict) and it.get("kind"):
                self.queues[it.get("source") or "user"].append(it)

    def take(self, source):
        self.pull()
        q = self.queues[source]
        out = []
        while q:
            it = q.popleft()
            out.append((it["kind"], str(it.get("text") or ""), it.get("meta") or {}))
        return out


class BridgeSensor:
    """ある source の入力を出来事として渡す感覚器。"""

    def __init__(self, name, inbox):
        self.name, self.inbox = name, inbox

    def poll(self):
        return self.inbox.take(self.name)


class AndroidProbes(Probes):
    """調べ方は PC 版と同じ。「見る」だけは、端末のカメラに映るもの (物の名前と文字) を文章にして返す。"""

    def __init__(self, cfg, memory, bridge, **kw):
        super().__init__(cfg, memory, **kw)
        self.bridge = bridge

    @property
    def camera(self):
        return self if self.bridge.cameraOn() else None

    @camera.setter
    def camera(self, value):
        pass                          # PC 版の初期化が None を入れに来るが、端末ではカメラの有無を都度 Bridge に聞く

    def look(self, query):
        if not self.bridge.cameraOn():
            return None
        desc = self.bridge.describeCamera(8000)
        return f"カメラに映っているもの: {desc}" if desc else None


class AndroidPower:
    """電池の放電の速さ (W)。測れないときは小さな目安。"""

    def __init__(self, bridge, fallback=2.0):
        self.bridge, self.fallback = bridge, fallback

    def watts(self, gpu_active_guess=False):
        try:
            w = float(self.bridge.batteryWatts())
        except (TypeError, ValueError):
            w = 0.0
        return w if w > 0 else self.fallback * (1.5 if gpu_active_guess else 1.0)


class Output:
    """agent の out。発話と思考ログを分けて画面に送る (出力は文字だけ)。"""

    def __init__(self, bridge):
        self.bridge = bridge

    def __call__(self, text):
        text = str(text)
        if text.startswith("  · "):
            self.bridge.log(text[4:])
        else:
            self.bridge.say(text)
