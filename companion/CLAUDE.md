# AI相棒(companion)

ユーザー専用の AI 相棒。会話・記憶・調査・作業支援を行い、**重要な判断は人間が承認する**(Human-in-the-loop)。
経緯・判断理由・次の作業は `docs/HANDOFF.md`、フェーズごとの記録は `docs/PHASES.md`。作業を始める前に HANDOFF を読むこと。

## 構成(B案・既定)
会話 = Gemini(無料枠) / 司令塔 = Claude Code(サブスク枠・最小構成で起動) / 作成・調査・画像 = Gemini。
単純な作業は Gemini が直接ツールを使い、計画・判断・検証を伴う複数ステップだけ `delegate_task` で Claude Code へ。

## コマンド(このフォルダで)
- セットアップ: `py -3 -m venv .venv` → `.venv\Scripts\pip install -r requirements-dev.txt`
- テスト: `.venv\Scripts\python -m pytest -q`(全件成功を保つ。変更には必ずテストを足す)
- 起動: `.venv\Scripts\python -m buddy`(設定は `.env`。雛形は `.env.example`)
- キー無しの通し確認: `devtools/fake_gemini_server.py` の先頭コメント参照
- 配布版のビルド: `build_exe.bat`(CI: push 時に GitHub Actions「Build AI-Buddy.exe」が Windows でビルド・起動確認)

## 守ること
- 回答・UI は日本語。ただし**可視化パネル(ネットワーク図・ACTIVITY ログ・ノード詳細)だけ英語**(ユーザー指定)
- APIキー・モデル名をコードに書かない(`.env` のみ)。秘密情報をログに出さない
- 権限: ツールは Tool Registry に登録し権限レベルを付ける。Lv4 は常に承認、外部送信は既定で承認。
  ポリシーは起動時の `.env` からのみ決まり、AI が実行中に変える手段を作らない
- 司令塔(Claude Code)は組み込みツール無効・AI相棒のツールのみ。`--bare` は使わない(サブスクのログインが効かなくなる)。
  `ANTHROPIC_API_KEY` を外して起動し、API キーで起動したら中止する(API 課金防止)
- 有料契約は Claude のサブスクのみ。新たな有料サービスを導入する場合は、費用・代替・保守負担を示してユーザーの了承を取る
- 外部サービスの仕様は記憶で書かない。公式 SDK・実通信で確認する(確認できない場合は「未検証」と明記)
- 事実と推測を区別して報告する。テストやCIの失敗を隠さない
- フェーズを終えたら `docs/PHASES.md` に実装・テスト・問題点・次の課題を追記する
