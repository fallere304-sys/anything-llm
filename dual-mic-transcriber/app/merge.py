"""文字起こしへの話者付与と、規則ベースの統合。

処理案⑤: Whisper の単語タイムスタンプごとに話者タイムライン（③）を引き、
同一話者の連続する単語を1発話にまとめる。これにより Whisper の1セグメント
内で話者が入れ替わっても分割できる。

規則ベース統合（LLM への下書き兼フォールバック）:
  話者Aの発言はマイク1（Aの近く）の文字起こし、話者Bの発言はマイク2の
  文字起こしを優先採用する。近いマイクの方が SNR が高く認識精度が良いため。
  優先側で取りこぼした発言は、もう一方のマイクから補う。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .diarize import SpeakerTimeline

SPEAKER_NAME = {"A": "話者A", "B": "話者B", "?": "話者?"}
# 各話者の発言を優先採用するマイク番号
PREFERRED_MIC = {"A": 1, "B": 2}


@dataclass
class Utterance:
    start: float
    end: float
    speaker: str  # "A" / "B" / "?"
    text: str
    mic: int      # どちらのマイクの文字起こし由来か

    def to_dict(self) -> dict:
        return asdict(self)


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    if h:
        return f"{h:d}:{m:02d}:{s:04.1f}"
    return f"{m:02d}:{s:04.1f}"


def _words_of(segments: list[dict]) -> list[dict]:
    words = []
    for seg in segments:
        ws = seg.get("words") or []
        if ws:
            words.extend(w for w in ws if w["word"].strip())
        elif seg["text"].strip():
            # 単語タイムスタンプが無い場合はセグメント全体を1語として扱う
            words.append({"start": seg["start"], "end": seg["end"], "word": seg["text"]})
    return words


def label_transcript(
    segments: list[dict], timeline: SpeakerTimeline, mic: int, gap_s: float = 1.0
) -> list[Utterance]:
    """1本の文字起こしに話者ラベルを付け、発話単位にまとめる。"""
    utts: list[Utterance] = []
    cur: Utterance | None = None
    for w in _words_of(segments):
        spk = timeline.speaker_for_span(w["start"], w["end"])
        if cur is not None and spk == cur.speaker and w["start"] - cur.end < gap_s:
            cur.text += w["word"]
            cur.end = max(cur.end, w["end"])
            continue
        if cur is not None:
            utts.append(cur)
        cur = Utterance(start=w["start"], end=w["end"], speaker=spk, text=w["word"], mic=mic)
    if cur is not None:
        utts.append(cur)
    for u in utts:
        u.text = u.text.strip()
    return [u for u in utts if u.text]


def _overlap(a: Utterance, b: Utterance) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def rule_based_merge(mic1: list[Utterance], mic2: list[Utterance]) -> list[Utterance]:
    by_mic = {1: mic1, 2: mic2}
    primary: list[Utterance] = []
    secondary: list[Utterance] = []
    for mic, utts in by_mic.items():
        for u in utts:
            if u.speaker in PREFERRED_MIC and PREFERRED_MIC[u.speaker] == mic:
                primary.append(u)
            else:
                secondary.append(u)
    merged = list(primary)
    # 優先側でカバーされていない区間の発言だけを補う
    for u in sorted(secondary, key=lambda x: (x.start, x.mic)):
        dur = max(u.end - u.start, 1e-3)
        covered = sum(_overlap(u, p) for p in merged)
        if covered / dur < 0.5:
            merged.append(u)
    merged.sort(key=lambda x: (x.start, x.end))
    return merged


def format_utterances(utts: list[Utterance], show_mic: bool = False) -> str:
    lines = []
    for u in utts:
        mic = f"(マイク{u.mic}) " if show_mic else ""
        lines.append(f"[{fmt_time(u.start)}] {mic}{SPEAKER_NAME.get(u.speaker, u.speaker)}: {u.text}")
    return "\n".join(lines)


def time_windows(utt_lists: list[list[Utterance]], window_s: float) -> list[tuple[float, float]]:
    """発言の開始時刻に基づき、LLM に渡す時間窓のリストを作る。空の窓は作らない。"""
    starts = sorted(u.start for utts in utt_lists for u in utts)
    if not starts:
        return []
    windows = []
    w0 = starts[0]
    for s in starts:
        if s >= w0 + window_s:
            windows.append((w0, s))
            w0 = s
    windows.append((w0, float("inf")))
    return windows


def in_window(utts: list[Utterance], win: tuple[float, float]) -> list[Utterance]:
    return [u for u in utts if win[0] <= u.start < win[1]]
