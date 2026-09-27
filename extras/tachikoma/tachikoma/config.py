"""設定の読み込み。config.json の値で DEFAULTS を上書きする。"""

import json
import os
import sys

DEFAULTS = {
    # --- 推論バックエンド (Ollama) ---
    "ollama_url": "http://127.0.0.1:11434",
    "model": "gemma4:e2b",
    # 128K まで扱えるが、GTX1060(6GB) では KV キャッシュが VRAM を圧迫するので絞る
    "num_ctx": 8192,
    "request_timeout": 120,
    # 背景思考が GPU を占有してよい時間割合 (0-1)。残りはユーザー対話と他アプリ用
    "gpu_duty_cycle": 0.5,
    "think_for_hypotheses": False,

    # --- 記憶 ---
    "db_path": "tachikoma.db",

    # --- 感覚器 (すべて opt-in) ---
    "watch_dirs": [],
    "watch_exts": [".py", ".js", ".ts", ".md", ".txt", ".json", ".log", ".yaml", ".yml", ".toml"],
    "max_file_bytes": 200000,
    "terminal_logs": [],
    "clipboard": False,
    "active_window": True,

    # --- 思考ループ ---
    "tick_seconds": 2.0,
    "novelty_threshold": 0.35,
    "curiosity_threshold": 0.35,
    "max_probe_attempts": 3,
    "relevance_half_life_s": 1800,

    # --- 発話ポリシー ---
    "speak_threshold": 0.6,
    "min_speak_interval_s": 90,
    "busy_idle_s": 5,
    "allow_ask_user": True,

    # --- 学習 (ファインチューン) ---
    # 無操作が続いたら、蓄積した「裏付けのある」標本で LoRA を学習し、
    # 検証で劣化していなければ Ollama のモデルを差し替える
    "finetune_enabled": True,
    "finetune_python": None,          # torch 等を入れた venv の python。None なら今の python
    "hf_base_model": "google/gemma-4-E2B-it",
    "finetune_dir": "finetune_runs",   # 学習の成果物 (版ごとの JSONL・アダプタ・Modelfile)
    "finetune_min_new_samples": 20,
    "finetune_after_idle_s": 7200,
    "finetune_timeout_s": 3 * 3600,
    "finetune_backoff_s": 6 * 3600,
    "finetune_args": ["--rank", "8", "--epochs", "2", "--lr", "1e-4", "--max-len", "768",
                      "--load-4bit", "--compute-dtype", "float32", "--gpu-mem-gib", "5"],
    "finetune_eval_max": 20,
    "finetune_min_holdout": 5,
    "finetune_tolerance": 0.02,
    "llama_cpp_dir": "",              # 指定すると LoRA を GGUF に変換してから Ollama に渡す
    "ollama_bin": "ollama",
    "model_prefix": "tachikoma",

    # --- 人格と認識の作法 ---
    "user_name": "",                  # タチコマが呼ぶ名前 (空なら「キミ」)
    "persona_playfulness": 0.6,       # はしゃぎ具合 (0-1)
    "persona_skepticism": 0.6,        # 慎重さ (確信過剰のときの初期確信の縮め方に効く)
    "chat_temperature": 0.5,
    "inquiry_on_chat": True,          # 返事の前に「怪しい前提」と「知らないこと」を点検する
    "wonder_interval_s": 900,         # 自分から疑問を作る間隔
    "challenge_share": 0.15,          # 好奇心の何割を「確信した推定の反証探し」に使うか
    "contact_email": "",              # OpenAlex の礼儀 (任意。送るのは検索時のみ)
    "gov_sites": ["go.jp", "gov"],    # SearXNG で政府文書を探すドメイン

    # --- 音声会話モード (耳・口) ---
    "voice": False,                   # マイクで常時聴取し、声で返事する
    "wake_words": ["タチコマ", "たちこま"],
    "conversation_window_s": 20,      # 話しかけ/返事の後、呼びかけ語なしで会話が続くとみなす秒数
    "talking_grace_s": 3,             # 誰かが話している最中 (この秒数以内に声) は割り込まない
    "voice_max_reply_chars": 120,
    "mic_device": None,
    "vad_aggressiveness": 2,
    "vad_end_silence_ms": 700,
    "barge_in": True,
    "barge_in_rms": 3000,
    "asr_model": "small",             # faster-whisper のモデル名 (初回に自動ダウンロード) またはパス
    "asr_device": "cuda",
    "asr_compute_type": "int8",       # Pascal は fp16 が遅いので int8
    "asr_language": "ja",
    "asr_beam_size": 3,
    "asr_min_logprob": -1.0,          # これより自信のない聞き取りは聞き返す
    "asr_prompt_terms": 20,
    "tts": "sapi",                    # sapi / voicevox / none
    "tts_rate": 1,
    "voicevox_url": "http://127.0.0.1:50021",
    "voicevox_speaker": 3,
    "voicevox_speed": 1.1,

    # --- カメラ (目) ---
    "camera": False,
    "camera_index": 0,
    "camera_interval_s": 1.0,
    "camera_absent_s": 60,
    "camera_motion_ratio": 0.02,
    "camera_face_model": "",          # OpenCV 5 系用: YuNet の ONNX (face_detection_yunet_*.onnx)
    "camera_describe": True,          # 人が現れたとき Gemma に様子を説明させる (画像は保存しない)

    # --- ネット ---
    "web": False,
    "web_language": "ja",
    "searxng_url": "",                # 自前の SearXNG があれば優先 (無ければ Wikipedia API)

    # --- 独りの時間 (自習と耳の学習) ---
    "absent_after_s": 300,            # 声も人影もこの秒数なければ「独り」
    "study": True,
    "study_dir": "study",             # 字幕付き動画 (foo.mp4 + foo.ja.vtt/srt) を置く・取得する場所
    "study_media_dirs": [],
    "study_urls": [],                 # yt-dlp で取得する動画/再生リスト (人手字幕のみ)
    "study_match_filter": "license ~= '(?i)creative commons'",
    "ytdlp_bin": "yt-dlp",
    "ffmpeg_bin": "ffmpeg",
    "study_cer_min": 0.05,            # これ未満は「もう聞き取れている」
    "study_cer_max": 0.5,             # これ超は字幕の意訳・ずれの可能性が高く捨てる
    "study_easy_ratio": 0.2,          # 聞き取れた例も忘却防止に一部残す
    "study_holdout_ratio": 0.15,
    "study_step_budget_s": 1.5,
    "asr_finetune_enabled": True,
    "asr_hf_base": "openai/whisper-small",
    "asr_train_after_alone_s": 3600,
    "asr_min_new_samples": 300,
    "asr_max_train": 4000,
    "asr_eval_max": 200,
    "asr_min_holdout": 30,
    "asr_anchor_dir": "",             # 利用者の声 (foo.wav + foo.txt) — 悪化していないかの確認用
    "asr_anchor_tolerance": 0.0,
    "asr_finetune_args": ["--rank", "16", "--epochs", "2", "--lr", "5e-5"],
    "ct2_converter": "",

    # --- 目 (OCR) の自習と学習 ---
    "eye": True,
    "ocr_model": "kha-white/manga-ocr-base",   # 行単位の日本語 OCR (Vision Encoder-Decoder)
    "ocr_device": "cuda",
    "ocr_cpu_threads": 4,
    "ocr_max_len": 64,
    "eye_dir": "study/eye",           # 文字情報付き PDF を置く・取得する場所
    "eye_pdf_dirs": [],
    "eye_fetch_topics": ["白書", "統計", "報告書", "ガイドライン", "年次報告", "資料"],
    "eye_max_pdf_bytes": 30 * 2**20,
    "eye_max_pages": 30,
    "eye_dpis": [96, 120, 150, 200],  # 画面・印刷・撮影の違いを模す描画解像度
    "eye_cer_min": 0.02,
    "eye_cer_max": 0.5,
    "eye_easy_ratio": 0.2,
    "eye_holdout_ratio": 0.15,
    "eye_rule_min_count": 3,
    "ocr_finetune_enabled": True,
    "eye_train_after_alone_s": 3600,
    "eye_min_new_samples": 500,
    "eye_max_train": 8000,
    "eye_eval_max": 300,
    "eye_min_holdout": 50,
    "eye_font_tolerance": 0.01,
    "ocr_finetune_args": ["--epochs", "2", "--lr", "2e-5", "--batch", "8"],

    # --- 独りの時間の使い方 (タチコマ自身が選ぶ) ---
    "idle_session_s": 900,            # 1 つの活動を続ける長さ (その後に選び直す)
    "idle_rest_s": 600,
    "idle_rest_value": 0.2,           # 「休む」の価値 (1Wh あたりの伸びがこれ未満なら休んで電力を節約)
    "idle_explore": 0.5,              # 試したことの少ない活動をどれだけ優遇するか
    "idle_ema": 0.3,
    "reading_weight": 0.5,            # 読書で解消した不確実性 1 bit を何ポイントとみなすか
    "gpu_tdp_w": 120,                 # GTX 1060
    "cpu_tdp_w": 65,                  # i7-7700

    # --- 資源の予算 (利用者が決める上限。タチコマは自分で上げられない) ---
    "budget_vram_gb": 6,
    "budget_ram_gb": 8,
    "budget_threads": 6,              # i7-7700 は 4 コア 8 スレッド → 論理 6 スレッドまで
    "budget_disk_gb": 20,

    # --- 自己進化・自己強化 (CPU と RAM で動く) ---
    "evolution_enabled": True,
    "evolution_dir": "evolution",
    "evolution_interval_s": 1800,     # 次の自己改良を始めるまでの最短間隔
    "evolution_window_h": 24,         # 「いまの必要度」を測る期間
    "evolution_explore": 0.3,
    "evolution_bold": 0.6,            # 使えるときに大胆な階層 (code) を選ぶ確率
    "evolution_min_gain": 0.03,       # 狙った指標がこれ以上改善したら定着
    "novelty_web_weight": 0.6,        # 新しさのうち「ネット・論文で見つからない」の比重
    "novelty_bonus": 1.0,             # 新しいやり方で成功したときの追加報酬
    "discovery_web_novelty": 0.8,     # これ以上ネットで見当たらない改良を「発見」として記録
    "canary_hours": 12,               # 試用期間
    "canary_min_steps": 500,
    "hotspot_min_s": 0.05,
    "profile_every": 300,             # 何 step ごとに 1 回プロファイルを取るか
    "sandbox": "docker",              # docker / local (local は allow_local_sandbox が必要)
    "allow_local_sandbox": False,
    "sandbox_image": "python:3.11-slim",
    "sandbox_timeout_s": 900,
    "docker_bin": "docker",
    # CPU の脳 (Docker の llama.cpp サーバー)。GGUF は利用者が用意する
    "cpu_brain_mode": "docker",       # docker / external (自分で起動した OpenAI 互換サーバー)
    "cpu_brain_url": "http://127.0.0.1:8081",
    "cpu_brain_image": "ghcr.io/ggml-org/llama.cpp:server",
    "cpu_brain_models": {
        "code": "models/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "ja": "models/RakutenAI-7B-instruct-q4_K_M.gguf",
    },
    "cpu_brain_ctx": 8192,
    "cpu_brain_ram_gb": 6,
    "evolution_threads": 4,           # 2 スレッドは会話・耳・目のために残す
    "cpu_brain_timeout_s": 1800,

    # --- 実行ファイル (tachikoma.exe) 用 ---
    # exe は Python 本体と標準ライブラリだけを内蔵する。音声・OCR など重い依存は、
    # 同じ Python 3.11 の venv の site-packages をここに指定すると読み込める
    "extra_site_packages": [],
    "ui_open_browser": True,

    # --- UI ---
    "ui": True,
    "ui_port": 8765,
    "ui_assets_dir": "ui/assets",     # ここに tachikoma.png (と表情別の画像) を置く

    # --- 睡眠(記憶の整理) ---
    "sleep_after_idle_s": 1800,
    "event_retention_days": 7,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    user = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        cfg.update(user)
    cfg["_user_keys"] = set(user)     # 利用者が明示した値は、進化で上書きしない
    cfg["watch_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["watch_dirs"]]
    cfg["terminal_logs"] = [os.path.abspath(os.path.expanduser(p)) for p in cfg["terminal_logs"]]
    cfg["study_media_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["study_media_dirs"]]
    cfg["eye_pdf_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["eye_pdf_dirs"]]
    return cfg


def frozen():
    """PyInstaller で固めた実行ファイル (tachikoma.exe) として動いているか。"""
    return bool(getattr(sys, "frozen", False))


def training_python(cfg):
    """学習スクリプトを動かす Python。exe 自身は Python として使えないので、exe では設定が必須。"""
    return cfg.get("finetune_python") or (None if frozen() else sys.executable)
