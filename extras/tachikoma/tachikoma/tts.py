"""発声 (口)。

- sapi:     Windows 標準の音声合成 (PowerShell 経由。追加インストール不要)
- voicevox: ローカルの VOICEVOX エンジン (http://127.0.0.1:50021) — 日本語が自然
- none:     話さない (文字だけ)

確度ラベルは読み上げず、話し言葉の言い回しに置き換える:
    [観測事実] → (そのまま断定) / [合理的推定] → 「たぶん」 / [低確度仮説] → 「もしかすると」
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import wave

HEDGES = {"[観測事実]": "", "[合理的推定]": "たぶん、", "[低確度仮説]": "もしかすると、", "[反証済み]": "違ってた。"}
_MARKDOWN = re.compile(r"[*_`#>|]+|\[(.*?)\]\((.*?)\)")
_NON_SPOKEN = re.compile(r"[^\w\s、。,.!?！？ー〜「」『』()（）%％:：/・\-]+")


def speakable(text):
    for label, spoken in HEDGES.items():
        text = re.sub(re.escape(label) + r"\s*", spoken, text)
    text = re.sub(r"^\[タチコマ [0-9:]+\]\s*", "", text)
    text = _MARKDOWN.sub(lambda m: m.group(1) or "", text)
    text = _NON_SPOKEN.sub("", text)     # 絵文字・記号
    return re.sub(r"\s+", " ", text).strip()


class NullTTS:
    speaking = False

    def speak(self, text):
        pass

    def stop(self):
        pass


class SapiTTS:
    SCRIPT = ("Add-Type -AssemblyName System.Speech;"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$s.Rate = [int]$env:TACHIKOMA_TTS_RATE;"
              "$s.Speak($env:TACHIKOMA_TTS_TEXT)")

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None

    @property
    def speaking(self):
        return self.proc is not None and self.proc.poll() is None

    def speak(self, text):
        self.stop()
        text = speakable(text)
        if not text:
            return
        # 本文はコマンドラインではなく環境変数で渡す (引用符による注入を避ける)
        env = dict(os.environ, TACHIKOMA_TTS_TEXT=text, TACHIKOMA_TTS_RATE=str(self.cfg["tts_rate"]))
        self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", self.SCRIPT], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.speaking:
            self.proc.terminate()


class VoicevoxTTS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = cfg["voicevox_url"].rstrip("/")
        self._until = 0.0

    @property
    def speaking(self):
        return time.monotonic() < self._until

    def _post(self, path, data=None):
        req = urllib.request.Request(self.url + path, data=data or b"", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def speak(self, text):
        self.stop()
        text = speakable(text)
        if not text:
            return
        sp = self.cfg["voicevox_speaker"]
        try:
            query = self._post(f"/audio_query?text={urllib.parse.quote(text)}&speaker={sp}")
            q = json.loads(query)
            q["speedScale"] = self.cfg["voicevox_speed"]
            wav = self._post(f"/synthesis?speaker={sp}", json.dumps(q).encode("utf-8"))
        except OSError:
            return
        path = os.path.join(tempfile.gettempdir(), "tachikoma_tts.wav")
        with open(path, "wb") as f:
            f.write(wav)
        with wave.open(path, "rb") as w:
            self._until = time.monotonic() + w.getnframes() / w.getframerate()
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def stop(self):
        if self.speaking and sys.platform == "win32":
            import winsound
            winsound.PlaySound(None, 0)
        self._until = 0.0


def build(cfg):
    kind = cfg.get("tts", "none")
    if kind == "sapi" and sys.platform == "win32":
        return SapiTTS(cfg)
    if kind == "voicevox":
        return VoicevoxTTS(cfg)
    return NullTTS()
