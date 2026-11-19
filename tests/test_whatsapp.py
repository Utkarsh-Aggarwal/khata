import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from khata.flow import Bot
from khata.guard import PaidGuard
from khata.ledger import Ledger
from khata.voice import VoicePipeline
from khata.whatsapp import make_router, owner_for, parse_messages, verify_signature

SECRET = "app-secret"


class FakeWA:
    def __init__(self):
        self.sent = []

    def send_text(self, to, text):
        self.sent.append(("text", to, text, None))

    def send_buttons(self, to, text, buttons):
        self.sent.append(("buttons", to, text, buttons))

    def download_media(self, media_id):
        return b"OggS" + b"\0" * 32, "audio/ogg; codecs=opus"


class FakeSTT:
    def transcribe(self, p):
        return "Ramesh ko 500 udhaar"


def setup(paid=True):
    lg = Ledger()
    bot = Bot(lg)
    wa = FakeWA()
    voice = VoicePipeline(PaidGuard(key="k" if paid else "", budget_calls=9, killed=False), FakeSTT())
    app = FastAPI()
    app.include_router(make_router(bot, voice, wa, verify_token="vt", app_secret=SECRET))
    return TestClient(app), wa, lg


def msg(mid, typ, **kw):
    m = {"id": mid, "from": "919800000001", "type": typ, **kw}
    return {"entry": [{"changes": [{"value": {"messages": [m]}}]}]}


def post(c, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/webhook/whatsapp", content=body,
                  headers={"x-hub-signature-256": sig, "content-type": "application/json"})


def test_handshake():
    c, _, _ = setup()
    ok = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_signature_required():
    c, wa, _ = setup()
    assert post(c, msg("m1", "text", text={"body": "hi"}), secret="wrong").status_code == 401
    assert c.post("/webhook/whatsapp", json=msg("m1", "text", text={"body": "hi"})).status_code == 401
    assert wa.sent == []
    assert not verify_signature(SECRET, b"x", "sha1=abc")


def test_text_then_button_confirm_saves_once():
    c, wa, lg = setup()
    assert post(c, msg("m1", "text", text={"body": "Sita ko 300 udhaar"})).status_code == 200
    kind, _, text, buttons = wa.sent[-1]
    assert kind == "buttons" and buttons == ["yes", "no"]
    owner = owner_for("919800000001", SECRET)
    assert lg.entries(owner) == []
    yes = msg("m2", "interactive", interactive={"type": "button_reply", "button_reply": {"id": "yes", "title": "Yes"}})
    post(c, yes)
    post(c, yes)                                    # Meta retry with the same id is ignored
    assert [e.paise for e in lg.entries(owner)] == [30000]
    assert "919800000001" not in owner              # raw number not stored as owner


def test_voice_note_round_trip():
    c, wa, lg = setup()
    post(c, msg("a1", "audio", audio={"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus"}))
    assert any("Heard" in s[2] for s in wa.sent)
    assert wa.sent[-1][0] == "buttons"


def test_voice_demo_mode_message():
    c, wa, _ = setup(paid=False)
    post(c, msg("a1", "audio", audio={"id": "MEDIA1"}))
    assert "demo mode" in wa.sent[-1][2]


def test_parse_ignores_status_updates():
    assert parse_messages({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []
