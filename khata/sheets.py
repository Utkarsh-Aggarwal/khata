"""Google Sheet view of one shopkeeper's khata. SQLite stays the source of truth.

The Transactions tab and the Dues tab are redrawn from the database whenever anything
changes (khata/render.py), so the sheet, the Excel export and the bot always agree. Hand
edits in the sheet are overwritten. Tabs from older layouts (Khata, Summary) are removed
once the new ones are written.

On free hosting the disk is wiped on restart, so on boot an empty database is rebuilt from the
sheet (restore). Restore reads the current layout and both older ones. If restore fails, the
mirror pauses: it never redraws the sheet from an empty database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Protocol

from . import render
from .ledger import Ledger

TAB, DUES_TAB = render.TX_TAB, render.DUES_TAB
LEGACY_TABS = ("Khata", "Summary")
LAYOUT_V1 = ["entry_id", "date", "customer", "kind"]        # first columns of the oldest layout
LAYOUT_V2 = ["id", "date", "type", "name"]                  # 9-column layout used briefly
LAYOUT_V3 = ["ref", "date", "type", "name", "category"]     # 13-column layout used briefly
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SheetClient(Protocol):
    def read_tab(self, tab: str) -> list[list]: ...        # every row, header included
    def write_tab(self, tab: str, rows: list[list]) -> None: ...   # replace the tab's contents
    def delete_tab(self, tab: str) -> None: ...


class GoogleSheetClient:
    """Sheets API v4 via a service account (share the sheet with the service account email)."""
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, spreadsheet_id: str, creds_file: str | None = None, tab: str = TAB,
                 info: dict | None = None):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = (service_account.Credentials.from_service_account_info(info, scopes=scopes) if info
                 else service_account.Credentials.from_service_account_file(creds_file, scopes=scopes))
        self.s = AuthorizedSession(creds)
        self.id, self.tab = spreadsheet_id, tab

    def _values(self, rng: str, suffix: str = "") -> str:
        from urllib.parse import quote
        return f"{self.API}/{self.id}/values/{quote(rng)}{suffix}"

    def _ensure_tab(self, tab: str) -> None:
        meta = self.s.get(f"{self.API}/{self.id}", params={"fields": "sheets.properties.title"}, timeout=20)
        meta.raise_for_status()
        if tab not in {sh["properties"]["title"] for sh in meta.json().get("sheets", [])}:
            self.s.post(f"{self.API}/{self.id}:batchUpdate", timeout=20, json={"requests": [
                {"addSheet": {"properties": {"title": tab}}}]}).raise_for_status()

    def read_tab(self, tab: str) -> list[list]:
        r = self.s.get(self._values(f"{tab}!A:Z"), timeout=30, params={"valueRenderOption": "UNFORMATTED_VALUE"})
        if r.status_code == 400:            # tab doesn't exist yet
            return []
        r.raise_for_status()
        return r.json().get("values", [])

    def write_tab(self, tab: str, rows: list[list]) -> None:
        self._ensure_tab(tab)
        self.s.post(self._values(f"{tab}!A:Z", ":clear"), timeout=20, json={}).raise_for_status()
        if rows:
            self.s.put(self._values(f"{tab}!A1"), params={"valueInputOption": "RAW"},
                       json={"values": rows}, timeout=30).raise_for_status()


    def delete_tab(self, tab: str) -> None:
        meta = self.s.get(f"{self.API}/{self.id}", params={"fields": "sheets.properties(title,sheetId)"}, timeout=20)
        meta.raise_for_status()
        sheets = meta.json().get("sheets", [])
        ids = {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in sheets}
        if tab in ids and len(sheets) > 1:
            self.s.post(f"{self.API}/{self.id}:batchUpdate", timeout=20, json={"requests": [
                {"deleteSheet": {"sheetId": ids[tab]}}]}).raise_for_status()


def _money(v) -> int:
    return round(float(str(v).replace(",", "").replace("₹", "")) * 100)


def _parse_v2(r: list) -> dict:
    # id, date, type, name, category, rupees, due, status, note
    r = list(r) + [""] * 9
    sid = str(r[0]).strip()
    kind = str(r[2]).strip()
    return {"id": int(sid[1:]) if kind == "expense" else int(float(sid)), "kind": kind, "date": str(r[1])[:10],
            "name": str(r[3]).strip(), "category": str(r[4]).strip(), "paise": _money(r[5]),
            "due": str(r[6])[:10] or None, "status": str(r[7]).strip() or "active", "note": str(r[8]),
            "via": "sheet", "recorded": ""}


def _parse_v1(r: list) -> dict:
    # entry_id, date, customer (or category), kind, rupees, due, status, note (bill vendor for expenses)
    r = list(r) + [""] * 8
    sid, kind, note = str(r[0]).strip(), str(r[3]).strip(), str(r[7])
    if kind == "expense":
        vendor = note[6:].strip() if note.startswith("bill: ") else ""
        return {"id": int(sid[1:]), "kind": kind, "date": str(r[1])[:10], "name": vendor,
                "category": str(r[2]).strip(), "paise": _money(r[4]), "due": None,
                "status": str(r[6]).strip() or "active", "note": "bill photo" if vendor else note,
                "via": "sheet", "recorded": ""}
    return {"id": int(float(sid)), "kind": kind, "date": str(r[1])[:10], "name": str(r[2]).strip(), "category": "",
            "paise": _money(r[4]), "due": str(r[5])[:10] or None, "status": str(r[6]).strip() or "active",
            "note": note, "via": "sheet", "recorded": ""}


def _parse_v3(r: list) -> dict:
    # Ref, Date, Type, Name, Category, Out, In, Balance after, Due, Status, Via, Note, Recorded on
    r = list(r) + [""] * 13
    ref = str(r[0]).strip().upper()
    kind = {"udhaar given": "credit", "payment received": "payment", "expense": "expense"}.get(str(r[2]).strip().lower())
    if kind is None or ref[:2] not in ("K-", "E-"):
        raise ValueError(ref)
    cat = str(r[4]).strip().lower()
    return {"id": int(ref[2:]), "kind": kind, "date": str(r[1])[:10], "name": str(r[3]).strip(),
            "category": render.CATEGORY_OF_LABEL.get(cat, cat),
            "paise": _money(r[6] if kind == "payment" else r[5]), "due": str(r[8])[:10] or None,
            "status": str(r[9]).strip() or "active", "note": str(r[11]),
            "via": render.VIA_OF_LABEL.get(str(r[10]).strip().lower(), "sheet"), "recorded": str(r[12])[:10]}


class SheetMirror:
    def __init__(self, ledger: Ledger, client: SheetClient, owner: str, clock=time.time):
        self.ledger, self.client, self.owner, self.clock = ledger, client, owner, clock
        self.paused = False          # set when restore failed: never redraw the sheet blind
        self.boot_result: dict = {}
        self._written: str | None = None     # fingerprint of what the sheet shows now
        self._next_at = 0.0
        self._fails = 0
        self._wake = threading.Event()

    # ---- boot and restore --------------------------------------------------------------
    def boot(self, attempts: int = 2, wait: float = 3.0) -> dict:
        """Call before serving requests. Restores an empty database from the sheet; pauses the
        mirror if that fails, so a wiped database can never overwrite the shopkeeper's sheet."""
        for n in range(attempts):
            try:
                self.boot_result = self.restore()
                self.paused = False
                return self.boot_result
            except Exception as ex:      # network, auth, quota
                self.boot_result = {"error": type(ex).__name__}
                if n + 1 < attempts:
                    time.sleep(wait)
        self.paused = True
        return self.boot_result

    def restore(self) -> dict:
        with self.ledger.lock:
            if (self.ledger.db.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
                    or self.ledger.db.execute("SELECT 1 FROM expenses LIMIT 1").fetchone()):
                return {"restored": 0, "skipped": 0, "reason": "database not empty"}
        rows = self.client.read_tab(TAB) or self.client.read_tab("Khata")
        if not rows:
            return {"restored": 0, "skipped": 0, "reason": "new sheet"}
        head = [str(c).strip() for c in rows[0]]
        low = [c.lower() for c in head]
        if head[:len(render.COLUMNS)] == render.COLUMNS:
            parse = render.parse_row
        elif low[:5] == LAYOUT_V3:
            parse = _parse_v3
        elif low[:4] == LAYOUT_V2:
            parse = _parse_v2
        elif low[:4] == LAYOUT_V1 or low[:1] == ["entry_id"]:
            parse = _parse_v1
        else:
            return {"restored": 0, "skipped": len(rows), "reason": "unknown sheet layout"}
        restored = skipped = 0
        with self.ledger.lock:
            for row in rows[1:]:
                if not row or not str(row[0]).strip():
                    continue
                try:
                    self._insert(parse(row))
                    restored += 1
                except (ValueError, TypeError, IndexError, sqlite3.IntegrityError):
                    skipped += 1
            self.ledger.db.commit()
        return {"restored": restored, "skipped": skipped}

    def _insert(self, d: dict) -> None:
        """Validate one parsed row and insert it with its original id. Caller holds the lock."""
        if (d["kind"] not in ("credit", "payment", "expense") or d["status"] not in ("active", "cancelled")
                or not 0 < d["paise"] <= 10**9 or not DATE.fullmatch(d["date"]) or d["id"] <= 0
                or (d["due"] and not DATE.fullmatch(d["due"]))):
            raise ValueError(d)
        created = (d["recorded"] if DATE.fullmatch(d.get("recorded") or "") else d["date"]) + "T00:00:00+00:00"
        if d["kind"] == "expense":
            self.ledger.db.execute(
                "INSERT INTO expenses(id,owner,category,paise,note,vendor,entry_date,status,source,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (d["id"], self.owner, (d["category"] or "other")[:40], d["paise"], d["note"][:200],
                 d["name"][:60], d["date"], d["status"], d["via"], created))
            return
        if not d["name"]:
            raise ValueError(d)
        cid = self.ledger.get_or_create_customer(self.owner, d["name"])
        self.ledger.db.execute(
            "INSERT INTO entries(id,owner,customer_id,kind,paise,note,entry_date,due_date,status,source,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (d["id"], self.owner, cid, d["kind"], d["paise"], d["note"][:200], d["date"], d["due"], d["status"],
             d["via"], created))

    # ---- redraw ------------------------------------------------------------------------
    def _views(self) -> tuple[list[list], list[list], str]:
        tx = [render.COLUMNS] + render.transaction_rows(self.ledger, self.owner)
        dues = render.dues_rows(self.ledger, self.owner)
        fp = hashlib.sha256(json.dumps([tx, dues], ensure_ascii=False, default=str).encode()).hexdigest()
        return tx, dues, fp

    def pending(self) -> int:
        """1 if the sheet is behind the database, else 0."""
        return int(self._views()[2] != self._written)

    def sync(self) -> dict:
        if self.paused:
            return {"synced": 0, "failed": 0, "pending": self.pending(), "paused": True}
        khata, summary, fp = self._views()
        if fp == self._written:
            return {"synced": 0, "failed": 0, "pending": 0}
        now = self.clock()
        if now < self._next_at:
            return {"synced": 0, "failed": 0, "pending": 1}
        try:
            self.client.write_tab(TAB, khata)
            self.client.write_tab(DUES_TAB, summary)
            for old in LEGACY_TABS:              # new tabs written: the old layout can go
                try:
                    self.client.delete_tab(old)
                except Exception:
                    pass
        except Exception:                                    # retry later with backoff
            self._fails += 1
            self._next_at = now + min(300, 2 ** self._fails)
            return {"synced": 0, "failed": 1, "pending": 1}
        self._written, self._fails, self._next_at = fp, 0, 0.0
        return {"synced": len(khata) - 1, "failed": 0, "pending": 0}

    def poke(self) -> None:
        """A change was committed: redraw now instead of waiting for the next check."""
        self._wake.set()

    def start(self, every: float = 15.0) -> threading.Thread:
        """Redraw right after each change (via the ledger's listener), and every `every` seconds
        as a fallback for retries. Rapid changes are coalesced into one redraw."""
        self.ledger.listeners.append(self.poke)
        self._wake.set()                            # draw once right away on startup

        def loop():
            while True:
                self._wake.wait(every)
                self._wake.clear()
                time.sleep(0.3)                     # let a burst of changes settle
                try:
                    self.sync()
                except Exception:
                    pass
        t = threading.Thread(target=loop, daemon=True, name="sheet-mirror")
        t.start()
        return t


def from_env(ledger: Ledger, owner: str | None) -> SheetMirror | None:
    """Credentials come from GOOGLE_SERVICE_ACCOUNT_JSON (the key file's contents, handy on
    hosts without files) or GOOGLE_APPLICATION_CREDENTIALS (a path)."""
    sid = os.environ.get("KHATA_SHEET_ID")
    info = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not (sid and owner and (info or path)):
        return None
    return SheetMirror(ledger, GoogleSheetClient(sid, path, info=json.loads(info) if info else None), owner)
