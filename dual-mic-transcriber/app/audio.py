"""録音 WAV の読み込みと 16kHz への変換。

faster-whisper の decode_audio() は PyAV のバージョン差異で壊れることがある
（例: faster-whisper 1.2.1 と PyAV 18 の組み合わせ）ため使わない。
自前で書き出した 16bit PCM WAV を標準ライブラリで読み、soxr で高品質に
リサンプリングする。ブロック単位で処理するので長時間録音でもメモリ効率が良い。
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

TARGET_SR = 16000
_BLOCK = 1 << 18  # 約5秒 @48kHz


def load_wav_16k(path: Path | str) -> np.ndarray:
    import soxr

    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"16bit PCM WAV のみ対応: {path}")
        sr = wf.getframerate()
        ch = wf.getnchannels()
        stream = None if sr == TARGET_SR else soxr.ResampleStream(sr, TARGET_SR, 1, dtype="float32")
        parts = []
        while True:
            raw = wf.readframes(_BLOCK)
            last = len(raw) < _BLOCK * 2 * ch
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            if stream is not None:
                x = stream.resample_chunk(x, last=last)
            if len(x):
                parts.append(x.astype(np.float32, copy=False))
            if last:
                break
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
