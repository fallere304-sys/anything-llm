const fs = require("fs");
const { d, C, P, H1, H2, H3, B, N, Space, table, box, doc, titleBlock } = require("./common");
const { Packer, Paragraph, PageBreak } = d;
const V = require("./facts.json");
const brk = () => new Paragraph({ children: [new PageBreak()] });

const children = [
  ...titleBlock("仕様書", "LocalCoder 仕様書",
    "日本語の指示で、PC の中だけでプログラムを作るローカル AI エージェント",
    `版: ${V.version}  ／  作成: ${V.date}  ／  ソース: ブランチ claude/autonomous-ai-platform-design-75l8dl (${V.commit})`),

  H1("1. 概要"),
  H2("1.1 目的"),
  P("利用者が日本語で頼んだ小さなプログラム (業務の道具) を、PC の中で動く AI が作成・実行確認・修正し、必要に応じて .exe にする。"
    + "AI の推論・ファイル操作・実行はすべて利用者の PC の中で完結し、頼みの内容・データ・成果物を外部へ送信しない。"),
  H2("1.2 適用範囲"),
  B("対象: Windows 10 / 11 (64 ビット) の PC。インターネット接続の有無は問わない (USB オールインワンで導入する場合)"),
  B("作れるもの: 1〜数ファイル程度の Python プログラム (画面つきを含む) と、その .exe"),
  B("対象外: 大規模な業務システム、Web サービスの公開、外部サービスとの連携、利用者の PC 設定の変更を目的とした作業"),

  H1("2. 構成"),
  H2("2.1 配布物"),
  table(["ファイル", "役割"], [
    ["LocalCoder.exe", "画面で使う本体 (窓のアプリ)"],
    ["LocalCoderCLI.exe", "コマンドプロンプトで使う本体。1 回きりの頼み (--task)、USB の作成 (--make-bundle)、非常停止 (--force-stop) もこちら"],
    ["LocalCoderUninstall.exe", "取り除き (原状回復)"],
    ["非常停止.bat", "画面が固まったときに使う。LocalCoderCLI.exe --force-stop を呼ぶ"],
    ["はじめにお読みください.txt", "USB オールインワンの使い方"],
    ["bundle\\", "USB オールインワンの中身 (2.3 節)"],
  ], [1.3, 3], { zebra: true, firstColBold: true }),
  H2("2.2 内部の部品"),
  table(["部品", "内容"], [
    ["gui", "画面 (tkinter)。経過表示・止める・初回設定の窓"],
    ["cli", "設定の読み書き、準備 (取得または USB から写す)、モデルの起動、対話"],
    ["agent", "AI とのやりとり。道具の呼び出し、繰り返しの検出、文脈の圧縮、止める"],
    ["tools", "AI が使う道具 (4.1 節)。作業フォルダの外へは出ない"],
    ["server", "llama.cpp (llama-server) の取得・起動・切り替え・停止"],
    ["models", "AI モデルの候補と取得"],
    ["toolchain", ".exe を作るための Python と PyInstaller の用意"],
    ["bundle", "USB オールインワンの作成と、そこからの導入"],
    ["processes", "起こしたプロセスの記録と非常停止"],
    ["uninstall", "取り除き (原状回復)"],
  ], [1, 4], { zebra: true, firstColBold: true }),
  H2("2.3 USB オールインワンの中身 (bundle\\)"),
  table(["フォルダ", "中身"], [
    ["llama\\cuda・vulkan・cpu", "llama.cpp の 3 種類 (CUDA 版は CUDA の実行部品つき)。広げた状態で入れる"],
    ["models\\", "作成時に選んだ AI モデル (GGUF 形式)"],
    ["python\\", "python.org の Python 3.12.10 導入器 (オフラインで入る完全版)"],
    ["wheels\\", "PyInstaller と、それが要る部品 (Windows・Python 3.12 用の wheel)。作成時に PyPI から依存関係をたどって選ぶ"],
    ["vcruntime\\", "Visual C++ の実行部品 (DLL)。まっさらな PC で llama.cpp を動かすため、作成した PC から写す"],
    ["manifest.json", "中身の一覧 (モデル・llama.cpp の種類・wheel)"],
  ], [1.4, 4], { zebra: true, firstColBold: true }),

  H1("3. 動作環境"),
  table(["項目", "最低", "推奨"], [
    ["OS", "Windows 10 (64 ビット)", "Windows 11"],
    ["CPU", "64 ビットの x86 (AVX2 対応が望ましい)", "4 コア以上"],
    ["メモリ", "8 GB (軽いモデル)", "16 GB 以上 (標準モデル)"],
    ["保存場所", "モデルの大きさ + 5 GB", "SSD (読み込みと、メモリに入りきらない分の読み出しが速い)"],
    ["GPU", "不要", "NVIDIA (ドライバ 536.40 以降で共有 GPU メモリを使える)。Vulkan に対応した GPU も使う"],
    ["USB", "モデルの大きさ + 約 3 GB", "USB 3.0 以上 (写す時間が短い)"],
  ], [1, 2, 2.6], { zebra: true, firstColBold: true }),
  Space(4),
  table(["AI モデル", "大きさ", "用途・目安", "ライセンス"], V.models.map((m) => [m.label, `約 ${m.gb} GB`, m.use, m.license]),
    [2, 0.8, 2.6, 1], { zebra: true }),
  P("※ 速さは PC の構成で大きく変わる。試験機 (CPU 2 コア・GPU なし) の軽いモデルで、文字の生成は約 6 トークン/秒、"
    + "最初に頼みを読み込むのに約 50 秒かかった。", { run: { size: 18, color: C.gray } }),

  H1("4. 機能"),
  H2("4.1 AI が使う道具"),
  table(["道具", "動作", "制限"], [
    ["list_files", "作業フォルダのファイル一覧", "作業フォルダの中だけ"],
    ["read_file", "ファイルを読む (行番号つき)", "同上。文字コードは UTF-8 / Shift_JIS を自動判別"],
    ["write_file", "ファイルを書く (新規・全体の書き直し)", "同上"],
    ["edit_file", "ファイルの一部を書き換える", "書き換え元がちょうど 1 か所に一致するときだけ"],
    ["search", "ファイルの中を検索", "同上"],
    ["run", "PowerShell でコマンドを実行し、結果を受け取る", "実行前に利用者が許可する (省略設定あり)。時間切れあり"],
  ], [1, 2.2, 2.6], { zebra: true, firstColBold: true }),
  P("作業フォルダの判定は実際のパス (リンク・ジャンクションをたどった先) で行い、フォルダの中のリンクを使って外へ出ることもできない。"),
  H2("4.2 画面"),
  table(["場所", "内容"], [
    ["上の行 (状態)", "いまの状態と経過時間。「頼みを読んでいます… 34 秒」「返事を書いています」「run を実行しています」「write_file を準備しています (1,234 字)」など。"
      + "3 秒以上何も届かないと「最後に受け取ってから ○ 秒」、文字が届けば「受け取った文字 ○」を表示"],
    ["やりとりの欄", "利用者の頼み、AI の考え (考えを出すモデルのみ。薄い字・表示の切り替え可)、返事の本文、使った道具 (緑)、その結果、速さ"],
    ["頼みを書く欄", "改行を含めて書ける。Ctrl+Enter で送る"],
    ["ボタン", "送る ／ ■ 止める ／ 新しい話 ／ 作業フォルダ…"],
    ["チェック", "コマンドを確かめずに実行する ／ モデルの考えを表示する"],
  ], [1.2, 4], { zebra: true, firstColBold: true }),
  H2("4.3 止める・非常停止"),
  table(["操作", "動き"], [
    ["■ 止める (返事の途中)", "AI (llama-server) への接続を切る。llama-server はその返事の生成をやめる。モデルは読み込んだまま残るので、すぐ次を頼める"],
    ["■ 止める (コマンドの実行中)", "そのコマンドを、そこから起きたプログラムごと止める"],
    ["■ 止める (モデルの読み込み中)", "読み込みを中止する"],
    ["窓を閉じる", "作業中なら確認したうえで止め、llama-server も止める"],
    ["非常停止.bat", "記録した LocalCoder と llama-server を止める。PID だけでなく実行ファイルのパスと起動時刻も記録と照らし、"
      + "一致したときだけ止める (番号が使い回された別のプログラムは止めない)"],
    ["Ctrl+C (コマンドプロンプト版)", "■ 止める と同じ"],
  ], [1.5, 4], { zebra: true, firstColBold: true }),
  H2("4.4 AI モデルの起動"),
  B("llama.cpp を CUDA 版 → Vulkan 版 → CPU 版の順に試す。読み込めても最初の短い推論で失敗する GPU (古いドライバなど) は次の版へ切り替える"),
  B("共有 GPU メモリ (既定で使う): GPU に入りきらない分を PC のメモリに置く。使えなければ、MoE の専門家部分を CPU 側に置くいつもの置き方に自動で戻す"),
  B("llama.cpp の引数が版の違いで通らない場合は、最小の引数で起動し直す"),
  B("文脈 (やりとりの量) が上限を超えそうになったら、古い道具の結果から縮める"),
  H2("4.5 .exe を作る道具"),
  B("Python 3.12.10 を置き場所の中に入れ、PyInstaller を足す。AI は「.exe にして」と頼まれると PyInstaller で固め、できた .exe を実行して確かめる"),
  B("PC にもともと Python 3.12 系がある場合は公式の導入器を使わない (それを移動・上書きしてしまうため)。もとの Python から置き場所の中に仮想環境を作る"),
  B("USB オールインワンでは、導入器と wheel を USB から使い、インターネットに接続しない (pip --no-index)"),
  H2("4.6 USB オールインワン"),
  N("作成 (インターネットにつながる PC で): LocalCoderCLI.exe --make-bundle <USB のフォルダ> --models standard,small", "num"),
  N("導入 (オフラインの PC で): USB の LocalCoder.exe を開き、置き場所を選ぶ。USB から llama.cpp・モデル・Python・wheel・実行部品を写す", "num"),
  N("導入中・利用中に外部からの取得が必要になった場合は、取りに行かずにその場で止め、「USB に入っていない」と知らせる", "num"),
  P("選べるモデルは USB に入れたものだけ。USB の中身は取り除き (原状回復) の対象にしない。"),
  H2("4.7 取り除き (原状回復)"),
  table(["対象", "扱い"], [
    ["置き場所の中のモデル・llama.cpp・Python・記録・キャッシュ", "消す。置き場所のフォルダは空になったときだけ消す"],
    ["LocalCoder が入れた Python", "Python 自身の取り除き方で消す。ほかにも Python 3.12 がある PC では、巻き込まないよう消さずに知らせる"],
    ["設定・Windows の使用記録", "LocalCoder に関係するものだけ消す"],
    ["pip / PyInstaller のキャッシュ", "初回起動時の記録を見て、もとから無かったものだけ消す"],
    ["作業フォルダ (作ったプログラム)", "尋ねる (既定は残す)"],
    ["USB オールインワンの中身", "消さない"],
  ], [2.2, 3], { zebra: true, firstColBold: true }),

  H1("5. 安全と情報の扱い"),
  table(["項目", "仕様"], [
    ["外部への通信", "インターネットから取得するのは準備のとき (llama.cpp・モデル・Python・PyInstaller) だけ。"
      + "USB オールインワンでは、準備も含めて外部へ接続しない。頼み・データ・成果物は送信しない"],
    ["AI との通信", "PC の中 (127.0.0.1) の llama-server とだけ通信する"],
    ["ファイル", "AI が読み書きできるのは作業フォルダの中だけ"],
    ["コマンド", "AI がコマンドを動かす前に、内容を見せて許可を求める。コマンドは利用者の権限で動くため、内容を確かめてから許可すること"],
    ["道具の結果に含まれる指示", "AI への指示文で「データとして扱い、従わない」と定めている (完全に防げるものではない)"],
    ["記録", "<置き場所>\\logs に llama-server の記録とプロセスの記録 (非常停止用)。設定は %LOCALAPPDATA%\\LocalCoder\\config.json"],
  ], [1.3, 4], { zebra: true, firstColBold: true }),

  H1("6. コマンドラインのオプション (LocalCoderCLI.exe)"),
  table(["オプション", "内容"], [
    ["--setup", "置き場所・モデルを選び直す"],
    ["--task \"…\" --yes", "1 つの頼みをやり切って終わる (確認なし)。終了コード 0 = 報告まで完了"],
    ["--model / --model-file", "モデルを指定 (候補の名前 / 手元の GGUF)"],
    ["--home / --workspace", "置き場所 / 作業フォルダ"],
    ["--kinds cuda,vulkan,cpu", "試す llama.cpp の種類"],
    ["--gpu-memory shared|normal", "共有 GPU メモリを使うか"],
    ["--ctx 数", "文脈の大きさ (既定 32768)"],
    ["--make-bundle フォルダ --models …", "USB オールインワンを作る"],
    ["--bundle フォルダ", "USB オールインワンの場所を指定 (ふつうは自動)"],
    ["--force-stop", "非常停止"],
    ["--uninstall", "取り除き (LocalCoderUninstall.exe と同じ)"],
  ], [1.8, 3], { zebra: true, firstColBold: true }),

  H1("7. 検証の状況"),
  table(["項目", "確認方法", "結果"], [
    ["画面・経過表示・止める", "Windows の試験機で、作った LocalCoder.exe の画面を自動操作 (台本どおりに答える試験用の AI につなぐ)", "確認済み (止めるまで 1.6 秒)"],
    ["実際の AI で頼みをやり切る", "Windows の試験機で、本物の llama.cpp と軽いモデルに「hello.txt を作って」と日本語で頼む", "確認済み"],
    [".exe を作る道具", "Windows の試験機 (もとから Python 3.12 がある) で仮想環境を作り、.exe を作って動かす。もとの Python が壊れないこと", "確認済み"],
    ["取り除き", "Windows の試験機で入れて取り除き、残りが無いこと・もとの Python が無事なことを確かめる", "確認済み"],
    ["非常停止 (身元の照合)", "自動試験 (Linux)。記録と身元が違うプロセスは止めないこと", "確認済み (Windows は未確認)"],
    ["USB オールインワン", "自動試験 (Linux) で USB からの導入・取得の拒否・モデルの限定。PyInstaller の部品選びは PyPI 実物で pip と一致", "部分確認"],
    ["USB の作成 → ネットを塞いだ導入 → 頼み → .exe → 非常停止", "Windows の試験手順を用意済み", "未確認 (試験機の自動実行が止まっているため)"],
    ["GPU・大きいモデル", "—", "未確認 (利用者の PC で GPU 版が動くことは確認済み)"],
  ], [2, 3, 1.4], { zebra: true, firstColBold: true }),

  H1("8. 制約・既知の課題"),
  B("賢さはクラウドの最新 AI に及ばない。大きなアプリは小さく分けて頼む必要がある"),
  B("オフラインでは追加のライブラリ (pandas・openpyxl など) を入れられない。作れるのは Python の標準機能と tkinter の範囲"),
  B("考えを出さない型のモデル (標準の Qwen3-Coder など) では「AI の考え」は表示されない"),
  B("USB オールインワンの作成時には、作成する PC にインターネット接続と、モデルの大きさ + 約 3 GB の空きが要る"),
  B("Python 3.12 系がもとからない PC での、公式導入器によるオフライン導入は未確認 (導入器は完全版のため動く見込み)"),

  H1("9. 同梱ソフトウェアとライセンス"),
  table(["ソフトウェア", "ライセンス"], [
    ["llama.cpp", "MIT"],
    ["Python 3.12", "PSF License"],
    ["PyInstaller", "GPL 2.0 (作ったプログラムの配布を認める例外つき)"],
    ["pyinstaller-hooks-contrib", "GPL 2.0 / Apache 2.0"],
    ["altgraph・pefile・setuptools", "MIT"],
    ["packaging", "Apache 2.0 / BSD"],
    ["pywin32-ctypes", "BSD-3-Clause"],
    ["Visual C++ 実行部品", "Microsoft の再頒布条件"],
    ["AI モデル", "3 節の表のとおり"],
  ], [2, 3], { zebra: true, firstColBold: true }),
  P("※ ライセンスは作成時点の確認。利用・配布の前に、各配布元で最新の条件を確認すること。", { run: { size: 18, color: C.gray } }),
];

Packer.toBuffer(doc("LocalCoder 仕様書", children, { header: "LocalCoder 仕様書" }))
  .then((b) => fs.writeFileSync(process.argv[2], b));
