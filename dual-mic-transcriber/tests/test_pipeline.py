"""パイプライン全体の結線テスト。Whisper と LLM はモックに置き換える。

soxr が無い環境ではスキップする。
"""
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("soxr")

import app.llm as real_llm  # noqa: E402
from app import pipeline as pl  # noqa: E402
from app.config import DEFAULTS  # noqa: E402
from app.recorder import RecordingResult, TrackInfo  # noqa: E402
from tests.test_logic import _scene  # noqa: E402


def _write_wav(path: Path, x: np.ndarray, sr: int):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


class FakeWhisper:
    """マイクごとに認識結果を返す。遠いマイク側は誤認識を含む想定。"""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kw):
        self.calls += 1
        W = lambda s, e, w: SimpleNamespace(start=s, end=e, word=w)  # noqa: E731
        if self.calls == 1:  # mic1
            segs = [
                SimpleNamespace(start=2.0, end=6.0, text="今日は晴れですね", words=[W(2.1, 5.8, "今日は晴れですね")]),
                SimpleNamespace(start=8.0, end=12.0, text="そうでうね", words=[W(8.2, 11.8, "そうでうね")]),
            ]
        else:  # mic2
            segs = [
                SimpleNamespace(start=2.0, end=6.0, text="今日は腫れですね", words=[W(2.1, 5.8, "今日は腫れですね")]),
                SimpleNamespace(start=8.0, end=12.0, text="そうですね", words=[W(8.2, 11.8, "そうですね")]),
            ]
        return iter(segs), SimpleNamespace(language="ja")


class FakeLlama:
    def create_chat_completion(self, messages, **kw):
        prompt = messages[-1]["content"]
        assert "# マイク1の認識結果" in prompt
        for piece in ["[00:02.1] 話者A: 今日は晴れですね\n", "[00:08.2] 話者B: そうですね"]:
            yield {"choices": [{"delta": {"content": piece}}]}


class FakeModels:
    def ensure_whisper(self, cancel, report):
        return Path(".")

    def ensure_llm(self, cancel, report):
        return Path("model.gguf")


def _run(tmp_path, monkeypatch, llm_fails=False):
    m1, m2 = _scene()
    # mic2 は 44.1kHz で 0.3 秒遅れて開始した想定
    sr2 = 44100
    m2_44 = np.interp(np.arange(int(len(m2) * sr2 / 16000)) * 16000 / sr2, np.arange(len(m2)), m2)
    lag = int(0.3 * sr2)
    _write_wav(tmp_path / "mic1.wav", m1, 16000)
    _write_wav(tmp_path / "mic2.wav", m2_44[lag:], sr2)
    rec = RecordingResult(
        tracks=[
            TrackInfo(tmp_path / "mic1.wav", 16000, 100.0, len(m1), 0),
            TrackInfo(tmp_path / "mic2.wav", sr2, 100.3, len(m2_44) - lag, 0),
        ],
        wall_start=100.0,
        wall_stop=120.0,
    )
    monkeypatch.setattr(pl, "load_whisper_safe", lambda *a: FakeWhisper())
    def _load(*a):
        if llm_fails:
            raise RuntimeError("model load failed")
        return FakeLlama()

    monkeypatch.setattr(real_llm, "load_llm", _load)
    p = pl.Pipeline(rec, tmp_path, dict(DEFAULTS), FakeModels())
    res = p._process()
    events = []
    while not p.events.empty():
        events.append(p.events.get())
    return res, events


def test_pipeline_end_to_end(tmp_path, monkeypatch):
    res, events = _run(tmp_path, monkeypatch)
    phases = [e[1] for e in events if e[0] == "phase"]
    assert phases == list(range(len(pl.PHASES)))
    assert res.final_text == "[00:02.1] 話者A: 今日は晴れですね\n[00:08.2] 話者B: そうですね"
    # 規則ベース統合では A はマイク1、B はマイク2 の認識結果が採用される
    assert "話者A: 今日は晴れですね" in res.draft_text
    assert "話者B: そうですね" in res.draft_text
    assert "腫れ" not in res.draft_text and "そうでうね" not in res.draft_text
    for name in (
        "speaker_timeline.json",
        "speaker_timeline.csv",
        "transcript_mic1_labeled.txt",
        "transcript_mic2_labeled.txt",
        "transcript_rule_merged.txt",
        "transcript_final.txt",
    ):
        assert (tmp_path / name).exists(), name


def test_pipeline_falls_back_when_llm_fails(tmp_path, monkeypatch):
    res, _ = _run(tmp_path, monkeypatch, llm_fails=True)
    assert "話者A: 今日は晴れですね" in res.final_text
    assert any("補正に失敗" in w for w in res.warnings)


def test_cancel_stops_pipeline(tmp_path, monkeypatch):
    p = pl.Pipeline(RecordingResult([], 0, 0), tmp_path, {}, FakeModels())
    p.cancel.set()
    p._run()
    assert p.events.get()[0] == "cancelled"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
