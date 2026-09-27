"""音声認識 (耳)。faster-whisper (CTranslate2 版 Whisper) を使う。

Gemma 4 E2B も音声入力を持つが、ここでは Whisper を採用する:
- 認識だけなら専用モデルの方が軽く速い (Gemma の GPU 時間を思考に残せる)
- 字幕との対で「耳だけ」を学習・評価・差し替えできる (§11)
Gemma の音声入力は、声色・雰囲気など「文字にならない情報」の把握用に将来使う余地を残す。
"""

from dataclasses import dataclass


@dataclass
class Transcript:
    text: str
    avg_logprob: float = 0.0      # 低いほど自信がない (Whisper の目安: -1.0 未満は怪しい)
    no_speech_prob: float = 0.0


class FasterWhisperASR:
    def __init__(self, cfg, model=None):
        self.cfg = cfg
        self.load(model or cfg["asr_model"])

    def load(self, model):
        from faster_whisper import WhisperModel   # 重い依存は使うときだけ読む
        self._model = WhisperModel(model, device=self.cfg["asr_device"],
                                   compute_type=self.cfg["asr_compute_type"])
        self.name = model

    def transcribe(self, audio, prompt=None):
        """audio: wav ファイルのパス、または 16kHz mono の float32 配列。"""
        segments, _ = self._model.transcribe(
            audio, language=self.cfg["asr_language"], beam_size=self.cfg["asr_beam_size"],
            initial_prompt=prompt, condition_on_previous_text=False, vad_filter=False)
        segs = list(segments)
        if not segs:
            return Transcript("", -10.0, 1.0)
        return Transcript("".join(s.text for s in segs).strip(),
                          sum(s.avg_logprob for s in segs) / len(segs),
                          max(s.no_speech_prob for s in segs))
