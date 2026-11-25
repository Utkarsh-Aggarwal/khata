"""Telegram Bot API channel.

`TelegramAPI` is a thin httpx client for the handful of Bot API methods Khata uses. Every
request goes to https://api.telegram.org/bot<token>/<method>. The token is part of that URL,
and httpx puts the URL into its exception messages, so errors are re-raised as
`TelegramError("<method>: <ExceptionType>") from None` and only the exception type name is
logged. Telegram's own `description` is scrubbed of the token as a second guard.

Replies are plain text (no parse_mode): customer names and transcripts are untrusted.
"""
from __future__ import annotations

import hashlib
import secrets
import hmac
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from . import bills
from .voice import VoiceError
from .whatsapp import Seen

API_BASE = "https://api.telegram.org"
FILE_MAX = 10 * 1024 * 1024
ALLOWED_UPDATES = ["message", "callback_query"]
BUTTON_TITLES = {"yes": "✅ Yes / हाँ", "no": "❌ No / नहीं",
                 "credit": "📤 I gave / मैंने दिए", "payment": "📥 They gave / उन्होंने दिए",
                 "?": "❓ Explain / समझाओ", "expense": "🧾 Shop expense / दुकान खर्च",
                 "mode:bill": "🧾 Bill / बिल", "mode:entry": "📒 Entry / एंट्री", "mode:udhaar": "🤝 Udhaar / उधार",
                 "bill:done": "✅ Done / हो गया", "bill:cancel": "❌ Cancel / रद्द",
                 "pay:cash": "💵 Cash / नकद", "pay:udhaar": "🤝 Udhaar / उधार",
                 "exp:Udhaar": "🤝 Udhaar", "exp:Bills": "🧾 Bills", "exp:Entries": "📒 Entries",
                 "exp:Dues": "📊 Dues", "exp:Rates": "🏷️ Rates", "exp:Customers": "👥 Customers",
                 "exp:all": "📚 All tables", "fmt:pdf": "📄 PDF", "fmt:xlsx": "📊 Excel",
                 "dir:in": "📥 Came in / आए", "dir:out": "📤 Went out / गए", "today": "📅 Dated today",
                 "sheet:skip": "⏭️ Skip, no sheet / शीट नहीं चाहिए",
                 "reset:yes": "🗑️ Yes, clear it / हाँ, मिटाओ", "reset:no": "↩️ No, keep it / नहीं"}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ERROR_REPLY = "Something went wrong. Nothing was saved. Please try again."
PRIVATE_ONLY = "Please message me privately. Khata only works in a one-to-one chat."
UNSUPPORTED = "I can read text, voice notes and bill photos."
TOO_LARGE = "That photo is too large. Please send a smaller one."
RATE_PER_MIN = 30
COMMANDS = {"start": "help", "help": "help", "export": "export",
            "list": "list", "undo": "undo", "remind": "remind",
            "expenses": "kharcha", "kharcha": "kharcha", "month": "kharcha",
            "bill": "bill", "rates": "rates", "dues": "dues", "udhaar": "dues", "hisaab": "dues", "today": "today", "aaj": "today"}  # "id": adapter
# Shown in Telegram's "/" menu (setMyCommands).
MENU = [("bill", "Make a bill / बिल बनाओ"), ("dues", "Udhaar balances / उधार हिसाब"),
        ("rates", "Your item rates / रेट लिस्ट"), ("today", "Today's expenses / आज का खर्च"),
        ("expenses", "This month's expenses / महीने का खर्च"), ("remind", "Reminder links / तगादा"),
        ("undo", "Undo last entry / पिछली एंट्री हटाओ"), ("export", "Excel file / एक्सेल"),
        ("sheet", "Connect your Google Sheet / शीट जोड़ो"), ("invite", "Add another phone to this shop"),
        ("reset", "Start a fresh khata / नया खाता"), ("help", "How to use / मदद"), ("id", "Your chat id")]

WELCOME = ("🙏 Namaste! I'm Ledgy, your khata.\n"
           "Type, send a voice note, or a photo of a bill: udhaar, payments, shop expenses and bills. "
           "Nothing is saved until you tap Yes.\n"
           "नमस्ते! मैं आपका खाता हूँ। लिखिए, बोलिए या बिल की फोटो भेजिए।\n\n")
