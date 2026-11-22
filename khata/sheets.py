"""Mirror ledger entries to a Google Sheet. SQLite stays the source of truth.

sync() diffs the ledger against what the sheet has seen (table sheet_sync), so failures
are retried on the next run with backoff. Appends are idempotent: before re-trying an
append, the entry id is looked up in column A so a lost response can't duplicate a row.
Undo flips the row's status cell to 'cancelled'; rows are never deleted.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Protocol

from .ledger import Ledger

# One row per record. "name" is the customer, or who was paid for an expense (shop on a bill,
# staff for salary). "category" is only for expenses. Older sheets used OLD_HEADER; restore reads both.
HEADER = ["id", "date", "type", "name", "category", "rupees", "due", "status", "note"]
OLD_HEADER = ["entry_id", "date", "customer", "kind", "rupees", "due", "status"]
STATUS_COL = "H"
LAST_COL = "I"

# Summary tab: live formulas over the Khata tab, so the sheet reads like a khata, not a log.
SUMMARY = [
    ["Customer balances (positive = they owe you, negative = you owe them)", "", "", "",
     "Shop expenses by month", "", ""],
    ['=IFERROR(QUERY(FILTER({Khata!D2:D, IF(Khata!C2:C="credit",1,-1)*Khata!F2:F}, Khata!H2:H="active", '
     '(Khata!C2:C="credit")+(Khata!C2:C="payment")), "select Col1, sum(Col2) group by Col1 '
     'order by sum(Col2) desc label Col1 \'customer\', sum(Col2) \'balance (₹)\'", 0), "no customer entries yet")',
     "", "", "",
     '=IFERROR(QUERY(FILTER({LEFT(Khata!B2:B,7), Khata!E2:E, Khata!F2:F}, Khata!H2:H="active", '
     'Khata!C2:C="expense"), "select Col1, Col2, sum(Col3) group by Col1, Col2 order by Col1 desc '
     'label Col1 \'month\', Col2 \'category\', sum(Col3) \'spent (₹)\'", 0), "no expenses yet")'],
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sheet_sync(
  entry_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, row INTEGER, synced_status TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, last_error TEXT);
"""


class SheetClient(Protocol):
    def append(self, row: list) -> int: ...              # returns 1-based row number
    def find(self, entry_id: int) -> int | None: ...
    def set_status(self, row: int, status: str) -> None: ...
    def read_all(self) -> list[list]: ...                 # every row, header included


class GoogleSheetClient:
    """Sheets API v4 via a service account (share the sheet with the service account email)."""
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, spreadsheet_id: str, creds_file: str | None = None, tab: str = "Khata",
                 info: dict | None = None):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = (service_account.Credentials.from_service_account_info(info, scopes=scopes) if info
                 else service_account.Credentials.from_service_account_file(creds_file, scopes=scopes))
        self.s = AuthorizedSession(creds)
        self.id, self.tab = spreadsheet_id, tab

    def _url(self, rng: str, suffix: str = "") -> str:
        from urllib.parse import quote
        return f"{self.API}/{self.id}/values/{quote(f'{self.tab}!{rng}')}{suffix}"

    def append(self, row):
        r = self.s.post(self._url(f"A:{LAST_COL}", ":append"), timeout=20,
                        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                        json={"values": [row]})
        r.raise_for_status()
        rng = r.json()["updates"]["updatedRange"]           # e.g. Khata!A12:I12
        return int(re.sub(r"[A-Z]", "", rng.split("!")[1].split(":")[0]))

    def find(self, entry_id):
        r = self.s.get(self._url("A:A"), timeout=20)
        r.raise_for_status()
        for i, v in enumerate(r.json().get("values", []), start=1):
            if v and str(v[0]) == str(entry_id):
                return i
        return None

    def set_status(self, row, status):
        r = self.s.put(self._url(f"{STATUS_COL}{row}"), params={"valueInputOption": "RAW"},
                       json={"values": [[status]]}, timeout=20)
        r.raise_for_status()

    def read_all(self):
        r = self.s.get(self._url(f"A:{LAST_COL}"), timeout=30, params={"valueRenderOption": "UNFORMATTED_VALUE"})
        r.raise_for_status()
        return r.json().get("values", [])

    def rewrite(self, rows: list[list]) -> None:
        """Replace the whole Khata tab (used once, to move an old sheet to the new columns)."""
        self.s.post(self._url("A:Z", ":clear"), timeout=20, json={}).raise_for_status()
        self.s.put(self._url(f"A1:{LAST_COL}{len(rows)}"), params={"valueInputOption": "RAW"},
                   json={"values": rows}, timeout=30).raise_for_status()

    def ensure_layout(self):
        """Add (or refresh) the Summary tab of live formulas."""
        from urllib.parse import quote
        meta = self.s.get(f"{self.API}/{self.id}", params={"fields": "sheets.properties.title"}, timeout=20)
        meta.raise_for_status()
        if "Summary" not in {sh["properties"]["title"] for sh in meta.json().get("sheets", [])}:
            self.s.post(f"{self.API}/{self.id}:batchUpdate", timeout=20, json={"requests": [
                {"addSheet": {"properties": {"title": "Summary"}}}]}).raise_for_status()
        self.s.put(f"{self.API}/{self.id}/values/{quote('Summary!A1:G2')}", timeout=20,
                   params={"valueInputOption": "USER_ENTERED"}, json={"values": SUMMARY}).raise_for_status()


