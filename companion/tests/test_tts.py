import io
import threading
import wave

import pytest
from fastapi.testclient import TestClient

from buddy.api.app import create_app
from buddy.llm.mock import MockProvider
from buddy.storage.db import Database
from buddy.tts.base import AudioClip, TTSError, TTSProvider
from buddy.tts.mock import MockTTSProvider
from buddy.tts.voiceroid2 import Voiceroid2Provider

from .conftest import make_settings, parse_sse


def app_client(tmp_path, tts):
    app = create_app(make_settings(tmp_path), MockProvider(), Database(":memory:"), tts=tts)
    return TestClient(app)


def test_tts_not_configured(tmp_path):
    with app_client(tmp_path, None) as c:
        assert c.post("/api/tts", json={"text": "こんにちは"}).status_code == 404
        assert c.get("/api/status").json()["tts"] == {"enabled": False}


def test_tts_mock_returns_valid_wav(tmp_path):
    with app_client(tmp_path, MockTTSProvider()) as c:
        r = c.post("/api/tts", json={"text": "こんにちは"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getframerate() == 16000 and w.getnframes() > 0
        assert c.get("/api/status").json()["tts"]["provider"] == "mock"
        assert c.post("/api/tts", json={"text": ""}).status_code == 422
        assert c.post("/api/tts", json={"text": "  "}).status_code == 422
        assert c.post("/api/tts", json={"text": "あ" * 2001}).status_code == 422


def test_tts_requires_auth(tmp_path):
    s = make_settings(tmp_path, host="0.0.0.0", access_token="tok-123456")
    app = create_app(s, MockProvider(), Database(":memory:"), tts=MockTTSProvider())
    with TestClient(app) as c:
        assert c.post("/api/tts", json={"text": "a"}).status_code == 401


class BoomTTS(TTSProvider):
    name = "boom"

    async def synthesize(self, text):
        raise TTSError("声が見つかりません")


def test_tts_error_is_surfaced(tmp_path):
    with app_client(tmp_path, BoomTTS()) as c:
        r = c.post("/api/tts", json={"text": "a"})
        assert r.status_code == 502 and "声が見つかりません" in r.json()["detail"]


# --- VOICEROID2 アダプタ(偽の pyvcroid2 で検証。実機は未検証) ---
class FakeParam:
    speed = pitch = volume = None


class FakeVc:
    def __init__(self):
        self.param = FakeParam()
        self.thread = None
        self.loaded = []

    def listLanguages(self): return ["standard"]
    def loadLanguage(self, l): self.loaded.append(("lang", l))
    def listVoices(self): return ["aoi_emo", "akane_emo"]
    def loadVoice(self, v): self.loaded.append(("voice", v))

    def textToSpeech(self, text):
        self.synth_thread = threading.current_thread().name
        return b"RIFFfake" + text.encode(), []


class FakeMod:
    def __init__(self): self.vc = FakeVc()
    def VcRoid2(self):
        self.vc.thread = threading.current_thread().name  # 生成(初期化)したスレッド
        return self.vc


async def test_voiceroid2_adapter_ok():
    mod = FakeMod()
    p = Voiceroid2Provider("aoi_emo", speed=1.2, pitch=0.9, volume=0.8, loader=lambda: mod)
    clip = await p.synthesize("やあ")
    assert isinstance(clip, AudioClip) and clip.data.startswith(b"RIFFfake")
    assert mod.vc.loaded == [("lang", "standard"), ("voice", "aoi_emo")]
    assert (mod.vc.param.speed, mod.vc.param.pitch, mod.vc.param.volume) == (1.2, 0.9, 0.8)
    await p.synthesize("二回目")  # 初期化は一度だけ・同一専用スレッド
    assert mod.vc.loaded.count(("voice", "aoi_emo")) == 1
    assert mod.vc.thread == mod.vc.synth_thread and mod.vc.thread.startswith("voiceroid2")
    await p.aclose()


async def test_voiceroid2_requires_voice_name_and_lists_voices():
    p = Voiceroid2Provider("", loader=lambda: FakeMod())
    with pytest.raises(TTSError, match="aoi_emo"):
        await p.synthesize("x")
    ok, detail = await p.health()
    assert not ok and "TTS_VOICE_NAME" in detail
    await p.aclose()


async def test_voiceroid2_unknown_voice():
    p = Voiceroid2Provider("nope", loader=lambda: FakeMod())
    with pytest.raises(TTSError, match="nope"):
        await p.synthesize("x")
    await p.aclose()


async def test_voiceroid2_missing_library_message():
    from buddy.tts import voiceroid2
    p = Voiceroid2Provider("aoi_emo")  # この環境に pyvcroid2 は無い
    with pytest.raises(TTSError, match="pyvcroid2"):
        await p.synthesize("x")
    await p.aclose()
