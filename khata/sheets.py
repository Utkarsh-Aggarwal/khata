"""Two-way Google Sheet for one shopkeeper's khata. The shopkeeper may only have the sheet.

Every sync first READS the sheet and applies the shopkeeper's edits to the database, then
redraws the tabs that differ:
  Udhaar, Entries  editable: change a cell to edit, delete a row to cancel it, type a new
                   row (no Ref) to add one.
  Rates, Customers editable: item rates for bills; customers' WhatsApp numbers.
  Bills, Dues      calculated; redrawn every time. Change money in Udhaar/Entries.
  Settings         linked phones, kept so free hosting can restore them.
A row that can't be read is never dropped: it stays with "⚠️ Not saved: <reason>".
Edits are compared with what the bot last wrote, so a bot change is never undone by an
older sheet. Tabs from older layouts (Transactions, Khata, Summary) are removed.

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

TAB = render.UDHAAR_TAB
LEGACY_TABS = ("Transactions", "Khata", "Summary")
LAYOUT_V1 = ["entry_id", "date", "customer", "kind"]        # first columns of the oldest layout
LAYOUT_V2 = ["id", "date", "type", "name"]                  # 9-column layout used briefly
LAYOUT_V3 = ["ref", "date", "type", "name", "category"]     # 13-column layout used briefly
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SheetClient(Protocol):
    def read_tab(self, tab: str) -> list[list]: ...        # every row, header included
    def write_tab(self, tab: str, rows: list[list]) -> None: ...   # replace the tab's contents
    # optional: read_tabs(names) -> {name: rows} in one call
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

    READ = {"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"}

    def read_tabs(self, tabs) -> dict[str, list[list]]:
        """All the given tabs in one request (missing tabs come back empty)."""
        meta = self.s.get(f"{self.API}/{self.id}", params={"fields": "sheets.properties.title"}, timeout=20)
        meta.raise_for_status()
        have = {sh["properties"]["title"] for sh in meta.json().get("sheets", [])}
        want = [t for t in tabs if t in have]
        out = {t: [] for t in tabs}
        if want:
            r = self.s.get(f"{self.API}/{self.id}/values:batchGet", timeout=30,
                           params=[("ranges", f"{t}!A:Z") for t in want] + list(self.READ.items()))
            r.raise_for_status()
            for t, vr in zip(want, r.json().get("valueRanges", [])):
                out[t] = vr.get("values", [])
        return out

    def read_tab(self, tab: str) -> list[list]:
        r = self.s.get(self._values(f"{tab}!A:Z"), timeout=30, params=self.READ)
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
        self._last: dict[str, dict] = {}        # tab -> {key: editable cells} as last written
        self._warn: dict[tuple, str] = {}       # (tab, key) -> reason an edit was refused
        self._orphans: dict[str, list] = {}     # tab -> rows the shopkeeper typed that couldn't be read
        self._imported: dict[str, str] = {}     # fingerprint of a typed row -> Ref it created
        self._legacy_done = False
        self.last_import: dict = {}

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
        udhaar, entries = self.client.read_tab(render.UDHAAR_TAB), self.client.read_tab(render.ENTRIES_TAB)
        if udhaar or entries:
            r = self._restore_split(udhaar, entries, self.client.read_tab(render.BILLS_TAB))
            self._restore_extras()
            return r
        rows = self.client.read_tab("Transactions") or self.client.read_tab("Khata")
        if not rows:
            return {"restored": 0, "skipped": 0, "reason": "new sheet"}
        head = [str(c).strip() for c in rows[0]]
        low = [c.lower() for c in head]
        if head[:len(render.COLUMNS)] == render.COLUMNS:
            parse = lambda r: render.parse_row(head, r)  # noqa: E731
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

    def _restore_split(self, udhaar, entries, bills) -> dict:
        restored = skipped = 0
        with self.ledger.lock:
            for tab in (udhaar, entries):
                if not tab:
                    continue
                head = [str(c).strip() for c in tab[0]]
                for row in tab[1:]:
                    if not row or not str(row[0]).strip():
                        continue
                    try:
                        self._insert(render.parse_row(head, row))
                        restored += 1
                    except (ValueError, TypeError, IndexError, sqlite3.IntegrityError):
                        skipped += 1
            for row in (bills or [])[1:]:
                if not row or not str(row[0]).strip():
                    continue
                try:
                    b = render.parse_bill(row)
                    if not DATE.fullmatch(b["date"]) or b["id"] <= 0 or b["total"] <= 0:
                        raise ValueError(row)
                    self.ledger.db.execute(
                        "INSERT INTO bills(id,owner,customer,items,total,payment,bill_date,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (b["id"], self.owner, b["customer"], json.dumps(b["items"], ensure_ascii=False), b["total"],
                         b["payment"], b["date"], b["date"] + "T00:00:00+00:00"))
                    restored += 1
                except (ValueError, TypeError, IndexError, KeyError, sqlite3.IntegrityError):
                    skipped += 1
            self.ledger.db.commit()
        return {"restored": restored, "skipped": skipped}

    def _restore_extras(self) -> None:
        """Rates, customers' WhatsApp numbers and linked phones, which only the sheet remembers."""
        from .reminders import normalize_phone
        for row in self.client.read_tab(render.RATES_TAB)[1:]:
            try:
                self.ledger.set_price(self.owner, str(row[0]).strip(), render.cell_amount(row[1]),
                                      str(row[2]).strip() if len(row) > 2 else "")
            except (ValueError, IndexError):
                pass
        for row in self.client.read_tab(render.CUSTOMERS_TAB)[1:]:
            ph = normalize_phone(str(row[1])) if len(row) > 1 and str(row[1]).strip() else None
            if row and str(row[0]).strip() and ph:
                self.ledger.set_phone(self.owner, str(row[0]).strip(), ph)
        for row in self.client.read_tab(render.SETTINGS_TAB)[1:]:
            if len(row) > 1 and str(row[0]).strip() == "linked phone" and str(row[1]).startswith("tg-"):
                self.ledger.restore_link(str(row[1]).strip(), self.owner)

    def _insert(self, d: dict) -> None:
        """Validate one parsed row and insert it with its original id. Caller holds the lock."""
        if (d["kind"] not in ("credit", "payment", "expense") or d["status"] not in ("active", "cancelled")
                or not 0 < d["paise"] <= 10**9 or not DATE.fullmatch(d["date"]) or d["id"] <= 0
                or (d["due"] and not DATE.fullmatch(d["due"]))):
            raise ValueError(d)
        created = (d["recorded"] if DATE.fullmatch(d.get("recorded") or "") else d["date"]) + "T00:00:00+00:00"
        if d["kind"] == "expense":
            self.ledger.db.execute(
                "INSERT INTO expenses(id,owner,category,paise,note,vendor,entry_date,status,source,created_at,"
                "direction) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], self.owner, (d["category"] or "other")[:40], d["paise"], d["note"][:200],
                 d["name"][:60], d["date"], d["status"], d["via"], created, d.get("direction", "out")))
            return
        if not d["name"]:
            raise ValueError(d)
        cid = self.ledger.get_or_create_customer(self.owner, d["name"])
        self.ledger.db.execute(
            "INSERT INTO entries(id,owner,customer_id,kind,paise,note,entry_date,due_date,status,source,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (d["id"], self.owner, cid, d["kind"], d["paise"], d["note"][:200], d["date"], d["due"], d["status"],
             d["via"], created))

    # ---- two-way sync ----------------------------------------------------------------
    def _views(self) -> tuple[dict, str]:
        tabs = render.all_tabs(self.ledger, self.owner)
        fp = hashlib.sha256(json.dumps(tabs, ensure_ascii=False, default=str).encode()).hexdigest()
        return tabs, fp

    def pending(self) -> int:
        """1 if the sheet is behind the database, else 0."""
        return int(self._views()[1] != self._written)

    def _read(self) -> dict[str, list[list]]:
        if hasattr(self.client, "read_tabs"):
            return self.client.read_tabs(render.TABS)
        return {t: self.client.read_tab(t) for t in render.TABS}

    def sync(self) -> dict:
        if self.paused:
            return {"synced": 0, "failed": 0, "pending": self.pending(), "paused": True}
        now = self.clock()
        if now < self._next_at:
            return {"synced": 0, "failed": 0, "pending": 1}
        try:
            sheet = self._read()
            self.last_import = self._import(sheet)
            tabs, fp = self._views()
            shown = self._decorate(tabs)
            wrote = 0
            for name, rows in shown.items():
                if [[render.norm(c) for c in r] for r in rows] != [[render.norm(c) for c in r] for r in sheet.get(name, [])]:
                    self.client.write_tab(name, rows)
                    wrote += 1
            if not self._legacy_done:
                for old in LEGACY_TABS:              # new tabs written: the old layout can go
                    try:
                        self.client.delete_tab(old)
                    except Exception:
                        pass
                self._legacy_done = True
        except Exception:                                    # retry later with backoff
            self._fails += 1
            self._next_at = now + min(300, 2 ** self._fails)
            return {"synced": 0, "failed": 1, "pending": 1}
        self._snapshot(tabs)
        self._imported.clear()
        self._written, self._fails, self._next_at = fp, 0, 0.0
        n = sum(len(tabs[t]) - 1 for t in (render.UDHAAR_TAB, render.ENTRIES_TAB)) if wrote else 0
        return {"synced": n, "failed": 0, "pending": 0, **({"imported": self.last_import} if any(
            self.last_import.values()) else {})}

    # what the bot wrote last, per editable tab: {key: tuple of editable cells}
    EDIT_COLS = {render.UDHAAR_TAB: ["Date", "Type", "Name", "Amount (₹)", "Due", "Note"],
                 render.ENTRIES_TAB: ["Date", "Type", "Name", "Amount (₹)", "Note"],
                 render.RATES_TAB: ["Item", "Rate (₹)", "Unit"],
                 render.CUSTOMERS_TAB: ["Name", "WhatsApp number"]}
    KEY_COL = {render.UDHAAR_TAB: "Ref", render.ENTRIES_TAB: "Ref", render.RATES_TAB: "Item",
               render.CUSTOMERS_TAB: "Name"}

    def _cells(self, tab, d: dict) -> tuple:
        vals = []
        for c in self.EDIT_COLS[tab]:
            v = d.get(c, "")
            vals.append(render.norm(render.clean_note(v) if c == "Note" else v))
        return tuple(vals)

    def _key(self, tab, d: dict) -> str:
        k = str(d.get(self.KEY_COL[tab], "")).strip()
        return k.upper() if tab in (render.UDHAAR_TAB, render.ENTRIES_TAB) else k.lower()

    def _snapshot(self, tabs: dict) -> None:
        for tab in render.EDITABLE:
            head = tabs[tab][0]
            self._last[tab] = {self._key(tab, d): self._cells(tab, d)
                               for d in (dict(zip(head, r)) for r in tabs[tab][1:])}

    def _decorate(self, tabs: dict) -> dict:
        """Add refusal notes and the rows that couldn't be read, so nothing typed disappears."""
        out = {}
        for name, rows in tabs.items():
            rows = [list(r) for r in rows]
            if name in render.EDITABLE and rows:
                head = rows[0]
                ni = head.index("Note")
                for r in rows[1:]:
                    why = self._warn.get((name, self._key(name, dict(zip(head, r)))))
                    if why:
                        r[ni] = render.WARN + why + (f" | {r[ni]}" if r[ni] else "")
                rows += self._orphans.get(name, [])
            out[name] = rows
        return out

    def _import(self, sheet: dict) -> dict:
        """Apply the shopkeeper's edits. Returns counts of what changed."""
        done = {"edited": 0, "added": 0, "cancelled": 0, "refused": 0}
        current = render.all_tabs(self.ledger, self.owner)
        self._orphans = {}
        for tab in render.EDITABLE:
            rows = sheet.get(tab) or []
            want = current[tab][0]
            if not rows or [str(c).strip() for c in rows[0][:len(want)]] != want:
                continue                              # missing tab or renamed headers: change nothing
            head = [str(c).strip() for c in rows[0]]
            last = self._last.get(tab)
            if last is None:                          # first sync since start: compare with the database
                last = {self._key(tab, d): self._cells(tab, d) for d in (dict(zip(want, r)) for r in current[tab][1:])}
            seen = set()
            for raw in rows[1:]:
                d = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(head)}
                if not any(str(v).strip() for v in d.values()):
                    continue
                key = self._key(tab, d)
                if key and key in last:
                    seen.add(key)
                    if self._cells(tab, d) != last[key]:
                        self._apply_edit(tab, key, d, done)
                    else:
                        self._warn.pop((tab, key), None)
                    continue
                self._apply_new(tab, d, raw, done)
            gone = [k for k in last if k not in seen]
            if gone and tab in (render.UDHAAR_TAB, render.ENTRIES_TAB, render.RATES_TAB):
                if len(gone) > max(3, len(last) // 2):
                    continue                          # looks like an accident (sheet cleared): keep them
                for k in gone:
                    if tab == render.RATES_TAB:
                        self.ledger.delete_price(self.owner, k)
                    elif self.ledger.cancel(self.owner, k):
                        done["cancelled"] += 1
        return done

    def _apply_edit(self, tab, key, d, done) -> None:
        try:
            if tab == render.UDHAAR_TAB:
                ok = self.ledger.update_entry(
                    self.owner, int(key[2:]), kind=render.udhaar_kind(d["Type"]), name=self._name(d["Name"]),
                    paise=render.cell_amount(d["Amount (₹)"]), entry_date=render.cell_date(d["Date"]),
                    due_date=render.cell_date(d["Due"]) if str(d.get("Due", "")).strip() else None,
                    note=render.clean_note(d.get("Note")))
            elif tab == render.ENTRIES_TAB:
                direction, cat = render.entry_kind(d["Type"])
                ok = self.ledger.update_expense(
                    self.owner, int(key[2:]), category=cat, direction=direction, vendor=str(d.get("Name", "")).strip(),
                    paise=render.cell_amount(d["Amount (₹)"]), entry_date=render.cell_date(d["Date"]),
                    note=render.clean_note(d.get("Note")))
            elif tab == render.RATES_TAB:
                self.ledger.set_price(self.owner, self._name(d["Item"]), render.cell_amount(d["Rate (₹)"]),
                                      str(d.get("Unit", "")).strip())
                ok = True
            else:
                ok = self._set_phone(d)
            if ok:
                done["edited"] += 1
                self._warn.pop((tab, key), None)
        except (ValueError, KeyError) as ex:
            self._warn[(tab, key)] = str(ex).strip("'\"") or "this row could not be read"
            done["refused"] += 1

    def _apply_new(self, tab, d, raw, done) -> None:
        """A row typed by the shopkeeper (no Ref yet)."""
        fp = tab + json.dumps([render.norm(c) for c in raw], ensure_ascii=False)
        if fp in self._imported:
            return                                    # already created; the sheet just wasn't redrawn yet
        try:
            if tab == render.UDHAAR_TAB:
                ref = "K-%d" % self.ledger.add_entry(
                    self.owner, self._name(d["Name"]), render.udhaar_kind(d["Type"]),
                    render.cell_amount(d["Amount (₹)"]),
                    entry_date=render.cell_date(d["Date"]) if str(d.get("Date", "")).strip() else None,
                    due_date=render.cell_date(d["Due"]) if str(d.get("Due", "")).strip() else None,
                    note=render.clean_note(d.get("Note")), source="sheet")
            elif tab == render.ENTRIES_TAB:
                direction, cat = render.entry_kind(d["Type"])
                ref = "E-%d" % self.ledger.add_expense(
                    self.owner, cat, render.cell_amount(d["Amount (₹)"]), vendor=str(d.get("Name", "")).strip(),
                    entry_date=render.cell_date(d["Date"]) if str(d.get("Date", "")).strip() else None,
                    note=render.clean_note(d.get("Note")), source="sheet", direction=direction)
            elif tab == render.RATES_TAB:
                self.ledger.set_price(self.owner, self._name(d["Item"]), render.cell_amount(d["Rate (₹)"]),
                                      str(d.get("Unit", "")).strip())
                ref = "rate"
            else:
                self.ledger.get_or_create_customer(self.owner, self._name(d["Name"]))
                self._set_phone(d)
                ref = "customer"
            self._imported[fp] = ref
            done["added"] += 1
        except (ValueError, KeyError) as ex:
            row = [str(c) for c in raw] + [""] * (len(self.EDIT_COLS[tab]) + 4 - len(raw))
            cols = {render.UDHAAR_TAB: render.UDHAAR_COLUMNS, render.ENTRIES_TAB: render.ENTRY_COLUMNS,
                    render.RATES_TAB: render.RATE_COLUMNS, render.CUSTOMERS_TAB: render.CUSTOMER_COLUMNS}[tab]
            row = row[:len(cols)]
            ni = cols.index("Note")
            old = render.clean_note(row[ni])
            row[ni] = render.WARN + (str(ex).strip("'\"") or "this row could not be read") + (f" | {old}" if old else "")
            self._orphans.setdefault(tab, []).append(row)
            done["refused"] += 1

    @staticmethod
    def _name(v) -> str:
        from .names import name_key
        t = " ".join(str(v or "").split())[:40]
        if not name_key(t):
            raise ValueError("name is empty")
        return t

    def _set_phone(self, d) -> bool:
        from .reminders import normalize_phone
        name = self.ledger.find_customer(self.owner, self._name(d["Name"])) or self._name(d["Name"])
        raw = str(d.get("WhatsApp number", "")).strip()
        if not raw:
            self.ledger.set_phone(self.owner, name, None)
            return True
        ph = normalize_phone(raw)
        if not ph:
            raise ValueError(f"WhatsApp number '{raw}' should be 10 digits, e.g. 98765 43210")
        self.ledger.set_phone(self.owner, name, ph)
        return True

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
