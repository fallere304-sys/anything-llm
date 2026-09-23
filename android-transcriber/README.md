# 文字起こし (Android)

開始ボタンを押すだけで、オフラインで日本語の文字起こしを行う Android アプリ。

- 対象: Android 6.0 (API 23) 以上 / 想定端末 Xperia Z4
- 音声認識: [Vosk](https://alphacephei.com/vosk/) 日本語小型モデル (`vosk-model-small-ja-0.22`, 約 48MB) を APK に同梱
  - 端末内で処理するため、ネット接続不要・時間制限なし・音声は外部に送信されない

## 使い方

1. アプリを起動 → 画面中央の **開始** ボタンのみが表示される
2. **開始** を押す → 文字起こしが始まり、画面が真っ黒になる (初回のみマイク権限を確認)
3. 画面をタッチ → 経過時間・直近の認識結果・**文字起こし終了** ボタンを表示 (8 秒で再び黒画面、もう一度タッチでも黒画面)
4. **文字起こし終了** を押す → 結果画面にテキストを表示
   - **全文コピー**: クリップボードへコピー
   - **共有**: メール / LINE / メモ帳などへ送る
   - テキストは画面上で直接修正してからコピーすることもできる
   - 同じ内容が `Android/data/app.mojiokoshi/files/transcripts/mojiokoshi_YYYYMMDD_HHMMSS.txt` にも保存される (PC と USB 接続で取り出せる)

## 2 時間の連続録音のための設計

| 対策 | 内容 |
| --- | --- |
| フォアグラウンドサービス | 録音と認識は `TranscriptionService` で実行。画面を消しても、アプリを切り替えても止まらない。通知から終了も可能 |
| Wake Lock | CPU がスリープして認識が止まるのを防ぐ (安全のため上限 6 時間) |
| 録音と認識の分離 | 録音スレッド → キュー → 認識スレッド。モデル読み込み中や処理が一時的に遅れても音声を取りこぼさない |
| 逐次保存 | 確定した文を 1 文ごとにファイルへ追記。途中でアプリが落ちてもそこまでの内容は残る |
| メモリ上限 | 認識待ちの音声は最大 5 分 (約 10MB)。認識が追いつかない状態が続いた場合は古い音声から捨てて強制終了を防ぎ、欠落した秒数を結果画面に表示する |
| 保存失敗時の継続 | 空き容量不足などでファイルに書けなくなっても認識は止めない。結果画面に警告を出し、テキストは画面からコピーできる |
| 黒画面 | 画面は点灯したままバックライトを最小にし、タッチで即座に操作できるようにする |
| 戻るキー対策 | 文字起こし中に戻るキーを押してもアプリは閉じず、操作パネルを表示する |

## ビルド方法

### GitHub Actions (推奨)

`android-transcriber/` 以下を push すると `.github/workflows/android-transcriber.yaml` が APK をビルドする。
Actions の実行結果ページの **Artifacts → mojiokoshi-apk** から APK をダウンロードし、端末にインストールする
(端末の設定で「提供元不明のアプリ」を許可しておく)。

### ローカル (Android Studio)

`android-transcriber` フォルダを Android Studio で開いてビルドするか、以下を実行する。

```sh
cd android-transcriber
./gradlew assembleRelease
# 出力: app/build/outputs/apk/release/app-release.apk
```

ビルド時に Vosk モデル (約 48MB) を自動でダウンロードする。
ダウンロードできない環境では https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip を手動で取得し、
`android-transcriber/.model-cache/vosk-model-small-ja-0.22.zip` に置く。

## 構成

```
app/src/main/java/app/mojiokoshi/
  MainActivity.java          画面 (開始 / 黒画面・操作パネル / 結果)
  TranscriptionService.java  録音・音声認識・ファイル保存 (フォアグラウンドサービス)
  ModelManager.java          同梱モデルの展開と読み込み
  TextFormatter.java         Vosk 出力の単語間スペースを除去
```

## 制約・注意

- 認識精度は小型モデルのため、クラウド音声認識 (Google など) より低め。専門用語や固有名詞は誤認識しやすい
- 句読点は付かない。1 行 = 1 発話 (無音で区切られた単位)
- 初回起動時はモデルの展開に数十秒かかる。その間に開始しても録音は始まっており、展開後にまとめて認識される
- APK サイズは約 70MB (モデル + ARM 用ネイティブライブラリ)
- CI の APK は実行ごとに異なるデバッグ鍵で署名されるため、更新時は一度アンインストールが必要な場合がある
