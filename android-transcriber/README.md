# 文字起こし (Android)

開始ボタンを押すだけで、オフラインで日本語の文字起こしを行う Android アプリ。

- 対象: Android 6.0 (API 23) 以上 / 想定端末 Xperia Z4
- 音声認識: [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + 日本語モデル [ReazonSpeech](https://huggingface.co/reazon-research/reazonspeech-k2-v2) (zipformer, int8 版 約 170MB) を APK に同梱
  - 発話の区切りは [Silero VAD](https://github.com/snakers4/silero-vad) で検出し、1 発話ごとに認識する
  - 端末内で処理するため、ネット接続不要・時間制限なし・音声は外部に送信されない
  - 音声はメモリ上でのみ扱い、認識後に捨てる (音声ファイルは作らない)
  - ライセンスはいずれも Apache-2.0 / MIT

## 使い方

1. アプリを起動 → 画面中央の **開始** ボタンのみが表示される
2. **開始** を押す → 文字起こしが始まり、画面が真っ黒になる (初回のみマイク権限を確認)
3. 画面をタッチ → 経過時間・直近の認識結果・**文字起こし終了** ボタンを表示
   - 発話中は「…」が出て、話し終わって約 1 秒後に文字が確定する
   - 認識が録音に追いつかないときは「処理待ち N 秒」と出る (端末性能の目安) (8 秒で再び黒画面、もう一度タッチでも黒画面)
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

ビルド時に以下を自動でダウンロードする (合計 約 760MB。2 回目以降は `.model-cache/` を再利用)。
ダウンロードできない環境では手動で取得し、`android-transcriber/.model-cache/` に同名で置く。

- https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar
- https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01.tar.bz2
- https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx

## 構成

```
app/src/main/java/app/mojiokoshi/
  MainActivity.java          画面 (開始 / 黒画面・操作パネル / 結果)
  TranscriptionService.java  録音・音声認識・ファイル保存 (フォアグラウンドサービス)
  ModelManager.java          同梱モデルの読み込み・認識と区切り検出の設定
  TextFormatter.java         認識結果の余分な空白を除去
```

## 制約・注意

- 句読点は付かない。1 行 = 1 発話 (0.8 秒以上の無音で区切られた単位、長い発話は 20 秒で区切る)
- 文字は話し終わってから確定する (話している途中の文字は出ない)
- 医療用語・薬剤名など一般的でない語は誤認識しやすい
- 起動直後はモデル (約 170MB) の読み込みに数秒〜十数秒かかる。その間に開始しても録音は始まっており、読み込み後にまとめて認識される
- APK サイズは約 220MB (モデル + ARM 用ネイティブライブラリ)。端末のメモリは 500MB 程度使う見込み
- CI の APK は実行ごとに異なるデバッグ鍵で署名されるため、更新時は一度アンインストールが必要な場合がある

## 検証結果 (PC 上、同じライブラリ・モデル・区切り設定で実施)

モデル付属のテスト音声 5 本 (約 55 秒) を 0.2 秒ずつ流した結果、6 発話すべてが正解文とほぼ一致した
(数字表記の違いと句読点の有無を除く)。処理時間は x86 1 コアで音声長の約 5%。
Xperia Z4 での速度・発熱は未計測。
