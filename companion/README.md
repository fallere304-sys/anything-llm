# AI相棒 (companion)

会話・記憶・調査・作業支援を担う「AI相棒」。**AIが考え・提案・作業し、重要な判断は人間が承認する**(Human-in-the-loop)。
実装済み: Phase 1〜5(基本チャット / LLM切替 / 記憶 / Tool Registry / 権限・承認)、司令塔(Claude Code)、可視化画面、音声出力の窓口。進捗と課題は [docs/PHASES.md](docs/PHASES.md)。

## 起動

### Windows 10(配布版 AI-Buddy.exe・おすすめ)
Python のインストールは不要。
1. GitHub の **Actions → 「Build AI-Buddy.exe」** の最新の成功した実行を開き、Artifacts の **AI-Buddy-windows** をダウンロードして展開する。
2. `AI-Buddy` フォルダを好きな場所に置き、`AI-Buddy.exe` をダブルクリック。
3. 初回は `.env`(設定)が作られてメモ帳で開くので、`GEMINI_API_KEY` とモデル名を設定して再起動。ブラウザで画面が開く。
- 設定・会話データ・作業フォルダはすべて `AI-Buddy` フォルダの中(フォルダごと移動・バックアップ可能)。
- 署名なしのため、初回に SmartScreen の警告が出ることがある(「詳細情報」→「実行」)。
- 自分の PC で exe を作る場合は `build_exe.bat`(Python 3.10+ が必要)。

### Windows 10(Python から起動)
1. Python 3.10 以上をインストール(https://www.python.org/ 、無料)。
2. `start.bat` をダブルクリック(初回は仮想環境の作成と依存導入を自動実行)。
3. ブラウザで http://127.0.0.1:8765/ を開く。

### Linux / macOS
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m buddy
```

## 構成(B案: 既定)
```
あなた ⇄ Gemini(会話・人格・記憶)                         無料枠
          │ 計画・判断・検証が要る複数ステップのタスクだけ「指示書」を渡す
          ▼
      Claude Code(司令塔)                                Claude のサブスク枠 / 最小構成で起動
          │ AI相棒のツールだけ使える(MCP 経由。組み込みツールは無効)
          ├ create_document  文書作成(Gemini)→ outputs/。Claude には保存先と冒頭だけ返す
          ├ research_web     調査(Gemini + Google 検索、出典付き)
          ├ generate_image   画像(Gemini)
          └ 作業フォルダ読み取り / 記憶の提案
          ▼ 短い報告 → Gemini が会話で伝える
単純な作業(1回のツールで済むもの)は Gemini が直接ツールを使い、Claude を消費しない。
承認・権限・作業履歴は、どちらの経路でも AI相棒側で一元管理。
```

### 準備
1. **Gemini の APIキー**: Google AI Studio で発行し、`.env` の `GEMINI_API_KEY` に設定。
   使えるモデル名は `python -m buddy.tools.gemini_api` で一覧表示できるので、`LLM_MODEL` / `GEMINI_TEXT_MODEL` / `GEMINI_IMAGE_MODEL` に設定。
2. **Claude Code**: PC にインストールし、一度 `claude` を起動して **サブスク(Pro/Max)でログイン**しておく。
   AI相棒は起動時に `claude` を自動で探し、見つかれば司令塔を有効にする(`CLAUDE_CODE_PATH` で場所を指定可)。

### コンテキスト(Claude の利用枠)を抑える仕組み
- Claude Code を最小構成で起動: 組み込みツール無効(`--tools ""`)、短い独自システムプロンプト(`--system-prompt`)、
  設定ファイル無視(`--restricted`)、AI相棒のツールのみ(`--strict-mcp-config`)。
  実測で 1回あたりの入力が **約31,900 → 約1,200 トークン**。
- Claude Code に会話履歴は渡さず、Gemini が作った「指示書」(目的・前提・期待する成果)だけを渡す。
- 作成物の本文は Claude に返さない(保存先と冒頭のみ)。必要なときだけ Claude が読む。
- 完了時、チャットに Claude Code の消費トークン(キャッシュ込み)を表示。

### 課金について
- Claude Code は **サブスク枠**で動かす。環境変数 `ANTHROPIC_API_KEY` は自動で外して起動し、
  それでも API キーで起動した場合は即中止する(意図しない API 課金の防止)。
- Gemini は無料枠(回数制限あり)。**無料枠では送信内容が Google の製品改善に使われる場合がある**。
- 他の経路(OpenAI / Perplexity / Claude API)は `.env` 末尾の「任意」欄で切り替え可能(いずれも API 課金)。

## 音声出力(琴葉葵 / VOICEROID2)
```
pip install pyvcroid2          # Windows。VOICEROID2 本体が必要
python -m buddy.tts.voiceroid2 # 利用可能な声の名前を表示
# .env
TTS_PROVIDER=voiceroid2
TTS_VOICE_NAME=<表示された琴葉葵の声の名前>
```
サーバー(PC)で合成した WAV を、UI(スマホ含む)で再生する。`TTS_PROVIDER=mock` でビープ音による動作確認ができる。
**VOICEROID2 アダプタは実機未検証。** VOICEROID2 の利用規約上、この用途が許されるかはご自身で確認すること。

## 権限と承認
- 権限レベル: Lv0 情報取得 / Lv1 読み取り / Lv2 可逆な変更 / Lv3 重要・外部送信 / Lv4 不可逆な外部操作(常に承認)。
- 外部への送信(Gemini・Claude Code)は既定で毎回承認。承認画面に「送信先・AIの理由・送る内容」が出る。
  Gemini 系は `TOOL_AUTO_APPROVE` で省略できる(会話自体が Gemini に送られているため)。
- Claude Code が使うツールも同じ権限判定・承認を通る。Claude Code 自身はファイル操作もコマンド実行もできない。
- ポリシーは起動時の `.env` からのみ決まり、AI が実行中に変更する手段は無い。

## 他の端末から使う(任意)
通常は PC 上でのみ使う想定。`.env` で `BUDDY_HOST=0.0.0.0` と `BUDDY_ACCESS_TOKEN=<長い乱数>` を設定し、スマホで `http://<PCのIP>:8765/` を開く。
初回にトークンの入力を求められる。**トークン未設定のLAN公開は起動を拒否する。** インターネットへの公開はしないこと。

## テスト
```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## ファイル構成
```
buddy/config.py          設定(.env / 環境変数)。秘密情報は repr に出さない
buddy/bootstrap.py       配布版の起動準備(exe の隣に設定・データを置く、初回ファイルの作成)
packaging/               配布版のビルド(PyInstaller)。CI: .github/workflows/companion-windows-exe.yml
buddy/logging_setup.py   秘密情報マスク付きログ
buddy/llm/               LLMProvider 抽象 + mock / openai_compat + registry
buddy/storage/db.py      SQLite 会話ストア
buddy/chat/              文脈構築 + チャットサービス(状態イベント生成)
buddy/activity.py        可視化ノード定義とアクセスイベント
buddy/memory/            記憶・プロジェクト・作業履歴(store / service / retrieval)
buddy/tools/             Tool Registry・権限ポリシー・各ツール(Claude / Perplexity / 作業フォルダ / 記憶)
buddy/agent/             承認の仲介 / 司令塔(Claude Code)の起動と実行管理
buddy/api/mcp.py         Claude Code 用の MCP 窓口(実行ごとの使い捨てトークン・PC内のみ)
buddy/tts/               TTSProvider 抽象 + mock / voiceroid2
buddy/api/               FastAPI(認証・SSE)+ static/(UI・可視化 viz.js)
prompts/system.md        人格・行動規則(編集可)
```
