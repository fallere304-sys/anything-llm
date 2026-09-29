"""起動: python -m tachikoma --config config.json [--verbose]  /  tachikoma.exe [--config ...]"""

import argparse
import json
import os
import shutil
import sys

from . import config, sensors
from .agent import Tachikoma
from .dataset import TrainingData
from .kernel import egress
from .learner import Learner
from .llm import LLMError, OllamaClient
from .memory import Memory
from .probes import Probes


SETUP_CODE = 3     # 準備が整っていない (見守り役は起動し直さずに止まる)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tachikoma")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--verbose", action="store_true", help="思考ログ (好奇心・更新・保留) を表示")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if config.frozen() and not os.path.exists(args.config):
        first_run_config(args.config)
    cfg = config.load(args.config)
    cfg["verbose"] = args.verbose or cfg.get("verbose", False)
    from .kernel.evolve import apply_params, apply_prompts
    evolved = apply_params(cfg)                # 自己進化したパラメータ (範囲内・利用者の明示値は優先)
    evolved_prompts = apply_prompts()          # 自己進化した指示文
    for p in cfg.get("extra_site_packages") or []:
        # exe は重い依存 (faster-whisper / torch 等) を内蔵しない。同じ Python 3.11 の venv を借りる
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    # 推論サーバーにつなぐ。GPU の部品が落ちる PC (NVIDIA のドライバが古いなど) では、CPU だけの Ollama に切り替える
    from .kernel import ollama_host
    try:
        llm, llm_note = ollama_host.connect(cfg, OllamaClient,
                                            lambda c: c.chat("Reply with OK.", "ping", max_tokens=4))
    except LLMError as e:
        print(f"Ollama に接続できません。`ollama serve` と `ollama pull {cfg['model']}` を確認してください。\n{e}")
        return SETUP_CODE

    memory = Memory(cfg["db_path"])
    # 外への関所: ここから先、外へ出せるのは個人情報を含まない文字の問い合わせだけ (egress.log に記録)
    privacy = egress.install(cfg, memory.db,
                             log_path=os.path.join(os.path.dirname(os.path.abspath(cfg["db_path"])), "egress.log"))
    data = TrainingData(memory)
    learner = Learner(cfg, data, llm)
    llm.model = learner.active_model()      # 前回までに採用した学習済みの版があればそれを使う
    senses = sensors.build(cfg)
    log = []                                 # agent ができる前のログ中継
    # カメラとマイクのスイッチ (画面で切り替える。前に選んだ状態を覚えている)
    from .kernel.switches import Switches
    switches = Switches(store=data, log=lambda m: log[0](m) if log else print(m))
    voice_why = camera_why = ""
    tts = asr = study = asr_learner = camera = web = scholar = None
    eyes = eye_learner = ui = None

    if cfg["voice"] or cfg["study"]:
        try:
            from .asr import FasterWhisperASR
            asr = FasterWhisperASR(cfg, data.get("active_asr") or cfg["asr_model"])
        except Exception as e:  # noqa: BLE001
            print(f"音声認識を使えないので音声会話と自習を無効にします: {e}\n"
                  "(依存は requirements-voice.txt。初回は Whisper モデルのダウンロードが必要)")
            cfg["voice"] = cfg["study"] = False
            voice_why = f"音声認識を使えません ({e})"
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
    voice = None
    if cfg["voice"]:
        voice = VoiceSensor(cfg, asr, tts, prompt_fn=study.prompt if study else None,
                            listen=switches.wanted("mic", True))
        senses.append(voice)
    if cfg["camera"] and switches.wanted("camera", True):
        try:
            from .camera import PresenceSensor
            camera = PresenceSensor(cfg)
            senses.append(camera)
        except Exception as e:  # noqa: BLE001
            print(f"カメラを使えません: {e}")
            camera_why = str(e)
    if cfg["eye"]:
        try:
            from .eye_learner import EyeLearner
            from .eyes import EyeStudy
            from .ocr import VisionOCR
            ocr = VisionOCR(cfg, data.get("active_ocr") or cfg["ocr_model"])
            eyes = EyeStudy(cfg, memory, ocr, log=lambda m: log[0](m) if log else print(m))
            if cfg["ocr_finetune_enabled"]:
                eye_learner = EyeLearner(cfg, eyes, data, ocr, lambda m: VisionOCR(cfg, m), llm=llm)
        except Exception as e:  # noqa: BLE001
            print(f"目の自習を無効にします: {e}\n(依存は requirements-eye.txt。初回は OCR モデルのダウンロードが必要)")
    if cfg["ui"]:
        from .ui import UIServer, UISensor
        ui = UIServer(cfg)
        try:
            url = ui.start()
            print(f"画面: {url}")
            senses.append(UISensor(ui))
            if cfg.get("ui_open_browser") and config.frozen():
                import webbrowser
                webbrowser.open(url)
        except OSError as e:
            print(f"画面を開けません (ポート {cfg['ui_port']} が使用中?): {e}")
            ui = None
    if cfg["web"]:
        from .web import WebSearch
        web = WebSearch(cfg)
        from .scholar import Scholar
        scholar = Scholar(cfg)
    news = None
    if cfg["news"]:
        from .news import NewsFeed, NewsSensor
        news = NewsFeed(cfg)
        senses.append(NewsSensor(cfg, news, memory.db))

    from .activities import ReadingActivity, StudyActivity, TrainActivity
    from .idle import IdleScheduler
    from .power import PowerMeter
    power = PowerMeter(cfg)
    idle = IdleScheduler(cfg, memory, power)
    agent = Tachikoma(cfg, llm, memory, senses, Probes(cfg, memory, web=web, camera=camera, llm=llm, scholar=scholar,
                                                       news=news),
                      idle_fn=sensors.idle_seconds, data=data, learner=learner,
                      tts=tts, study=study, asr_learner=asr_learner, eyes=eyes, eye_learner=eye_learner,
                      idle=idle, ui=ui, power=power)
    log.append(agent.log)
    wire_switches(switches, cfg, agent, voice, camera, voice_why, camera_why, ui)
    if llm_note:
        agent.log(llm_note)
    if evolved or evolved_prompts:
        agent.log(f"自己進化の結果を反映: {evolved} {evolved_prompts}")

    # ---- 自己進化・自己強化 (CPU と RAM で動く。GPU は会話・耳・目に残す) ----
    from .kernel import runtime
    from .kernel.budget import Budget
    from .kernel.cpu_brain import CpuBrain
    from .kernel.evolve import Evolution
    from .kernel.metrics import Metrics
    from .kernel.novelty import NoveltyJudge
    from .kernel.sandbox import Sandbox
    from .kernel.foresight import Foresight
    from .kernel.initiative import Initiative
    from .kernel.plugins import PluginHost
    budget = Budget(cfg)
    metrics = Metrics(memory.db)
    # 先見の帳簿: 拾った情報が後で役立ったか (カーネルが記録し、思考のコードには読み出しだけ渡す)
    foresight = Foresight(cfg, memory.db)
    agent.usefulness = foresight.usefulness
    agent.foresight_rates = foresight.rates
    # 自発性の測定: 自分から言ったことに相棒が反応したか (発話と入力の口でカーネルが直接記録する)
    Initiative(cfg, memory.db).attach(agent)
    plugins = None
    if cfg["plugins"]:
        plugins = PluginHost(cfg, agent, metrics, log=agent.log)
        plugins.load()
    brain = CpuBrain(cfg, budget)
    evolution = None
    if cfg["evolution_enabled"] and config.frozen():
        # exe は起動のたびに一時フォルダへ展開されるので、自分を書き換えても残らない
        agent.log("自己進化はソースから起動したとき (python supervisor.py) だけ有効です")
    elif cfg["evolution_enabled"]:
        evolution = Evolution(cfg, memory, brain, Sandbox(cfg, budget), metrics,
                              novelty=NoveltyJudge(cfg, scholar=scholar, web=web))
        if not brain.available():
            agent.log("CPU の脳のモデルが無いので、自己進化はパラメータの調整だけ行います (README 参照)")
    # 独りの時間の候補。耳と目 (自習・学習) は同時には走らない: スケジューラが 1 つずつ選ぶ
    alone = agent.attention.alone_for
    if study is not None:
        idle.register("ear_study", StudyActivity("ear", study, data))
    if eyes is not None:
        idle.register("eye_study", StudyActivity("eye", eyes, data))
    if asr_learner is not None:
        idle.register("ear_train", TrainActivity("ear", asr_learner, data, alone, agent.log))
    if eye_learner is not None:
        idle.register("eye_train", TrainActivity("eye", eye_learner, data, alone, agent.log))
    if cfg["finetune_enabled"]:
        idle.register("brain_train", TrainActivity("brain", learner, data, alone, agent.log))
    idle.register("reading", ReadingActivity(agent))
    try:
        agent.say("起動しました。見てます。")
        code = runtime.run(agent, cfg, metrics, evolution=evolution, brain=brain if evolution else None,
                           power=power, foresight=foresight, plugins=plugins, privacy=privacy)
        if code == runtime.RESTART_CODE:
            agent.say("自分を改良したので、少し再起動するね！")
            for lr in agent.learners():
                lr.abort()
            if ui is not None:
                ui.close()
            runtime.restart(code)
    except KeyboardInterrupt:
        for lr in agent.learners():
            lr.abort()
        for st in (study, eyes):
            if st is not None:
                st.pause()
        agent.say("おやすみなさい。")
        if ui is not None:
            ui.close()
        if evolution is not None:
            brain.stop()
    return 0


