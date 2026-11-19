"""WhatsApp Cloud API adapter (test number).

GET  /webhook/whatsapp : Meta verify handshake (hub.mode / hub.verify_token / hub.challenge)
POST /webhook/whatsapp : signed with X-Hub-Signature-256 = HMAC-SHA256(app secret, raw body)

Every message goes through the same Bot as the web chat, so nothing is saved without a
confirm tap. Env: WA_VERIFY_TOKEN, WA_APP_SECRET, WA_TOKEN, WA_PHONE_ID, WA_GRAPH_VERSION.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections import OrderedDict
from typing import Callable, Protocol

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

MEDIA_MAX = 10 * 1024 * 1024
BUTTON_TITLES = {"yes": "✅ Yes / हाँ", "no": "❌ No / नहीं", "credit": "Credit / उधार", "payment": "Payment / जमा"}


class WAClient(Protocol):
    def send_text(self, to: str, text: str) -> None: ...
    def send_buttons(self, to: str, text: str, buttons: list[str]) -> None: ...
    def download_media(self, media_id: str) -> tuple[bytes, str]: ...


class CloudClient:
    def __init__(self, token: str, phone_id: str, version: str | None = None):
        self.token, self.phone_id = token, phone_id
        self.base = f"https://graph.facebook.com/{version or os.environ.get('WA_GRAPH_VERSION', 'v21.0')}"

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _send(self, body: dict) -> None:
        import httpx
        httpx.post(f"{self.base}/{self.phone_id}/messages", headers=self._h(),
                   json={"messaging_product": "whatsapp", **body}, timeout=15)

    def send_text(self, to, text):
        self._send({"to": to, "type": "text", "text": {"body": text[:4096]}})

    def send_buttons(self, to, text, buttons):
        self._send({"to": to, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b, "title": BUTTON_TITLES.get(b, b)[:20]}}
                                   for b in buttons[:3]]}}})

    def download_media(self, media_id):
        import httpx
        meta = httpx.get(f"{self.base}/{media_id}", headers=self._h(), timeout=15).json()
        size = int(meta.get("file_size") or 0)
        if size > MEDIA_MAX:
            raise ValueError("media too large")
        r = httpx.get(meta["url"], headers=self._h(), timeout=30)
        if len(r.content) > MEDIA_MAX:
            raise ValueError("media too large")
        return r.content, meta.get("mime_type", "")


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def parse_messages(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry") or []:
        for ch in entry.get("changes") or []:
            for m in (ch.get("value") or {}).get("messages") or []:
                t = m.get("type")
                msg = {"id": m.get("id"), "from": m.get("from"), "type": t}
                if t == "text":
                    msg["text"] = (m.get("text") or {}).get("body", "")
                elif t in ("audio", "image"):
                    msg["media_id"] = (m.get(t) or {}).get("id")
                    msg["mime"] = (m.get(t) or {}).get("mime_type", "")
                elif t == "interactive":
                    it = m.get("interactive") or {}
                    msg["text"] = ((it.get("button_reply") or {}).get("id")
                                   or (it.get("list_reply") or {}).get("id") or "")
                elif t == "button":
                    msg["text"] = (m.get("button") or {}).get("payload", "")
                out.append(msg)
    return out


def owner_for(number: str, salt: str) -> str:
    """Stable per-shopkeeper owner id; the raw phone number is not stored in the ledger."""
    return "wa-" + hashlib.sha256((salt + ":" + number).encode()).hexdigest()[:24]


class Seen:
    """Meta retries webhooks; drop message ids we've already handled."""
    def __init__(self, cap=5000):
        self.d: OrderedDict[str, None] = OrderedDict()
        self.cap = cap

    def add(self, k: str) -> bool:
        if k in self.d:
            return False
        self.d[k] = None
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
        return True


def make_router(bot, voice, client: WAClient | None = None, *, verify_token: str | None = None,
                app_secret: str | None = None, on_image: Callable | None = None) -> APIRouter:
    verify_token = verify_token if verify_token is not None else os.environ.get("WA_VERIFY_TOKEN", "")
    app_secret = app_secret if app_secret is not None else os.environ.get("WA_APP_SECRET", "")
    if client is None and os.environ.get("WA_TOKEN") and os.environ.get("WA_PHONE_ID"):
        client = CloudClient(os.environ["WA_TOKEN"], os.environ["WA_PHONE_ID"])
    seen = Seen()
    r = APIRouter()

    @r.get("/webhook/whatsapp")
    def handshake(request: Request):
        q = request.query_params
        if verify_token and q.get("hub.mode") == "subscribe" and hmac.compare_digest(
                q.get("hub.verify_token", ""), verify_token):
            return Response(q.get("hub.challenge", ""), media_type="text/plain")
        raise HTTPException(403, "verification failed")

    @r.post("/webhook/whatsapp")
    async def incoming(request: Request, bg: BackgroundTasks):
        if not client or not app_secret:
            raise HTTPException(503, "whatsapp not configured")
        body = await request.body()
        if not verify_signature(app_secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(401, "bad signature")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "bad json")
        for m in parse_messages(payload):
            if m["id"] and m["from"] and seen.add(m["id"]):
                bg.add_task(handle, m)
        return {"ok": True}       # ack fast; Meta retries on slow responses

    def reply(to: str, r: dict) -> None:
        if r.get("buttons"):
            client.send_buttons(to, r["reply"], r["buttons"])
        else:
            client.send_text(to, r["reply"])

    def handle(m: dict) -> None:
        to = m["from"]
        owner = owner_for(to, app_secret)
        try:
            if m["type"] in ("text", "interactive", "button"):
                return reply(to, bot.handle(owner, m.get("text", "")[:1000]))
            if m["type"] == "audio":
                from .voice import VoiceError
                try:
                    audio, mime = client.download_media(m["media_id"])
                    text = voice.transcribe(audio, ".ogg" if "ogg" in (mime or m["mime"]) else ".bin")
                except VoiceError as e:
                    return client.send_text(to, str(e))
                client.send_text(to, f"🎙️ Heard: “{text}”")
                return reply(to, bot.handle(owner, text, source="voice"))
            if m["type"] == "image" and on_image:
                img, mime = client.download_media(m["media_id"])
                return reply(to, on_image(owner, img, mime or m["mime"]))
            client.send_text(to, "I can read text, voice notes and bill photos.")
        except Exception:
            client.send_text(to, "Something went wrong. Nothing was saved. Please try again.")

    return r
