"""FastAPI app: web chat, ledger views, export, demo login, health."""
from __future__ import annotations

import io
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from fastapi import Cookie, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bills import MAX_IMAGE as BILL_MAX, SAMPLE_BILL, BillError, BillReader, intro, to_extraction
from .flow import Bot
from .sheets import from_env as sheet_from_env
from .whatsapp import make_router, owner_for
from .guard import PaidGuard
from .ledger import Ledger, format_rupees
from .voice import MAX_BYTES, VoiceError, VoicePipeline

STATIC = Path(__file__).parent / "static"
DB_PATH = os.environ.get("KHATA_DB", "khata.db")
RATE_PER_MIN = int(os.environ.get("KHATA_RATE_PER_MIN", "30"))
LOGINS_PER_HOUR = int(os.environ.get("KHATA_LOGINS_PER_HOUR", "20"))

SEED = [
    "Ramesh ko 1200 udhaar", "haan", "Sita ne 300 diye", "haan",
    "Sita ko 800 udhaar", "haan", "रमेश को पाँच सौ रुपये उधार", "हाँ",
    "Gupta ji ko dhai hazaar udhaar kal dunga", "haan",
]


class Msg(BaseModel):
    text: str


def create_app(db_path: str | None = None, guard: PaidGuard | None = None,
               transcriber=None, vision=None, wa_client=None, wa_verify_token=None,
               wa_app_secret=None) -> FastAPI:
    app = FastAPI(title="Khata", version="0.3.0")
    ledger = Ledger(db_path or DB_PATH)
    bot = Bot(ledger, base_url=os.environ.get("KHATA_PUBLIC_URL", "").rstrip("/"))
    guard = guard or PaidGuard()
    voice = VoicePipeline(guard, transcriber)
    bills = BillReader(guard, vision)
    app.state.bot, app.state.guard, app.state.voice, app.state.ledger = bot, guard, voice, ledger

    def bill_reply(owner: str, f) -> dict:
        r = bot.start_from_extraction(owner, to_extraction(f), "bill")
        return {**r, "reply": intro(f) + "\n" + r["reply"]}

    def on_image(owner: str, image: bytes, mime: str) -> dict:
        try:
            return bill_reply(owner, bills.read(image, mime))
        except BillError as e:
            return {"reply": str(e), "buttons": []}

    # Sheet mirror for the demo shopkeeper's WhatsApp number (env-configured; off by default).
    sheet_num = os.environ.get("KHATA_SHEET_OWNER_NUMBER")
    mirror = sheet_from_env(ledger, owner_for(sheet_num, wa_app_secret or os.environ.get("WA_APP_SECRET", ""))
                            if sheet_num else None)
    app.state.mirror = mirror
    if mirror:
        mirror.start()

    app.include_router(make_router(bot, voice, wa_client, verify_token=wa_verify_token,
                                   app_secret=wa_app_secret, on_image=on_image))
    hits: dict[str, deque] = defaultdict(deque)
    logins: dict[str, deque] = defaultdict(deque)

    def owner_of(sid: str | None) -> str:
        if not sid or not sid.startswith("demo-") or len(sid) < 20:
            raise HTTPException(401, "login first")
        return sid

    def limit(owner: str) -> None:
        q, now = hits[owner], time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            raise HTTPException(429, "slow down")
        q.append(now)

    @app.get("/health")
    def health():
        sheet = {"sheet_pending": mirror.pending()} if mirror else {"sheet": "off"}
        return {"ok": True, "mode": "live" if guard.on else "demo", **guard.status(), **sheet}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/demo-login")
    def demo_login(request: Request, response: Response, seed: bool = True):
        hdr = os.environ.get("KHATA_CLIENT_IP_HEADER")       # e.g. fly-client-ip, only behind that proxy
        ip = (request.headers.get(hdr) if hdr else None) or (request.client.host if request.client else "?")
        q, now = logins[ip], time.time()
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= LOGINS_PER_HOUR:
            raise HTTPException(429, "too many demo logins from this network; try later")
        q.append(now)
        ledger.purge_demo_owners(older_than_days=7)
        sid = "demo-" + secrets.token_urlsafe(18)
        if seed:
            for line in SEED:
                bot.handle(sid, line)
        response.set_cookie("sid", sid, httponly=True, samesite="lax", max_age=86400 * 7)
        return {"ok": True}

    @app.post("/api/chat")
    def chat(m: Msg, sid: str | None = Cookie(None)):
        o = owner_of(sid)
        limit(o)
        if len(m.text) > 1000:
            raise HTTPException(413, "message too long")
        return bot.handle(o, m.text)

    @app.post("/api/voice")
    async def voice_note(file: UploadFile, sid: str | None = Cookie(None)):
        o = owner_of(sid)
        limit(o)
        audio = await file.read(MAX_BYTES + 1)
        suffix = Path(file.filename or "").suffix.lower() or ".webm"
        try:
            text = voice.transcribe(audio, suffix)
        except VoiceError as e:
            return {"reply": str(e), "buttons": [], "transcript": None}
        r = bot.handle(o, text, source="voice")
        return {**r, "transcript": text}

    @app.post("/api/bill")
    async def bill_photo(file: UploadFile, sid: str | None = Cookie(None)):
        o = owner_of(sid)
        limit(o)
        return on_image(o, await file.read(BILL_MAX + 1), file.content_type or "")

    @app.post("/api/bill/sample")
    def bill_sample(sid: str | None = Cookie(None)):
        """Demo-mode path: canned fields for the bundled sample bill image (no paid API)."""
        o = owner_of(sid)
        limit(o)
        r = bill_reply(o, SAMPLE_BILL)
        return {**r, "reply": "(sample bill, pre-read) " + r["reply"]}

    @app.get("/api/ledger")
    def get_ledger(sid: str | None = Cookie(None)):
        o = owner_of(sid)
        bal = ledger.balances(o)
        overdue = {e.customer for e in ledger.overdue(o)}
        return {
            "total_due": format_rupees(ledger.total_due(o)),
            "customers": [
                {"name": n, "paise": v, "balance": format_rupees(v), "overdue": n in overdue,
                 "wa": "https://wa.me/?text=" + quote(
                     f"Namaste {n} ji, aapka {format_rupees(v)} baaki hai. Kripya jaldi chuka dein. 🙏")
                 if v > 0 else None}
                for n, v in bal.items() if v != 0],
            "entries": [e.__dict__ for e in ledger.entries(o, include_cancelled=True)][::-1][:50],
        }

    @app.get("/api/export.xlsx")
    def export(sid: str | None = Cookie(None)):
        from openpyxl import Workbook
        o = owner_of(sid)
        wb = Workbook()
        ws = wb.active
        ws.title = "Khata"
        ws.append(["id", "customer", "kind", "rupees", "date", "due", "status"])
        for e in ledger.entries(o, include_cancelled=True):
            ws.append([e.id, e.customer, e.kind, e.paise / 100, e.entry_date, e.due_date, e.status])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": "attachment; filename=khata.xlsx"})

    @app.delete("/api/data")
    def delete_data(response: Response, sid: str | None = Cookie(None)):
        o = owner_of(sid)
        ledger.delete_owner_data(o)
        bot.pending.pop(o, None)
        return {"deleted": True}

    return app


app = create_app()
