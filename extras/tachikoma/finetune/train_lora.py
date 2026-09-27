"""Tachikoma の標本 (JSONL) で LoRA アダプタを学習する。単体でも実行できる。

    python train_lora.py --base google/gemma-4-E2B-it --train train.jsonl --out adapter/ \
        --load-4bit --compute-dtype float32 --gpu-mem-gib 5

GTX 1060 (Pascal, 6GB) 向けの既定:
- 基盤は 4bit (NF4) で読み、載り切らない部分は CPU に逃がす (device_map=auto + max_memory)
- Pascal は bf16 非対応・fp16 演算が極端に遅いので、演算は float32
- バッチ 1 + 勾配累積 + gradient checkpointing
- 損失は assistant の返答部分だけにかけ、標本ごとの重み (weight) を掛ける
- 言語モデル部分の attention 射影だけに LoRA を挿す (視覚・音声エンコーダには触れない)

依存: requirements.txt (torch / transformers / peft / accelerate / bitsandbytes)
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
    ap.add_argument("--base", required=True, help="HF のモデル ID またはローカルディレクトリ")
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--alpha", type=int, default=None)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--max-minutes", type=float, default=150, help="時間予算。超えたらそこまでで保存")
    ap.add_argument("--load-4bit", action="store_true")
    ap.add_argument("--compute-dtype", default="float32", choices=["float32", "float16", "bfloat16"])
    ap.add_argument("--gpu-mem-gib", type=float, default=5)
    ap.add_argument("--cpu-mem-gib", type=float, default=10)
    ap.add_argument("--targets", default="q_proj,k_proj,v_proj,o_proj")
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _ids(x):
    """apply_chat_template の戻り値の揺れ (list / BatchEncoding / dict) を吸収する。"""
    if isinstance(x, dict) or hasattr(x, "keys"):
        x = x["input_ids"]
    if x and isinstance(x[0], list):
        x = x[0]
    return list(x)


def build_example(tok, messages, max_len):
    """プロンプト部分は損失を -100 で無視し、assistant の返答だけを学習する。"""
    prompt = _ids(tok.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=True))
    full = _ids(tok.apply_chat_template(messages, tokenize=True))
    if full[: len(prompt)] != prompt:
        # テンプレートが整合しない場合は返答を直接つなぐ
        reply = tok(messages[-1]["content"], add_special_tokens=False)["input_ids"]
        full = prompt + list(reply) + ([tok.eos_token_id] if tok.eos_token_id is not None else [])
    labels = [-100] * len(prompt) + full[len(prompt):]
    if len(full) > max_len:
        # 返答を優先して残し、プロンプトは先頭 (古い文脈) から削る
        full, labels = full[-max_len:], labels[-max_len:]
    if all(v == -100 for v in labels):
        return None
    return full, labels


def load_model(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.base)
    cuda = torch.cuda.is_available()
    kwargs = {}
    if args.load_4bit and cuda:
        from transformers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=getattr(torch, args.compute_dtype),
            llm_int8_enable_fp32_cpu_offload=True)
        kwargs["device_map"] = "auto"
        kwargs["max_memory"] = {0: f"{args.gpu_mem_gib}GiB", "cpu": f"{args.cpu_mem_gib}GiB"}
        # 量子化されない層 (埋め込み等) は fp16 で保持してメモリを節約する
        kwargs["dtype"] = torch.float16
    elif cuda:
        kwargs["device_map"] = "auto"
        kwargs["max_memory"] = {0: f"{args.gpu_mem_gib}GiB", "cpu": f"{args.cpu_mem_gib}GiB"}
        kwargs["dtype"] = getattr(torch, args.compute_dtype)
    else:
        kwargs["dtype"] = torch.float32
    try:
        model = AutoModelForCausalLM.from_pretrained(args.base, **kwargs)
    except (ValueError, KeyError):
        # マルチモーダル構成で CausalLM として読めない場合
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(args.base, **kwargs)
    return tok, model


def lora_target_regex(model, targets):
    names = [n for n, _ in model.named_modules()]
    alt = "|".join(targets.split(","))
    if any("language_model" in n for n in names):
        return rf".*language_model.*\.({alt})$"
    return rf".*\.({alt})$"


def main(argv=None):
    args = parse_args(argv)
    import torch
    from peft import LoraConfig, get_peft_model

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    rows = load_jsonl(args.train)
    if not rows:
        print("学習標本がありません", file=sys.stderr)
        return 2

    t0 = time.time()
    tok, model = load_model(args)
    print(f"[load] {args.base} ({time.time() - t0:.0f}s)", flush=True)

    examples = []
    for r in rows:
        ex = build_example(tok, r["messages"], args.max_len)
        if ex:
            examples.append((ex[0], ex[1], float(r.get("weight", 1.0))))
    if not examples:
        print("有効な標本がありません", file=sys.stderr)
        return 2
    print(f"[data] {len(examples)} 件 / 平均 {sum(len(e[0]) for e in examples) / len(examples):.0f} tokens",
          flush=True)

    model.config.use_cache = False
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    lcfg = LoraConfig(r=args.rank, lora_alpha=args.alpha or args.rank * 2, lora_dropout=args.dropout,
                      target_modules=lora_target_regex(model, args.targets), task_type="CAUSAL_LM")
    model = get_peft_model(model, lcfg)
    model.print_trainable_parameters()

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    total_steps = max(1, math.ceil(len(examples) * args.epochs / args.accum))
    warmup = max(1, total_steps // 10)

    def lr_at(step):
        if step < warmup:
            return args.lr * (step + 1) / warmup
        return args.lr * max(0.05, 0.5 * (1 + math.cos(math.pi * (step - warmup) / max(1, total_steps - warmup))))

    device = model.get_input_embeddings().weight.device
    deadline = t0 + args.max_minutes * 60
    model.train()
    step, micro, running = 0, 0, 0.0
    stopped_early = False
    for epoch in range(args.epochs):
        random.shuffle(examples)
        for ids, labels, w in examples:
            if time.time() > deadline:
                stopped_early = True
                break
            x = torch.tensor([ids], device=device)
            y = torch.tensor([labels], device=device)
            loss = model(input_ids=x, labels=y).loss * w / args.accum
            loss.backward()
            running += loss.item()
            micro += 1
            if micro % args.accum == 0:
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                print(f"[train] epoch {epoch + 1} step {step}/{total_steps} loss {running:.4f}", flush=True)
                running = 0.0
        if stopped_early:
            break
    if micro % args.accum:
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        step += 1

    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out)
    with open(os.path.join(args.out, "tachikoma_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"base": args.base, "samples": len(examples), "steps": step,
                   "stopped_early": stopped_early, "minutes": (time.time() - t0) / 60}, f)
    print(f"[done] {args.out} steps={step} early_stop={stopped_early}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
