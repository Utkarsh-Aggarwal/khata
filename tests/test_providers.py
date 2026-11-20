"""Request shapes and error handling for the real (Groq) provider clients, with HTTP mocked."""
import io
import json

import httpx
import pytest
from PIL import Image

from khata.bills import BillError, OpenAICompatVision, parse_json_reply, shrink
from khata.voice import OpenAICompatTranscriber, VoiceError


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def capture(monkeypatch, status, body):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return Resp(status, body)
    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


def test_stt_defaults_to_groq_whisper(monkeypatch, tmp_path):
    seen = capture(monkeypatch, 200, {"text": " Ramesh ko 500 udhaar "})
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    assert OpenAICompatTranscriber("gsk").transcribe(wav) == "Ramesh ko 500 udhaar"
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["data"]["model"] == "whisper-large-v3" and "udhaar" in seen["data"]["prompt"]
    assert seen["headers"]["Authorization"] == "Bearer gsk"


def test_stt_rate_limit_message(monkeypatch, tmp_path):
    capture(monkeypatch, 429, {})
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(VoiceError, match="free-tier limit"):
        OpenAICompatTranscriber("gsk").transcribe(wav)


def test_vision_defaults_to_groq(monkeypatch):
    seen = capture(monkeypatch, 200, {"choices": [{"message": {
        "content": '<think>hmm</think>```json\n{"vendor": "Sharma", "total_rupees": 846.5, "date": null}\n```'}}]})
    out = OpenAICompatVision("gsk").read(b"\x89PNG\r\n\x1a\n" + b"\0" * 10, "image/png")
    assert out == {"vendor": "Sharma", "total_rupees": 846.5, "date": None}
    body = seen["json"]
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert body["model"] == "qwen/qwen3.8-27b" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_rate_limit(monkeypatch):
    capture(monkeypatch, 429, {})
    with pytest.raises(BillError, match="free-tier limit"):
        OpenAICompatVision("gsk").read(b"\x89PNG\r\n\x1a\n", "image/png")


def test_parse_json_reply_rejects_garbage():
    assert parse_json_reply('{"a": 1} trailing') == {"a": 1}
    for bad in ("no json here", "[1,2]", "{broken"):
        with pytest.raises(BillError):
            parse_json_reply(bad)


def test_shrink_big_photo():
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(big, "PNG")
    out, mime = shrink(big.getvalue(), "image/png")
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) == 1600 and mime == "image/jpeg"
    small = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(small, "JPEG")
    assert shrink(small.getvalue(), "image/jpeg") == (small.getvalue(), "image/jpeg")
