# LocalCoder — 日本語で頼むと、この PC の中だけでプログラムを作る相棒

タチコマとは別のアプリ。`LocalCoder.exe` を起動して日本語で頼むと、ローカルのモデルが

1. 作業フォルダの中身を読み
2. プログラムを書き・直し
3. 実行して確かめ、エラーが出たら直し
4. 頼まれれば PyInstaller で `.exe` に固めて `dist\` に置き
5. 何を作ったかを日本語で報告する

を、道具 (ファイルの読み書き・検索・コマンド実行) を使って最後までやる。速さより賢さを取る作り。

## 初回の準備 (画面の案内に答えるだけ)

1. **置き場所**: モデルを置くドライブとフォルダを選ぶ (SSD がおすすめ。空きと SSD/HDD の別を表示する)
2. **モデル** (大きいほど賢く、遅い。目安は i7-7700・メモリ 16GB・GTX 1060 6GB の場合)

   | 候補 | 大きさ | 目安 |
   |---|---|---|
   | Qwen3-Coder 30B-A3B (標準) | 約 19GB | メモリと GPU にだいたい載る。数トークン/秒 |
   | gpt-oss 120B (大) | 約 64GB | メモリに入らない分は SSD から読みながら動く。1 トークンに数秒のことも |
   | GLM-4.5-Air 106B (最大) | 約 70GB | 道具を使うのが得意。1 回の返事に数十分のことも |
   | Qwen3 4B (軽い) | 約 2.6GB | 速い。1 ファイルの小さな道具くらいまで |
   | Qwen2.5-Coder 1.5B (試用) | 約 1GB | 動作確認用。簡単なことしかできない |

3. **.exe にする道具**: python.org の正式な Python を置き場所の中 (`python\`) に入れ、PyInstaller を足す
   (PC にもともと入っている Python には触れない。Windows の「アプリ」一覧に Python が載る)

llama.cpp は GitHub の最新版を取ってくる。GPU は **CUDA 版 → Vulkan 版 → CPU 版** の順に試し、
読み込めても最初の推論で落ちる (古い NVIDIA ドライバの「PTX … unsupported toolchain」など) ときは次の版に切り替える。
モデルは mmap で開くので、メモリに入りきらない分は SSD から読まれる。MoE モデルは専門家の重みを CPU 側に、
共通部分と文脈を GPU 側に置く (`--cpu-moe`)。

## 使い方

```
LocalCoder.exe                      対話 (日本語で頼む)
LocalCoder.exe --setup              置き場所・モデルを選び直す
LocalCoder.exe --task "…" --yes     1 つの頼みをやり切って終わる
```

対話中: `/new` (話を切り替える) `/cd フォルダ` (作業フォルダ) `/auto` (コマンドを毎回確かめない) `/status` `/exit`

- コマンドを実行する前に確かめる (`a` で以後は聞かない)。ファイルは作業フォルダの外には触れない
- 外へ出る通信は、準備のときの取得 (llama.cpp・モデル・Python) だけ。頼みごと・コード・結果は外に出ない
- 記録: `<置き場所>\logs\llama-server.log`

## 正直なところ

- [合理的推定] 手元で動く公開モデルは、Claude Code の裏のモデルより実装力がはっきり劣る。1 ファイル〜数ファイルの道具
  (ファイル整理・集計・変換・簡単な画面つきツール) なら、何度か直しを挟んで `.exe` まで届くことが多い。
  大きなアプリは、小さく分けて頼む方がよい
- 速さは SSD・メモリ・GPU しだいで大きく変わる。大きいモデルほど、1 回の頼みに時間がかかる

## 作り方

```
pip install pyinstaller
pyinstaller --onefile --console --name LocalCoder LocalCoder.py
python -m unittest discover -s tests
```
GitHub Actions ("Build LocalCoder.exe") が exe を作り、実物の llama.cpp (CPU 版) と小さなモデルで
「日本語で頼む → ファイルができる」までと、`.exe` にする道具が動くことを確かめる。
