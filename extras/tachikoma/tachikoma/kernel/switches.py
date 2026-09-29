"""カメラとマイクのスイッチ (カーネル)。相棒が画面で切り替え、タチコマ自身には切り替えられない。

- 切ると、機器そのものを手放す (カメラの点灯・Windows のマイク使用中の表示が消える)。
  切っている間は、映像も音も一切取らない (目の端で眺める・人の出入りの判定・音声認識も止まる)
- 選んだ状態は記憶 (kv) に残し、次に起動したときもそのままにする
- 機器を使う部品が入っていないときは、スイッチは押せず、理由を出す
- 切り替えは本体のループの中で行う (カメラの読み取りと取り合わないように)。画面は頼むだけ
"""

KEYS = {"camera": "switch_camera", "mic": "switch_mic"}


class Switches:
    def __init__(self, store=None, log=print, push=None):
        self.store = store              # get(k, default) / set(k, v) を持つもの (TrainingData)
        self.log = log
        self.push = push or (lambda state: None)
        self.devices = {}

    def wanted(self, name, default):
        """前に相棒が選んだ状態 (無ければ default)。"""
        if self.store is None:
            return default
        return bool(self.store.get(KEYS.get(name, "switch_" + name), default))

    def register(self, name, label, turn_on=None, turn_off=None, on=False, why=""):
        """turn_on が None の機器は使えない (why に理由)。"""
        self.devices[name] = {"label": label, "on": bool(on and turn_on), "available": turn_on is not None,
                              "why": "" if turn_on is not None else why, "turn_on": turn_on, "turn_off": turn_off}

    def set(self, name, on):
        d = self.devices.get(name)
        if d is None:
            return f"{name} というスイッチはありません"
        if on and not d["available"]:
            return f"{d['label']}は使えません: {d['why']}"
        if bool(on) == d["on"]:
            self._save(name, on)
            self.push(self.state())
            return ""
        try:
            (d["turn_on"] if on else d["turn_off"])()
            d["on"] = bool(on)
            d["why"] = ""
            msg = f"{d['label']}を{'入れた' if on else '切った'}"
        except Exception as e:  # noqa: BLE001  (機器が開けない・外れている)
            d["on"] = False
            if d["turn_off"] is not None:
                try:
                    d["turn_off"]()
                except Exception:  # noqa: BLE001
                    pass
            d["why"] = f"開けませんでした: {e}"
            msg = f"{d['label']}を入れられませんでした: {e}"
        self._save(name, d["on"] if on else False)
        self.log(msg)
        self.push(self.state())
        return msg

    def _save(self, name, on):
        if self.store is not None:
            self.store.set(KEYS.get(name, "switch_" + name), bool(on))

    def is_on(self, name):
        return bool(self.devices.get(name, {}).get("on"))

    def state(self):
        return {"devices": {n: {k: d[k] for k in ("label", "on", "available", "why")} for n, d in self.devices.items()}}
