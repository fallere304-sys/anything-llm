"""音量比較による話者識別・話者付与・統合ロジックのテスト（numpy のみで実行可能）。"""
import numpy as np
import pytest

from app.diarize import align_tracks, diarize_by_volume
from app.llm import _clean_output
from app.merge import (
    Utterance,
    fmt_time,
    in_window,
    label_transcript,
    rule_based_merge,
    time_windows,
)

SR = 16000
CFG = {}
A_TURNS = [(2.0, 6.0), (14.0, 16.0)]
B_TURNS = [(8.0, 12.0), (16.5, 19.0)]


def _speech(dur, rng):
    """振幅変調したノイズで音声を模擬する。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 3 * t))
    return (rng.normal(0, 0.1, n) * env).astype(np.float32)


def _scene(gain1=2.0, gain2=0.25, crosstalk=0.3, total=20.0, seed=0, a_turns=A_TURNS, b_turns=B_TURNS, noise1=0.0, noise2=0.0):
    """noise1/noise2: 各マイク側だけに加わる環境雑音（ファン等）の標準偏差。"""
    rng = np.random.default_rng(seed)
    n = int(total * SR)
    a_src = np.zeros(n, np.float32)
    b_src = np.zeros(n, np.float32)
    for src, turns in ((a_src, a_turns), (b_src, b_turns)):
        for s, e in turns:
            i0, i1 = int(s * SR), int(e * SR)
            src[i0:i1] = _speech((i1 - i0) / SR, rng)
    mic1 = gain1 * (a_src + crosstalk * b_src + rng.normal(0, 0.002 + noise1, n))
    mic2 = gain2 * (b_src + crosstalk * a_src + rng.normal(0, 0.002 + noise2, n))
    return mic1.astype(np.float32), mic2.astype(np.float32)


def test_diarize_separates_speakers_despite_gain_mismatch():
    m1, m2 = _scene()
    tl = diarize_by_volume(m1, m2, CFG)
    assert tl.speaker_for_span(3.0, 5.0) == "A"
    assert tl.speaker_for_span(9.0, 11.0) == "B"
    assert tl.speaker_for_span(14.5, 15.5) == "A"
    assert tl.speaker_for_span(17.0, 18.5) == "B"
    segs = tl.segments()
    speakers = [s["speaker"] for s in segs]
    assert speakers == ["A", "B", "A", "B"], segs
    # 区間の境界は概ね正しい（±0.5秒）
    assert abs(segs[0]["start"] - 2.0) < 0.5 and abs(segs[0]["end"] - 6.0) < 0.5
    assert abs(segs[1]["start"] - 8.0) < 0.5 and abs(segs[1]["end"] - 12.0) < 0.5


def test_silence_is_not_labeled():
    m1, m2 = _scene()
    tl = diarize_by_volume(m1, m2, CFG)
    for s in tl.segments():
        assert not (s["start"] < 7.0 and s["end"] > 7.5), s  # 6〜8秒は無音


def test_align_tracks_trims_earlier_stream():
    x = np.arange(SR * 3, dtype=np.float32)
    # mic1 は 0.5 秒早く始まった → 先頭 0.5 秒を削る
    a, b, skew = align_tracks(x, 10.0, x[: SR * 2], 10.5)
    assert skew == pytest.approx(0.5)
    assert a[0] == SR * 0.5
    assert b[0] == 0
    assert len(a) == len(b) == SR * 2


def test_label_transcript_splits_segment_at_speaker_change():
    m1, m2 = _scene()
    tl = diarize_by_volume(m1, m2, CFG)
    # 14〜19秒を1セグメントとして認識した（途中で A→B に交代）ケース
    segs = [
        {
            "start": 14.0,
            "end": 19.0,
            "text": "こんにちは。はい、どうも。",
            "words": [
                {"start": 14.1, "end": 14.9, "word": "こんにち"},
                {"start": 14.9, "end": 15.6, "word": "は。"},
                {"start": 16.6, "end": 17.4, "word": "はい、"},
                {"start": 17.5, "end": 18.6, "word": "どうも。"},
            ],
        }
    ]
    utts = label_transcript(segs, tl, mic=1)
    assert [(u.speaker, u.text) for u in utts] == [("A", "こんにちは。"), ("B", "はい、どうも。")]


def test_label_transcript_without_word_timestamps():
    m1, m2 = _scene()
    tl = diarize_by_volume(m1, m2, CFG)
    utts = label_transcript([{"start": 8.2, "end": 11.8, "text": " テスト", "words": []}], tl, mic=2)
    assert len(utts) == 1 and utts[0].speaker == "B" and utts[0].text == "テスト"


def test_rule_based_merge_prefers_near_mic_and_fills_gaps():
    mic1 = [
        Utterance(2.0, 6.0, "A", "Aの発言(近)", 1),
        Utterance(8.0, 12.0, "B", "Bの発言(遠)", 1),
        Utterance(20.0, 21.0, "B", "mic2が取りこぼしたB", 1),
    ]
    mic2 = [
        Utterance(2.0, 6.0, "A", "Aの発言(遠)", 2),
        Utterance(8.0, 12.0, "B", "Bの発言(近)", 2),
    ]
    merged = rule_based_merge(mic1, mic2)
    assert [u.text for u in merged] == ["Aの発言(近)", "Bの発言(近)", "mic2が取りこぼしたB"]


def test_time_windows_cover_all_utterances_once():
    utts = [Utterance(float(t), float(t) + 1, "A", str(t), 1) for t in range(0, 500, 7)]
    wins = time_windows([utts], 120)
    assert len(wins) >= 4
    seen = [u.text for w in wins for u in in_window(utts, w)]
    assert seen == [u.text for u in utts]
    assert time_windows([[]], 120) == []


def test_fmt_time():
    assert fmt_time(5.25) == "00:05.2" or fmt_time(5.25) == "00:05.3"
    assert fmt_time(3725.0) == "1:02:05.0"


def test_clean_output_strips_preamble():
    raw = "以下が補正版です。\n```\n[00:01.0] 話者A: こんにちは\n[00:03.0] 話者B：どうも\n```"
    assert _clean_output(raw) == "[00:01.0] 話者A: こんにちは\n[00:03.0] 話者B：どうも"


def _accuracy(tl, a_turns, b_turns):
    ok = tot = 0
    for spk, turns in (("A", a_turns), ("B", b_turns)):
        for s, e in turns:
            for t in np.arange(s + 0.2, e - 0.2, 0.25):
                tot += 1
                ok += tl.speaker_for_span(t, t + 0.25) == spk
    return ok / tot


@pytest.mark.parametrize("noise1,noise2", [(0.0, 0.01), (0.0, 0.02), (0.01, 0.0)])
def test_auto_balance_corrects_unequal_ambient_noise(noise1, noise2):
    """片方のマイクだけ環境雑音が大きい（雑音下限が14〜20dB高い）場合。"""
    m1, m2 = _scene(noise1=noise1, noise2=noise2)
    off = diarize_by_volume(m1, m2, {"auto_balance": False})
    on = diarize_by_volume(m1, m2, {"auto_balance": True})
    assert _accuracy(off, A_TURNS, B_TURNS) < 0.6  # 第1段だけでは片側に偏る
    assert _accuracy(on, A_TURNS, B_TURNS) == 1.0
    assert abs(on.balance_offset_db) > 10
    assert [s["speaker"] for s in on.segments()] == ["A", "B", "A", "B"]


def test_auto_balance_robust_to_unequal_talk_time():
    a_turns, b_turns = [(0.5, 9.0), (10.0, 17.5)], [(18.0, 19.5)]
    m1, m2 = _scene(a_turns=a_turns, b_turns=b_turns, noise2=0.01)
    tl = diarize_by_volume(m1, m2, {})
    assert _accuracy(tl, a_turns, b_turns) == 1.0


@pytest.mark.parametrize("noise2", [0.0, 0.01])
def test_auto_balance_skipped_for_single_speaker(noise2):
    """1人しか話していない場合は、声の大小を2話者と誤認して補正してはいけない。"""
    a_turns = [(1.0, 8.0), (10.0, 18.0)]
    m1, m2 = _scene(a_turns=a_turns, b_turns=[], noise2=noise2)
    tl = diarize_by_volume(m1, m2, {})
    assert tl.balance_offset_db == 0.0
    assert "補正なし" in tl.balance_note
    assert {s["speaker"] for s in tl.segments()} == {"A"}


def test_auto_balance_does_not_change_balanced_setup():
    m1, m2 = _scene()
    tl = diarize_by_volume(m1, m2, {})
    assert abs(tl.balance_offset_db) < 1.0