def _money(v) -> int:
    return round(float(str(v).replace(",", "").replace("₹", "")) * 100)


def _parse_new(r: list) -> tuple:
    # id, date, type, name, category, rupees, due, status, note
    return (str(r[0]).strip(), str(r[2]).strip(), str(r[1])[:10], str(r[3]).strip(), str(r[4]).strip(),
            _money(r[5]), str(r[6])[:10] or None, str(r[7]).strip() or "active", str(r[8]))


def _parse_old(r: list) -> tuple:
    # entry_id, date, customer (or category), kind, rupees, due, status, note (bill vendor for expenses)
    sid, kind, note = str(r[0]).strip(), str(r[3]).strip(), str(r[7])
    if kind == "expense":
        vendor = note[6:].strip() if note.startswith("bill: ") else ""
        return sid, kind, str(r[1])[:10], vendor, str(r[2]).strip(), _money(r[4]), None, \
            str(r[6]).strip() or "active", "bill photo" if vendor else note
    return sid, kind, str(r[1])[:10], str(r[2]).strip(), "", _money(r[4]), str(r[5])[:10] or None, \
        str(r[6]).strip() or "active", note


class SheetMirror:
    def __init__(self, ledger: Ledger, client: SheetClient, owner: str, clock=time.time):
        self.ledger, self.client, self.owner, self.clock = ledger, client, owner, clock
        self.paused = False          # set when restore failed: never write to the sheet blind
        self.boot_result: dict = {}
        with ledger.lock:
            ledger.db.executescript(SCHEMA)

    def boot(self, attempts: int = 2, wait: float = 3.0) -> dict:
        """Call before serving requests. Restores from the sheet; pauses syncing if that fails,
        because writing new rows after a failed restore could reuse entry ids already in the sheet."""
        for n in range(attempts):
            try:
                self.boot_result = self.restore()
                self.paused = False
                self._migrate_layout()
                layout = getattr(self.client, "ensure_layout", None)
                if layout:
                    try:
                        layout()                       # cosmetic: never blocks syncing
                    except Exception as ex:
                        self.boot_result = {**self.boot_result, "layout_error": type(ex).__name__}
                return self.boot_result
            except Exception as ex:      # network, auth, quota
                self.boot_result = {"error": type(ex).__name__}
                if n + 1 < attempts:
                    time.sleep(wait)
        self.paused = True
        return self.boot_result

    def restore(self) -> dict:
        """Rebuild this owner's entries from the sheet after free hosting wiped the disk.

        Runs only when the database has no entries at all. Restored entries keep their original
        ids, so later syncs and undo keep updating the same sheet rows. Rows that fail
        validation are skipped and counted, never guessed at.
        """
        with self.ledger.lock:
            if (self.ledger.db.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
                    or self.ledger.db.execute("SELECT 1 FROM expenses LIMIT 1").fetchone()):
                return {"restored": 0, "skipped": 0, "reason": "database not empty"}
        rows = self.client.read_all()
        if not rows:
            self.client.append(HEADER)            # brand-new sheet: label the columns
            return {"restored": 0, "skipped": 0, "reason": "new sheet"}
        new = [str(c).strip().lower() for c in rows[0][:3]] == ["id", "date", "type"]
        restored = skipped = 0
        with self.ledger.lock:
            for i, row in enumerate(rows, start=1):
                row = list(row) + [""] * (9 - len(row))
                if i == 1 or not str(row[0]).strip():
                    continue
                try:
                    self._restore_row(i, *(_parse_new(row) if new else _parse_old(row)))
                    restored += 1
                except (ValueError, TypeError, sqlite3.IntegrityError):
                    skipped += 1
            self.ledger.db.commit()
        return {"restored": restored, "skipped": skipped}

    def _restore_row(self, i, sid, kind, entry_date, name, category, paise, due, status, note) -> None:
        """Validate and insert one row with its original id. Caller holds the lock."""
        ok_date = re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry_date)
        if (kind not in ("credit", "payment", "expense") or status not in ("active", "cancelled")
                or not 0 < paise <= 10**9 or not ok_date or (due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due))):
            raise ValueError(sid)
        created = entry_date + "T00:00:00+00:00"
        if kind == "expense":
            xid = int(str(sid).strip()[1:])
            if xid <= 0 or not category:
                raise ValueError(sid)
            self.ledger.db.execute(
                "INSERT INTO expenses(id,owner,category,paise,note,vendor,entry_date,status,source,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (xid, self.owner, category[:40], paise, note[:200], name[:60], entry_date, status, "sheet", created))
            key = -xid
        else:
            key = int(float(sid))
            if not name:
                raise ValueError(sid)
            cid = self.ledger.get_or_create_customer(self.owner, name)
            self.ledger.db.execute(
                "INSERT INTO entries(id,owner,customer_id,kind,paise,note,entry_date,due_date,status,"
                "source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key, self.owner, cid, kind, paise, note[:200], entry_date, due, status, "sheet", created))
        self.ledger.db.execute(
            "INSERT OR REPLACE INTO sheet_sync(entry_id,owner,row,synced_status,attempts,next_at) "
            "VALUES(?,?,?,?,0,0)", (key, self.owner, i, status))

    def _migrate_layout(self, force: bool = False) -> None:
        """An old-format sheet is rewritten once in the new columns, from the database.
        `force` rewrites a new-format sheet too (after a data fix in the database)."""
        read = getattr(self.client, "read_all", None)
        rewrite = getattr(self.client, "rewrite", None)
        if not read or not rewrite:
            return
        rows = read()
        if not rows or (not force and [str(c).strip().lower() for c in rows[0][:3]] == ["id", "date", "type"]):
            return
        recs = sorted(self._records(), key=lambda r: (str(r[3][1]), r[1]))   # by date
        rewrite([HEADER] + [r[3] for r in recs])
        with self.ledger.lock:
            self.ledger.db.execute("DELETE FROM sheet_sync WHERE owner=?", (self.owner,))
            for n, (key, _, status, _) in enumerate(recs, start=2):
                self.ledger.db.execute(
                    "INSERT INTO sheet_sync(entry_id,owner,row,synced_status,attempts,next_at) VALUES(?,?,?,?,0,0)",
                    (key, self.owner, n, status))
            self.ledger.db.commit()
        self.boot_result = {**self.boot_result, "migrated_rows": len(recs)}

    def _records(self) -> list[tuple[int, str, str, list]]:
        """Everything mirrored: (sync key, sheet id, status, row). Expenses use negative keys."""
        out = [(e.id, str(e.id), e.status, [e.id, e.entry_date, e.kind, e.customer, "", e.paise / 100,
                                            e.due_date or "", e.status, e.note or ""])
               for e in self.ledger.entries(self.owner, include_cancelled=True)]
        out += [(-x.id, f"E{x.id}", x.status, [f"E{x.id}", x.entry_date, "expense", x.vendor, x.category,
                                               x.paise / 100, "", x.status, x.note or ""])
                for x in self.ledger.expenses(self.owner, include_cancelled=True)]
        return out

    def _state(self) -> dict[int, dict]:
        with self.ledger.lock:
            rows = self.ledger.db.execute("SELECT * FROM sheet_sync WHERE owner=?", (self.owner,)).fetchall()
        return {r["entry_id"]: dict(r) for r in rows}

    def pending(self) -> int:
        st = self._state()
        return sum(1 for key, _, status, _ in self._records() if st.get(key, {}).get("synced_status") != status)

    def sync(self) -> dict:
        if self.paused:
            return {"synced": 0, "failed": 0, "pending": self.pending(), "paused": True}
        done = failed = 0
        now = self.clock()
        with self.ledger.lock:
            st = self._state()
            todo = [r for r in self._records()
                    if st.get(r[0], {}).get("synced_status") != r[2] and st.get(r[0], {}).get("next_at", 0) <= now]
        for key, sheet_id, status, values in todo:
            s = st.get(key, {})
            try:
                row = s.get("row")
                if row is None and s.get("attempts", 0) > 0:
                    row = self.client.find(sheet_id if key < 0 else key)   # did a previous append land?
                if row is None:
                    row = self.client.append(values)
                elif status != s.get("synced_status"):
                    self.client.set_status(row, status)
                self._save(key, row, status, 0, 0, None)
                done += 1
            except Exception as ex:                          # retry later with backoff
                n = s.get("attempts", 0) + 1
                self._save(key, s.get("row"), s.get("synced_status"), n,
                           now + min(300, 2 ** n), type(ex).__name__)
                failed += 1
        return {"synced": done, "failed": failed, "pending": self.pending()}

    def _save(self, entry_id, row, status, attempts, next_at, err):
        with self.ledger.lock:
            self.ledger.db.execute(
                "INSERT INTO sheet_sync(entry_id,owner,row,synced_status,attempts,next_at,last_error) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET row=excluded.row, "
                "synced_status=excluded.synced_status, attempts=excluded.attempts, "
                "next_at=excluded.next_at, last_error=excluded.last_error",
                (entry_id, self.owner, row, status, attempts, next_at, err))
            self.ledger.db.commit()

    def start(self, every: float = 15.0) -> threading.Thread:
        def loop():
            while True:
                try:
                    self.sync()
                except Exception:
                    pass
                time.sleep(every)
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
    import json
    return SheetMirror(ledger, GoogleSheetClient(sid, path, info=json.loads(info) if info else None), owner)
