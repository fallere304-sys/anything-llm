"""ローカル LLM（llama.cpp / GGUF）による話者識別付き文字起こしの補正（処理案⑥）。

長い会話はコンテキスト長を超えるため、時間窓（既定120秒）ごとに区切って
処理し、結果を連結する。各窓では次の3つを渡す:
  1. 規則ベース統合版（下書き）
  2. マイク1の話者付き文字起こし
  3. マイク2の話者付き文字起こし
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Callable

from .errors import Cancelled
from .merge import Utterance, format_utterances, in_window, time_windows

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "あなたは会話の文字起こしを校正する専門の編集者です。"
    "与えられた資料だけを根拠に、正確で読みやすい話者付き文字起こしを作成します。"
    "要約・意訳・創作は絶対にしません。"
)

USER_TEMPLATE = """同じ会話を2本のマイクで同時に録音し、それぞれを音声認識した結果です。
- マイク1は話者Aの近く、マイク2は話者Bの近くに置かれています。
- 話者ラベルは2本のマイクの音量比較で自動推定したもので、誤りを含むことがあります。
- 一般に、話者Aの発言はマイク1、話者Bの発言はマイク2の認識結果の方が正確です。
- 遠い側のマイクの認識結果には、相手の発言が不正確な形で混入しています。

# 下書き（規則ベースで統合したもの）
{draft}

# マイク1の認識結果
{mic1}

# マイク2の認識結果
{mic2}

# 指示
1. 3つの資料を照合し、誤認識・脱字・重複を補正した1本の文字起こしを作成してください。
2. 話者ラベルが文脈上明らかに誤っている場合のみ修正してください。
3. 時系列順に、1行1発言で、次の形式だけを出力してください:
[mm:ss.s] 話者A: 発言内容
4. 発言を要約したり、資料にない言葉を追加したりしないでください。
5. 文字起こし本文以外（説明・前置き・見出し）は出力しないでください。"""

_LINE_RE = re.compile(r"^\s*\[[0-9:.]+\]\s*話者[AB?？]\s*[:：]")


def load_llm(model_path: Path, cfg: dict, threads: int):
    from llama_cpp import Llama

    return Llama(
        model_path=str(model_path),
        n_ctx=int(cfg.get("llm_n_ctx", 8192)),
        n_threads=threads,
        n_batch=256,
        verbose=False,
    )


def _clean_output(text: str) -> str:
    """モデルが前置きやコードブロックを付けた場合に、形式に合う行だけを残す。"""
    lines = [ln.rstrip() for ln in text.replace("```", "").splitlines()]
    good = [ln.strip() for ln in lines if _LINE_RE.match(ln)]
    return "\n".join(good) if good else text.strip()


def refine(
    llm,
    draft: list[Utterance],
    mic1: list[Utterance],
    mic2: list[Utterance],
    cfg: dict,
    cancel: threading.Event,
    report: Callable[[float | None, str], None],
) -> tuple[str, list[str]]:
    """補正版テキストと警告メッセージのリストを返す。"""
    windows = time_windows([draft, mic1, mic2], float(cfg.get("llm_chunk_seconds", 120)))
    outputs: list[str] = []
    warnings: list[str] = []
    for i, win in enumerate(windows):
        if cancel.is_set():
            raise Cancelled()
        d, m1, m2 = in_window(draft, win), in_window(mic1, win), in_window(mic2, win)
        prompt = USER_TEMPLATE.format(
            draft=format_utterances(d) or "（なし）",
            mic1=format_utterances(m1) or "（なし）",
            mic2=format_utterances(m2) or "（なし）",
        )
        base = f"ローカルAIで補正版を生成中（区間 {i + 1} / {len(windows)}）"
        report(i / len(windows), base)
        text = ""
        failed = False
        try:
            stream = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=float(cfg.get("llm_temperature", 0.2)),
                max_tokens=int(cfg.get("llm_max_tokens", 2048)),
                stream=True,
            )
            n_tok = 0
            for chunk in stream:
                if cancel.is_set():
                    raise Cancelled()
                piece = chunk["choices"][0].get("delta", {}).get("content") or ""
                text += piece
                n_tok += 1
                if n_tok % 20 == 0:
                    report(i / len(windows), f"{base}  生成済み {len(text)} 文字")
        except Cancelled:
            raise
        except Exception as e:
            log.exception("LLM 生成失敗 (window %d)", i)
            failed = True
            warnings.append(f"区間{i + 1}の AI 補正に失敗したため下書きを使用: {e}")
            text = ""
        cleaned = _clean_output(text)
        if not cleaned.strip():
            if not failed:
                warnings.append(f"区間{i + 1}で AI の出力が空だったため下書きを使用しました")
            cleaned = format_utterances(d)
        outputs.append(cleaned)
    report(1.0, "ローカルAIによる補正が完了しました")
    return "\n".join(o for o in outputs if o.strip()), warnings