SHEET_STEPS = ("📗 Keep your khata in your own Google Sheet too (optional, 1 minute):\n"
               "1. Open https://sheets.new\n"
               "2. Tap Share, paste the email from my next message, choose Editor, tap Send.\n"
               "3. Copy the sheet's link and paste it here.\n\n"
               "📗 अपनी Google Sheet में भी खाता रखें (ज़रूरी नहीं):\n"
               "1. https://sheets.new खोलें\n"
               "2. Share दबाएँ, मेरे अगले मैसेज वाला ईमेल डालें, Editor चुनें, Send दबाएँ।\n"
               "3. शीट का लिंक कॉपी करके यहाँ भेजें।\n\n"
               "👇 Press and hold to copy / दबाकर रखें और कॉपी करें:")
SKIP_SHEET = ("Okay, no sheet. You can connect one any time with /sheet. Until then, /export gives you "
              "a PDF or Excel copy.")
SHEET_LINK = re.compile(r"docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]{20,}")

log = logging.getLogger("khata.telegram")

_INT_RE = re.compile(r"-?[0-9]+")


def _chat_int(value: int | str) -> int:
    """Strict integer parse: ints, or strings of ASCII digits with an optional leading '-'."""
    if isinstance(value, bool):
        raise ValueError("chat id must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError("chat id must be an integer")


def owner_for_chat(chat_id: int | str, salt: str) -> str:
    """'tg-' + sha256(f'{salt}:tg:{int(chat_id)}').hexdigest()[:24]. Raises ValueError on a non-integer id."""
    cid = _chat_int(chat_id)
    return "tg-" + hashlib.sha256(f"{salt}:tg:{cid}".encode()).hexdigest()[:24]


def parse_allowed(raw: str | None) -> frozenset[int]:
    """'123, 456' -> {123, 456}; '' or None -> empty set. Raises ValueError on any non-integer entry."""
    if not raw or not raw.strip():
        return frozenset()
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not _INT_RE.fullmatch(part):
            raise ValueError("TELEGRAM_ALLOWED_CHAT_IDS must be comma-separated integers")
        out.add(int(part))
    return frozenset(out)


def command_of(text: str) -> str | None:
    """'/Export@KhataBot extra' -> 'export'; returns None when text does not start with '/'."""
    if not isinstance(text, str) or not text.startswith("/"):
        return None
    rest = text[1:]
    if not rest or rest[0].isspace():
        return ""                       # a bare "/" or "/ text" is no command
    return rest.split(maxsplit=1)[0].split("@", 1)[0].lower()


def pick_photo(sizes: list[dict], limit: int) -> dict | None:
    """Largest size (by width*height, then file_size) with file_size <= limit; missing file_size counts as 0."""
    best, best_key = None, None
    for s in sizes or []:
        if not isinstance(s, dict):
            continue
        fsize = int(s.get("file_size") or 0)
        if fsize > limit:
            continue
        key = (int(s.get("width") or 0) * int(s.get("height") or 0), fsize)
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best


def webhook_token(secret: str) -> str:
    """sha256('khata-tg:' + secret).hexdigest(): 64 chars from [0-9a-f], valid as Telegram's secret_token."""
    return hashlib.sha256(("khata-tg:" + secret).encode()).hexdigest()


def next_delay(delay: float) -> float:
    """Backoff step: min(60, delay * 2)."""
    return min(60.0, delay * 2)


class TelegramError(Exception):
    """'<method>: <Telegram description or exception type name>'. Never contains the bot token."""


class TelegramAPI:
    def __init__(self, token: str, http=None):
        self._token = token
        self.http = http if http is not None else httpx.Client(timeout=35)  # > 25 s long poll

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _call(self, method: str, *, json=None, data=None, files=None, timeout: float = 20):
        url = f"{API_BASE}/bot{self._token}/{method}"
        try:
            r = self.http.post(url, json=json, data=data, files=files, timeout=timeout)
            body = r.json()
            if not isinstance(body, dict):
                raise ValueError("response is not an object")
        except Exception as ex:
            log.warning("telegram %s failed: %s", method, type(ex).__name__)
            raise TelegramError(f"{method}: {type(ex).__name__}") from None
        if not body.get("ok"):
            desc = self._scrub(str(body.get("description") or "error"))
            log.warning("telegram %s failed: %s", method, desc)
            raise TelegramError(f"{method}: {desc}")
        return body.get("result")

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        result = self._call("getUpdates", json={"offset": offset, "timeout": timeout,
                                                "allowed_updates": ALLOWED_UPDATES},
                            timeout=timeout + 10)
        return list(result or [])

    def send_message(self, chat_id: int, text: str, buttons: list[str] | None = None) -> int:
        body: dict = {"chat_id": chat_id, "text": text[:4096]}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": [[
                {"text": BUTTON_TITLES.get(b, b), "callback_data": b[:64]} for b in buttons]]}
        result = self._call("sendMessage", json=body)
        return int(result["message_id"])

    def answer_callback(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", json={"callback_query_id": callback_id})

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self._call("editMessageReplyMarkup", json={"chat_id": chat_id, "message_id": message_id,
                                                   "reply_markup": {"inline_keyboard": []}})

    def download(self, file_id: str) -> tuple[bytes, str]:
        meta = self._call("getFile", json={"file_id": file_id}) or {}
        path = str(meta.get("file_path") or "")
        if int(meta.get("file_size") or 0) > FILE_MAX:
            raise TelegramError("download: file too large")
        if not path:
            raise TelegramError("download: no file path")
        try:
            r = self.http.get(f"{API_BASE}/file/bot{self._token}/{path}", timeout=30)
            status = int(getattr(r, "status_code", 200))
            content = bytes(r.content)
        except Exception as ex:
            log.warning("telegram download failed: %s", type(ex).__name__)
            raise TelegramError(f"download: {type(ex).__name__}") from None
        if status >= 400:
            log.warning("telegram download failed: HTTP %s", status)
            raise TelegramError(f"download: HTTP {status}")
        if len(content) > FILE_MAX:
            raise TelegramError("download: file too large")
        return content, path

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str = "",
                      mime: str = XLSX_MIME) -> None:
        self._call("sendDocument", data={"chat_id": str(chat_id), "caption": caption[:1024]},
                   files={"document": (filename, data, mime)}, timeout=60)

    def set_webhook(self, url: str, secret_token: str) -> None:
        self._call("setWebhook", json={"url": url, "secret_token": secret_token,
                                       "allowed_updates": ALLOWED_UPDATES,
                                       "drop_pending_updates": False})

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self._call("setMyCommands", json={"commands": [{"command": c, "description": d[:256]} for c, d in commands]})

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", json={})


