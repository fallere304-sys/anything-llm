"""2本のマイクの同時録音。

- 各マイクは独立した PortAudio 入力ストリームで開く（デバイスごとに
  サンプリングレートが異なってもよい）。
- コールバックで受け取った音声はキュー経由で書き込みスレッドに渡し、
  WAV (16bit mono) に逐次書き出す。長時間録音でも RAM を消費しない。
- 各ストリームの「最初のサンプルが取り込まれた時刻」を
  time.perf_counter() 基準で記録し、後段で2本の時間軸を揃える。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# 同時に2本開くと失敗しやすい WDM-KS は一覧から除外する
_HOSTAPI_PRIORITY = {
    "Windows WASAPI": 0,
    "MME": 1,
    "Windows DirectSound": 2,
}
_HOSTAPI_SHORT = {
    "Windows WASAPI": "WASAPI",
    "MME": "MME",
    "Windows DirectSound": "DirectSound",
}


@dataclass
class InputDevice:
    index: int
    name: str
    hostapi: str
    samplerate: int
    channels: int

    @property
    def label(self) -> str:
        return f"{self.name}  [{_HOSTAPI_SHORT.get(self.hostapi, self.hostapi)}]"


def list_input_devices() -> list[InputDevice]:
    import sounddevice as sd

    try:
        sd._terminate()  # デバイスの抜き差しを反映させるため再初期化
        sd._initialize()
    except Exception:
        log.debug("PortAudio の再初期化に失敗（無視）", exc_info=True)

    hostapis = sd.query_hostapis()
    result = []
    for idx, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        api = hostapis[d["hostapi"]]["name"]
        if api == "Windows WDM-KS":
            continue
        result.append(
            InputDevice(
                index=idx,
                name=d["name"],
                hostapi=api,
                samplerate=int(d["default_samplerate"]),
                channels=int(d["max_input_channels"]),
            )
        )
    result.sort(key=lambda dv: (_HOSTAPI_PRIORITY.get(dv.hostapi, 9), dv.name))
    return result


@dataclass
class TrackInfo:
    path: Path
    samplerate: int
    first_sample_time: float  # perf_counter 基準
    frames: int
    overflows: int


@dataclass
class RecordingResult:
    tracks: list[TrackInfo]
    wall_start: float
    wall_stop: float
    warnings: list[str] = field(default_factory=list)


class _MicStream:
    """1本のマイク入力を WAV に書き出す。"""

    def __init__(self, device: InputDevice, wav_path: Path):
        self.device = device
        self.wav_path = wav_path
        self.samplerate = device.samplerate
        self.first_sample_time: float | None = None
        self.level = 0.0  # 直近ブロックの RMS (0..1)
        self.frames = 0
        self.overflows = 0
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._writer: threading.Thread | None = None
        self.error: BaseException | None = None

    # PortAudio のオーディオスレッドから呼ばれる。重い処理は禁止。
    def _callback(self, indata, frames, time_info, status):
        now = time.perf_counter()
        if status.input_overflow:
            self.overflows += 1
        if self.first_sample_time is None:
            self.first_sample_time = now - frames / self.samplerate
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        self.level = float(np.sqrt(np.mean(mono * mono)))
        self._q.put(mono.copy())

    def _open(self):
        import sounddevice as sd

        dv = self.device
        rates = [dv.samplerate] + [r for r in (48000, 44100, 16000) if r != dv.samplerate]
        chans = []
        for c in (min(2, dv.channels), dv.channels, 1):
            if c not in chans:
                chans.append(c)
        last_err = None
        for rate in rates:
            for ch in chans:
                try:
                    stream = sd.InputStream(
                        device=dv.index,
                        channels=ch,
                        samplerate=rate,
                        dtype="float32",
                        callback=self._callback,
                    )
                    self.samplerate = int(rate)
                    log.info("opened %s rate=%s ch=%s", dv.label, rate, ch)
                    return stream
                except Exception as e:  # 組み合わせを変えて再試行
                    last_err = e
        raise RuntimeError(f"マイク「{dv.label}」を開けませんでした: {last_err}")

    def _write_loop(self):
        try:
            with wave.open(str(self.wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.samplerate)
                while True:
                    block = self._q.get()
                    if block is None:
                        break
                    pcm = np.clip(block, -1.0, 1.0)
                    wf.writeframes((pcm * 32767.0).astype("<i2").tobytes())
                    self.frames += len(block)
        except BaseException as e:
            log.exception("WAV 書き込み失敗")
            self.error = e

    def prepare(self):
        self._stream = self._open()
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    def start(self):
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None
        self._q.put(None)
        if self._writer is not None:
            self._writer.join()


class DualRecorder:
    def __init__(self, dev1: InputDevice, dev2: InputDevice, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.streams = [
            _MicStream(dev1, out_dir / "mic1.wav"),
            _MicStream(dev2, out_dir / "mic2.wav"),
        ]
        self.wall_start = 0.0
        self.recording = False

    def start(self):
        try:
            for s in self.streams:
                s.prepare()
        except Exception:
            for s in self.streams:
                s.stop()
            raise
        self.wall_start = time.perf_counter()
        # 2本の開始間隔を最小にするため、オープン済みのストリームを連続で start する
        try:
            for s in self.streams:
                s.start()
        except Exception:
            for s in self.streams:
                s.stop()
            raise
        self.recording = True

    def levels(self) -> tuple[float, float]:
        return self.streams[0].level, self.streams[1].level

    def elapsed(self) -> float:
        return time.perf_counter() - self.wall_start if self.recording else 0.0

    def stop(self) -> RecordingResult:
        for s in self.streams:
            s.stop()
        wall_stop = time.perf_counter()
        self.recording = False
        warnings = []
        tracks = []
        for i, s in enumerate(self.streams, 1):
            if s.error:
                raise RuntimeError(f"マイク{i}の録音データ書き込みに失敗: {s.error}")
            if s.first_sample_time is None or s.frames == 0:
                raise RuntimeError(f"マイク{i}から音声が届きませんでした。デバイスを確認してください。")
            if s.overflows:
                warnings.append(f"マイク{i}で入力オーバーフローが {s.overflows} 回発生しました")
            tracks.append(
                TrackInfo(
                    path=s.wav_path,
                    samplerate=s.samplerate,
                    first_sample_time=s.first_sample_time,
                    frames=s.frames,
                    overflows=s.overflows,
                )
            )
        return RecordingResult(tracks, self.wall_start, wall_stop, warnings)
