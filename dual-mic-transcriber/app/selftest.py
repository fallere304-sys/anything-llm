"""ビルド後の exe に必要なライブラリ・DLL が同梱されているかを確認する。

  DualMicTranscriber.exe --selftest <結果ファイル>

CI で exe の起動確認に使う（--windowed の exe は標準出力が無いため結果をファイルに書く）。
"""
from __future__ import annotations

import sys
import tempfile
import traceback
import wave
from pathlib import Path


def run(out_path: str) -> int:
    lines = []
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except BaseException:
            ok = False
            lines.append(f"FAIL {name}:\n{traceback.format_exc()}")

    def _sounddevice():
        import sounddevice as sd

        return f"portaudio={sd.get_portaudio_version()[1]} devices={len(sd.query_devices())}"

    def _decode():
        import numpy as np
        from .audio import load_wav_16k

        p = Path(tempfile.mkdtemp()) / "t.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            t = np.arange(48000) / 48000
            wf.writeframes((np.sin(2 * np.pi * 440 * t) * 10000).astype("<i2").tobytes())
        a = load_wav_16k(p)
        if abs(len(a) - 16000) > 16:
            raise RuntimeError(f"unexpected length {len(a)}")
        import av

        return f"samples={len(a)} av={av.__version__}"

    def _vad():
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions())
        return "silero vad ok"

    def _ct2():
        import ctranslate2

        return f"ctranslate2={ctranslate2.__version__} cuda={ctranslate2.get_cuda_device_count()}"

    def _llama():
        import llama_cpp

        info = llama_cpp.llama_print_system_info()
        return f"llama_cpp={llama_cpp.__version__} {info.decode(errors='ignore') if isinstance(info, bytes) else info}"

    def _tk():
        import tkinter

        return f"tk={tkinter.TkVersion}"

    def _pipeline():
        import numpy as np

        from .diarize import diarize_by_volume

        rng = np.random.default_rng(0)
        a = rng.normal(0, 0.001, 16000 * 4).astype(np.float32)
        b = a.copy()
        a[16000:32000] += 0.2 * np.sin(np.arange(16000) / 5).astype(np.float32)
        tl = diarize_by_volume(a, b, {})
        return f"segments={tl.segments()}"

    check("tkinter", _tk)
    check("sounddevice", _sounddevice)
    check("soxr/load_wav_16k", _decode)
    check("onnxruntime/vad", _vad)
    check("ctranslate2", _ct2)
    check("llama_cpp", _llama)
    check("diarize", _pipeline)
    lines.append("RESULT: " + ("PASS" if ok else "FAIL"))
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "selftest.txt"))
