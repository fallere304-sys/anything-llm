# 引き継ぎ：Z4 MotionCam をキオスク端末（専用端末）にする

このファイルは、「Z4 MotionCam キオスク端末化」の作業用の依頼文です。ブランチ `claude/xperia-z4-motion-camera-7x4wet` の最新（コミット 417ecba 以降）を土台に作業してください。説明・報告・質問はすべて日本語で書いてください（依頼主は英語を読めません。コミットメッセージだけ英語でよい）。

## 今のアプリの状態（v1.12 相当。要点だけ）
v1.11 からの主な変更（すべて本流に入っている。README に詳細あり）:
- 検知モードの切り替え: 設定 `detect_mode`（motion = 物が動いたら録画 / person = 人がいると判断したら録画）、`person_sens`（厳しい／標準／ゆるい）。3段階の人判定（動体検知 → シルエット（形）→ 体のモデル（任意））。明るさの変化だけでは録画しない動き検知。
- 低電池（18%で停止・50%で再開）、おやすみ時間（3:00〜4:00、80%以上なら継続）の自動停止（`MonitorPolicy`）。
- USB なしの更新導入: インターネットから確認（プル型）、ブラウザから送る（プッシュ型、LAN限定・6桁コード）。
- 動画の日時表示、録画一覧のサムネイルと再生中の行のスクロール、前面に戻す機能（KeepFront）、稼働記録の出来事。
- 標準のビルドは約165KB（外部ライブラリなし）。`PERSON_MODEL=1` で体のモデル入り（約6.8MB）。

## 作業の前提
- リポジトリ: https://github.com/fallere304-sys/anything-llm のブランチ `claude/xperia-z4-motion-camera-7x4wet`、フォルダ `xperia-z4-motion-camera/`。作業前に pull する。
- ビルドは `build-apk.sh`（Windows なら WSL の Ubuntu）。必要なもの: `sudo apt-get install aapt apksigner zipalign dalvik-exchange android-sdk-platform-23 openjdk-17-jdk`。Android Studio / Gradle ではビルドしない（デバッグ鍵で署名され、端末のアプリを上書き更新できなくなる）。
- **署名鍵**: `C:\z4\z4motioncam.keystore`（パスワードとエイリアスは依頼主に確認する。この資料には書かない）。WSL からは `/mnt/c/z4/z4motioncam.keystore`。`KEYSTORE=... KS_PASS=（依頼主に確認した値）` を build-apk.sh に渡す。**鍵はコミットしない。別の鍵で署名すると、端末に入っているアプリを上書きできず、入れ直し（録画が消える）になる。**
- 版: 今回は 1.13（`VERSION_CODE=14 VERSION_NAME=1.13`）。1.12（VERSION_CODE=13）は、キオスク前の版として別にビルドしてよい。汎用版 → `release/z4motioncam-<版>.apk`（古い APK は git rm）。個人用（自宅IP入り）: `EXTERNAL_HOST=（依頼主の自宅のグローバルIP。依頼主に確認する）APK_NAME=（リポジトリの外のパス）` — **自宅のグローバルIPは、この資料にも、コード・README・コミットメッセージにも、絶対に書かない。個人用 APK もリポジトリに置かない。**
- テスト: `xperia-z4-motion-camera/tools/run-tests.sh`（Android 5.0 と 7.1 の両方で全テスト。現在 410 件すべて通る。壊さない）。Maven Central から必要な jar を1度だけ取得する。
- force push はしない。

## 絶対ルール
- 次の操作は、実行する前に「何をするか・何が起きるか・どう戻すか」を日本語で説明し、依頼主の了承を得てから行う：端末へのインストール、`dpm set-device-owner`、端末の設定変更、アプリの無効化、再起動。
- 端末の初期化、アンインストール（録画が消える）、アカウントの削除は、あなたが実行しない。必要なら依頼主に頼む。
- チャレンジタブレット（ベネッセの管理下の端末）には一切操作しない。対象は Xperia Z4（Android 5.0）と、実験用の SOYES XS15（Android 8.1）など依頼主自身の端末だけ。
- Google アカウントは最終的に端末に残す（設定のときだけ一時的に外す。下の手順 4）。