def wire_switches(switches, cfg, agent, voice, camera, voice_why, camera_why, ui):
    """カメラとマイクのスイッチをつなぐ。カメラは起動時に切ってあっても、入れたときに開く。"""
    import importlib.util
    setup = "`Tachikoma.exe --setup` で「音声会話と耳の自習」を選ぶと入ります"
    if voice is not None:
        switches.register("mic", "マイク", voice.resume, voice.pause, on=not voice.paused)
    else:
        switches.register("mic", "マイク", why=voice_why or f"音声の部品が入っていないか、音声会話が切ってあります。{setup}")
    cam = {"sensor": camera}

    def camera_on():
        if cam["sensor"] is None:
            from .camera import PresenceSensor
            cam["sensor"] = PresenceSensor(cfg)
            agent.sensors.append(cam["sensor"])
            agent.probes.camera = cam["sensor"]
        else:
            cam["sensor"].resume()

    def camera_off():
        if cam["sensor"] is not None:
            cam["sensor"].pause()
    if importlib.util.find_spec("cv2") is None:
        switches.register("camera", "カメラ", why=f"カメラの部品 (opencv) が入っていません。{setup}")
    else:
        switches.register("camera", "カメラ", camera_on, camera_off, on=camera is not None)
        if camera_why:
            switches.devices["camera"]["why"] = f"開けませんでした: {camera_why}"
    if ui is not None:
        switches.push = lambda state: ui.push(dict({"type": "state"}, **state))
        ui.switches = switches
        switches.push(switches.state())


def first_run_config(path):
    """exe の初回起動: 同梱の設定例をもとに config.json を作る (音声などは依存が揃うまで無効)。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    example = os.path.join(base, "config.example.json")
    cfg = {}
    if os.path.exists(example):
        with open(example, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.update({"watch_dirs": [], "terminal_logs": [], "voice": False, "study": False, "eye": False,
                "camera": False, "finetune_python": None, "ui": True})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    readme = os.path.join(base, "README.md")
    if os.path.exists(readme) and not os.path.exists("README.md"):
        shutil.copy(readme, "README.md")
    print(f"初回起動: {os.path.abspath(path)} を作りました。音声・目・学習は README.md の手順で依存を入れてから有効にしてください。")


if __name__ == "__main__":
    sys.exit(main())
