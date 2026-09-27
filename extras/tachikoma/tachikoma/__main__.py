"""起動: python -m tachikoma --config config.json [--verbose]"""

import argparse
import sys

from . import config, sensors
from .agent import Tachikoma
from .dataset import TrainingData
from .learner import Learner
from .llm import LLMError, OllamaClient
from .memory import Memory
from .probes import Probes


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tachikoma")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--verbose", action="store_true", help="思考ログ (好奇心・更新・保留) を表示")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.load(args.config)
    cfg["verbose"] = args.verbose or cfg.get("verbose", False)

    llm = OllamaClient(cfg)
    try:
        llm.chat("Reply with OK.", "ping", max_tokens=4)
    except LLMError as e:
        print(f"Ollama に接続できません。`ollama serve` と `ollama pull {cfg['model']}` を確認してください。\n{e}")
        return 1

    memory = Memory(cfg["db_path"])
    data = TrainingData(memory)
    learner = Learner(cfg, data, llm)
    llm.model = learner.active_model()      # 前回までに採用した学習済みの版があればそれを使う
    agent = Tachikoma(cfg, llm, memory, sensors.build(cfg), Probes(cfg, memory),
                      idle_fn=sensors.idle_seconds, data=data, learner=learner)
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        learner.abort()
        agent.say("おやすみなさい。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
