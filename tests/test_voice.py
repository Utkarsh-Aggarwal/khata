import os
import time

import pytest
from fastapi.testclient import TestClient

from khata.app import create_app
from khata.guard import PaidGuard
from khata.voice import VoiceError, VoicePipeline


class FakeTranscriber:
    def __init__(self, text="Ramesh ko 500 udhaar", delay=0.0, boom=False):
        self.text, self.delay, self.boom, self.saw = text, delay, boom, None

    def transcribe(self, wav_path):
        self.saw = wav_path
        assert wav_path.exists()
        if self.boom:
            raise TimeoutError("provider timeout")
        return self.text


def test_demo_mode_without_key_asks_to_type():
    vp = VoicePipeline(PaidGuard(key="", budget_calls=10, killed=False), FakeTranscriber())
    with pytest.raises(VoiceError, match="demo mode"):
        vp.transcribe(b"x" * 100)


def test_kill_switch_and_budget():
    g = PaidGuard(key="k", budget_calls=1, killed=False)
    vp = VoicePipeline(g, FakeTranscriber())
    assert vp.transcribe(b"x" * 100) == "Ramesh ko 500 udhaar"
    with pytest.raises(VoiceError, match="demo mode"):     # budget spent
        vp.transcribe(b"x" * 100)
    with pytest.raises(VoiceError):
        VoicePipeline(PaidGuard(key="k", budget_calls=9, killed=True), FakeTranscriber()).transcribe(b"x")


def test_audio_deleted_after_success_and_failure():
    for tr in (FakeTranscriber(), FakeTranscriber(boom=True)):
        vp = VoicePipeline(PaidGuard(key="k", budget_calls=9, killed=False), tr)
        try:
            vp.transcribe(b"x" * 100)
        except VoiceError as e:
            assert "timed out" in str(e)
        assert tr.saw is not None
        assert not os.path.exists(vp.last_tmpdir)


def test_size_limit_and_empty():
    vp = VoicePipeline(PaidGuard(key="k", budget_calls=9, killed=False), FakeTranscriber())
    with pytest.raises(VoiceError, match="too large"):
        vp.transcribe(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(VoiceError, match="Empty"):
        vp.transcribe(b"")


def test_voice_endpoint_goes_through_confirm():
    tr = FakeTranscriber("सीता ने 200 रुपये दिए")
    c = TestClient(create_app(":memory:", PaidGuard(key="k", budget_calls=5, killed=False), tr))
    c.post("/api/demo-login?seed=false")
    r = c.post("/api/voice", files={"file": ("n.ogg", b"OggS" + b"\0" * 64, "audio/ogg")}).json()
    assert r["transcript"] == "सीता ने 200 रुपये दिए"
    assert r["buttons"] == ["yes", "no"]
    assert c.get("/api/ledger").json()["entries"] == []          # nothing saved before confirm
    c.post("/api/chat", json={"text": "हाँ"})
    e = c.get("/api/ledger").json()["entries"][0]
    assert (e["customer"], e["kind"], e["paise"]) == ("सीता", "payment", 20000)


def test_voice_endpoint_demo_mode():
    c = TestClient(create_app(":memory:", PaidGuard(key="", budget_calls=5, killed=False)))
    c.post("/api/demo-login?seed=false")
    r = c.post("/api/voice", files={"file": ("n.ogg", b"abc", "audio/ogg")}).json()
    assert "demo mode" in r["reply"] and r["transcript"] is None
    assert c.get("/health").json()["mode"] == "demo"


@pytest.mark.real_ffmpeg
@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_ffmpeg_converts_to_16k_mono(tmp_path):
    import subprocess
    import wave
    from khata.voice import to_wav
    src = tmp_path / "tone.ogg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ac", "2", "-ar", "44100", str(src)], check=True)
    out = to_wav(src, tmp_path / "out.wav")
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getframerate()) == (1, 16000)
