# フェーズ記録

## Phase 1: 基本チャット — 完了(2026-10-02)

### 実装
- 設定(.env / 環境変数)。LAN公開時はトークン必須(未設定なら起動拒否)
- 秘密情報マスク付きログ、APIキー/トークンは `repr` に出さない
- `LLMProvider` 抽象(`stream()` のみ実装すればよい)。`mock` / `openai_compat`(Ollama・LM Studio・OpenAI)
- SQLite 会話ストア(`project_id` 列は Phase 3 用に確保済み)
- 文脈管理: システムプロンプト + 直近履歴を文字数予算で切り詰め。最新発言は必ず保持
- SSE ストリーミング(`status` / `delta` / `done` / `error` イベント)
- UI: 状態インジケータ(待機/考え中/応答中/エラー)、外部送信の警告、会話一覧、スマホ幅対応

### テスト
- 単体・API: 27件(`pytest`)すべて成功
- 実起動(Linux, 実HTTP)で確認:
  - mock でのチャットと永続化
  - 疑似OpenAI互換サーバー(実SSE)経由のストリーミング
  - LLM未接続時にエラーが握り潰されず UI/状態APIへ出る
  - `0.0.0.0` + トークン無しで起動拒否 / トークン有りで 401・200 の切り分け
  - ログに APIキー・トークンが出ない
- ヘッドレス Chromium(390px幅)で、送信・ストリーム表示・リロード後の履歴復元・HTMLのエスケープ(XSS)を確認

### 問題点・未検証
- **Windows実機・GTX1060・実Ollama では未検証**(このVMにはいずれも無い)
- 文脈の予算は文字数での近似。トークン数ではない
- ストリーム中にブラウザが切断されると、その応答は保存されない(ユーザー発言のみ残る)
- 認証は単一の共有トークン(HTTPなので LAN 内限定の想定。TLS 無し)
- `/api/status` は `llm_reachable` のため呼ぶたびにLLMへ疎通確認する(10秒間隔)
- Claude / Gemini プロバイダ、モデル切替は未実装

### 次フェーズへの課題
- Phase 2: Claude / Gemini プロバイダ追加、モデルプロファイル(fast / strong)をconfigで定義、切替UI
- Phase 3: 記憶(短期・長期・プロジェクト・作業履歴・重要事項)。保存は必ずMemory Tool経由 + ユーザー確認
- Phase 5 に備え、ツール実行の監査ログ(操作ログ)の置き場所を決める

## Phase 2: LLM Provider 拡張(OpenAI) + 音声出力の窓口 — 完了(2026-10-02)
方針変更: 思考モデルは OpenAI(クラウド)、音声は VOICEROID2 琴葉葵。

### 実装
- `openai` プロバイダ(APIキー必須・常に「外部送信」扱い・キーはログに出ない)
- モデルプロファイル `fast` / `strong`(モデル名は `LLM_MODEL` / `LLM_MODEL_STRONG`)。自動ルーティングはせず、UIで手動切替
- `TTSProvider` 抽象 + `mock`(ビープ) + `voiceroid2`(pyvcroid2 経由・専用単一スレッド)
- `POST /api/tts`(認証必須・2000文字上限)、UI に音声ON/OFFとモデル選択
- Claude / Gemini は今回見送り(クラウドは OpenAI を採用)

### テスト
- pytest 43件成功(profile選択、未設定profileの拒否、openai設定検証、TTS API、VOICEROID2アダプタ[偽の pyvcroid2 で検証])
- 実起動+ヘッドレスChromium: strong 選択→疑似OpenAI互換サーバーが model-big と `Bearer` 付きで受信、返答後に WAV を取得して再生、ログにキー無し

### 問題点・未検証
- **実OpenAI API・VOICEROID2・pyvcroid2 は未検証**(キーも製品もこの環境に無い)。pyvcroid2 の API は公開仕様に基づく想定
- OpenAI の推論系モデルは最初の出力まで時間がかかる場合がある(UIは「考え中」表示)。`max_tokens` 等は送っていない
- 利用料の上限管理(予算)は未実装。Phase 6 のタスク予算と合わせて実装する
- スマホでの音声自動再生は、音声ONボタン操作で解除する方式(実機未検証)
- 返答全文を一度に読み上げる(分割・ストリーミング再生は未対応)

### 次への課題
- 画面: 「知識・処理へのアクセスの視覚化」(参考画像の添付待ち)。そのためのイベント(記憶/Web/ファイル/ツールの参照・使用)をバックエンドから流す
- Phase 3: 記憶