class TelegramAdapter:
    """Turns Telegram updates into `Bot.handle` / `on_image` calls and sends the replies.

    Never touches the Ledger: every save goes through the Bot's confirm step. One lock
    serialises updates from the poller thread and webhook background tasks.
    """

    def __init__(self, bot, voice, api, *, on_image: Callable | None,
                 export: Callable[[str], bytes] | None, salt: str, sheets=None,
                 allowed: frozenset[int] = frozenset(), clock=time.monotonic,
                 image_limit: int = bills.MAX_IMAGE):
        self.bot, self.voice, self.api = bot, voice, api
        self.on_image, self.export = on_image, export
        self.sheets = sheets                     # SheetPool, or None when Google Sheets is off
        self._salt = salt
        self.allowed = frozenset(allowed)
        self.clock = clock
        self.image_limit = image_limit
        self.lock = threading.Lock()
        self.seen = Seen()
        self.keyboards: dict[int, int] = {}      # chat_id -> Latest_Keyboard message id
        self.hits: dict[int, deque] = defaultdict(deque)
        self.invites: dict[str, tuple[str, float]] = {}    # code -> (shop owner, expires at)

    def handle_update(self, update: dict) -> None:
        with self.lock:
            uid = update.get("update_id") if isinstance(update, dict) else None
            if uid is None or not self.seen.add(str(uid)):
                return
            if "callback_query" in update:
                self._on_callback(update["callback_query"])
            elif "message" in update:
                self._on_message(update["message"])

    def _owner(self, chat_id: int) -> str:
        """The shop khata this chat writes to (several phones can share one shop)."""
        own = owner_for_chat(chat_id, self._salt)
        ledger = getattr(self.bot, "ledger", None)
        return ledger.shop_of(own) if ledger is not None and hasattr(ledger, "shop_of") else own

    def _link_command(self, chat_id: int, cmd: str, text: str) -> dict | None:
        """/invite on the main phone gives a code; /join CODE on another phone links it to the shop."""
        ledger = getattr(self.bot, "ledger", None)
        if ledger is None or not hasattr(ledger, "link"):
            return None
        own = owner_for_chat(chat_id, self._salt)
        now = self.clock()
        if cmd == "invite":
            shop = ledger.shop_of(own)
            code = f"{secrets.randbelow(900000) + 100000}"
            self.invites = {c: v for c, v in self.invites.items() if v[1] > now}
            self.invites[code] = (shop, now + 600)
            return {"reply": f"🔗 To add another phone to this shop, send this from that phone within 10 minutes:\n"
                             f"/join {code}\nEverything it records will go into this khata and Google Sheet."}
        if cmd == "join":
            parts = text.split()
            code = parts[1] if len(parts) > 1 else ""
            hit = self.invites.pop(code, None)
            if not hit or hit[1] < now:
                return {"reply": "That code is wrong or has expired. Ask the main phone to send /invite again."}
            shop = hit[0]
            if shop == own:
                return {"reply": "This phone is already the main phone of this shop."}
            if self.sheets and self.sheets.get(own):
                self.sheets.disconnect(own)       # its records move into the shop's sheet from now on
            moved = ledger.link(own, shop)
            return {"reply": f"✅ This phone is now linked to the shop. {moved} earlier record(s) were moved into the "
                             "shop's khata. Send /leave to unlink."}
        if cmd == "leave":
            return {"reply": "Unlinked. This phone has its own khata again." if ledger.unlink(own)
                    else "This phone is not linked to another shop."}
        return None

    # ---- each shop's own Google Sheet ---------------------------------------------------
    def _is_main(self, chat_id: int, owner: str) -> bool:
        return owner_for_chat(chat_id, self._salt) == owner

    def _wants_sheet(self, chat_id: int, owner: str) -> bool:
        """Sheets are on, this is the shop's main phone, and no sheet is connected yet."""
        return bool(self.sheets) and self._is_main(chat_id, owner) and self.sheets.get(owner) is None

    def _welcome(self, chat_id: int, owner: str, greet: bool = True) -> None:
        """The steps, then the bot's email alone in its own message so a long-press copies just it."""
        self._send(chat_id, owner, {"reply": (WELCOME if greet else "") + SHEET_STEPS})
        self._send(chat_id, owner, {"reply": self.sheets.email, "buttons": ["sheet:skip"]})

    def _sheet_command(self, chat_id: int, owner: str, text: str) -> dict:
        """/sheet: status and how to connect. /sheet <link>: connect. /sheet off: disconnect."""
        from .sheets import SheetError, sheet_id_of
        pool = self.sheets
        if pool is None:
            return {"reply": "Google Sheets is not set up on this server. /export still gives you an Excel file."}
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        how = (f"1. Make a new Google Sheet (sheets.new).\n"
               f"2. Share it with {pool.email} as Editor.\n"
               f"3. Send /sheet and the sheet's link here.")
        if not arg:
            link = pool.link(owner)
            if link:
                m = pool.get(owner)
                state = " (paused: could not read it, try /sheet <link> again)" if m and m.paused else ""
                return {"reply": f"📗 Your khata is kept in this sheet{state}:\n{link}\n\n"
                                 "To move to a new sheet:\n" + how + "\nSend /sheet off to stop using a sheet."}
            return {"reply": "📗 Keep your khata in your own Google Sheet (you can edit it there too):\n" + how}
        if not self._is_main(chat_id, owner):
            return {"reply": "Only the shop's main phone can change the sheet."}
        if arg.lower() in ("off", "stop", "band"):
            if not pool.disconnect(owner):
                return {"reply": "No sheet is connected."}
            return {"reply": "Sheet disconnected. It is left as it is, and new entries are no longer written to it.\n"
                             "⚠️ This free server forgets data when it restarts. Connect a sheet again with /sheet <link> "
                             "to keep your khata safe."}
        sid = sheet_id_of(arg)
        if not sid:
            return {"reply": "That doesn't look like a Google Sheet link. It should look like "
                             "https://docs.google.com/spreadsheets/d/..."}
        if sid == pool.sheet_ids.get(owner):
            return {"reply": "That sheet is already connected."}
        try:
            r = pool.connect(owner, sid)
        except SheetError as e:
            why = {"no access": f"I can't open that sheet. Share it with {pool.email} as Editor, then send the link again.",
                   "read only": f"I can read that sheet but can't write to it. Share it with {pool.email} as Editor "
                                "(not Viewer), then send the link again.",
                   "both have data": "That sheet already has a khata in it, and so does this chat. I won't mix them.\n"
                                     "Use an empty sheet, or send /reset first to start fresh from that sheet.",
                   }.get(str(e), "Google Sheets didn't answer. Please try again in a minute.")
            return {"reply": why}
        n = r.get("restored", 0)
        got = f" I read {n} record(s) from it into your khata." if n else " Your khata is in it now."
        return {"reply": f"✅ Sheet connected.{got}\nYou can edit Udhaar, Entries, Rates and Customers right in the sheet.\n"
                         f"{pool.link(owner)}"}

    def _reset_command(self, chat_id: int, owner: str) -> dict:
        if not self._is_main(chat_id, owner):
            return {"reply": "Only the shop's main phone can clear the khata. Send /leave to unlink this phone."}
        sheet = (" Your Google Sheet will be disconnected and left as it is, as a backup."
                 if self.sheets and self.sheets.get(owner) else "")
        return {"reply": "⚠️ This deletes every udhaar, entry, bill, rate and customer in this khata. It can't be undone."
                         f"{sheet}\nStart fresh?", "buttons": ["reset:yes", "reset:no"]}

    def _reset_confirm(self, chat_id: int, owner: str, yes: bool) -> dict:
        if not yes:
            return {"reply": "Okay, nothing was deleted."}
        if not self._is_main(chat_id, owner):
            return {"reply": "Only the shop's main phone can clear the khata."}
        had_sheet = bool(self.sheets and self.sheets.disconnect(owner))   # first, so the sheet is never blanked
        self.bot.ledger.delete_owner_data(owner)
        for d in (self.bot.pending, self.bot.drafts, self.bot.export_choice, self.bot.waiting_mode):
            d.pop(owner, None)
        old = " Your old sheet was disconnected and kept as it is." if had_sheet else ""
        nxt = " Send /sheet to connect a new Google Sheet." if self.sheets else ""
        return {"reply": f"🗑️ Khata cleared. You're starting fresh.{old}{nxt}"}

    def _admit(self, chat: dict) -> bool:
        chat_id = chat["id"]
        if self.allowed and chat_id not in self.allowed:
            return False                                  # silent: do not reveal the bot
        if chat.get("type") != "private":
            self._send(chat_id, None, {"reply": PRIVATE_ONLY})
            return False
        now = self.clock()
        q = self.hits[chat_id]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return False                                  # silent drop
        q.append(now)
        return True

    def _on_message(self, msg: dict) -> None:
        chat = (msg or {}).get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        owner = None
        try:
            if not self._admit(chat):
                return
            owner = self._owner(chat_id)
            text = msg.get("text")
            if isinstance(text, str):
                cmd = command_of(text)
                if cmd in ("invite", "join", "leave"):
                    r = self._link_command(chat_id, cmd, text)
                    if r:
                        return self._send(chat_id, owner, r)
                if cmd == "start" and self._wants_sheet(chat_id, owner):
                    return self._welcome(chat_id, owner)
                if cmd == "sheet":
                    if len(text.split()) == 1 and self._wants_sheet(chat_id, owner):
                        return self._welcome(chat_id, owner, greet=False)
                    return self._send(chat_id, owner, self._sheet_command(chat_id, owner, text))
                if cmd is None and SHEET_LINK.search(text) and self._wants_sheet(chat_id, owner):
                    # a pasted sheet link, after the welcome steps: connect it, no command needed
                    return self._send(chat_id, owner, self._sheet_command(chat_id, owner, "/sheet " + text))
                if cmd == "reset":
                    return self._send(chat_id, owner, self._reset_command(chat_id, owner))
                if cmd == "id":
                    return self._send(chat_id, owner, {"reply": (
                        f"Your chat id is {chat_id}. To keep your khata in a Google Sheet, send /sheet.")})
                if cmd in COMMANDS:
                    return self._send(chat_id, owner,
                                      self.bot.handle(owner, COMMANDS[cmd], source="telegram"))
                return self._send(chat_id, owner,
                                  self.bot.handle(owner, text[:1000], source="telegram"))
            media = msg.get("voice") or msg.get("audio")
            if isinstance(media, dict):
                return self._on_voice(chat_id, owner, media)
            if msg.get("photo") and self.on_image:
                size = pick_photo(msg["photo"], self.image_limit)
                if size is None:
                    return self._send(chat_id, owner, {"reply": TOO_LARGE})
                data, _ = self.api.download(size["file_id"])
                return self._send(chat_id, owner, self.on_image(owner, data, "image/jpeg"))
            doc = msg.get("document")
            if (isinstance(doc, dict) and self.on_image
                    and str(doc.get("mime_type") or "").startswith("image/")):
                if int(doc.get("file_size") or 0) > self.image_limit:
                    return self._send(chat_id, owner, {"reply": TOO_LARGE})
                data, _ = self.api.download(doc["file_id"])
                return self._send(chat_id, owner, self.on_image(owner, data, doc["mime_type"]))
            return self._send(chat_id, owner, {"reply": UNSUPPORTED})
        except Exception as ex:
            log.warning("telegram message failed: %s", type(ex).__name__)
            try:
                self._send(chat_id, owner, {"reply": ERROR_REPLY})
            except Exception as ex2:
                log.warning("telegram error reply failed: %s", type(ex2).__name__)

    def _on_voice(self, chat_id: int, owner: str, media: dict) -> None:
        data, path = self.api.download(media["file_id"])
        suffix = Path(path).suffix.lower() or ".oga"
        try:
            text = self.voice.transcribe(data, suffix)
        except VoiceError as e:
            return self._send(chat_id, owner, {"reply": str(e)})
        self._send(chat_id, owner, {"reply": f"🎙️ Heard: “{text}”"})
        self._send(chat_id, owner, self.bot.handle(owner, text, source="voice"))

    def _on_callback(self, cq: dict) -> None:
        """A button tap. Only the chat's Latest_Keyboard is honoured.

        A tap on any older message is stale: its keyboard is cleared and the Bot is not
        called, so an old "Yes" can't become a customer named "Yes" or confirm a newer entry.
        """
        cq = cq if isinstance(cq, dict) else {}
        try:
            self.api.answer_callback(str(cq.get("id", "")))
        except Exception as ex:
            log.warning("telegram answerCallbackQuery failed: %s", type(ex).__name__)
        msg = cq.get("message") if isinstance(cq.get("message"), dict) else {}
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id, mid = chat.get("id"), msg.get("message_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        owner = None
        try:
            if not self._admit(chat):
                return
            if mid is None or self.keyboards.get(chat_id) != mid:
                if mid is not None:
                    self._clear(chat_id, mid)             # stale: Bot not called
                return
            self._clear(chat_id, mid)
            del self.keyboards[chat_id]
            owner = self._owner(chat_id)
            data = str(cq.get("data", ""))[:64]
            if data == "sheet:skip":
                return self._send(chat_id, owner, {"reply": SKIP_SHEET + "\n\n" + self.bot.handle(
                    owner, "help", source="telegram")["reply"]})
            if data in ("reset:yes", "reset:no"):
                return self._send(chat_id, owner, self._reset_confirm(chat_id, owner, data == "reset:yes"))
            self._send(chat_id, owner, self.bot.handle(owner, data, source="telegram"))
        except Exception as ex:
            log.warning("telegram callback failed: %s", type(ex).__name__)
            try:
                self._send(chat_id, owner, {"reply": ERROR_REPLY})
            except Exception as ex2:
                log.warning("telegram error reply failed: %s", type(ex2).__name__)

    def _clear(self, chat_id: int, mid: int) -> None:
        """Best effort: Telegram refuses edits on old or already-cleared messages."""
        try:
            self.api.clear_buttons(chat_id, mid)
        except Exception as ex:
            log.warning("telegram clear_buttons failed: %s", type(ex).__name__)

    def _send(self, chat_id: int, owner: str | None, r: dict) -> None:
        """The only way replies leave the adapter.

        Clears the chat's previous keyboard, sends, and records the new message id as the
        Latest_Keyboard only when `r` has buttons (otherwise the chat has none).
        Task 8.2 adds the `r["export"]` document.
        """
        prev = self.keyboards.pop(chat_id, None)
        if prev is not None:
            self._clear(chat_id, prev)
        buttons = r.get("buttons") or None
        mid = self.api.send_message(chat_id, r["reply"], buttons)
        if buttons:
            self.keyboards[chat_id] = mid
        if r.get("file"):
            self.api.send_document(chat_id, r.get("file_name") or "khata", r["file"], r.get("file_name", ""),
                                   mime=r.get("file_mime") or XLSX_MIME)
        elif r.get("export") and owner and self.export:
            self.api.send_document(chat_id, "khata.xlsx", self.export(owner), "khata.xlsx")


class Poller:
    """Long-polls getUpdates on a daemon thread (local runs with no public URL).

    The offset advances before each update is handled, so an update that crashes the adapter
    is not fetched again in a loop. A long poll can block up to `timeout` seconds after
    `stop()`; the thread is a daemon, so shutdown waits at most `join_timeout`.
    """

    def __init__(self, adapter, api, *, timeout: int = 25,
                 stop: threading.Event | None = None):
        self.adapter, self.api, self.timeout = adapter, api, timeout
        self.offset: int | None = None
        self.stop_event = stop if stop is not None else threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> int:
        updates = sorted(self.api.get_updates(self.offset, self.timeout),
                         key=lambda u: u["update_id"])
        for u in updates:
            self.offset = u["update_id"] + 1
            try:
                self.adapter.handle_update(u)
            except Exception as ex:
                log.warning("telegram update failed: %s", type(ex).__name__)
        return len(updates)

    def run(self) -> None:
        try:
            self.api.delete_webhook()
        except Exception as ex:
            log.warning("telegram deleteWebhook failed: %s", type(ex).__name__)
        delay = 1.0
        while not self.stop_event.is_set():
            try:
                self.run_once()
                delay = 1.0
            except Exception as ex:
                log.warning("telegram getUpdates failed: %s", type(ex).__name__)
                self.stop_event.wait(delay)
                delay = next_delay(delay)

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run, name="telegram-poller", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, join_timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(join_timeout)


def make_router(adapter: TelegramAdapter | None, secret: str | None) -> APIRouter:
    """`POST /webhook/telegram` for webhook mode.

    Telegram sends `webhook_token(secret)` back in `X-Telegram-Bot-Api-Secret-Token`. The update
    is handled in a background task so Telegram gets its 200 quickly.
    """
    r = APIRouter()

    @r.post("/webhook/telegram")
    async def incoming(request: Request, bg: BackgroundTasks):
        if adapter is None or not secret:
            raise HTTPException(503, "telegram not configured")
        got = request.headers.get("x-telegram-bot-api-secret-token", "")
        # Compare bytes so a non-ASCII header cannot raise inside compare_digest.
        if not hmac.compare_digest(got.encode(), webhook_token(secret).encode()):
            raise HTTPException(401, "bad secret")
        try:
            update = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "bad json")
        if not isinstance(update, dict):
            raise HTTPException(400, "bad json")
        bg.add_task(adapter.handle_update, update)
        return {"ok": True}

    return r
