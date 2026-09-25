# BTファイル転送（Xperia Z4 ⇔ Windows 10）

このアプリを開いている間、Xperia Z4 は **Windows 10 標準の「Bluetooth 経由でファイルを送受信」**
（`fsquirt.exe`。タスクバーの Bluetooth アイコン →「ファイルの送信」「ファイルの受信」）の
相手として認識され、ファイルをやり取りできます。PC 側に追加のソフトは要りません。

| 項目 | 内容 |
| --- | --- |
| 対象端末 | Xperia Z4（SO-03G / SOV31 / 402SO、Android 5.0〜7.0）。minSdk 21 なので他の Android 5.0 以降でも動作 |
| 通信方式 | Bluetooth Classic、OBEX Object Push Profile（OPP、UUID `0x1105`）、RFCOMM |
| PC 側 | Windows 10 標準機能のみ（Bluetooth アダプタが必要） |
| 依存ライブラリ | なし（AndroidX 不使用、OBEX も自前実装） |

## 「近距離通信」に Bluetooth OPP を選んだ理由

Windows 10 の標準機能のうち、Android と直接ファイルをやり取りできる候補を比較しました。

| 候補 | Android から相手になれるか | 判断 |
| --- | --- | --- |
| **Bluetooth ファイル転送（OBEX OPP）** | 公開仕様。Android の公開 API で RFCOMM 待ち受けと SDP 登録ができる | **採用** |
| 近距離共有（Nearby Sharing） | Windows 同士専用の非公開プロトコル。公式に Android 非対応 | 不採用 |
| NFC（近距離無線通信） | NFC 付きの Windows PC はまれ。Windows 10 に NFC でファイルを受け渡す標準機能はない | 不採用 |

## 機能

- **起動中は PC から見える**：起動時に「検出可能」（300 秒）を要求し、OPP サーバとして待ち受けます。
  前面サービスで動くため、ホームボタンで別のアプリに切り替えても待ち受けは続きます。
  「戻る」「終了」、または通知の「停止」で待ち受けを終了します。
- **PC → スマホ**：受け取ったファイルは `ダウンロード/BtFileTransfer` に保存され、履歴からタップで開けます。
  - ペアリング済みの相手に限って接続を受け付けます（secure RFCOMM）。
  - ファイル名はパスを取り除いて安全な名前にし、同名がある場合は `名前 (2).拡張子` にします。
  - 空き容量が足りない場合は受信を拒否します。
- **スマホ → PC**：「ファイルを PC へ送信」で複数ファイルを選び、送信先をペアリング済み機器から選びます。
  ほかのアプリの「共有」から送ることもできます。進捗表示と中止ができます。
- **Android 標準の受信との共存**：Android OS 自身も OPP サーバを持っています（後述）。
  OS 側で受信されたファイル（`bluetooth` フォルダ）も検知して、このアプリの履歴に表示します（Android 9 以下）。

## 使い方

アプリ内の「Windows 側の操作」ボタンでも同じ手順を表示できます。

1. **初回だけ：ペアリング**
   アプリを起動して検出可能を許可します。PC の「設定」→「デバイス」→「Bluetooth とその他のデバイス」→
   「Bluetooth またはその他のデバイスを追加する」→「Bluetooth」で端末名を選び、表示される番号が同じなら両方で承認します。
2. **PC → スマホ**：アプリを開いたまま、PC の Bluetooth アイコンを右クリック →「ファイルの送信」→ 端末 → ファイルを選びます。
3. **スマホ → PC**：先に PC で「ファイルの受信」を開いて待機させます。
   次にアプリで「ファイルを PC へ送信」を押します。受信が終わったら、PC 側で保存場所を選んで「完了」を押します。

## ビルドとインストール

- **GitHub Actions**：`android-bt-file-transfer/` 以下を変更して push すると、
  `.github/workflows/android-bt-file-transfer.yaml` が単体テストを実行し、デバッグ APK を作ります。
  APK は Artifacts の `bt-file-transfer-debug-apk` からダウンロードできます。手動実行（workflow_dispatch）もできます。
- **Android Studio**：このフォルダを開いてビルドします（JDK 17、AGP 8.5.2、Gradle 8.9）。
- **コマンドライン**：`./gradlew testDebugUnitTest assembleDebug`
  → `app/build/outputs/apk/debug/app-debug.apk`
- **Xperia Z4 へのインストール**：APK を端末にコピーし、「設定」→「セキュリティ」→「提供元不明のアプリ」を許可してから開きます。
  `adb install app-debug.apk` でも入れられます。

## 構成

```
app/src/main/java/app/btfiletransfer/
├── MainActivity.java          画面・権限・Bluetooth ON/検出可能化・ファイル選択・送信先選択
├── TransferService.java       前面サービス: OPP サーバ待ち受け／送信キュー／通知
├── SystemReceiveWatcher.java  OS 標準の Bluetooth 受信フォルダの監視
├── LogEntry.java
├── obex/                      OBEX 1.x（純 Java。JVM で単体テスト可能）
│   ├── ObexPushServer.java    CONNECT / PUT(分割) / ABORT / DISCONNECT の受信側
│   ├── ObexPushClient.java    送信側（最大パケット長のネゴシエーション、Body の分割）
│   ├── ObexPacket.java / ObexHeaders.java / ObexConstants.java / ObexException.java
└── storage/
    ├── ReceivedFileStore.java Android 9 以下はファイル直書き、10 以降は MediaStore
    └── FileNames.java         受信ファイル名のサニタイズ
```

## 検証状況と既知の制約

- **検証済み**
  - OBEX 層はクライアントとサーバをパイプで直結する単体テスト 10 件が通っています。
    対象は複数ファイルの連続送信、パケット分割、境界サイズ、0 バイトのファイル、日本語や絵文字のファイル名、拒否応答、
    パケットのバイト列、パストラバーサル対策です。
  - アプリ全体は Android API 34 のフレームワーク jar に対してコンパイルが通ります。
  - Android 5.0 と 7.0 の jar に対してもコンパイルし、新しい API の呼び出しがすべてバージョン判定の内側にあることを確認しました。
- **未検証**：Xperia Z4 実機と Windows 10 PC を使った通信は試していません。
- **Android OS 標準の OPP サーバとの競合**：Android は OS 自身が OPP の SDP レコードを登録しています。
  このアプリも同じ UUID で登録するため、Windows がどちらに接続するかは端末の Bluetooth スタック次第です。
  - OS 側に接続された場合は、通知の「ファイルを受信しますか？」を承諾すれば受信できます。
    保存先は `/sdcard/bluetooth` などで、このアプリの履歴にも表示されます。
  - 機種によっては、アプリ側の待ち受け開始に失敗することがあります。その場合は画面上部に理由が表示され、
    OS 標準の受信機能だけで受信を続けます。
- **スマホ → PC の送信**は、PC 側で「ファイルの受信」画面を開いている間だけ成功します（Windows の仕様）。
