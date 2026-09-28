"""端末の小型モデル (llama.cpp) を、OllamaClient と同じ形 (chat / gate / model) で使えるようにする。

- JSON スキーマは GBNF 文法に変換して、出力をその形に縛る (Ollama の structured output の代わり)。
  小型モデルが文字列を書き続けて途中で切れないよう、文字列には長さの上限を入れる
- 入力が文脈長に収まらなければ、中ほどを省く (前は状況、後ろは新しい出来事なので残す)
- 電池が少なく充電していないときは、背景思考をしない (話しかけへの返事はする)
- 相棒が話しかけたら、走っている背景の推論を打ち切る (Kotlin 側が abort を呼ぶ)。返事の推論は打ち切らない
- model に学習済みの版の名前を渡すと、その LoRA アダプタを当てて推論する (採否の検証と切り替えに使う)
"""

import copy
import json
import os
import time

from ..llm import GpuGate, LLMError
from .json_schema_to_grammar import SchemaConverter


def cap_strings(schema, n):
    """列挙でない文字列に長さの上限を入れる (上限がすでにあるものはそのまま)。"""
    schema = copy.deepcopy(schema)

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "string" and "enum" not in x and "maxLength" not in x:
                x["maxLength"] = n
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(schema)
    return schema


def schema_to_grammar(schema, max_string=300):
    c = SchemaConverter(prop_order={}, allow_fetch=False, dotall=False, raw_pattern=False)
    s = c.resolve_refs(cap_strings(schema, max_string), "")
    c.visit(s, "")
    return c.format_grammar()


class BatteryGate(GpuGate):
    """CPU (と電池) を 1 本の資源として配分する。電池が心もとないときは背景思考をしない。"""

    def __init__(self, duty_cycle, power_ok=None, clock=time.monotonic):
        super().__init__(duty_cycle=duty_cycle, clock=clock)
        self.power_ok = power_ok or (lambda: True)

    def can_run_background(self):
        return super().can_run_background() and self.power_ok()


class LocalLLM:
    def __init__(self, cfg, engine, power_ok=None, clock=time.monotonic):
        self.cfg, self.engine = cfg, engine
        self.gate = BatteryGate(cfg["gpu_duty_cycle"], power_ok, clock)
        self.base = cfg["model"]
        self.model = self.base
        self.num_ctx = cfg["num_ctx"]
        self.foreground = 0          # 返事を作っている最中 (打ち切らない)
        self._adapter = None
        self._grammars = {}

    def adapter_path(self, name):
        return os.path.join(self.cfg["adapters_dir"], f"{name}.gguf")

    def _use(self, model):
        want = None if model in (None, self.base) else model
        if want == self._adapter:
            return
        if not self.engine.setAdapter(self.adapter_path(want) if want else "", 1.0):
            raise LLMError(f"アダプタ {want} を使えません: {self.engine.lastError()}")
        self._adapter = want

    def _grammar(self, schema):
        key = json.dumps(schema, sort_keys=True, ensure_ascii=False)
        if key not in self._grammars:
            self._grammars[key] = schema_to_grammar(schema, self.cfg["android_max_string"])
        return self._grammars[key]

    def fit(self, system, user, max_tokens):
        """文脈長に収まるように、user の中ほどを省く。"""
        budget = self.num_ctx - max_tokens - 64
        for _ in range(4):
            n = self.engine.countTokens(system + "\n" + user)
            if n < 0 or n <= budget:
                return user
            keep = max(200, int(len(user) * budget / n * 0.9))
            head = keep // 3
            user = user[:head] + "\n…(省略)…\n" + user[-(keep - head):]
        return user

    def unload(self, model=None):
        pass                          # 端末では常に 1 つだけ載せている

    def chat(self, system, user, schema=None, think=False, max_tokens=512, temperature=0.3, model=None,
             images=None):
        max_tokens = min(max_tokens, self.cfg["android_max_tokens"])
        grammar = self._grammar(schema) if schema is not None else ""
        user = self.fit(system, user, max_tokens)
        background = self.foreground == 0

        def call():
            self._use(model or self.model)
            return self.engine.chat(system, user, grammar, max_tokens, float(temperature), background)

        text = self.gate.run(call)
        if text is None:
            raise LLMError(f"推論に失敗: {self.engine.lastError()}")
        if schema is None:
            return text.strip()
        try:
            return json.loads(text)
        except ValueError as e:
            raise LLMError(f"JSON を解釈できません: {text[:200]}") from e
