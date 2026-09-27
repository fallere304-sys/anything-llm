"""OCR (Vision Encoder-Decoder, 例: kha-white/manga-ocr-base) を (行画像, 正解文字列) で微調整する。

    python train_ocr.py --base kha-white/manga-ocr-base --train train.jsonl --out model/

train.jsonl の 1 行: {"image": "line.png", "text": "正解", "weight": 1.0}

GTX 1060 (Pascal) 向け: float32・小さな学習率で全体を微調整 (モデルが約 1 億パラメータと小さいため)。
画像は学習時に軽く劣化 (縮小→拡大・コントラスト変化) させ、画面・印刷・撮影の違いに強くする。
依存: torch / transformers / pillow
"""

import argparse
import json
import math
import os
import random
import sys
import time


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=64)
    ap.add_argument("--max-minutes", type=float, default=90)
    ap.add_argument("--augment", type=float, default=0.5, help="劣化させる確率")
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def degrade(img, rng):
    from PIL import ImageEnhance
    w, h = img.size
    s = rng.uniform(0.5, 0.9)
    img = img.resize((max(8, int(w * s)), max(8, int(h * s)))).resize((w, h))
    return ImageEnhance.Contrast(img).enhance(rng.uniform(0.6, 1.2))


def main(argv=None):
    args = parse_args(argv)
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoTokenizer, VisionEncoderDecoderModel

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    with open(args.train, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if os.path.exists(r["image"])]
    if not rows:
        print("有効な標本がありません", file=sys.stderr)
        return 2
    t0 = time.time()
    proc = AutoImageProcessor.from_pretrained(args.base)
    tok = AutoTokenizer.from_pretrained(args.base)
    model = VisionEncoderDecoderModel.from_pretrained(args.base)
    if model.config.decoder_start_token_id is None:
        model.config.decoder_start_token_id = tok.cls_token_id if tok.cls_token_id is not None else tok.bos_token_id
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tok.pad_token_id
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).train()
    print(f"[load] {args.base} on {device} ({time.time() - t0:.0f}s) / {len(rows)} 行", flush=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total = max(1, math.ceil(len(rows) * args.epochs / args.batch))
    warm = max(1, total // 10)
    deadline = t0 + args.max_minutes * 60
    step, early = 0, False
    for epoch in range(args.epochs):
        rng.shuffle(rows)
        for i in range(0, len(rows), args.batch):
            if time.time() > deadline:
                early = True
                break
            batch = rows[i:i + args.batch]
            imgs = []
            for r in batch:
                img = Image.open(r["image"]).convert("L").convert("RGB")
                imgs.append(degrade(img, rng) if rng.random() < args.augment else img)
            pixels = proc(imgs, return_tensors="pt").pixel_values.to(device)
            enc = tok([r["text"] for r in batch], padding=True, truncation=True, max_length=args.max_len,
                      return_tensors="pt")
            labels = enc.input_ids.masked_fill(enc.attention_mask == 0, -100).to(device)
            weights = torch.tensor([float(r.get("weight", 1.0)) for r in batch], device=device)
            logits = model(pixel_values=pixels, labels=labels).logits
            # 標本ごとの重み付き損失 (hard の行を重く)
            ce = torch.nn.functional.cross_entropy(logits.transpose(1, 2), labels, ignore_index=-100,
                                                   reduction="none")
            per = ce.sum(1) / (labels != -100).sum(1).clamp(min=1)
            loss = (per * weights).sum() / weights.sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            lr = args.lr * ((step + 1) / warm if step < warm else
                            max(0.05, 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total - warm)))))
            for g in opt.param_groups:
                g["lr"] = lr
            opt.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            print(f"[train] epoch {epoch + 1} step {step}/{total} loss {loss.item():.4f}", flush=True)
        if early:
            break
    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    proc.save_pretrained(args.out)
    with open(os.path.join(args.out, "tachikoma_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"base": args.base, "lines": len(rows), "steps": step, "stopped_early": early,
                   "minutes": (time.time() - t0) / 60}, f)
    print(f"[done] {args.out} steps={step}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
