"""faster-whisper による文字起こし（CPU / int8）。"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .errors import Cancelled

log = logging.getLogger(__name__)


def load_whisper(model_dir: Path, cfg: dict, threads: int):
    from faster_whisper import WhisperModel

    return WhisperModel(
        str(model_dir),
        device="cpu",
        compute_type=str(cfg.get("whisper_compute_type", "int8")),
        cpu_threads=threads,
    )


def transcribe(
    model,
    audio: np.ndarray,
    cfg: dict,
    cancel: threading.Event,
    report: Callable[[float | None, str], None],
    label: str,
) -> list[dict]:
    """音声全体を文字起こしし、単語タイムスタンプ付きセグメントのリストを返す。

    セグメントはジェネレータで1つずつ得られるので、その都度停止要求を確認する。
    """
    duration = len(audio) / 16000.0
    if duration < 0.5:
        return []
    lang = cfg.get("language") or None
    segments, info = model.transcribe(
        audio,
        language=lang,
        beam_size=int(cfg.get("whisper_beam_size", 5)),
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,  # 長時間録音での繰り返し幻覚を抑える
    )
    log.info("%s: language=%s duration=%.1fs", label, info.language, duration)
    out = []
    report(0.0, f"{label}（0 / {duration:.0f} 秒）")
    for seg in segments:
        if cancel.is_set():
            raise Cancelled()
        out.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "words": [
                    {"start": float(w.start), "end": float(w.end), "word": w.word}
                    for w in (seg.words or [])
                ],
            }
        )
        report(min(1.0, seg.end / duration), f"{label}（{seg.end:.0f} / {duration:.0f} 秒）")
    return out
