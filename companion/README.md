# AI相棒 (companion)

会話・記憶・調査・作業支援を担う「AI相棒」。**AIが考え・提案・作業し、重要な判断は人間が承認する**(Human-in-the-loop)。
実装済み: Phase 1(基本チャット)、Phase 2(OpenAI・モデル切替・音声出力の窓口)、Phase 3(記憶)、知識・処理アクセスの可視化画面。進捗と課題は [docs/PHASES.md](docs/PHASES.md)。

## 起動

### Windows 10
1. Python 3.10 以上をインストール(https://www.python.org/ 、無料)。
2. `start.bat` をダブルクリック(初回は仮想環境の作成と依存導入を自動実行)。
3. ブラウザで http://127.0.0.1:8765/ を開く。

> Windows での実機検証は未実施(開発は Linux 上)。パスは `pathlib`、文字コードは UTF-8 で書いてあるが、問題があれば報告のこと。

### Linux / macOS
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m buddy
```

## LLM を繋ぐ
既定は `LLM_PROVIDER=mock`(LLM不要のダミー応答)。

### OpenAI(クラウド)
```
# .env
LLM_PROVIDER=openai
LLM_API_KEY=sk-...            # https://platform.openai.com で発行
LLM_MODEL=<普段使いのモデル名>        # profile "fast"
LLM_MODEL_STRONG=<深く考えるモデル名>  # profile "strong"(任意)。UIのモデル選択で切替
```
- モデル名はコードに書いていない。OpenAI の最新ドキュメントで確認して指定する。
- **ChatGPT(Plus等)の契約と API の課金は別**。API は従量課金で、会話内容は OpenAI に送信される(UIに警告表示)。
- ローカルLLM(Ollama 等)は `LLM_PROVIDER=openai_compat` + `LLM_BASE_URL`。

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

## スマホから使う
`.env` で `BUDDY_HOST=0.0.0.0` と `BUDDY_ACCESS_TOKEN=<長い乱数>` を設定し、スマホで `http://<PCのIP>:8765/` を開く。
初回にトークンの入力を求められる。**トークン未設定のLAN公開は起動を拒否する。** インターネットへの公開はしないこと。

## テスト
```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## 構成
```
buddy/config.py          設定(.env / 環境変数)。秘密情報は repr に出さない
buddy/logging_setup.py   秘密情報マスク付きログ
buddy/llm/               LLMProvider 抽象 + mock / openai_compat + registry
buddy/storage/db.py      SQLite 会話ストア
buddy/chat/              文脈構築 + チャットサービス(状態イベント生成)
buddy/activity.py        可視化ノード定義とアクセスイベント
buddy/memory/            記憶・プロジェクト・作業履歴(store / service / retrieval)
buddy/tts/               TTSProvider 抽象 + mock / voiceroid2
buddy/api/               FastAPI(認証・SSE)+ static/(UI・可視化 viz.js)
prompts/system.md        人格・行動規則(編集可)
```
