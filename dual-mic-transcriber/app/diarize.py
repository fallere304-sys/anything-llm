"""時間軸の整列と、音量比較による話者識別。

考え方:
  マイク1は話者Aの近く、マイク2は話者Bの近くに置く。ある瞬間に
  「どちらのマイクに対して相対的に大きく入っているか」で話者を決める。

  ただしマイクごとに感度（ゲイン）が異なるため、生の音量 (dBFS) を
  そのまま比べると感度の高い側に偏る。そこで各マイクの雑音下限
  （録音全体の下位10%点）を基準にした「雑音下限からの持ち上がり量」
  (≒SNR) を比べる。ゲインは信号と雑音に等しく掛かるので dB の差を
  取ると打ち消される。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
LABEL_SILENCE = -1
LABEL_A = 0
LABEL_B = 1
LABEL_UNSURE = 2
_LABEL_NAME = {LABEL_A: "A", LABEL_B: "B", LABEL_UNSURE: "?"}


@dataclass
class SpeakerTimeline:
    frame_sec: float
    labels: np.ndarray       # フレームごとのラベル (int8)
    diff_db: np.ndarray      # 平滑化後の (マイク1 SNR - マイク2 SNR)
    snr1: np.ndarray
    snr2: np.ndarray
    floor1_db: float
    floor2_db: float

    def segments(self) -> list[dict]:
        """無音以外の連続区間を [{start, end, speaker}] で返す（録音開始からの秒）。"""
        out = []
        for lab, s, e in _runs(self.labels):
            if lab == LABEL_SILENCE:
                continue
            out.append(
                {
                    "start": round(s * self.frame_sec, 3),
                    "end": round(e * self.frame_sec, 3),
                    "speaker": _LABEL_NAME[int(lab)],
                }
            )
        return out

    def speaker_for_span(self, start: float, end: float) -> str:
        """[start, end) 秒の区間で優勢な話者を返す。"""
        n = len(self.labels)
        i0 = int(np.clip(np.floor(start / self.frame_sec), 0, n))
        i1 = int(np.clip(np.ceil(end / self.frame_sec), 0, n))
        if i1 <= i0:
            i1 = min(n, i0 + 1)
            i0 = max(0, i1 - 1)
        if i1 <= i0:
            return "?"
        lab = self.labels[i0:i1]
        va = int(np.sum(lab == LABEL_A))
        vb = int(np.sum(lab == LABEL_B))
        if va != vb:
            return "A" if va > vb else "B"
        # 票が拮抗（または全て無音/判別不能）なら音量差の平均の符号で決める
        d = float(np.mean(self.diff_db[i0:i1]))
        if d > 0:
            return "A"
        if d < 0:
            return "B"
        return "?"


def align_tracks(
    audio1: np.ndarray, start1: float, audio2: np.ndarray, start2: float, sr: int = SR
) -> tuple[np.ndarray, np.ndarray, float]:
    """2本の音声の開始時刻を揃え、同じ長さに切り詰める。

    start1/start2 は各ストリームの最初のサンプルの取り込み時刻 (秒, 共通時計)。
    遅く始まった方の開始時刻を時間軸の 0 とし、早く始まった方の先頭を削る。
    戻り値: (aligned1, aligned2, 先頭のずれ秒)
    """
    t0 = max(start1, start2)
    trim1 = int(round((t0 - start1) * sr))
    trim2 = int(round((t0 - start2) * sr))
    a = audio1[trim1:]
    b = audio2[trim2:]
    n = min(len(a), len(b))
    return a[:n], b[:n], abs(start1 - start2)


def _frame_db(x: np.ndarray, hop: int) -> np.ndarray:
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    fr = x[: n * hop].astype(np.float64).reshape(n, hop)
    rms = np.sqrt(np.mean(fr * fr, axis=1))
    return (20.0 * np.log10(rms + 1e-10)).astype(np.float32)


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or len(x) == 0:
        return x
    k = np.ones(width, dtype=np.float64) / width
    pad = width // 2
    xp = np.pad(x.astype(np.float64), (pad, width - 1 - pad), mode="edge")
    return np.convolve(xp, k, mode="valid").astype(np.float32)


def _runs(labels: np.ndarray):
    """同じ値が続く区間を (値, 開始idx, 終了idx(排他)) で列挙。"""
    n = len(labels)
    if n == 0:
        return []
    change = np.flatnonzero(np.diff(labels)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [n]))
    return [(labels[s], int(s), int(e)) for s, e in zip(starts, ends)]


def _absorb_short_runs(labels: np.ndarray, min_frames: int) -> np.ndarray:
    """min_frames 未満の短い発話区間を隣接する長い区間のラベルで塗りつぶす。

    無音区間は短くても保持する（発話の区切りとして意味があるため）。
    """
    labels = labels.copy()
    if min_frames <= 1:
        return labels
    for _ in range(10):  # 収束するまで数回繰り返す
        runs = _runs(labels)
        changed = False
        for i, (lab, s, e) in enumerate(runs):
            if lab == LABEL_SILENCE or e - s >= min_frames:
                continue
            prev_lab = runs[i - 1][0] if i > 0 else None
            next_lab = runs[i + 1][0] if i + 1 < len(runs) else None
            prev_len = runs[i - 1][2] - runs[i - 1][1] if i > 0 else 0
            next_len = runs[i + 1][2] - runs[i + 1][1] if i + 1 < len(runs) else 0
            cands = [
                (ln, lb)
                for lb, ln in ((prev_lab, prev_len), (next_lab, next_len))
                if lb is not None and lb != LABEL_SILENCE
            ]
            if not cands:
                continue
            new = max(cands)[1]
            if new != lab:
                labels[s:e] = new
                changed = True
        if not changed:
            break
    return labels


def diarize_by_volume(a: np.ndarray, b: np.ndarray, cfg: dict, sr: int = SR) -> SpeakerTimeline:
    frame_ms = float(cfg.get("frame_ms", 50))
    hop = max(1, int(sr * frame_ms / 1000))
    frame_sec = hop / sr

    db1 = _frame_db(a, hop)
    db2 = _frame_db(b, hop)
    n = min(len(db1), len(db2))
    db1, db2 = db1[:n], db2[:n]
    if n == 0:
        empty = np.zeros(0, dtype=np.float32)
        return SpeakerTimeline(frame_sec, np.zeros(0, dtype=np.int8), empty, empty, empty, 0.0, 0.0)

    floor1 = float(np.percentile(db1, 10))
    floor2 = float(np.percentile(db2, 10))
    snr1 = db1 - floor1
    snr2 = db2 - floor2

    smooth = max(1, int(round(float(cfg.get("smoothing_ms", 400)) / frame_ms)))
    snr1_s = _moving_average(snr1, smooth)
    snr2_s = _moving_average(snr2, smooth)
    diff = snr1_s - snr2_s

    vad_th = float(cfg.get("vad_threshold_db", 8.0))
    margin = float(cfg.get("dominance_margin_db", 3.0))
    # 発話判定は平滑化前の値で行い、語頭・語尾の取りこぼしを減らすため軽く広げる
    active = np.maximum(snr1, snr2) > vad_th
    active = _moving_average(active.astype(np.float32), 3) > 0

    labels = np.full(n, LABEL_SILENCE, dtype=np.int8)
    labels[active & (diff > margin)] = LABEL_A
    labels[active & (diff < -margin)] = LABEL_B
    labels[active & (np.abs(diff) <= margin)] = LABEL_UNSURE

    min_frames = int(round(float(cfg.get("min_segment_ms", 300)) / frame_ms))
    labels = _absorb_short_runs(labels, min_frames)

    log.info(
        "diarize: frames=%d floor1=%.1fdB floor2=%.1fdB A=%d B=%d ?=%d silence=%d",
        n, floor1, floor2,
        int(np.sum(labels == LABEL_A)), int(np.sum(labels == LABEL_B)),
        int(np.sum(labels == LABEL_UNSURE)), int(np.sum(labels == LABEL_SILENCE)),
    )
    return SpeakerTimeline(frame_sec, labels, diff, snr1, snr2, floor1, floor2)
