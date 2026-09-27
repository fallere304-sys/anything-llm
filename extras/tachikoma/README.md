# Tachikoma — 好奇心で自走する常駐ローカルAI (Gemma 4 E2B + Ollama)

ユーザーのアウトプット (保存したファイル、端末の出力、前面ウィンドウ、話しかけ) を常に観測し、
**「目の前の状況についての低確度推定」を減らすために自分から調べ**、必要なときだけ話しかける。
ユーザーとのやり取りと自律的に調べて確定した内容は学習データとして蓄積され、
長い無操作時 (睡眠中) に **LoRA ファインチューン** → 検証 → Ollama のモデル差し替え、まで自動で行う。

**音声会話モード**では、マイク・カメラ・ネットを情報源に声で会話し、
誰にも話しかけられず誰もカメラに映っていない間は、字幕付き動画で音声認識 (耳) を鍛える。

設計の詳細は [DESIGN.md](./DESIGN.md) (音声会話モードは §11)。

- 依存: Python 3.10+ 標準ライブラリのみ (pip install 不要)
- 推論: Ollama 上の `gemma4:e2b` (他のモデルでも可)
- 想定環境: Windows 10 / i7-7700 / RAM 16GB / GTX 1060 6GB

## セットアップ (Windows 10)

1. Ollama をインストールし、モデルを取得:
   ```powershell
   ollama pull gemma4:e2b
   ```
2. (推奨) システム環境変数を設定して Ollama を再起動:
   ```
   OLLAMA_KEEP_ALIVE=-1
   OLLAMA_NUM_PARALLEL=1
   OLLAMA_MAX_LOADED_MODELS=1
   ```
3. 端末の出力を観測させたい場合、PowerShell のプロファイル (`notepad $PROFILE`) に追記:
   ```powershell
   Start-Transcript -Path "$HOME\Documents\tachikoma-terminal.log" -Append | Out-Null
   ```
4. 設定ファイルを作る:
   ```powershell
   cd extras\tachikoma
   copy config.example.json config.json
   notepad config.json   # watch_dirs / terminal_logs を自分の環境に合わせる
   ```
5. 起動:
   ```powershell
   python -m tachikoma --config config.json --verbose
   ```
   コンソールに文字を打てば話しかけられる。`--verbose` で好奇心・信念更新・保留の思考ログが見える。

起動後に `ollama ps` で VRAM に全層が載っているか (PROCESSOR 列が `100% GPU`) を確認すること。
CPU 側に溢れている場合は `num_ctx` を 4096 に下げる。

## 学習 (ファインチューン) のセットアップ

本体は標準ライブラリだけで動くが、重みの学習には PyTorch 等が要るので **別の venv** を作る。
学習環境が無くても、蓄積した標本は few-shot 例として即座に使われる (学習だけが失敗扱いになり 6 時間ごとに再試行)。

```powershell
python -m venv C:\tachikoma-train
# GTX 1060 (Pascal, sm_61) を含む CUDA 版 torch を入れる (新しい CUDA 12.8+/13 ビルドは Pascal 非対応の可能性)
C:\tachikoma-train\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu126
C:\tachikoma-train\Scripts\pip install -r finetune\requirements.txt
C:\tachikoma-train\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_arch_list())"
# Gemma の利用規約に Hugging Face 上で同意し、ログインしておく (基盤モデル ~10GB をダウンロードする)
C:\tachikoma-train\Scripts\huggingface-cli login
```

`config.json` に追記:

```json
"finetune_python": "C:/tachikoma-train/Scripts/python.exe",
"hf_base_model": "google/gemma-4-E2B-it"
```

Ollama が LoRA (safetensors) を直接読めない場合は、llama.cpp を clone して `"llama_cpp_dir"` を設定すると GGUF 変換を経由する。

### 学習データの増やし方 (チャットのコマンド)

| コマンド | 効果 |
|---|---|
| `/good` | 直前の応答を正例として学習データに入れる |
| `/bad 正しい答え` | 直前の応答の代わりに、訂正文を正解として学習する (重み 1.5) |
| `/bad` | 悪い応答として保存 (学習はしない。将来の選好学習用) |
| `/learn` | 今すぐ学習を始める |
| `/rollback` | 一つ前に採用した版 (最終的には素のモデル) に戻す |
| `/status` | 使用中のモデル・標本数・学習の進行状況 |