## 作るもの
1. デバイス管理の受け口: `AdminReceiver`（DeviceAdminReceiver を継承）、`res/xml/device_admin.xml`、マニフェストへの登録（BIND_DEVICE_ADMIN）。
2. 切り替え画面 `KioskHomeActivity`（HOME / DEFAULT の intent-filter を持つホーム画面）。表示するのは「見守りカメラ」（MainActivity を開く）と「管理（暗証番号）」の2つだけ。大きなボタン、日本語。
3. キオスク動作 — このアプリがデバイスオーナーのときだけ有効にする。そうでないときは今までどおり動き、何も変えない。
   - `setLockTaskPackages`（自分のアプリだけ）、HOME の `addPersistentPreferredActivity`（KioskHomeActivity）、自分の各画面で `startLockTask()`。
   - API 23 以上: `setStatusBarDisabled(true)` と `setKeyguardDisabled(true)`。Z4 は Android 5.0（API 21）なので API レベルで分岐する。
   - ユーザー制限: `DISALLOW_INSTALL_UNKNOWN_SOURCES`、`DISALLOW_ADD_USER`。**使わない**: `DISALLOW_MODIFY_ACCOUNTS`（Google アカウントを入れ直すため）、`DISALLOW_INSTALL_APPS` と `DISALLOW_DEBUGGING_FEATURES`（adb での更新を残すため）、`DISALLOW_FACTORY_RESET`（初期化を最終の復旧手段として残すため）。
   - **更新導入との両立**: アプリの更新導入（ブラウザから送る／インターネットから取得）で出る標準のインストーラ画面が、ロックタスク中でも出て、承認して更新できること。出ない場合は、更新の間だけロックタスクを一時解除する、などの対策を入れ、README に書く。
   - 監視・録画・HTTP/HTTPS・稼働記録・起動時の自動開始・更新導入は今までどおり動くこと。
   - `KeepFront` と矛盾しないこと（ロックタスク中は背面に回らないので余計な再起動をしない。一時解除中に設定画面を開いていても、10分たつまで引き戻さない）。
   - 端末を起動したら、自動で切り替え画面が出てロックタスクに入ること。
4. 解除の方法（必須）
   - 管理画面には暗証番号（4〜8桁）。キオスクを最初に有効にするときに設定させる。保存するのはソルト付きハッシュだけ（PBKDF2WithHmacSHA1 など API 21 で使える方式）。5回間違えたら15分ロック。
   - 「一時解除（10分）」: `stopLockTask` し、ステータスバーとキーガードの無効化も一時的に戻す。その間は端末の設定画面（Google アカウントの追加、Wi-Fi など）を開けること。管理画面に「端末の設定を開く」ボタンを置く。10分後に、または「キオスクに戻る」ボタンで元に戻す。
   - 「キオスクを完全に解除」: 制限と HOME の固定を消してから `stopLockTask`、`clearDeviceOwnerApp`。確認ダイアログを2回出す。
   - 暗証番号を忘れたときの最終手段は初期化。README に明記する。
5. LAN のブラウザ設定や外出先の HTTPS からは、キオスクの解除や暗証番号の変更をできないようにする（端末の前にいる人だけができる）。ブラウザの状態表示に「キオスク: 有効 / 無効」を出すのはよい（`/api/status` は追加のみ。既存項目は変えない）。
6. README.md に日本語の節「キオスク化（専用端末）」を追加する。内容: しくみ、準備、adb の手順、解除の方法、注意点（Google アカウントは設定のときだけ一時的に外してあとで入れ直せる、チャレンジタブレットには使えない、Android 5 では通知欄の無効化 API がない、更新導入との関係 など）。
7. JUnit テストを追加する。対象は純 Java に切り出せる部分（暗証番号のハッシュ照合と試行回数によるロック、一時解除の時間切れ判定、API レベルごとにどの機能を有効にするかの判定）。

