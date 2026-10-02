# AI相棒 (companion)

会話・記憶・調査・作業支援を担う「AI相棒」。**AIが考え・提案・作業し、重要な判断は人間が承認する**(Human-in-the-loop)。
現在は **Phase 1(基本チャット)** まで実装済み。進捗と課題は [docs/PHASES.md](docs/PHASES.md)。

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

## LLM を繋ぐ(任意)
既定は `LLM_PROVIDER=mock`(LLM不要のダミー応答)。ローカルLLMを使う例(Ollama):
```
ollama pull qwen2.5:3b          # GTX1060 6GB なら 3B〜7B(4bit)が目安 [推定]
# .env
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b
```
OpenAI など外部APIを使う場合は `LLM_BASE_URL` と `LLM_API_KEY` を設定する。外部送信になる場合、UIに警告が出る。

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
buddy/api/               FastAPI(認証・SSE)+ static/index.html(UI)
prompts/system.md        人格・行動規則(編集可)
```
