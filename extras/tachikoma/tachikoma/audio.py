"""マイク (常時聴取) と発話区間検出 (VAD)。

- 別スレッドで 30ms ずつ録音し、声の区間だけを切り出してキューに入れる
- 認識 (ASR) は poll() でメインスレッドが行う (自習と同じ ASR を取り合わないため)
- 自分が話している間 (TTS 再生中) は聞かない = 自分の声を自分の発話と取り違えない
  ただし大きな声で割り込まれたら (barge-in) 発声を止める

依存: numpy, sounddevice (webrtcvad があれば使い、無ければ適応的な音量閾値)
"""

import collections
import queue
import threading
import time

RATE = 16000
FRAME_MS = 30
FRAME = RATE * FRAME_MS // 1000


class EnergyVAD:
    """背景雑音の水準を追いかけ、その数倍を超えたら声とみなす簡易 VAD。"""

    def __init__(self, ratio=3.0, floor=200.0):
        self.noise = floor
        self.ratio, self.floor = ratio, floor

    def is_speech(self, frame_i16):
        import numpy as np
        rms = float(np.sqrt(np.mean(frame_i16.astype(np.float32) ** 2)) + 1e-6)
        speech = rms > max(self.floor, self.noise * self.ratio)
        if not speech:
            self.noise = 0.95 * self.noise + 0.05 * rms
        return speech


def make_vad(aggressiveness=2):
    try:
        import webrtcvad
        v = webrtcvad.Vad(aggressiveness)
        return type("WebRtc", (), {"is_speech": lambda self, f: v.is_speech(f.tobytes(), RATE)})()
    except ImportError:
        return EnergyVAD()


class Segmenter:
    """VAD の判定列から発話区間を組み立てる (ハードウェア非依存・テスト可能)。"""

    def __init__(self, start_frames=4, end_silence_ms=700, max_s=25, pre_roll_ms=300):
        self.start_frames = start_frames
        self.end_frames = end_silence_ms // FRAME_MS
        self.max_frames = max_s * 1000 // FRAME_MS
        # 声の立ち上がり判定に使ったフレーム + その直前の無音 (語頭の子音が欠けないように)
        self.pre = collections.deque(maxlen=start_frames + pre_roll_ms // FRAME_MS)
        self.buf, self.voiced, self.silent, self.active = [], 0, 0, False

    def push(self, frame, speech):
        """区間が閉じたらフレーム列を返す。"""
        if not self.active:
            self.pre.append(frame)
            self.voiced = self.voiced + 1 if speech else 0
            if self.voiced >= self.start_frames:
                self.active, self.buf, self.silent = True, list(self.pre), 0
            return None
        self.buf.append(frame)
        self.silent = 0 if speech else self.silent + 1
        if self.silent >= self.end_frames or len(self.buf) >= self.max_frames:
            out, self.buf, self.active, self.voiced = self.buf, [], False, 0
            self.pre.clear()
            return out
        return None

    @property
    def in_speech(self):
        return self.active


class VoiceSensor:
    """マイクから「聞こえた発話」を出来事にする感覚器。"""
    name = "voice"

    def __init__(self, cfg, asr, tts=None, prompt_fn=None):
        self.cfg, self.asr, self.tts = cfg, asr, tts
        self.prompt_fn = prompt_fn or (lambda: None)
        self.q = queue.Queue()
        self.seg = Segmenter(end_silence_ms=cfg["vad_end_silence_ms"])
        self.vad = make_vad(cfg["vad_aggressiveness"])
        self.heard_voice_at = 0.0
        self._muted_until = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import numpy as np
        import sounddevice as sd
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                            device=self.cfg.get("mic_device")) as stream:
            while True:
                data, _ = stream.read(FRAME)
                frame = np.frombuffer(data, dtype=np.int16) if not hasattr(data, "reshape") else data.reshape(-1)
                speaking = self.tts is not None and self.tts.speaking
                if speaking:
                    self._muted_until = time.monotonic() + 0.4
                speech = self.vad.is_speech(frame)
                if speech:
                    self.heard_voice_at = time.monotonic()
                if speaking or time.monotonic() < self._muted_until:
                    # 自分の声は聞かない。ただし大声の割り込みには反応する
                    if speech and self.cfg["barge_in"] and self._loud(frame):
                        self.tts.stop()
                    continue
                out = self.seg.push(frame, speech)
                if out is not None:
                    self.q.put(np.concatenate(out).astype(np.float32) / 32768.0)

    def _loud(self, frame):
        import numpy as np
        return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) > self.cfg["barge_in_rms"]

    def poll(self):
        out = []
        while not self.q.empty():
            audio = self.q.get_nowait()
            t = self.asr.transcribe(audio, prompt=self.prompt_fn())
            if t.text and t.no_speech_prob < 0.8:
                out.append(("speech", t.text, {"avg_logprob": t.avg_logprob,
                                               "seconds": len(audio) / RATE}))
        return out

    def recently_heard(self, seconds=1.0):
        return time.monotonic() - self.heard_voice_at < seconds
