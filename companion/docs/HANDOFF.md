# 引き継ぎ資料(クラウド版 Claude Code → ローカル版 Claude Code)

作成: 2026-10-03 / ブランチ: `claude/ai-companion-phase1`(このブランチに PR #2 が紐付いている)

## 1. ユーザーと前提
- 実行環境: **Windows 10 の PC のみ**(i7-7700 / RAM 16GB / GTX1060 6GB)。スマホは使わない
- 回答は日本語。忖度しない。事実・推定・仮説を区別する(ユーザー設定の「構造化思考」)
- 有料契約は **Claude のサブスクのみ**(OpenAI / Perplexity / Anthropic API には払っていない)
- 元ネタ: Instagram の「Claude を頭にした JARVIS 風 AI 相棒」の動画(会話の頭=Claude、高速/上位モデルの自動切替、
  OpenAI 音声、声に合わせて動く画面、開発は Codex と Claude Code、重要判断は人間)。投稿本体はクラウド環境から閲覧不可だった

## 2. ユーザーが決めたこと(時系列)
1. 当初仕様: 汎用の AI 相棒(Phase 1〜10、Human-in-the-loop、権限レベル Lv0〜4)。仕様の要点は README と PHASES を参照
2. 思考は ChatGPT、音声は **VOICEROID2 琴葉葵**、クラウド AI 中心 → 後に変更
3. 可視化画面は参考画像(赤いネットワーク・ガラス球ノード・緑のチップ核・シアンの HUD 円弧)に合わせる → 実装済み・好評
4. **可視化パネルだけ英語**(「英語の方がかっこいい」)
5. 画像は Canva → **Gemini** に変更
6. 構成は **B案**(会話=Gemini、司令塔=Claude Code、作成=Gemini)。他社 AI(Groq 等)は**入れない**(「純粋な B 案」)
7. 配布形式は **AI相棒アプリ自体を .exe 化**(Claude Code の .exe 化ではない)

## 3. 現状(実装済み)
| 範囲 | 内容 |
|---|---|
| Phase 1 | FastAPI + SQLite、SSE ストリーミング、設定(.env)、秘密情報マスク、LAN 公開時トークン必須 |
| Phase 2 | LLMProvider 抽象(mock / openai_compat / openai / gemini)、fast/strong プロファイル、TTSProvider 抽象 |
| Phase 3 | 記憶(長期・プロジェクト・重要事項・作業履歴)、AI は提案のみ→承認で保存、2文字一致の関連度で文脈注入 |
| Phase 4/5 | Tool Registry、権限ポリシー(不変)、承認カード、エージェントループ(最大ステップ・タイムアウト) |
| B案 | Gemini 会話(OpenAI 互換窓口)、`create_document` / `research_web`(Google 検索)/ `generate_image`、`delegate_task`(Claude Code) |
| 司令塔 | `claude -p --output-format stream-json --verbose --tools "" --system-prompt … --restricted --strict-mcp-config --mcp-config … --allowedTools mcp__buddy`、指示書は標準入力、MCP 窓口 `/mcp/{run}`(実行ごとの使い捨てトークン・ループバックのみ) |
| UI | 可視化(英語 HUD)、承認カード、ツール結果(出典・成果物・サムネイル・司令塔の内訳と消費トークン)、記憶パネル |
| 配布 | `AI-Buddy.exe`(PyInstaller onedir)。exe の隣に .env / data / workspace / prompts。初回はメモ帳で .env を開く |

主要ファイル: `buddy/chat/service.py`(ループ・承認・中継)、`buddy/agent/orchestrator.py`(Claude Code 起動)、
`buddy/api/mcp.py`(MCP 窓口)、`buddy/tools/*`(各ツール)、`buddy/api/static/*`(UI・可視化)、`buddy/bootstrap.py`(exe 起動準備)。

## 4. 検証の状態
**検証済み(実物)**
- pytest 136 件(Linux と Windows の両方。Windows は GitHub Actions)
- 本物の Claude Code CLI(v2.1.288)で司令塔の通し確認: 疑似 Gemini → 承認 → Claude Code が MCP 経由で調査・文書作成 → 保存 → 報告(約15秒)
- コンテキスト実測: Claude Code 1回の入力 既定 約31,900 → 最小構成 約1,200 トークン。報告書タスク全体(3ターン)で約12,400
- exe: Windows でビルド・別の場所から起動・チャット応答・初回のメモ帳起動・zip に個人ファイル無し
- Gemini の送信形式(画像・文書・調査・モデル一覧)を公式 Python SDK(google-genai 2.28)の実送信と照合

**未検証**
- **ユーザーの PC での実起動**(exe・実 Gemini キー・Claude Code サブスクログイン)← 最優先
- 実 Gemini API: OpenAI 互換窓口でのツール呼び出し、Google 検索の無料枠、画像モデルの無料枠
- Windows で `claude` が `.cmd` の場合の起動(司令塔はリスト引数で起動するため `.cmd` だと失敗する可能性。`.exe` 版なら問題ない見込み)
- VOICEROID2(下記)

## 5. 既知の問題・注意
- **VOICEROID2 は現状使えない**: `pyvcroid2` は PyPI に無い(CI で確認)。以前の「pip install pyvcroid2」の案内は誤りだった。
  候補は VOICEROID2 を HTTP で喋らせる外部ツール経由(仕様未確認)。`TTSProvider` の差し替え口はそのまま使える
- Gemini 無料枠では送信内容(会話・記憶を含む)が Google の製品改善に使われる場合がある
- Claude Code の利用量はサブスク上限と共有。残量は取得できない
- 承認待ち中にページを閉じると、その作業は取り消される
- 関連度は文字一致のみ(意味検索ではない)。作業履歴は自動削除されない
- 自分の PC で公式 CLI を自分用に自動実行するのは問題ないと推定(未確認)。配布するなら API キー方式が必要

## 6. 次の作業(優先順)
1. **実機確認**: ユーザーと一緒に exe(または `python -m buddy`)を実キーで起動し、壊れた所を直す。
   特に `claude` の場所と形式(`where claude`)、Gemini のモデル名(`python -m buddy.tools.gemini_api`)
2. **VOICEROID2 の接続方式の再設計**: 実在する手段を調べて確認してから実装(琴葉葵で読み上げたいのがユーザーの要望)
3. **Phase 6 タスク**: 複数ステップの計画表示・進捗・予算(Claude Code の消費トークンの累計表示など)
4. **Phase 7 音声入力**: 無料・ローカル優先で検討(GTX1060 6GB で動くもの)。元ネタのように画面を声に合わせて動かす
5. 高速/上位モデルの自動振り分け(当初仕様の 3 章。現状は手動切替)
6. Phase 9/10: イベント駆動(新しいファイル・RSS 等)、自律性の拡張

## 7. 作業の進め方(クラウド版で守ってきたこと)
- 仕様を記憶で書かない: 公式 SDK を入れて実際の送信内容を記録して照合した(Gemini)、CLI は `--help` と実起動で確認した(Claude Code)
- 偽物のテストだけで終わらせない: 本物の CLI / ブラウザ(Playwright)で通し確認し、見つかった不具合(相対パス等)には回帰テストを足した
- コミットはこのブランチに push。push すると CI が Windows でテスト・exe ビルド・起動確認まで行う
- 報告では、検証済み/未検証を分けて書く
