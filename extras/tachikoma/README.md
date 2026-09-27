# Tachikoma — 好奇心で自走する常駐ローカルAI (Gemma 4 E2B + Ollama)

ユーザーのアウトプット (保存したファイル、端末の出力、前面ウィンドウ、話しかけ) を常に観測し、
**「目の前の状況についての低確度推定」を減らすために自分から調べ**、必要なときだけ話しかける。

設計の詳細は [DESIGN.md](./DESIGN.md)。

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

## テスト

Ollama なしで、台本付きの偽 LLM を使って思考ループ全体を検証する:

```bash
cd extras/tachikoma
python -m unittest discover -s tests -v
```

## プライバシー

- すべてローカル。外部送信・コマンド実行・ファイル書き込みの機能は持たない (プローブは読み取り専用)。
- キー入力の内容は取らない (取るのは「最後に操作した時刻」だけ)。
- 記憶は `tachikoma.db` (SQLite) のみ。消せば全て忘れる。
