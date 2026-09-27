"""Whisper を (音声クリップ, 字幕) の対で LoRA 微調整し、基盤に統合して保存する。

    python train_whisper.py --base openai/whisper-small --train train.jsonl --out merged/ --language ja

train.jsonl の 1 行: {"audio": "clip.wav (16kHz mono)", "text": "字幕", "weight": 1.0}

GTX 1060 (Pascal) 向けの既定:
- whisper-small (244M) を float32 で読み、LoRA (q_proj, v_proj / エンコーダ・デコーダ両方) だけを学習
- バッチ 1 + 勾配累積。学習率は小さめ (微増狙い・破壊的忘却を避ける)
- 最後に LoRA を基盤へ統合 (merge) して保存 → ct2-transformers-converter で faster-whisper 形式にする

依存: requirements.txt (torch / transformers / peft) + numpy
"""

import argparse
import json
import math
import os
import random
import sys
import time
import wave

LANG_NAMES = {"ja": "japanese", "en": "english", "zh": "chinese", "ko": "korean"}


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--language", default="ja")
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--max-minutes", type=float, default=120)
    ap.add_argument("--targets", default="q_proj,v_proj")
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def read_wav(path):
    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: 16kHz mono 16bit の wav が必要です")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def load(args):
    import torch
    from transformers import AutoFeatureExtractor, AutoTokenizer, WhisperForConditionalGeneration
    fe = AutoFeatureExtractor.from_pretrained(args.base)
    tok = AutoTokenizer.from_pretrained(args.base)
    if hasattr(tok, "set_prefix_tokens"):
        tok.set_prefix_tokens(language=LANG_NAMES.get(args.language, args.language), task="transcribe")
    model = WhisperForConditionalGeneration.from_pretrained(args.base, dtype=torch.float32)
    return fe, tok, model


def build_labels(tok, text, decoder_start):
    ids = tok(text).input_ids
    if tok.eos_token_id is not None and (not ids or ids[-1] != tok.eos_token_id):
        ids = ids + [tok.eos_token_id]
    if ids and ids[0] == decoder_start:
        ids = ids[1:]      # モデルが labels を右シフトして開始トークンを付けるため
    return ids


def main(argv=None):
    args = parse_args(argv)
    import torch
    from peft import LoraConfig, get_peft_model

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    with open(args.train, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    t0 = time.time()
    fe, tok, model = load(args)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    start = model.config.decoder_start_token_id
    print(f"[load] {args.base} on {device} ({time.time() - t0:.0f}s)", flush=True)

    data = []
    for r in rows:
        try:
            audio = read_wav(r["audio"])
        except (OSError, ValueError) as e:
            print(f"[skip] {e}", flush=True)
            continue
        feats = fe(audio, sampling_rate=16000, return_tensors="pt").input_features
        labels = build_labels(tok, r["text"], start)
        if labels:
            data.append((feats, labels, float(r.get("weight", 1.0))))
    if not data:
        print("有効な標本がありません", file=sys.stderr)
        return 2
    print(f"[data] {len(data)} 件", flush=True)

    alt = "|".join(args.targets.split(","))
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
                                             target_modules=rf".*\.({alt})$"))
    model.print_trainable_parameters()
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr)
    total = max(1, math.ceil(len(data) * args.epochs / args.accum))
    warm = max(1, total // 10)
    deadline = t0 + args.max_minutes * 60
    model.train()
    step = micro = 0
    running = 0.0
    early = False
    for epoch in range(args.epochs):
        random.shuffle(data)
        for feats, labels, w in data:
            if time.time() > deadline:
                early = True
                break
            out = model(input_features=feats.to(device), labels=torch.tensor([labels], device=device))
            loss = out.loss * w / args.accum
            loss.backward()
            running += loss.item()
            micro += 1
            if micro % args.accum == 0:
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                lr = args.lr * ((step + 1) / warm if step < warm else
                                max(0.05, 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total - warm)))))
                for g in opt.param_groups:
                    g["lr"] = lr
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                print(f"[train] epoch {epoch + 1} step {step}/{total} loss {running:.4f}", flush=True)
                running = 0.0
        if early:
            break
    if micro % args.accum:
        opt.step()
        opt.zero_grad(set_to_none=True)
        step += 1

    merged = model.merge_and_unload()
    os.makedirs(args.out, exist_ok=True)
    merged.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    fe.save_pretrained(args.out)
    with open(os.path.join(args.out, "tachikoma_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"base": args.base, "samples": len(data), "steps": step, "stopped_early": early,
                   "minutes": (time.time() - t0) / 60}, f)
    print(f"[done] {args.out} steps={step}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