## 端末での作業（順番を守る。各手順の前に説明して了承を得る）
0. 準備確認: パソコンの adb（なければ Android SDK Platform-Tools の入れ方を説明）、端末の USB デバッグ、`adb devices`。
1. まず実験機（SOYES など）か Android エミュレータで全部を通す。Z4 は最後にする。
2. 端末の情報（Android の版・機種）を取る。録画を `adb pull` でパソコンに保存する（`Android/data/com.z4motioncam/` 以下と SD カード側）。
3. `adb install -r` で新しい APK に更新する。署名が違うと言われたら止まって相談する（アンインストールすると録画と設定が消えるため）。
4. `adb shell dumpsys account` でアカウントを確認する。Google アカウントは最終的に残す。
   (a) まず依頼主に、端末の「設定 > アカウント」から Google アカウントを一時的に削除してもらう（あなたは削除しない。削除しても Google 側のデータ（メール・写真・連絡先など）は消えず、入れ直せば同期し直されることを、説明してから頼む）。
   (b) 手順 5 の `dpm set-device-owner` を実行する。
   (c) 成功したら、キオスクの管理画面で「一時解除」→「端末の設定を開く」から、依頼主に同じ Google アカウントを入れ直してもらう。
   (d) 入れ直したあとも、デバイスオーナーのままでキオスクが動くことを確認する。
   削除を依頼主が望まない場合だけ、代わりに Google のアカウント関連のアプリ（`com.google.android.gms` など）を `pm disable-user` で一時的に止め、設定が終わったらすぐ `pm enable` で戻す方法を試してよい。実行前に「Android の版によっては通らないこと」と「戻し方」を説明して、了承を得ること。
5. `adb shell dpm set-device-owner com.z4motioncam/.AdminReceiver`
6. 動作確認: 再起動すると切り替え画面が出る／ホーム・履歴・通知欄から抜けられない／監視・録画・LAN と外出先からの視聴が動く／**更新導入（ブラウザから送る）でインストーラが出て更新できる**／暗証番号で一時解除（設定画面が開ける・10分で戻る）と完全解除ができる（完全解除 → もう一度設定、まで一度試す）／Google アカウントが入っている状態で、再起動後もキオスクが有効なまま。
7. 何を設定したかと戻し方を、日本語で報告する。

## 怪しいアプリの調査（キオスクとは別の作業。勝手に無効化しない）
- 次を調べ、怪しい候補を「理由・根拠」つきの一覧にして依頼主に見せる: `adb shell pm list packages -f -s`（出荷時から入っているもの）と `-3`（あとから入れたもの）、各アプリの権限（`dumpsys package`）、通信の様子（`dumpsys netstats`、`/proc/net/tcp` と `tcp6`）、既知の不審なもの（adups、gota、FOTA 系など）。
- 無効化するのは、依頼主が了承したものだけ。`adb shell pm disable-user --user 0 <パッケージ>` を使い、無効化したものと戻し方（`pm enable`）を記録したファイルを、リポジトリの外に残す。SystemUI、設定、電話、Wi-Fi、ランチャー、Google のアカウント関連など、端末に必須の部品は無効化しない。
- 前提として、キオスク化しても、裏で動くシステムアプリや OS に組み込まれた不正なコードは止められない。過大な説明はしない。

## 完了の条件
- テストが全部通る（`tools/run-tests.sh`）。
- 汎用 APK を `release/` に置き、個人用 APK はリポジトリの外に作る。
- README を更新し、ブランチに push する（IP と署名鍵は含めない）。
- 端末作業の結果と戻し方を、日本語で報告する。
