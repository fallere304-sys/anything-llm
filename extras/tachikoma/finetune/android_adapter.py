"""端末 (Android 版タチコマ) の学習データで、端末用の LoRA アダプタを PC で作る。

    python finetune/android_adapter.py tachikoma-train-20260928-120000.zip --llama-cpp C:/llama.cpp

1. 端末で「学習データを書き出す」で作った zip を展開する (train.jsonl・holdout.jsonl・base_model.txt)
2. 端末と同じ基盤モデル (base_model.txt。既定は TinySwallow-1.5B-Instruct) で、PC 版と同じ学習スクリプト
   (train_lora.py) を使って LoRA を学習する。GTX 1060 (6GB) なら 1.5B は 4bit で読んで学習できる [合理的推定]
3. llama.cpp の convert_lora_to_gguf.py で GGUF のアダプタにする
4. できた .gguf を端末に送り、「学習したアダプタを取り込む」で取り込む。端末が PC 版と同じ規則
   (健全性検査と検証標本の採点) で採否を決める。検証標本 (holdout.jsonl) は端末に残っているので、ここでは使わない

学習用の Python には requirements.txt (torch など) が要る (Tachikoma.exe の「学習」を選んでいれば入っている)。
"""

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("export_zip", help="端末で書き出した tachikoma-train-*.zip")
    ap.add_argument("--llama-cpp", required=True, help="llama.cpp を clone したフォルダ (convert_lora_to_gguf.py がある)")
    ap.add_argument("--out", default=None, help="出力する .gguf (既定: zip と同じ場所)")
    ap.add_argument("--base", default=None, help="基盤モデル (既定: zip の base_model.txt)")
    # `--` の後ろは train_lora.py にそのまま渡す (既定: 4bit 読み込み・float32 演算・GPU 5GiB)
    #   python finetune/android_adapter.py export.zip --llama-cpp C:/llama.cpp -- --rank 16 --epochs 3
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough = argv[argv.index("--") + 1:] if "--" in argv else []
    args = ap.parse_args(argv[: argv.index("--")] if "--" in argv else argv)

    work = tempfile.mkdtemp(prefix="tachikoma-android-")
    with zipfile.ZipFile(args.export_zip) as zf:
        zf.extractall(work)
    base = args.base
    if base is None:
        with open(os.path.join(work, "base_model.txt"), encoding="utf-8") as f:
            base = f.read().strip()
    train = os.path.join(work, "train.jsonl")
    with open(train, encoding="utf-8") as f:
        n = sum(1 for line in f if line.strip())
    if n == 0:
        print("学習標本がありません。端末でもう少し話したり、/good や訂正をしてから書き出してください。")
        return 1
    print(f"基盤モデル {base} / 学習標本 {n} 件")

    adapter_dir = os.path.join(work, "adapter")
    extra = passthrough or [
        "--rank", "8", "--epochs", "2", "--lr", "1e-4", "--max-len", "768",
        "--load-4bit", "--compute-dtype", "float32", "--gpu-mem-gib", "5"]
    subprocess.run([sys.executable, os.path.join(HERE, "train_lora.py"), "--base", base, "--train", train,
                    "--out", adapter_dir, *extra], check=True)

    out = args.out or os.path.splitext(os.path.abspath(args.export_zip))[0] + "-lora.gguf"
    base_arg = ["--base", base] if os.path.isdir(base) else ["--base-model-id", base]
    subprocess.run([sys.executable, os.path.join(args.llama_cpp, "convert_lora_to_gguf.py"),
                    "--outfile", out, *base_arg, adapter_dir], check=True)
    print(f"\nできました: {out}\n端末に送って、タチコマの「…」→「学習したアダプタを取り込む」で選んでください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