質問に答えること、自分の状況を話すことも学習データになる。
自律調査の結果は、**独立な根拠 2 件以上 または ユーザーの回答** で確定したものだけが学習に回る。

## 音声会話モード (マイク・カメラ・ネット)

```powershell
pip install -r requirements-voice.txt      # numpy / sounddevice / faster-whisper / opencv / yt-dlp
# ffmpeg を https://ffmpeg.org から入れて PATH を通す
copy config.voice.example.json config.json
python -m tachikoma --config config.json --verbose
```

- 「タチコマ、〜」と呼びかけると声で返事する。返事の後 20 秒は呼びかけなしで会話が続く。
- 聞き取りに自信がないと「もう一回言って？」と聞き返す。
- 声のフィードバック: 「正解」「覚えといて」/「違う、正しくは○○」 (学習データになる)。
- 誰もいないとき (声も人影も 5 分なし) は声を出さず、`study/` の字幕付き動画 (`foo.mp4` + `foo.ja.srt` など) を
  **再生せずに** 聞き取り練習する。聞き間違えた行が溜まると、独りの時間に Whisper を微調整し、
  字幕の検証で文字誤り率が下がったときだけ差し替える。
- 自分の声の録音 (`anchors/foo.wav` + `foo.txt`、16kHz mono) を 20 文ほど置き `asr_anchor_dir` に指定すると、
  動画の話者に偏って利用者の声が聞き取りにくくなる変化を防げる。
- 耳の学習には「学習 (ファインチューン) のセットアップ」の venv が必要 (`ctranslate2` も入れる)。

| コマンド | 効果 |
|---|---|
| `/ear_learn` | 今すぐ耳 (Whisper) の学習を始める |
| `/ear_rollback` | 一つ前の耳に戻す |
| `/status` | 注意状態・自習の進み・最近の文字誤り率 (CER) も表示 |

> `study_urls` による動画の自動取得は既定で Creative Commons ライセンスのものに限定している。
> 各サービスの利用規約・著作権は利用者の責任で確認すること。

## 主な設定

| キー | 既定 | 意味 |
|---|---|---|
| `watch_dirs` | `[]` | 観測・検索してよいフォルダ (この外は読まない) |
| `terminal_logs` | `[]` | 追記を監視するログファイル |
| `clipboard` | `false` | クリップボード監視 (opt-in) |
| `active_window` | `true` | 前面ウィンドウのタイトル |
| `gpu_duty_cycle` | `0.5` | 背景思考が GPU を使ってよい時間割合 |
| `curiosity_threshold` | `0.35` | これ以上気になる仮説があれば調べ始める |
| `speak_threshold` | `0.6` | これ以上の価値がある話だけ口に出す |
| `allow_ask_user` | `true` | わからないとき質問してよいか |
| `num_ctx` | `8192` | 1 回の推論の文脈長 (128K にしない: VRAM 不足になる) |
| `finetune_enabled` | `true` | 睡眠中の自動学習 |
| `finetune_after_idle_s` | `7200` | 何秒無操作なら学習を始めるか |
| `finetune_min_new_samples` | `20` | 未学習標本がこれ以上溜まったら学習する |
| `finetune_args` | 4bit / r=8 / 2 epoch / fp32 演算 | `finetune/train_lora.py` への引数 |

## テスト

Ollama なしで、台本付きの偽 LLM を使って思考ループ全体を検証する:

```bash
cd extras/tachikoma
python -m unittest discover -s tests -v
```

学習スクリプト単体は、学習用 venv で `python finetune/train_lora.py --help`。

## プライバシー

- 任意コマンド実行の機能は持たない。調査 (プローブ) は読み取り専用で、書き込むのは自分の DB と `finetune_runs/`・`study/` だけ。
- 外に出るのは、有効にした場合の **検索語** (パス・メール・長い数字・URL・トークン風の語を含む検索は送らない) と **動画の取得要求**、および各モデルの初回ダウンロードだけ。
- **カメラ映像とマイク音声は保存しない。** 残るのは Gemma が書いた様子の説明文と、認識した文字だけ (自習用の動画クリップは除く)。
- キー入力の内容は取らない (取るのは「最後に操作した時刻」だけ)。
- 記憶は `tachikoma.db` (SQLite) のみ。消せば全て忘れる。
- 学習データと学習済みアダプタは `finetune_runs/` と Ollama の `tachikoma-vN` モデルに残る。完全に消すには
  これらも削除し `ollama rm tachikoma-vN` する。学習データが外に出ることはない。
