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
    senses = sensors.build(cfg)
    log = []                                 # agent ができる前のログ中継
    tts = asr = study = asr_learner = camera = web = scholar = None

    if cfg["voice"] or cfg["study"]:
        try:
            from .asr import FasterWhisperASR
            asr = FasterWhisperASR(cfg, data.get("active_asr") or cfg["asr_model"])
        except Exception as e:  # noqa: BLE001
            print(f"音声認識を使えないので音声会話と自習を無効にします: {e}\n"
                  "(依存は requirements-voice.txt。初回は Whisper モデルのダウンロードが必要)")
            cfg["voice"] = cfg["study"] = False
    if cfg["voice"]:
        from . import tts as tts_mod
        from .audio import VoiceSensor
        tts = tts_mod.build(cfg)
    if cfg["study"]:
        from .asr_learner import AsrLearner
        from .study import Study
        study = Study(cfg, memory, asr, log=lambda m: log[0](m) if log else print(m))
        if cfg["asr_finetune_enabled"]:
            asr_learner = AsrLearner(cfg, study, data, asr, lambda m: FasterWhisperASR(cfg, m), llm=llm)
    if cfg["voice"]:
        senses.append(VoiceSensor(cfg, asr, tts, prompt_fn=study.prompt if study else None))
    if cfg["camera"]:
        try:
            from .camera import PresenceSensor
            camera = PresenceSensor(cfg)
            senses.append(camera)
        except Exception as e:  # noqa: BLE001
            print(f"カメラを使えません: {e}")
    if cfg["web"]:
        from .web import WebSearch
        web = WebSearch(cfg)
        from .scholar import Scholar
        scholar = Scholar(cfg)

    agent = Tachikoma(cfg, llm, memory, senses, Probes(cfg, memory, web=web, camera=camera, llm=llm, scholar=scholar),
                      idle_fn=sensors.idle_seconds, data=data, learner=learner,
                      tts=tts, study=study, asr_learner=asr_learner)
    log.append(agent.log)
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        for lr in agent.learners():
            lr.abort()
        if study is not None:
            study.pause()
        agent.say("おやすみなさい。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
