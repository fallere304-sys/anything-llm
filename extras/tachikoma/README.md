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

## Windows アプリ: Tachikoma.exe (これ 1 つで入る)

GitHub Actions ("Build Tachikoma.exe") の実行結果ページの **Artifacts → Tachikoma-windows** から `Tachikoma.exe` をダウンロードして
ダブルクリックするだけ。Python などを別に入れる必要はない (自分で作るなら `packaging\build_exe.ps1`)。

初回にやること (画面の案内に答えるだけ):
1. 自分を `%LOCALAPPDATA%\Tachikoma` に入れ、スタートメニューに「Tachikoma」を登録する (次からはそこから起動)
2. 追加機能を選ぶ: 音声会話と耳の自習 / 目の自習と頭・耳・目の学習 (PyTorch) / 自己進化 (Docker Desktop と CPU の考えるモデル)。
   選んだものは自動でダウンロードして入れる (あとで `Tachikoma.exe --setup` で選び直せる)
3. Ollama が無ければダウンロードのページを開く。あれば Gemma (`gemma4:e2b`) を自動で取得する
4. タチコマを起動し、ブラウザで画面 (http://127.0.0.1:8765) を開く。終わるときはコンソールで Ctrl+C

- 中身は組み込み版の Python 3.11 と本体のソース。**自己進化はインストール先のソースを書き換える**ので、
  アプリの中で進化が積み重なる (見守り役 `supervisor.py` が改良後の再起動と、壊れたときの自動撤回を担う)
- 新しい `Tachikoma.exe` を起動すると更新される。自己進化で書き換わった思考のファイルは残し (同梱の版は `*.new` として横に置く)、
  記憶 (`tachikoma.db`)・設定 (`config.json`)・学習の成果・モデル・育ったプラグインには触れない
- 設定は `%LOCALAPPDATA%\Tachikoma\app\config.json` (監視フォルダ `watch_dirs` などはここで)
- `Tachikoma.exe --selftest` で、同梱の Python でテスト一式を走らせて動作を確かめられる。`--uninstall` で取り除く (記憶も消える)

> exe は署名していないので、初回に Windows SmartScreen の警告が出ることがある (「詳細情報」→「実行」)。
> Ollama と Docker Desktop はそれぞれ独立したアプリなので、Tachikoma.exe の中には入れられない (案内とダウンロードのページを開くところまで行う)。

## Android 版: Tachikoma.apk (RAM 4GB の端末を想定)

GitHub Actions ("Build Tachikoma APK") の実行結果ページの **Artifacts → Tachikoma-android** から `Tachikoma.apk` を取り出して、
端末に入れる (提供元不明のアプリのインストールを許可する)。考える力は端末の中で動く小型モデルで、**出力は文字だけ**。

| | PC 版 | Android 版 |
|---|---|---|
| 考える力 | Gemma 4 E2B (GPU・Ollama) | **TinySwallow-1.5B-Instruct** (日本語に強い 1.5B・公式の Q5_K_M 約 1.1GB) を端末の CPU で (llama.cpp)。もっと軽い Qwen2.5-0.5B (約 0.4GB) も選べる |
| 入力 | 文字・マイク・カメラ・ネット・画面・ファイル | 文字・**マイク** (端末の音声認識。呼びかけ語「タチコマ」)・**カメラ** (人がいるか・映っている物と文字)・ネット (論文・ニュース) |
| 出力 | 声・画面 | **文字だけ** (チャット画面と通知) |
| 思考・記憶・学習データ・較正・深掘り・先見・自発性・プラグイン | ← 同じ Python のコード | ← 同じ |
| 自己進化 | パラメータ・指示文・コード・プラグイン | パラメータの調整 (コードを書く CPU の脳と隔離テストは端末に載らない)。PC で育ったプラグインはそのまま動く |
| 重みの学習 | 睡眠中に自動 (GPU) | 学習データを書き出し → **PC で学習** → アダプタを取り込むと、端末が PC 版と同じ規則で採否を決める |

使い方:
1. 初回に考える力 (モデル) を選んで取得する (Wi-Fi 推奨)。手元の GGUF を選んで取り込むこともできる
2. 「耳」「目」のボタンで、マイクとカメラを使うかを決める (画像も音声も保存・送信しない)。画面を閉じても、通知を出して考え続ける。
   電池が 30% 未満で充電していないときは、自分からは考えない (話しかけには答える)
3. 重みの学習: 「…」→「学習データを書き出す」で zip を保存し、PC で
   `python finetune/android_adapter.py tachikoma-train-*.zip --llama-cpp C:/llama.cpp` を実行してできた `.gguf` を、
   「…」→「学習したアダプタを取り込む」で取り込む。悪くなっていなければ採用され、`/rollback` で戻せる

自分でビルドするには Android SDK/NDK が要る: `extras/tachikoma/android` に llama.cpp を置いて (`.github/workflows/tachikoma-apk.yml` の版)、
`gradle assembleDebug`。設計の詳細は DESIGN.md §19。

## ソースから動かす (Windows 10)

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

## 性格 — タチコマという個体

- 一人称「ボク」の元気な口調 (攻殻機動隊のタチコマの気質を参考)。`"user_name"` で呼び名を設定できる。
- **知ったかぶりしない**: 返事の前に「知らないこと」と「怪しい前提」を点検し、知らないことは調べると約束する。
  調べ終わったら「さっきの、調べたよ！」と自分から報告する。
- **根拠の強さと因果**: 論文 (OpenAlex / PubMed / arXiv) と政府文書を格付けして使う。
  因果の主張は、ランダム化比較試験・メタ分析などの根拠が無い限り「推定」止まり。
- **自分を疑う**: 自分の予測の当たり外れを記録して確信過剰を補正し、確信している推定の反証も探す。
- **個性が育つ**: わかって嬉しかった話題への興味、褒められた/訂正された思い出、毎日の日記が、その後の話し方に反映される。

| コマンド | 効果 |
|---|---|
| `/self` | 生まれて何日目か・興味・これまでの数・確信の当たり具合 |
| `/diary` | 最新の日記 |

政府文書の検索には、同梱の `docker-compose.yml` で自前の検索エンジン SearXNG を立ち上げる (Docker Desktop):

```powershell
docker compose up -d        # http://127.0.0.1:8080 (自分の PC からだけ使える)
```

`config.json` に `"web": true, "searxng_url": "http://127.0.0.1:8080"` を設定する。
論文検索 (OpenAlex / PubMed / arXiv) は `"web": true` だけで使える (キー不要)。

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

## 画面 (UI)

起動すると http://127.0.0.1:8765 で画面が開ける。タチコマの画像が会話や状態に合わせて動き、画面からも話しかけられる。

- 画像は `ui/assets/tachikoma.png` に置く (表情別に `tachikoma_happy.png` / `_curious` / `_puzzled` / `_sorry` /
  `_proud` / `_thinking` / `_talk` / `_sleeping` / `_studying_ear` / `_studying_eye` / `_training` も置ける)。
  無ければ同梱の仮アバター。**タチコマの画像の著作権は権利者にあるので、私的利用の範囲で各自用意し、配布しないこと。**

## 目の自習 (日本語 OCR)

```powershell
pip install -r requirements-eye.txt        # torch / torchvision / transformers / pymupdf / pillow
```

- 文字情報付き PDF を `study/eye/` に置く (いろいろなフォントのものほど良い)。`"web": true` と SearXNG があれば、
  政府機関の公開 PDF (site:go.jp) を自分で取ってくる (出典は `*.source.txt` に記録)。
- 独りの時間に PDF を画像として読み、文字情報で答え合わせし、読み間違えた行と文字の組を集める。
  500 行溜まると OCR モデルを微調整し、**検証の CER が下がり、どのフォントも悪化していない**ときだけ採用する。

| コマンド | 効果 |
|---|---|
| `/eye_learn` / `/eye_rollback` | 目の学習を今すぐ / 一つ前の目に戻す |
| `/idle` | 独りの時間の各活動の「1Wh あたりの伸び」の見込みと試行回数 |

## 独りの時間の使い方はタチコマが決める

耳の自習・目の自習・耳/目/頭の学習・論文や資料を読む・休む、の中から、**「1Wh あたりの伸び」が最も大きいもの**を
1 つずつ選ぶ (耳と目は並行しない)。伸びなくなった練習は自然に選ばれなくなり、どれも割に合わなければ休んで電力を節約する。
何をなぜ選んだかは画面と `/status` に出る。詳しくは DESIGN.md §15。

## 視界の端への興味と、そこから育つ振る舞い

たとえ話: 攻殻機動隊のタチコマは、視界の端に映ったテレビのニュースからかつての仲間のピンチを知り、自分から情報を集めて行動した。
でも作品の序盤のタチコマにはできなかった。**その振る舞いを書き込むのではなく、自然に育つ進化を誘発する**のがここの設計。

**出発点 (序盤のタチコマ)** — 周辺にも興味を持ち、深掘りできる。でも、知ったことを誰かと結びつけたり、それで動いたりはしない。
- 視界の端: 聞こえてきたテレビや会話・ニュース (RSS)・背後のウィンドウのタイトル (中身は読まない)・ときどきカメラで眺める目の端
- 興味 = 新しさ × 後で役立ちそうか (経験から学ぶ)。興味の高い周辺の出来事には問いを立て、調べ、わかったことから次の問いを作る (`/threads`)。
  掘っても新しくわかることが減ったら見切る (最適採餌の限界値定理)

**選択圧 (カーネルが測る。タチコマには書き換えられない)**
- 自発性: 自分から (話しかけられていないのに) 言ったことに、あなたが反応したか (10 分以内に同じ話題で話しかけた / `/good`)。
  うるさがられた (`/bad`) は悪化として撤回の理由になる。反応はあなたにしか作れないので水増しできない
- 先見: 拾った情報が、後であなたの話しかけ・編集・端末・画面に出てきたか (`/foresight`)

**変異の余地** — 自己進化は、パラメータ・指示文・既存の関数の改良に加えて、**まったく新しい振る舞いをプラグインとして書き足せる**
(`evolvable/plugins/`)。プラグインが使えるのは狭い基本動作 (出来事を見る・記憶を引く・仮説を立てる・調べる・考える・伝える・メモする) だけ。
何を書くかは CPU の脳が、目標 (選択圧) と観察 (何が役に立ち、何が役に立たなかったか) から考える。
静的検査 → Docker の隔離環境での全テスト (プラグインの動作確認を含む) → 12 時間の試用期間で良くなったものだけが残る。
止まったり例外を繰り返したりするプラグインは、本体を止めずに外される。

どんな振る舞いが育つかは決めていない。育つかどうかも保証はない (→ DESIGN.md §18)。

ニュースを見るには config で `"web": true, "news": true` (設定例は有効)。

## 自己進化・自己強化 (CPU と RAM で動く)

タチコマが自分の「思考」のコード・指示文・性格パラメータを改良する。GPU は使わず、
**Docker の中の llama.cpp (CPU 専用、4 スレッド・6GB まで)** で考え、**Docker の隔離環境で全テストに合格した変更だけ**を入れ、
12 時間の試用期間で良くなったものだけを残す。**論文やネットで見つからない新しいやり方ほど高く評価して試す。**

1. 役割ごとの GGUF を `models/` に置く (どちらか片方だけでも動く):
   - `models/qwen2.5-coder-7b-instruct-q4_k_m.gguf` — コードの改良用 (コード特化)
   - `models/RakutenAI-7B-instruct-q4_K_M.gguf` — 日本語の指示文の書き直し・アイデア出し用
2. Docker Desktop を起動しておく (隔離テストと CPU の脳に使う)
3. ソースから、見守り役ごと起動する (自己改良後の再起動と、壊れたときの自動撤回を担う):
   ```powershell
   python supervisor.py --config config.json --verbose
   ```
   exe では自己進化は動かない (起動のたびに一時フォルダに展開されるので、書き換えが残らない)。

進化の方向 (頑健さ・速さ・知識の効率・合理性・省電力・関係) はタチコマ自身が、実運用の数値と過去の成功率から選ぶ。
変えられないもの (テスト・評価・学習の採否・資源の上限・ネットに触れる部分) は「カーネル」として固定されていて、
変更は提案だけ・あなたの `/approve` が必要。監督コマンド: `/evolution` `/freeze` `/unfreeze` `/revert N` `/proposals` `/approve N` `/discoveries`。
詳しくは DESIGN.md §13。

## VRAM と RAM の配分

「モデルは VRAM、コンテキストは RAM」は Gemma 4 E2B では得にならない (KV キャッシュが 128K でも 1GB 弱と小さく、
RAM に置くと生成が遅くなる)。**計算するもの (層・KV) は VRAM、引くだけのもの (長期記憶の DB・検索) は RAM** が推奨。
推奨設定と根拠は DESIGN.md §16。

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
- ニュースを有効にすると、設定した RSS を定期的に読み、深掘りのときに **検索語をニュース検索に送る**。背後のウィンドウは **タイトルだけ** を見て、中身は読まない。
- 自己進化が書いたプラグインも、ネット・ファイル・プロセスには直接触れられない (狭い基本動作だけ。検索語は同じ個人情報フィルタを通る)。
- 外に出るのは、有効にした場合の **検索語** (パス・メール・長い数字・URL・トークン風の語を含む検索は送らない) と **動画の取得要求**、および各モデルの初回ダウンロードだけ。
- **カメラ映像とマイク音声は保存しない。** 残るのは Gemma が書いた様子の説明文と、認識した文字だけ (自習用の動画クリップは除く)。
- キー入力の内容は取らない (取るのは「最後に操作した時刻」だけ)。
- 記憶は `tachikoma.db` (SQLite) のみ。消せば全て忘れる。
- 学習データと学習済みアダプタは `finetune_runs/` と Ollama の `tachikoma-vN` モデルに残る。完全に消すには
  これらも削除し `ollama rm tachikoma-vN` する。学習データが外に出ることはない。
