"""設定の読み込み。config.json の値で DEFAULTS を上書きする。"""

import json
import os

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

    # --- 睡眠(記憶の整理) ---
    "sleep_after_idle_s": 1800,
    "event_retention_days": 7,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["watch_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["watch_dirs"]]
    cfg["terminal_logs"] = [os.path.abspath(os.path.expanduser(p)) for p in cfg["terminal_logs"]]
    cfg["study_media_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["study_media_dirs"]]
    return cfg
